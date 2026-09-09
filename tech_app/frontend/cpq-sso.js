/*
 * 技术工艺 · 接入配置报价 CPQ 的统一登录。
 *
 * 后端见 backend/services/cpq_sso.py：身份来自 CPQ 的 /auth/*（角色只有销售经理 /
 * 工艺经理），技术工艺不再维护第二套账号。这个脚本负责前端这一半：
 *
 *   ① 令牌搬运。CPQ 的令牌存在 localStorage.cpq_auth_token，而技术工艺十几个页面
 *      各自读的是 authToken / cad_engine_token，且多数在脚本求值时就读走了。
 *      所以本文件必须是每页的**第一个** script：把 CPQ 令牌镜像过去，其余代码
 *      一行不用改就自动带上了正确的 Authorization。
 *   ② 未登录时挡住页面，并把人送去 CPQ 的登录框（不是技术工艺自己的 auth.html
 *      —— 那套在 SSO 模式下已经停用）。
 *   ③ 不是工艺经理时标成只读：顶部横幅说清楚，并拦下写请求。
 *      真正的权限判定始终在后端；这里只是别让人白点一遍再吃 403。
 *
 *      **注意别拦过头**：2.3 成本测算归财务经理（他在工艺侧确实只读）。
 *      这里原来只看 can_write（=工艺侧写权限），于是财务经理点「测算」时请求
 *      根本发不出去，前端自己伪造了一个 403，还带着"仅限工艺经理"的文案 ——
 *      后端权限改对了也没用。现在按**能力**分别放行：can_write 管工艺侧，
 *      can_cost 管成本相关接口。
 */
(function () {
  'use strict';

  var CPQ_TOKEN_KEY = 'cpq_auth_token';
  var MIRROR_KEYS = ['authToken', 'cad_engine_token'];
  var state = { enabled: false, user: null, canWrite: false, canCost: false,
                roleName: '', checked: false };

  /* 成本相关接口：2.3 本体，以及零件/整机成本与整合参数（都随成本搬去了 2.3）。
     只列**写**接口的路径特征，判定仍以后端 COST_ROLES 为准。 */
  var COST_URL_PATTERNS = [
    /\/cost-review(\/|$|\?)/,
    /\/parts\/[^/]+\/cost(\/|$|\?)/,
    /\/integration\/cost(\/|$|\?)/,
    /\/integration\/params\/(autofill|finalize)(\/|$|\?)/,
  ];
  function isCostUrl(url) {
    return COST_URL_PATTERNS.some(function (re) { return re.test(url); });
  }

  function ls(key) { try { return localStorage.getItem(key) || ''; } catch (e) { return ''; } }
  function setLs(key, value) {
    try { value ? localStorage.setItem(key, value) : localStorage.removeItem(key); } catch (e) {}
  }

  /** 把 CPQ 令牌镜像成技术工艺各页面在用的键。CPQ 登出后同步清掉，避免拿着过期票转圈。 */
  function mirrorToken() {
    var token = ls(CPQ_TOKEN_KEY);
    MIRROR_KEYS.forEach(function (key) {
      if (ls(key) !== token) setLs(key, token);
    });
    return token;
  }

  mirrorToken();          // 同步执行：必须早于任何读 authToken 的脚本

  // CPQ 那边登录/登出会写同一个 key。两条通知缺一不可：
  //   · storage —— 只在**别的**标签页触发（规范如此），管跨标签页同步；
  //   · cpq-auth-change —— cpq_auth.js 登录/登出成功后在本标签页派发的事件。
  // 早先只监听 storage，于是在本页弹出 CPQ 登录框、登录成功之后没有任何人收到通知，
  // 那张「请先登录」的遮罩就一直挂着。
  window.addEventListener('storage', function (event) {
    if (event.key === CPQ_TOKEN_KEY) onIdentityChanged();
  });
  document.addEventListener('cpq-auth-change', onIdentityChanged);

  /* 登录态变了就整页重载，而不是就地把遮罩摘掉。
     技术工艺有十几个页面在**脚本求值时**就把 authToken 读进了局部变量
     （app.js: `let authToken = readToken()` 等）。登录前那一刻它们读到的是空串，
     只摘遮罩的话页面看着能用，之后每个请求都不带 Authorization —— 比遮罩不消失更难查。
     重载后所有脚本重新读一遍镜像过的令牌，状态天然一致。 */
  function onIdentityChanged() {
    var before = ls(MIRROR_KEYS[0]);
    var after = mirrorToken();
    if (before === after) return;      // 没真的变（重复事件）就别白刷一次页面
    location.reload();
  }

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function injectCss() {
    if (document.getElementById('cpqSsoCss')) return;
    var style = document.createElement('style');
    style.id = 'cpqSsoCss';
    style.textContent = [
      '.cpq-sso-bar{position:sticky;top:0;z-index:9998;display:flex;align-items:center;gap:10px;',
      'padding:9px 16px;background:#fef3c7;color:#92400e;font-size:13px;line-height:1.6;',
      'border-bottom:1px solid #fde68a;}',
      '.cpq-sso-bar b{font-weight:650;}',
      '.cpq-sso-bar button{margin-left:auto;padding:5px 14px;border:none;border-radius:7px;',
      'background:#92400e;color:#fff;font-size:12.5px;cursor:pointer;}',
      '.cpq-sso-mask{position:fixed;inset:0;z-index:99998;display:flex;align-items:center;',
      'justify-content:center;background:rgba(15,23,42,.55);backdrop-filter:blur(3px);}',
      '.cpq-sso-card{width:400px;max-width:92vw;padding:26px 28px;border-radius:14px;background:#fff;',
      'color:#0f172a;box-shadow:0 24px 60px rgba(0,0,0,.28);font-size:14px;text-align:center;}',
      '.cpq-sso-card h3{margin:0 0 10px;font-size:17px;font-weight:650;}',
      '.cpq-sso-card p{margin:0 0 18px;color:#64748b;font-size:13px;line-height:1.75;}',
      '.cpq-sso-card button{padding:9px 22px;border:none;border-radius:8px;background:#0067D1;',
      'color:#fff;font-size:13px;font-weight:600;cursor:pointer;}',
      '.cpq-sso-toast{position:fixed;left:50%;bottom:32px;transform:translateX(-50%);z-index:99999;',
      'padding:11px 18px;border-radius:10px;background:#b91c1c;color:#fff;font-size:13px;',
      'box-shadow:0 8px 24px rgba(0,0,0,.2);max-width:82vw;}',
    ].join('');
    document.head.appendChild(style);
  }

  function toast(message) {
    injectCss();
    var existing = document.querySelector('.cpq-sso-toast');
    if (existing) existing.remove();
    var el = document.createElement('div');
    el.className = 'cpq-sso-toast';
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 4200);
  }

  /** 拉起 CPQ 的登录框。它由一体化服务在根路径提供，技术工艺页面同源，直接按需加载。 */
  function openCpqLogin() {
    if (window.cpqAuth && typeof window.cpqAuth.open === 'function') {
      window.cpqAuth.open();
      return;
    }
    var script = document.createElement('script');
    script.src = '/cpq_auth.js';
    script.onload = function () {
      // cpqAuth 自身要先拉一次登录态才知道该弹登录还是账号卡片。
      var open = function () { window.cpqAuth && window.cpqAuth.open(); };
      window.cpqAuth && window.cpqAuth.refresh ? window.cpqAuth.refresh().then(open, open) : open();
    };
    script.onerror = function () {
      toast('打不开 CPQ 登录框，请回到配置报价 CPQ 首页登录后再进入技术工艺。');
    };
    document.head.appendChild(script);
  }

  function showLoginWall(message) {
    injectCss();
    if (document.querySelector('.cpq-sso-mask')) return;
    var mask = document.createElement('div');
    mask.className = 'cpq-sso-mask';
    mask.innerHTML = '<div class="cpq-sso-card"><h3>请先登录</h3><p>' + esc(message) +
      '</p><button type="button">去登录</button></div>';
    mask.querySelector('button').onclick = openCpqLogin;
    document.body.appendChild(mask);
  }

  function showReadonlyBar() {
    injectCss();
    if (document.querySelector('.cpq-sso-bar')) return;
    var bar = document.createElement('div');
    bar.className = 'cpq-sso-bar';
    bar.innerHTML = '<span>当前以 <b>' + esc(state.roleName || '非工艺经理') +
      '</b> 身份登录，' + (state.canCost
        ? '你负责 <b>2.3 成本测算</b>：那一步可以测算、确认并对外发送；' +
          '2.1 图纸解析与 2.2 组装整合归工艺经理，这里是只读。'
        : '技术工艺为<b>只读</b>：可以查看项目与结果，' +
          '解析、生成、保存、确认、发布等操作仅限工艺经理。') + '</span>' +
      '<button type="button">切换账号</button>';
    bar.querySelector('button').onclick = openCpqLogin;
    document.body.insertBefore(bar, document.body.firstChild);
  }

  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }

  /* --------------------------------------------------------------------- *
   * 写请求拦截。后端才是权限的判定方（每个接口都有 _require），这里只是提前
   * 把结果告诉用户 —— 否则只读账号点「开始解析」会看着转半天再吃一个 403。
   * --------------------------------------------------------------------- */
  var nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    var isWrite = method !== 'GET' && method !== 'HEAD' && url.indexOf('/api/') !== -1;
    // 有对应能力就放行，让后端逐接口判定：拦截只是省一次往返，不是权限本身。
    var allowed = state.canWrite || (state.canCost && isCostUrl(url));
    if (isWrite && state.enabled && state.checked && !allowed) {
      var detail = state.canCost
        ? '这一步归工艺经理；' + (state.roleName || '当前账号') +
          '负责的是 2.3 成本测算，其余步骤只读'
        : '技术工艺的操作仅限工艺经理；' + (state.roleName || '当前账号') +
          '只能浏览，不能执行本操作';
      toast(detail);
      return Promise.resolve(new Response(JSON.stringify({ detail: detail }), {
        status: 403, headers: { 'Content-Type': 'application/json' },
      }));
    }
    return nativeFetch(input, init).then(function (response) {
      // 会话在使用中过期：立刻挡住页面，而不是让后续每个动作各报一次错。
      if (response.status === 401 && url.indexOf('/api/') !== -1 && state.enabled) {
        state.user = null; state.canWrite = false;
        MIRROR_KEYS.forEach(function (key) { setLs(key, ''); });
        ready(function () { showLoginWall('登录状态已失效，请在配置报价 CPQ 中重新登录。'); });
      }
      return response;
    });
  };

  /* --------------------------------------------------------------------- */
  function removeLoginWall() {
    var mask = document.querySelector('.cpq-sso-mask');
    if (mask) mask.remove();
  }

  function apply(data) {
    var sso = (data && data.sso) || {};
    state.enabled = !!sso.enabled;
    state.user = (data && data.user) || null;
    state.canWrite = !!sso.can_write;
    state.canCost = !!sso.can_cost;
    state.roleName = sso.role_name || '';
    state.checked = true;
    // 身份已确认：无论此前因为未登录还是登录服务故障挂了遮罩，这里都要收掉。
    // 正常路径靠重载走到这里，但 CpqSso.refresh() 也能被单独调用，不能只依赖重载。
    ready(removeLoginWall);
    if (!state.enabled) return;                    // 未开 SSO：保持原有本地行为
    if (!state.canWrite) ready(showReadonlyBar);
    document.dispatchEvent(new CustomEvent('cpq-sso-ready', { detail: state }));
  }

  function check() {
    var path = location.pathname;
    // auth.html 是技术工艺自带的登录页，SSO 模式下已停用，不在它上面再弹一层。
    if (path.indexOf('/auth.html') !== -1) return;
    nativeFetch('/api/me', { headers: { Authorization: 'Bearer ' + ls(CPQ_TOKEN_KEY) } })
      .then(function (response) {
        if (response.status === 401) {
          state.enabled = true; state.checked = true;
          ready(function () {
            showLoginWall('技术工艺已接入配置报价 CPQ 的统一登录，请以工艺经理身份登录后使用。');
          });
          return null;
        }
        if (response.status === 503) {
          state.checked = true;
          ready(function () {
            showLoginWall('登录服务暂不可用，无法校验身份。请检查配置报价 CPQ 的登录后端（数据库连接）。');
          });
          return null;
        }
        return response.json().catch(function () { return null; });
      })
      .then(function (data) { if (data) apply(data); })
      .catch(function () { /* 网络抖动不阻断页面；写请求仍由后端把关 */ });
  }

  window.CpqSso = {
    state: function () { return state; },
    token: function () { return ls(CPQ_TOKEN_KEY); },
    canWrite: function () { return !state.enabled || state.canWrite; },
    login: openCpqLogin,
    refresh: check,
  };

  check();
})();
