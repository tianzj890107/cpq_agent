/* 【CPQ 定制】3.1 汇总结果在提交送审时，一并落盘并定稿「技术工艺总结」(SummaryDoc)。

   为什么需要这个文件：
   上游把 SummaryDoc 交给 /apps/tech-process/ 的第 8 步「技术工艺总结」产出
   （POST /api/projects/{id}/summary/recommend → PUT /api/projects/{id}/summary
   → POST /api/projects/{id}/summary/confirm）。CPQ 精简流程只保留 2.1 图纸解析，
   该子应用已整体移出流程（workflow-navigation.js 不再路由到 /apps/tech-process/），
   于是 SummaryDoc 再无任何产出入口，而后端送审门禁
   backend/main.py::_report_prerequisite_issues 仍然检查它：

       if not summary_doc: issues.append("技术工艺总结尚未生成")

   结果就是 3.1 点「提交」永远被 409「报告暂不可送审：技术工艺总结尚未生成」挡住。

   在只有 2.1 的精简流程里，「技术工艺总结」和 3.1 汇总结果本来就是同一件事，
   且两个接口的权限相同（都要求 MANAGER_ROLES），所以这里把总结的落盘与定稿并入
   3.1 的「提交」：先用本页内容写出并确认 SummaryDoc，再走原来的保存 + 送审。
   （门禁本身保持不动，仍然真实生效——只是现在有了产出它的入口。） */

const cpqSdPid = () => encodeURIComponent(srPid);

/** 把 3.1 页面上的内容整理成 SummaryDoc 的执行摘要字段。 */
function cpqSummaryDocFrom(report) {
  const base = srAggregate?.summary || {};
  const evals = (report.evaluation_items || []).filter((row) => row.item);
  const stages = (report.stage_results || []).filter((row) => row.stage);
  const pick = (keyword) => evals.filter((row) => row.item.includes(keyword))
    .map((row) => row.conclusion.trim()).filter(Boolean);
  const overviewLines = evals
    .map((row) => `${row.item}（${row.status}）：${row.conclusion.trim() || '—'}`);
  const product = report.basic_info?.product_name || srView?.basic?.product_name || '';
  const overview = [
    product ? `产品：${product}` : '',
    report.report_no ? `报告编号：${report.report_no}` : '',
    ...overviewLines,
  ].filter(Boolean).join('\n');
  return {
    ...base,
    project_id: srPid,
    title: base.title || report.title || '技术工艺总结报告',
    overview: overview || base.overview || '',
    highlights: stages.map((row) => `${row.stage}：${row.conclusion.trim() || '—'}`),
    risks: pick('风险').length ? pick('风险') : (base.risks || []),
    conclusion: report.conclusion || base.conclusion || '',
  };
}

/** 保存并定稿技术工艺总结；失败时抛错，由调用方 toast 出来。 */
async function cpqSyncSummaryDoc(report) {
  const doc = cpqSummaryDocFrom(report);
  if (!doc.conclusion) throw new Error('请先填写「一、工艺可行性总结论」的小结，它会作为技术工艺总结的总体结论。');
  if (!doc.overview) throw new Error('请先补全评估项内容，它会作为技术工艺总结的执行摘要。');
  const saved = await api(`/api/projects/${cpqSdPid()}/summary`, {
    method: 'PUT', body: JSON.stringify(doc),
  });
  const confirmed = await api(`/api/projects/${cpqSdPid()}/summary/confirm`, { method: 'POST' });
  if (srAggregate) srAggregate.summary = confirmed.summary || saved.summary || doc;
}

const cpqBaseSrSave = srSave;
srSave = async function (submit) {
  if (!submit) return cpqBaseSrSave(false);
  try {
    const payload = srRead();
    if (!payload.title) return srToast('请填写产品名称', true);
    await cpqSyncSummaryDoc(payload);
  } catch (error) {
    return srToast(error.message || '技术工艺总结定稿失败', true);
  }
  // 总结已定稿，原逻辑继续：保存报告 → 送审 → 跳 3.2。
  return cpqBaseSrSave(true);
};
