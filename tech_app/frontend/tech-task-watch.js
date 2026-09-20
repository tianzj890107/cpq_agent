/* 技术工艺 · 长任务轮询与恢复的唯一入口（批次 9 §7.4）。
 *
 * 在此之前，三处轮询都是 `while (true) { await sleep(1200); const task = await api(...) }`，
 * 而 api() 在 fetch 抛错或响应非 2xx 时**直接 throw** —— 一次网络抖动就把一个还在正常
 * 跑的长任务判成「失败」，用户重试又起了第二份模型调用（重复计费）。同一层还缺「按
 * task_id 恢复」的入口：页面一刷新，内存里的 taskId 就没了，卡也断了。
 *
 * 这里把这件事收成一份实现：
 *   · 状态词表与中文口径（与后端 tasks.TASK_STATUSES 逐字一致）；
 *   · recover / list：刷新后按 task_id 取回记录、任务中心列出全部任务（含在途）；
 *   · watch：轮询到终态才结算；瞬时故障（网络抛错 / 5xx / JSON 解析失败）只计数，
 *     连续成功一次清零；连续达到 transientLimit 进入 degraded（连接不稳定，结果仍在
 *     处理中），**绝不因此判失败**，只是放慢间隔继续等；abort() 以 aborted 结算且幂等，
 *     不发任何写请求（取消要显式调 cancel 接口）。
 */
(function () {
  'use strict';

  var STATUSES = ['queued', 'running', 'succeeded', 'partial', 'failed',
                  'interrupted', 'cancelled', 'unknown'];
  var TERMINAL = ['succeeded', 'partial', 'failed', 'interrupted', 'cancelled'];
  var WORDS = {
    queued: '排队中', running: '进行中', succeeded: '已完成', partial: '部分完成',
    failed: '失败', interrupted: '中断', cancelled: '已取消', unknown: '状态未知',
  };
  var TERMINAL_MESSAGES = {
    failed: '任务失败，请查看失败原因后重试。',
    interrupted: '服务在任务执行期间重启，任务已中断；请确认输入后重新发起。',
    cancelled: '任务已被取消。',
  };
  var DEFAULT_INTERVAL = 1200;
  var DEFAULT_DEGRADED_INTERVAL = 5000;
  var DEFAULT_TRANSIENT_LIMIT = 3;

  function normalizeStatus(value) {
    var text = String(value == null ? '' : value).trim().toLowerCase();
    return STATUSES.indexOf(text) >= 0 ? text : 'unknown';
  }

  function isTerminal(value) {
    return TERMINAL.indexOf(normalizeStatus(value)) >= 0;
  }

  function statusWord(value) {
    return WORDS[normalizeStatus(value)] || '状态未知';
  }

  function taskUrl(projectId, taskId) {
    return '/api/projects/' + encodeURIComponent(String(projectId || ''))
      + '/tasks/' + encodeURIComponent(String(taskId || ''));
  }

  function listUrl(projectId) {
    return '/api/projects/' + encodeURIComponent(String(projectId || '')) + '/tasks';
  }

  /* 默认取票口径与页面一致（批次 8 之后令牌仍会镜像进这两个兼容键）。 */
  function authHeaders() {
    var headers = {};
    try {
      var token = localStorage.getItem('cpq_auth_token')
        || localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token');
      if (token) headers.Authorization = 'Bearer ' + token;
    } catch (error) { /* 隐私模式 / 无 localStorage：交给调用方的 fetchImpl */ }
    return headers;
  }

  function failure(code, message, extra) {
    var error = new Error(message || code || '任务未完成');
    error.ok = false;
    error.code = String(code || '');
    if (extra) {
      Object.keys(extra).forEach(function (key) {
        if (extra[key] !== undefined) error[key] = extra[key];
      });
    }
    return error;
  }

  function detailOf(body) {
    if (!body || typeof body !== 'object') return '';
    var detail = body.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length) {
      return detail.map(function (row) {
        return String((row && (row.msg || row.detail)) || '');
      }).filter(Boolean).join('；');
    }
    return String(body.message || '');
  }

  /* 追踪 ID：错误体优先（后端 ≥400 的 JSON 体带它），再退回 X-Trace-Id 响应头。 */
  function traceOf(body, response) {
    var fromBody = body && (body.trace_id || body.traceId);
    if (fromBody) return String(fromBody);
    try {
      var header = response && response.headers && response.headers.get
        ? response.headers.get('X-Trace-Id') : '';
      if (header) return String(header);
    } catch (error) { /* 读不到响应头就留空 */ }
    return '';
  }

  function pickFetch(fetchImpl) {
    if (typeof fetchImpl === 'function') return fetchImpl;
    if (typeof window.fetch === 'function') {
      return function (url, options) { return window.fetch(url, options); };
    }
    return null;
  }

  function callFetch(fetchImpl, url, options) {
    var impl = pickFetch(fetchImpl);
    if (!impl) return Promise.reject(failure('request-rejected', '当前环境不支持网络请求'));
    try {
      return Promise.resolve(impl(url, options));
    } catch (error) {
      return Promise.reject(error);
    }
  }

  /* 错误体只是附带信息：读不出来（空体 / 非 JSON）也必须能分类。 */
  function readBody(response) {
    try {
      if (response && typeof response.json === 'function') {
        return Promise.resolve(response.json()).then(
          function (data) { return data || {}; }, function () { return {}; });
      }
    } catch (error) { /* 落到空对象 */ }
    return Promise.resolve({});
  }

  function statusOf(response) {
    var status = response && typeof response.status === 'number' ? response.status : 0;
    return status || 0;
  }

  function recover(projectId, taskId, fetchImpl) {
    return callFetch(fetchImpl, taskUrl(projectId, taskId),
                     {method: 'GET', headers: authHeaders()}).then(function (response) {
      var status = statusOf(response);
      return readBody(response).then(function (body) {
        if (status === 404) return null;                      // 任务已不在：不是错误
        if (status === 401 || status === 403) {
          throw failure('permission_denied', detailOf(body) || '没有查看该任务的权限',
                        {status: status, trace_id: traceOf(body, response)});
        }
        if (status >= 400 || !response || !response.ok) {
          throw failure('request-rejected',
                        detailOf(body) || ('读取任务失败（HTTP ' + status + '）'),
                        {status: status, trace_id: traceOf(body, response)});
        }
        return body;
      });
    });
  }

  function list(projectId, fetchImpl) {
    return callFetch(fetchImpl, listUrl(projectId),
                     {method: 'GET', headers: authHeaders()}).then(function (response) {
      var status = statusOf(response);
      return readBody(response).then(function (body) {
        if (status === 401 || status === 403) {
          throw failure('permission_denied', detailOf(body) || '没有查看任务列表的权限',
                        {status: status, trace_id: traceOf(body, response)});
        }
        if (status >= 400 || !response || !response.ok) {
          throw failure('request-rejected',
                        detailOf(body) || ('读取任务列表失败（HTTP ' + status + '）'),
                        {status: status, trace_id: traceOf(body, response)});
        }
        if (Array.isArray(body)) return body;
        return Array.isArray(body && body.tasks) ? body.tasks : [];
      });
    });
  }

  function positive(value, fallback) {
    var number = Number(value);
    return number > 0 ? number : fallback;
  }

  function watch(options) {
    var opts = options || {};
    var intervalMs = positive(opts.intervalMs, DEFAULT_INTERVAL);
    var degradedIntervalMs = positive(opts.degradedIntervalMs, DEFAULT_DEGRADED_INTERVAL);
    var transientLimit = positive(opts.transientLimit, DEFAULT_TRANSIENT_LIMIT);
    var state = {status: 'unknown', transient_failures: 0, degraded: false, record: null};
    var done = false;
    var timer = null;
    var resolvePromise = null;
    var rejectPromise = null;
    var promise = new Promise(function (resolve, reject) {
      resolvePromise = resolve;
      rejectPromise = reject;
    });

    function clearTimer() {
      if (timer === null) return;
      try { clearTimeout(timer); } catch (error) { /* 已触发 */ }
      timer = null;
    }

    function settle(settleWith, value) {
      if (done) return;
      done = true;
      clearTimer();
      settleWith(value);
    }

    function abort() {
      // 幂等：已结算（含已 abort）再调一次什么都不做，也不抛异常。
      settle(rejectPromise, failure('aborted', '已停止等待该任务（任务本身没有被取消）'));
    }

    function sleep(ms) {
      return new Promise(function (resolve) {
        timer = setTimeout(function () { timer = null; resolve(); }, ms);
      });
    }

    /* 瞬时故障：只计数。达到上限进入 degraded（放慢间隔继续等），promise 既不 resolve
       也不 reject —— 一次网络抖动不等于任务失败。 */
    function transient() {
      state.transient_failures += 1;
      if (!state.degraded && state.transient_failures >= transientLimit) {
        state.degraded = true;
        if (typeof opts.onDegraded === 'function') {
          try { opts.onDegraded(state); } catch (error) { /* 回调异常不影响轮询 */ }
        }
      }
    }

    function notice(record) {
      state.transient_failures = 0;                    // 连续成功一次即清零
      var status = normalizeStatus(record && record.status);
      state.status = status;
      state.record = record || null;
      if (typeof opts.onProgress === 'function') {
        try { opts.onProgress(record, state); } catch (error) { /* 同上 */ }
      }
      if (status === 'succeeded' || status === 'partial') {
        settle(resolvePromise, {ok: true, status: status, record: record});
        return;
      }
      if (status === 'failed' || status === 'interrupted' || status === 'cancelled') {
        settle(rejectPromise, failure(status === 'failed' ? 'task-failed' : status,
                                      (record && record.error) || TERMINAL_MESSAGES[status],
                                      {status: status, record: record,
                                       trace_id: String((record && record.trace_id) || '')}));
      }
      // queued / running / unknown：继续按当前节奏轮询。
    }

    function pollOnce() {
      return callFetch(opts.fetchImpl, taskUrl(opts.projectId, opts.taskId),
                       {method: 'GET', headers: authHeaders()}).then(function (response) {
        var status = statusOf(response);
        if (status === 404) {
          return readBody(response).then(function (body) {
            settle(rejectPromise, failure('task-not-found',
                  detailOf(body) || '任务已不在（可能被清理，或不属于这个项目）',
                  {status: status, trace_id: traceOf(body, response)}));
          });
        }
        if (status === 401 || status === 403) {
          return readBody(response).then(function (body) {
            settle(rejectPromise, failure('permission_denied',
                  detailOf(body) || '没有查看该任务的权限',
                  {status: status, trace_id: traceOf(body, response)}));
          });
        }
        if (status >= 400 && status < 500) {
          return readBody(response).then(function (body) {
            settle(rejectPromise, failure('request-rejected',
                  detailOf(body) || ('读取任务失败（HTTP ' + status + '）'),
                  {status: status, trace_id: traceOf(body, response)}));
          });
        }
        if (status >= 500 || !response || !response.ok) {
          transient();
          return null;
        }
        var parsed = null;
        try {
          parsed = response && typeof response.json === 'function'
            ? Promise.resolve(response.json()) : Promise.reject(new Error('没有 JSON 响应体'));
        } catch (error) {
          parsed = Promise.reject(error);
        }
        return parsed.then(function (record) { notice(record || {}); return null; },
                           function () { transient(); return null; });
      }, function () { transient(); return null; });
    }

    (async function loop() {
      while (!done) {
        await sleep(state.degraded ? degradedIntervalMs : intervalMs);
        if (done) return;
        try {
          await pollOnce();
        } catch (error) { /* pollOnce 内部已完成分类，异常不外泄 */ }
      }
    })();

    try {
      if (opts.signal && typeof opts.signal.addEventListener === 'function') {
        opts.signal.addEventListener('abort', abort);
      }
    } catch (error) { /* 无 AbortSignal：调用方直接调 abort() */ }

    return {
      promise: promise,
      abort: abort,
      state: function () {
        return {status: state.status, transient_failures: state.transient_failures,
                degraded: state.degraded, record: state.record};
      },
    };
  }

  window.TechTaskWatch = {
    STATUSES: STATUSES.slice(),
    TERMINAL: TERMINAL.slice(),
    WORDS: WORDS,
    normalizeStatus: normalizeStatus,
    isTerminal: isTerminal,
    statusWord: statusWord,
    recover: recover,
    list: list,
    watch: watch,
  };
})();
