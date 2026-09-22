/* Shared client for the ten enterprise workflow pages. No action here calls an LLM. */
const qp = new URLSearchParams(location.search);
// 所有流程页共用的可展开、可校验导航；按需加载，不影响旧页面的业务逻辑。
if (!document.querySelector('script[data-workflow-navigation]')) {
  const navCss = document.createElement('link');
  navCss.rel = 'stylesheet'; navCss.href = '/workflow-navigation.css?v=workflow-nav4';
  document.head.appendChild(navCss);
  const navScript = document.createElement('script');
  navScript.src = '/workflow-navigation.js?v=workflow-nav6'; navScript.dataset.workflowNavigation = '1';
  document.head.appendChild(navScript);
}
if (!document.querySelector('script[data-session-guard]')) {
  const sessionGuard = document.createElement('script');
  sessionGuard.src = '/session-guard.js?v=session1'; sessionGuard.dataset.sessionGuard = '1';
  document.head.appendChild(sessionGuard);
}
// 唯一来源：共享模块（URL project / 父壳 data-project）——模块在就完全以它为准（不覆盖判定）；
// 只有模块未加载的旧页（tech-task.html 不在本批 11 个页面里）才退到「只认 URL」。
// 任何情况下都不读 localStorage 里的上一次项目。
const projectId = (typeof TechProjectContext === 'undefined')
  ? (qp.get('project') || '')
  : TechProjectContext.bind().project;
const authHeaders = () => {
  const token = localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token');
  return token ? {Authorization: `Bearer ${token}`} : {};
};
/* FastAPI 的 422 里 detail 是一个**数组**（每个不合法字段一条），直接塞进 Error
   会变成 "[object Object]" —— 屏幕上只剩"创建失败"四个字，谁也不知道差了哪个字段。
   这里把字段名和原因摊开。其余状态码的 detail 是字符串，原样用。 */
function apiError(data, status) {
  const detail = data && data.detail;
  if (Array.isArray(detail)) {
    const lines = detail.map(item => {
      const field = (item.loc || []).filter(x => x !== 'body').join('.');
      return field ? `${field}: ${item.msg}` : item.msg;
    }).filter(Boolean);
    if (lines.length) return `提交的数据不合法（${lines.join('；')}）`;
  }
  if (typeof detail === 'string' && detail) return detail;
  /* 包装下游的业务错误统一回结构化 detail `{code, message}`（Spec
     docs/specs/packaging-downstream-block-code-parity.md §2.2）：只认字符串的话
     人话会被吞掉，屏幕上只剩"请求失败 (409)"。code 由 api() 提到 error 上。 */
  if (detail && typeof detail === 'object' && typeof detail.message === 'string' && detail.message) {
    return detail.message;
  }
  return (data && data.message) || `请求失败 (${status})`;
}
/* 写请求前的项目身份一致性校验（项目身份唯一来源 = tech-project-context.js）。
   规则：PUT/POST/DELETE/PATCH 且 URL 指向 /api/projects/<id>/… 时，<id> 必须等于本页解析出的项目。
   · 本页身份为空（1.1 新建草稿、2.3 待办恢复等「还没有 project 但确实要写」的合法链路）→ 放行；
   · 非项目级 URL（如 POST /api/projects 建项）→ 放行；
   · 身份非空且与目标不一致 → 不发请求，只提示（防「拿着 A 的身份把写入发给 B 的项目」）。
   返回空串 = 放行，返回文案 = 拒绝并提示。 */
function projectWriteGuard(url, options) {
  const method = String((options && options.method) || 'GET').toUpperCase();
  if (method === 'GET' || method === 'HEAD' || method === 'OPTIONS') return '';
  const match = String(url || '').match(/\/api\/projects\/([^/?#]+)(?:[/?#]|$)/);
  if (!match) return '';
  let target = match[1];
  try { target = decodeURIComponent(target); } catch (error) { /* 非法转义按原样比对 */ }
  let current = '';
  try {
    current = (typeof TechProjectContext !== 'undefined' && TechProjectContext.current().project) || '';
  } catch (error) { current = ''; }
  if (!current || current === target) return '';
  return `本页的项目是 ${current}，不能把这次写入发给 ${target}；已拒绝发送。请从统一工作台重新进入该项目。`;
}
async function api(url, options) {
  options = options || {};
  const blocked = projectWriteGuard(url, options);
  if (blocked) { toast(blocked); throw new Error(blocked); }
  const headers = {...authHeaders(), ...(options.headers || {})};
  if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const res = await fetch(url, {...options, headers});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    /* 结构化字段必须活到调用方：落点冲突时服务端回的是 409 + {code, candidates, message}，
       只抛 new Error(文案) 的话，界面读到的 error.code 恒为 undefined，
       「认回哪张卡片 / 明确新建」这两个出口就永远弹不出来。 */
    const err = new Error(apiError(data, res.status));
    const detail = (data && data.detail) || {};
    err.status = res.status;
    err.code = (detail && detail.code) || (data && data.code) || '';
    err.candidates = (detail && detail.candidates) || (data && data.candidates) || [];
    throw err;
  }
  return data;
}
function esc(value) { return String(value ?? '').replace(/[&<>'"]/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[s])); }
function text(value) { return esc(value).replace(/\n/g, '<br>'); }
function toast(message, ms = 2600) { const el = document.createElement('div'); el.className='toast'; el.textContent=message; document.body.append(el); setTimeout(()=>el.remove(),ms); }
function href(page, id = projectId) { return `${page}${id ? `?project=${encodeURIComponent(id)}` : ''}`; }
function statusLabel(status) { return ({draft:'草稿',pending_confirmation:'待确认',pending_review:'待审核',approved:'已通过',rejected:'已退回',in_review:'审核中',published:'已发布'})[status] || status || '未创建'; }
function statusClass(status) { return ['approved','published'].includes(status) ? 'green' : ['rejected'].includes(status) ? 'orange' : status === 'draft' ? 'gray' : ''; }
/* 【5 阶段 × 13 子步骤】技术工艺流程栏的唯一口径（与 tech-workbench.js 的 STAGES /
   backend/services/workflow_stages.py 同源）。本页面的调用方仍按老的 active 1–3 与子步骤号
   传入（requirement-*.js / report-workflow.js 不在本批改动范围），下面只做口径换算。 */
const WORKFLOW_PHASES = [
  { no: 1, title: '工艺评估需求', subs: [['1.1', '创建'], ['1.2', '确认'], ['1.3', '审核']] },
  { no: 2, title: '图纸解析', subs: [['2.1', '图纸解析']] },
  { no: 3, title: '组装与整合', subs: [['3.1', '整合图纸'], ['3.2', '参数推荐'], ['3.3', '组装工艺']] },
  { no: 4, title: '成本测算', subs: [['4.1', '零件成本'], ['4.2', '组装成本'], ['4.3', '汇总']] },
  { no: 5, title: '工艺评估报告', subs: [['5.1', '汇总结果'], ['5.2', '结果审核'], ['5.3', '发布并回传报价']] },
];
const WORKFLOW_LEGACY_PHASE = { 1: 1, 2: 2, 3: 5 };
const WORKFLOW_LEGACY_SUB = { '1.1': '1.1', '1.2': '1.2', '1.3': '1.3', '2.1': '2.1',
                              '3.1': '5.1', '3.2': '5.2', '3.3': '5.3' };
function workflow(active, sub = '') {
  const phase = WORKFLOW_LEGACY_PHASE[active] || 1;
  const current = WORKFLOW_LEGACY_SUB[sub] || '';
  const c = (on, done) => done ? 'done' : on ? 'active' : 'pending';
  const groups = WORKFLOW_PHASES.map((row, index) => {
    const links = row.subs.map(([no, label]) =>
      `<span class="${current === no ? 'active' : ''}">${no} ${label}</span>`).join(' → ');
    return `${index ? `<i class="workflow-connector ${row.no <= phase ? 'active' : ''}"></i>` : ''}`
      + `<div class="workflow-group"><div class="workflow-step ${c(row.no === phase, row.no < phase)}"><i>${row.no}</i>${row.title}</div>`
      + `<div class="workflow-sub">${links}</div></div>`;
  }).join('');
  return `<section class="workflow card"><div class="workflow-main">${groups}</div></section>`;
}
function header() { return `<header class="topbar"><a class="brand" href="${href('home.html','')}"><span class="brand-mark">AI</span><span>AI 工艺平台</span></a><div class="top-links"><a href="${href('home.html','')}">工作台</a><a href="${href('requirement-detail.html')}">需求详情</a><a href="${href('index.html')}">图纸解析</a><span id="userName">加载中…</span><span class="avatar" id="userAvatar">AI</span></div></header>`; }
async function loadMe(){ try { const d=await api('/api/me'); const u=d.user||{}; const n=u.display_name||u.username||'系统'; const el=document.querySelector('#userName'), av=document.querySelector('#userAvatar'); if(el)el.textContent=`${n} · ${u.role||''}`; if(av)av.textContent=n.slice(0,1); return u;}catch{return {};}}
function renderHistory(history) { if (!history?.length) return '<div class="empty">尚无流程留痕</div>'; return `<ul class="history">${history.slice().reverse().map(x=>`<li><time>${esc(x.at)}</time><div><b>${esc(x.action)}</b><div>${esc(x.actor)} ${x.role?`· ${esc(x.role)}`:''}${x.comment?`：${esc(x.comment)}`:''}</div></div></li>`).join('')}</ul>`; }
function setProject(id) { if (id) { localStorage.setItem('cad_engine_project_id', id); localStorage.setItem('currentProject', id); } }
