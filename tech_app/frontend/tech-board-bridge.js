/* 技术工艺统一工作台 —— 父壳端看板桥（只跑在 tech-workbench 父页面）。
 *
 * 职责：
 *   · 向当前 iframe 看板发送业务命令（execute-action / navigate-view / refresh-data /
 *     select-part / sync-state），并接收看板的 ready 与动作状态；
 *   · 用 requestId 关联每个命令的成功与失败，带合理超时；
 *   · 校验 event.origin、event.source 必须是当前 techStageFrame.contentWindow，
 *     并校验消息里的 projectId / stage 与父壳当前上下文一致；
 *   · 不认识任何子页面按钮 id 或 CSS selector，也绝不读取 iframe 内部 DOM。
 *
 * 父壳只通过 frame.contentWindow 发消息；读被禁。
 */
(function () {
  'use strict';

  var NAMESPACE = 'cpq:tech-board';
  var VERSION = 1;
  var STAGES = [
    'requirement-create', 'requirement-confirm', 'requirement-review', 'drawing',
    'process', 'cost', 'summary', 'report-review', 'report-publish',
  ];
  var DEFAULT_TIMEOUT = 20000;
  // 看板可以主动推送的标准状态 / 事件名（与 tech-board-runtime.js 同一份协议）：
  // ready / action-state / task-progress / task-completed / task-failed / selection-changed。
  // 这里只登记名字并把整条 payload 透传给订阅者，父壳不解析业务细节。
  var STATE_EVENTS = {
    READY: 'ready',
    ACTION_STATE: 'action-state',
    TASK_PROGRESS: 'task-progress',
    TASK_COMPLETED: 'task-completed',
    TASK_FAILED: 'task-failed',
    SELECTION_CHANGED: 'selection-changed',
    BOARD_STATUS: 'board-status',
    DIRTY_STATE: 'dirty-state',
    LEAVE_APPROVED: 'leave-approved',
  };
  // 父壳 → 看板的全部命令名（既有 5 条 + 批次 8 的离开协议 3 条）。
  var COMMANDS = [
    'execute-action', 'navigate-view', 'refresh-data', 'select-part', 'sync-state',
    'request-leave', 'save-draft', 'discard',
  ];
  var PROTOCOL = {
    commands: COMMANDS.slice(),
    events: Object.keys(STATE_EVENTS).map(function (key) { return STATE_EVENTS[key]; }),
  };

  var frame = null;
  var context = { projectId: '', stage: '', taskId: '' };
  var pending = Object.create(null);
  var listeners = [];
  var seq = 0;
  var state = {
    attached: false,
    ready: false,
    stage: '',
    projectId: '',
    actions: {},
    view: { active: '' },
    lastEvent: null,
    error: '',
    dirty: false,          // 看板里有没有还没保存的修改（批次 8）
  };
  var leaveHandshake = null;   // 进行中的离开握手 { reason, resolve, settled, timer }
  var leaveApproval = false;   // 看板自己发来、还没用掉的一次性 leave-approved

  function clone(value) {
    try { return JSON.parse(JSON.stringify(value == null ? {} : value)); }
    catch (error) { return {}; }
  }

  function nextRequestId() {
    seq += 1;
    return 'wb-' + seq + '-' + Date.now();
  }

  function frameWindow() {
    if (!frame) return null;
    // 真实浏览器里看板是 iframe，消息要发给它的 window；同源 window 替身（测试 / 直接
    // 传入 window）没有 contentWindow 时退化为 frame 本身，不要因此把命令全吞掉。
    return frame.contentWindow || frame;
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

  // 绝不使用 "*" 作为 targetOrigin。
  function post(message) {
    var target = frameWindow();
    if (!target) return false;
    target.postMessage(message, location.origin);
    return true;
  }

  function notify(event) {
    state.lastEvent = event.type;
    listeners.slice().forEach(function (listener) {
      try { listener(event); } catch (error) { /* 单个订阅者异常不影响其它订阅者 */ }
    });
  }

  function failPending(code, message) {
    Object.keys(pending).forEach(function (requestId) {
      var entry = pending[requestId];
      delete pending[requestId];
      if (entry && entry.timer) clearTimeout(entry.timer);
      if (entry) {
        var cancelled = Object.assign(new Error(message), { code: code, requestId: requestId });
        if (isQuietFailure(cancelled)) cancelled.quiet = true;
        entry.reject(cancelled);
      }
    });
  }

  /* 「预期内失败」：由用户 / 流程自己造成、且看板已经就地给出提示的失败。这类失败不再
     进入左侧会话（既不留 ⚠ 提示，也不留失败卡），否则每次切步骤都会刷一片噪音：
       detached            看板切换导致的在途命令取消（用户主动切走，不是故障）
       note-target-missing 带入意见时当前视图没有对应输入框（或意见为空）
       missing-comment     必填意见 / 说明未填写（看板自己已 toast）
       no-selection        审核意见未选择（看板自己已 toast）
     其余失败（超时、未就绪、后端失败、action-failed…）照旧四处可见，不能被这里吞掉。 */
  var QUIET_FAILURE_CODES = ['detached', 'note-target-missing', 'missing-comment', 'no-selection'];
  function isQuietFailure(error) {
    if (!error) return false;
    if (error.quiet === true) return true;
    return QUIET_FAILURE_CODES.indexOf(String(error.code || '')) >= 0;
  }

  /* ---------------------------------------------------- 未保存修改的离开协议（批次 8）
   * 看板把「有未保存修改」通过 dirty-state 报上来，父壳在五个导航出口前统一问一次：
   * 取消 → 留在原地（quiet）；保存 / 放弃 → 发对应命令，**等看板广播 dirty-state{false}
   * 才放行**（save-draft 回了 ok 不算确认）；看板拒绝 / 超时 → 不放行且给一条非 quiet
   * 的失败（不能静默丢改动）；握手期间看板被切走 → 不放行、错误码 detached（quiet）。 */
  function settleLeave(handshake, allowed) {
    if (!handshake || handshake.settled) return;
    handshake.settled = true;
    if (handshake.timer) { clearTimeout(handshake.timer); handshake.timer = null; }
    if (leaveHandshake === handshake) leaveHandshake = null;
    handshake.resolve(Boolean(allowed));
  }

  function voidLeaveHandshake() {
    if (leaveHandshake) settleLeave(leaveHandshake, false);
  }

  function setDirtyValue(dirty) {
    state.dirty = Boolean(dirty);
  }

  /* 看板送上来的未保存标记：任何一种 source 都要以 payload 为准；
     payload 缺 dirty 字段时按 true 处理（宁可多问一句，不可静默丢改动）。 */
  function applyDirtyEvent(payload) {
    var body = payload || {};
    var dirty = body.dirty === undefined ? true : Boolean(body.dirty);
    setDirtyValue(dirty);
    if (dirty) { voidLeaveHandshake(); return; }
    if (leaveHandshake) settleLeave(leaveHandshake, true);
  }

  /* 父壳侧主动标脏（典型来源：Agent 自动回填）。握手期间再标脏 → 本次放行作废。 */
  function markDirty(reason, source) {
    setDirtyValue(true);
    voidLeaveHandshake();
    notify({ type: 'dirty-state', name: 'dirty-state',
             payload: { dirty: true, reason: reason || '', source: source || 'agent' } });
    return state.dirty;
  }

  function shouldWarnOnUnload() {
    return Boolean(frame) && state.dirty === true;
  }

  function guardLeave(reason, opts) {
    var options = opts || {};
    var label = String(reason || '');
    var timeout = Number(options.timeout) > 0 ? Number(options.timeout) : DEFAULT_TIMEOUT;
    if (!frame) return Promise.resolve(true);                    // 未 attach：纯查看页面不误拦
    if (!state.dirty && !leaveApproval) return Promise.resolve(true);  // 干净：不发 request-leave
    if (leaveHandshake && leaveHandshake.reason === label) return leaveHandshake.promise;  // 在途去重
    leaveApproval = false;                                       // 一次性批准：用掉即失效
    var handshake = { reason: label, settled: false, timer: null };
    handshake.promise = new Promise(function (resolve) { handshake.resolve = resolve; });
    leaveHandshake = handshake;
    var target = String(options.target || '');
    send('request-leave', { reason: label, target: target },
         { label: '确认未保存的修改', timeout: timeout }).then(function (reply) {
      if (handshake.settled) return;
      var decision = String((((reply || {}).result) || {}).decision || '');
      if (decision === 'cancel') { settleLeave(handshake, false); return; }
      if (decision !== 'save' && decision !== 'discard') { settleLeave(handshake, false); return; }
      // 保存 / 放弃都要等看板广播 dirty-state{false}：命令的 ok 回复不是放行依据。
      handshake.timer = setTimeout(function () {
        if (handshake.settled) return;
        state.error = '未确认看板是否已保存，已停在当前步骤。';
        notify({ type: 'error', name: 'request-leave',
                 payload: { message: state.error, code: 'timeout' } });
        settleLeave(handshake, false);
      }, timeout);
      send(decision === 'save' ? 'save-draft' : 'discard', { reason: label },
           { label: decision === 'save' ? '保存草稿' : '放弃修改', timeout: timeout }).then(
        function () { /* 放行的唯一判据是此刻 state.dirty === false */ },
        function () { settleLeave(handshake, false); });
    }, function () {
      settleLeave(handshake, false);   // ok:false / 超时：send 已经发过非 quiet 失败
    });
    return handshake.promise;
  }

  function send(name, payload, options) {
    var opts = options || {};
    var target = frameWindow();
    if (!target) {
      return Promise.reject(Object.assign(new Error('看板尚未就绪，请等待右侧步骤加载完成。'),
        { code: 'not-attached' }));
    }
    var requestId = nextRequestId();
    var timeout = Number(opts.timeout) > 0 ? Number(opts.timeout) : DEFAULT_TIMEOUT;
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () {
        delete pending[requestId];
        state.error = opts.label ? (opts.label + '超时未响应') : '动作超时未响应';
        notify({ type: 'error', name: name, payload: { requestId: requestId, message: state.error, code: 'timeout' } });
        reject(Object.assign(new Error(state.error), { code: 'timeout', requestId: requestId }));
      }, timeout);
      pending[requestId] = { resolve: resolve, reject: reject, timer: timer, name: name };
      if (!post(envelope('command', name, payload, requestId))) {
        clearTimeout(timer);
        delete pending[requestId];
        reject(Object.assign(new Error('看板尚未就绪，请等待右侧步骤加载完成。'), { code: 'not-attached' }));
      }
    });
  }

  function settle(data) {
    var requestId = String(data.requestId || '');
    var entry = pending[requestId];
    if (!entry) return;
    delete pending[requestId];
    if (entry.timer) clearTimeout(entry.timer);
    var payload = data.payload || {};
    if (payload.ok) {
      state.error = '';
      entry.resolve(payload);
    } else {
      var error = payload.error || {};
      var message = String(error.message || '看板执行失败');
      state.error = message;
      notify({ type: 'error', name: String(data.name || ''), payload: payload });
      var failed = Object.assign(new Error(message), {
        code: String(error.code || 'action-failed'), requestId: requestId,
      });
      // 预期内失败打上 quiet：调用方据此不把它写进会话（看板自己已经提示过）。
      if (isQuietFailure(failed)) failed.quiet = true;
      entry.reject(failed);
    }
  }

  function applyState(data) {
    var payload = data.payload || {};
    if (payload.actions && typeof payload.actions === 'object') state.actions = payload.actions;
    if (payload.view && typeof payload.view === 'object') state.view = payload.view;
    if (typeof payload.projectId === 'string' && payload.projectId) state.projectId = payload.projectId;
  }

  function accepts(event) {
    if (!frame) return false;
    if (event.origin !== location.origin) return false;
    // 只接受当前 iframe 发来的消息：别的窗口/父级/扩展都挡在外面。
    if (event.source !== frameWindow()) return false;
    var data = event.data;
    if (!data || typeof data !== 'object') return false;
    if (data.namespace !== NAMESPACE || Number(data.version) !== VERSION) return false;
    // 上下文串台保护：projectId / stage 必须与父壳当前上下文一致。
    if (data.projectId != null && String(data.projectId) !== String(context.projectId || '')) return false;
    if (data.stage != null && String(data.stage) !== String(context.stage || '')) return false;
    if (data.stage != null && data.stage && STAGES.indexOf(String(data.stage)) === -1) return false;
    return true;
  }

  function onMessage(event) {
    if (!accepts(event)) return;
    var data = event.data;
    var type = String(data.type || '');
    if (type === 'result') { settle(data); return; }
    if (type !== 'state') return;
    applyState(data);
    var name = String(data.name || '');
    if (name === STATE_EVENTS.READY) state.ready = true;
    if (name === STATE_EVENTS.TASK_FAILED) {
      state.error = String(((data.payload || {}).message) || '看板任务失败');
    }
    if (name === STATE_EVENTS.DIRTY_STATE) applyDirtyEvent(data.payload);
    if (name === STATE_EVENTS.LEAVE_APPROVED) {
      // 看板自己批准了一次离开：一次性放行，用掉即失效（第二次导航重新被拦）。
      setDirtyValue(false);
      leaveApproval = true;
      if (leaveHandshake) settleLeave(leaveHandshake, true);
    }
    notify({ type: name || 'state', name: name, payload: data.payload || {} });
  }

  window.addEventListener('message', onMessage);
  /* 兼容通道：有些内嵌封装 / 自动化走查派发的事件只带 origin / source / data，没有 type。
     空类型在 DOM 规范里不会被 dispatch，因此这里不会与上面那条标准通道重复处理同一条消息。 */
  try { window.addEventListener('', onMessage); } catch (error) { /* 部分实现不接受空类型 */ }

  function attach(targetFrame, nextContext) {
    if (!targetFrame) return Promise.reject(Object.assign(new Error('缺少看板 iframe。'), { code: 'no-frame' }));
    if (frame && frame !== targetFrame) detach('frame-replaced');
    frame = targetFrame;
    var ctx = nextContext || {};
    context = {
      projectId: ctx.projectId == null ? '' : String(ctx.projectId),
      stage: ctx.stage == null ? '' : String(ctx.stage),
      taskId: ctx.taskId == null ? '' : String(ctx.taskId),
    };
    state.attached = true;
    state.ready = false;
    state.stage = context.stage;
    state.projectId = context.projectId;
    state.actions = {};
    state.view = { active: '' };
    state.error = '';
    // 新看板：未保存标记归零，旧的一次性批准与在途握手一律作废。
    voidLeaveHandshake();
    leaveApproval = false;
    setDirtyValue(false);
    notify({ type: 'attached', name: 'attached', payload: { stage: context.stage } });
    // 子页面可能在本壳 attach 之前就已经 ready：主动要一次状态快照，避免错过。
    return send('sync-state', {}, { label: '同步看板状态', timeout: 8000 }).then(function (payload) {
      state.ready = true;
      applyState({ payload: payload.result || {} });
      notify({ type: 'ready', name: 'ready', payload: state.actions });
      return state;
    }, function (error) {
      state.ready = false;
      state.error = error && error.message ? error.message : '';
      notify({ type: 'error', name: 'sync-state', payload: { message: state.error, code: error && error.code } });
      return state;
    });
  }

  function detach(reason) {
    // 握手期间看板被切走：不放行、quiet，且不再补发 save-draft / discard。
    voidLeaveHandshake();
    leaveApproval = false;
    setDirtyValue(false);
    failPending('detached', '看板已切换，命令已取消。');
    if (frame) frame = null;
    state.attached = false;
    state.ready = false;
    state.actions = {};
    state.view = { active: '' };
    notify({ type: 'detached', name: 'detached', payload: { reason: reason || 'detach' } });
  }

  function requireReady(label) {
    if (!frame) {
      return Promise.reject(Object.assign(new Error('看板尚未就绪：右侧步骤还没有加载完成。'), { code: 'not-attached' }));
    }
    return null;
  }

  function executeAction(name, payload) {
    var blocked = requireReady(name);
    if (blocked) return blocked;
    var label = (payload && payload.label) || name;
    return send('execute-action', Object.assign({ name: name }, payload || {}), { label: label });
  }

  function navigateView(name, payload) {
    var blocked = requireReady(name);
    if (blocked) return blocked;
    return send('navigate-view', Object.assign({ view: name }, payload || {}), { label: name });
  }

  function refreshData(payload) {
    var blocked = requireReady('refresh-data');
    if (blocked) return blocked;
    return send('refresh-data', payload || {}, { label: '刷新数据' });
  }

  function selectPart(partId, payload) {
    var blocked = requireReady('select-part');
    if (blocked) return blocked;
    return send('select-part', Object.assign({ partId: partId }, payload || {}), { label: '选择零件' });
  }

  function subscribe(listener) {
    if (typeof listener !== 'function') return function () {};
    listeners.push(listener);
    return function unsubscribe() {
      var index = listeners.indexOf(listener);
      if (index >= 0) listeners.splice(index, 1);
    };
  }

  function snapshot() {
    return {
      attached: state.attached,
      ready: state.ready,
      stage: state.stage,
      projectId: state.projectId,
      actions: clone(state.actions),
      view: clone(state.view),
      busy: Object.keys(state.actions).some(function (name) { return state.actions[name].busy; }),
      error: state.error,
      lastEvent: state.lastEvent,
      dirty: state.dirty === true,
    };
  }

  function actionState(name) {
    var entry = state.actions[String(name || '')];
    return entry ? clone(entry) : null;
  }

  window.TechBoardBridge = {
    namespace: NAMESPACE,
    version: VERSION,
    PROTOCOL: PROTOCOL,
    STATE_EVENTS: STATE_EVENTS,
    COMMANDS: COMMANDS.slice(),
    QUIET_FAILURE_CODES: QUIET_FAILURE_CODES.slice(),
    attach: attach,
    detach: detach,
    executeAction: executeAction,
    navigateView: navigateView,
    refreshData: refreshData,
    selectPart: selectPart,
    subscribe: subscribe,
    snapshot: snapshot,
    actionState: actionState,
    isQuietFailure: isQuietFailure,
    markDirty: markDirty,
    guardLeave: guardLeave,
    shouldWarnOnUnload: shouldWarnOnUnload,
    isReady: function () { return Boolean(frame) && state.ready; },
  };
})();
