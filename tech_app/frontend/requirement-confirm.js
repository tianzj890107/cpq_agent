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
    const unresolved = (gaps.material_unresolved || []).length
      ? `<div class="pb-hint">解析不到材料码（已在库外）：${pbEsc(gaps.material_unresolved.join('、'))}</div>` : '';
    return `<section class="card section pb-panel" id="packagingBomPanel">
      <h2>部件展开与包装 BOM${boxTypeCode ? `（${pbEsc(boxTypeCode)}）` : ''}</h2>
      <div class="pb-hint">按第 4 批确认的盒型参数化展开：共 ${pbEsc(stats.total || 0)} 行 · 已算出 ${pbEsc(stats.computed || 0)} · 缺输入 ${pbEsc(stats.needs_input || 0)} · 已锁定 ${pbEsc(stats.locked || 0)}。尺寸按公式求值，缺变量一律留空交工艺经理补。`
      + `${partsHash ? `零件文档版本：${pbEsc(partsHash.slice(0, 12))}。` : ''}</div>
      ${unresolved}
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
  function pcGapLine(gap) {
    const detail = gap.detail || gap.code || '';
    return `<div class="pc-gap">待询价：${pcEsc(detail)}</div>`;
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
    // 权威清单重新导入 = 这份成本照的那一版业务部件清单已经换过（Spec
    // `packaging-business-parts-version-pinning.md` §2.4）：不许把码直接甩给用户。
    business_parts_reimported: '按上一版业务部件清单建的（权威清单重新导入过，请重建 BOM 后重算成本）',
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
  function pcPanel(cost, items, writable, handoff) {
    const record = cost || {};
    const built = !!record.built;
    const gaps = record.gaps || [];
    const gapLines = gaps.map(pcGapLine).join('');
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
      ${pcRuleSnapshotBanner(record)}
      ${pcHandoffDriftBanner(handoff)}
      ${head}${summary}${totals}
      ${gapLines}
      <div class="pc-actions"><button class="btn primary" data-pc-build="1" ${writable ? '' : 'disabled'}>重算成本</button><button class="btn" data-pc-send-quote="1" ${built ? '' : 'disabled'}>回传销售继续报价</button></div>
      <div class="pc-hint">回传销售继续报价：把这份成本整包（行业 / 需求 / 盒型 / 参数 / BOM / 路线 / 成本 / 缺口 / 公式依据 / 来源）发回原报价卡片，卡片第 2 步的「包装：定价与报价分区」就带上它。有缺口时必须写明原因才放行（会随包留痕）；没有权限或落点认不回来时，会按后端给的原因提示。</div>
      ${writable ? '' : `<div class="pc-hint">${PC_WRITE_HINT}，当前为只读。</div>`}
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
    host.outerHTML = pcPanel(cost, items, pcCanWrite(), handoff);
    pcBind(pid);
  }
  function pcBind(pid) {
    const build = document.querySelector('[data-pc-build]');
    if (build) build.onclick = () => pcSubmit(pid);
    const send = document.querySelector('[data-pc-send-quote]');
    if (send) send.onclick = () => pcSendQuote(pid);
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
          pcToast(`已回传销售：交接 ${handoff.handoff_no || '—'} · 第 ${handoff.version_no ?? '—'} 版`
            + (handoff.already_sent ? '（同一份成本，复用已有交接，没有重复发送）' : '')
            + '；报价卡片第 2 步的「包装：定价与报价分区」带上这份整包。');
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
