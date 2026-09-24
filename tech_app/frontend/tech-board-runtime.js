/* 技术工艺统一工作台 —— 右侧看板运行时（九个阶段页面共用，只加载一次）。
 *
 * 职责（iframe 端，只跑在右侧阶段页面里）：
 *   · 接收父壳（tech-workbench）发来的业务命令，校验同源、消息来源、命名空间、
 *     版本、requestId、projectId 与 stage；
 *   · 维护当前页面注册的语义化业务动作，执行动作并用同一 requestId 回传成功或失败；
 *   · 未知动作明确返回结构化错误，绝不静默忽略；
 *   · 动作状态变化时主动通知父壳，页面初始化完成后发送 ready 与动作状态快照。
 *
 * 消息信封（与父壳 tech-board-bridge.js 共用同一份协议）：
 *   type    消息类别：'command'（父壳→看板）/ 'state'（看板→父壳）/ 'result'（看板回执）；
 *   name    事件名 / 命令名：'ready' / 'action-state' / 'task-progress' /
 *           'task-completed' / 'task-failed' / 'selection-changed' ...；
 *   payload 业务数据。状态事件的主体（动作名 / view 名 / 'register-actions' /
 *           'register-views' / 'context' / 'view'）放在 payload.subject；
 *           任务类事件继续保留 payload.action。type 是消息类别，绝不是事件名。
 *
 * 页面不再各自写一套 postMessage 解析器，也不暴露按钮 id / CSS selector：
 * 父壳和 Agent 只知道动作名，具体实现留在本页面。
 *
 * 长任务（`deferred: true`）：动作条目只负责「启动」，回执照旧秒级返回，但运行时
 * 不再替它发布 task-completed —— 真正的完成 / 失败由本页在后台任务结束时用
 * `publish('task-completed' | 'task-failed', <动作名>, { action, message? })` 自报，
 * 20 秒的桥默认超时只用来抓「看板没响应」，长任务不会再被误判成超时。
 *
 * 静默条目（`silent: true`）：动作照常执行、照常回执并更新 action-state，但运行时不为它
 * 发布 task-progress / task-completed / task-failed —— 会话里不会留下「xxx 已完成」卡片。
 * 只给纯看板内同步用（刷新数据、刷新某个页面）；解析这类需要在左侧留痕的长任务不适用。
 *
 * 独立打开（无 embed、无父壳）时本模块不做任何通信，页面照常工作。
 */
(function () {
  'use strict';

  var NAMESPACE = 'cpq:tech-board';
  var VERSION = 1;
  var STAGES = [
    'requirement-create', 'requirement-confirm', 'requirement-review', 'drawing',
    'process', 'cost', 'summary', 'report-review', 'report-publish',
  ];

  // 标准状态 / 事件名（与父壳 tech-board-bridge.js 共用同一份协议常量）：
  //   ready            页面初始化完成、动作表可用；
  //   action-state     动作表或某个动作的 visible/enabled/busy/label 变化；
  //   task-progress    长任务开始或有进度推进；
  //   task-completed   长任务成功结束；
  //   task-blocked    长任务被前置条件阻断（终态，不是失败）；
  //   task-failed      长任务失败（结构化错误同时通过 result 回传）；
  //   selection-changed 看板内部选中的零件 / 视图发生变化；
  //   board-status     本页那行步骤状态（「已打开项目 …」/「就绪」/「本步已确认」…）
  //                    原样上报，由父壳渲染进统一标题行的提示位（payload.text / level）。
  var EVENT = {
    READY: 'ready',
    ACTION_STATE: 'action-state',
    TASK_PROGRESS: 'task-progress',
    TASK_COMPLETED: 'task-completed',
    TASK_PARTIAL: 'task-partial',
    TASK_BLOCKED: 'task-blocked',
    TASK_FAILED: 'task-failed',
    SELECTION_CHANGED: 'selection-changed',
    BOARD_STATUS: 'board-status',
  };

  var qs = new URLSearchParams(location.search);
  var context = {
    projectId: qs.get('project') || '',
    stage: qs.get('stage') || '',
    taskId: qs.get('task_id') || '',
  };

  // 只有嵌在父壳里才通信；独立打开时 registerActions 仍然可用，只是不发送。
  var parentWindow = (window.parent && window.parent !== window) ? window.parent : null;

  var actions = Object.create(null);   // 业务动作：name -> { label, run, getState, state }
  var views = Object.create(null);     // 看板内部视图：name -> { run, getState }
  var overrides = Object.create(null); // updateActionState 写入的覆盖值
  var currentView = '';
  var seq = 0;
  var readySent = false;

  function nextRequestId(prefix) {
    seq += 1;
    return String(prefix || 'rt') + '-' + seq + '-' + Date.now();
  }

  function envelope(type, name, payload, requestId) {
    return {
      namespace: NAMESPACE,
      version: VERSION,
      type: type,
      requestId: requestId || '',
      projectId: context.projectId || '',
      stage: context.stage || '',
      name: name || '',
      payload: payload || {},
    };
  }

  // 绝不使用 "*" 作为 targetOrigin：只发给同源父壳。
  function post(message) {
    if (!parentWindow) return false;
    parentWindow.postMessage(message, location.origin);
    return true;
  }

  function emit(type, name, payload) {
    return post(envelope(type, name, payload, ''));
  }

  // 状态事件统一信封：type 固定为 'state'，name 才是事件名（ready / action-state /
  // task-progress / task-completed / task-failed / selection-changed）。
  // 父壳桥只认 type === 'state'，事件名放在 name；主体由 publish 放进 payload.subject。
  function emitState(name, payload) {
    return emit('state', name, payload || {});
  }

  function entryState(name) {
    var entry = actions[name] || views[name] || {};
    // 静态元数据基线：外层声明的 role / order / hint 就是这三者的单一事实来源，
    // getState() / entry.state 在自己的返回值里覆盖它们（不写即沿用静态声明）。
    // 只回退「元数据」—— visible / enabled / busy / active / analyzed 仍只由运行时状态
    // 决定，不把静态声明误当成运行时状态。否则「外层 role: 'primary'、getState() 只返回
    // visible/enabled/busy」的条目会被静默降级成 aux（1.2 / 1.3 / 3.2 的主按钮就是这样丢的）。
    var base = {};
    ['role', 'order', 'hint'].forEach(function (key) {
      if (entry[key] != null && entry[key] !== '') base[key] = entry[key];
    });
    var raw = base;
    try {
      if (typeof entry.getState === 'function') raw = Object.assign({}, base, entry.getState() || {});
      else if (entry.state && typeof entry.state === 'object') raw = Object.assign({}, base, entry.state);
    } catch (error) {
      // getState() 抛错时保留静态元数据基线（与既有 label 回退语义一致），照旧不向上抛。
      raw = base;
    }
    if (overrides[name]) raw = Object.assign({}, raw, overrides[name]);
    return {
      label: raw.label != null ? String(raw.label) : String(entry.label || name),
      visible: raw.visible !== false,
      enabled: raw.enabled !== false,
      busy: Boolean(raw.busy),
      active: raw.active == null ? null : String(raw.active),
      analyzed: raw.analyzed === true,
      // 动作元数据：role 只认白名单 'primary' / 'aux'（其它值一律降级为 'aux'），
      // order 供父壳在同一步骤内定序，hint 给父壳拼 tooltip。三者既可来自静态条目，
      // 也可由 getState() 动态返回（2.2 的主操作反转就靠它）。
      role: raw.role === 'primary' ? 'primary' : 'aux',
      order: raw.order != null && raw.order !== '' && Number.isFinite(Number(raw.order))
        ? Number(raw.order) : null,
      hint: raw.hint != null ? String(raw.hint) : '',
    };
  }

  function actionSnapshot() {
    var out = {};
    Object.keys(actions).forEach(function (name) { out[name] = entryState(name); });
    return out;
  }

  /* 每个稳定状态的「可见主按钮」必须恰好一个：父壳只渲染 role === 'primary' 的那一颗，
     既不在多个 primary 里挑第一个（那等于父壳替看板决定业务优先级），也不替看板补造
     主按钮。诊断带上 primary_count / stage / view / 当前可见动作名，异常时直接把快照的
     毛病说清楚，而不是让人对着两颗蓝按钮猜。只有「一个可见动作都没有」的页面（这一步
     没有可做的业务动作，左侧操作栏整栏隐藏）不算异常。 */
  function primaryAudit() {
    var visibleActions = [];
    var primaryActions = [];
    Object.keys(actions).forEach(function (name) {
      var state = entryState(name);
      if (state.visible === false) return;
      visibleActions.push(name);
      if (state.role === 'primary') primaryActions.push(name);
    });
    return {
      primary_count: primaryActions.length,
      primary_actions: primaryActions,
      visible_actions: visibleActions,
      stage: context.stage == null ? '' : String(context.stage),
      view: currentView || viewSnapshot().active || '',
      ok: visibleActions.length === 0 || primaryActions.length === 1,
    };
  }

  // 诊断只在动作完整注册后的快照上判定（注册中途不报），返回 null 表示这一帧没有毛病。
  function primaryDiagnostics() {
    var audit = primaryAudit();
    if (audit.visible_actions.length === 0) return null;
    if (audit.primary_count !== 1) {
      return {
        code: 'primary-count',
        primary_count: audit.primary_count,
        stage: audit.stage,
        view: audit.view,
        visible_actions: audit.visible_actions,
        primary_actions: audit.primary_actions,
        message: '看板动作快照的可见主按钮数不是 1：primary_count=' + audit.primary_count
          + '，stage=' + audit.stage + '，view=' + audit.view
          + '，可见动作=' + audit.visible_actions.join('、'),
      };
    }
    return null;
  }

  function viewSnapshot() {
    var active = currentView;
    if (!active) {
      Object.keys(views).forEach(function (name) {
        if (active) return;
        var state = entryState(name);
        if (state.active) active = state.active;
      });
    }
    return { active: active || '' };
  }

  // name = 事件名；subject = 该事件的主体（动作名 / view 名 / 'register-actions' /
  // 'register-views' / 'context' / 'view'）。extra 里的 action / message / selection
  // 等业务字段原样保留，不删任何既有 payload 字段。
  function publish(name, subject, extra) {
    var payload = {
      subject: subject == null ? '' : String(subject),
      actions: actionSnapshot(),
      view: viewSnapshot(),
      stage: context.stage,
      projectId: context.projectId,
      taskId: context.taskId,
      // 快照元数据：这一帧的可见主按钮审计（primary_count / stage / view / 可见动作名）。
      // 只读诊断，不改 actions 结构，也不新增事件；父壳据此决定渲染还是报错。
      primary: primaryAudit(),
    };
    if (extra && typeof extra === 'object') {
      Object.keys(extra).forEach(function (key) { payload[key] = extra[key]; });
    }
    return emitState(name, payload);
  }

  // 阶段页的状态行（status() / aiStatus() / crStatus() / 需求单状态徽标）与本地
  // 显示用的是同一段文字：这里只做透明转发，不改写、不截断、不补前缀。level 只有
  // 'info' 与 'error' 两种，父壳用它决定提示位是否需要优先显示。
  function publishStatus(text, level) {
    return publish(EVENT.BOARD_STATUS, '', {
      text: text == null ? '' : String(text),
      level: level === 'error' ? 'error' : 'info',
    });
  }

  /* ------------------------------------------------------------ 注册与状态 */
  function normalizeRegistry(map, target) {
    if (!map || typeof map !== 'object') return;
    Object.keys(map).forEach(function (name) {
      var value = map[name];
      if (typeof value === 'function') target[name] = { run: value };
      else if (value && typeof value === 'object') target[name] = value;
    });
  }

  function registerActions(map) {
    normalizeRegistry(map, actions);
    publish(EVENT.ACTION_STATE, 'register-actions');
    sendReady();
    return api;
  }

  function registerViews(map) {
    normalizeRegistry(map, views);
    publish(EVENT.ACTION_STATE, 'register-views');
    return api;
  }

  function updateActionState(name, patch) {
    if (!name) return api;
    overrides[name] = Object.assign({}, overrides[name] || {}, patch || {});
    publish(EVENT.ACTION_STATE, String(name));
    return api;
  }

  // 看板每次渲染收尾调它：重新发布一次 action-state 快照，父壳按钮的 busy /
  // 可见性不会停在上一帧。只重发，不做覆盖（覆盖走 updateActionState）。
  function refreshState() {
    publish(EVENT.ACTION_STATE, 'refresh');
    return api;
  }

  function setContext(patch) {
    if (!patch || typeof patch !== 'object') return api;
    Object.keys(patch).forEach(function (key) {
      if (key === 'view' || key === 'viewActive') { currentView = String(patch[key] || ''); return; }
      context[key] = patch[key];
    });
    publish(EVENT.ACTION_STATE, 'context');
    return api;
  }

  function sendReady() {
    if (readySent) return false;
    readySent = true;
    return emitState(EVENT.READY, {
      subject: EVENT.READY,
      actions: actionSnapshot(),
      view: viewSnapshot(),
      stage: context.stage,
      projectId: context.projectId,
      taskId: context.taskId,
    });
  }

  /* ------------------------------------------------------------ 命令分发 */
  // 统一的结构化结果：success 表示动作真的执行成功，error 带上可展示的原因。
  function failure(code, message) {
    return { ok: false, status: 'error', error: { code: code, message: message } };
  }

  function ok(action, result) {
    return { ok: true, status: 'success', action: action, result: result === undefined ? null : result };
  }

  // 动作条目可以可选声明 prompt：这一次执行要用「用户口吻」说清楚自己在做什么。
  // 它是**执行方**的知识（只有右侧看板知道自己这一步真干了什么），所以放在动作
  // 条目上，不进 getState()；解析不出就留空，绝不拿动作名 / label 兜底造句 ——
  // 凭空造句会让左侧出现一句与实际执行不符的话。
  function resolveActionPrompt(entry, payload) {
    var declared = entry ? entry.prompt : '';
    if (typeof declared === 'function') {
      try {
        declared = declared(payload || {});
      } catch (error) {
        return '';
      }
    } else if (typeof declared !== 'string') {
      return '';
    }
    return String(declared === undefined || declared === null ? '' : declared).trim();
  }

  function runEntry(source, target, name, payload) {
    if (!name || !target[name]) {
      return Promise.resolve(failure('unknown-action', '看板未注册动作：' + (name || '(空)')));
    }
    var entry = target[name];
    if (typeof entry.run !== 'function') {
      return Promise.resolve(failure('no-handler', '动作缺少可执行实现：' + name));
    }
    // 卡片事件的唯一出口：progress / completed / failed 都经这里。
    // silent 条目（纯看板内同步：刷新数据、刷新某个页面）只跑动作，
    // 不在会话里留「xxx 已完成」；其余动作（解析等长任务）照旧出卡。
    function publishTaskCard(eventName, extra) {
      if (entry.silent === true) return;
      if ([EVENT.TASK_PROGRESS, EVENT.TASK_COMPLETED, EVENT.TASK_PARTIAL,
           EVENT.TASK_BLOCKED, EVENT.TASK_FAILED].indexOf(eventName) < 0) return;
      publish(eventName, name, extra);
    }
    // 「这一次执行」的唯一标识：同一次执行只出一次回声，两次执行各自再出一次。
    // 父壳据此去重，绝不按项目 / 动作名去重（否则第二次执行就静默了）。
    var runId = nextRequestId('run');
    var progressTaskId = entry.taskIdPerRun === true ? runId : (context.taskId || '');
    publishTaskCard(EVENT.TASK_PROGRESS, { action: name, phase: 'start',
      label: entryState(name).label, taskId: progressTaskId,
      runId: runId, prompt: resolveActionPrompt(entry, payload),
      progress: entry.startMessage == null ? '' : String(entry.startMessage) });
    var outcome;
    try {
      outcome = entry.run(payload || {}, { runId: runId, taskId: progressTaskId });
    } catch (error) {
      publishTaskCard(EVENT.TASK_FAILED, { action: name,
        label: entryState(name).label, taskId: progressTaskId,
        code: String((error && error.code) || 'action-failed'),
        message: String((error && error.message) || error) });
      return Promise.resolve(failure('action-failed', String((error && error.message) || error)));
    }
    return Promise.resolve(outcome).then(function (result) {
      // 业务函数自己回结构化失败时原样透传，不伪装成功。
      if (result && typeof result === 'object' && result.ok === false) {
        var reason = (result.error && result.error.message) || ('动作执行失败：' + name);
        publishTaskCard(EVENT.TASK_FAILED, { action: name,
          label: entryState(name).label, taskId: progressTaskId,
          // 只转发失败码，不在运行时判定「要不要出卡」：父壳据 code 跳过预期内失败。
          code: String((result.error && result.error.code) || 'action-failed'),
          message: reason });
        return result.error ? result : failure('action-failed', '动作执行失败：' + name);
      }
      if (source === 'view') currentView = name;
      if (entry.keepActionState !== true) publish(EVENT.ACTION_STATE, name);
      // deferred 条目只负责启动：回执照旧成功，但不在这里发 task-completed ——
      // 后台任务还没跑完，抢发完成会让父壳以为长任务瞬间结束。真正的收尾
      // 由本页在任务结束时自己经 publishTaskCard 上报（silent 条目照旧不出卡）。
      if (entry.deferred === true) return ok(name, result);
      publishTaskCard(EVENT.TASK_COMPLETED, { action: name,
        label: entryState(name).label, taskId: progressTaskId });
      return ok(name, result);
    }, function (error) {
      publishTaskCard(EVENT.TASK_FAILED, { action: name,
        label: entryState(name).label, taskId: progressTaskId,
        code: String((error && error.code) || 'action-failed'),
        message: String((error && error.message) || error) });
      return failure('action-failed', String((error && error.message) || error));
    });
  }

  function dispatch(data) {
    var name = String(data.name || '');
    var payload = data.payload || {};
    if (name === 'execute-action') {
      return runEntry('action', actions, String(payload.name || payload.action || ''), payload);
    }
    if (name === 'navigate-view') {
      return runEntry('view', views, String(payload.view || payload.name || ''), payload);
    }
    if (name === 'refresh-data') {
      var refreshName = String(payload.action || 'refreshData');
      return runEntry('refresh', actions, refreshName, payload).then(function (result) {
        if (!result.ok && result.error && result.error.code === 'unknown-action') {
          result.error.message = '当前看板未注册 refreshData，无法刷新数据。';
        }
        return result;
      });
    }
    if (name === 'select-part') {
      return runEntry('selection', actions, String(payload.action || 'selectPart'), payload).then(function (result) {
        if (result.ok) publish(EVENT.SELECTION_CHANGED, 'select-part', { selection: payload });
        return result;
      });
    }
    if (name === 'sync-state') {
      return Promise.resolve(ok('sync-state', {
        actions: actionSnapshot(), view: viewSnapshot(), stage: context.stage,
        projectId: context.projectId, taskId: context.taskId,
      }));
    }
    return Promise.resolve(failure('unknown-command', '未知命令：' + (name || '(空)')));
  }

  function reply(requestId, name, result) {
    post(envelope('result', name, result, requestId));
  }

  function onMessage(event) {
    // 只接受同源、且来源就是本页父窗口的消息。
    if (event.origin !== location.origin) return;
    if (!parentWindow || event.source !== parentWindow) return;
    var data = event.data;
    if (!data || typeof data !== 'object') return;
    if (data.namespace !== NAMESPACE || Number(data.version) !== VERSION) return;
    if (data.type !== 'command') return;
    var requestId = String(data.requestId || '');
    if (!requestId) return;
    // 父壳声明的项目 / 阶段必须与本页一致，避免串台执行别人的业务动作。
    if (data.projectId && context.projectId && String(data.projectId) !== String(context.projectId)) {
      reply(requestId, data.name, failure('context-mismatch', 'projectId 与本页不一致，已拒绝执行。'));
      return;
    }
    if (data.stage && context.stage && String(data.stage) !== String(context.stage)) {
      reply(requestId, data.name, failure('context-mismatch', 'stage 与本页不一致，已拒绝执行。'));
      return;
    }
    if (data.stage && STAGES.indexOf(String(data.stage)) === -1) {
      reply(requestId, data.name, failure('context-mismatch', 'stage 不在阶段白名单内。'));
      return;
    }
    dispatch(data).then(
      function (result) { reply(requestId, String(data.name || ''), result); },
      function (error) {
        reply(requestId, String(data.name || ''), failure('action-failed', String((error && error.message) || error)));
      });
  }

  var api = {
    refreshState: function () { return refreshState(); },
    namespace: NAMESPACE,
    version: VERSION,
    context: context,
    registerActions: registerActions,
    auditPrimary: primaryAudit,
    primaryDiagnostics: primaryDiagnostics,
    registerViews: registerViews,
    updateActionState: updateActionState,
    setContext: setContext,
    emit: emit,
    emitState: emitState,
    publish: publish,
    publishStatus: publishStatus,
    setView: function (name) { currentView = String(name || ''); return publish(EVENT.ACTION_STATE, 'view'); },
    snapshot: function () {
      return { actions: actionSnapshot(), view: viewSnapshot(), stage: context.stage, projectId: context.projectId };
    },
    nextRequestId: nextRequestId,
  };

  window.TechBoardRuntime = api;

  window.addEventListener('message', onMessage);
  if (document.readyState === 'complete') sendReady();
  else window.addEventListener('load', sendReady);
})();
