/* 【CPQ 定制】3.3 发布报告：把「发布范围 / 抄送对象」做成可编辑，就地设置发布对象。

   为什么需要这个文件：
   后端发布接口要求至少一个发布对象（backend/main.py::publish_process_report）：

       if not body.recipients:
           raise HTTPException(409, "请至少设置一个正式发布对象")

   而 3.3 前端的 recipients 是从 report.distribution_scope 拆出来的
   （report-publish-result.js::rpPrimaryAction → rpView.scope），3.3 页面本身却只
   把发布范围渲染成只读标签。上游的编辑入口只在 3.1（草稿态）和 3.2（送审/审核态）
   —— 一旦报告已审核通过、人走到 3.3，发布范围若仍为空，就会陷入「点发布报 409、
   本页又没有地方填」的死角。

   这里给 3.3 补上编辑入口：报告处于 approved（待发布）时显示「发布设置」卡片，
   走后端既有的 PUT /process-report/distribution（该接口允许 approved 状态，
   且放行 MANAGER|DIRECTOR，发布人是总监，权限够）。点「正式发布」时先保存再发布，
   为空则给出可操作提示，而不是把 409 原样抛给用户。 */

const CPQ_PUB_EDITABLE = new Set(['approved']);

/** 保存发布范围与抄送对象；返回是否成功。 */
async function cpqSavePublishDistribution(quiet = false) {
  const box = document.querySelector('#cpqPubScope');
  if (!box) return true;                       // 非可编辑状态，什么都不用做
  const scope = box.value.trim();
  const cc = document.querySelector('#cpqPubCc')?.value.trim() || '';
  try {
    const result = await api(`/api/projects/${encodeURIComponent(rpPid)}/process-report/distribution`, {
      method: 'PUT', body: JSON.stringify({ distribution_scope: scope, distribution_cc: cc }),
    });
    rpReport = result.report;
    rpView.scope = rpScope(scope);
    rpView.cc = cc;
    if (!quiet) rpToast('发布范围与抄送对象已保存。');
    return true;
  } catch (error) {
    rpToast(error.message || '保存发布设置失败', true);
    return false;
  }
}

const cpqBaseRpPrimaryAction = rpPrimaryAction;
rpPrimaryAction = async function () {
  if (rpReport?.status === 'approved') {
    if (!await cpqSavePublishDistribution(true)) return;
    if (!rpView.scope.length) {
      return rpToast('请先在「发布设置」中填写发布范围，它就是本报告的正式发布对象。', true);
    }
  }
  return cpqBaseRpPrimaryAction();
};

const cpqBaseRpRender = rpRender;
rpRender = function () {
  cpqBaseRpRender();
  if (!CPQ_PUB_EDITABLE.has(rpView?.status)) return;
  const footer = document.querySelector('.footer-bar');
  if (!footer) return;
  footer.insertAdjacentHTML('beforebegin', `<section class="summary-card distribution-maintain-card"><div class="summary-card-header"><div class="summary-card-title">${rpIcon}发布设置</div></div><div style="display:grid;gap:14px;padding:18px 20px"><label style="display:grid;gap:6px;font-size:13px;color:#475569">发布范围（以顿号、逗号或换行分隔；每一项即一个正式发布对象）<textarea id="cpqPubScope" class="table-input" rows="3" placeholder="例如：销售部、工艺工程部、质量管理部">${rpEsc((rpView.scope || []).join('、'))}</textarea></label><label style="display:grid;gap:6px;font-size:13px;color:#475569">抄送对象（以顿号、逗号或换行分隔）<textarea id="cpqPubCc" class="table-input" rows="2" placeholder="例如：项目经理、客户接口人">${rpEsc(rpView.cc || '')}</textarea></label><div><button type="button" id="cpqPubSave" class="summary-btn secondary">保存发布设置</button></div><p style="margin:0;color:#94a3b8;font-size:12px">点击右下角「正式发布」也会先保存本卡片内容。</p></div></section>`);
  document.querySelector('#cpqPubSave').onclick = () => cpqSavePublishDistribution();
};
