function confirmationQuestions(req){const d=req.data||{};const rows=[];if(!d.technical_requirements)rows.push(['技术要求是否完整？','当前需求未填写技术要求与约束，请确认是否可按现有图纸推进。']);if(!d.due_date)rows.push(['期望交付日期？','当前未设置完成日期，请补充项目计划。']);if(!d.customer_project)rows.push(['客户或项目归属？','未填写客户 / 项目名称，建议确认归属以便报告分发。']);return rows.length?rows:[['需求范围确认','系统完整性检查未发现必填缺失，请确认图纸、技术目标与评估范围无误。']];}
/* 候选盒型「选它能不能往下走」的一句披露（Spec
   docs/specs/packaging-box-candidate-runnability-in-panel.md §C1）：
   后端 `packaging_match._candidate()` 给 `part_template_available` 三态，
   本函数只把它翻成一句话 —— 四态互斥，**「查不到」不许说成「没有」**
   （Spec `packaging-silent-degradation-disclosure.md` §2.4 的同一条纪律）。
   顶层纯函数：体内无 DOM / 无 `fetch(` / 无 `localStorage`，可被 `node -e` 抽出来真跑。 */
function boxCandidateRunnabilityNote(row){
  const item=(row&&typeof row==='object')?row:{};
  const available=item.part_template_available;
  if(available===false){
    const total=Number(item.part_template_total);
    const count=Number.isFinite(total)?total:0;
    return `这个盒型还没有部件模板（${count} 条），确认后 BOM / 工艺 / 成本都跑不动；先补模板再确认`;
  }
  if(available===true){
    const total=Number(item.part_template_total);
    return (Number.isFinite(total)&&total>0)?`部件模板 ${total} 条`:'';
  }
  if(available===null){
    const why=(item.part_template_unavailable&&typeof item.part_template_unavailable==='object')
      ?item.part_template_unavailable:null;
    const message=why?String(why.message||'').trim():'';
    return message||'部件模板暂时查不到，请稍后重试；这不代表该盒型没有模板';
  }
  return '';
}
function renderConfirm(req){const d=req.data||{};document.querySelector('#app').innerHTML=`${header()}${workflow(1,'1.2')}<section class="title-card card"><div class="title-row"><h1>确认工艺评估需求</h1><span class="badge ${statusClass(req.status)}">${statusLabel(req.status)}</span></div><div class="ai-hint"><strong>✦ AI 辅助预检</strong><span>以下为结构化完整性检查结果，不调用模型、不产生 API 费用。请确认后送审。</span></div></section><section class="grid"><section class="card section"><h2>需求摘要</h2><div class="field"><label>需求名称</label><div>${esc(req.title)}</div></div><div class="field"><label>需求类型</label><div>${esc(d.requirement_type||'—')}</div></div><div class="field"><label>需求背景与目标</label><div>${text(d.description||'—')}</div></div><div class="field"><label>技术要求与约束</label><div>${text(d.technical_requirements||'待确认')}</div></div><a class="btn secondary" href="${href('requirement-create.html')}">返回修改需求</a></section><section class="card section"><h2>待澄清问题</h2><div id="questions">${confirmationQuestions(req).map(([q,j],i)=>`<div class="question-card"><div class="question">${i+1}. ${esc(q)}</div><div class="judgement">AI 初步判断：${esc(j)}</div><div class="confirm-row"><input id="answer${i}" placeholder="输入确认说明或补充信息"><button class="btn primary" data-answer="${i}">确认</button></div></div>`).join('')}</div><div class="field"><label>整体确认说明</label><textarea id="confirmationNote" placeholder="填写本次确认结论、补充说明或处理意见">${esc(req.confirmation_note||'')}</textarea></div></section></section><section class="card section"><h2>已有流程留痕</h2>${renderHistory(req.history)}</section><div class="footer-actions"><div class="footer-actions-inner"><a class="btn secondary" href="${href('requirement-create.html')}">上一步</a><button class="btn primary" id="confirmRequirement">确认需求并送审</button></div></div>`;loadMe();document.querySelectorAll('[data-answer]').forEach(b=>b.onclick=()=>{const input=document.querySelector(`#answer${b.dataset.answer}`);if(!input.value.trim())return toast('请先输入确认说明');b.textContent='已确认';b.disabled=true;});document.querySelector('#confirmRequirement').onclick=async()=>{try{const comment=document.querySelector('#confirmationNote').value;await api(`/api/projects/${projectId}/requirement/confirm`,{method:'POST',body:JSON.stringify({comment})});location.href=href('requirement-review.html');}catch(e){toast(e.message,4200)}};}

/* ------------------------------------------------------------------------ *
 * 包装第 4 批：盒型匹配面板（Spec docs/specs/packaging-box-type-matching.md §4）
 * 1.2 需求确认页里，工艺经理看候选盒型 + 总分 + 分项分 + 淘汰原因，并做四态决策。
 * 只在 industry=packaging 的需求单上出现；其它行业完全不挂载（不产生额外请求）。
 * 接口：/api/projects/<pid>/requirement/box-match[/decision]
 * ------------------------------------------------------------------------ */
(function () {
  const BM_DECIDE_ROLES = ['process_manager', 'process_director', 'admin'];
  const BM_DIMENSION_LABELS = {size_range: '尺寸区间', fit_clearance: '配合间隙', face_paper_gsm: '面纸克重', closure_type: '闭合方式', v_groove: 'V 槽'};
  const BM_REASON_LABELS = {closure_type_mismatch: '闭合方式无交集（硬门槛）', fit_clearance_out_of_tolerance: '配合间隙超差（硬门槛）', gsm_unparsable: '盒型克重无法解析', v_groove_required_but_unsupported: '需要 V 槽但盒型不支持', missing_input: '需求未填'};
  const BM_STATUS_LABELS = {matched: '可确认', needs_input: '缺输入', rejected: '已淘汰'};
  /* 盒型匹配做不下去时，按后端给的稳定 code 给"下一步去哪"（Spec
     docs/specs/packaging-downstream-block-code-parity.md §2.3）：
       · requirement_draft_missing → 还没有需求单，去建需求草稿；
       · industry_missing         → 需求单行业不是包装，去需求单把行业改过来；
       · box_type_not_confirmed   → 盒型还没确认，留在本页继续确认（不给跳转）。
     码只用来选出口，人话一律用后端返回的 message，不在前端改口径。 */
  const BM_BLOCK_ACTIONS = {
    requirement_draft_missing: {text: '去建需求单', page: 'requirement-create.html'},
    industry_missing: {text: '去需求单把行业选成「包装」', page: 'requirement-create.html'},
    // 未确认盒型是"留在本页继续确认"，不是"去别处补数据"，所以不给跳转出口。
    box_type_not_confirmed: null,
  };
  let bmPid = '';
  let bmBusy = false;
  let bmBlocked = null;

  function bmEsc(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  }
  function bmToast(message, error) {
    if (typeof toast === 'function') { toast(message, error ? 4200 : 3000); return; }
    const el = document.createElement('div');
    el.className = `page-toast${error ? ' error' : ''}`;
    el.textContent = message;
    document.body.append(el);
    setTimeout(() => el.remove(), 3600);
  }
  function bmApi(url, options) {
    if (typeof api === 'function') return api(url, options);
    return fetch(url, {...(options || {}), headers: {'Content-Type': 'application/json', ...((options || {}).headers || {})}})
      .then(async res => {
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = body.detail;
          const message = (typeof detail === 'string' ? detail : (detail && detail.message))
            || body.message || `请求失败（${res.status}）`;
          const err = new Error(message);
          err.status = res.status;
          err.code = (detail && detail.code) || body.code || '';
          throw err;
        }
        return body;
      });
  }
  function bmBlockAction(code) {
    return BM_BLOCK_ACTIONS[String(code || '')] || null;
  }
  function bmBlockNote() {
    if (!bmBlocked) return '';
    const action = bmBlockAction(bmBlocked.code);
    const link = action
      ? `<a class="btn secondary" href="${href(action.page)}" data-bm-block-go="${bmEsc(bmBlocked.code)}">${bmEsc(action.text)}</a>`
      : '';
    return `<div class="box-match-block" data-bm-block="${bmEsc(bmBlocked.code)}">`
      + `<div class="box-match-block-msg">${bmEsc(bmBlocked.message)}</div>${link}</div>`;
  }
  function bmPaintBlock() {
    const host = document.querySelector('#boxMatchPanel');
    if (!host) return;
    const existing = host.querySelector('.box-match-block');
    if (existing) existing.remove();
    const html = bmBlockNote();
    if (!html) return;
    const box = document.createElement('div');
    box.innerHTML = html;
    const node = box.firstElementChild;
    const actions = host.querySelector('.box-match-actions');
    if (actions) host.insertBefore(node, actions); else host.appendChild(node);
  }
  function bmProjectId() {
    if (bmPid) return bmPid;
    try {
      if (window.TechProjectContext && typeof TechProjectContext.bind === 'function') bmPid = TechProjectContext.bind().project || '';
    } catch (err) { bmPid = ''; }
    if (!bmPid) bmPid = new URLSearchParams(location.search).get('project') || '';
    return bmPid;
  }
  // 谁能不能确认盒型由后端说了算（403）；这里只决定按钮是否可见，避免只读账号点出 403。
  function bmCanDecide() {
    const user = (window.CpqAuth && typeof CpqAuth.user === 'function' && CpqAuth.user()) || window.__user || null;
    const role = String((user && (user.role_code || user.role)) || '').trim();
    return !role || BM_DECIDE_ROLES.includes(role);
  }
  function bmScore(value) {
    return value === null || value === undefined ? '—' : (Math.round(Number(value) * 100) / 100).toFixed(2);
  }
  function bmDimensionText(scores) {
    const parts = Object.keys(BM_DIMENSION_LABELS)
      .filter(key => scores && scores[key] !== undefined)
      .map(key => `${BM_DIMENSION_LABELS[key]} ${bmScore(scores[key])}`);
    return parts.join(' · ') || '—';
  }
  function bmCandidate(row, index) {
    const reasons = (row.reject_reasons || []).map(code => BM_REASON_LABELS[code] || code);
    const undecidable = (row.undecidable_dimensions || []).map(key => BM_DIMENSION_LABELS[key] || key);
    const status = BM_STATUS_LABELS[row.status] || row.status || '';
    // 选它能不能往下走（Spec `packaging-box-candidate-runnability-in-panel.md` §C2）：
    // 只是披露 —— **不**参与 `can_confirm`，也不禁用确认按钮。
    const runnability = boxCandidateRunnabilityNote(row);
    return `<li class="box-match-candidate" data-status="${bmEsc(row.status)}">
      <div class="box-match-candidate-head">
        <span class="box-match-rank">${index + 1}</span>
        <strong>${bmEsc(row.box_type_code)}</strong>
        <span>${bmEsc(row.name || '')}</span>
        <span class="box-match-family">${bmEsc(row.family || '')}</span>
        <span class="box-match-score">总分 ${bmScore(row.total_score)}</span>
        <span class="box-match-status">${bmEsc(status)}</span>
        ${row.out_of_range ? '<span class="box-match-flag">尺寸越界</span>' : ''}
      </div>
      <div class="box-match-dims">${bmEsc(bmDimensionText(row.dimension_scores))}</div>
      ${reasons.length ? `<div class="box-match-reason">淘汰原因：${bmEsc(reasons.join('；'))}</div>` : ''}
      ${undecidable.length ? `<div class="box-match-reason">无法判定：${bmEsc(undecidable.join('、'))}</div>` : ''}
      ${runnability ? `<div class="box-match-runnability" data-bm-runnability="1">${bmEsc(runnability)}</div>` : ''}
      <div class="box-match-candidate-actions">
        <button class="btn primary" data-bm-confirm="${bmEsc(row.box_type_code)}" ${row.can_confirm && bmCanDecide() ? '' : 'disabled'}>确认此盒型</button>
        <span class="box-match-usage">适用行业：${bmEsc(row.applicable_industries || '—')} · 状态：${bmEsc(row.business_status || '—')}</span>
      </div>
    </li>`;
  }
  function bmPanel(record, editable) {
    const candidates = record.candidates || [];
    const missing = record.missing_inputs || [];
    const confirmed = record.confirmed_box_type
      ? `<div class="box-match-confirmed">已确认盒型：<strong>${bmEsc(record.confirmed_box_type)}</strong>（${bmEsc(record.confirmed_by || '—')} · ${bmEsc(record.confirmed_at || '—')}）</div>`
      : '';
    const stale = record.stale
      ? `<div class="box-match-stale">匹配输入已变化，请重新确认：${bmEsc((record.stale_reasons || []).join('、'))}</div>`
      : '';
    const empty = candidates.length ? '' : '<div class="box-match-empty">还没有盒型匹配结果，点「运行盒型匹配」开始。</div>';
    return `<section class="card section box-match-panel" id="boxMatchPanel">
      <h2>盒型匹配（包装）</h2>
      <div class="box-match-hint">五维匹配（尺寸 / 配合间隙 / 面纸克重 / 闭合方式 / V 槽）权重与硬门槛读知识库 <code>kb_packaging_match_weight</code>，本页不调用模型。</div>
      ${record.decision && record.decision !== 'none' ? `<div class="box-match-decision">当前决策：${bmEsc(record.decision)}</div>` : ''}
      ${confirmed}${stale}
      ${missing.length ? `<div class="box-match-missing">缺失输入：${bmEsc(missing.join('、'))}</div>` : ''}
      ${empty}
      ${bmBlockNote()}
      <ol class="box-match-list">${candidates.map(bmCandidate).join('')}</ol>
      <div class="box-match-actions">
        <button class="btn secondary" data-bm-run="1" ${editable ? '' : 'disabled'}>运行盒型匹配</button>
        <button class="btn primary" data-bm-decision="new_tooling" ${editable ? '' : 'disabled'}>新制评估</button>
        <button class="btn secondary" data-bm-decision="returned" ${editable ? '' : 'disabled'}>退回补充需求</button>
      </div>
    </section>`;
  }

  async function bmRefresh() {
    const pid = bmProjectId();
    const host = document.querySelector('#boxMatchPanel');
    if (!pid || !host) return;
    const record = await bmApi(`/api/projects/${encodeURIComponent(pid)}/requirement/box-match`);
    bmBlocked = null;   // 读到了结果就说明这一轮没有被前置缺口挡下
    host.outerHTML = bmPanel(record, bmCanDecide());
    bmBind(pid);
  }
  function bmBind(pid) {
    document.querySelectorAll('[data-bm-run]').forEach(button => {
      button.onclick = () => bmSubmit(pid, `/api/projects/${encodeURIComponent(pid)}/requirement/box-match`, {}, '匹配完成');
    });
    document.querySelectorAll('[data-bm-decision]').forEach(button => {
      button.onclick = () => bmSubmit(pid, `/api/projects/${encodeURIComponent(pid)}/requirement/box-match/decision`, {decision: button.dataset.bmDecision}, '决策已提交');
    });
    document.querySelectorAll('[data-bm-confirm]').forEach(button => {
      button.onclick = () => bmSubmit(pid, `/api/projects/${encodeURIComponent(pid)}/requirement/box-match/decision`, {decision: 'confirmed', box_type_code: button.dataset.bmConfirm}, '已确认盒型');
    });
  }
  async function bmSubmit(pid, url, body, okMessage) {
    if (bmBusy) return;
    bmBusy = true;
    try {
      await bmApi(url, {method: 'POST', body: JSON.stringify(body)});
      bmToast(okMessage);
      bmBusy = false;
      await bmRefresh();
    } catch (error) {
      bmBusy = false;
      const code = String((error && error.code) || '');
      bmBlocked = {code: code, message: String((error && error.message) || '') || '盒型匹配操作失败'};
      const action = bmBlockAction(code);
      bmToast(action ? `${bmBlocked.message}（${action.text}）` : bmBlocked.message, true);
      bmPaintBlock();
    }
  }

  // 需求单不是包装行业就不挂载：面板只服务包装需求（其它行业一条请求都不发）。
  let bmMounting = false;
  let bmFailures = 0;
  async function bmMaybeMount() {
    if (bmMounting || bmFailures >= 2) return;
    const host = document.querySelector('#app .footer-actions');
    if (!host || document.querySelector('#boxMatchPanel')) return;
    const pid = bmProjectId();
    if (!pid) return;
    bmMounting = true;
    try {
      const requirement = await bmApi(`/api/projects/${encodeURIComponent(pid)}/requirement`);
      const industry = String((((requirement || {}).requirement || {}).data || {}).industry || '').trim();
      if (industry !== 'packaging') return;
      const placeholder = document.createElement('section');
      placeholder.id = 'boxMatchPanel';
      placeholder.className = 'card section box-match-panel';
      placeholder.dataset.pending = '1';
      placeholder.innerHTML = '<h2>盒型匹配（包装）</h2><div class="box-match-empty">正在读取盒型匹配结果…</div>';
      host.parentNode.insertBefore(placeholder, host);
      await bmRefresh();
    } catch (error) {
      bmFailures += 1;
      const pending = document.querySelector('#boxMatchPanel[data-pending="1"]');
      if (pending) pending.remove();
    } finally {
      bmMounting = false;
    }
  }

  window.CfBoxMatchPanel = {mount: bmMaybeMount, refresh: bmRefresh};
  const bmStart = () => { bmMaybeMount().catch(() => {}); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bmStart);
  else bmStart();
  // 页面重新渲染（Agent refresh-data / 重新拉需求单）后把面板挂回去。
  const bmApp = document.querySelector('#app');
  if (bmApp && typeof MutationObserver === 'function') {
    new MutationObserver(() => { if (!document.querySelector('#boxMatchPanel')) bmStart(); }).observe(bmApp, {childList: true});
  }
})();

/* ------------------------------------------------------------------------ *
 * 包装第 5 批：参数化部件展开与包装 BOM 面板
 * （Spec docs/specs/packaging-parametric-bom.md §4）
 * 1.2 需求确认页里，已确认盒型的包装需求单多一块 BOM 面板：七类分组、部件尺寸、
 * needs_input 与缺失变量、单行锁定/解锁、变量覆盖后重算。
 * 只在 industry=packaging 的需求单上出现；其它行业完全不挂载（一条请求都不发）。
 * 接口：/api/projects/<pid>/requirement/packaging-bom[/lock]
 * ------------------------------------------------------------------------ */
(function () {
  const PB_WRITE_ROLES = ['process_manager', 'process_director', 'admin'];
  const PB_ORDER = ['finished', 'box_part', 'optional_part', 'material', 'process', 'tooling', 'packaging'];
  const PB_CATEGORY_LABELS = {finished: '成品', box_part: '盒型部件', material: '材料', process: '工艺', packaging: '包材', tooling: '工装/模具', optional_part: '可选部件'};
  const PB_STATUS_LABELS = {computed: '已算出', needs_input: '缺输入', locked: '已锁定'};
  const PB_WRITE_HINT = '需要工艺经理、工艺技术总监或管理员权限';
  let pbPid = '';
  let pbBusy = false;

  function pbEsc(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  }
  function pbToast(message, error) {
    if (typeof toast === 'function') { toast(message, error ? 4200 : 3000); return; }
    const el = document.createElement('div');
    el.className = `page-toast${error ? ' error' : ''}`;
    el.textContent = message;
    document.body.append(el);
    setTimeout(() => el.remove(), 3600);
  }
  async function pbApi(url, options) {
    const response = await fetch(url, Object.assign({headers: {'Content-Type': 'application/json'}}, options || {}));
    if (!response.ok) {
      let detail = '';
      try {
        const payload = await response.json();
        const raw = payload.detail;
        /* 包装下游的业务错误统一回结构化 detail {code,message}（Spec
           docs/specs/packaging-downstream-block-code-parity.md §2.2）；
           只认字符串的话人话会被吞成 [object Object]。 */
        detail = (typeof raw === 'string' ? raw : (raw && raw.message)) || payload.message || '';
      } catch (error) { detail = ''; }
      throw new Error(detail || `请求失败（${response.status}）`);
    }
    return response.json();
  }
  function pbProjectId() {
    if (pbPid) return pbPid;
    const query = new URLSearchParams(location.search);
    pbPid = query.get('project_id') || query.get('pid') || document.body.dataset.projectId || '';
    return pbPid;
  }
  function pbRoles() {
    try { return (window.techSession && techSession.roles && techSession.roles()) || []; } catch (error) { return []; }
  }
  function pbCanWrite() {
    const user = (typeof currentUser === 'function') ? (currentUser() || {}) : {};
    const role = String(user.role || user.cpq_role_code || '');
    if (PB_WRITE_ROLES.indexOf(role) >= 0) return true;
    return pbRoles().some(item => PB_WRITE_ROLES.indexOf(String(item)) >= 0);
  }
  function pbNumber(value) {
    return (value === null || value === undefined || value === '') ? '—' : value;
  }
  function pbSize(item) {
    return `${pbNumber(item.length_mm)} × ${pbNumber(item.width_mm)} × ${pbNumber(item.height_mm)}`;
  }
  // 图纸重解析后，行上的尺寸可能来自**上一版**零件文档（Spec
  // `packaging-bom-parts-version-binding.md` §2.4）：这些行不许继续用"已确认/无缺口"的样式展示
  // —— 历史尺寸照旧带着（用户要能对比），但必须看得见"该重新生成 BOM 了"。
  function pbStaleNote(stale) {
    if (!stale) return '';
    const why = stale.reason === 'binding_without_version'
      ? '这一行是历史绑定，没有留下零件文档版本（无从判断）'
      : '图纸已经重新解析过，这一行的尺寸来自上一版零件文档';
    return `<div class="pb-stale" data-pb-stale="${pbEsc(stale.reason || '')}">${pbEsc(why)}，`
      + `请重新生成 BOM 后再用。</div>`;
  }
  // 尺寸口径（后端 `size_source`）的人话 —— 与 `app.js` 的 `PACKAGING_SIZE_SOURCE_COPY` 同一份说法。
  // 本面板在 `requirement-confirm.js`，该页不加载 `app.js`，所以取不到那份词表时按同一句回退
  // （Spec `packaging-bom-size-quality-accounting.md` §2.3 的"不许另写一套说法"指的就是这句话）。
  function pbSizeSourceCopy(sizeSource) {
    const shared = (typeof window !== 'undefined' && window.PACKAGING_SIZE_SOURCE_COPY) || null;
    const key = String(sizeSource || '');
    if (shared && shared[key]) return shared[key];
    if (key === 'closed_outline') return '真实轮廓（闭合环）';
    if (key === 'component_bbox') return '分量包围盒（求不出轮廓，仅供估算）';
    if (key === 'dwg_outline') return '图纸自带包围盒（这一件没有可用坐标）';
    return '';
  }
  // 材料码缺口的人话与逐行清单（Spec `packaging-material-unresolved-panel.md` §C1）：
  // 后端给的是**闭集**三档原因，前端只翻译、绝不替后端猜行外归因。
  function pbMaterialReasonLabel(reason) {
    const labels = {
      map_key_missing: '映射表里还没有这条原文',
      map_entry_not_applied: '映射表里写了，但这一版没生效',
      map_unknown: '映射表读不到，这次没能判定'
    };
    return labels[String(reason || '')] || '原因未知（后端没有给出原因档）';
  }
  function pbMaterialUnresolvedRows(gaps) {
    const scope = (gaps && typeof gaps === 'object' && !Array.isArray(gaps)) ? gaps : {};
    const detail = Array.isArray(scope.material_unresolved_detail) ? scope.material_unresolved_detail : null;
    const rows = [];
    if (detail) {
      detail.forEach(entry => {
        const item = (entry && typeof entry === 'object') ? entry : {};
        const key = String(item.item_key || '').trim();
        if (!key) return;  // 空项跳过：不许造无名行。
        rows.push({key: key, label: pbMaterialReasonLabel(item.reason), action: String(item.action || '').trim()});
      });
      return rows;
    }
    const fallback = Array.isArray(scope.material_unresolved) ? scope.material_unresolved : null;
    if (!fallback) return [];
    fallback.forEach(value => {
      const key = String(value || '').trim();
      if (!key) return;
      // 退回旧清单时**只**有行名 —— 不编原因、不编动作。
      rows.push({key: key, label: pbMaterialReasonLabel(''), action: ''});
    });
    return rows;
  }
  function pbMaterialUnresolvedHint(gaps) {
    const count = pbMaterialUnresolvedRows(gaps).length;
    return count ? `解析不到材料码 ${count} 行：下面逐条给出原因与补齐办法。` : '';
  }
  function pbMaterialMapNote(scope) {
    const facts = (scope && typeof scope === 'object' && !Array.isArray(scope)) ? scope : {};
    const unavailable = (facts.map_unavailable && typeof facts.map_unavailable === 'object') ? facts.map_unavailable : null;
    if (unavailable && Object.keys(unavailable).length) {
      const message = String(unavailable.message || '').trim();
      // 逐字转达后端的 message（不许换个说法），并说明这一次的解析口径没变。
      return message ? `${message}；本次材料码只按既有规则解析。`
        : '材料原文映射表读不到：本次材料码只按既有规则解析。';
    }
    const hits = Number(facts.map_hit_total || 0);
    return hits > 0 ? `材料码映射表命中 ${hits} 行。` : '';
  }
  // 材料码映射表**自己**的账（Spec `packaging-material-map-account-panel.md` §C2）：表是空的还是
  // 有 N 条、这一版用的是哪一份（来源 / 指纹）、这次各档几行。事实全部来自后端
  // `business_material_rows`（`map_entry_total` / `map_source` / `map_fingerprint` /
  // `map_unavailable` / `reason_counts`）—— 前端不重算、不猜名字、没有事实就说话。
  function pbMaterialMapState(scope) {
    const facts = (scope && typeof scope === 'object' && !Array.isArray(scope)) ? scope : null;
    const text = value => String(value === undefined || value === null ? '' : value).trim();
    const positive = value => {
      const number = Number(value);
      return (Number.isFinite(number) && number > 0) ? number : 0;
    };
    const entries = positive(facts ? facts.map_entry_total : undefined);
    const hits = positive(facts ? facts.map_hit_total : undefined);
    const source = text(facts ? facts.map_source : '');
    const fingerprint = text(facts ? facts.map_fingerprint : '');
    const sourceLabels = {
      default: '仓库内置',
      override: '环境变量覆盖（影响所有项目）'
    };
    const unavailable = !!(facts && facts.map_unavailable
      && typeof facts.map_unavailable === 'object'
      && !Array.isArray(facts.map_unavailable)
      && Object.keys(facts.map_unavailable).length);
    // 状态判据（§C2，顺序固定）：不是对象 → 未知档；读不到 → unavailable；**没有条数这一栏**
    // （老载荷）→ 未知档（不许当成"0 条"）；条数 <= 0 → 空表；否则有表。命中数不参与判定。
    const rawEntryTotal = facts ? facts.map_entry_total : undefined;
    const size = (rawEntryTotal === undefined || rawEntryTotal === null
      || String(rawEntryTotal).trim() === '') ? NaN : Number(rawEntryTotal);
    let state = 'unknown';
    let headline = '后端没给材料码映射表的状态（老载荷），这一版说不清用了哪份映射。';
    if (facts && unavailable) {
      state = 'unavailable';
      headline = '材料原文映射表读不到：这一版 BOM 的材料码只按既有分词规则解析。';
    } else if (facts && Number.isFinite(size)) {
      if (size > 0) {
        state = 'ready';
        headline = `材料原文映射表 ${size} 条，本次命中 ${hits} 行。`;
      } else {
        state = 'empty';
        headline = '材料原文映射表读得到，但一条映射都没有（0 条）：客户原文只能按既有分词规则解析，'
          + '解不出来的行要往表里补。';
      }
    }
    return {
      state: state, headline: headline, entries: entries, hits: hits,
      source: source, sourceLabel: sourceLabels[source] || '来源未知', fingerprint: fingerprint
    };
  }
  function pbMaterialMapBreakdown(scope) {
    const facts = (scope && typeof scope === 'object' && !Array.isArray(scope)) ? scope : {};
    const counts = (facts.reason_counts && typeof facts.reason_counts === 'object'
      && !Array.isArray(facts.reason_counts)) ? facts.reason_counts : null;
    if (!counts) return '';
    const positive = value => {
      const number = Number(value);
      return (Number.isFinite(number) && number > 0) ? number : 0;
    };
    // 闭集与顺序固定（与后端 `MATERIAL_RESOLVE_REASONS` 同一批）：档名一律给人话，
    // 闭集外的档名**不猜**，只按总数合并成「其它档」。
    const buckets = [['map_hit', '映射命中'], ['legacy_hit', '旧规则命中'],
                     ['map_key_missing', '原文没映射'],
                     ['map_entry_not_applied', '映射写了没生效'],
                     ['map_unknown', '映射表读不到没判定']];
    const parts = [];
    buckets.forEach(bucket => {
      const value = positive(counts[bucket[0]]);
      if (value) parts.push(`${bucket[1]} ${value} 行`);
    });
    let other = 0;
    Object.keys(counts).forEach(key => {
      if (buckets.some(bucket => bucket[0] === key)) return;
      other += positive(counts[key]);
    });
    if (other) parts.push(`其它档 ${other} 行`);
    return parts.length ? `这次解析：${parts.join(' · ')}` : '';
  }
  function pbMaterialMapAccountBlock(scope) {
    const facts = pbMaterialMapState(scope);
    // 「读不到」由既有 `pbMaterialMapNote()` 那一句承担、「未知档」没有事实可说：都不在这里
    // 重复，也不编一句话（§C2）。
    if (facts.state !== 'ready' && facts.state !== 'empty') return '';
    const breakdown = pbMaterialMapBreakdown(scope);
    const rows = breakdown
      ? breakdown.replace(/^这次解析：/, '').split(' · ').filter(item => item)
      : [];
    return `<div class="pb-hint" data-pb-material-map-state="${pbEsc(facts.state)}"`
      + ` data-pb-material-map-source="${pbEsc(facts.sourceLabel)}"`
      + ` data-pb-material-map-fingerprint="${pbEsc(facts.fingerprint)}">`
      + `${pbEsc(facts.headline)}</div>`
      + (rows.length
        ? `<div class="pb-hint" data-pb-material-map-reasons="${rows.length}">${pbEsc(breakdown)}</div>`
        : '');
  }
  // 这份 BOM 照哪一版**业务部件清单**配的（Spec `packaging-bom-business-parts-scope-panel.md` §C1）：
  // 零件文档版本与业务部件清单版本是两把尺子，head 里只写了前者，这里补后者。件数**只**读
  // `record.business_parts`（绝不用几何件数冒充业务件数），可用性**只**读 `available`
  // （读不到不报漂移：比较不了 ≠ 变了），读不到 / 还没导入 / 一件都没有各说各的。
  function pbBusinessPartsScope(record) {
    const root = (record && typeof record === 'object' && !Array.isArray(record)) ? record : {};
    const scope = (root.business_parts && typeof root.business_parts === 'object'
      && !Array.isArray(root.business_parts)) ? root.business_parts : null;
    const text = value => String(value === undefined || value === null ? '' : value).trim();
    const count = value => {
      const number = Number(value);
      return (Number.isFinite(number) && number > 0) ? number : 0;
    };
    const gap = (scope && scope.gap && typeof scope.gap === 'object' && !Array.isArray(scope.gap))
      ? scope.gap : {};
    const id = text(root.business_parts_id);
    const hash = text(scope ? scope.business_parts_hash : '');
    const total = count(scope ? scope.business_part_total : undefined);
    const bound = count(scope ? scope.bound_total : undefined);
    const unbound = count(scope ? scope.unbound_total : undefined);
    const gapCode = text(gap.code);
    const gapMessage = text(gap.message);
    // 判据顺序（§C1）：不是对象 → 未知档；`available` 不为真 → 读不到 / 还没导入（由 gap 说清）；
    // 件数 0 → 一件都没有；否则有清单。
    let state = 'unknown';
    if (scope && scope.available === true) state = total > 0 ? 'ready' : 'empty';
    else if (scope) state = 'unavailable';
    const headlines = {
      ready: `这版 BOM 照的业务部件清单：${id || '—'} · 共 ${total} 件（已绑几何 ${bound} 件 · `
        + `未绑 ${unbound} 件）· 版本 ${hash ? hash.slice(0, 12) : '—'}。`,
      empty: '业务部件清单读到了，但一件都没有（0 件）：这版 BOM 只按几何零件配，清单要重导。',
      unknown: '后端没给业务部件清单这一块（老载荷）：说不清这版 BOM 照哪一版业务部件算的。'
    };
    const fallback = '业务部件清单读不到：这版 BOM 只按几何零件配，别把这次当成「没有业务部件」。';
    return {
      state: state,
      id: id, hash: hash, total: total, bound: bound, unbound: unbound,
      gapCode: gapCode, gapMessage: gapMessage,
      headline: state === 'unavailable' ? (gapMessage || fallback)
        : (headlines[state] || headlines.unknown)
    };
  }
  function pbBusinessPartsScopeBlock(record) {
    const facts = pbBusinessPartsScope(record);
    // 四态都渲染：每一态都有一句各不相同的事实（没有别的块替它说）。
    return `<div class="pb-hint" data-pb-business-parts-state="${pbEsc(facts.state)}"`
      + ` data-pb-business-parts-id="${pbEsc(facts.id)}"`
      + ` data-pb-business-parts-total="${pbEsc(facts.total)}"`
      + ` data-pb-business-parts-gap="${pbEsc(facts.gapCode)}">`
      + `${pbEsc(facts.headline)}</div>`;
  }
  // 这一版 BOM 的**部件组行**有多少来自对照表（Spec `packaging-bom-business-rows-account-panel.md` §C1）：
  // 材料组那本账由前面两块承担，这里只答部件组这一半 —— 对照表来的行与几何零件模板展开的行在表格里
  // 同形，只有这本账能把它们分开（既然决定「该不该去导对照表」）。行数**只**读 `record.business_rows`
  // （判据在后端那一块账里，前端自己按 `source` 数行就是第二套口径），老载荷（没有这一块）不许与
  // 「0 行来自对照表」同形。
  function pbBusinessRowsAccount(record) {
    const root = (record && typeof record === 'object' && !Array.isArray(record)) ? record : {};
    const scope = (root.business_rows && typeof root.business_rows === 'object'
      && !Array.isArray(root.business_rows)) ? root.business_rows : null;
    const count = value => {
      const number = Number(value);
      return (Number.isFinite(number) && number > 0) ? number : 0;
    };
    const total = count(scope ? scope.row_total : undefined);
    const boxParts = count(scope ? scope.box_part_total : undefined);
    const optionalParts = count(scope ? scope.optional_part_total : undefined);
    const needsInput = count(scope ? scope.needs_input_total : undefined);
    // 判据顺序（§C1）：不是对象 → 未知档；行数 > 0 → 有对照表的行；否则这一版一行都没来自对照表。
    const state = scope ? (total > 0 ? 'ready' : 'empty') : 'unknown';
    const headlines = {
      ready: `部件组行：来自对照表 ${total} 行（盒型件 ${boxParts} 件 · 选配件 ${optionalParts} 件），`
        + `其中缺输入 ${needsInput} 行。`,
      empty: '这一版 BOM 的部件组行没有一行来自对照表（部件组是按几何零件模板展开的）。',
      unknown: '后端没给部件组行的来源账（老载荷）：说不清这一版 BOM 的部件组行是对照表还是模板来的。'
    };
    return {
      state: state, total: total, boxParts: boxParts, optionalParts: optionalParts,
      needsInput: needsInput, headline: headlines[state]
    };
  }
  function pbBusinessRowsBlock(record) {
    const facts = pbBusinessRowsAccount(record);
    // 三态都渲染：每一态都有一句各不相同的事实（没有别的块替它说）。
    return `<div class="pb-hint" data-pb-business-rows-state="${pbEsc(facts.state)}"`
      + ` data-pb-business-rows-total="${pbEsc(facts.total)}"`
      + ` data-pb-business-rows-needs-input="${pbEsc(facts.needsInput)}">`
      + `${pbEsc(facts.headline)}</div>`;
  }
  // 配对复核 / 零件回填失败 / 业务清单换版三本账的人话与逐行清单
  // （Spec `packaging-bom-disclosure-panel.md` §C1）：只消费后端结论，前端不重判一次。
  function pbPairingMismatchRows(record) {
    const scope = (record && typeof record === 'object') ? record : {};
    const raw = Array.isArray(scope.pairing_review) ? scope.pairing_review : [];
    const text = value => String(value === undefined || value === null ? '' : value).trim();
    const rows = [];
    raw.forEach(entry => {
      const item = (entry && typeof entry === 'object') ? entry : {};
      if (item.material_match === true) return;  // 这一列的是「不一致项」，一致的不进清单。
      const key = text(item.item_key);
      if (!key) return;                          // 空项跳过：不许造无名行。
      rows.push({key: key, part_code: text(item.part_code),
                 row_material: text(item.row_material), part_material: text(item.part_material)});
    });
    return rows;
  }
  function pbPairingMismatchNote(record) {
    const scope = (record && typeof record === 'object') ? record : {};
    const why = (scope.pairing_review_unavailable && typeof scope.pairing_review_unavailable === 'object')
      ? scope.pairing_review_unavailable : {};
    if (Object.keys(why).length) {
      const code = String(why.code || 'pairing_review_unavailable').trim();
      // 读不到 ≠ 没有不一致：这句话必须显形。
      return `配对复核读不到（${code}）：这一版不知道有没有材料配错，别当成「没有不一致」。`;
    }
    const count = pbPairingMismatchRows(scope).length;
    return count ? `配对复核：${count} 行材料与绑定的几何件不一致，请核对后再往下算。` : '';
  }
  function pbBindingErrorNote(record) {
    const scope = (record && typeof record === 'object') ? record : {};
    const error = (scope.binding_error && typeof scope.binding_error === 'object') ? scope.binding_error : {};
    if (!Object.keys(error).length) return '';   // {} = 没有失败，不说。
    const code = String(error.code || '').trim();
    const reason = String(error.reason || '').trim();
    const head = code ? `零件回填失败（${code}）` : '零件回填失败';
    return `${head}${reason ? `：${reason}` : ''}；这一版 BOM 的几何绑定可能不完整。`;
  }
  function pbBusinessStaleRows(record) {
    const scope = (record && typeof record === 'object') ? record : {};
    const raw = Array.isArray(scope.business_parts_stale) ? scope.business_parts_stale : [];
    const labels = {
      business_parts_reimported: '业务清单已重新导入（这一行还是上一版清单算的）',
      binding_without_version: '这一行没记业务清单版本，判断不了是不是过期'
    };
    const rows = [];
    raw.forEach(entry => {
      const item = (entry && typeof entry === 'object') ? entry : {};
      const key = String(item.item_key === undefined || item.item_key === null ? '' : item.item_key).trim();
      if (!key) return;
      const reason = typeof item.reason === 'string' ? item.reason : '';
      // 闭集认两档，其余一律未知档（不许归到已知两档）。
      rows.push({key: key, label: labels[reason] || '原因未知（后端没有给出原因档）'});
    });
    return rows;
  }
  function pbRow(item, writable, staleMap, markMap) {
    const status = String(item.status || 'computed');
    const stale = (staleMap || {})[String(item.item_key || '')] || null;
    const mark = (markMap || {})[String(item.item_key || '')] || null;
    const missing = (item.missing_variables || []).length
      ? `<div class="pb-missing">缺失变量：${pbEsc(item.missing_variables.join('、'))}</div>` : '';
    const toggle = writable
      ? `<button class="btn secondary" data-pb-lock="${pbEsc(item.item_key)}" data-pb-locked="${status === 'locked' ? '1' : '0'}">${status === 'locked' ? '解锁' : '锁定'}</button>`
      : '';
    const otherBoxCode = String((mark && mark.otherBox && item.box_type_code) || '');
    const otherBox = (mark && mark.otherBox)
      ? `<div class="pb-box-note" data-pb-other-box="${pbEsc(otherBoxCode)}">这一行属于另一个盒型（${pbEsc(otherBoxCode || '未知')}），请解锁或重新确认盒型后再算。</div>`
      : '';
    const unknownBox = (mark && mark.unknownBox)
      ? '<div class="pb-box-note" data-pb-box-unknown="1">这一行的盒型未知（旧数据）。</div>'
      : '';
    const bboxOnly = (mark && mark.bboxOnly)
      ? `<div class="pb-size-note" data-pb-bbox-only="1">尺寸来自包围盒（仅供估算）：${pbEsc(pbSizeSourceCopy(item.size_source))}</div>`
      : '';
    return `<li class="pb-item${stale ? ' pb-item-stale' : ''}" data-status="${pbEsc(stale ? 'stale' : status)}"${stale ? ` data-pb-stale="${pbEsc(stale.reason || '')}"` : ''}${mark && mark.bboxOnly ? ' data-pb-bbox-only="1"' : ''}>
      <div class="pb-item-head">
        <span class="pb-key">${pbEsc(item.item_key)}</span>
        <span class="pb-name">${pbEsc(item.item_name || '')}</span>
        <span class="pb-status">${pbEsc(stale ? '图纸已换版（待重新生成）' : (PB_STATUS_LABELS[status] || status))}</span>
        ${toggle}
      </div>
      ${pbStaleNote(stale)}
      ${otherBox}
      ${unknownBox}
      ${bboxOnly}
      <div class="pb-dims">尺寸：${pbEsc(pbSize(item))}${item.quantity ? ` · 数量 ${pbEsc(item.quantity)}` : ''}</div>
      ${item.material ? `<div class="pb-material">材料：${pbEsc(item.material)}${item.material_code ? `（${pbEsc(item.material_code)}）` : ''}</div>` : ''}
      ${item.component ? `<div class="pb-component">${pbEsc(item.component)}</div>` : ''}
      ${missing}
    </li>`;
  }
  function pbPanel(record, writable, boxTypeCode) {
    const items = (record && record.items) || [];
    const stats = (record && record.stats) || {};
    const gaps = (record || {}).gaps || {};
    // 零件文档版本漂移（Spec `packaging-bom-parts-version-binding.md` §2.4）：逐行标记 + 顶部一句
    // 总账；读不到零件文档版本时与"没有过期行"分开说。
    const staleRows = Array.isArray(record && record.parts_binding_stale)
      ? record.parts_binding_stale : [];
    const staleMap = {};
    staleRows.forEach(entry => { staleMap[String((entry || {}).item_key || '')] = entry || {}; });
    const partsUnavailable = (record && record.parts_document_unavailable) || {};
    const partsHash = String(((record && record.source_versions) || {}).parts_hash || '');
    const staleBanner = staleRows.length
      ? `<div class="pb-warning" data-pb-parts-stale="${staleRows.length}">有 ${staleRows.length} 行来自上一版图纸解析（图纸已重解析）：历史尺寸照旧带着可对比，但请重新生成 BOM 后再用。</div>`
      : '';
    const unavailableBanner = partsUnavailable.code
      ? `<div class="pb-warning" data-pb-parts-unavailable="${pbEsc(partsUnavailable.code)}">暂时读不到零件文档版本（${pbEsc(partsUnavailable.code)}），请稍后重试；这不代表没有过期行。</div>`
      : '';
    // 盒型维度（Spec `packaging-bom-box-type-provenance.md` §2.5）：逐行标记 + 顶部总账。
    const otherBoxRows = Array.isArray(record && record.rows_from_other_box_type)
      ? record.rows_from_other_box_type : [];
    const unknownBoxRows = Array.isArray(record && record.rows_without_box_type)
      ? record.rows_without_box_type : [];
    const boxTypeCodes = ((record && record.source_versions) || {}).box_type_codes;
    const mixedCodes = Array.isArray(boxTypeCodes) ? boxTypeCodes : [];
    // 尺寸质量账（Spec `packaging-bom-size-quality-accounting.md` §2.3）。
    const bboxRows = Array.isArray(gaps.bbox_only) ? gaps.bbox_only : [];
    const markMap = {};
    const mark = (key, patch) => {
      const name = String((key || {}).item_key || key || '');
      if (!name) return;
      markMap[name] = Object.assign(markMap[name] || {}, patch);
    };
    otherBoxRows.forEach(entry => mark(entry, {otherBox: true}));
    unknownBoxRows.forEach(entry => mark(entry, {unknownBox: true}));
    bboxRows.forEach(entry => mark(entry, {bboxOnly: true}));
    const mixedBanner = (otherBoxRows.length || mixedCodes.length > 1)
      ? `<div class="pb-warning" data-pb-mixed-box="${mixedCodes.length || otherBoxRows.length}">这份 BOM 混了 ${pbEsc(mixedCodes.length || otherBoxRows.length)} 个盒型（${pbEsc(mixedCodes.join('、') || '—')}）：其中 ${pbEsc(otherBoxRows.length)} 行属于另一个盒型，已逐行标出，请解锁或重新确认盒型后再算。</div>`
      : '';
    const unknownBoxBanner = unknownBoxRows.length
      ? `<div class="pb-warning" data-pb-box-unknown-total="${unknownBoxRows.length}">有 ${pbEsc(unknownBoxRows.length)} 行的盒型未知（旧数据，落库时还没有盒型字段）。</div>`
      : '';
    const bboxTotal = Number(((stats || {}).size_quality || {}).bbox_only || 0);
    const bboxBanner = bboxTotal
      ? `<div class="pb-warning" data-pb-bbox-total="${bboxTotal}">有 ${pbEsc(bboxTotal)} 行的尺寸来自包围盒（仅供估算，未求到真实轮廓）：料费按外接包围盒算，行内已逐行标出。</div>`
      : '';
    const groups = PB_ORDER.filter(category => items.some(item => item.bom_category === category));
    const body = groups.length
      ? groups.map(category => `<section class="pb-group">
          <h3>${pbEsc(PB_CATEGORY_LABELS[category] || category)}（${items.filter(item => item.bom_category === category).length}）</h3>
          <ul class="pb-list">${items.filter(item => item.bom_category === category).map(item => pbRow(item, writable, staleMap, markMap)).join('')}</ul>
        </section>`).join('')
      : '<div class="pb-empty">还没有包装 BOM，先在盒型匹配里确认盒型，再点「展开部件并生成 BOM」。</div>';
    // 材料码缺口（Spec `packaging-material-unresolved-panel.md` §C2）：逐行给原因 + 补齐办法；
    // `gaps.material_unresolved` 仍是"哪些行没解析出来"的**唯一**依据（这里只渲染，不重算）。
    const unresolvedRows = pbMaterialUnresolvedRows(gaps);
    const unresolved = unresolvedRows.length
      ? `<div class="pb-hint" data-pb-material-unresolved="${unresolvedRows.length}">${pbEsc(pbMaterialUnresolvedHint(gaps))}
        <ul class="pb-list">${unresolvedRows.map(row => `<li class="pb-item" data-pb-material-unresolved-key="${pbEsc(row.key)}"><span class="pb-key">${pbEsc(row.key)}</span><span class="pb-name">${pbEsc(row.label)}</span>${row.action ? `<div class="pb-missing">${pbEsc(row.action)}</div>` : ''}</li>`).join('')}</ul></div>`
      : '';
    const mapNote = pbMaterialMapNote(record.business_material_rows);
    const mapBlock = mapNote ? `<div class="pb-hint" data-pb-material-map="1">${pbEsc(mapNote)}</div>` : '';
    // 三本账的界面落点（Spec `packaging-bom-disclosure-panel.md` §C2）：与既有的「几何零件文档
    // 版本漂移」并列 —— 配对复核（逐条不一致）、零件回填失败、业务清单换版。三家空值语义不同，
    // 空 / 读不到 / 没有 必须分得开。
    const pairingRows = pbPairingMismatchRows(record);
    const pairingWhy = (record && record.pairing_review_unavailable) || {};
    const pairingBlock = Object.keys(pairingWhy || {}).length
      ? `<div class="pb-warning" data-pb-pairing-review-unavailable="${pbEsc(pairingWhy.code || 'pairing_review_unavailable')}">${pbEsc(pbPairingMismatchNote(record))}</div>`
      : (pairingRows.length
        ? `<div class="pb-hint" data-pb-pairing-review="${pairingRows.length}">${pbEsc(pbPairingMismatchNote(record))}
        <ul class="pb-list">${pairingRows.map(row => `<li class="pb-item" data-pb-pairing-mismatch="${pbEsc(row.key)}"><span class="pb-key">${pbEsc(row.key)}</span><span class="pb-name">${pbEsc(row.part_code)}</span><div class="pb-material">行材料：${pbEsc(row.row_material || '—')} · 几何件材料：${pbEsc(row.part_material || '—')}</div></li>`).join('')}</ul></div>`
        : '');
    const bindingNote = pbBindingErrorNote(record);
    const bindingBlock = bindingNote
      ? `<div class="pb-warning" data-pb-binding-error="${pbEsc(((record && record.binding_error) || {}).code || 'part_binding_failed')}">${pbEsc(bindingNote)}</div>`
      : '';
    const businessStaleRows = pbBusinessStaleRows(record);
    const businessStaleBlock = businessStaleRows.length
      ? `<div class="pb-warning" data-pb-business-stale="${businessStaleRows.length}">有 ${pbEsc(businessStaleRows.length)} 行是照上一版业务部件清单算的：
        <ul class="pb-list">${businessStaleRows.map(row => `<li class="pb-item" data-pb-business-stale-key="${pbEsc(row.key)}"><span class="pb-key">${pbEsc(row.key)}</span><span class="pb-name">${pbEsc(row.label)}</span></li>`).join('')}</ul></div>`
      : '';
    return `<section class="card section pb-panel" id="packagingBomPanel">
      <h2>部件展开与包装 BOM${boxTypeCode ? `（${pbEsc(boxTypeCode)}）` : ''}</h2>
      <div class="pb-hint">按第 4 批确认的盒型参数化展开：共 ${pbEsc(stats.total || 0)} 行 · 已算出 ${pbEsc(stats.computed || 0)} · 缺输入 ${pbEsc(stats.needs_input || 0)} · 已锁定 ${pbEsc(stats.locked || 0)}。尺寸按公式求值，缺变量一律留空交工艺经理补。`
      + `${partsHash ? `零件文档版本：${pbEsc(partsHash.slice(0, 12))}。` : ''}</div>
      ${unresolved}
      ${mapBlock}
      ${pbMaterialMapAccountBlock(record.business_material_rows)}
      ${pbBusinessPartsScopeBlock(record)}
      ${pbBusinessRowsBlock(record)}
      ${pairingBlock}
      ${bindingBlock}
      ${businessStaleBlock}
      ${mixedBanner}
      ${unknownBoxBanner}
      ${bboxBanner}
      ${staleBanner}
      ${unavailableBanner}
      <div class="pb-actions">
        <input id="pbOverrides" placeholder="变量覆盖，如：H盖=30, 包边=15" ${writable ? '' : 'disabled'}>
        <button class="btn primary" data-pb-build="1" ${writable ? '' : 'disabled'}>展开部件并生成 BOM</button>
      </div>
      ${writable ? '' : `<div class="pb-hint">${PB_WRITE_HINT}，当前为只读。</div>`}
      ${body}
      <div class="pb-hint">本批只做分类与关联，不算钱、不查价、不排工艺顺序（第 6、7 批）。</div>
    </section>`;
  }

  function pbParseOverrides() {
    const input = document.querySelector('#pbOverrides');
    const out = {};
    if (!input || !input.value) return out;
    input.value.split(/[,，;；]/).forEach(part => {
      const pair = part.split('=');
      if (pair.length !== 2) return;
      const name = String(pair[0]).trim();
      const value = parseFloat(String(pair[1]).trim());
      if (name && !isNaN(value)) out[name] = value;
    });
    return out;
  }
  async function pbRefresh() {
    const pid = pbProjectId();
    const host = document.querySelector('#packagingBomPanel');
    if (!pid || !host) return;
    const payload = await pbApi(`/api/projects/${encodeURIComponent(pid)}/requirement/packaging-bom`);
    const record = (payload || {}).bom || {};
    host.outerHTML = pbPanel(record, pbCanWrite(), record.box_type_code);
    pbBind(pid);
  }
  function pbBind(pid) {
    const build = document.querySelector('[data-pb-build]');
    if (build) {
      build.onclick = () => pbSubmit(pid, 'build', '/requirement/packaging-bom', {overrides: pbParseOverrides()}, '部件已展开、BOM 已更新');
    }
    document.querySelectorAll('[data-pb-lock]').forEach(button => {
      button.onclick = () => pbSubmit(pid, 'lock', '/requirement/packaging-bom/lock',
        {item_key: button.dataset.pbLock, locked: button.dataset.pbLocked !== '1'},
        button.dataset.pbLocked !== '1' ? '已锁定该行' : '已解锁该行');
    });
  }
  async function pbSubmit(pid, kind, suffix, body, okMessage) {
    if (pbBusy) return;
    pbBusy = true;
    try {
      await pbApi(`/api/projects/${encodeURIComponent(pid)}/requirement${suffix}`, {method: 'POST', body: JSON.stringify(body)});
      pbToast(okMessage);
      pbBusy = false;
      await pbRefresh();
    } catch (error) {
      pbBusy = false;
      pbToast((error && error.message) || '包装 BOM 操作失败', true);
    }
  }

  // 需求单不是包装行业就不挂载（其它行业一条请求都不发）。
  let pbMounting = false;
  let pbFailures = 0;
  async function pbMaybeMount() {
    if (pbMounting || pbFailures >= 2) return;
    const host = document.querySelector('#app .footer-actions');
    if (!host || document.querySelector('#packagingBomPanel')) return;
    const pid = pbProjectId();
    if (!pid) return;
    pbMounting = true;
    try {
      const requirement = await pbApi(`/api/projects/${encodeURIComponent(pid)}/requirement`);
      const industry = String((((requirement || {}).requirement || {}).data || {}).industry || '').trim();
      if (industry !== 'packaging') return;
      const placeholder = document.createElement('section');
      placeholder.id = 'packagingBomPanel';
      placeholder.className = 'card section pb-panel';
      placeholder.dataset.pending = '1';
      placeholder.innerHTML = '<h2>部件展开与包装 BOM</h2><div class="pb-empty">正在读取包装 BOM…</div>';
      host.parentNode.insertBefore(placeholder, host);
      await pbRefresh();
    } catch (error) {
      pbFailures += 1;
      const pending = document.querySelector('#packagingBomPanel[data-pending="1"]');
      if (pending) pending.remove();
    } finally {
      pbMounting = false;
    }
  }

  window.CfPackagingBomPanel = {mount: pbMaybeMount, refresh: pbRefresh};
  const pbStart = () => { pbMaybeMount().catch(() => {}); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', pbStart);
  else pbStart();
  const pbApp = document.querySelector('#app');
  if (pbApp && typeof MutationObserver === 'function') {
    new MutationObserver(() => { if (!document.querySelector('#packagingBomPanel')) pbStart(); }).observe(pbApp, {childList: true});
  }
})();

/* ------------------------------------------------------------------------ *
 * 包装第 6 批：工艺路线与标准工时面板
 * （Spec docs/specs/packaging-process-route.md §4）
 * 1.2 需求确认页里，包装需求单多一块路线面板：工序表（位次 / 设备 / 标准工时 /
 * 自动化 / 质控点）、待补工时缺口、聚合工序缺口、顺序违规、确认并冻结版本、
 * 版本快照列表与 stale 提示。
 * 只在 industry=packaging 的需求单上出现；其它行业完全不挂载（一条请求都不发）。
 * 接口：/api/projects/<pid>/requirement/packaging-route[/confirm|/versions]
 * ------------------------------------------------------------------------ */
(function () {
  const PR_WRITE_ROLES = ['process_manager', 'process_director', 'admin'];
  const PR_WRITE_HINT = '需要工艺经理、工艺技术总监或管理员权限';
  const PR_STATUS_LABELS = {draft: '草稿（未确认）', confirmed: '已确认'};
  const PR_STALE_LABELS = {route_changed: '工序序列已变', requirement_changed: '表面工艺字段已变', quantity_changed: '报价数量已变', bom_rebuilt: '排产照的那一版 BOM 已变', provenance_missing: '这份路线没记下排产照的是哪一版 BOM（历史数据，请重算）', box_type_reconfirmed: '确认的盒型已改'};
  let prPid = '';
  let prBusy = false;

  function prEsc(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  }
  function prToast(message, error) {
    if (typeof toast === 'function') { toast(message, error ? 4200 : 3000); return; }
    const el = document.createElement('div');
    el.className = `page-toast${error ? ' error' : ''}`;
    el.textContent = message;
    document.body.append(el);
    setTimeout(() => el.remove(), 3600);
  }
  async function prApi(url, options) {
    const response = await fetch(url, Object.assign({headers: {'Content-Type': 'application/json'}}, options || {}));
    if (!response.ok) {
      let detail = '';
      try {
        const payload = await response.json();
        const raw = payload.detail;
        /* 包装下游的业务错误统一回结构化 detail {code,message}（Spec
           docs/specs/packaging-downstream-block-code-parity.md §2.2）；
           只认字符串的话人话会被吞成 [object Object]。 */
        detail = (typeof raw === 'string' ? raw : (raw && raw.message)) || payload.message || '';
      } catch (error) { detail = ''; }
      throw new Error(detail || `请求失败（${response.status}）`);
    }
    return response.json();
  }
  function prProjectId() {
    if (prPid) return prPid;
    const query = new URLSearchParams(location.search);
    prPid = query.get('project_id') || query.get('pid') || document.body.dataset.projectId || '';
    return prPid;
  }
  function prRoles() {
    try { return (window.techSession && techSession.roles && techSession.roles()) || []; } catch (error) { return []; }
  }
  function prCanWrite() {
    const user = (typeof currentUser === 'function') ? (currentUser() || {}) : {};
    const role = String(user.role || user.cpq_role_code || '');
    if (PR_WRITE_ROLES.indexOf(role) >= 0) return true;
    return prRoles().some(item => PR_WRITE_ROLES.indexOf(String(item)) >= 0);
  }
  function prSeconds(value) {
    return (value === null || value === undefined || value === '') ? '待补' : `${value}s`;
  }
  function prSource(item) {
    const source = String(item.source || '');
    if (source.indexOf('requirement:') === 0) return `需求补齐（${prEsc(source.slice('requirement:'.length))}）`;
    if (source.indexOf('template:') === 0) return `模板展开（${prEsc(source.slice('template:'.length))}）`;
    if (source === 'template') return '模板';
    return prEsc(source || '—');
  }
  function prRow(item) {
    const needsTime = item.needs_standard_time || item.standard_seconds === null || item.standard_seconds === undefined;
    return `<tr class="pr-step${needsTime ? ' pr-step-pending' : ''}" data-step-no="${prEsc(item.step_no)}">
      <td class="pr-no">${prEsc(item.step_no)}</td>
      <td class="pr-name">${prEsc(item.step_name)}</td>
      <td class="pr-station">${prEsc(item.workstation || '—')}</td>
      <td class="pr-seconds">${needsTime ? '<span class="pr-todo">待补</span>' : prEsc(prSeconds(item.standard_seconds))}</td>
      <td class="pr-auto">${prEsc(item.automation || '—')}</td>
      <td class="pr-control">${prEsc(item.control_point || '—')}</td>
      <td class="pr-source">${prSource(item)}</td>
    </tr>`;
  }
  function prPanel(record, writable, versions) {
    const route = record || {};
    const steps = route.steps || [];
    const stats = route.stats || {};
    const gaps = route.gaps || {};
    const built = !!route.built;
    const title = `工艺路线与标准工时${route.box_type_code ? `（${prEsc(route.box_type_code)}）` : ''}`;
    const status = String(route.status || 'draft');
    const confirmed = route.confirmed_by
      ? `<span class="pr-confirmed">已确认：${prEsc(route.confirmed_by)} · ${prEsc(route.confirmed_at || '—')}</span>` : '';
    const staleReasons = Array.isArray(route.stale_reasons) ? route.stale_reasons : [];
    const stale = route.stale
      ? `<div class="pr-stale" data-pr-stale="1">输入已变化，请重新确认：${prEsc(staleReasons.map(code => PR_STALE_LABELS[code] || code).join('、'))}${staleReasons.includes('bom_rebuilt') ? '（排产照的 BOM 已变，请重算工艺路线）' : ''}</div>` : '';
    // 当前 BOM 读不到 = "比较不了"，不许说成"没有上游版本"（Spec packaging-route-bom-version-pinning.md §2.5）。
    const bomUnknown = (route.bom_unavailable && route.bom_unavailable.code)
      ? `<div class="pr-stale" data-pr-bom-unavailable="1">当前读不到包装 BOM，无法核对这份路线照的是哪一版（不是"没有上游版本"）。</div>` : '';
    // 盒型轴（Spec packaging-route-box-type-drift.md §2.1）：两侧盒型都写出来，别让人猜。
    const boxDrift = staleReasons.includes('box_type_reconfirmed')
      ? `<div class="pr-stale" data-pr-box-drift="1">这条路线是照盒型 ${prEsc(route.box_type_code || '—')} 排的，现在确认的是 ${prEsc(route.current_box_type_code || '—')}，请重排。</div>` : '';
    // 读不到盒型确认记录 = "比较不了"，不许说成"盒型没变"（Spec §2.3）。
    const boxUnknown = (route.box_match_unavailable && route.box_match_unavailable.code)
      ? `<div class="pr-stale" data-pr-box-match-unavailable="1">当前读不到盒型确认记录，无法核对这条路线照的是哪个盒型。</div>` : '';
    // "按当前输入排不出来"要报出来（Spec packaging-route-recompute-unavailable.md §2.3）：
    // 披露归披露，已存的路线与工序照旧显示；原因码原样带出来，不许只报"有问题"。
    const recomputeUnknown = (route.route_recompute_unavailable
      && (route.route_recompute_unavailable.code || route.route_recompute_unavailable.reason))
      ? `<div class="pr-stale" data-pr-recompute-unavailable="1">按当前输入已经排不出这条路线（原因：${prEsc(route.route_recompute_unavailable.code || '未标明')}${route.route_recompute_unavailable.reason ? ` · ${prEsc(route.route_recompute_unavailable.reason)}` : ''}），请检查盒型工艺模板。</div>` : '';
    const needsTime = (gaps.needs_standard_time || []);
    const aggregate = (gaps.aggregate_steps || []);
    const violations = (gaps.order_violations || []);
    const gapLines = [
      // 模板没了这条缺口读回时必须是真的（Spec packaging-route-recompute-unavailable.md §2.1/§2.3）：
      // 重排会 409、读回却说"没缺口"就是界面两侧打架。
      gaps.no_process_template ? `<div class="pr-gap pr-gap-error">盒型 ${prEsc(route.box_type_code || '—')} 没有工艺模板，按当前输入排不出工艺路线（重排会直接失败，请先补齐模板）。</div>` : '',
      needsTime.length ? `<div class="pr-gap">待补标准工时：${prEsc(needsTime.join('、'))}（模板没有这道工序的秒数，不编数）</div>` : '',
      aggregate.length ? `<div class="pr-gap">未拆开的聚合工序：${prEsc(aggregate.join('、'))}（需求没填覆膜/烫金/UV，保留模板原样）</div>` : '',
      violations.length ? `<div class="pr-gap pr-gap-error">顺序违规：${prEsc(violations.join('；'))}（不改好不能确认）</div>` : '',
    ].join('');
    const table = steps.length
      ? `<table class="pr-table">
          <thead><tr><th>序号</th><th>工序</th><th>设备</th><th>标准工时</th><th>自动化</th><th>质控点</th><th>来源</th></tr></thead>
          <tbody>${steps.map(prRow).join('')}</tbody>
        </table>`
      : '<div class="pr-empty">还没有工艺路线。确认盒型并生成包装 BOM 后，点「重算工艺路线」开始。</div>';
    const versionList = (versions || []).length
      ? `<ol class="pr-versions">${versions.map(item => `<li class="pr-version">
          <span class="pr-version-no">v${prEsc(item.version)}</span>
          <span class="pr-version-meta">${prEsc(item.confirmed_by || '—')} · ${prEsc(item.confirmed_at || '—')}</span>
          <span class="pr-version-meta">工序 ${prEsc((JSON.parse(item.steps_json || '[]') || []).length)} 道 · 单件 ${prEsc(item.total_seconds === null || item.total_seconds === undefined ? '—' : `${item.total_seconds}s`)}</span>
          <span class="pr-version-meta">指纹 ${prEsc(String(item.steps_fingerprint || '').slice(0, 8) || '—')}</span>
          <span class="pr-version-meta">照的 BOM ${prEsc((item.source_versions || {}).bom_version || '—（历史数据，未记录）')}</span>
        </li>`).join('')}</ol>`
      : '<div class="pr-empty">还没有冻结版本。工艺经理确认后会出现第 1 条快照。</div>';
    return `<section class="card section pr-panel" id="packagingRoutePanel">
      <h2>${title}</h2>
      <div class="pr-hint">工序顺序按第 6 批的规范位次排（不用知识库的里程碑分组）；顺序违规 / 待补工时都是显式缺口，不编数。状态：${prEsc(PR_STATUS_LABELS[status] || status)}${confirmed ? ` · ${confirmed}` : ''}</div>
      ${stale}
      ${bomUnknown}
      ${boxDrift}
      ${boxUnknown}
      ${recomputeUnknown}
      <div class="pr-hint">共 ${prEsc(stats.step_count || 0)} 道 · 模板工序 ${prEsc(stats.template_steps || 0)} · 需求补齐 ${prEsc(stats.synthetic_steps || 0)} · 手工 ${prEsc(stats.manual_steps || 0)} · 自动 ${prEsc(stats.auto_steps || 0)} · 已冻结 ${prEsc(stats.confirmed_versions || 0)} 版。单件合计 ${prEsc(route.total_seconds === null || route.total_seconds === undefined ? '—' : `${route.total_seconds}s`)} · 批量 ${prEsc(route.batch_seconds === null || route.batch_seconds === undefined ? '—' : `${route.batch_seconds}s`)}。</div>
      ${gapLines}
      <div class="pr-actions">
        <button class="btn secondary" data-pr-build="1" ${writable ? '' : 'disabled'}>重算工艺路线</button>
        <button class="btn primary" data-pr-confirm="1" ${writable && built && !violations.length ? '' : 'disabled'}>确认并冻结版本</button>
      </div>
      ${writable ? '' : `<div class="pr-hint">${PR_WRITE_HINT}，当前为只读。</div>`}
      ${table}
      <h3>版本快照</h3>
      ${versionList}
      <div class="pr-hint">本批只排路线与标准工时，不算成本价格、不做利润报价、不排产能与设备日历（第 7、8 批）。</div>
    </section>`;
  }

  async function prRefresh() {
    const pid = prProjectId();
    const host = document.querySelector('#packagingRoutePanel');
    if (!pid || !host) return;
    const payload = await prApi(`/api/projects/${encodeURIComponent(pid)}/requirement/packaging-route`);
    const record = (payload || {}).route || {};
    let versions = [];
    if (record.built) {
      const versionPayload = await prApi(`/api/projects/${encodeURIComponent(pid)}/requirement/packaging-route/versions`);
      versions = (versionPayload || {}).versions || [];
    }
    host.outerHTML = prPanel(record, prCanWrite(), versions);
    prBind(pid);
  }
  function prBind(pid) {
    const build = document.querySelector('[data-pr-build]');
    if (build) build.onclick = () => prSubmit(pid, '/requirement/packaging-route', {}, '工艺路线已重算');
    const confirm = document.querySelector('[data-pr-confirm]');
    if (confirm) confirm.onclick = () => prSubmit(pid, '/requirement/packaging-route/confirm', {}, '已确认并冻结版本');
  }
  async function prSubmit(pid, suffix, body, okMessage) {
    if (prBusy) return;
    prBusy = true;
    try {
      await prApi(`/api/projects/${encodeURIComponent(pid)}/requirement${suffix}`, {method: 'POST', body: JSON.stringify(body)});
      prToast(okMessage);
      prBusy = false;
      await prRefresh();
    } catch (error) {
      prBusy = false;
      prToast((error && error.message) || '包装工艺路线操作失败', true);
    }
  }

  // 需求单不是包装行业就不挂载（其它行业一条请求都不发）。
  let prMounting = false;
  let prFailures = 0;
  async function prMaybeMount() {
    if (prMounting || prFailures >= 2) return;
    const host = document.querySelector('#app .footer-actions');
    if (!host || document.querySelector('#packagingRoutePanel')) return;
    const pid = prProjectId();
    if (!pid) return;
    prMounting = true;
    try {
      const requirement = await prApi(`/api/projects/${encodeURIComponent(pid)}/requirement`);
      const industry = String((((requirement || {}).requirement || {}).data || {}).industry || '').trim();
      if (industry !== 'packaging') return;
      const placeholder = document.createElement('section');
      placeholder.id = 'packagingRoutePanel';
      placeholder.className = 'card section pr-panel';
      placeholder.dataset.pending = '1';
      placeholder.innerHTML = '<h2>工艺路线与标准工时</h2><div class="pr-empty">正在读取工艺路线…</div>';
      host.parentNode.insertBefore(placeholder, host);
      await prRefresh();
    } catch (error) {
      prFailures += 1;
      const pending = document.querySelector('#packagingRoutePanel[data-pending="1"]');
      if (pending) pending.remove();
    } finally {
      prMounting = false;
    }
  }

  window.CfPackagingRoutePanel = {mount: prMaybeMount, refresh: prRefresh};
  const prStart = () => { prMaybeMount().catch(() => {}); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', prStart);
  else prStart();
  const prApp = document.querySelector('#app');
  if (prApp && typeof MutationObserver === 'function') {
    new MutationObserver(() => { if (!document.querySelector('#packagingRoutePanel')) prStart(); }).observe(prApp, {childList: true});
  }
})();

/* ------------------------------------------------------------------------ *
 * 包装第 7 批：专用成本测算面板
 * （Spec docs/specs/packaging-cost-engine.md §4 / §8）
 * 1.2 需求确认页里，包装需求单多一块成本面板：三层汇总（部件 / 项目 / 成本类别）、
 * 24 个成本类别、10 个报告分组、逐行明细、缺口（缺料价 / 缺费率 / 缺工时 → 明确
 * 「待询价」，绝不当 0 静默计入）与工装分摊 / 最低收费命中标记。
 * 只在 industry=packaging 且已有确认盒型 + BOM + 路线时出现；其它行业一条请求都不发。
 * 接口：/api/projects/<pid>/requirement/packaging-cost[/items]
 * ------------------------------------------------------------------------ */
(function () {
  const PC_WRITE_ROLES = ['process_manager', 'process_director', 'admin'];
  const PC_WRITE_HINT = '需要工艺经理、工艺技术总监或管理员权限';
  // 13 个报告分组，顺序与 Spec 修复第 1 批 §2.1（packaging_cost.REPORT_GROUPS）一致；
  // 漏写组名 = 该组金额在界面上永远不可见。
  const PC_GROUP_ORDER = ['材料', '印刷', '覆膜', '烫金', '丝印', '表面处理', '裱纸', '模切',
    '装订贴盒', '开槽', '手工', '其他费用', '包装'];
  let pcPid = '';
  let pcBusy = false;
  let pcSendBusy = false;
  let pcRelayBusy = false;

  function pcEsc(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  }
  function pcMoney(value) {
    if (value === null || value === undefined || value === '') return '—';
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(6) : '—';
  }
  function pcToast(message, error) {
    if (typeof toast === 'function') { toast(message, error ? 4200 : 3000); return; }
    const el = document.createElement('div');
    el.className = `page-toast${error ? ' error' : ''}`;
    el.textContent = message;
    document.body.append(el);
    setTimeout(() => el.remove(), 3600);
  }
  async function pcApi(url, options) {
    const response = await fetch(url, Object.assign({headers: {'Content-Type': 'application/json'}}, options || {}));
    if (!response.ok) {
      let detail = '';
      let code = '';
      let candidates = [];
      try {
        const payload = await response.json();
        const raw = payload.detail;
        /* 包装下游的业务错误统一回结构化 detail {code,message}（Spec
           docs/specs/packaging-downstream-block-code-parity.md §2.2）；
           只认字符串的话人话会被吞成 [object Object]。 */
        detail = (typeof raw === 'string' ? raw : (raw && raw.message)) || payload.message || '';
        code = (raw && typeof raw === 'object' && raw.code) ? String(raw.code) : '';
        candidates = (raw && typeof raw === 'object' && raw.candidates) ? raw.candidates : [];
      } catch (error) { detail = ''; }
      const failure = new Error(detail || `请求失败（${response.status}）`);
      /* 三个键原样带出去（缺口放行 / 落点认不回来时界面要按 code 问一句、并回传原因）；
         message 与改动前逐字一致 —— 既有调用点只看 message。 */
      failure.status = response.status;
      failure.code = code;
      failure.candidates = candidates;
      throw failure;
    }
    return response.json();
  }
  function pcProjectId() {
    if (pcPid) return pcPid;
    const query = new URLSearchParams(location.search);
    pcPid = query.get('project_id') || query.get('pid') || document.body.dataset.projectId || '';
    return pcPid;
  }
  function pcRoles() {
    try { return (window.techSession && techSession.roles && techSession.roles()) || []; } catch (error) { return []; }
  }
  function pcCanWrite() {
    const user = (typeof currentUser === 'function') ? (currentUser() || {}) : {};
    const role = String(user.role || user.cpq_role_code || '');
    if (PC_WRITE_ROLES.indexOf(role) >= 0) return true;
    return pcRoles().some(item => PC_WRITE_ROLES.indexOf(String(item)) >= 0);
  }
  // 裁决与缺口分层（Spec `packaging-cost-readiness-panel.md` §C1）：裁决**只**读后端 payload，
  // 前端不按缺口重算"正式 / 暂定"；严重度、要补的变量与补数入口也都由后端给。
  function pcReadinessVerdict(cost) {
    const record = (cost && typeof cost === 'object') ? cost : {};
    const readiness = (record.readiness && typeof record.readiness === 'object') ? record.readiness : {};
    const raw = String(readiness.verdict === undefined || readiness.verdict === null
      ? '' : readiness.verdict).trim();
    const verdict = (raw === 'formal' || raw === 'provisional') ? raw : '';
    const headlines = {
      formal: '正式成本：缺口已清零，可用于正式报价。',
      provisional: '暂定成本：有阻断缺口或还没测算，出价前必须走放行留痕（POC 豁免签字）。'
    };
    const rawReasons = Array.isArray(readiness.reasons) ? readiness.reasons : [];
    const reasons = rawReasons
      .map(item => String(item === undefined || item === null ? '' : item).trim())
      .filter(item => item);
    const count = key => {
      const number = Number(readiness[key]);
      return (Number.isFinite(number) && number > 0) ? number : 0;
    };
    return {
      verdict: verdict, formal: verdict === 'formal',
      headline: headlines[verdict] || '这份成本没有正式/暂定的裁决（后端没给 verdict），别当成正式成本。',
      reasons: reasons,
      counts: {gap_total: count('gap_total'), blocking_total: count('blocking_total'),
               advisory_total: count('advisory_total'), unbound_total: count('unbound_total')}
    };
  }
  function pcGapSeverityLabel(severity) {
    const text = typeof severity === 'string' ? severity.trim() : '';
    if (text === 'blocking') return '阻断（挡住正式成本）';
    if (text === 'advisory') return '提示（不影响正式/暂定）';
    // 闭集外的严重度一律未知档：不许归到任一一档。
    return '未知档（后端没给严重度）';
  }
  function pcGapActionText(gap) {
    const row = (gap && typeof gap === 'object') ? gap : {};
    const text = value => String(value === undefined || value === null ? '' : value).trim();
    const vars = (Array.isArray(row.missing_variable) ? row.missing_variable : [])
      .map(text).filter(item => item);
    const action = text(row.resolution_action);
    const entry = text(row.resolution_entry);
    if (!vars.length && !action && !entry) return '';
    let out = '';
    if (vars.length) out += `要补：${vars.join('、')}`;
    if (action) out += `；${action}`;
    if (entry) out += `（入口：${entry}）`;
    return out;
  }
  function pcGapPrefix(code) {
    // 「待询价」只留给价格 / 费率类：缺尺寸 / 缺公式 / 缺口径不是"待询价"。
    const quoted = ['material_price_missing', 'material_price_unit_missing',
                    'material_price_unit_mismatch', 'rate_missing', 'freight_rule_missing'];
    const text = typeof code === 'string' ? code.trim() : '';
    return quoted.indexOf(text) >= 0 ? '待询价' : '待补输入';
  }
  // 包材绑定数据源（Spec `packaging-cost-content-binding-panel.md` §C1）：`source` **只**读后端
  // payload —— 「认不出这一单用了哪几项包材」（`none`）与「拿权威绑定算的」（`authoritative`）
  // 在读接口上同形（都是 `bound_gaps = []`），不把来源说出来，「没有阻断缺口」就会被读成
  // 「没有缺口」；被降级披露的包材项还必须**指名道姓**（§2.4）。
  function pcContentBinding(cost) {
    const record = (cost && typeof cost === 'object') ? cost : {};
    const binding = (record.content_binding && typeof record.content_binding === 'object')
      ? record.content_binding : {};
    const text = value => String(value === undefined || value === null ? '' : value).trim();
    const raw = text(binding.source);
    // 闭集外的来源一律不认：不假装权威（今天后端老实报 `none`），也不假装缺失。
    const source = (raw === 'none' || raw === 'authoritative') ? raw : '';
    const headlines = {
      none: '包材绑定数据源缺失：认不出这一单用了哪几项包材，包材缺口（content_formula_error）只披露、不进阻断。',
      authoritative: '包材绑定数据源：权威来源，这一单用哪几项包材是从权威数据里读的。'
    };
    const count = key => {
      const number = Number(binding[key]);
      return (Number.isFinite(number) && number > 0) ? number : 0;
    };
    const codes = (Array.isArray(binding.unbound_codes) ? binding.unbound_codes : [])
      .map(text).filter(item => item);
    return {
      source: source, state: source || 'unknown',
      headline: headlines[source]
        || '后端没给包材绑定来源（content_binding.source），「包材没有缺口」这句话不成立。',
      boundTotal: count('bound_total'), unboundTotal: count('unbound_total'),
      unboundCodes: codes
    };
  }
  function pcContentBindingCodesText(cost) {
    const binding = pcContentBinding(cost);
    if (!binding.unboundCodes.length) return '';
    // 报告要能点到具体包材项：逐字点名，不改写 / 不截断 / 不加序号。
    return `被降级披露的包材项：${binding.unboundCodes.join('、')}`;
  }
  function pcContentBindingBlock(cost) {
    const binding = pcContentBinding(cost);
    const codes = pcContentBindingCodesText(cost);
    return `<div class="pc-hint" data-pc-content-binding="${pcEsc(binding.state)}">`
      + `${pcEsc(binding.headline)} · 绑定 ${pcEsc(binding.boundTotal)} · 未绑 ${pcEsc(binding.unboundTotal)}</div>`
      + (codes
        ? `<div class="pc-hint" data-pc-content-binding-codes="${pcEsc(binding.unboundCodes.length)}">${pcEsc(codes)}</div>`
        : '');
  }
  /* 这一单用了几个默认值、哪几个（Spec `packaging-cost-assumption-disclosure.md` §C4）：
     两个计数**逐字取后端**（`assumptions_total` / `assumptions_0903_total`，明细行那几个
     `=默认=` / `=0903=` 由后端数，前端不再数一遍）；明细行那本账按 seq 升序展开。
     顶层纯函数：体内无 DOM / 无 fetch( ，可被 `node -e` 抽出来真跑。 */
  function pcAssumptions(cost, items) {
    const record = (cost && typeof cost === 'object') ? cost : {};
    const text = value => String(value === undefined || value === null ? '' : value).trim();
    const count = key => {
      const raw = record[key];
      if (raw === undefined || raw === null || raw === '') return null;
      const number = Number(raw);
      return (Number.isFinite(number) && number >= 0) ? number : null;
    };
    const total = count('assumptions_total');
    const from0903 = count('assumptions_0903_total');
    const rows = [];
    (Array.isArray(items) ? items : [])
      .map(item => ((item && typeof item === 'object') ? item : {}))
      .sort((a, b) => Number(a.seq || 0) - Number(b.seq || 0))
      .forEach(item => {
        const part = text(item.part_name) || text(item.part_code) || '项目级';
        const texts = Array.isArray(item.assumptions) ? item.assumptions : [];
        texts.forEach(entry => { rows.push({part: part, text: text(entry)}); });
      });
    let summary;
    if (total === null || from0903 === null) {
      summary = '后端没给这本账（assumptions_total / assumptions_0903_total）：这一单用了几个默认值是未知，不是 0。';
    } else if (total === 0) {
      summary = '这一单没有用默认值顶上去的参数。';
    } else {
      summary = `这一单有 ${total} 条默认值，其中 ${from0903} 条来自 0903 常量。`;
    }
    return {total: total, source0903: from0903, rows: rows, summary: summary};
  }
  function pcAssumptionsBlock(cost, items) {
    const book = pcAssumptions(cost, items);
    const total = (book.total === null) ? '' : book.total;
    return `<div class="pc-hint" data-pc-assumptions="${pcEsc(total)}">${pcEsc(book.summary)}</div>`
      + book.rows.map((row, index) =>
        `<div class="pc-hint" data-pc-assumption="${index}">${pcEsc(`${row.part} · ${row.text}`)}</div>`
      ).join('');
  }
  function pcCategoryRows(cost) {
    const categories = cost.categories || {};
    const labels = cost.category_labels || {};
    const codes = Object.keys(categories);
    return codes.map(code => {
      const name = labels[code] || code;
      const amount = categories[code];
      const zero = amount === null || amount === undefined || Number(amount) === 0;
      return `<tr><td>${pcEsc(name)}</td><td class="pc-num${zero ? ' pc-zero' : ''}">${pcMoney(amount)}</td></tr>`;
    }).join('');
  }
  function pcGroupRows(cost) {
    const groups = cost.report_groups || {};
    return PC_GROUP_ORDER.filter(name => name in groups).map(name =>
      `<tr><td>${pcEsc(name)}</td><td class="pc-num">${pcMoney(groups[name])}</td></tr>`).join('');
  }
  function pcItemRows(items) {
    if (!items.length) return '<div class="pc-empty">还没有成本明细行。</div>';
    return `<table class="pc-table"><thead><tr><th>部件</th><th>类别</th><th class="pc-num">金额</th><th class="pc-num">含损耗</th><th>来源</th></tr></thead><tbody>`
      + items.map(item => {
        const flag = item.min_charge_applied ? ' <span class="pc-flag">最低收费</span>' : '';
        return `<tr><td>${pcEsc(item.part_name || '—')}</td><td>${pcEsc(item.cost_category || '—')}</td>`
          + `<td class="pc-num">${pcMoney(item.amount)}${flag}</td><td class="pc-num">${pcMoney(item.amount_with_loss)}</td>`
          + `<td class="pc-source">${pcEsc(item.source_ref || item.source || '—')}</td></tr>`;
      }).join('') + '</tbody></table>';
  }
  //: 成本「输入已变」的理由（Spec `packaging-cost-input-version-pinning.md` §2.2/§2.5）：
  // 后端给的是稳定的 reason_code，人话只在这里说一次。
  const PC_STALE_REASONS = {
    bom_rebuilt: 'BOM 已重建（尺寸 / 材料 / 行数变了）',
    route_reconfirmed: '工艺路线已重新确认',
    provenance_missing: '这份成本是旧版本算的，没有记下来源（无从判断输入是否变过）',
    // 对照表重新导入 = 这份成本照的那一版业务部件清单已经换过（Spec
    // `packaging-business-parts-version-pinning.md` §2.4）：不许把码直接甩给用户。
    business_parts_reimported: '按上一版业务部件清单建的（对照表重新导入过，请重建 BOM 后重算成本）',
  };
  function pcStaleLine(reason) {
    return PC_STALE_REASONS[String(reason || '')] || `输入已变（${reason}）`;
  }
  function pcStaleBanner(record) {
    if (!record || !record.stale) return '';
    const reasons = Array.isArray(record.stale_reasons) ? record.stale_reasons : [];
    const lines = reasons.length ? reasons.map(code => `<li>${pcEsc(pcStaleLine(code))}</li>`).join('')
      : '<li>输入已变</li>';
    return `<div class="pc-warning" data-pc-stale="${reasons.length}">输入已变（BOM / 工艺路线），请重新测算后再用这份成本 —— 现在的金额是按旧输入算出来的。`
      + `<ul class="pc-stale-reasons">${lines}</ul></div>`;
  }
  // 「比较不了」与「变了」分开说（Spec `packaging-cost-input-version-pinning.md` §2.2）：
  // 当前 BOM 读不到时既不断言"变了"，也不许看起来"没变"。
  function pcBomUnavailableBanner(record) {
    const flag = (record || {}).bom_unavailable || {};
    if (!flag.code) return '';
    return `<div class="pc-warning" data-pc-bom-unavailable="${pcEsc(flag.code)}">暂时读不到当前 BOM${flag.reason ? `（${pcEsc(flag.reason)}）` : ''}，无法判断这份成本是否还跟得上；这不代表输入没变。</div>`;
  }
  // 路线那条轴与 BOM 那条轴各自独立披露（Spec `packaging-cost-route-version-read-failure.md` §2.2）：
  // "读不到当前路线版本"不是"路线重新确认过"，不许混进 PC_STALE_REASONS 那张"变了"的人话表。
  function pcRouteUnavailableBanner(record) {
    const flag = (record || {}).route_unavailable || {};
    if (!flag.code) return '';
    return `<div class="pc-warning" data-pc-route-unavailable="${pcEsc(flag.code)}">暂时读不到当前工艺路线版本${flag.reason ? `（${pcEsc(flag.reason)}）` : ''}，无法判断这份成本是否还跟得上；这不代表输入没变。</div>`;
  }
  // 「这份成本照哪一版规则算的」的来源（Spec `packaging-cost-and-handoff-static-downgrade-disclosure.md`
  // §2.2 / §3.4）：读不到与"没拉过快照"都不许显示成"版本号就是空"。
  function pcRuleSnapshotBanner(record) {
    const source = String((record || {}).rule_snapshot_source || '');
    if (source !== 'unavailable' && source !== 'none') return '';
    if (source === 'unavailable') {
      const flag = (record || {}).rule_snapshot_unavailable || {};
      return `<div class="pc-warning" data-pc-rule-snapshot="unavailable">暂时读不到规则快照版本${flag.reason ? `（${pcEsc(flag.reason)}）` : ''}，这份成本照哪一版规则算的暂时核实不了（可重试）。</div>`;
    }
    return '<div class="pc-warning" data-pc-rule-snapshot="none">这份成本算的时候还没拉过规则快照，照哪一版规则算的没有记下来 —— 不是"版本号为空"。</div>';
  }
  /* 回传记录的输入漂移（Spec `packaging-handoff-input-drift-disclosure.md` §2.3）：
     "这一版回传是按哪一版成本发的、现在成本变了没有"必须在成本面板上说一句 —— 不许让人觉得
     旧回传还是当前有效。 */
  const PC_HANDOFF_STALE_LABELS = {
    cost_recomputed: '成本已重算（这一版回传是按旧成本发的）',
    provenance_missing: '这一版回传没记下当时的成本版本（历史数据）',
  };
  // 业务部件清单那条轴（`packaging-business-parts-version-pinning.md` §2.3）的读失败披露
  // （Spec `packaging-cost-business-parts-read-failure-panel.md` §C1）：与 BOM / 路线两条轴同一条
  // 纪律 ——「读不到」**不是**「没变」（那条轴比不了就不报漂移），所以既不进那张"变了"的人话表，
  // 也不许静默：这一版成本只按几何零件算。
  function pcBusinessPartsUnavailable(record) {
    const value = (record || {}).business_parts_unavailable;
    if (value === undefined || value === null || typeof value !== 'object' || Array.isArray(value)) {
      // 老载荷连这一栏都没有：不许当成"清单没换版"，也不许当成"读不到"。
      return {state: 'unknown', code: '', reason: '', headline: ''};
    }
    const text = item => String(item === undefined || item === null ? '' : item).trim();
    if (!Object.keys(value).length) {
      // 后端规定正常给 `{}`：读得到就是读得到，不编话。
      return {state: 'ok', code: '', reason: '', headline: ''};
    }
    return {
      state: 'unavailable',
      code: text(value.code) || 'business_parts_unavailable',
      reason: text(value.reason),
      headline: '暂时读不到业务部件清单：这一版成本只按几何零件算，'
        + '而且判不了清单有没有换版 —— 这不代表没换版。'
    };
  }
  function pcBusinessPartsBanner(record) {
    const facts = pcBusinessPartsUnavailable(record);
    if (facts.state !== 'unavailable') return '';
    return `<div class="pc-warning" data-pc-business-parts-unavailable="${pcEsc(facts.code)}">`
      + `${pcEsc(facts.headline)}${facts.reason ? `（${pcEsc(facts.reason)}）` : ''}</div>`;
  }
  function pcHandoffDriftBanner(handoff) {
    const record = handoff || {};
    if (!record.handoff_no) return '';
    const reasons = Array.isArray(record.stale_reasons) ? record.stale_reasons : [];
    if (record.stale || reasons.length) {
      const lines = reasons.length
        ? reasons.map(code => `<li>${pcEsc(PC_HANDOFF_STALE_LABELS[code] || code)}</li>`).join('')
        : '<li>成本已重算</li>';
      return `<div class="pc-warning" data-pc-handoff-stale="${reasons.length}">成本已重算，`
        + `这一版回传（第 ${pcEsc(record.version_no ?? '—')} 版）是按旧成本发的，请重新回传。`
        + `<ul class="pc-stale-reasons">${lines}</ul></div>`;
    }
    // 读不到成本 = "比较不了"，不许说成"没有成本"（Spec §2.3）。
    if (record.cost_unavailable && record.cost_unavailable.code) {
      return '<div class="pc-warning" data-pc-handoff-cost-unavailable="1">'
        + '当前读不到成本，无法核对这一版回传是按哪一版成本发的。</div>';
    }
    return '';
  }
  /* 留痕欠条这一串（Spec docs/specs/packaging-handoff-audit-relay.md §C1/C2 +
     docs/specs/packaging-handoff-audit-pending-panel.md §C1/§C2）：
     回传成功 ≠ 留痕落下。后端把"这条留痕现在处在什么状态"写成七键披露体
     （`ok` / `attempts` / `pending`），面板只把它翻成人话 —— 不猜、不重算、
     不把 message 重新措辞（Spec §4）。
     顶层风格与 boxCandidateRunnabilityNote() 一致：体内无 DOM / 无 `fetch(` /
     无 storage，可被 `node -e` 抽出来真跑。 */
  function pcAuditWarning(audit) {
    const item = (audit && typeof audit === 'object') ? audit : null;
    if (!item || item.ok !== false) return '';
    const attempts = Number(item.attempts);
    const tried = (Number.isFinite(attempts) && attempts > 0) ? `（试了 ${attempts} 次）` : '';
    const message = String(item.message || '').trim();
    const pending = String(item.pending || '');
    const tail = pending === 'recorded'
      ? '；已记成待补写，可在下面「补写留痕」里补'
      : (pending === 'unavailable'
        ? '；连待补写也没能记下，接口恢复前这条留痕只活在这次响应里'
        : '；等接口恢复后重发一次回传即可');
    return `这条回传的留痕没落下${tried}：${message || '后端没有给原因'}${tail}`;
  }
  function pcPendingRows(doc) {
    const items = (doc && typeof doc === 'object' && Array.isArray(doc.items)) ? doc.items : [];
    return items.filter(item => item && typeof item === 'object').map(item => {
      const payload = (item.payload && typeof item.payload === 'object') ? item.payload : {};
      const requirementNo = String(payload.requirement_no || '').trim() || '（未知需求单）';
      const version = Number(payload.version_no);
      const versionText = Number.isFinite(version) ? ` · 第 ${version} 版` : '';
      const code = String(item.code || '').trim();
      return {id: String(item.pending_id || ''),
              text: `${requirementNo}${versionText}${code ? ` · ${code}` : ''}`,
              when: String(item.recorded_at || '').trim()};
    });
  }
  function pcPendingHeadline(doc) {
    const rows = pcPendingRows(doc);
    return rows.length ? `还欠 ${rows.length} 条留痕没落下` : '';
  }
  function pcRelayText(result) {
    const item = (result && typeof result === 'object') ? result : null;
    if (!item) return '';
    const relayed = Number(item.relayed);
    const remaining = Number(item.remaining);
    const done = Number.isFinite(relayed) ? relayed : 0;
    const left = Number.isFinite(remaining) ? remaining : 0;
    const message = String(item.message || '').trim();
    return `补写留痕：补上 ${done} 条，还欠 ${left} 条${message ? ` —— ${message}` : ''}`;
  }
  function pcAuditPendingPath(pid) {
    return `/api/projects/${encodeURIComponent(pid)}/requirement/packaging-quote/audit-pending`;
  }
  function pcRelayAuditsPath(pid) {
    return `/api/projects/${encodeURIComponent(pid)}/requirement/packaging-quote/audit-pending/relay`;
  }
  /* 留痕块：告警句 + 还欠几条 + 逐条 + 补写按钮。两样都没有就整块不渲染 ——
     不许拼一个空块出来占位（Spec §C2）。 */
  function pcAuditBlock(handoff, pending, writable) {
    const warning = pcAuditWarning((handoff || {}).audit);
    const rows = pcPendingRows(pending);
    if (!warning && !rows.length) return '';
    const head = pcPendingHeadline(pending);
    const list = rows.length
      ? `<ul class="pc-audit-rows">${rows.map(row => `<li data-pc-audit-id="${pcEsc(row.id)}">${pcEsc(row.text)}${row.when ? ` · ${pcEsc(row.when)}` : ''}</li>`).join('')}</ul>`
      : '';
    const button = (rows.length && writable)
      ? '<button class="btn" data-pc-audit-relay="1">补写留痕</button>' : '';
    const hint = (rows.length && !writable)
      ? `<div class="pc-hint">补写要${PC_WRITE_HINT}，当前为只读。</div>` : '';
    return `<div class="pc-audit" data-pc-audit="1">`
      + `${warning ? `<div class="pc-audit-warning">${pcEsc(warning)}</div>` : ''}`
      + `${head ? `<div class="pc-audit-headline">${pcEsc(head)}</div>` : ''}`
      + `${list}${button}${hint}</div>`;
  }
  function pcPanel(cost, items, writable, handoff, pending) {
    const record = cost || {};
    const built = !!record.built;
    const gaps = record.gaps || [];
    // 裁决条 + 缺口分层（Spec `packaging-cost-readiness-panel.md` §C2）：证据优先取
    // `readiness.gaps`（逐条带 severity / 要补的变量 / 补数入口），老载荷退回 `gaps`；
    // 两处都不许隐藏 —— "暂定但没说出来"就是静默降级。
    const readiness = pcReadinessVerdict(cost);
    const readinessBlock = `<div class="pc-hint" data-pc-readiness="${pcEsc(readiness.verdict || 'unknown')}">`
      + `${pcEsc(readiness.headline)} · 阻断 ${pcEsc(readiness.counts.blocking_total)} · 提示 ${pcEsc(readiness.counts.advisory_total)} · 缺口合计 ${pcEsc(readiness.counts.gap_total)}</div>`
      + (readiness.reasons.length
        ? `<ul class="pc-gaps">${readiness.reasons.map((text, index) => `<li class="pc-gap" data-pc-readiness-reason="${index}">${pcEsc(text)}</li>`).join('')}</ul>`
        : '');
    const readinessGaps = Array.isArray((record.readiness || {}).gaps)
      ? record.readiness.gaps.filter(row => row && typeof row === 'object') : [];
    const gapRows = readinessGaps.length ? readinessGaps : gaps;
    // 严重度**只**取后端原文：闭集外的值归未知档（`pcGapSeverityLabel`），不许按"有没有 severity"
    // 猜成 blocking / advisory；两条缺口的分组顺序也保持后端给的顺序（前端不排序）。
    const gapSeverity = row => String((row && row.severity) || '').trim();
    const gapText = (row, key) => {
      const raw = (row && typeof row === 'object') ? row[key] : '';
      return String(raw === undefined || raw === null ? '' : raw).trim();
    };
    // 每条缺口：`data-pc-gap` = 码、`data-pc-gap-severity` = 严重度原文（Spec §C2）；
    // 正文 = `pcGapPrefix()` + detail + `pcGapSeverityLabel()` + `pcGapActionText()`，
    // 口径一律取自纯函数，这里只拼装。
    const gapRow = row => {
      const code = gapText(row, 'code');
      const severity = gapSeverity(row);
      const detail = gapText(row, 'detail') || code;
      const action = pcGapActionText(row);
      return `<div class="pc-gap" data-pc-gap="${pcEsc(code)}" data-pc-gap-severity="${pcEsc(severity)}">`
        + `${pcEsc(pcGapPrefix(code))}：${pcEsc(detail)} · ${pcEsc(pcGapSeverityLabel(severity))}`
        + `${action ? ` · ${pcEsc(action)}` : ''}</div>`;
    };
    const blockingGaps = gapRows.filter(row => gapSeverity(row) === 'blocking');
    const advisoryGaps = gapRows.filter(row => gapSeverity(row) !== 'blocking');
    const blockingGapBlock = blockingGaps.length
      ? `<h3>阻断缺口（挡住正式成本）</h3><div class="pc-gap-group" data-pc-gap-blocking="${blockingGaps.length}">${blockingGaps.map(gapRow).join('')}</div>`
      : '';
    const advisoryGapBlock = advisoryGaps.length
      ? `<h3>提示缺口（不影响正式/暂定）</h3><div class="pc-gap-group" data-pc-gap-advisory="${advisoryGaps.length}">${advisoryGaps.map(gapRow).join('')}</div>`
      : '';
    const summary = `<div class="pc-hint">材料 ${pcMoney(record.material_total)} · 加工 ${pcMoney(record.process_total)} · 人工 ${pcMoney(record.labor_total)} · 工装 ${pcMoney(record.tooling_total)} · 包装 ${pcMoney(record.packaging_total)} · 运输 ${pcMoney(record.freight_total)} · 其他 ${pcMoney(record.other_total)}</div>`;
    const totals = `<div class="pc-hint">小计（含损耗）${pcMoney(record.subtotal)} · 损耗 ${pcMoney(record.loss_amount)} · 总成本 ${pcMoney(record.total_cost)} ${pcEsc(record.currency || 'CNY')}</div>`;
    const head = built
      ? `<div class="pc-hint">引擎 ${pcEsc(record.engine_version || '—')} · 口径 ${pcEsc(record.cost_profile || '—')} · 数量 ${pcEsc(record.quote_quantity ?? '—')} · 场景 ${pcEsc(record.scenario_code || 'default')}</div>`
      : '<div class="pc-empty">还没有成本测算，点「重算成本」按已确认盒型 + BOM + 路线生成。</div>';
    const tables = built
      ? `<h3>成本类别</h3><table class="pc-table"><thead><tr><th>类别</th><th class="pc-num">金额</th></tr></thead><tbody>${pcCategoryRows(record)}</tbody></table>`
        + `<h3>报告分组</h3><table class="pc-table"><thead><tr><th>分组</th><th class="pc-num">金额</th></tr></thead><tbody>${pcGroupRows(record)}</tbody></table>`
        + `<h3>明细行</h3>${pcItemRows(items)}`
      : '';
    return `<section class="card section pc-panel" id="packagingCostPanel">
      <h2>包装成本测算</h2>
      <div class="pc-hint">逐部件 × 逐成本类别；缺料价 / 缺费率 / 缺工时一律出「待询价」缺口，合计不含该金额。本批只出成本，不出售价 / 利润（第 8 批）。</div>
      ${pcStaleBanner(record)}
      ${pcBomUnavailableBanner(record)}
      ${pcRouteUnavailableBanner(record)}
      ${pcBusinessPartsBanner(record)}
      ${pcRuleSnapshotBanner(record)}
      ${pcHandoffDriftBanner(handoff)}
      ${readinessBlock}
      ${pcContentBindingBlock(cost)}
      ${pcAssumptionsBlock(cost, items)}
      ${head}${summary}${totals}
      ${blockingGapBlock}
      ${advisoryGapBlock}
      <div class="pc-actions"><button class="btn primary" data-pc-build="1" ${writable ? '' : 'disabled'}>重算成本</button><button class="btn" data-pc-send-quote="1" ${built ? '' : 'disabled'}>回传销售继续报价</button></div>
      <div class="pc-hint">回传销售继续报价：把这份成本整包（行业 / 需求 / 盒型 / 参数 / BOM / 路线 / 成本 / 缺口 / 公式依据 / 来源）发回原报价卡片，卡片第 2 步的「包装：定价与报价分区」就带上它。有缺口时必须写明原因才放行（会随包留痕）；没有权限或落点认不回来时，会按后端给的原因提示。</div>
      ${writable ? '' : `<div class="pc-hint">${PC_WRITE_HINT}，当前为只读。</div>`}
      ${pcAuditBlock(handoff, pending, writable)}
      ${tables}
    </section>`;
  }

  async function pcRefresh() {
    const pid = pcProjectId();
    const host = document.querySelector('#packagingCostPanel');
    if (!pid || !host) return;
    const payload = await pcApi(`/api/projects/${encodeURIComponent(pid)}/requirement/packaging-cost`);
    const cost = (payload || {}).cost || {};
    const items = (cost.items || []);
    // 最近一次回传记录的输入漂移（Spec `packaging-handoff-input-drift-disclosure.md` §2.3）：
    // 读不到就不显示（不许把"读不到回传"说成"没回传过"）。
    let handoff = {};
    try {
      const sent = await pcApi(`/api/projects/${encodeURIComponent(pid)}/requirement/packaging-quote`);
      handoff = (sent || {}).handoff || {};
    } catch (error) { handoff = {}; }
    // 留痕欠条（Spec `packaging-handoff-audit-pending-panel.md` §C2）：
    // 读不到就置 null —— 读不到**不等于**「不欠」，界面上不许写成「还欠 0 条」。
    let pending = null;
    try {
      pending = await pcApi(pcAuditPendingPath(pid));
    } catch (error) { pending = null; }
    host.outerHTML = pcPanel(cost, items, pcCanWrite(), handoff, pending);
    pcBind(pid);
  }
  function pcBind(pid) {
    const build = document.querySelector('[data-pc-build]');
    if (build) build.onclick = () => pcSubmit(pid);
    const send = document.querySelector('[data-pc-send-quote]');
    if (send) send.onclick = () => pcSendQuote(pid);
    const relay = document.querySelector('[data-pc-audit-relay]');
    if (relay) relay.onclick = () => pcRelayAudits(pid);
  }
  async function pcSubmit(pid) {
    if (pcBusy) return;
    pcBusy = true;
    try {
      await pcApi(`/api/projects/${encodeURIComponent(pid)}/requirement/packaging-cost`, {method: 'POST', body: JSON.stringify({})});
      pcToast('包装成本已重算');
      pcBusy = false;
      await pcRefresh();
    } catch (error) {
      pcBusy = false;
      pcToast((error && error.message) || '包装成本操作失败', true);
    }
  }

  /* 包装整包回传（Spec docs/specs/packaging-quote-send-button-entry.md §2.1）：
     这条链以前**只有 HTTP 接口、没有按钮**，只点前端按钮的人永远走不到 ——
     卡片第 2 步的「包装：定价与报价分区」（唯一取数来源 snapshot.packaging_package）
     因此永远空着。这里补的就是那颗按钮。
     两类 409 是**可操作**的：缺口未清 / 落点认不回来。按后端给的 code 问一句原因再重发，
     不猜、不自动放行、不替用户写理由（Spec §2.3：缺口放行的口径一个字不改）。 */
  function pcPackagingSendPath(pid) {
    return `/api/projects/${encodeURIComponent(pid)}/requirement/packaging-quote/send`;
  }
  async function pcSendQuote(pid) {
    if (pcSendBusy) return;
    pcSendBusy = true;
    const body = {};
    try {
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          const payload = await pcApi(pcPackagingSendPath(pid), {method: 'POST', body: JSON.stringify(body)});
          const handoff = (payload || {}).handoff || {};
          /* 留痕没落下时，这句 toast 也必须说（Spec
             `packaging-handoff-audit-pending-panel.md` §C3）：回传成功 ≠ 留痕落下。 */
          const auditWarning = pcAuditWarning(handoff.audit);
          pcToast(`已回传销售：交接 ${handoff.handoff_no || '—'} · 第 ${handoff.version_no ?? '—'} 版`
            + (handoff.already_sent ? '（同一份成本，复用已有交接，没有重复发送）' : '')
            + '；报价卡片第 2 步的「包装：定价与报价分区」带上这份整包。'
            + (auditWarning ? ` ${auditWarning}` : ''), !!auditWarning);
          await pcRefresh();
          return;
        } catch (error) {
          const code = String((error && error.code) || '');
          if (code === 'cost_gaps_unresolved' && !body.allow_gaps) {
            const reason = window.prompt('成本仍有缺口，放行必须写明原因（会随包留痕）：');
            if (!reason || !reason.trim()) { pcToast('没有写明原因，已取消回传', true); return; }
            body.allow_gaps = true;
            body.reason = reason.trim();
            continue;
          }
          if (code === 'gap_reason_required' && !String(body.reason || '').trim()) {
            const reason = window.prompt('放行必须写明原因：');
            if (!reason || !reason.trim()) { pcToast('没有写明原因，已取消回传', true); return; }
            body.reason = reason.trim();
            continue;
          }
          if ((code === 'no_candidate' || code === 'multiple_candidates') && !body.create_new) {
            const reason = window.prompt(`${(error && error.message) || '这张卡片认不回来'} —— 要新建一张报价卡片，请写明原因：`);
            if (!reason || !reason.trim()) { pcToast('没有写明新建原因，已取消回传', true); return; }
            body.create_new = true;
            body.create_reason = reason.trim();
            continue;
          }
          throw error;
        }
      }
    } catch (error) {
      pcToast((error && error.message) || '包装回传失败', true);
    } finally {
      pcSendBusy = false;
    }
  }

  /* 补写留痕（Spec `packaging-handoff-audit-pending-panel.md` §C4）：把后端欠条里
     还欠的逐条补上 —— 成功划掉、失败留着。前端**不猜结果**，只把后端返回的五键
     披露体翻成一句话，然后重新读一次欠条（补完还剩几条，界面当次就对上）。 */
  async function pcRelayAudits(pid) {
    if (pcRelayBusy) return;
    pcRelayBusy = true;
    try {
      const payload = await pcApi(pcRelayAuditsPath(pid), {method: 'POST', body: JSON.stringify({})});
      pcToast(pcRelayText(payload) || '补写留痕已完成', false);
      await pcRefresh();
    } catch (error) {
      pcToast((error && error.message) || '补写留痕失败', true);
    } finally {
      pcRelayBusy = false;
    }
  }

  let pcMounting = false;
  let pcFailures = 0;
  async function pcMaybeMount() {
    if (pcMounting || pcFailures >= 2) return;
    const host = document.querySelector('#app .footer-actions');
    if (!host || document.querySelector('#packagingCostPanel')) return;
    const pid = pcProjectId();
    if (!pid) return;
    pcMounting = true;
    try {
      const requirement = await pcApi(`/api/projects/${encodeURIComponent(pid)}/requirement`);
      const industry = String((((requirement || {}).requirement || {}).data || {}).industry || '').trim();
      if (industry !== 'packaging') return;
      const placeholder = document.createElement('section');
      placeholder.id = 'packagingCostPanel';
      placeholder.className = 'card section pc-panel';
      placeholder.dataset.pending = '1';
      placeholder.innerHTML = '<h2>包装成本测算</h2><div class="pc-empty">正在读取成本测算…</div>';
      host.parentNode.insertBefore(placeholder, host);
      await pcRefresh();
    } catch (error) {
      pcFailures += 1;
      const pending = document.querySelector('#packagingCostPanel[data-pending="1"]');
      if (pending) pending.remove();
    } finally {
      pcMounting = false;
    }
  }

  window.CfPackagingCostPanel = {mount: pcMaybeMount, refresh: pcRefresh};
  const pcStart = () => { pcMaybeMount().catch(() => {}); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', pcStart);
  else pcStart();
  const pcApp = document.querySelector('#app');
  if (pcApp && typeof MutationObserver === 'function') {
    new MutationObserver(() => { if (!document.querySelector('#packagingCostPanel')) pcStart(); }).observe(pcApp, {childList: true});
  }
})();
