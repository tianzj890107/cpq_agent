/* 3.3 发布页：只展示当前项目的正式报告与发布记录。 */
const rpPid = TechProjectContext.bind().project;
const rpIcon='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>';
const rpCheck='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>';
let rpReport,rpAggregate,rpView;
const rpEsc=value=>esc(value??'');
const rpDateTime=value=>String(value||'').replace('T',' ').slice(0,16)||'—';
function rpToast(message,error=false){const el=document.createElement('div');el.className=`summary-toast${error?' error':''}`;el.textContent=message;document.body.append(el);setTimeout(()=>el.remove(),3600);}
/* 关键失败必须留在页面上（批次 9 §7.5）：toast 只留给成功提示与安静失败。
   回传销售经理失败 = 报价侧没收到这次交接，属于必须常驻 + 带追踪 ID 的一类。 */
function rpShowFailure(code,message,traceId){if(window.TechFailure&&typeof window.TechFailure.show==='function'){window.TechFailure.show({code:code,message:message,stage:'5.3 发布并回传报价',trace_id:traceId||''});return true;}return false;}
function rpScope(value){if(Array.isArray(value))return value.filter(Boolean);return String(value||'').split(/[、，,\n]/).map(x=>x.trim()).filter(Boolean);}
function rpCard(title,body){return `<section class="summary-card"><div class="summary-card-header"><div class="summary-card-title">${rpIcon}${title}</div></div>${body}</section>`;}
/* 3.3 的结果区要能回答两个问题：这一次交接的编号是多少、来源待办关掉没有。
   回传响应里有这两项，但刷新就没了，所以按项目记一行「展示用摘要」——只存编号与状态
   文案，不存项目身份、不参与任何身份解析，也不作为任何判断依据。 */
const RP_HANDOFF_NOTE_KEY='tech_report_handoff_note';
function rpSourceState(source,handoffId){const row=source||{};if(row.closed)return `来源待办 ${row.task_id||handoffId||''} 已关闭`.trim();if(row.already)return '来源待办在此之前已完成，无需重复关闭';if(row.skipped)return `本次没有需要关闭的来源待办（${row.skipped}）`;if(row.error)return `来源待办未能关闭：${row.error}`;return '来源待办状态未回传';}
/* 来源待办关掉没有：回传响应里带了状态就直接用；只有 task_id 时按 task_id 读一次。
   读取任务记录的唯一入口是 TechTaskWatch（批次 9 §7.4）——刷新 / 复原走的是同一套
   口径，页面不再自己拼 fetch。 */
async function rpSourceLine(source,handoffId){
  const row=source||{},taskId=String(row.task_id||'').trim(),watch=window.TechTaskWatch;
  if(!taskId||!watch||typeof watch.recover!=='function')return rpSourceState(row,handoffId);
  try{
    const record=await watch.recover(rpPid,taskId);
    if(!record)return rpSourceState(row,handoffId);
    return rpSourceState({...row,status:String(record.status||row.status||''),
                          closed:Boolean(row.closed)||watch.isTerminal(record.status)},handoffId);
  }catch{return rpSourceState(row,handoffId);}
}
function rpNowLocal(){const d=new Date(),p=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;}
function rpRememberHandoff(result){try{const row=result||{};localStorage.setItem(`${RP_HANDOFF_NOTE_KEY}:${rpPid}`,JSON.stringify({handoff_id:String(row.handoff_id||''),task_no:String((row.handoff||{}).task_no||''),source:rpSourceState(row.source_task,row.handoff_id),at:rpNowLocal()}));}catch{/* 展示用摘要，写不进去不影响回传本身 */}}
function rpHandoffNote(){try{const last=JSON.parse(localStorage.getItem(`${RP_HANDOFF_NOTE_KEY}:${rpPid}`)||'null');if(!last)return '——';return [`交接编号 ${last.handoff_id||'——'}`,last.task_no?`任务 ${last.task_no}`:'',last.source||'',last.at?`记录于 ${last.at}`:''].filter(Boolean).join(' · ');}catch{return '——';}}
async function rpPrimaryAction(){try{if(rpReport.status==='published'){await api(`/api/projects/${encodeURIComponent(rpPid)}/process-report/new-version`,{method:'POST'});window.TechEmbed&&window.TechEmbed.embedded?window.TechEmbed.navigateToFile('summary.html',rpPid):location.href=`summary.html?project=${encodeURIComponent(rpPid)}`;return;}if(rpReport.status==='approved'){const recipients=rpView.scope.map(name=>({name,organization:'',channel:'平台通知'}));await api(`/api/projects/${encodeURIComponent(rpPid)}/process-report/publish`,{method:'POST',body:JSON.stringify({recipients,comment:'工艺评估报告已正式发布。'})});rpToast('报告已正式发布。');setTimeout(()=>location.reload(),450);return;}rpToast('报告尚未审核通过，请先在 3.2 审核报告完成审核。',true);}catch(error){rpToast(error.message||'发布操作失败',true);}}
async function rpSendReportToSales(){
  if((rpReport?.status||'')!=='published'){rpToast('报告尚未正式发布，不能回传销售经理。',true);return;}
  if(!window.confirm('确认把已发布的工艺评估报告回传销售经理继续报价？'))return;
  try{
    const result=await api(`/api/projects/${encodeURIComponent(rpPid)}/process-report/send-to-quote`,{method:'POST',body:JSON.stringify({note:'工艺评估报告已发布，回传销售经理继续报价。'})});
    const handoff=result.handoff||{};
    const lines=[
      `任务 ${handoff.task_no||''} 已发给${handoff.target_role_name||handoff.target_name||'销售经理'}`,
      `进入报价第 ${result.next_step_no||3} 步「${result.next_step_name||'定价-利润加成'}」`,
      result.already_sent?'同一版报告已经回传过，沿用已有交接':'',
      result.new_card?'没有原报价卡片，已建立新的报价会话':'',
      `随包带上报告 ${result.report_no||''} V${result.version||''}`,
      result.handoff_id?`交接编号 ${result.handoff_id}`:'',
    ].filter(Boolean);
    lines.push(await rpSourceLine(result.source_task,result.handoff_id));
    rpRememberHandoff(result);
    rpToast(lines.join(' · '));
    await rpRefreshReport();
  }catch(error){rpToast(error.message||'回传销售经理失败',true);rpShowFailure((error&&error.code)||'handoff_failed',error.message||'回传销售经理失败',error&&error.trace_id);}
}
async function rpStart(){if(!rpPid){if(window.TechEmbed&&window.TechEmbed.embedded){window.TechEmbed.exitToTechHome();return;}location.href='home.html';return;}try{const [reportResult,aggregate]=await Promise.all([api(`/api/projects/${encodeURIComponent(rpPid)}/process-report`),api(`/api/projects/${encodeURIComponent(rpPid)}/summary`)]);rpReport=reportResult.report||{project_id:rpPid,status:'draft'};rpAggregate=aggregate;rpView=rpLiveView(rpReport,aggregate);rpRender();}catch(error){document.querySelector('#app').innerHTML=`<section class="title-section"><h1 class="form-title">发布工艺评估报告</h1><p style="color:#6b7280">页面加载失败：${rpEsc(error.message)}</p></section>`;}}
// 3.3 只展示真实报告与真实发布记录，未产生的信息不再回填参考项目的默认数据。
function rpAttachments(report){const rows=report?.attachments||[];if(rows.length)return rows.map(row=>Array.isArray(row)?{name:row[0],source:row[1],href:String(row[2]||'#').replace('{project}',encodeURIComponent(rpPid))}:{...row,href:String(row.href||'#').replace('{project}',encodeURIComponent(rpPid))});const output=[];if(rpAggregate?.ir){output.push({name:'图纸解析报告',source:'2.1 图纸解析',href:`report.html?project=${encodeURIComponent(rpPid)}`});output.push({name:'当前 BOM 清单',source:'2.1 图纸解析',href:`/api/projects/${encodeURIComponent(rpPid)}/bom.csv`});}return output;}
function rpLiveView(report,aggregate){const basic=report?.basic_info||{},ir=aggregate?.ir||{},product=aggregate?.device_name||ir.device_name||basic.product_name||report?.title||'',status=report?.status||'draft',prepared=rpDateTime(report?.prepared_at),reviewed=rpDateTime(report?.reviewed_at),published=rpDateTime(report?.published_at);return {real:true,title:report?.title||`${product||'当前项目'}工艺评估报告`,published_at:published,published_by:report?.published_by||'',report_no:report?.report_no||basic.report_no||'',version:report?.version||'',requirement_no:report?.requirement_no||basic.requirement_no||'',product_name:product,timeline:[['汇总工艺评估结果',report?.prepared_at?`${basic.prepared_by||report?.prepared_by||'—'}完成报告汇总`:'尚未完成汇总',prepared],['审核工艺评估报告',['approved','published'].includes(status)?`${report?.reviewed_by||'—'}审核通过`:status==='rejected'?'审核已退回':'尚未完成审核',reviewed],['正式发布报告',status==='published'?`${report?.published_by||'—'}已正式发布`:'尚未正式发布',published]],scope:rpScope(report?.distribution_scope),cc:report?.distribution_cc||'',attachments:rpAttachments(report),status};}
// 【5 阶段 × 13 子步骤】前 4 阶段已完成；第 5 阶段「工艺评估报告」下挂
// 5.1 汇总结果 / 5.2 结果审核 / 5.3 发布并回传报价，本页停在 5.3。
function rpWorkflow(){const status=rpReport?.status||'draft',done=status==='published',reviewDone=['approved','published'].includes(status);const phases=[['1','工艺评估需求',[['1.1','创建'],['1.2','确认'],['1.3','审核']]],['2','图纸解析',[['2.1','图纸解析']]],['3','组装与整合',[['3.1','整合图纸'],['3.2','参数推荐'],['3.3','组装工艺']]],['4','成本测算',[['4.1','零件成本'],['4.2','组装成本'],['4.3','汇总']]],['5','工艺评估报告',[['5.1','汇总结果'],['5.2','结果审核'],['5.3','发布并回传报价']]]];const groups=phases.map(([no,title,subs],index)=>{const state=index<4?'completed':done?'completed':'active';const links=subs.map(([sub,label])=>{const cls=index<4?'completed':sub==='5.3'?(done?'completed':'active'):sub==='5.2'?(reviewDone?'completed':''):'';return `<span class="sub-label ${cls}">${sub} ${label}</span>`;}).join('<span class="sub-label-arrow">→</span>');return `${index?'<div class="main-connector active"></div>':''}<div class="main-step-wrapper"><div class="main-step ${state}"><div class="main-step-number">${no}</div><span>${title}</span></div><div class="sub-labels-row">${links}</div></div>`;}).join('');return `<section class="workflow-section"><div class="main-workflow">${groups}</div></section>`;}
function rpRender(){const d=rpView,status=d.status||'draft',empty=(count,cols)=>count?'':`<tr><td colspan="${cols}" class="empty-cell">暂无数据（待前序步骤完成）</td></tr>`,reportInfo=rpCard('报告信息',`<div class="table-wrap"><table class="info-table"><tbody><tr><th>报告编号</th><td>${rpEsc(d.report_no)}</td><th>对应需求单号</th><td>${rpEsc(d.requirement_no)}</td></tr><tr><th>产品名称</th><td colspan="3">${rpEsc(d.product_name)}</td></tr><tr><th>最近交接</th><td colspan="3">${rpEsc(rpHandoffNote())}</td></tr></tbody></table></div>`),timeline=rpCard('发布流程',`<div class="timeline">${d.timeline.map((row,index)=>`<div class="timeline-item"><div class="timeline-dot">${index===0?rpIcon:index===1?rpCheck:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line></svg>'}</div><div class="timeline-content"><div class="timeline-title">${rpEsc(row[0])}</div><div class="timeline-desc">${rpEsc(row[1])}</div><div class="timeline-time">${rpEsc(row[2])}</div></div></div>`).join('')}</div>`),distribution=rpCard('发布范围',`<div class="distribution-list">${d.scope.length?d.scope.map(x=>`<span class="distribution-tag">${rpEsc(x)}</span>`).join(''):'<span class="distribution-tag">暂无发布范围</span>'}<span class="distribution-tag cc">抄送：${rpEsc(d.cc||'暂无抄送对象')}</span></div>`),attachments=rpCard('附件清单',`<div class="table-wrap"><table class="attachment-table"><thead><tr><th>序号</th><th>附件名称</th><th>来源</th></tr></thead><tbody>${empty(d.attachments.length,3)}${d.attachments.map((row,index)=>`<tr><td>${index+1}</td><td><a class="attachment-link" target="_blank" rel="noopener" href="${rpEsc(row.href)}">${rpIcon}${rpEsc(row.name)}</a></td><td>${rpEsc(row.source)}</td></tr>`).join('')}</tbody></table></div>`),published=status==='published',cardTitle=published?'报告已成功发布':status==='approved'?'报告已审核通过':'报告尚未发布',cardSubtitle=published?'工艺评估报告已完成审核并正式发布至相关部门':status==='approved'?'请确认发布范围后正式发布报告':'请先完成 5.1 汇总结果与 5.2 结果审核。',primary=published?'新建报告':status==='approved'?'正式发布':'等待审核',badge=published?'已发布':status==='approved'?'待发布':status==='rejected'?'已退回':'待审核';document.querySelector('#app').innerHTML=`${rpWorkflow()}<section class="title-section"><div class="title-row"><h1 class="form-title">${rpEsc(d.title)}</h1><span class="status-badge">${rpEsc(badge)}</span></div></section><section class="published-card"><div class="published-icon">${rpCheck}</div><div class="published-title">${rpEsc(cardTitle)}</div><div class="published-subtitle">${rpEsc(cardSubtitle)}</div><div class="publish-info"><div class="publish-info-item"><div class="publish-info-label">发布时间</div><div class="publish-info-value">${rpEsc(d.published_at)}</div></div><div class="publish-info-item"><div class="publish-info-label">发布人</div><div class="publish-info-value">${rpEsc(d.published_by)}</div></div><div class="publish-info-item"><div class="publish-info-label">报告编号</div><div class="publish-info-value">${rpEsc(d.report_no)}</div></div><div class="publish-info-item"><div class="publish-info-label">版本</div><div class="publish-info-value">${d.version?`V${rpEsc(d.version)}.0`:'—'}</div></div></div></section>${reportInfo}${timeline}${distribution}${attachments}<footer class="footer-bar"><div class="footer-left"><button id="rpBack" class="summary-btn secondary">← 上一步</button></div><div class="footer-right"><button id="rpPrint" class="summary-btn secondary">打印报告</button><button id="rpExport" class="summary-btn secondary">导出PDF</button>${published?`<button id="rpSendToSales" class="summary-btn secondary" data-report-handoff="sales">⇪ 回传销售经理继续报价</button>`:''}${['approved','published'].includes(status)?`<button id="rpPrimary" class="summary-btn primary">＋ ${primary}</button>`:''}</div></footer>`;document.querySelector('#rpBack').onclick=()=>location.href=`report-review.html?project=${encodeURIComponent(rpPid)}`;document.querySelector('#rpPrint').onclick=()=>window.print();document.querySelector('#rpExport').onclick=()=>{rpToast('请在系统打印窗口中选择“存储为 PDF”。');window.print();};const primaryButton=document.querySelector('#rpPrimary');if(primaryButton)primaryButton.onclick=rpPrimaryAction;const salesButton=document.querySelector('#rpSendToSales');if(salesButton)salesButton.onclick=rpSendReportToSales;}
rpStart();

/* 重新拉报告与汇总并重绘。原先它写在 rpRegisterTechBoardActions 闭包里，而
   rpSendReportToSales()（点「⇪ 回传销售经理继续报价」的入口）定义在闭包外调用它 ——
   一跑就是 ReferenceError: rpRefreshReport is not defined。提到模块作用域后只有一份实现。 */
async function rpRefreshReport() {
  const [reportResult, aggregate] = await Promise.all([
    api(`/api/projects/${encodeURIComponent(rpPid)}/process-report`),
    api(`/api/projects/${encodeURIComponent(rpPid)}/summary`),
  ]);
  rpReport = reportResult.report || rpReport;
  rpAggregate = aggregate;
  rpView = rpLiveView(rpReport, aggregate);
  rpRender();
  return { ok: true };
}

/* 统一看板协议：3.3 的发布复用既有 rpPrimaryAction；按钮只在可发布 / 可新建时存在。 */
(function rpRegisterTechBoardActions() {
  if (!window.TechBoardRuntime || typeof window.TechBoardRuntime.registerActions !== 'function') return;
  let rpBoardBusy = false;
  function rpBoardPublish(name, extra) {
    const runtime = window.TechBoardRuntime;
    if (!runtime || typeof runtime.publish !== 'function') return;
    try { runtime.publish(name, 'report', Object.assign({ action: name }, extra || {})); }
    catch { /* 进度上报失败不影响业务本身 */ }
  }
  // rpRefreshReport 已提到模块作用域（见文件上方）：闭包外的「回传销售经理继续报价」
  // 也要用它，留在闭包里就是 ReferenceError。
  async function rpSendToQuote() {
    rpBoardPublish('task-progress', { taskId: 'report-to-quote', label: '回传报价', status: 'running' });
    try {
      const result = await api(`/api/projects/${encodeURIComponent(rpPid)}/process-report/send-to-quote`, { method: 'POST', body: JSON.stringify({ note: '工艺评估报告已发布，回传销售经理继续报价。' }) });
      // 看板这条入口也要留痕：先记「最近交接」，再刷新一次把结果区带出来（刷新失败不影响回传本身）。
      rpRememberHandoff(result);
      rpToast([`交接编号 ${(result && result.handoff_id) || ''}`,
               rpSourceState((result && result.source_task) || {}, (result && result.handoff_id) || '')]
              .filter(Boolean).join(' · '));
      try { await rpRefreshReport(); } catch { /* 结果区刷新失败不改变回传已经成功的事实 */ }
      rpBoardPublish('task-completed', { taskId: 'report-to-quote', label: '回传报价', status: 'succeeded' });
      return { ok: true, handoff: (result && result.handoff) || {},
               handoff_id: (result && result.handoff_id) || '',
               source_task: (result && result.source_task) || {} };
    } catch (error) {
      rpBoardPublish('task-failed', { taskId: 'report-to-quote', label: '回传报价', status: 'failed', message: (error && error.message) || '回传失败' });
      return { ok: false, error: { code: 'action-failed', message: (error && error.message) || '回传报价失败' } };
    }
  }
  async function rpNewVersion() {
    rpBoardPublish('task-progress', { taskId: 'report-new-version', label: '新建报告版本', status: 'running' });
    try {
      await api(`/api/projects/${encodeURIComponent(rpPid)}/process-report/new-version`, { method: 'POST' });
      await rpRefreshReport();
      rpBoardPublish('task-completed', { taskId: 'report-new-version', label: '新建报告版本', status: 'succeeded' });
      return { ok: true };
    } catch (error) {
      rpBoardPublish('task-failed', { taskId: 'report-new-version', label: '新建报告版本', status: 'failed', message: (error && error.message) || '新建失败' });
      return { ok: false, error: { code: 'action-failed', message: (error && error.message) || '新建报告版本失败' } };
    }
  }
  window.TechBoardRuntime.registerActions({
    publishProcessReport: {
      label: '发布报告',
      role: 'aux',
      order: 10,
      run: async () => {
        const button = document.querySelector('#rpPrimary');
        if (!button) return { ok: false, error: { code: 'not-ready', message: '报告尚未审核通过，暂不能发布。' } };
        if (rpBoardBusy) return { ok: false, error: { code: 'busy', message: '正在发布，请稍候。' } };
        rpBoardBusy = true;
        try { await rpPrimaryAction(); return { ok: true }; }
        catch (error) { return { ok: false, error: { code: 'action-failed', message: (error && error.message) || '发布失败' } }; }
        finally { rpBoardBusy = false; }
      },
      getState: () => {
        const button = document.querySelector('#rpPrimary');
        // 已审核通过 → 发布报告是本页唯一主按钮；已发布 → 主按钮让位给「回传销售经理继续报价」。
        const status = rpReport?.status || 'draft';
        return { visible: Boolean(button), enabled: Boolean(button) && !button.disabled && !rpBoardBusy,
                 busy: rpBoardBusy, role: status === 'approved' ? 'primary' : 'aux' };
      },
    },
    refreshProcessReport: {
      label: '刷新发布报告',
      role: 'aux',
      order: 40,
      silent: true,
      run: async () => { try { return await rpRefreshReport(); } catch (error) { return { ok: false, error: { code: 'action-failed', message: (error && error.message) || '刷新报告失败' } }; } },
      // 只退出左侧栏：刷新仍由 refresh-data 命令与 Agent 工具走 executeAction 触发。
      getState: () => ({ visible: false, enabled: !rpBoardBusy, busy: rpBoardBusy }),
    },
    sendReportToQuote: {
      label: '回传销售经理继续报价',
      role: 'aux',
      order: 20,
      run: () => rpSendToQuote(),
      // 只有报告正式发布之后才出现回传入口：发布完成后它就是这一步的主操作（幂等重发）。
      getState: () => {
        const status = rpReport?.status || 'draft';
        return { visible: status === 'published', enabled: !rpBoardBusy, busy: rpBoardBusy,
                 role: status === 'published' ? 'primary' : 'aux' };
      },
    },
    createReportNewVersion: {
      label: '新建报告版本',
      role: 'aux',
      order: 30,
      run: () => rpNewVersion(),
      getState: () => ({ visible: true, enabled: !rpBoardBusy, busy: rpBoardBusy }),
    },
  });
})();
