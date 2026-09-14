/* 1.2 确认需求：默认展示本地规则；仅点击“AI 检查”才会调用模型。 */
const cfPid = new URLSearchParams(location.search).get('project') || localStorage.getItem('cad_engine_project_id') || '';
let cfRequirement = null, cfProject = null, cfPrecheck = null;
const cfEsc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));

function cfToast(message, error = false) { const el = document.createElement('div'); el.className = `page-toast${error ? ' error' : ''}`; el.textContent = message; document.body.append(el); setTimeout(() => el.remove(), 3600); }
function cfLabel(key, value) { const maps = {requirement_type:{new:'全新评估',iteration:'迭代评估',change:'技术变更评估'},priority:{urgent:'紧急',high:'高',medium:'中',low:'低'},bu:{bu1:'半导体精密零部件事业部',bu2:'精密装备事业部'},customer_type:{new:'新客户',old:'老客户'},customer_industry:{foundry:'晶圆代工',idm:'IDM',equipment:'半导体设备制造',other:'其他'},appliance_category:{refrigerator:'冰箱',washer:'洗衣机',ac:'空调',kitchen:'厨房电器',small:'小家电',other:'其他'},energy_efficiency_grade:{g1:'一级',g2:'二级',g3:'三级',tbd:'待定'},housing_material:{abs:'ABS 塑料',hips:'HIPS 片材',vcm:'VCM 覆膜板',spcc:'冷轧板 SPCC',glass:'钢化玻璃',other:'其他'},surface_process:{brushed:'拉丝',spray:'喷涂',laminated:'覆膜',ecoat:'电泳',none:'免处理'},certification_region:{ccc:'中国 CCC',ce:'欧盟 CE',ul:'北美 UL',multi:'多区域'}}; return maps[key]?.[value] || value || '—'; }
const CF_FIELD_LABELS={title:'需求名称',requirement_type:'需求类型',priority:'优先级',bu:'BU',disclosure:'披露口径',description:'需求描述',customer_type:'新旧客户',customer_industry:'客户行业分类',account_manager:'客户经理',final_customer_name:'最终客户名称',transaction_customer_name:'交易客户名称',project_name:'项目名称',project_code:'项目编码',product_iteration:'全新或迭代',project_manager:'项目经理',technical_contact:'技术对接人',product_name:'产品名称',product_model:'产品型号',wafer_size:'晶圆尺寸',chuck_type:'静电吸盘类型',temperature_zones:'温区数量',ceramic_material:'陶瓷基体材料',electrode_material:'电极材料',base_material:'金属基座材质',product_weight:'产品重量',overall_dimensions:'外形尺寸',ttv:'平面度（TTV）要求',roughness:'表面粗糙度（Ra）要求',micro_hole_diameter:'微孔孔径',micro_hole_diameter_tolerance:'微孔孔径公差',micro_hole_depth_tolerance:'微孔深度公差',mesa_height:'微凸台高度',adsorption_uniformity:'吸附力均匀性',temperature_range:'工作温度范围',max_voltage:'最高使用电压',leakage_current:'漏电流要求',helium_leak_rate:'氦气漏率要求',cleanliness:'洁净度等级',service_life:'使用寿命要求',target_equipment:'目标设备类型',process_stage:'适用工艺环节',vacuum_environment:'真空环境要求',heating:'是否含加热功能',annual_forecast:'年预测量',lifetime_forecast:'生命周期总预测',first_sample_due:'期望首样交付时间',mass_production_due:'期望量产时间',target_price:'目标售价',competitors:'竞争对手情况',current_situation:'目前状况说明',project_k0:'预估项目 K0 时间',evaluation_due:'期望工艺评估完成日期',project_start_due:'项目启动预计时间',milestones:'关键里程碑节点',category_a:'类型 A',category_b:'类型 B',product_type:'产品类型',complexity:'工艺复杂度等级',new_technology:'是否涉及新技术',technology_source:'技术来源',notes:'备注',related_requirement:'关联需求单号',battery_model:'电芯型号',cathode_material:'正极材料',anode_material:'负极材料',nominal_voltage:'标称电压',gravimetric_energy_density:'质量能量密度',volumetric_energy_density:'体积能量密度',dcir:'直流内阻（DCIR）',battery_operating_temperature:'工作温度范围',thermal_runaway_temperature:'热失控触发温度',crush_puncture_safety:'挤压/针刺安全',cycle_life:'循环寿命',calendar_life:'日历寿命',stacking_process:'叠片工艺',minimalist_packaging:'极简封装',battery_process_other:'其他核心工艺特点',vda_dimensions:'VDA标准尺寸',slim_cell_dimensions:'长薄化尺寸',battery_form_factor:'形状',appliance_category:'产品品类',appliance_model:'产品型号',rated_voltage:'额定电压/频率',rated_power:'额定功率',energy_efficiency_grade:'能效等级',appliance_dimensions:'整机外形尺寸',appliance_weight:'整机净重',key_performance:'关键性能指标',noise_limit:'噪声限值',standby_power:'待机功耗',appliance_operating_temperature:'工作环境温度',appliance_service_life:'整机使用寿命',reliability_test:'可靠性试验要求',housing_material:'主体结构材料',surface_process:'外观表面工艺',insulation_requirement:'保温与密封要求',core_components:'核心部件',forming_process:'关键成型工艺',safety_standard:'安规标准',hipot_requirement:'耐压测试要求',ground_resistance:'接地电阻要求',emc_requirement:'EMC 要求',certification_region:'认证区域'};
function cfRecommendationEntries(){const data=cfRequirement?.data||{},info=data.document_extraction||{},recommendations=info.recommendations||{},confidence=info.recommendation_confidence||{},keys=info.all_recommended_fields||info.recommended_fields||Object.keys(recommendations);return [...new Set(keys)].map(key=>({key,label:CF_FIELD_LABELS[key]||key,value:recommendations[key]||data[key]||'',confidence:Number(confidence[key])})).filter(item=>String(item.value).trim())}
function cfRecommendationSection(){const rows=cfRecommendationEntries(),lowConfidenceRows=rows.filter(row=>!Number.isFinite(row.confidence)||row.confidence<0.6),warning=lowConfidenceRows.length?`<div class="ai-recommendation-warning">⚠ 有 ${lowConfidenceRows.length} 个推荐缺少置信度或低于 60%，请重点核对后再确认。</div>`:'';return `<section class="ai-recommendation-section is-collapsed"><button class="ai-recommendation-header" type="button" aria-expanded="false"><span class="ai-recommendation-heading"><span class="section-title">AI 推荐默认值</span><span class="ai-recommendation-toggle-hint">点击标题展开</span></span><span class="ai-recommendation-legend">AI 推荐</span></button><div class="ai-recommendation-body"><p class="ai-recommendation-description">黄色内容表示 AI 根据资料和行业经验给出的建议值，请在确认前人工核对；百分比为推荐置信度。</p>${warning}${rows.length?`<div class="ai-recommendation-list">${rows.map(row=>{const low=!Number.isFinite(row.confidence)||row.confidence<0.6,confidence=Number.isFinite(row.confidence)?`${Math.round(Math.max(0,Math.min(1,row.confidence))*100)}%`:'未提供';return `<div class="ai-recommendation-item${low?' low-confidence':''}"><span class="ai-recommendation-label">${cfEsc(row.label)}</span><span class="ai-recommendation-value">${cfEsc(row.value)}</span><span class="ai-recommendation-confidence">${low?'⚠ ':''}置信度 ${confidence}${low?' · 重点核对':''}</span><span class="ai-recommendation-status">待确认</span></div>`}).join('')}</div>`:'<div class="ai-recommendation-empty">本次 AI 没有生成可用的推荐默认值。</div>'}</div></section>`}
function cfMountRecommendationSection(){const anchor=document.querySelector('.pdf-section');if(!anchor||document.querySelector('.ai-recommendation-section'))return;anchor.insertAdjacentHTML('afterend',cfRecommendationSection())}
function cfBindRecommendationToggle(){const section=document.querySelector('.ai-recommendation-section'),header=section?.querySelector('.ai-recommendation-header');if(!section||!header)return;header.onclick=()=>{const expanded=section.classList.toggle('is-expanded');section.classList.toggle('is-collapsed',!expanded);header.setAttribute('aria-expanded',String(expanded));header.querySelector('.ai-recommendation-toggle-hint').textContent=expanded?'点击标题收起':'点击标题展开';}}
function cfIcon(kind) { return kind === 'pdf' ? '<svg width="48" height="48" viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm-1 2l5 5h-5V4zM6 20V4h5v7h7v9H6z"/></svg>' : '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>'; }
function cfAiNote() { if (cfPrecheck?.engine !== 'qwen') return '结构化规则检查，未调用模型'; const model = cfPrecheck.model_source === 'runtime_actual' ? String(cfPrecheck.model || '').trim() : ''; return `AI 检查${model ? ` · 模型：${model}` : ' · 实际模型未留痕，请重新检查'} · ${cfPrecheck.checked_at || '刚刚完成'}`; }
function cfPrecheckRows() {
  const items = Array.isArray(cfPrecheck?.items) ? cfPrecheck.items : [];
  if (!items.length) return '<tr><td class="item-name" colspan="3">暂无检查结果，请点击“AI 检查”重新生成。</td></tr>';
  return items.map((row, index) => {
    const status = String(row?.status || 'need_info').toLowerCase() === 'ok' ? 'ok' : 'need_info';
    const result = status === 'ok' ? '✓ 已确认' : '⚠ 待补充';
    const item = String(row?.item || `检查项 ${index + 1}`);
    const detail = String(row?.detail || (status === 'ok' ? '已具备' : '请补充相关信息'));
    const rowClass = /^([一二三四五六七]、|3\.)/.test(item) ? ' section-row' : '';
    return `<tr class="${rowClass.trim()}"><td class="item-name">${cfEsc(item)}</td><td class="confirm-result"><span class="${status === 'ok' ? 'ok' : 'need-info'}">${result}</span></td><td class="confirm-supplement">${cfEsc(detail)}</td></tr>`;
  }).join('');
}
function cfSourceUrl() { const token = localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token'); const base = `/api/projects/${encodeURIComponent(cfPid)}/source`; return token ? `${base}?token=${encodeURIComponent(token)}` : base; }
function cfPdfUrl(download = false) { const token = localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token'); const params = new URLSearchParams(); if (download) params.set('download', 'true'); if (token) params.set('token', token); const query = params.toString(); return `/api/projects/${encodeURIComponent(cfPid)}/requirement/pdf${query ? `?${query}` : ''}`; }

function cfRender() {
  const data = cfRequirement.data || {};
  const status = cfRequirement.status === 'pending_confirmation' ? '待确认' : cfRequirement.status === 'draft' ? '草稿（请先提交）' : statusLabel(cfRequirement.status);
  const fields = [['需求编号',cfRequirement.requirement_no],['需求名称',cfRequirement.title],['需求类型',cfLabel('requirement_type',data.requirement_type)],['优先级',cfLabel('priority',data.priority)],['BU',cfLabel('bu',data.bu)],['创建人',cfRequirement.created_by],['新旧客户',cfLabel('customer_type',data.customer_type)],['最终客户',data.final_customer_name],['客户行业',cfLabel('customer_industry',data.customer_industry)],['项目编码',data.project_code],['年预测量',data.annual_forecast],['期望交付',data.first_sample_due]];
  document.querySelector('#app').innerHTML = `<section class="workflow-section"><div class="main-workflow"><div class="main-step-wrapper"><div class="main-step active"><div class="main-step-number">1</div><span>接受工艺评估需求</span></div><div class="sub-labels-row"><span class="sub-label completed">1.1 创建</span><span class="sub-label-arrow">→</span><span class="sub-label active">1.2 确认</span><span class="sub-label-arrow">→</span><span class="sub-label">1.3 审核</span></div></div><div class="main-connector"></div><div class="main-step-wrapper"><div class="main-step pending"><div class="main-step-number">2</div><span>解析技术工艺过程</span></div></div><div class="main-connector"></div><div class="main-step-wrapper"><div class="main-step pending"><div class="main-step-number">3</div><span>输出工艺评估结果</span></div></div></div></section><section class="title-section"><div class="title-row"><h1 class="form-title">${cfEsc(cfRequirement.title || '工艺评估需求单')}</h1><span class="status-badge">${cfEsc(status)}</span></div></section><section class="pdf-section"><div class="pdf-header"><div class="pdf-title">${cfIcon('doc')}工艺评估需求表单（点击查看详情）</div></div><div class="pdf-preview"><button class="pdf-thumbnail" id="previewForm" type="button">${cfIcon('pdf')}<span>预览 PDF</span></button><div class="pdf-info"><div class="pdf-info-title">需求基本信息</div><div class="info-list">${fields.map(([label,value]) => `<div class="info-item"><span class="info-label">${label}</span><span class="info-value">${cfEsc(value || '—')}</span></div>`).join('')}</div><div class="pdf-actions"><button class="pdf-action-btn" id="viewForm" type="button">◉ 查看完整表单</button><button class="pdf-action-btn" id="downloadForm" type="button">⇩ 下载 PDF</button><button class="pdf-action-btn ai-check-btn" id="runAiCheck" type="button">⚡ AI 检查</button></div></div></div></section><section class="ai-check-section"><div class="section-title">⚡ AI 检查结果</div><div class="ai-result-box"><div class="ai-result-header"><span class="ai-badge">⚡ 工艺评估需求单确认表</span><span class="ai-note">${cfEsc(cfAiNote())}</span></div><table class="confirm-table"><thead><tr><th>确认项目</th><th>确认结果</th><th>补充说明</th></tr></thead><tbody>${cfPrecheckRows()}</tbody></table></div></section><section class="opinion-section"><div class="section-title"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>提交意见</div><textarea id="confirmationNote" class="opinion-textarea" maxlength="3000" placeholder="请输入审批意见...">${cfEsc(cfRequirement.confirmation_note || '')}</textarea><div class="opinion-hint"><span style="color:var(--color-red)">*</span> 必填，最多可输入 3000 字</div></section><footer class="footer-bar"><div class="footer-left"><a class="btn btn-secondary" href="requirement-create.html?project=${encodeURIComponent(cfPid)}">← 上一步</a></div><div class="footer-right"><button class="btn btn-secondary" id="bringAi" type="button">⚡ AI 结果带入</button><button class="btn btn-danger" id="returnDraft" type="button">× 驳回</button><button class="btn btn-primary" id="confirmPass" type="button">✓ 通过</button></div></footer><div class="modal-overlay" id="formModal"><div class="modal"><div class="modal-header"><span class="modal-title">工艺评估需求单</span><button class="modal-close" id="closeModal" type="button">×</button></div><div class="modal-content"><img class="source-preview" src="${cfSourceUrl()}" alt="项目原始图纸"><a class="modal-link" href="requirement-create.html?project=${encodeURIComponent(cfPid)}">打开可编辑的完整需求表单</a></div></div></div>`;
  cfBind();
  cfPublishStatus();
}

// 渲染出的状态徽标文本原样上报给父壳（统一标题行提示位）；独立打开（无运行时）时不通信。
function cfPublishStatus(){const badge=document.querySelector('.title-section .title-row .status-badge');const text=(badge&&badge.textContent||'').trim();const runtime=window.TechBoardRuntime;if(runtime&&typeof runtime.publishStatus==='function')runtime.publishStatus(text,'info');}

function cfBind() {
  cfMountRecommendationSection();
  cfBindRecommendationToggle();
  const pdfUrl = cfPdfUrl();
  const oldPreview = document.querySelector('#previewForm');
  oldPreview.replaceWith(Object.assign(document.createElement('div'), {id:'previewForm', className:'pdf-thumbnail pdf-thumbnail-frame', innerHTML:`<iframe src="${cfEsc(pdfUrl)}#toolbar=0&navpanes=0&scrollbar=0" title="工艺评估需求单 PDF 预览"></iframe><span>PDF 表单预览</span>`}));
  const modal = document.querySelector('#formModal'), open = () => modal.classList.add('active'), close = () => modal.classList.remove('active');
  modal.querySelector('.modal-content').innerHTML = `<iframe class="requirement-pdf-full" src="${cfEsc(pdfUrl)}" title="完整工艺评估需求单 PDF"></iframe><a class="modal-link" href="${cfEsc(cfPdfUrl(true))}">下载 PDF 表单</a>`;
  document.querySelector('#previewForm').onclick = open; document.querySelector('#viewForm').onclick = open; document.querySelector('#closeModal').onclick = close; modal.onclick = event => { if (event.target === modal) close(); };
  document.querySelector('#downloadForm').onclick = () => { window.location.href = cfPdfUrl(true); }; document.querySelector('#runAiCheck').onclick = cfRunAiCheck;
  document.querySelector('#bringAi').onclick = () => { const area = document.querySelector('#confirmationNote'); area.value = area.value ? `${area.value}\n${cfPrecheck.generated_note}` : cfPrecheck.generated_note; area.focus(); };
  document.querySelector('#confirmPass').onclick = () => cfAct('confirm'); document.querySelector('#returnDraft').onclick = () => cfAct('return');
}

async function cfRunAiCheck() {
  const button = document.querySelector('#runAiCheck'); button.disabled = true; button.textContent = '⏳ AI 检查中…';
  try { const response = await api(`/api/projects/${cfPid}/requirement/ai-check`, {method:'POST'}); cfPrecheck = response.check; cfRequirement.ai_check = response.check; cfRender(); cfToast('AI 检查完成，结果已保存。'); }
  catch (err) { button.disabled = false; button.textContent = '⚡ AI 检查'; cfToast(`AI 检查失败：${err.message}`, true); }
}

// 确认进度经统一看板协议上报父壳（独立打开时静默），不能只发 iframe 内 window 事件。
function cfPublishTaskEvent(type, extra) {
  try {
    if (window.TechBoardRuntime && typeof window.TechBoardRuntime.publish === 'function') {
      window.TechBoardRuntime.publish(type, 'confirmRequirement', extra || {});
    }
  } catch (_) { /* 独立打开无运行时 */ }
}

// 复用 #bringAi 的「带入」写法：只追加，不覆盖人工已写内容，也不重复追加同一段。
function cfAppendNote(note) {
  const area = document.querySelector('#confirmationNote');
  const text = String(note || '').trim();
  if (!area || !text) return false;
  area.value = area.value ? (area.value.includes(text) ? area.value : `${area.value}\n${text}`) : text;
  area.focus();
  return true;
}

async function cfAct(kind) {
  const comment = document.querySelector('#confirmationNote').value.trim();
  if (!comment) { cfToast('请填写提交意见。', true); return { ok: false, error: { code: 'missing-comment', message: '请填写提交意见。' } }; }
  if (cfRequirement.status !== 'pending_confirmation') { cfToast('当前需求尚未提交至确认环节，请先返回上一步点击“提交”。', true); return { ok: false, error: { code: 'invalid-status', message: '当前需求尚未提交至确认环节。' } }; }
  const taskId = `requirement-confirm-${kind}`;
  const label = kind === 'confirm' ? '通过确认' : '退回草稿';
  cfPublishTaskEvent('task-progress', { taskId, status: 'running', progress: `正在${label}…` });
  try {
    const url = kind === 'confirm' ? `/api/projects/${cfPid}/requirement/confirm` : `/api/projects/${cfPid}/requirement/return-to-draft`;
    await api(url,{method:'POST',body:JSON.stringify({comment})});
    cfPublishTaskEvent('task-completed', { taskId, status: 'succeeded' });
    if(window.TechEmbed&&window.TechEmbed.embedded){window.TechEmbed.requestNavigate(kind==='confirm'?'requirement-review':'requirement-create',cfPid);}else{location.href = kind === 'confirm' ? `requirement-review.html?project=${encodeURIComponent(cfPid)}` : `requirement-create.html?project=${encodeURIComponent(cfPid)}`;}
    return { ok: true };
  } catch (err) {
    cfPublishTaskEvent('task-failed', { taskId, status: 'failed', error: err.message });
    cfToast(err.message, true);
    return { ok: false, error: { code: 'action-failed', message: err.message } };
  }
}

async function cfStart() {
  if (!cfPid) { if(window.TechEmbed&&window.TechEmbed.embedded){window.TechEmbed.exitToTechHome();return;} location.href = 'home.html'; return; }
  try { const [req,project,ruleCheck] = await Promise.all([api(`/api/projects/${cfPid}/requirement`),api(`/api/projects/${cfPid}`),api(`/api/projects/${cfPid}/requirement/precheck`)]); cfRequirement = req.requirement; if (!cfRequirement) { window.TechEmbed&&window.TechEmbed.embedded?window.TechEmbed.navigateToFile('requirement-create.html',cfPid):location.href = `requirement-create.html?project=${encodeURIComponent(cfPid)}`; return; } cfProject = project; cfPrecheck = cfRequirement.ai_check?.engine === 'qwen' ? cfRequirement.ai_check : ruleCheck; cfRender(); }
  catch (err) { document.querySelector('#app').innerHTML = `<div class="page-toast error" style="position:static">页面加载失败：${cfEsc(err.message)}</div>`; }
}
cfStart();

// 客户信用等级是销售主数据，在确认页与 PDF 预览中一并展示。
const cfRenderWithCustomerCredit = cfRender;
cfRender = function () {
  cfRenderWithCustomerCredit();
  const list = document.querySelector('.info-list');
  if (!list) return;
  const value = String(cfRequirement?.data?.customer_credit || '').trim() || '—';
  list.insertAdjacentHTML('beforeend', `<div class="info-item"><span class="info-label">客户信用等级</span><span class="info-value">${cfEsc(value)}</span></div>`);
};

/* 统一看板协议：确认 / 驳回复用既有 cfAct，父壳只发动作名。 */
(function cfRegisterTechBoardActions() {
  if (!window.TechBoardRuntime || typeof window.TechBoardRuntime.registerActions !== 'function') return;
  let cfBoardBusy = false;
  function cfButtonState() {
    const pass = document.querySelector('#confirmPass');
    const back = document.querySelector('#returnDraft');
    return { enabled: Boolean(pass && !pass.disabled && !cfBoardBusy), busy: cfBoardBusy, back: Boolean(back && !back.disabled) };
  }
  async function cfBoardRun(kind) {
    if (cfBoardBusy) return { ok: false, error: { code: 'busy', message: '正在提交，请稍候。' } };
    cfBoardBusy = true;
    try { return await cfAct(kind); }
    catch (error) { return { ok: false, error: { code: 'action-failed', message: (error && error.message) || '提交失败' } }; }
    finally { cfBoardBusy = false; }
  }
  window.TechBoardRuntime.registerActions({
    // Agent（SaveRequirementConfirmationNote）起草的确认意见带回看板：复用 cfAppendNote
    // 的追加写法写入既有 #confirmationNote，不覆盖人工已写内容，也不动任何状态。
    applyConfirmationNote: {
      label: '带入确认意见',
      role: 'aux',
      order: 30,
      run: ({ note } = {}) => {
        // 当前视图没有确认意见输入框（或 Agent 没给正文）不是故障：如实回传 applied=false
        // 即可，不再返回失败码 —— 否则每次带入意见都会在会话里留一条 ⚠ 卡。
        const applied = cfAppendNote(note);
        return { ok: true, result: { applied: applied, reason: applied ? '' : 'target-missing' } };
      },
      getState: () => ({ visible: true, enabled: Boolean(document.querySelector('#confirmationNote')), busy: cfBoardBusy }),
    },
    // Agent 改完状态（ConfirmRequirement / ReturnRequirementToDraft）后左侧只发
    // refresh-data：复用既有 cfStart() 重新拉需求单与预检并重绘，不新增读取逻辑。
    refreshData: {
      label: '刷新需求确认页',
      role: 'aux',
      order: 40,
      silent: true,
      run: async () => { await cfStart(); return { ok: true }; },
      // 只退出左侧栏：刷新仍由 refresh-data 命令与 Agent 工具走 executeAction 触发。
      getState: () => ({ visible: false, enabled: Boolean(document.querySelector('#confirmPass')), busy: cfBoardBusy }),
    },
    confirmRequirement: {
      label: '✓ 通过确认',
      role: 'primary',
      order: 10,
      run: () => cfBoardRun('confirm'),
      getState: () => { const state = cfButtonState(); return { visible: true, enabled: state.enabled, busy: state.busy }; },
    },
    returnRequirementDraft: {
      label: '× 驳回',
      role: 'aux',
      order: 20,
      run: () => cfBoardRun('return'),
      getState: () => { const state = cfButtonState(); return { visible: true, enabled: state.back && !state.busy, busy: state.busy }; },
    },
  });
})();
