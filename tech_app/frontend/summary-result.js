/* 3.1 汇总结果：始终展示当前项目的真实工艺数据，缺失项明确标记待完成。 */
// 项目身份唯一来源：共享模块（模块缺失时按「没有项目」处理，绝不退回 localStorage；
// 既有视图级守卫测试会在不带该模块的沙箱里执行本文件头部，故用 typeof 兜底而不是裸引用）。
const srPid = (typeof TechProjectContext !== 'undefined'
  && TechProjectContext.bind().project) || '';
const srIcon = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>';
let srReport, srAggregate, srRequirement, srView, srCurrentUser={};
const srEsc = value => esc(value ?? '');
const srDate = value => String(value || '').slice(0,10).replaceAll('-', '/') || '—';
function srCstDate(){const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());const part=type=>parts.find(item=>item.type===type)?.value||'';return `${part('year')}-${part('month')}-${part('day')}`;}
function srAutoReportNo(requirementNo){return `RPT-${srPid.toUpperCase()}`;}
function srToast(message,error=false){const el=document.createElement('div');el.className=`summary-toast${error?' error':''}`;el.textContent=message;document.body.append(el);setTimeout(()=>el.remove(),3200);}
function srHas(value){if(Array.isArray(value))return value.some(srHas);if(value&&typeof value==='object')return Object.entries(value).some(([key,item])=>!['project_id','updated_at','timing','confirmed','confirmed_by','confirmed_at'].includes(key)&&srHas(item));return typeof value==='string'?Boolean(value.trim()):value!==null&&value!==undefined&&value!==false;}
function srCard(title,body,kind='summary-card'){const header=kind==='form-card'?'form-card-header':'summary-card-header',titleClass=kind==='form-card'?'form-card-title':'summary-card-title';return `<section class="${kind}" data-card><div class="${header}" data-card-toggle><div class="${titleClass}">${srIcon}${title}</div><div class="form-card-toggle">⌄</div></div>${body}</section>`;}
// 【5 阶段 × 13 子步骤】报告页的流程条：前 4 阶段已完成，第 5 阶段「工艺评估报告」
// 下挂 5.1 汇总结果 / 5.2 结果审核 / 5.3 发布并回传报价，本页停在 5.1。
function srWorkflow(){const phases=[['1','工艺评估需求',[['1.1','创建'],['1.2','确认'],['1.3','审核']]],['2','图纸解析',[['2.1','图纸解析']]],['3','组装与整合',[['3.1','整合图纸'],['3.2','参数推荐'],['3.3','组装工艺']]],['4','成本测算',[['4.1','零件成本'],['4.2','组装成本'],['4.3','汇总']]],['5','工艺评估报告',[['5.1','汇总结果'],['5.2','结果审核'],['5.3','发布并回传报价']]]];const groups=phases.map(([no,title,subs],index)=>{const state=index<4?'completed':'active';const links=subs.map(([sub,label])=>`<span class="sub-label ${index<4?'completed':sub==='5.1'?'active':''}">${sub} ${label}</span>`).join('<span class="sub-label-arrow">→</span>');return `${index?'<div class="main-connector active"></div>':''}<div class="main-step-wrapper"><div class="main-step ${state}"><div class="main-step-number">${no}</div><span>${title}</span></div><div class="sub-labels-row">${links}</div></div>`;}).join('');return `<section class="workflow-section"><div class="main-workflow">${groups}</div></section>`;}
function srRender(){const d=srView,basic=d.basic;const basicCard=srCard('基本信息',`<div class="form-card-content"><div class="form-row three-col"><div class="form-field"><label class="form-label">报告编号</label><input id="srReportNo" class="form-input" value="${srEsc(basic.report_no)}"></div><div class="form-field"><label class="form-label">对应需求单号</label><input id="srRequirementNo" class="form-input" value="${srEsc(basic.requirement_no)}"></div><div class="form-field"><label class="form-label">编制日期</label><input id="srPreparedAt" class="form-input" value="${srEsc(basic.prepared_at)}"></div></div><div class="form-row two-col"><div class="form-field"><label class="form-label">编制人</label><input id="srPreparedBy" class="form-input" value="${srEsc(basic.prepared_by)}"></div><div class="form-field"><label class="form-label"><span class="required">*</span>产品名称</label><input id="srProductName" class="form-input" value="${srEsc(basic.product_name)}"></div></div></div>`,'form-card');const feasibility=srCard('一、工艺可行性总结论',`<div class="table-wrap"><table class="summary-table"><thead><tr><th>#</th><th>评估项</th><th>结论</th></tr></thead><tbody>${d.evaluation.map((row,index)=>`<tr data-evaluation><td>${index+1}</td><td>${srEsc(row.item)}</td><td><div class="conclusion-cell"><select class="table-select" data-status>${srOptions(row.status)}</select><input class="table-input" data-conclusion value="${srEsc(row.conclusion)}"></div></td></tr>`).join('')}<tr><td colspan="3"><strong>小结：</strong><input id="srConclusion" class="table-input" value="${srEsc(d.conclusion)}"></td></tr></tbody></table></div>`);const stages=srCard('二、各环节评估结果汇总',`<div class="table-wrap"><table class="summary-table"><thead><tr><th>#</th><th>评估环节</th><th>核心结论</th></tr></thead><tbody>${d.stages.map((row,index)=>`<tr data-stage><td>${index+1}</td><td>${srEsc(row.stage)}</td><td><input class="table-input" data-stage-conclusion value="${srEsc(row.conclusion)}"></td></tr>`).join('')}</tbody></table></div>`);const reviews=srCard('三、审核与发布信息',`<div class="table-wrap"><table class="review-table"><thead><tr><th>审核角色</th><th>状态</th><th>日期</th></tr></thead><tbody>${d.reviews.map(row=>`<tr data-review><td>${srEsc(row.role)}</td><td><select class="table-select" data-review-status>${srReviewOptions(row.status)}</select></td><td><input class="table-input" data-review-date value="${srEsc(row.date)}"></td></tr>`).join('')}</tbody></table></div>`);const attachments=srCard('四、附件清单',`<div class="table-wrap"><table class="attachment-table"><thead><tr><th>序号</th><th>附件名称</th><th>来源</th></tr></thead><tbody>${d.attachments.map((row,index)=>`<tr><td>${index+1}</td><td><a class="attachment-link" target="_blank" rel="noopener" href="${srEsc(row.href)}">${srIcon}${srEsc(row.name)}</a></td><td>${srEsc(row.source)}</td></tr>`).join('')}</tbody></table></div>`);document.querySelector('#app').innerHTML=`${srWorkflow()}<section class="title-section"><div class="title-row"><h1 class="form-title">${srEsc(d.title)}</h1><span class="status-badge">${d.status==='draft'?'初稿':srEsc(statusLabel(d.status))}</span></div></section>${basicCard}${feasibility}${stages}${reviews}${attachments}<footer class="footer-bar"><div class="footer-left"><button class="summary-btn secondary" id="srBack">← 上一步</button></div><div class="footer-right"><button class="summary-btn secondary" id="srSave">保存</button><button class="summary-btn primary" id="srSubmit">提交</button></div></footer>`;document.querySelectorAll('[data-card-toggle]').forEach(header=>header.onclick=()=>header.closest('[data-card]').classList.toggle('is-collapsed'));document.querySelector('#srBack').onclick=()=>location.href=`/index.html?project=${encodeURIComponent(srPid)}`;document.querySelector('#srSave').onclick=()=>srSave(false);document.querySelector('#srSubmit').onclick=()=>srSave(true);}
function srRead(){const basic={report_no:document.querySelector('#srReportNo').value.trim(),requirement_no:document.querySelector('#srRequirementNo').value.trim(),prepared_at:document.querySelector('#srPreparedAt').value.trim(),prepared_by:document.querySelector('#srPreparedBy').value.trim(),product_name:document.querySelector('#srProductName').value.trim()};const evaluation=[...document.querySelectorAll('[data-evaluation]')].map(row=>({item:row.children[1].textContent.trim(),status:row.querySelector('[data-status]').value,conclusion:row.querySelector('[data-conclusion]').value.trim()}));const stages=[...document.querySelectorAll('[data-stage]')].map(row=>({stage:row.children[1].textContent.trim(),conclusion:row.querySelector('[data-stage-conclusion]').value.trim()}));const reviews=[...document.querySelectorAll('[data-review]')].map(row=>({role:row.children[0].textContent.trim(),status:row.querySelector('[data-review-status]').value,date:row.querySelector('[data-review-date]').value.trim()}));return {...srReport,project_id:srPid,report_no:basic.report_no,requirement_no:basic.requirement_no,title:basic.product_name?`${basic.product_name}工艺评估报告`:srView.title,basic_info:basic,evaluation_items:evaluation,stage_results:stages,review_progress:reviews,attachments:srView.attachments,conclusion:document.querySelector('#srConclusion').value.trim(),display_mode:srView.real?'real':'reference'};}
async function srSave(submit){try{const payload=srRead();if(!payload.title)return srToast('请填写产品名称',true);const saved=await api(`/api/projects/${encodeURIComponent(srPid)}/process-report`,{method:'PUT',body:JSON.stringify(payload)});srReport=saved.report;if(submit){await api(`/api/projects/${encodeURIComponent(srPid)}/process-report/submit-review`,{method:'POST',body:JSON.stringify({comment:'工艺评估报告已汇总，提交审核。'})});window.TechEmbed&&window.TechEmbed.embedded?window.TechEmbed.navigateToFile('report-review.html',srPid):location.href=`report-review.html?project=${encodeURIComponent(srPid)}`;return;}srToast('工艺评估报告已保存');}catch(error){srToast(error.message||'保存失败',true);}}
// 不再回填参考稿数据：3.1 始终基于当前项目已保存的结果展示，缺失项明确标记为待完成。
function srText(...items){for(const item of items){if(typeof item==='string'&&item.trim())return item.trim();if(Array.isArray(item)&&item.length)return item.map(value=>typeof value==='string'?value:JSON.stringify(value)).join('；');}return '暂无数据（待前序步骤完成）';}
function srAttachments(){const rows=srReport?.attachments||[];if(rows.length)return rows.map(row=>Array.isArray(row)?{name:row[0],source:row[1],href:String(row[2]||'#').replace('{project}',encodeURIComponent(srPid))}:{...row,href:String(row.href||'#').replace('{project}',encodeURIComponent(srPid))});const output=[];if(srAggregate?.ir)output.push({name:'图纸解析报告',source:'2.1 图纸解析',href:`report.html?project=${encodeURIComponent(srPid)}`});if(srAggregate?.ir)output.push({name:'当前 BOM 清单',source:'2.1 图纸解析',href:`/api/projects/${encodeURIComponent(srPid)}/bom.csv`});return output;}
function srEmptyReport(){return {project_id:srPid,report_no:'',requirement_no:'',title:'',status:'draft',version:1,history:[],source_snapshot:{}};}
// 「4 成本测算」→「5.1 汇总结果」：金额只做展示格式化，前端不对成本求和（合计由后端成本口径给出）。
function srMoney(value){const number=Number(value);if(!Number.isFinite(number))return '';return `¥${number.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g,',')}`;}
// 成本确认状态优先取聚合层顶层口径 aggregate.cost（backend services/summary.py 复用
// cost_review 生成），老接口只有 steps.cost_review 时也能拿到财务确认信息。
function srCostInfo(cost,review){const data=cost||{},confirm=(review&&srHas(review))?review:(data.review||{}),final=data.final||{},assembly=data.assembly||{},parts=data.parts_total||{},total=[final.total,(assembly.breakdown||{}).total,assembly.subtotal,assembly.unit_cost].find(value=>Number.isFinite(Number(value))&&Number(value)>0);return {confirm,confirmed:Boolean(confirm.confirmed),measured:Boolean(data.ready||confirm.confirmed||total),total,partsTotal:parts.total,assemblyTotal:((assembly.breakdown||{}).total??assembly.subtotal)};}
function srCostConfirmedText(info){const who=info.confirm.confirmed_by||'',at=info.confirm.confirmed_at||'';return `已由${who?' '+who:''}${at?' 于 '+at:''}确认`;}
function srCostAmounts(info){const bits=[];if(Number.isFinite(Number(info.partsTotal)))bits.push(`零件成本合计 ${srMoney(info.partsTotal)}`);if(Number.isFinite(Number(info.assemblyTotal)))bits.push(`组装成本 ${srMoney(info.assemblyTotal)}`);if(Number.isFinite(Number(info.total)))bits.push(`整机成本合计 ${srMoney(info.total)}`);return bits;}
// 经济可行性只有三种状态：成本已确认 → 可行；已测算未确认 / 没做 → 待评估。
function srCostEconomic(cost,review){const info=srCostInfo(cost,review);if(info.confirmed)return {item:'经济可行性',status:'可行',conclusion:`${srCostAmounts(info).join('，')||'成本已确认'}；${srCostConfirmedText(info)}，可用于报价定价。`};if(info.measured)return {item:'经济可行性',status:'待评估',conclusion:`成本已测算${Number.isFinite(Number(info.total))?`（整机成本合计 ${srMoney(info.total)}）`:''}，财务尚未确认，确认后再出经济可行性结论。`};return {item:'经济可行性',status:'待评估',conclusion:'成本测算尚未完成，暂无成本结论。'};}
// 「4 成本测算」阶段行：只有成本已确认（结论完整）才进汇总表 —— 空着也加一行的话，那行必然带
// 「尚未/暂无」，而后端送审门禁禁止 stage_results 出现这两个词。
function srCostStage(cost,review){const info=srCostInfo(cost,review);if(!info.confirmed)return[];const amounts=srCostAmounts(info);return [{stage:'4 成本测算',conclusion:`${amounts.length?`${amounts.join('，')}；`:''}${srCostConfirmedText(info)}。`}];}
function srLiveView(report,aggregate,requirement){const steps=aggregate?.steps||{},ir=aggregate?.ir||{},summary=aggregate?.summary||{},material=steps.material||{},manufacturing=steps.manufacturing||{},cleaning=steps.cleaning||{},assembly=steps.assembly||{},production=steps.production||{},cost=aggregate?.cost||{},costReview=steps.cost_review||{},product=aggregate?.device_name||ir.device_name||requirement?.title||report.title||'';const stage=(label,data,text)=>({stage:label,conclusion:data&&srHas(data)?text:'尚未完成，暂无可汇总结论。'});const reportStatus=report.status||'draft';return {real:true,title:report.title||`${product||'当前项目'}工艺评估报告`,status:reportStatus,basic:{report_no:report.report_no||'',requirement_no:report.requirement_no||requirement?.requirement_no||'',prepared_at:report.prepared_at?String(report.prepared_at).slice(0,10):'',prepared_by:report.prepared_by||'',product_name:product},evaluation:[{item:'技术可行性',status:ir.parts?.length?'待评估':'待评估',conclusion:summary.conclusion||(`已完成图纸解析，识别 ${ir.parts?.length||0} 个零件；尚未形成完整工艺评估结论。`)},srCostEconomic(cost,costReview),{item:'风险评估',status:summary.risks?.length?'待评估':'待评估',conclusion:summary.risks?.length?srText(summary.risks):'尚未形成完整风险评估。'}],conclusion:summary.conclusion||report.conclusion||'尚未形成汇总结论。',stages:[stage('2.1 图纸解析',ir,`图纸解析完成，已识别 ${ir.parts?.length||0} 个零件。`),...srIntegrationStage(steps.integration),...srCostStage(cost,costReview)],reviews:[{role:'工艺技术经理（汇总）',status:reportStatus==='draft'?'待汇总':'已完成',date:srDate(report.prepared_at)},{role:'工艺技术总监（审核）',status:['approved','published'].includes(reportStatus)?'已完成':'待审核',date:srDate(report.reviewed_at)},{role:'工艺技术总监（发布）',status:reportStatus==='published'?'已发布':'待发布',date:srDate(report.published_at)}],attachments:srAttachments()};}
// 「3 组装与整合」：**有结果才进汇总表**。空着也加一行的话，那行结论必然写着「尚未…」，
// 而后端送审门禁(_report_content_issues)恰好禁止 stage_results 出现「尚未/暂无」——
// 没走「3 组装与整合」的老项目会因此再也提交不了报告。
function srIntegrationStage(doc){
  const params=doc?.params,process=doc?.process,cost=doc?.cost;
  if(!params?.params?.length&&!process?.steps?.length&&!cost?.items?.length)return[];
  const bits=[];
  if(params?.assembly_name)bits.push(`整机「${params.assembly_name}」`);
  if(params?.params?.length)bits.push(`整机参数 ${params.params.length} 项`);
  if(params?.interfaces?.length)bits.push(`零件连接 ${params.interfaces.length} 处`);
  if(process?.steps?.length)bits.push(`组装工序 ${process.steps.length} 道`);
  if(cost?.items?.length)bits.push(`整机成本已按 ${cost.quantity||1} 台批量测算`);
  return[{stage:'3 组装与整合',conclusion:`${bits.join('，')}。`}];
}
function srBuildView(report,aggregate,requirement){return srLiveView(report,aggregate,requirement);}
function srOptions(value){return ['待评估','可行','需补充','中低风险'].map(option=>`<option${option===value?' selected':''}>${option}</option>`).join('');}
function srReviewOptions(value){return ['待汇总','已完成','待审核','已驳回','待发布','已发布'].map(option=>`<option${option===value?' selected':''}>${option}</option>`).join('');}
// 3.1 首次打开即展示系统生成的单据基础信息；保存时仍由后端再次校正并留痕。
const srBaseLiveView=srLiveView;
srLiveView=function(report,aggregate,requirement){const view=srBaseLiveView(report,aggregate,requirement),basic=view.basic||{},requirementNo=basic.requirement_no||report.requirement_no||requirement?.requirement_no||'';basic.requirement_no=requirementNo;basic.report_no=basic.report_no||srAutoReportNo(requirementNo);basic.prepared_at=basic.prepared_at||srCstDate();basic.prepared_by=basic.prepared_by||srCurrentUser.display_name||srCurrentUser.username||'系统';view.basic=basic;return view;};
async function srStart(){if(!srPid){if(window.TechEmbed&&window.TechEmbed.embedded){window.TechEmbed.exitToTechHome();return;}location.href='home.html';return;}try{const [reportResult,aggregate,requirementResult,meResult]=await Promise.all([api(`/api/projects/${encodeURIComponent(srPid)}/process-report`),api(`/api/projects/${encodeURIComponent(srPid)}/summary`),api(`/api/projects/${encodeURIComponent(srPid)}/requirement`).catch(()=>({requirement:null})),api('/api/me').catch(()=>({user:{}}))]);srCurrentUser=meResult.user||{};srReport=reportResult.report||srEmptyReport();srAggregate=aggregate;srRequirement=requirementResult.requirement;srView=srBuildView(srReport,srAggregate,srRequirement);srRender();}catch(error){document.querySelector('#app').innerHTML=`<section class="title-section"><h1 class="form-title">汇总工艺评估结果</h1><p style="color:#6b7280">页面加载失败：${srEsc(error.message)}</p></section>`;}};
// 3.1：在报告草稿阶段维护发布范围与抄送对象，点击页面“保存/提交”时一并保存。
const srBaseRead=srRead;
srRead=function(){const report=srBaseRead();report.distribution_scope=document.querySelector('#srDistributionScope')?.value.trim()||report.distribution_scope||'';report.distribution_cc=document.querySelector('#srDistributionCc')?.value.trim()||report.distribution_cc||'';return report;};
const srBaseRender=srRender;
srRender=function(){srBaseRender();const status=srView?.status||'draft';if(!['draft','rejected'].includes(status))return;const footer=document.querySelector('.footer-bar');if(!footer)return;footer.insertAdjacentHTML('beforebegin',`<section class="summary-card distribution-maintain-card"><div class="summary-card-header"><div class="summary-card-title">${srIcon}发布设置</div></div><div style="display:grid;gap:14px;padding:18px 20px"><label style="display:grid;gap:6px;font-size:13px;color:#475569">发布范围（以顿号、逗号或换行分隔）<textarea id="srDistributionScope" class="table-input" rows="3" placeholder="例如：销售部、工艺工程部、质量管理部">${srEsc(srReport?.distribution_scope||'')}</textarea></label><label style="display:grid;gap:6px;font-size:13px;color:#475569">抄送对象（以顿号、逗号或换行分隔）<textarea id="srDistributionCc" class="table-input" rows="2" placeholder="例如：项目经理、客户接口人">${srEsc(srReport?.distribution_cc||'')}</textarea></label><p style="margin:0;color:#94a3b8;font-size:12px">点击本页“保存”或“提交”即可一并保存发布设置。</p></div></section>`);};
srStart();

/* 统一看板协议：3.1 的保存与提交审核复用既有 srSave，父壳只发动作名。 */
(function srRegisterTechBoardActions() {
  if (!window.TechBoardRuntime || typeof window.TechBoardRuntime.registerActions !== 'function') return;
  let srBoardBusy = false;
  function srBoardPublish(name, extra) {
    const runtime = window.TechBoardRuntime;
    if (!runtime || typeof runtime.publish !== 'function') return;
    try { runtime.publish(name, 'report', Object.assign({ action: name }, extra || {})); }
    catch { /* 进度上报失败不影响业务本身 */ }
  }
  async function srRefreshReport() {
    const [reportResult, aggregate] = await Promise.all([
      api(`/api/projects/${encodeURIComponent(srPid)}/process-report`),
      api(`/api/projects/${encodeURIComponent(srPid)}/summary`),
    ]);
    srReport = reportResult.report || srReport;
    srAggregate = aggregate;
    srView = srBuildView(srReport, srAggregate, srRequirement);
    srRender();
    return { ok: true };
  }
  async function srGenerateDraft() {
    srBoardPublish('task-progress', { taskId: 'report-draft', label: '生成报告草稿', status: 'running' });
    try {
      const result = await api(`/api/projects/${encodeURIComponent(srPid)}/process-report/prepare`, { method: 'POST', body: JSON.stringify({}) });
      srReport = result.report || srReport;
      await srRefreshReport();
      srBoardPublish('task-completed', { taskId: 'report-draft', label: '生成报告草稿', status: 'succeeded' });
      return { ok: true };
    } catch (error) {
      srBoardPublish('task-failed', { taskId: 'report-draft', label: '生成报告草稿', status: 'failed', message: (error && error.message) || '生成失败' });
      return { ok: false, error: { code: 'action-failed', message: (error && error.message) || '生成报告草稿失败' } };
    }
  }
  async function srUpdateFields(fields) {
    try {
      const payload = Object.assign(srRead(), fields || {});
      const saved = await api(`/api/projects/${encodeURIComponent(srPid)}/process-report`, { method: 'PUT', body: JSON.stringify(payload) });
      srReport = saved.report || srReport;
      await srRefreshReport();
      return { ok: true };
    } catch (error) {
      return { ok: false, error: { code: 'action-failed', message: (error && error.message) || '更新报告字段失败' } };
    }
  }
  async function srSaveDistribution(payload) {
    const data = payload || {};
    const scope = data.distribution_scope != null ? String(data.distribution_scope) : (document.querySelector('#srDistributionScope')?.value.trim() || '');
    const cc = data.distribution_cc != null ? String(data.distribution_cc) : (document.querySelector('#srDistributionCc')?.value.trim() || '');
    try {
      const result = await api(`/api/projects/${encodeURIComponent(srPid)}/process-report/distribution`, { method: 'PUT', body: JSON.stringify({ distribution_scope: scope, distribution_cc: cc }) });
      srReport = result.report || srReport;
      await srRefreshReport();
      return { ok: true };
    } catch (error) {
      return { ok: false, error: { code: 'action-failed', message: (error && error.message) || '保存发布范围失败' } };
    }
  }
  async function srBoardRun(submit) {
    if (srBoardBusy) return { ok: false, error: { code: 'busy', message: '正在保存，请稍候。' } };
    srBoardBusy = true;
    try { await srSave(submit); return { ok: true }; }
    catch (error) { return { ok: false, error: { code: 'action-failed', message: (error && error.message) || '保存失败' } }; }
    finally { srBoardBusy = false; }
  }
  // 3.1「草稿是否已经形成」的唯一判定：报告落过库（单据号 / 编制时间由后端在
  // prepare / save 时写入）才算形成。两颗按钮的 role 由它反转，不许各写一份。
  const srHasDraft = () => Boolean(srReport && (srReport.report_no || srReport.prepared_at));
  window.TechBoardRuntime.registerActions({
    saveProcessReport: {
      label: '保存',
      role: 'aux',
      order: 20,
      run: () => srBoardRun(false),
      getState: () => ({ visible: Boolean(document.querySelector('#srSave')), enabled: !srBoardBusy, busy: srBoardBusy }),
    },
    submitProcessReportReview: {
      label: '提交审核',
      role: 'aux',
      order: 10,
      run: () => srBoardRun(true),
      // 草稿形成后它才是本页唯一主按钮；还没草稿时让位给「一键生成报告草稿」。
      getState: () => ({ visible: Boolean(document.querySelector('#srSubmit')), enabled: !srBoardBusy,
                         busy: srBoardBusy, role: srHasDraft() ? 'primary' : 'aux' }),
    },
    refreshProcessReport: {
      label: '刷新汇总报告',
      role: 'aux',
      order: 60,
      silent: true,
      run: async () => { try { return await srRefreshReport(); } catch (error) { return { ok: false, error: { code: 'action-failed', message: (error && error.message) || '刷新报告失败' } }; } },
      // 只退出左侧栏：刷新仍由 refresh-data 命令与 Agent 工具走 executeAction 触发。
      getState: () => ({ visible: false, enabled: !srBoardBusy, busy: srBoardBusy }),
    },
    generateProcessReportDraft: {
      label: '一键生成报告草稿',
      role: 'aux',
      order: 30,
      run: () => srGenerateDraft(),
      getState: () => ({ visible: true, enabled: !srBoardBusy, busy: srBoardBusy,
                         role: srHasDraft() ? 'aux' : 'primary' }),
    },
    updateProcessReportFields: {
      label: '更新报告字段',
      role: 'aux',
      order: 40,
      run: (payload) => srUpdateFields(payload || {}),
      getState: () => ({ visible: true, enabled: !srBoardBusy, busy: srBoardBusy }),
    },
    saveProcessReportDistribution: {
      label: '保存发布范围',
      role: 'aux',
      order: 50,
      run: (payload) => srSaveDistribution(payload || {}),
      getState: () => ({ visible: true, enabled: !srBoardBusy, busy: srBoardBusy }),
    },
  });
})();
