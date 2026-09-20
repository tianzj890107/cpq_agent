/* 已开启认证时，所有业务页统一回到登录页；鉴权关闭时不改变原有本地开发体验。
 *
 * 【CPQ 定制】接入 CPQ 统一登录后，本地的 auth.html 已停用（后端 /api/login 直接回
 * 409）。此时把人重定向过去只会跳到一个登不进去的页面，登录入口改由 cpq-sso.js
 * 弹 CPQ 的登录框 —— 所以 SSO 模式下这里只负责让路。
 *
 * 【批次 8】「首次登录态查询」全页只允许一次，统一由 TechAuth.ready() 发出：本文件
 * 在它 resolve 之后按状态决定要不要让路，不再自己另发一份 /api/me（原来同一页面要查
 * 三次登录态）。TechAuth 缺失（旧壳）时保持原有的自带查询，不抛错、不重载。 */
(() => {
  const path = location.pathname;
  if (path.endsWith('/auth.html') || path.endsWith('/account.html')) return;
  const auth = window.TechAuth;

  const token = () => {
    if (auth && typeof auth.token === 'function') return auth.token();
    return localStorage.getItem('cpq_auth_token') || '';
  };

  const clearToken = () => {
    if (auth && typeof auth.clear === 'function') { auth.clear(); return; }
    localStorage.removeItem('cpq_auth_token');
  };

  const redirectToLogin = () => {
    fetch('/api/health').then(r => r.ok ? r.json() : null).catch(() => null).then(sso => {
      if (sso?.sso_enabled) return;      // 交给 cpq-sso.js 处理，别跳本地登录页
      clearToken();
      const next = `${location.pathname.replace(/^\//, '')}${location.search}${location.hash}`;
      location.replace(`auth.html?next=${encodeURIComponent(next)}`);
    }).catch(() => {});
  };

  const inspect = (status) => { if (status === 401) redirectToLogin(); };

  if (auth && typeof auth.ready === 'function') {
    auth.ready().then(identity => inspect(identity && identity.status), () => {});
    return;
  }

  const current = token();
  fetch('/api/me', {headers: current ? {Authorization:`Bearer ${current}`} : {}})
    .then(response => inspect(response.status))
    .catch(() => {});
})();
