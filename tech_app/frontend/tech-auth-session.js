/* 技术工艺 · 统一登录态客户端（批次 8A）——每页第一个加载，早于任何读令牌的脚本。
 *
 * 背景：CPQ 的令牌只存在 localStorage.cpq_auth_token，但技术工艺十几个页面在**脚本
 * 求值时**读的是历史兼容键 authToken / cad_engine_token；而兼容键的抄写散落在各处，
 * 任何一处漏抄就是「页面看着能点、每个请求都不带票」。身份变化后的收敛手段是整页重载，
 * 代价是正在填的表单、会话滚动位置、当前 project / stage 全部丢掉。
 *
 * 本文件把这件事收成一份事实源与唯一写入口：
 *   · token()              —— 唯一读取：cpq_auth_token → 兼容键兜底
 *   · setToken() / clear() —— 唯一写入：三份键同步、同步广播；绝不 reload、
 *                             绝不写 sessionStorage（iframe 与父壳共用同一份 localStorage）
 *   · migrate()            —— 兼容键 → 唯一事实源，读一次即迁移并删除兼容键（幂等）
 *   · subscribe()          —— 身份变化时**同步**通知，页面据此就地重取身份
 *   · bindContext()        —— 记住本页 project / stage，与登录态解耦（登出也不清空）
 *   · ready()              —— 首次登录态查询（同一页面只此一次）；无票也 resolve
 *
 * 兼容键仍由 setToken 一并镜像：技术工艺仍有页面在脚本求值时直接读 authToken /
 * cad_engine_token（app.js、cost-review.js、workflow.js 等）。抄写职责整体收进本文件，
 * 是「唯一写入口」，不是第二份事实源。
 */
(function () {
  'use strict';

  var TOKEN_KEY = 'cpq_auth_token';
  var LEGACY_KEYS = ['authToken', 'cad_engine_token'];
  var NAMESPACE = 'cpq:tech-auth';

  var subscribers = [];
  var context = { project: '', stage: '' };
  var readyDone = false;
  var readyPromise = null;
  var identity = null;
  var lastBroadcastToken = null;

  function storage() {
    try { return window.localStorage || null; } catch (error) { return null; }
  }

  function readKey(key) {
    var store = storage();
    if (!store) return '';
    try {
      var value = store.getItem(key);
      return value == null ? '' : String(value);
    } catch (error) { return ''; }
  }

  function writeKey(key, value) {
    var store = storage();
    if (!store) return;
    try {
      if (value) store.setItem(key, String(value));
      else store.removeItem(key);
    } catch (error) { /* 隐私模式等：全部读写都被 try/catch 包住 */ }
  }

  function dropKey(key) { writeKey(key, ''); }

  /* 唯一读取：cpq_auth_token 优先；只有它为空时才用兼容键兜底（历史会话迁移期）。 */
  function token() {
    var canonical = readKey(TOKEN_KEY);
    if (canonical) return canonical;
    for (var i = 0; i < LEGACY_KEYS.length; i += 1) {
      var legacy = readKey(LEGACY_KEYS[i]);
      if (legacy) return legacy;
    }
    return '';
  }

  /* 兼容键 → 唯一事实源。已迁移过的键不会再次写回；重复调用结果不变（幂等）。 */
  function migrate() {
    var canonical = readKey(TOKEN_KEY);
    if (canonical) {
      LEGACY_KEYS.forEach(dropKey);          // 唯一事实源有值：兼容键只删不写
      return canonical;
    }
    var found = '';
    for (var i = 0; i < LEGACY_KEYS.length; i += 1) {
      var legacy = readKey(LEGACY_KEYS[i]);
      if (legacy) { found = legacy; break; }
    }
    if (found) writeKey(TOKEN_KEY, found);
    LEGACY_KEYS.forEach(dropKey);
    return found;
  }

  function notify(event) {
    lastBroadcastToken = event.token;
    subscribers.slice().forEach(function (listener) {
      try { listener(event); } catch (error) { /* 单个订阅者异常不影响其它订阅者 */ }
    });
  }

  function writeAll(value) {
    writeKey(TOKEN_KEY, value);
    LEGACY_KEYS.forEach(function (key) { writeKey(key, value); });
  }

  /* 唯一写入口：三份键同时是「新值 / 新值」或「全空」，再同步广播。 */
  function setToken(next, opts) {
    var options = opts || {};
    var value = next == null ? '' : String(next);
    var previous = token();
    writeAll(value);
    if (options.silent) return;
    notify({ token: value, previous: previous, source: options.source || 'local' });
  }

  function clear() {
    var previous = token();
    writeAll('');
    notify({ token: '', previous: previous, source: 'local' });
  }

  function subscribe(listener) {
    if (typeof listener !== 'function') return function () {};
    subscribers.push(listener);
    return function unsubscribe() {
      var index = subscribers.indexOf(listener);
      if (index >= 0) subscribers.splice(index, 1);
    };
  }

  function bindContext(next) {
    if (!next || typeof next !== 'object') return;
    if (next.project != null) context.project = String(next.project);
    if (next.stage != null) context.stage = String(next.stage);
    if (next.taskId != null) context.taskId = String(next.taskId);
  }

  function contextValue() {
    return { project: context.project, stage: context.stage };
  }

  function embedded() {
    try { return Boolean(window.parent && window.parent !== window); } catch (error) { return false; }
  }

  /* iframe → 父壳：不维护第二份登录态，只把变化广播出去。targetOrigin 绝不用 "*"。 */
  function broadcast(type, detail) {
    if (!embedded()) return false;
    var origin = '';
    try { origin = location.origin || ''; } catch (error) { origin = ''; }
    if (!origin) return false;
    try {
      window.parent.postMessage({
        namespace: NAMESPACE,
        version: 1,
        type: String(type || ''),
        detail: detail || {},
        token: token(),
        context: contextValue(),
      }, origin);
      return true;
    } catch (error) { return false; }
  }

  function isReady() { return readyDone; }

  function requestIdentity(header) {
    var request = null;
    try { request = typeof window.fetch === 'function' ? window.fetch : null; } catch (error) { request = null; }
    if (!request) return Promise.resolve({ ok: false, status: 0, data: null });
    var init = { headers: header ? { Authorization: header } : {} };
    return Promise.resolve(request('/api/me', init)).then(function (response) {
      var status = response && typeof response.status === 'number' ? response.status : 0;
      var ok = Boolean(response && response.ok);
      if (!response || typeof response.json !== 'function') return { ok: false, status: status, data: null };
      if (!ok && status !== 0) return { ok: false, status: status, data: null };
      return Promise.resolve(response.json()).then(
        function (data) { return { ok: true, status: status, data: data || null }; },
        function () { return { ok: false, status: status, data: null }; });
    }, function () { return { ok: false, status: 0, data: null }; });
  }

  /* 首次登录态查询：整个页面只允许这一次（session-guard.js 等复用它的结果）。 */
  function ready() {
    if (readyPromise) return readyPromise;
    var effective = migrate();
    if (effective) writeAll(effective);      // 兼容键删掉后仍镜像一份，老读者不断供
    readyPromise = requestIdentity(effective ? ('Bearer ' + effective) : '').then(function (result) {
      readyDone = true;
      identity = {
        token: token(),
        user: (result.data && result.data.user) || null,
        payload: result.data || null,
        status: result.status,
        ok: Boolean(result.ok),
      };
      return identity;
    }, function () {
      readyDone = true;
      identity = { token: token(), user: null, payload: null, status: 0, ok: false };
      return identity;
    });
    return readyPromise;
  }

  function identityResult() { return identity; }

  /* CPQ 自己写 cpq_auth_token（cpq_auth.js 的登录 / 登出）：本页就地镜像并广播，
     绝不整页重载。跨标签页走 storage 事件，本标签页走 cpq-auth-change。 */
  function syncFromStorage(sourceName) {
    var current = token();
    if (current === lastBroadcastToken) return;
    setToken(current, { source: sourceName });
  }

  try {
    window.addEventListener('storage', function (event) {
      var key = event && event.key;
      if (key !== TOKEN_KEY && LEGACY_KEYS.indexOf(String(key)) < 0) return;
      syncFromStorage('storage');
    });
  } catch (error) { /* 无 window 事件能力时静默降级 */ }

  try {
    document.addEventListener('cpq-auth-change', function () { syncFromStorage('local'); });
  } catch (error) { /* 同上 */ }

  window.TechAuth = {
    TOKEN_KEY: TOKEN_KEY,
    LEGACY_KEYS: LEGACY_KEYS.slice(),
    token: token,
    setToken: setToken,
    clear: clear,
    migrate: migrate,
    subscribe: subscribe,
    bindContext: bindContext,
    context: contextValue,
    embedded: embedded,
    broadcast: broadcast,
    isReady: isReady,
    ready: ready,
    identity: identityResult,
  };
})();
