/* 【CPQ 定制 · 本文件不来自 process_drawing，同步上游时保留】
 *
 * 把报价助手的「待办任务」与「消息」搬进技术工艺首页。
 *
 * 工艺经理的活分两头：报价那边转过来的任务（工艺确认 / 新增工艺）在报价首页，
 * 技术工艺自己的项目清单在这边。两个首页来回跳才能知道有没有新活，是个纯粹的浪费。
 * 现在：
 *   · 清单区多一个「待办任务」页签，读 /wf/tasks —— 与报价首页同一个接口、同一批数据；
 *   · 左侧栏多一个消息铃铛，复用一体化服务的 cpq_msg.js（未读红点、轮询、右下角提醒
 *     全都是现成的，不重写一份）。
 *
 * 实现方式与 cpq-home-tabs.js 一致：包住 home.js 的 renderHome / renderCards，
 * 不改上游文件。两个包装器各自捕获前一个，可以叠加。
 *
 * 依赖 /cpq_auth.js 与 /cpq_msg.js（由 8010 一体化服务提供）。单独跑 8012 时它们
 * 取不到，本文件的所有入口都会安静地不出现 —— 技术工艺自己的功能不受影响。
 */
(function () {
  'use strict';

  const QUOTE_PAGE = '/' + encodeURIComponent('确认需求解析结果.html');
  const POLL_MS = 30000;
  let tasks = [];
  let loaded = false;
  // 自己记「当前是不是待办任务页签」，不借用 home.js 的 activeTab：
  // 那是经典脚本里的顶层 let，只存在于脚本作用域，window 上根本读不到也改不了。
  let showingTasks = false;

  const esc = v => String(v == null ? '' : v).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function cpqApi(path, options) {
    if (!window.cpqAuth) return Promise.reject(new Error('未加载登录模块'));
    return window.cpqAuth.api(path, options);
  }

  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso).replace('T', ' ').slice(0, 16);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
    if (diff < 604800) return Math.floor(diff / 86400) + ' 天前';
    const p = n => (n < 10 ? '0' : '') + n;
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function injectCss() {
    if (document.getElementById('cpqInboxCss')) return;
    const style = document.createElement('style');
    style.id = 'cpqInboxCss';
    style.textContent = [
      '.cpq-inbox-card{cursor:pointer}',
      '.cpq-inbox-card.technew{border-left:3px solid #1d4ed8}',
      '.cpq-inbox-meta{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px}',
      '.cpq-inbox-chip{padding:2px 9px;border-radius:999px;background:var(--bg-page,#f3f4f6);',
      'color:var(--text-secondary,#64748b);font-size:11.5px}',
      '.cpq-inbox-chip.no{background:#1d4ed8;color:#fff;font-weight:700;letter-spacing:.03em}',
      '.cpq-inbox-note{margin-top:7px;padding:7px 10px;border-radius:8px;',
      'background:var(--bg-page,#f8fafc);color:var(--text-secondary,#64748b);font-size:12px}',
      '.cpq-tab-badge{margin-left:6px;padding:0 6px;border-radius:999px;background:#ef4444;',
      'color:#fff;font-size:11px;font-weight:700;line-height:16px;display:inline-block}',
      // 铃铛与账号按钮借用侧栏图标的样式，只补一个相对定位给未读红点用
      '#cpqMsgBtn,#cpqAuthBtn{position:relative}',
    ].join('');
    document.head.appendChild(style);
  }

  /* ------------------------------------------------------------ 数据 */
  async function loadTasks(quiet) {
    if (!window.cpqAuth || !window.cpqAuth.user()) {
      tasks = [];
      loaded = true;
      if (!quiet) refreshUi();
      return;
    }
    try {
      const data = await cpqApi('/wf/tasks');
      tasks = data.tasks || [];
    } catch (e) {
      tasks = [];
    }
    loaded = true;
    refreshUi();
  }

  function pendingCount() {
    return tasks.filter(t => t.status === 'open').length;
  }

  function refreshUi() {
    syncBadge();
    if (showingTasks) renderTaskCards();
  }

  function syncBadge() {
    const tab = document.querySelector('.list-tab[data-list="tasks"]');
    if (!tab) return;
    const n = pendingCount();
    tab.innerHTML = '待办任务' + (n ? `<span class="cpq-tab-badge">${n}</span>` : '');
  }

  /* ------------------------------------------------------------ 渲染 */
  function taskCard(t) {
    const claimed = t.status === 'claimed';
    const techNew = t.task_kind === 'tech_new_product';
    // 支线任务用任务类型本身作标题，比"定向角色"有信息量得多。
    const side = { tech_new_product: '新增工艺', tech_cost: '成本测算',
                   tech_cost_return: '成本结果复核' }[t.task_kind];
    const way = side || (t.target_type === 'public' ? '公共任务'
      : t.target_type === 'role' ? '定向角色' : '指派给我');
    return `<article class="request-card cpq-inbox-card${techNew ? ' technew' : ''}" data-inbox-task="${esc(t.task_id)}">
      <div class="request-card-header"><span class="request-id">${esc(way)}</span>
        <span class="request-status ${claimed ? 'status-reviewing' : 'status-draft'}">${claimed ? '进行中' : '待领取'}</span></div>
      <div class="request-title" title="${esc(t.title || '')}">${esc(t.title || '未命名报价')}</div>
      <div class="cpq-inbox-meta">
        ${side && t.task_no ? `<span class="cpq-inbox-chip no">${esc(t.task_no)}</span>` : ''}
        <span class="cpq-inbox-chip">${esc(t.next_step_name || '')}</span>
        <span class="cpq-inbox-chip">客户 ${esc(t.customer || '—')}</span>
        <span class="cpq-inbox-chip">${esc(fmtTime(t.created_at))}</span>
        <span class="cpq-inbox-chip">来自 ${esc(t.from_display_name || '—')}（${esc(t.from_role_name || '')}）</span>
      </div>
      ${t.note ? `<div class="cpq-inbox-note">“${esc(t.note)}”</div>` : ''}
      <div class="request-footer"><div class="request-creator">
        <span class="creator-avatar">${esc(String(t.from_display_name || '?').charAt(0))}</span>
        <span>${esc(t.next_role_name || '')}负责</span></div>
        <span class="request-detail">${claimed ? '继续处理' : (techNew ? '领取并开始' : '领取并去报价')}</span>
      </div></article>`;
  }

  function renderTaskCards() {
    const grid = document.querySelector('#cardGrid');
    const info = document.querySelector('#paginationInfo');
    const controls = document.querySelector('#paginationControls');
    if (!grid) return;
    if (controls) controls.innerHTML = '';
    if (!window.cpqAuth || !window.cpqAuth.user()) {
      grid.innerHTML = '<div class="empty-grid">登录后可查看报价那边转交给你的任务。</div>';
      if (info) info.textContent = '';
      return;
    }
    if (!loaded) {
      grid.innerHTML = '<div class="empty-grid">正在读取待办任务…</div>';
      return;
    }
    grid.innerHTML = tasks.length ? tasks.map(taskCard).join('')
      : '<div class="empty-grid">暂时没有分派给你的任务。</div>';
    if (info) info.textContent = tasks.length ? `共 ${tasks.length} 条待办` : '';
    grid.querySelectorAll('[data-inbox-task]').forEach(el => {
      el.onclick = () => openTask(tasks.find(t => String(t.task_id) === el.dataset.inboxTask));
    });
  }

  /** 领取并跳转。新增工艺去技术工艺自己的任务页，工艺确认回报价工作台。 */
  async function openTask(task) {
    if (!task) return;
    let claim = null;
    try {
      if (task.status !== 'claimed') {
        claim = await cpqApi('/wf/task/claim', { method: 'POST', body: { task_id: task.task_id } });
      }
      if (window.cpqMsg) window.cpqMsg.ping();
    } catch (e) {
      if (window.homeToast) homeToast(e.message || '领取失败', true);
      else alert(e.message || '领取失败');
      return;
    }
    const kind = (claim && claim.task_kind) || task.task_kind;
    if (kind === 'tech_new_product') {
      location.href = '/tech-task.html?tech_task=' + encodeURIComponent(task.task_id);
      return;
    }
    // 成本测算（财务经理）→ 2.3；成本结果复核（工艺经理）→ 2.2 改工序/用量。
    // 这两条支线的 session_id 就是技术工艺的项目号。
    if (kind === 'tech_cost' || kind === 'tech_cost_return') {
      const page = kind === 'tech_cost' ? '/cost-review.html' : '/assembly-integration.html';
      location.href = page + '?project=' + encodeURIComponent(task.session_id || '');
      return;
    }
    // 工艺确认这一步是在报价工作台里做的（回显第 1 步需求 + 产品推荐表），不在技术工艺。
    try { sessionStorage.setItem('cpq:openSession', task.session_id || ''); } catch (e) {}
    location.href = QUOTE_PAGE;
  }

  /* ------------------------------------------------------------ 挂到首页 */
  function mountTab() {
    const tabs = document.querySelector('.list-tabs');
    if (!tabs || tabs.querySelector('[data-list="tasks"]')) return;
    const tab = document.createElement('button');
    tab.className = 'list-tab';
    tab.dataset.list = 'tasks';
    tab.textContent = '待办任务';
    tabs.appendChild(tab);
    // home.js 的 bindHome 已经给 .list-tab 绑过 switchHomeView，但那是在本标签存在之前，
    // 所以这一个要自己绑：同步高亮、画任务列表。
    tab.onclick = () => {
      document.querySelectorAll('.list-tab').forEach(x =>
        x.classList.toggle('active', x.dataset.list === 'tasks'));
      document.querySelectorAll('[data-home-view]').forEach(x => x.classList.remove('active'));
      renderTaskCards();
      if (!loaded) loadTasks();
    };
    syncBadge();
  }

  /* 页签状态在**捕获阶段**先定下来：上游那两类按钮的 onclick 里会调 renderCards()，
     等到那时再判断就晚了 —— 会拿着上一次的状态把清单画成任务、或反过来。 */
  function trackTabClicks() {
    if (document.body.dataset.cpqInboxTracking) return;
    document.body.dataset.cpqInboxTracking = '1';
    document.addEventListener('click', event => {
      const tab = event.target.closest && event.target.closest('.list-tab[data-list]');
      if (tab) { showingTasks = tab.dataset.list === 'tasks'; return; }
      // 侧栏的「我的清单 / 全部清单」同样会切回项目清单
      if (event.target.closest && event.target.closest('[data-home-view]')) showingTasks = false;
    }, true);
  }

  /* 消息与登录两格**不再由这里注入** —— home.js 的 renderHome() 已经按报价助手那份
     markup 原样渲染了它们（消息 / 设置 / 分隔线 / 登录）。两边都建的结果是侧栏底部
     排了四格：这里插的「消息 / 登录」+ 首页自己的「模型设置 / 用户设置」。
     留一层守卫：万一 home.js 那份没渲染出来（旧缓存），至少把消息补回去。 */
  function mountNavButtons() {
    const rail = document.querySelector('.cpq-nav-bottom-icons');
    if (!rail || document.getElementById('cpqMsgBtn')) return;
    const bell = document.createElement('div');
    bell.className = 'cpq-nav-icon';
    bell.id = 'cpqMsgBtn';
    bell.setAttribute('role', 'button');
    bell.tabIndex = 0;
    bell.innerHTML = '<i class="ti ti-bell"></i><span class="nav-tooltip">消息</span>';
    bell.onclick = () => (window.cpqMsg ? window.cpqMsg.open()
      : (window.homeToast && homeToast('消息模块未加载（需经 8010 一体化服务访问）', true)));
    rail.insertBefore(bell, rail.firstChild);
  }

  function mount() {
    injectCss();
    trackTabClicks();
    mountTab();
    mountNavButtons();
    // renderHome 会重建 DOM：如果切换前停在待办任务上，重画后要把它恢复回来。
    if (showingTasks) {
      document.querySelectorAll('.list-tab').forEach(x =>
        x.classList.toggle('active', x.dataset.list === 'tasks'));
      renderTaskCards();
    }
  }

  /* ------------------------------------------------------------ 包装上游 */
  function wrap() {
    if (typeof window.renderHome === 'function') {
      const base = window.renderHome;
      window.renderHome = function () {
        base.apply(this, arguments);
        mount();
      };
    }
    // 清单区停在「待办任务」时，上游的 renderCards 会按项目清单重画，把任务列表冲掉。
    if (typeof window.renderCards === 'function') {
      const base = window.renderCards;
      window.renderCards = function () {
        if (showingTasks) { renderTaskCards(); return; }
        return base.apply(this, arguments);
      };
    }
  }

  // home.js 末尾的 startHome() 已经同步渲染过一轮，所以先挂一次；
  // 之后 renderHome 再重渲染时由 wrap() 里的包装补上（mount 幂等）。
  wrap();
  mount();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);

  // 登录态就绪/变化后再拉一次：未登录时列表是空的，登录完要立刻有内容。
  document.addEventListener('cpq-auth-ready', () => loadTasks());
  document.addEventListener('cpq-auth-change', () => { loaded = false; loadTasks(); });
  setInterval(() => { if (window.cpqAuth && window.cpqAuth.user()) loadTasks(true); }, POLL_MS);
  loadTasks(true);
})();
