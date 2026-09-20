function confirmationQuestions(req){const d=req.data||{};const rows=[];if(!d.technical_requirements)rows.push(['技术要求是否完整？','当前需求未填写技术要求与约束，请确认是否可按现有图纸推进。']);if(!d.due_date)rows.push(['期望交付日期？','当前未设置完成日期，请补充项目计划。']);if(!d.customer_project)rows.push(['客户或项目归属？','未填写客户 / 项目名称，建议确认归属以便报告分发。']);return rows.length?rows:[['需求范围确认','系统完整性检查未发现必填缺失，请确认图纸、技术目标与评估范围无误。']];}
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
  let bmPid = '';
  let bmBusy = false;

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
        if (!res.ok) throw new Error(body.detail || body.message || `请求失败（${res.status}）`);
        return body;
      });
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
      bmToast((error && error.message) || '盒型匹配操作失败', true);
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
      try { const payload = await response.json(); detail = payload.detail || payload.message || ''; } catch (error) { detail = ''; }
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
  function pbRow(item, writable) {
    const status = String(item.status || 'computed');
    const missing = (item.missing_variables || []).length
      ? `<div class="pb-missing">缺失变量：${pbEsc(item.missing_variables.join('、'))}</div>` : '';
    const toggle = writable
      ? `<button class="btn secondary" data-pb-lock="${pbEsc(item.item_key)}" data-pb-locked="${status === 'locked' ? '1' : '0'}">${status === 'locked' ? '解锁' : '锁定'}</button>`
      : '';
    return `<li class="pb-item" data-status="${pbEsc(status)}">
      <div class="pb-item-head">
        <span class="pb-key">${pbEsc(item.item_key)}</span>
        <span class="pb-name">${pbEsc(item.item_name || '')}</span>
        <span class="pb-status">${pbEsc(PB_STATUS_LABELS[status] || status)}</span>
        ${toggle}
      </div>
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
    const groups = PB_ORDER.filter(category => items.some(item => item.bom_category === category));
    const body = groups.length
      ? groups.map(category => `<section class="pb-group">
          <h3>${pbEsc(PB_CATEGORY_LABELS[category] || category)}（${items.filter(item => item.bom_category === category).length}）</h3>
          <ul class="pb-list">${items.filter(item => item.bom_category === category).map(item => pbRow(item, writable)).join('')}</ul>
        </section>`).join('')
      : '<div class="pb-empty">还没有包装 BOM，先在盒型匹配里确认盒型，再点「展开部件并生成 BOM」。</div>';
    const unresolved = (gaps.material_unresolved || []).length
      ? `<div class="pb-hint">解析不到材料码（已在库外）：${pbEsc(gaps.material_unresolved.join('、'))}</div>` : '';
    return `<section class="card section pb-panel" id="packagingBomPanel">
      <h2>部件展开与包装 BOM${boxTypeCode ? `（${pbEsc(boxTypeCode)}）` : ''}</h2>
      <div class="pb-hint">按第 4 批确认的盒型参数化展开：共 ${pbEsc(stats.total || 0)} 行 · 已算出 ${pbEsc(stats.computed || 0)} · 缺输入 ${pbEsc(stats.needs_input || 0)} · 已锁定 ${pbEsc(stats.locked || 0)}。尺寸按公式求值，缺变量一律留空交工艺经理补。</div>
      ${unresolved}
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
