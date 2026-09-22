/* 1.3 审核需求：严格对应参考页面的结构，审批动作接入真实需求状态机。 */
const rrPid = TechProjectContext.bind().project;
let rrRequirement=null, rrGaps=[];
const rrEsc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
function rrToast(message,error=false){const el=document.createElement('div');el.className=`page-toast${error?' error':''}`;el.textContent=message;document.body.append(el);setTimeout(()=>el.remove(),3600)}
function rrLabel(key,value){const map={requirement_type:{new:'全新评估',iteration:'迭代评估',change:'技术变更评估'},priority:{urgent:'紧急',high:'高',medium:'中',low:'低'},bu:{bu1:'半导体精密零部件事业部',bu2:'精密装备事业部'},customer_type:{new:'新客户',old:'老客户'},customer_industry:{foundry:'晶圆代工',idm:'IDM',equipment:'半导体设备制造',other:'其他'}};return map[key]?.[value]||value||'—'}
function rrSourceUrl(){const token=localStorage.getItem('authToken')||localStorage.getItem('cad_engine_token');const base=`/api/projects/${encodeURIComponent(rrPid)}/source`;return token?`${base}?token=${encodeURIComponent(token)}`:base}
function rrPdfUrl(download=false){const token=localStorage.getItem('authToken')||localStorage.getItem('cad_engine_token'),params=new URLSearchParams();if(download)params.set('download','true');if(token)params.set('token',token);const query=params.toString();return `/api/projects/${encodeURIComponent(rrPid)}/requirement/pdf${query?`?${query}`:''}`}
function rrStatusClass(status){return status==='approved'?'approved':status==='rejected'?'rejected':''}
function rrIcon(kind){return kind==='pdf'?'<svg width="48" height="48" viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm-1 2l5 5h-5V4zM6 20V4h5v7h7v9H6z"/></svg>':'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'}
function rrRender(){const data=rrRequirement.data||{},status=rrRequirement.status==='pending_review'?'待审核':statusLabel(rrRequirement.status),fields=[['需求编号',rrRequirement.requirement_no],['需求名称',rrRequirement.title],['需求类型',rrLabel('requirement_type',data.requirement_type)],['优先级',rrLabel('priority',data.priority)],['BU',rrLabel('bu',data.bu)],['创建人',rrRequirement.created_by],['新旧客户',rrLabel('customer_type',data.customer_type)],['最终客户',data.final_customer_name],['客户行业',rrLabel('customer_industry',data.customer_industry)],['项目编码',data.project_code],['年预测量',data.annual_forecast],['期望交付',data.first_sample_due]];document.querySelector('#app').innerHTML=`<section class="workflow-section"><div class="main-workflow"><div class="main-step-wrapper"><div class="main-step active"><div class="main-step-number">1</div><span>接受工艺评估需求</span></div><div class="sub-labels-row"><span class="sub-label completed">1.1 创建</span><span class="sub-label-arrow">→</span><span class="sub-label completed">1.2 确认</span><span class="sub-label-arrow">→</span><span class="sub-label active">1.3 审核</span></div></div><div class="main-connector"></div><div class="main-step-wrapper"><div class="main-step pending"><div class="main-step-number">2</div><span>解析技术工艺过程</span></div></div><div class="main-connector"></div><div class="main-step-wrapper"><div class="main-step pending"><div class="main-step-number">3</div><span>输出工艺评估结果</span></div></div></div></section><section class="title-section"><div class="title-row"><h1 class="form-title">${rrEsc(rrRequirement.title||'工艺评估需求单')}</h1><span class="status-badge ${rrStatusClass(rrRequirement.status)}">${rrEsc(status)}</span></div></section><section class="pdf-section"><div class="pdf-header"><div class="pdf-title">${rrIcon('doc')}工艺评估需求表单（点击查看详情）</div></div><div class="pdf-preview"><button class="pdf-thumbnail" id="previewForm" type="button">${rrIcon('pdf')}<span>预览 PDF</span></button><div class="pdf-info"><div class="pdf-info-title">需求基本信息</div><div class="info-list">${fields.map(([label,value])=>`<div class="info-item"><span class="info-label">${label}</span><span class="info-value">${rrEsc(value||'—')}</span></div>`).join('')}</div><div class="pdf-actions"><button class="pdf-action-btn" id="viewForm" type="button">◉ 查看完整表单</button><button class="pdf-action-btn" id="downloadForm" type="button">⇩ 下载 PDF</button></div></div></div></section><section class="opinion-section"><div class="section-title">▢ 审核结果</div><div class="radio-group"><label class="radio-item"><input type="radio" name="opinion" value="approve" class="radio-input" checked><span class="radio-circle"></span><span class="radio-label">通过</span></label><label class="radio-item"><input type="radio" name="opinion" value="reject" class="radio-input"><span class="radio-circle"></span><span class="radio-label">驳回</span></label></div><textarea class="opinion-textarea" id="reviewText" placeholder="请输入审核说明..." disabled>${rrEsc(rrRequirement.review_note||'')}</textarea><div class="opinion-hint" id="reviewHint">通过无需填写审核说明</div></section><footer class="footer-bar"><div class="footer-left"><a class="btn btn-secondary" href="requirement-confirm.html?project=${encodeURIComponent(rrPid)}">← 上一步</a></div><div class="footer-right"><button class="btn btn-primary" id="submitReview" type="button">提交审核意见</button></div></footer><section class="history-section"><div class="section-title">▤ 流程留痕</div>${rrHistoryHtml()}</section><div class="modal-overlay" id="formModal"><div class="modal"><div class="modal-header"><span class="modal-title">工艺评估需求单</span><button class="modal-close" id="closeModal" type="button">×</button></div><div class="modal-content"><img class="source-preview" src="${rrSourceUrl()}" alt="项目原始图纸"><a class="modal-link" href="requirement-create.html?project=${encodeURIComponent(rrPid)}">打开完整需求表单</a></div></div></div>`;rrBind()}
function rrBind(){const pdfUrl=rrPdfUrl(),oldPreview=document.querySelector('#previewForm'),frame=document.createElement('div');frame.id='previewForm';frame.className='pdf-thumbnail pdf-thumbnail-frame';frame.innerHTML=`<iframe src="${rrEsc(pdfUrl)}#toolbar=0&navpanes=0&scrollbar=0" title="工艺评估需求单 PDF 预览"></iframe><span>PDF 表单预览</span>`;oldPreview.replaceWith(frame);const modal=document.querySelector('#formModal'),open=()=>modal.classList.add('active'),close=()=>modal.classList.remove('active'),radios=[...document.querySelectorAll('input[name="opinion"]')],text=document.querySelector('#reviewText'),hint=document.querySelector('#reviewHint');modal.querySelector('.modal-content').innerHTML=`<iframe class="requirement-pdf-full" src="${rrEsc(pdfUrl)}" title="完整工艺评估需求单 PDF"></iframe><a class="modal-link" href="${rrEsc(rrPdfUrl(true))}">下载 PDF 表单</a>`;document.querySelector('#previewForm').onclick=open;document.querySelector('#viewForm').onclick=open;document.querySelector('#closeModal').onclick=close;modal.onclick=event=>{if(event.target===modal)close()};document.querySelector('#downloadForm').onclick=()=>{window.location.href=rrPdfUrl(true)};radios.forEach(radio=>radio.onchange=()=>{const reject=radio.value==='reject'&&radio.checked;text.disabled=!reject;if(reject){text.focus();hint.textContent='如驳回，可补充审核说明（选填）'}else{text.value='';hint.textContent='通过无需填写审核说明'}});document.querySelector('#submitReview').onclick=rrSubmit}
// 右侧显示审核状态与流程留痕：复用 workflow.js 的 renderHistory 渲染 rrRequirement.history
// （含 reviewed_by / reviewed_at / review_note），独立打开或留痕缺失时安全降级。
function rrHistoryHtml() {
  try {
    if (typeof renderHistory === 'function') return renderHistory(rrRequirement.history || []);
  } catch (_) { /* 留痕渲染失败不影响审核主流程 */ }
  return '';
}

// 退回草稿：与 1.2 页（`requirement-confirm-page.js` 的 `CF_RETURNABLE_STATUSES`）同一份口径
// （Spec `packaging-requirement-confirm-order-guard.md` §2.2 第 7/9 条）。退回是**动作**，不是
// 「待审核」动作的前置：approve 之后人就在 1.3 这一页，这里必须还能退回草稿 —— 否则图纸解析
// 第 8 步提示的「请先退回草稿」只有直接调 API 才走得到。状态集只落一处（1.2 页声明），这里引用。
const RR_RETURNABLE_STATUSES = (typeof CF_RETURNABLE_STATUSES !== 'undefined')
  ? CF_RETURNABLE_STATUSES
  : ['pending_confirmation', 'pending_review', 'approved'];
function rrCanReturn(){return RR_RETURNABLE_STATUSES.includes(rrRequirement&&rrRequirement.status)}
async function rrReturnToDraft(){
  if(!rrCanReturn()){rrToast('当前需求已经是草稿，不需要退回。',true);return {ok:false,error:{code:'invalid-status',message:'当前需求已经是草稿。'}};}
  const note=document.querySelector('#reviewText');
  const comment=note?String(note.value||'').trim():'';
  const taskId='requirement-review-return';
  rrPublishTaskEvent('task-progress',{taskId,status:'running',progress:'正在退回草稿…'});
  try{
    await api(`/api/projects/${rrPid}/requirement/return-to-draft`,{method:'POST',body:JSON.stringify({comment})});
    rrPublishTaskEvent('task-completed',{taskId,status:'succeeded'});
    rrToast('需求已退回草稿，补齐后可重新提交确认。');
    setTimeout(()=>{if(window.TechEmbed&&window.TechEmbed.embedded){window.TechEmbed.requestNavigate('requirement-create',rrPid);return;}location.href=`requirement-create.html?project=${encodeURIComponent(rrPid)}`},400);
    return {ok:true};
  }catch(err){
    rrPublishTaskEvent('task-failed',{taskId,status:'failed',error:err.message});
    rrToast(err.message,true);
    return {ok:false,error:{code:'action-failed',message:err.message}};
  }
}
// 脚注里的退路按钮按状态挂载（复用本文件既有的 rrRender 包装写法，不动 rrRender 内部模板）。
function rrMountReturnDraft(){
  const bar=document.querySelector('.footer-right');
  if(!bar)return;
  let button=bar.querySelector('#returnDraft');
  if(!rrCanReturn()){if(button)button.remove();return;}
  if(!button){
    button=document.createElement('button');
    button.type='button';
    button.id='returnDraft';
    button.className='btn btn-secondary';
    button.textContent='× 退回草稿';
    bar.insertBefore(button,bar.firstChild);
  }
  button.onclick=rrReturnToDraft;
}

// 审核进度经统一看板协议上报父壳（独立打开时静默），不能只发 iframe 内 window 事件。
function rrPublishTaskEvent(type, extra) {
  try {
    if (window.TechBoardRuntime && typeof window.TechBoardRuntime.publish === 'function') {
      window.TechBoardRuntime.publish(type, 'submitRequirementReview', extra || {});
    }
  } catch (_) { /* 独立打开无运行时 */ }
}

async function rrSubmit(){const decision=document.querySelector('input[name="opinion"]:checked').value,comment=document.querySelector('#reviewText').value.trim();if(rrRequirement.status!=='pending_review'){rrToast('当前需求不在待审核状态，请先完成前序确认。',true);return {ok:false,error:{code:'invalid-status',message:'当前需求不在待审核状态。'}};}let waiver=null;if(decision==='approve'&&rrGaps.length){const go=window.confirm(`这一步还有 ${rrGaps.length} 项没完成：\n${rrGaps.join('、')}\n\n确定要带着这些缺口批准通过吗？（点「确定」＝仍要继续）`);if(!go){rrToast(`已停在审核这一步，请先补齐：${rrGaps.join('、')}`,true);return {ok:false,error:{code:'gap-unconfirmed',message:`还有没完成的项：${rrGaps.join('、')}`}};}waiver={reason:`带缺口继续：${rrGaps.join('、')}`,missing_fields:rrGaps};}const taskId=`requirement-review-${decision}`;rrPublishTaskEvent('task-progress',{taskId,status:'running',progress:decision==='approve'?'正在提交审核通过…':'正在提交审核退回…'});try{await api(`/api/projects/${rrPid}/requirement/review`,{method:'POST',body:JSON.stringify(waiver?{decision,comment,waiver}:{decision,comment})});rrPublishTaskEvent('task-completed',{taskId,status:'succeeded'});rrToast(decision==='approve'?'审核通过，已进入图纸解析。':'需求已驳回，已退回创建页。');setTimeout(()=>{if(window.TechEmbed&&window.TechEmbed.embedded){window.TechEmbed.requestNavigate(decision==='approve'?'drawing':'requirement-create',rrPid);return;}location.href=decision==='approve'?`index.html?project=${encodeURIComponent(rrPid)}`:`requirement-create.html?project=${encodeURIComponent(rrPid)}`},400);return {ok:true};}catch(err){rrPublishTaskEvent('task-failed',{taskId,status:'failed',error:err.message});rrToast(err.message,true);return {ok:false,error:{code:'action-failed',message:err.message}};}}
async function rrStart(){if(!rrPid){if(window.TechEmbed&&window.TechEmbed.embedded){window.TechEmbed.exitToTechHome();return}location.href='home.html';return}try{const [response,pre]=await Promise.all([api(`/api/projects/${rrPid}/requirement`),api(`/api/projects/${rrPid}/requirement/precheck`).catch(()=>({}))]);rrRequirement=response.requirement;rrGaps=(pre&&pre.gaps&&Array.isArray(pre.gaps.labels))?pre.gaps.labels.slice():[];if(!rrRequirement){window.TechEmbed&&window.TechEmbed.embedded?window.TechEmbed.navigateToFile('requirement-create.html',rrPid):location.href=`requirement-create.html?project=${encodeURIComponent(rrPid)}`;return}rrRender()}catch(err){document.querySelector('#app').innerHTML=`<div class="page-toast error" style="position:static">页面加载失败：${rrEsc(err.message)}</div>`}}
rrStart();

// 审核页沿用确认页展示口径，确保信用等级在流程中可追溯。
// 渲染出的状态徽标文本原样上报给父壳（统一标题行提示位）；独立打开（无运行时）时不通信。
function rrPublishStatus(){const badge=document.querySelector('.title-section .title-row .status-badge');const text=(badge&&badge.textContent||'').trim();const runtime=window.TechBoardRuntime;if(runtime&&typeof runtime.publishStatus==='function')runtime.publishStatus(text,'info');}
// 1.3 页的退路按钮随每次重绘挂载（rrPublishStatus 由 rrRender 的既有包装在重绘后调用一次）。
const rrPublishStatusWithReturnDraft = rrPublishStatus;
rrPublishStatus = function () { rrPublishStatusWithReturnDraft(); rrMountReturnDraft(); };
const rrRenderWithCustomerCredit=rrRender;
rrRender=function(){rrRenderWithCustomerCredit();rrPublishStatus();const list=document.querySelector('.info-list');if(!list)return;const value=String(rrRequirement?.data?.customer_credit||'').trim()||'—';list.insertAdjacentHTML('beforeend',`<div class="info-item"><span class="info-label">客户信用等级</span><span class="info-value">${rrEsc(value)}</span></div>`)};

/* 统一看板协议：提交审核复用既有 rrSubmit，父壳只发动作名；校验前置条件后返回结构化结果。 */
(function rrRegisterTechBoardActions() {
  if (!window.TechBoardRuntime || typeof window.TechBoardRuntime.registerActions !== 'function') return;
  let rrBoardBusy = false;
  window.TechBoardRuntime.registerActions({
    // Agent（SaveRequirementReviewNote）起草的审核意见带回看板：复用 rrBind 的单选联动
    // 选中审核结果，并把意见追加进既有 #reviewText，不覆盖人工已写内容、不动任何状态。
    applyReviewNote: {
      label: '带入审核意见',
      role: 'aux',
      order: 20,
      run: ({ decision, note } = {}) => {
        const text = document.querySelector('#reviewText');
        const target = String(decision || '').trim().toLowerCase();
        if (target) {
          const radio = [...document.querySelectorAll('input[name="opinion"]')]
            .find(node => node.value === target);
          if (!radio) return { ok: false, error: { code: 'unknown-decision', message: `未知的审核结果：${target}` } };
          radio.checked = true;
          radio.dispatchEvent(new Event('change', { bubbles: true }));
        }
        const value = String(note || '').trim();
        if (value && text) {
          const checked = document.querySelector('input[name="opinion"]:checked');
          const isReject = Boolean(checked) && checked.value === 'reject';
          text.disabled = !isReject;
          text.value = text.value ? (text.value.includes(value) ? text.value : `${text.value}\n${value}`) : value;
          if (isReject) text.focus();
        }
        return { ok: true };
      },
      getState: () => ({ visible: true, enabled: Boolean(document.querySelector('#reviewText')), busy: rrBoardBusy }),
    },
    // Agent 改完审核状态（ApproveRequirementReview / RejectRequirementReview）后左侧只发
    // refresh-data：复用既有 rrStart() 重新拉需求单与留痕并重绘，不新增读取逻辑。
    refreshData: {
      label: '刷新需求审核页',
      role: 'aux',
      order: 30,
      silent: true,
      run: async () => { await rrStart(); return { ok: true }; },
      // 只退出左侧栏：刷新仍由 refresh-data 命令与 Agent 工具走 executeAction 触发。
      getState: () => ({ visible: false, enabled: Boolean(document.querySelector('#submitReview')), busy: rrBoardBusy }),
    },
    submitRequirementReview: {
      label: '提交审核意见',
      role: 'primary',
      order: 10,
      run: async function submitRequirementReview() {
        if (rrBoardBusy) return { ok: false, error: { code: 'busy', message: '正在提交，请稍候。' } };
        const checked = document.querySelector('input[name="opinion"]:checked');
        if (!checked) return { ok: false, error: { code: 'no-selection', message: '请选择审核意见。' } };
        if (rrRequirement && rrRequirement.status !== 'pending_review') {
          return { ok: false, error: { code: 'invalid-status', message: '当前需求不在待审核状态，请先完成前序确认。' } };
        }
        rrBoardBusy = true;
        try { return await rrSubmit(); }
        catch (error) { return { ok: false, error: { code: 'action-failed', message: (error && error.message) || '提交失败' } }; }
        finally { rrBoardBusy = false; }
      },
      getState: () => {
        const button = document.querySelector('#submitReview');
        return { visible: true, enabled: Boolean(button) && !rrBoardBusy, busy: rrBoardBusy };
      },
    },
  });
})();
