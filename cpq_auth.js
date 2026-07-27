/* 配置报价CPQ —— 登录 / 注册 / 账号信息（四个页面共用）
 *
 * 用法：页面里引入 <script src="cpq_auth.js"></script>，并把左下角原「深色模式」按钮换成
 *   <div class="nav-icon" id="cpqAuthBtn" onclick="cpqAuth.open()">
 *     <i class="ti ti-user"></i><span class="nav-tooltip">登录</span>
 *   </div>
 * 脚本会自动接管该按钮的图标/文案，并按登录态弹出「登录/注册」或「账号信息」。
 *
 * 令牌存 localStorage 并以 Authorization: Bearer 发送——用 header 而非 Cookie，
 * 不受 SameSite 限制，跨源/内嵌场景同样可用。
 */
(function () {
  'use strict';

  var TOKEN_KEY = 'cpq_auth_token';
  var state = { user: null, roles: [], loaded: false };

  function token() { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; } }
  function setToken(t) {
    try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch (e) {}
  }

  function api(path, opts) {
    opts = opts || {};
    var headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    var t = token();
    if (t) headers['Authorization'] = 'Bearer ' + t;
    return fetch(path, {
      method: opts.method || 'GET', headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || ('请求失败（' + r.status + '）'));
        return d;
      });
    });
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ---------------- 样式（自带，不依赖各页面 CSS 变量之外的东西） ---------------- */
  var CSS = [
    '.cpq-auth-mask{position:fixed;inset:0;background:rgba(15,23,42,.45);backdrop-filter:blur(2px);',
    'display:flex;align-items:center;justify-content:center;z-index:99999;}',
    '.cpq-auth-box{width:380px;max-width:92vw;background:var(--bg-primary,#fff);color:var(--text-primary,#0f172a);',
    'border-radius:14px;box-shadow:0 24px 60px rgba(0,0,0,.28);overflow:hidden;font-size:14px;}',
    '.cpq-auth-hd{padding:18px 22px 0;}',
    '.cpq-auth-hd h3{margin:0;font-size:17px;font-weight:650;}',
    '.cpq-auth-hd p{margin:6px 0 0;font-size:12px;color:var(--text-secondary,#64748b);}',
    '.cpq-auth-tabs{display:flex;gap:18px;padding:14px 22px 0;border-bottom:1px solid var(--border-color,#e2e8f0);}',
    '.cpq-auth-tab{padding:8px 0 10px;cursor:pointer;font-size:13px;color:var(--text-secondary,#64748b);',
    'border-bottom:2px solid transparent;margin-bottom:-1px;}',
    '.cpq-auth-tab.on{color:var(--color-primary,#6366f1);border-bottom-color:var(--color-primary,#6366f1);font-weight:600;}',
    '.cpq-auth-bd{padding:16px 22px 4px;}',
    '.cpq-auth-f{margin-bottom:12px;}',
    '.cpq-auth-f label{display:block;font-size:12px;color:var(--text-secondary,#64748b);margin-bottom:5px;}',
    '.cpq-auth-f input,.cpq-auth-f select{width:100%;box-sizing:border-box;padding:9px 11px;font-size:13px;',
    'border:1px solid var(--border-color,#e2e8f0);border-radius:8px;background:var(--bg-primary,#fff);',
    'color:var(--text-primary,#0f172a);outline:none;}',
    '.cpq-auth-f input:focus,.cpq-auth-f select:focus{border-color:var(--color-primary,#6366f1);}',
    '.cpq-auth-msg{min-height:18px;padding:0 22px;font-size:12px;color:#dc2626;}',
    '.cpq-auth-msg.ok{color:#16a34a;}',
    '.cpq-auth-ft{display:flex;gap:10px;padding:10px 22px 20px;}',
    '.cpq-auth-btn{flex:1;padding:9px 0;border:none;border-radius:8px;font-size:13px;cursor:pointer;font-weight:600;}',
    '.cpq-auth-btn.primary{background:var(--color-primary,#6366f1);color:#fff;}',
    '.cpq-auth-btn.primary:disabled{opacity:.6;cursor:default;}',
    '.cpq-auth-btn.ghost{background:var(--bg-tertiary,#f1f5f9);color:var(--text-secondary,#64748b);}',
    '.cpq-auth-card{display:flex;align-items:center;gap:12px;padding:4px 0 12px;}',
    '.cpq-auth-avatar{width:44px;height:44px;border-radius:50%;flex:0 0 44px;display:flex;align-items:center;',
    'justify-content:center;font-size:17px;font-weight:650;color:#fff;background:var(--color-primary,#6366f1);}',
    '.cpq-auth-name{font-size:15px;font-weight:650;}',
    '.cpq-auth-role{display:inline-block;margin-top:4px;padding:2px 8px;border-radius:20px;font-size:11px;',
    'background:rgba(99,102,241,.12);color:var(--color-primary,#6366f1);}',
    '.cpq-auth-kv{border-top:1px solid var(--border-color,#e2e8f0);padding-top:10px;}',
    '.cpq-auth-kv div{display:flex;justify-content:space-between;padding:5px 0;font-size:12px;}',
    '.cpq-auth-kv span:first-child{color:var(--text-secondary,#64748b);}',
  ].join('');

  function injectCss() {
    if (document.getElementById('cpqAuthCss')) return;
    var st = document.createElement('style');
    st.id = 'cpqAuthCss';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  /* ---------------- 左下角按钮：按登录态显示 ---------------- */
  function syncButton() {
    var btn = document.getElementById('cpqAuthBtn');
    if (!btn) return;
    var icon = btn.querySelector('.ti');
    var tip = btn.querySelector('.nav-tooltip');
    if (state.user) {
      if (icon) icon.className = 'ti ti-user-check';
      if (tip) tip.textContent = state.user.display_name + '（' + state.user.role_name + '）';
      btn.classList.add('active');
    } else {
      if (icon) icon.className = 'ti ti-login';
      if (tip) tip.textContent = '登录';
      btn.classList.remove('active');
    }
  }

  /* ---------------- 弹窗 ---------------- */
  function close() {
    var m = document.getElementById('cpqAuthMask');
    if (m) m.remove();
  }

  function mask(inner) {
    close();
    injectCss();
    var m = document.createElement('div');
    m.className = 'cpq-auth-mask';
    m.id = 'cpqAuthMask';
    m.innerHTML = '<div class="cpq-auth-box">' + inner + '</div>';
    m.addEventListener('mousedown', function (e) { if (e.target === m) close(); });
    document.body.appendChild(m);
    return m;
  }

  function msg(text, ok) {
    var el = document.getElementById('cpqAuthMsg');
    if (el) { el.textContent = text || ''; el.className = 'cpq-auth-msg' + (ok ? ' ok' : ''); }
  }

  function roleOptions(sel) {
    return state.roles.map(function (r) {
      return '<option value="' + esc(r.role_code) + '"' + (r.role_code === sel ? ' selected' : '') + '>' +
        esc(r.role_name) + '</option>';
    }).join('');
  }

  function showAuthForm(tab) {
    tab = tab || 'login';
    var isLogin = tab === 'login';
    var body = isLogin
      ? '<div class="cpq-auth-f"><label>登录名</label><input id="cpqAuthU" autocomplete="username"></div>' +
        '<div class="cpq-auth-f"><label>密码</label><input id="cpqAuthP" type="password" autocomplete="current-password"></div>'
      : '<div class="cpq-auth-f"><label>登录名</label><input id="cpqAuthU" autocomplete="username"></div>' +
        '<div class="cpq-auth-f"><label>姓名</label><input id="cpqAuthN" placeholder="显示在卡片与任务上"></div>' +
        '<div class="cpq-auth-f"><label>角色</label><select id="cpqAuthR">' + roleOptions('sales_mgr') + '</select></div>' +
        '<div class="cpq-auth-f"><label>密码</label><input id="cpqAuthP" type="password" placeholder="至少 6 位" autocomplete="new-password"></div>';

    mask(
      '<div class="cpq-auth-hd"><h3>配置报价 CPQ</h3><p>登录后按角色完成对应的报价步骤</p></div>' +
      '<div class="cpq-auth-tabs">' +
      '<div class="cpq-auth-tab' + (isLogin ? ' on' : '') + '" data-tab="login">登录</div>' +
      '<div class="cpq-auth-tab' + (isLogin ? '' : ' on') + '" data-tab="register">注册</div>' +
      '</div>' +
      '<div class="cpq-auth-bd">' + body + '</div>' +
      '<div class="cpq-auth-msg" id="cpqAuthMsg"></div>' +
      '<div class="cpq-auth-ft">' +
      '<button class="cpq-auth-btn ghost" id="cpqAuthCancel">取消</button>' +
      '<button class="cpq-auth-btn primary" id="cpqAuthOk">' + (isLogin ? '登录' : '注册并登录') + '</button>' +
      '</div>'
    );

    document.querySelectorAll('.cpq-auth-tab').forEach(function (t) {
      t.onclick = function () { showAuthForm(t.dataset.tab); };
    });
    document.getElementById('cpqAuthCancel').onclick = close;
    document.getElementById('cpqAuthOk').onclick = function () { submit(isLogin); };
    ['cpqAuthU', 'cpqAuthP', 'cpqAuthN'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(isLogin); });
    });
    var u = document.getElementById('cpqAuthU');
    if (u) u.focus();
  }

  function submit(isLogin) {
    var btn = document.getElementById('cpqAuthOk');
    var val = function (id) { var e = document.getElementById(id); return e ? e.value.trim() : ''; };
    var username = val('cpqAuthU'), password = val('cpqAuthP');
    if (!username || !password) { msg('请填写登录名与密码'); return; }
    var payload = { username: username, password: password };
    if (!isLogin) {
      payload.display_name = val('cpqAuthN') || username;
      payload.role_code = val('cpqAuthR');
      if (password.length < 6) { msg('密码至少 6 位'); return; }
    }
    btn.disabled = true;
    msg('');
    api(isLogin ? '/auth/login' : '/auth/register', { method: 'POST', body: payload })
      .then(function (d) {
        setToken(d.token);
        state.user = d.user;
        syncButton();
        msg(isLogin ? '登录成功' : '注册成功，已登录', true);
        setTimeout(function () { close(); showAccount(); }, 450);
        document.dispatchEvent(new CustomEvent('cpq-auth-change', { detail: { user: state.user } }));
      })
      .catch(function (e) { msg(e.message || '操作失败'); })
      .then(function () { if (btn) btn.disabled = false; });
  }

  function showAccount() {
    var u = state.user;
    if (!u) { showAuthForm('login'); return; }
    var initial = (u.display_name || u.username || '?').trim().charAt(0).toUpperCase();
    mask(
      '<div class="cpq-auth-hd"><h3>账号信息</h3></div>' +
      '<div class="cpq-auth-bd">' +
      '<div class="cpq-auth-card">' +
      '<div class="cpq-auth-avatar">' + esc(initial) + '</div>' +
      '<div><div class="cpq-auth-name">' + esc(u.display_name) + '</div>' +
      '<span class="cpq-auth-role">' + esc(u.role_name) + '</span></div>' +
      '</div>' +
      '<div class="cpq-auth-kv">' +
      '<div><span>登录名</span><span>' + esc(u.username) + '</span></div>' +
      (u.email ? '<div><span>邮箱</span><span>' + esc(u.email) + '</span></div>' : '') +
      '<div><span>上次登录</span><span>' + esc(fmt(u.last_login_at)) + '</span></div>' +
      '</div></div>' +
      '<div class="cpq-auth-msg" id="cpqAuthMsg"></div>' +
      '<div class="cpq-auth-ft">' +
      '<button class="cpq-auth-btn ghost" id="cpqAuthCancel">关闭</button>' +
      '<button class="cpq-auth-btn primary" id="cpqAuthOut">退出登录</button>' +
      '</div>'
    );
    document.getElementById('cpqAuthCancel').onclick = close;
    document.getElementById('cpqAuthOut').onclick = function () {
      api('/auth/logout', { method: 'POST' })
        .catch(function () { /* 令牌失效也照常本地登出 */ })
        .then(function () {
          setToken(''); state.user = null; syncButton(); close();
          document.dispatchEvent(new CustomEvent('cpq-auth-change', { detail: { user: null } }));
        });
    };
  }

  function fmt(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
      ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  /* ---------------- 启动：拉角色字典 + 当前登录态 ---------------- */
  function refresh() {
    return Promise.all([
      api('/auth/roles').then(function (d) { state.roles = d.roles || []; }).catch(function () {}),
      api('/auth/me').then(function (d) { state.user = d.user || null; }).catch(function () { state.user = null; }),
    ]).then(function () {
      state.loaded = true;
      syncButton();
      document.dispatchEvent(new CustomEvent('cpq-auth-ready', { detail: { user: state.user } }));
      return state.user;
    });
  }

  window.cpqAuth = {
    open: function () { state.user ? showAccount() : showAuthForm('login'); },
    login: function () { showAuthForm('login'); },
    register: function () { showAuthForm('register'); },
    close: close,
    refresh: refresh,
    user: function () { return state.user; },
    token: token,
    /** 带登录态的 fetch 封装，供后续任务流接口调用 */
    api: api,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', refresh);
  } else {
    refresh();
  }
})();
