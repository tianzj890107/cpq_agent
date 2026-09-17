/* 技术工艺业务页面「项目身份唯一来源」（window.TechProjectContext）。
 *
 * 背景：原先 13 个页面各自写了一份
 *   「?project 取不到时退回 localStorage 里上一次的项目」的兜底表达式，
 * 于是 URL 少了 project 就静默打开「上一次那个项目」——在共享终端上会串项目、
 * 越权、两个标签页互相覆写。本模块是**唯一**的项目身份解析点：
 *
 *   · URL 的 ?project= 是唯一事实源；
 *   · 嵌入统一工作台时，允许用父壳标在 iframe 上的 data-project 兜底（父壳有 project 时
 *     子页 URL 一定带 project，这条只是防手改 URL 后两边不一致）；
 *   · URL 有、父壳也有且不同 → 拒绝（project_mismatch），不猜；
 *   · 只在 2.3 允许第三条通道：URL 无 project 但有 task_id 时，用 GET /wf/task 唯一推导；
 *     关联 0 个或多个不同项目 → 停止并提示。
 *
 * 硬口径：
 *   · **绝不读** localStorage / sessionStorage —— 那两个键只能承担「最近访问导航」；
 *   · resolve / bind / current / assertSame / projectFromTaskPayload 纯读、幂等、无副作用；
 *   · 缺项目时页面不得请求项目接口、不得写入，也不得自动打开上一次项目；
 *   · fromTask 对同一 task id 去重（含并发），失败后清缓存允许重试。
 *
 * 只读入参、只做判定：错误文案在这里，怎么显示（toast / 错误态 / 回首页）归调用点。
 */
(function (root) {
  'use strict';

  // 每个错误码都带一句可以直接展示给用户的中文 message。
  var MESSAGES = {
    missing_project: '本页没有拿到项目编号（URL 缺 ?project=）。请从统一工作台的流程栏或项目列表重新进入；'
      + '本页不会自动打开上一次访问的项目。',
    project_mismatch: '当前页面的项目与打开它的统一工作台不一致，已拒绝加载，也不会退回上一次的项目。'
      + '请从统一工作台重新进入。',
    task_no_project: '这条任务没有关联任何项目，无法确定项目身份，已停止加载。请从项目列表进入。',
    task_ambiguous: '这条任务关联了多个不同的项目，无法确定项目身份，已停止加载。请从项目列表进入。',
  };

  // 本页已 bind 的身份；未 bind 过时 current() 会先 bind()。
  var context = null;
  // 同一 task id 的 in-flight / 已得结论缓存：并发 Promise.all 也只发一次请求。
  var taskCache = new Map();

  function textOf(value) {
    if (value === null || value === undefined) return '';
    return String(value).trim();
  }

  function normalizedSearch(search) {
    var text = textOf(search);
    return text.charAt(0) === '?' ? text.slice(1) : text;
  }

  function projectFromSearch(search) {
    var query = normalizedSearch(search);
    if (!query) return '';
    try {
      return textOf(new URLSearchParams(query).get('project'));
    } catch (error) {
      return '';
    }
  }

  function locationSearch() {
    try {
      return (typeof location !== 'undefined' && location && location.search) ? String(location.search) : '';
    } catch (error) {
      return '';
    }
  }

  /* 父壳（统一工作台）把本壳 project 标在承载本页的 iframe 上（data-project）。
     非嵌入、frameElement 为 null、跨域访问抛错，一律当作「没有父壳项目」。 */
  function parentProjectFromFrame() {
    try {
      var frame = (typeof window !== 'undefined' && window) ? window.frameElement : null;
      if (!frame || !frame.dataset) return '';
      return textOf(frame.dataset.project);
    } catch (error) {
      return '';
    }
  }

  function outcome(project, source, error) {
    var id = textOf(project);
    return {
      project: id,
      source: id ? (source || '') : '',
      error: error || '',
      message: error ? (MESSAGES[error] || '') : '',
    };
  }

  /* 纯函数：只读入参。search 为 location.search 原文（可带或不带前导 '?'）；
     parentProject 省略时按「没有父壳」处理（只有 bind() 才去读真实环境）。 */
  function resolve(options) {
    var opts = options || {};
    var urlProject = projectFromSearch(opts.search === undefined ? locationSearch() : opts.search);
    var parentProject = opts.parentProject === undefined ? '' : textOf(opts.parentProject);
    if (urlProject && parentProject && urlProject !== parentProject) {
      return outcome('', '', 'project_mismatch');
    }
    if (urlProject) return outcome(urlProject, 'url', '');
    if (parentProject) return outcome(parentProject, 'parent', '');
    return outcome('', '', 'missing_project');
  }

  /* 读真实环境（location.search + window.frameElement.dataset.project）解析并缓存本页身份。 */
  function bind(options) {
    if (options && typeof options === 'object') {
      context = resolve({
        search: options.search === undefined ? locationSearch() : options.search,
        parentProject: options.parentProject === undefined ? parentProjectFromFrame() : options.parentProject,
      });
    } else {
      context = resolve({ search: locationSearch(), parentProject: parentProjectFromFrame() });
    }
    return context;
  }

  function current() {
    return context || bind();
  }

  /* 写请求（PUT/POST/DELETE）前的一致性校验：project 必须等于 current().project。 */
  function assertSame(project) {
    var ctx = current();
    if (!ctx.project) {
      return { ok: false, error: 'missing_project', message: MESSAGES.missing_project };
    }
    if (textOf(project) !== ctx.project) {
      return { ok: false, error: 'project_mismatch', message: MESSAGES.project_mismatch };
    }
    return { ok: true, error: '', message: '' };
  }

  // /wf/task 的 payload 里可能带项目的四处位置；去重后取唯一值。
  var PAYLOAD_PATHS = [
    function (payload) { return payload.tech_cost && payload.tech_cost.project_id; },
    function (payload) { return payload.tech_project_id; },
    function (payload) { return payload.project_id; },
    function (payload) { return payload.tech_result && payload.tech_result.project_id; },
    function (payload) { return payload.result && payload.result.project_id; },
  ];

  /* 纯函数：从 /wf/task 的 task.payload 推导唯一项目。
     0 个 → task_no_project；≥2 个**不同**值 → task_ambiguous（相同值重复不算冲突）。 */
  function projectFromTaskPayload(payload) {
    if (!payload || typeof payload !== 'object') return outcome('', '', 'task_no_project');
    var seen = [];
    PAYLOAD_PATHS.forEach(function (pick) {
      var value = '';
      try { value = textOf(pick(payload)); } catch (error) { value = ''; }
      if (value && seen.indexOf(value) < 0) seen.push(value);
    });
    if (!seen.length) return outcome('', '', 'task_no_project');
    if (seen.length > 1) return outcome('', '', 'task_ambiguous');
    return outcome(seen[0], 'task', '');
  }

  /* 唯一允许异步取项目的地方：GET /wf/task?task_id=<id>（既有接口，不新增路由）。
     同一 task id 去重：连续两次 / 并发两次都只发一次请求；失败后清缓存允许重试。 */
  function fromTask(taskId, options) {
    var id = textOf(taskId);
    if (!id) return Promise.reject(new Error('缺少 task_id，无法解析所属项目。'));
    if (taskCache.has(id)) return taskCache.get(id);
    var opts = options || {};
    var doFetch = opts.fetchImpl || (typeof fetch === 'function' ? fetch : null);
    if (!doFetch) return Promise.reject(new Error('当前环境不支持网络请求，无法读取任务所属项目。'));
    var headers = {};
    var token = textOf(opts.token);
    if (token) headers.Authorization = 'Bearer ' + token;

    var pending = Promise.resolve()
      .then(function () { return doFetch('/wf/task?task_id=' + encodeURIComponent(id), { headers: headers }); })
      .then(function (response) {
        if (!response || !response.ok) {
          throw new Error('任务信息读取失败（HTTP ' + String((response && response.status) || 0) + '），无法确定所属项目。');
        }
        return response.json();
      })
      .then(function (doc) {
        if (!doc || doc.ok !== true) throw new Error('任务信息读取失败：服务未返回可用任务，无法确定所属项目。');
        var derived = projectFromTaskPayload(doc.task && doc.task.payload);
        if (derived.error) throw new Error(derived.message);
        return { project: derived.project, source: 'task', error: '', message: '' };
      });

    // 失败要清缓存，用户修好之后再进一次能重试；成功则保持同一份结论。
    var tracked = pending.catch(function (error) {
      taskCache.delete(id);
      throw error;
    });
    taskCache.set(id, tracked);
    return tracked;
  }

  var api = {
    resolve: resolve,
    bind: bind,
    current: current,
    assertSame: assertSame,
    projectFromTaskPayload: projectFromTaskPayload,
    fromTask: fromTask,
  };

  root.TechProjectContext = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
