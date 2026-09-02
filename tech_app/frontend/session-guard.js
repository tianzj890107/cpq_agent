/* 已开启认证时，所有业务页统一回到登录页；鉴权关闭时不改变原有本地开发体验。
 *
 * 【CPQ 定制】接入 CPQ 统一登录后，本地的 auth.html 已停用（后端 /api/login 直接回
 * 409）。此时把人重定向过去只会跳到一个登不进去的页面，登录入口改由 cpq-sso.js
 * 弹 CPQ 的登录框 —— 所以 SSO 模式下这里只负责让路。 */
(() => {
  const path = location.pathname;
  if (path.endsWith('/auth.html') || path.endsWith('/account.html')) return;
  const token = localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token') || '';
  fetch('/api/me', {headers: token ? {Authorization:`Bearer ${token}`} : {}})
    .then(async response => {
      if (response.status !== 401) return;
      const sso = await fetch('/api/health').then(r => r.ok ? r.json() : null).catch(() => null);
      if (sso?.sso_enabled) return;      // 交给 cpq-sso.js 处理，别跳本地登录页
      localStorage.removeItem('authToken'); localStorage.removeItem('cad_engine_token');
      const next = `${location.pathname.replace(/^\//, '')}${location.search}${location.hash}`;
      location.replace(`auth.html?next=${encodeURIComponent(next)}`);
    })
    .catch(() => {});
})();
