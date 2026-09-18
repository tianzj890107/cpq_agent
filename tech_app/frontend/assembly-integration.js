/* embed=1：本页在技术工艺统一工作台（tech-workbench.html）右侧打开，会话宿主在父壳 techChatPane。 */
const __techEmbedMode__ = new URLSearchParams(location.search).get('embed') === '1';
/*
 * 3 组装与整合（3.1 整合图纸 / 3.2 参数推荐 / 3.3 组装工艺）。
 *
 * 2.1 把一张图纸拆成零件、逐件出工艺与成本；这一页把零件装回一台整机，四个环节：
 *   ① 整合图纸 → ② 参数推荐 → ③ 组装工艺 → ④ 成本测算
 * 顺序是硬的：后一环节拿前一环节的结论当输入，后端也照这个顺序挡（缺参数不给排工艺、
 * 缺工艺不给算成本）。前端这里只是把同样的规则提前告诉人，别让他撞一个 400 才知道。
 *
 * 渲染刻意沿用 2.1 内嵌分析面板的 .inline-* 类：工艺路线和成本明细在两页是同一种东西，
 * 长得不一样只会让人以为算法也不一样。
 */
const aiPid = TechProjectContext.bind().project;
// 本步只剩三个环节：成本测算搬去了 4 成本测算（财务经理的步骤）。
// 整合参数（报价必填项的补全与最终确认）留在本步的「参数推荐」里 ——
// 工艺经理在这里交的是**工艺、参数与用量**，成本的数字不由他给。
const AI_TABS = { drawings: '整合图纸', params: '参数推荐', process: '组装工艺' };
// 【5 阶段口径】带子步骤号的完整名称：3.1 整合图纸 / 3.2 参数推荐 / 3.3 组装工艺。
const AI_TAB_NAMES = { drawings: '3.1 整合图纸', params: '3.2 参数推荐', process: '3.3 组装工艺' };
const AI_TYPE_LABEL = {
  blank: '下料/备料', turning: '车', milling: '铣', drilling: '钻', boring: '镗',
  grinding: '磨', bench: '钳工', sheet_metal: '钣金', welding: '焊接',
  heat_treat: '热处理', surface: '表面处理', assembly: '装配', inspection: '检验', other: '其他',
};
const AI_CAT_LABEL = {
  material: '材料费', labor: '人工费', machining: '加工费用',
  standard_part: '标准件/外购', heat_treat: '热处理',
  surface: '表面处理', welding: '焊接', assembly: '装配', inspection: '检验',
  tooling: '工装摊销', logistics: '物流包装', overhead: '管理费', profit: '利润', other: '其他',
};

let aiData = null;          // 后端 payload：plan / status / *_validation / *_lookup
// 2.1 的零件与它们的单件成本单独放：aiData 会被每次任务结果和 PUT 响应整体替换，
// 混在一起的话零件清单会在第一次生成之后凭空消失。
let aiParts = [];
let aiTab = 'drawings';
let aiBusy = false;
// deferred 长任务（runIntegration / integrationStep）自己的并发闸门：启动即回执，
// 后台链路没结束前不允许再排一条。
let aiDeferredBusy = false;
const aiEditing = { params: false, process: false };

const $ai = id => document.getElementById(id);
const aiUrl = (suffix = '') => `/api/projects/${encodeURIComponent(aiPid)}/integration${suffix}`;
const aiAttr = value => esc(value).replace(/"/g, '&quot;');
const aiMoney = value => value == null ? '—'
  : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
const aiSleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const aiPlan = () => aiData?.plan || {};
// 两个「这一步做到哪了」的共享判定：左侧动作的可见性 / role 都由它们推导，
// 不许各处各写一份（写散了就会出现「参数已经生成、按钮却还是生成」这类漂移）。
const aiHasParams = () => Boolean(aiData?.status?.has_params);
const aiHasProcess = () => Boolean(aiData?.status?.has_process);

/* 页签切换 + 重渲染。原先它只在 aiRegisterTechBoardActions 的闭包里用 const 声明，
   而「确认图纸并进入参数推荐」「确认并进入下一页签」这两个收口函数定义在外层，
   调用它当场就是 ReferenceError「aiSetTab is not defined」。切页签是这一页的基础
   设施，挂在模块作用域，视图注册与业务函数共用同一个。 */
function aiSetTab(name) { aiTab = name; aiRender(); }

/* 同一处病灶的另外两个助手：「确认图纸并进入参数推荐」在闭包外调用 aiAnalyzed()，
   留在闭包里同样一跑就 ReferenceError。它们只读看板手里那份 payload，不额外发请求。 */
function aiStatusState() {
  return (aiData && aiData.status) || {};
}
function aiAnalyzed() {
  const state = aiStatusState();
  return Boolean(state.has_params && state.has_process);
}

// --------------------------------------------------------------------------- 对话区
/* 一次用户触发 = 一个 turn 容器：容器里先放用户气泡，紧随其后的执行卡 / 说明也落进来，
   两者因此永远是「我：… → Agent 卡」的成对顺序。容器在 CSS 里是 display:contents，
   视觉顺序与 DOM 顺序一致，不多出一层盒子；历史回放与系统提示仍直接追加。 */
let boardTurn = null;
function aiThreadAppend(html) {
  $ai('aiEmpty')?.remove();
  const node = document.createElement('div');
  node.innerHTML = html;
  const element = node.firstElementChild;
  const host = (!aiReplaying && boardTurn) ? boardTurn : $ai('aiTinner');
  host.append(element);
  $ai('aiThread').scrollTop = $ai('aiThread').scrollHeight;
  return element;
}

function aiSay(text) {
  aiThreadAppend(`<div class="oc-amsg" data-agent-card="" data-status="completed">
    <div class="oc-abody" data-agent-role="body"><div class="oc-atxt">${esc(text)}</div></div></div>`);
  aiTimelineNote(text);
}

/* 用户气泡原语：本页所有「用户点按钮 → 会话区出卡」的入口都先经过它（唯一 turn helper
   的可见部分），返回本轮 turn 容器，后续生成的执行卡就长在里面。 */
function aiUserSay(text) {
  $ai('aiEmpty')?.remove();
  const turn = document.createElement('div');
  turn.className = 'oc-turn';
  turn.setAttribute('data-agent-card', '');
  turn.setAttribute('data-status', 'completed');
  const bubble = document.createElement('div');
  bubble.className = 'oc-ubub';
  bubble.textContent = String(text == null ? '' : text);
  turn.append(bubble);
  $ai('aiTinner').append(turn);
  $ai('aiThread').scrollTop = $ai('aiThread').scrollHeight;
  boardTurn = turn;
  aiTimelineNote(text);
  return bubble;
}

/* --------------------------------------------- 会话时间线（项目级，按顺序持久化）
   过程文字既写本地线程（可见效果不变），也以 session-note / source=board 落库；重进项目
   或重载 iframe 后用 GET /agent/events 取回本阶段那几条回放进 #aiTinner。排序 / 去重
   复用父壳同一份 tech-session-timeline.js，两个出口不各写一套顺序规则。 */
let aiReplaying = false;
function aiTimelineNote(text) {
  const value = String(text || '').trim();
  if (!value || aiReplaying || !aiPid) return;
  Promise.resolve()
    .then(() => api(`/api/projects/${encodeURIComponent(aiPid)}/agent/event`, {
      method: 'POST',
      body: JSON.stringify({ kind: 'session-note', source: 'board', stage: 'process',
        text: value, key: `process:${value}` }),
    }))
    .catch(() => { /* 落库失败不影响本地可见性 */ });
}
function aiReplayTimeline() {
  if (!aiPid) return Promise.resolve();
  return Promise.resolve()
    .then(() => api(`/api/projects/${encodeURIComponent(aiPid)}/agent/events?stage=process&source=board`))
    .then((payload) => {
      const rows = (window.TechSessionTimeline && window.TechSessionTimeline.forStage)
        ? window.TechSessionTimeline.forStage({ stage: 'process', events: (payload && payload.events) || [] })
        : [];
      aiReplaying = true;
      try { rows.forEach(row => { if (row && row.text) aiSay(String(row.text)); }); }
      finally { aiReplaying = false; }
    })
    .catch(() => { /* 时间线读不到不影响本轮渲染 */ });
}

/** 处理过程卡：任务进度逐条落在这里，和 2.1 的 Agent 对话框一个样子。 */
function aiProcessCard(title) {
  // 与 2.1 的 Agent 回复是同一张卡、同一个状态 chip：不再有第二套过程卡样式。
  const card = aiThreadAppend(`<div class="oc-amsg" data-agent-card="" data-status="running">
    <div class="oc-abody" data-agent-role="body"><div class="oc-alabel" data-agent-role="header">`
      + `<span data-agent-role="identity">技术工艺智能体</span>`
      + `<span class="oc-alabel-sub" data-agent-role="title">${esc(title)}</span>`
      + `<span class="oc-alabel-state is-running" data-agent-role="status">◌ 运行中</span></div>
      <div class="oc-process-steps" data-agent-role="tools"></div></div></div>`);
  const steps = card.querySelector('.oc-process-steps');
  const seen = new Set();
  return {
    log(lines) {
      for (const line of lines) {
        if (seen.has(line)) continue;
        seen.add(line);
        const sub = line.startsWith('  ');
        // 一条业务过程 = 一个 Tool Item（保留整句原话，不概括）。
        steps.insertAdjacentHTML('beforeend',
          `<div class="oc-process-step${sub ? ' sub' : ''}" data-agent-role="tool-item" data-state="completed">`
          + `<span class="oc-process-dot" data-agent-role="tool-item">${sub ? '·' : '●'}</span>`
          + `<span class="oc-process-text" data-agent-role="tool-title">${esc(line.trim())}</span></div>`);
      }
      $ai('aiThread').scrollTop = $ai('aiThread').scrollHeight;
    },
    done(ok, message) {
      const state = card.querySelector('.oc-alabel-state');
      // 中断沿用进行中的蓝色 chip：同一张卡、同一个 chip 就地翻转，不新增第二行。
      const interrupted = ok === 'interrupted';
      card.setAttribute('data-status', interrupted ? 'interrupted' : ok ? 'completed' : 'failed');
      state.className = `oc-alabel-state ${interrupted ? 'is-interrupted' : ok ? 'is-succeeded' : 'is-failed'}`;
      state.textContent = interrupted ? '⏸ 中断' : ok ? '✓ 已完成' : '⚠ 失败';
      if (message) this.log([`  ${message}`]);
    },
  };
}

// --------------------------------------------------------------------------- 状态与提示
function aiStatus(message, error = false) {
  const badge = $ai('status');
  if (badge && !badge.dataset.fatal) badge.textContent = message;
  const panel = $ai('aiPanelStatus');
  panel.textContent = message || '';
  panel.classList.toggle('error', error);
  // 与本地显示的同一段文字原样上报；独立打开（无运行时）时不通信。
  const runtime = window.TechBoardRuntime;
  if (runtime && typeof runtime.publishStatus === 'function') runtime.publishStatus(message || '', error ? 'error' : 'info');
}

function aiToast(message, error = false) {
  document.querySelectorAll('.ai-toast').forEach(node => node.remove());
  const el = document.createElement('div');
  el.className = `ai-toast${error ? ' error' : ''}`;
  el.textContent = message;
  document.body.append(el);
  setTimeout(() => el.remove(), 3200);
}

/* 关键失败必须留在页面上（批次 9 §7.5）：toast 只留给成功提示与安静失败。
   code 决定文案与影响（task-failed / interrupted / permission_denied…），
   trace_id 是这次调用的错误追踪 ID，用户报障时与线上日志逐字对得上。 */
function aiShowFailure(code, message, traceId) {
  if (window.TechFailure && typeof window.TechFailure.show === 'function') {
    window.TechFailure.show({ code: code, message: message, stage: '3 组装与整合',
                              trace_id: traceId || '' });
    return true;
  }
  return false;
}

/** 这一环节能不能开跑。返回空串表示可以，否则是拦下来的理由。 */
function aiBlocker(tab) {
  const state = aiData?.status || {};
  if (tab === 'params') return '';
  if (tab === 'process' && !state.has_params) return '请先完成「参数推荐」：组装工序要按整机 BOM 与连接关系来排。';
  return '';
}

// --------------------------------------------------------------------------- 请求
/** 长任务进度经统一看板协议上报父壳：左侧进度卡按 taskId 去重、日志增量追加。
    独立打开（无父壳 / 无运行时）时不通信，页面照常工作。 */
function aiPublishTask(name, extra) {
  const runtime = window.TechBoardRuntime;
  if (!runtime || typeof runtime.publish !== 'function') return;
  try {
    runtime.publish(name, 'integrationStep',
      Object.assign({ action: 'integrationStep' }, extra || {}));
    aiRememberSettle(name, 'integrationStep', extra);
  } catch { /* 进度上报失败不影响业务本身 */ }
}

// 最近一次收尾事件：长任务包装器据此决定要不要补一条属于本动作名的 task-completed，
// 已经发过同样事件时不再重复，失败时把真实原因原样带过去。
let aiLastSettle = null;
function aiRememberSettle(name, subject, extra) {
  if (name !== 'task-completed' && name !== 'task-failed') return;
  aiLastSettle = { event: name, subject: subject,
                   message: (extra && (extra.error || extra.message)) || '' };
}

// 长任务（deferred）在后台真正结束时由本页自报收尾：主体必须是本动作名，
// 失败必须带真实错误文本（父壳拿来显示，不再只报「超时未响应」）。
function aiSettleTask(event, action, extra) {
  try {
    window.TechBoardRuntime.publish(event, action, Object.assign({ action: action }, extra || {}));
  } catch (error) { /* 独立打开无运行时 */ }
  aiRememberSettle(event, action, extra);
}

// 既有实现（aiPost / aiRunOp）已经就同一动作发过同一收尾事件时不再补发。
function aiSettleIfNeeded(event, action, extra) {
  if (aiLastSettle && aiLastSettle.event === event && aiLastSettle.subject === action) return;
  aiSettleTask(event, action, extra);
}

// 后台链路放在注册表外：动作条目只启动它并秒级回执（deferred），整合分析
// （参数推荐 + 组装工艺两轮模型调用）跑完后再由这里推 task-completed / task-failed。
async function aiRunIntegrationInBackground() {
  try {
    await aiRunAll();
    if (aiLastSettle && aiLastSettle.event === 'task-failed') {
      aiSettleIfNeeded('task-failed', 'runIntegration',
        { message: aiLastSettle.message || '整合分析未完成，请查看右侧看板提示。' });
    } else {
      aiSettleIfNeeded('task-completed', 'runIntegration');
    }
  } catch (error) {
    aiSettleIfNeeded('task-failed', 'runIntegration',
      { message: (error && error.message) || '整合分析失败，请查看右侧看板提示。' });
  } finally {
    aiDeferredBusy = false;
    window.TechBoardRuntime.updateActionState('runIntegration', { busy: false });
  }
}

// 单个环节（参数推荐 / 组装工艺 / 成本测算）的后台链路，同上：只启动，跑完自报。
async function aiIntegrationStepInBackground(step, options) {
  try {
    const result = await aiGenerate(step, options);
    if (result && result.ok === false) {
      aiSettleIfNeeded('task-failed', 'integrationStep',
        { message: (result.error && result.error.message) || '整合环节未完成，请查看右侧看板提示。' });
    } else {
      aiSettleIfNeeded('task-completed', 'integrationStep');
    }
  } catch (error) {
    aiSettleIfNeeded('task-failed', 'integrationStep',
      { message: (error && error.message) || '整合环节失败，请查看右侧看板提示。' });
  } finally {
    aiDeferredBusy = false;
    window.TechBoardRuntime.updateActionState('integrationStep', { busy: false });
  }
}

/** 中断与失败要分开：服务重启 / 桥超时把在途任务打断是「中断」（蓝色 chip），不是失败。 */
function aiTaskInterrupted(error) {
  return String((error && error.code) || '') === 'interrupted';
}

async function aiPost(path, opts) {
  // 解构放在函数体里：静态提取「函数体」时不会被参数表里的花括号截断。
  // 参数默认值同样不写成花括号字面量，避免静态提取在参数表处提前收尾。
  opts = opts || {};
  const { form, label, quantity, keepTab } = opts;
  if (aiBusy) return { ok: false, error: { code: 'busy', message: '已有任务在执行，请稍候。' } };
  const blocked = aiBlocker(path);
  if (blocked) { aiToast(blocked, true); return { ok: false, error: { code: 'blocked', message: blocked } }; }
  aiBusy = true;
  aiRenderActions();
  const title = label || AI_TAB_NAMES[path] || AI_TABS[path] || path;
  aiStatus(`${title}生成中…`);
  // 用户点按钮触发：先出用户气泡，再出执行卡（唯一 turn helper，隔离执行时安全跳过）。
  if (typeof aiUserSay === "function") aiUserSay(`开始${title}。`);
  const card = aiProcessCard(title);
  let taskId = '';
  try {
    const query = quantity ? `?quantity=${quantity}` : '';
    const submitted = await api(aiUrl(`/${path}${query}`), { method: 'POST', body: form || new FormData() });
    taskId = String(submitted.task_id || '');
    aiPublishTask('task-progress', { taskId: taskId, label: title, status: 'running',
                                     progress: `${title}已提交，正在生成…` });
    aiData = await aiPollTask(taskId, card, title);
    aiEditing[path] = false;
    card.done(true);
    aiStatus(`${title}已完成`);
    aiPublishTask('task-completed', { taskId: taskId, label: title, status: 'succeeded' });
    if (!keepTab) aiTab = path;
    aiRender();
    return { ok: true };
  } catch (error) {
    const message = error.message || `${title}失败`;
    const interrupted = aiTaskInterrupted(error);
    card.done(interrupted ? 'interrupted' : false, message);
    aiStatus(interrupted ? `${title}已中断：${message}` : `${title}失败：${message}`, !interrupted);
    aiToast(message, true);
    // 中断也是关键失败：常驻块要出来（toast 会消失，用户回头看不到原因）。
    aiShowFailure(interrupted ? 'interrupted' : 'task-failed', message, error.trace_id);
    aiPublishTask('task-failed', { taskId: taskId, label: title,
                                   status: interrupted ? 'interrupted' : 'failed',
                                   code: interrupted ? 'interrupted' : 'task-failed',
                                   error: message });
    return { ok: false, error: { code: interrupted ? 'interrupted' : 'task-failed', message: message } };
  } finally {
    aiBusy = false;
    aiRenderActions();
  }
}

/* 轮询收口到 TechTaskWatch（批次 9 §7.4）：一次网络抖动只计数、不判失败；
   连续不可达降级成「连接不稳定，结果仍在处理中」并放慢间隔继续等；刷新后仍能按
   task_id 复原。状态词表与中文口径也由它一处给出。 */
async function aiPollTask(taskId, card, label) {
  if (!window.TechTaskWatch || typeof window.TechTaskWatch.watch !== 'function') {
    throw new Error('缺少 tech-task-watch.js：无法跟踪长任务状态');
  }
  const handle = window.TechTaskWatch.watch({
    projectId: aiPid,
    taskId: taskId,
    intervalMs: 1200,
    onProgress: task => {
      const record = task || {};
      const log = Array.isArray(record.progress_log) ? record.progress_log : [];
      card.log(log);
      // progress_log 只增量追加：同一 taskId 的进度卡不会被后来的快照覆盖掉中间步骤。
      aiPublishTask('task-progress', { taskId: taskId, label: label || '整合分析',
                                       status: record.status === 'interrupted' ? 'interrupted' : 'running',
                                       log: log,
                                       process: Array.isArray(record.process_log) ? record.process_log : null });
    },
    onDegraded: () => {
      aiStatus('连接不稳定，结果仍在处理中');
      aiPublishTask('task-progress', { taskId: taskId, label: label || '整合分析',
                                       status: 'running',
                                       progress: '连接不稳定，结果仍在处理中' });
    },
  });
  try {
    const settled = await handle.promise;
    return settled.record && settled.record.result;
  } catch (failure) {
    const code = String((failure && failure.code) || '');
    // 服务重启把在途任务打断了：中断是终态，不能继续轮询下去。
    if (code === 'interrupted') {
      throw Object.assign(new Error((failure && failure.message) || '服务重启中断，任务已中止。'),
                          { code: 'interrupted', trace_id: (failure && failure.trace_id) || '' });
    }
    throw Object.assign(new Error((failure && failure.message) || '任务失败'),
                        { code: code || 'task-failed', trace_id: (failure && failure.trace_id) || '' });
  }
}

async function aiPut(path, payload, message) {
  try {
    aiData = await api(aiUrl(path), { method: 'PUT', body: JSON.stringify(payload) });
    aiRender();
    aiStatus(message);
    aiToast(message);
    return true;
  } catch (error) {
    aiStatus(`保存失败：${error.message}`, true);
    aiToast(error.message || '保存失败', true);
    return false;
  }
}

// --------------------------------------------------------------------------- 顶部动作条
function aiRenderActions() {
  const host = $ai('aiActions');
  if (!host) return;
  if (aiTab === 'drawings') {
    host.innerHTML = `<button type="button" class="inline-action" id="aiUploadBtn">上传整合图纸</button>`
      + `<span class="ai-hint">装配图 / 爆炸图 / 接线图都可以，支持多选。它们会作为参数推荐与组装工艺的视觉输入。</span>`;
    $ai('aiUploadBtn').onclick = () => $ai('aiDrawingInput').click();
    return;
  }
  const has = { params: aiData?.status?.has_params, process: aiData?.status?.has_process }[aiTab];
  // 参数推荐这一环节也要有人按下确认：整机参数、连接关系与 BOM 是后面工艺与成本的输入，
  // 没人点过头就往下走，错的口径会一路带到报价。
  // 参数推荐与组装工艺各有一个确认键。组装工艺那个是闸门：确认之后才允许把任务
  // 推给财务经理去 4 成本测算 —— 工序和用量没定稿，算出来的成本没有意义。
  const confirmBtn = aiTab === 'params'
    ? `<button type="button" class="inline-action" id="aiParamsConfirm" ${!has || aiBusy ? 'disabled' : ''}>`
      + `${aiData?.status?.params_confirmed ? '重新确认' : '确认参数推荐'}</button>`
    : aiTab === 'process'
    ? `<button type="button" class="inline-action" id="aiProcessConfirm" ${!has || aiBusy ? 'disabled' : ''}>`
      + `${aiData?.status?.process_confirmed ? '重新确认' : '确认组装工艺'}</button>`
      + `<span class="ai-hint">${aiData?.status?.process_confirmed
          ? '已确认，可以在左边把任务发给财务经理做成本测算。'
          : '确认后才能把任务推给财务经理 —— 工序与用量定稿了，成本才算得准。'}</span>`
    : '';
  const label = `${has ? '重新生成' : '生成'}${AI_TABS[aiTab]}`;
  // 参数表始终可填：它的行是报价字典定死的，模型没推出来的那些正是要人工补的。
  // 藏在「编辑」后面的话，一旦模型一条都没给，按钮连出现的机会都没有。
  const alwaysEditable = aiTab === 'params';
  // 参数推荐（3.2）是本阶段的收口页：智能补全 / 保存补填 / 确认参数已齐都长在这里，
  // 复用既有的 autofill 与 finalize 接口，成本步骤不再承担这份工作。
  const gaps = aiRequiredGaps();
  const final = Boolean(aiData?.status?.params_final);
  const paramsExtra = aiTab === 'params'
    ? `<button type="button" class="inline-action" id="aiParamsAutofill" ${has && !aiBusy ? '' : 'disabled'}>`
      + (aiBusy ? `<span class="parse-spinner" aria-hidden="true"></span><span>智能补全中…</span>` : '✦ 智能补全')
      + `</button>`
      + `<button type="button" class="inline-action save" id="aiParamsFinalSave" ${has && !aiBusy ? '' : 'disabled'}>保存补填</button>`
      + `<button type="button" class="inline-action" id="aiParamsFinalConfirm" ${has && !aiBusy ? '' : 'disabled'}>`
      + `${final ? '重新确认参数已齐' : '确认参数已齐'}</button>`
      + `<span class="ai-hint">已填 ${gaps.required_filled}/${gaps.required_total} 项报价必填，`
      + `还缺 ${gaps.required_missing} 项；${final ? '已最终确认' : '尚未最终确认'}。</span>`
    : '';
  host.innerHTML =
    `<button type="button" class="inline-action" id="aiGenerate" ${aiBusy ? 'disabled aria-busy="true"' : ''}>`
    + (aiBusy ? `<span class="parse-spinner" aria-hidden="true"></span><span>${esc(label)}中…</span>` : esc(label))
    + `</button>`
    + `<button type="button" class="inline-action" id="aiEdit" ${alwaysEditable || !has || aiEditing[aiTab] || aiBusy ? 'hidden' : ''}>编辑</button>`
    + `<button type="button" class="inline-action save" id="aiSave" ${alwaysEditable || aiEditing[aiTab] ? '' : 'hidden'} ${aiBusy ? 'disabled' : ''}>保存参数</button>`
    + confirmBtn
    + paramsExtra
;
  $ai('aiGenerate').onclick = () => aiGenerate(aiTab);
  const paramsConfirm = $ai('aiParamsConfirm');
  if (paramsConfirm) paramsConfirm.onclick = () => aiConfirmStep('params');
  const processConfirm = $ai('aiProcessConfirm');
  if (processConfirm) processConfirm.onclick = () => aiConfirmStep('process');
  const autofill = $ai('aiParamsAutofill');
  if (autofill) autofill.onclick = () => aiParamsAutofill();
  const finalSave = $ai('aiParamsFinalSave');
  if (finalSave) finalSave.onclick = () => aiParamsFinalize(false);
  const finalConfirm = $ai('aiParamsFinalConfirm');
  if (finalConfirm) finalConfirm.onclick = () => aiParamsFinalize(true);
  const edit = $ai('aiEdit');
  if (edit) edit.onclick = () => { aiEditing[aiTab] = true; aiRender(); };
  const save = $ai('aiSave');
  if (save) save.onclick = () => aiSaveEdits(aiTab);
  if (alwaysEditable) save.textContent = '保存参数';
}

function aiGenerate(tab, options) {
  const form = new FormData();
  const note = $ai('aiRequirement')?.value.trim() || '';
  if (note) form.append('note', note);
  return aiPost(tab, Object.assign({ form }, options || {}));
}

// --------------------------------------------------------------------------- 面板：整合图纸
function aiRenderDrawings() {
  const plan = aiPlan();
  const parts = aiParts;
  let html = `<section class="inline-card"><div class="inline-card-title">已上传的整合图纸</div>`;
  if (!(plan.drawings || []).length) {
    html += `<div class="inline-empty">还没有整合图纸。装配图不是必需的 —— 没有它也能按 2.1 的零件推参数，`
      + `但连接关系只能靠零件角色推断，结果会明显更粗。</div>`;
  } else {
    plan.drawings.forEach(drawing => {
      const href = `${aiUrl(`/drawings/${encodeURIComponent(drawing.filename)}`)}`;
      html += `<div class="ai-drawing"><a href="${aiAttr(href)}" target="_blank" rel="noopener">${esc(drawing.filename)}</a>`
        + `<small>${esc(drawing.uploaded_at || '')}${drawing.note ? ` · ${esc(drawing.note)}` : ''}</small>`
        + `<button type="button" class="inline-action" data-ai-drop="${aiAttr(drawing.filename)}">移除</button></div>`;
    });
  }
  html += `</section>`;

  html += `<section class="inline-card"><div class="inline-card-title">来自 2.1 的零件（参数推荐的另一路输入）</div>`;
  if (!parts.length) {
    html += `<div class="inline-warn">⚠ 尚未拿到 2.1 的零件清单，请先完成图纸解析。</div>`;
  } else {
    html += `<div class="inline-totals"><span><strong>${parts.length}</strong>个零件</span>`
      + `<span><strong>${parts.filter(part => part.hasCost).length}</strong>个已测算成本</span></div>`;
    parts.forEach(part => {
      html += `<div class="inline-cov-row ${part.hasCost ? 'reused' : 'missing'}">`
        + `<span class="inline-cov-no">${esc(part.part_id)}</span>`
        + `<span class="inline-cov-name">${esc(part.name)} ×${part.quantity || 1}</span>`
        + `<span class="inline-cov-code${part.hasCost ? '' : ' new'}">`
        + (part.hasCost ? `单件 ${aiMoney(part.unitCost)} 元` : '未测算成本') + `</span></div>`;
    });
    if (parts.some(part => !part.hasCost)) {
      html += `<div class="inline-warn">⚠ 有零件在 2.1 还没做成本测算。整机成本会缺这几项的底价，`
        + `模型只能估 —— 建议先回 2.1 把它们算完。</div>`;
    }
  }
  return html + `</section>`;
}

/** 报价必填完成度：以报价成品参数字典为准（required 且没有值，且不是平台生成项）。
    字典没加载到时退回后端给的 required_missing 计数。 */
function aiRequiredGaps() {
  const fields = ((aiData?.param_checklist?.groups) || [])
    .flatMap(group => group.fields || []);
  const required = fields.filter(field => field.required);
  const pending = required.filter(field => !field.filled && !field.generated);
  return {
    required_total: required.length,
    required_filled: required.filter(field => field.filled).length,
    required_missing: fields.length ? pending.length : (aiData?.status?.required_missing || 0),
    fields: pending,
  };
}

/** 报价成品参数表里的**全部**空格子（不只必填、也不只报价必填）。
    自动补全按它判断要不要跑：模型推得出来的非必填项也一并补上，
    报价那头才不会一列空着。字典没加载到时退回必填缺口，别把补全整条跳过。 */
function aiMissingParamFields() {
  const fields = ((aiData?.param_checklist?.groups) || [])
    .flatMap(group => group.fields || []);
  const missing = fields.filter(field => !field.filled && !field.generated);
  return fields.length ? missing : aiRequiredGaps().fields;
}

/** 「报价必填参数完成度」：缺几项、缺哪些、是否已最终确认。 */
function aiRequiredCard() {
  if (!aiData?.param_checklist) return '';
  const gaps = aiRequiredGaps();
  const final = Boolean(aiData?.status?.params_final);
  let html = `<section class="inline-card"><div class="inline-card-title">报价必填参数完成度</div>`
    + `<div class="inline-totals"><span><strong>${gaps.required_filled}</strong>/${gaps.required_total} 报价必填已填</span>`
    + `<span class="${gaps.required_missing ? 'total' : ''}">还缺 <strong>${gaps.required_missing}</strong> 项</span>`
    + `<span>${final ? '已最终确认' : '尚未最终确认'}</span></div>`;
  if (gaps.required_missing) {
    html += `<div class="inline-warn">⚠ 还有 ${gaps.required_missing} 项报价必填参数没有值：`
      + `${esc(gaps.fields.map(field => field.name || field.code).join('、'))}。`
      + `可以直接在下表「值」列补填，或点上方「智能补全」按 2.1 零件、已排工艺给建议值；`
      + `建议只是建议，逐项核对后点「保存补填」才落库，都齐了再点「确认参数已齐」。</div>`;
  }
  return html + `</section>`;
}

// --------------------------------------------------------------------------- 面板：参数推荐
function aiRenderParams() {
  const params = aiPlan().params;
  if (!params) {
    return `<div class="inline-empty">尚未生成整机参数。点击上方「生成参数推荐」，`
      + `平台会把<strong>你写的整合需求 + 已上传的整合图纸 + 2.1 已确认的零件</strong>一起交给模型，`
      + `按<strong>报价成品参数字典</strong>（亿纬锂能 DA 梳理 · 产品技术参数）逐项推荐整机参数，`
      + `并给出零件间连接关系与整机 BOM。</div>`;
  }
  const editing = true;   // 参数表一直可填，见 aiRenderActions 的 alwaysEditable
  let html = `<section class="inline-card"><div class="inline-card-title">整机概览</div>`;
  html += `<div class="inline-row"><b>整机</b>${esc(params.assembly_name || '—')}</div>`;
  const checklist = aiData?.param_checklist;
  if (checklist) html += `<div class="inline-row"><b>产品族</b>${esc(checklist.family_name)}`
    + `<small class="ai-hint">（决定报价要哪些成品参数）</small></div>`;
  if (params.assembly_category) html += `<div class="inline-row"><b>库内类别</b>${esc(params.assembly_category)}<small class="ai-hint">（用来召回库内组装工艺路线）</small></div>`;
  if (params.material_spec) html += `<div class="inline-row"><b>整机层耗材</b>${esc(params.material_spec)}</div>`;
  if (params.summary) html += `<div class="inline-row"><b>整合思路</b>${esc(params.summary)}</div>`;
  html += `</section>`;

  html += aiRequiredCard();
  html += QuoteParams.card(checklist, editing);
  html += QuoteParams.extraCard(params, checklist, editing);

  html += `<section class="inline-card"><div class="inline-card-title">零件间连接</div>`;
  if (!(params.interfaces || []).length) html += `<div class="inline-hint">未识别出连接关系。</div>`;
  (params.interfaces || []).forEach(row => {
    html += `<div class="inline-step"><div class="inline-sno">⇄</div><div class="inline-sbody">`
      + `<div class="inline-step-title">${esc(row.name)}`
      + (row.method ? `<span class="inline-type">${esc(row.method)}</span>` : '') + `</div>`
      + `<div class="inline-step-grid">`
      + (row.parts?.length ? `<div><span class="inline-key">零件:</span> ${esc(row.parts.join('、'))}</div>` : '')
      + (row.spec ? `<div><span class="inline-key">规格:</span> ${esc(row.spec)}</div>` : '')
      + (row.control ? `<div><span class="inline-key">控制:</span> ${esc(row.control)}</div>` : '')
      + `</div>` + (row.note ? `<div class="inline-dep">${esc(row.note)}</div>` : '')
      + `</div></div>`;
  });
  html += `</section>`;

  html += `<section class="inline-card"><div class="inline-card-title">整机 BOM（单台用量）</div>`
    + `<div class="inline-cost-table-wrap"><table class="inline-cost-table"><thead><tr>`
    + `<th>零件编号</th><th>名称</th><th>用量</th><th>作用</th><th>来源</th></tr></thead><tbody>`;
  (params.part_refs || []).forEach(row => {
    html += `<tr><td>${esc(row.part_id || '（新增）')}</td><td>${esc(row.name)}</td>`
      + `<td class="number">${row.quantity || 1}</td><td class="source">${esc(row.role || '')}</td>`
      + `<td><span class="inline-cat-tag">${esc(row.source || '')}</span></td></tr>`;
  });
  html += `</tbody></table></div></section>`;
  return html + aiOpenQuestionsCard(params.assumptions, params.open_questions);
}

/** 确认某个环节。参数推荐缺口不拦（型号未定的方案也要能往下走），但要说出来；
    组装工艺的确认是把任务推给财务的前提。 */
async function aiConfirmStep(step) {
  if (aiBusy) return;
  aiBusy = true;
  aiRenderActions();
  const label = AI_TABS[step];
  const confirmTask = `confirm-${step}`;
  aiPublishTask('task-progress', { taskId: confirmTask, label: `${label}确认`,
                                   progress: `正在确认${label}…` });
  try {
    aiData = await api(aiUrl(`/${step}/confirm`), { method: 'POST' });
    const missing = aiData?.status?.required_missing || 0;
    aiStatus(`${label}已确认`);
    aiToast(`${label}已确认`);
    aiPublishTask('task-completed', { taskId: confirmTask, label: `${label}确认`,
                                      status: 'succeeded' });
    if (step === 'process') {
      aiSay('组装工艺已确认。现在可以点左边「确认工艺并发送至财务做成本测算」，'
        + '把任务交给财务经理 —— 他会在 4 成本测算逐件算零件成本与组装成本。');
    } else {
      aiSay(missing
        ? `参数推荐已确认。注意还有 ${missing} 项报价必填的成品参数没有值 —— `
          + `请在本页签点「智能补全」或直接在表里补填，都齐了再点「确认参数已齐」。`
        : '参数推荐已确认，报价必填项也齐了。可以继续排组装工艺了。');
    }
  } catch (error) {
    aiStatus(`确认失败：${error.message}`, true);
    aiToast(error.message || '确认失败', true);
    aiPublishTask('task-failed', { taskId: confirmTask, label: `${label}确认`,
                                   status: 'failed', error: error.message || '确认失败' });
  } finally {
    aiBusy = false;
    aiRender();
  }
}

/** 第 3 阶段 · 3.2 参数推荐：智能补全。只给还缺的报价必填项出**建议值**，不落库。
    复用既有 POST /integration/params/autofill 与任务轮询，不新增第二套推荐逻辑；
    建议值经 QuoteParams.applyFills() 回填到当前参数表，人工核对后保存才会写进模型。 */
async function aiParamsAutofill() {
  if (aiBusy) return;
  if (!aiData?.status?.has_params) { aiToast('请先生成参数推荐', true); return; }
  aiBusy = true;
  aiRenderActions();
  aiStatus('智能补全中…');
  // 用户点按钮触发：先出用户气泡，再出执行卡。
  if (typeof aiUserSay === "function") aiUserSay('按库内规则补全缺失的参数。');
  const card = aiProcessCard('参数推荐 · 智能补全');
  let taskId = '';
  try {
    const form = new FormData();
    const note = $ai('aiRequirement')?.value.trim() || '';
    if (note) form.append('note', note);
    const submitted = await api(
      `/api/projects/${encodeURIComponent(aiPid)}/integration/params/autofill`,
      { method: 'POST', body: form });
    taskId = String(submitted.task_id || '');
    aiPublishTask('task-progress', { taskId: taskId, label: '智能补全', status: 'running',
                                     progress: '已提交，正在按 2.1 零件与库内规则推缺失项…' });
    const result = await aiPollTask(taskId, card, '智能补全');
    const fills = result?.fills || [];
    const unresolved = result?.unresolved || [];
    aiBusy = false;
    aiRender();                     // 先把表画回来，再往输入框里填
    const applied = QuoteParams.applyFills(fills);
    card.done(true);
    aiStatus(`智能补全给出 ${applied} 项建议`);
    aiPublishTask('task-completed', { taskId: taskId, label: '智能补全', status: 'succeeded' });
    aiSay(applied
      ? `已为 ${applied} 项参数填入建议值（表格里标了「AI 建议」）。`
        + `**这是建议不是结论** —— 请逐项核对后再确认。`
        + (unresolved.length ? `\n另有 ${unresolved.length} 项确实推不出来：${unresolved.join('、')}。` : '')
      : `没有可以推出来的参数${unresolved.length ? `：${unresolved.join('、')} 都需要人工确定。` : '。'}`);
    // 调用方（生成参数推荐链路）据此判断要不要把补上的值落库：建议已回填进表格，
    // 但只有真的填了值才值得写一次 finalize。
    return { applied: applied, unresolved: unresolved };
  } catch (error) {
    const message = error.message || '智能补全失败';
    const interrupted = aiTaskInterrupted(error);
    card.done(interrupted ? 'interrupted' : false, message);
    aiStatus(interrupted ? `智能补全已中断：${message}` : `智能补全失败：${message}`, !interrupted);
    aiToast(message, true);
    aiPublishTask('task-failed', { taskId: taskId, label: '智能补全',
                                   status: interrupted ? 'interrupted' : 'failed',
                                   code: interrupted ? 'interrupted' : 'task-failed',
                                   error: message });
  } finally {
    aiBusy = false;
    aiRender();
  }
}

/** 「保存补填」/「确认参数已齐」：把表里的值合进整机参数（finalize），
    confirm=true 时后端校验报价必填项 —— 缺项就退回真实原因，不把缺口带给后面。
    可选第二个参数 waiver 是「仍要继续」那条路：缺口原样留档、人签了字，
    params_final 仍为 false（签字不等于参数已齐），发送财务时同一批缺口凭它复用。 */
async function aiParamsFinalize(confirm, waiver) {
  if (aiBusy) return;
  aiBusy = true;
  aiRenderActions();
  const label = confirm ? '确认参数已齐' : '保存补填';
  const taskId = `params-final-${confirm ? 'confirm' : 'save'}`;
  aiStatus(`${label}…`);
  aiPublishTask('task-progress', { taskId: taskId, label: label, progress: `正在${label}…` });
  try {
    aiData = await api(
      `/api/projects/${encodeURIComponent(aiPid)}/integration/params/finalize`, {
        method: 'POST',
        body: JSON.stringify({ values: QuoteParams.collect(), confirm: !!confirm,
                               waiver: waiver || null }),
      });
    const gaps = aiRequiredGaps();
    aiStatus(confirm ? '参数已最终确认' : '补填已保存');
    aiToast(confirm ? '参数已最终确认' : '已保存');
    aiPublishTask('task-completed', { taskId: taskId, label: label, status: 'succeeded' });
    aiSay(confirm
      ? '参数已最终确认：报价必填的成品参数都齐了，接下来排组装工艺，再把任务发送财务。'
      : `补填已保存${gaps.required_missing ? `，还有 ${gaps.required_missing} 项报价必填参数没有值。` : '，报价必填项已齐。'}`);
  } catch (error) {
    const message = error.message || `${label}失败`;
    aiStatus(`${label}失败：${message}`, true);
    aiToast(message, true);
    aiPublishTask('task-failed', { taskId: taskId, label: label, status: 'failed', error: message });
  } finally {
    aiBusy = false;
    aiRender();
  }
}

/** 「生成之后直接补全」的唯一实现：智能补全 → 把补上的值落库。
    「开始整合分析」与「一键生成参数推荐」两条链路共用它 —— 生成完参数就补，
    用户不必再点一次「智能补全 / 保存补填」。没有空格子时不发请求、不写库。 */
async function aiAutoFillParams() {
  if (!aiHasParams()) return { applied: 0, unresolved: [] };
  if (!aiMissingParamFields().length) return { applied: 0, unresolved: [] };
  const filled = await aiParamsAutofill();
  if (filled && filled.applied > 0) await aiParamsFinalize(false);
  return filled || { applied: 0, unresolved: [] };
}

/** 「生成参数推荐」的完整链路：生成 → 自动补全整张表的空格子 → 把补上的值落库。
    三步全部复用既有实现与既有接口（/integration/params、/params/autofill、/params/finalize），
    不新增第二套推荐逻辑；用户因此不必再点「智能补全 / 保存补填 / 确认参数已齐」。 */
async function aiGenerateParamsFully() {
  const generated = await aiGenerate('params');
  if (!generated || generated.ok === false) {
    return generated || { ok: false, error: { code: 'generate-failed', message: '生成参数推荐失败，请查看右侧看板提示。' } };
  }
  if (!aiHasParams()) {
    return { ok: false, error: { code: 'no-params',
      message: '模型没有产出整机参数，请查看右侧看板提示后重试。' } };
  }
  await aiAutoFillParams();
  const gaps = aiRequiredGaps();
  aiSay(gaps.required_missing
    ? `参数推荐已生成：报价必填 ${gaps.required_filled}/${gaps.required_total} 项有值，`
      + `还缺 ${gaps.required_missing} 项（${gaps.fields.map(field => field.name || field.code).join('、')}）—— `
      + `平台推不出来的这些请在右侧表格「值」列直接补填，补齐后点「确认并进入下一步」。`
    : `参数推荐已生成，报价必填 ${gaps.required_total} 项都齐了。`
      + `在右侧核对无误后，点「确认并进入下一步」继续排组装工艺。`);
  return { ok: true };
}

/** 「确认图纸并进入参数推荐」（整合图纸页的收口动作）：不新增确认接口 —— 先回读
    后端真实状态（GET /integration），整合结果确实在才切到「参数推荐」页签；回读不到或
    还没分析出结果就如实返回原因，不用本地布尔冒充确认完成。 */
async function aiConfirmDrawingsAndNext() {
  if (aiBusy) {
    return { ok: false, error: { code: 'busy', message: '已有任务在执行，请稍候。' } };
  }
  if (!aiAnalyzed()) {
    return { ok: false, error: { code: 'not-analyzed',
      message: '请先点「一键分析整合图纸」，有整合结果后才能进入参数推荐。' } };
  }
  try {
    aiData = await api(aiUrl(''));
    aiRender();
  } catch (error) {
    return { ok: false, error: { code: 'read-failed',
      message: (error && error.message) || '读取整合结果失败，请稍后重试。' } };
  }
  if (!aiAnalyzed()) {
    return { ok: false, error: { code: 'not-analyzed',
      message: '后端还没有整合结果，请先完成整合分析。' } };
  }
  aiSetTab('params');
  aiSay('整合图纸已确认，已切到「参数推荐」。');
  return { ok: true };
}

/** 软闸门：前置没做完时不再硬阻断 —— 先把「还差什么」原样摆出来，人点「仍要继续」
    就带着缺口往下走，点「取消」才停手。
    只用于「没完成」这一类：权限不在这里，那是后端 _require 的判定，前端只如实转述。
    不记状态、不发请求、不用浏览器原生 confirm 挡住整页。 */
function aiAskProceed(why, options = {}) {
  const text = String(why || '').trim();
  if (!text) return Promise.resolve(true);
  const mask = document.createElement('div');
  mask.className = 'ai-confirm-mask';
  mask.innerHTML =
    `<div class="ai-confirm-box" role="dialog" aria-modal="true" aria-label="确认继续">
      <div class="ai-confirm-head">${esc(options.title || '这一步还有没完成的项')}</div>
      <div class="ai-confirm-body">${esc(text)}</div>
      <div class="ai-confirm-foot">
        <button type="button" class="cancel" data-ai-confirm-cancel>取消</button>
        <button type="button" class="go" data-ai-confirm-go>仍要继续</button>
      </div></div>`;
  document.body.append(mask);
  return new Promise(resolve => {
    const finish = value => {
      document.removeEventListener('keydown', onKey);
      mask.remove();
      resolve(value);
    };
    const onKey = event => { if (event.key === 'Escape') finish(false); };
    mask.querySelector('[data-ai-confirm-go]').onclick = () => finish(true);
    mask.querySelector('[data-ai-confirm-cancel]').onclick = () => finish(false);
    mask.addEventListener('mousedown', event => { if (event.target === mask) finish(false); });
    document.addEventListener('keydown', onKey);
    const go = mask.querySelector('[data-ai-confirm-go]');
    if (go) go.focus();
  });
}

/** 「确认并进入下一页签」：确认参数已齐（按报价必填校验）→ 确认参数推荐 → 切到组装工艺。
    两步都复用既有接口（/params/finalize 的 confirm 分支、/params/confirm）。
    必填没齐时不硬阻断：把缺的项摆清楚，人点「仍要继续」就把已填的值先落库，
    再走 /params/confirm 带着缺口进入组装工艺 —— 这是人的决定，由他签字。 */
async function aiConfirmParamsAndNext() {
  if (aiBusy) {
    return { ok: false, error: { code: 'busy', message: '已有任务在执行，请稍候。' } };
  }
  if (!aiHasParams()) {
    return { ok: false, error: { code: 'no-params', message: '请先生成参数推荐。' } };
  }
  // 先落库 + 校验报价必填：缺项时后端退回真实原因，这里如实摆给用户看。
  await aiParamsFinalize(true);
  if (!aiData?.status?.params_final) {
    const gaps = aiRequiredGaps();
    const why = `还有 ${gaps.required_missing} 项报价必填参数没有值：`
      + `${gaps.fields.map(field => field.name || field.code).join('、')}。`;
    const go = await aiAskProceed(
      `${why}\n\n这些项平台推不出来，需要有人给个值。现在继续的话，`
      + `报价测算单上这几格会是空白。确定要带着缺口进入「组装工艺」吗？`);
    if (!go) {
      return { ok: false, error: { code: 'required-missing',
        message: `${why}已停在参数推荐这一步，在右侧表格补填后再点「确认并进入下一页签」。` } };
    }
    // 带着缺口继续：既有的「保存补填」调用 aiParamsFinalize(false) 形态不变，只是这次
    // 多带一个签字 —— 已填的值照旧落库（params_final 仍为 false，签字不等于参数已齐），
    // 缺口与签字一起留档，发送财务时同一批缺口凭它复用，不再要第二次签字。
    const waiver = { reason: `带缺口继续：${why}` };
    await aiParamsFinalize(false, waiver);
  }
  await aiConfirmStep('params');
  if (!aiData?.status?.params_confirmed) {
    return { ok: false, error: { code: 'confirm-failed',
      message: '参数推荐确认没有通过，请查看右侧看板提示。' } };
  }
  aiSetTab('process');
  aiSay('参数推荐已确认。已切到「组装工艺」—— 点「生成组装工艺」继续排工序。');
  return { ok: true };
}

/** 切到下一步：复用既有嵌入导航通道（tech-embed.js 的 requestNavigate 会按
    cpq:tech-workbench:navigate 交给父壳；独立打开时它自己整页跳转）。
    这里不自己拼 postMessage，也不认 iframe 内部 DOM —— 导航只有这一条通道。 */
function techGoNextStage(stage) {
  if (!stage) return false;
  try {
    if (window.TechEmbed && typeof window.TechEmbed.requestNavigate === 'function') {
      window.TechEmbed.requestNavigate(stage, aiPid);
      return true;
    }
  } catch (error) { /* 嵌入壳缺失或未就绪：保持页内不动，由顶部流程条兜底 */ }
  return false;
}

/** 「确认并进入下一步」（组装工艺页）：确认组装工艺 → 切到 4 成本测算。
    确认走既有 /integration/process/confirm，切步骤走既有嵌入导航通道；
    确认没过就把真实原因原样返回，不往下走。交给财务的交接不在这里做 ——
    接收人必须人工选，仍由同一页的「确认工艺并发送财务」承担。 */
async function aiConfirmProcessAndNext() {
  if (aiBusy) {
    return { ok: false, error: { code: 'busy', message: '已有任务在执行，请稍候。' } };
  }
  if (!aiHasProcess()) {
    return { ok: false, error: { code: 'no-process', message: '请先生成组装工艺。' } };
  }
  await aiConfirmStep('process');
  if (!aiData?.status?.process_confirmed) {
    return { ok: false, error: { code: 'confirm-failed',
      message: '组装工艺确认没有通过，请查看右侧看板提示。' } };
  }
  techGoNextStage('cost');
  aiSay('组装工艺已确认，已进入下一步 4 成本测算。'
    + (aiPlan().finance_handoff
        ? '任务已经在财务手里，接下来由他在 4 成本测算逐件测算零件成本与组装成本。'
        : '如果还没把任务交给财务，先在左边点「确认工艺并发送财务」（接收人要人工选），'
          + '否则 4 成本测算没有可接手的测算任务。'));
  return { ok: true };
}

/** 把参数表里改过的值收回成 IntegrationParamPlan。后端 finalize 那条路只回传值
    （由后端按"人工补填"标注），这里要连依据与来源一起存。 */
function aiCollectParams() {
  const params = JSON.parse(JSON.stringify(aiPlan().params || {}));
  params.params = params.params || [];
  // 补充参数按下标就地改
  document.querySelectorAll('[data-ai-xp]').forEach(row => {
    const target = params.params[Number(row.dataset.aiXp)];
    if (!target) return;
    row.querySelectorAll('[data-f]').forEach(input => {
      target[input.dataset.f] = input.value.trim() || null;
    });
    target.name = target.name || '未命名参数';
    target.value = target.value || '';
  });
  // 报价字典行按 param_code 找；字典里有、参数表里还没有的，填了值才新建一行 ——
  // 空行也建的话，一次编辑就会往参数表里塞进十几条空参数。
  const byCode = new Map();
  params.params.forEach(row => {
    if (row.param_code && !byCode.has(row.param_code)) byCode.set(row.param_code, row);
  });
  document.querySelectorAll('[data-ai-qp]').forEach(row => {
    const read = field => row.querySelector(`[data-f="${field}"]`)?.value.trim() || '';
    const value = read('value');
    let target = byCode.get(row.dataset.aiQp);
    if (!target) {
      if (!value) return;
      target = {
        name: row.dataset.aiName, value: '', unit: row.dataset.aiUnit || null,
        param_code: row.dataset.aiQp, category: null, basis: null,
        source: '人工填写', confidence: 0.9,
      };
      params.params.push(target);
      byCode.set(row.dataset.aiQp, target);
    }
    target.value = value;
    target.basis = read('basis') || null;
  });
  return params;
}

// --------------------------------------------------------------------------- 面板：组装工艺
function aiRenderProcess() {
  const plan = aiPlan().process;
  if (!plan) {
    return `<div class="inline-empty">尚未生成组装工艺。平台会先按整机类别检索企业工艺库里的<strong>组装路线模板</strong>，`
      + `再让模型在库内工序上排产 —— 工序编号与标准工时都来自库，不是模型自己编的。</div>`;
  }
  const validation = aiData?.process_validation || {};
  const editing = aiEditing.process;
  const steps = plan.steps || [];
  let html = `<div class="inline-process-stack"><section class="inline-card"><div class="inline-card-title">工序明细</div>`;
  if (!steps.length) {
    html += `<div class="inline-warn">⚠ 本次没有排出任何组装工序。点上方「编辑」后可以<b>逐道手工添加</b>，`
      + `也可以补充整合需求或先完善参数推荐后重新生成。</div>`;
  }
  html += `<div class="inline-steps">`;
  steps.forEach((step, index) => { html += aiProcessStep(step, index, editing); });
  html += `</div>`;
  // 编辑态给「添加工序」：原来编辑只能改已有的行，模型一道都没排出来时就无从下手了。
  if (editing) {
    html += `<button type="button" class="inline-action" id="aiAddStep">＋ 添加工序</button>`;
  }
  html += `</section>`;

  html += `<section class="inline-card"><div class="inline-card-title">装配方案</div>`;
  if (plan.blank) html += `<div class="inline-row"><b>来料构成</b>${esc(plan.blank)}</div>`;
  if (plan.summary) html += `<div class="inline-row"><b>思路</b>${esc(plan.summary)}</div>`;
  html += `<div class="inline-totals"><span><strong>${validation.step_count ?? (plan.steps || []).length}</strong>工序数</span>`;
  if (validation.total_duration_min != null) html += `<span><strong>${validation.total_duration_min}</strong>合计工时(分钟/台)</span>`;
  html += `</div>`;
  (validation.warnings || []).forEach(warning => { html += `<div class="inline-warn">⚠ ${esc(warning)}</div>`; });
  (plan.rule_warnings || []).forEach(warning => { html += `<div class="inline-warn">⚠ ${esc(warning)}</div>`; });
  html += `</section>`;
  html += aiCoverageCard(aiData?.process_coverage);
  html += aiProcessLibraryCard(aiData?.process_lookup);
  html += aiOpenQuestionsCard(null, plan.open_questions);
  return html + `</div>`;
}

function aiProcessStep(step, index, editing) {
  if (editing) {
    const options = Object.keys(AI_TYPE_LABEL).map(type =>
      `<option value="${type}"${type === step.type ? ' selected' : ''}>${AI_TYPE_LABEL[type]}</option>`).join('');
    return `<div class="inline-step" data-ai-step data-i="${index}"><div class="inline-sno">${esc(step.step_no)}</div>`
      + `<div class="inline-sbody"><div class="inline-edit-grid">`
      + `<label>工序号</label><input data-f="step_no" type="number" value="${aiAttr(step.step_no)}"/>`
      + `<label>名称</label><input data-f="name" value="${aiAttr(step.name || '')}"/>`
      + `<label>类型</label><select data-f="type">${options}</select>`
      + `<label>内容</label><textarea data-f="description" rows="2">${esc(step.description || '')}</textarea>`
      + `<label>设备</label><input data-f="equipment" value="${aiAttr(step.equipment || '')}"/>`
      + `<label>工装</label><input data-f="fixture" value="${aiAttr(step.fixture || '')}"/>`
      + `<label>质量要求</label><input data-f="quality" value="${aiAttr(step.quality || '')}"/>`
      + `<label>工时(分)</label><input data-f="duration_min" type="number" step="0.1" value="${step.duration_min != null ? aiAttr(step.duration_min) : ''}"/>`
      + `</div><button type="button" class="inline-action ai-row-del" data-ai-del-step="${index}">删除本道工序</button>`
      + `</div></div>`;
  }
  const kv = (key, value) => value ? `<div><span class="inline-key">${key}:</span> ${esc(value)}</div>` : '';
  return `<div class="inline-step"><div class="inline-sno">${esc(step.step_no)}</div><div class="inline-sbody">`
    + `<div class="inline-step-title">${esc(step.name || '')}`
    + `<span class="inline-type">${AI_TYPE_LABEL[step.type] || esc(step.type)}</span></div>`
    + (step.description ? `<div class="inline-description">${esc(step.description)}</div>` : '')
    + `<div class="inline-step-grid">${kv('设备', step.equipment)}${kv('工装', step.fixture)}${kv('质量', step.quality)}`
    + (step.duration_min != null ? `<div><span class="inline-key">工时:</span> ${esc(step.duration_min)} 分</div>` : '')
    + `</div>`
    + ((step.depends_on || []).length ? `<div class="inline-dep">前序依赖：工序 ${step.depends_on.join('、')}</div>` : '')
    + (step.note ? `<div class="inline-dep">${esc(step.note)}</div>` : '')
    + `</div></div>`;
}

function aiCollectProcess() {
  const plan = JSON.parse(JSON.stringify(aiPlan().process || {}));
  plan.steps = plan.steps || [];
  document.querySelectorAll('[data-ai-step][data-i]').forEach(row => {
    const step = plan.steps[Number(row.dataset.i)];
    if (!step) return;
    row.querySelectorAll('[data-f]').forEach(input => {
      const field = input.dataset.f;
      const value = input.value;
      if (field === 'step_no') step.step_no = parseInt(value, 10) || step.step_no;
      else if (field === 'duration_min') step.duration_min = value.trim() === '' ? null : parseFloat(value);
      else step[field] = value.trim() === '' ? null : value;
    });
  });
  plan.steps.sort((a, b) => a.step_no - b.step_no);
  return plan;
}

/* 增删行的通用套路：先把界面上已经填的内容收回来，再改结构，最后重画。
   顺序反过来的话，点一下「添加工序」就会把这一轮手打的字全丢掉。 */
function aiMutateProcess(mutate) {
  const plan = aiCollectProcess();
  mutate(plan);
  aiData.plan.process = plan;
  aiRender();
}

function aiAddProcessStep() {
  aiMutateProcess(plan => {
    const last = plan.steps.length ? Math.max(...plan.steps.map(s => Number(s.step_no) || 0)) : 0;
    plan.steps.push({
      step_no: last + 10, name: '', type: 'assembly', description: '',
      equipment: '', fixture: '', quality: '', duration_min: null,
      depends_on: last ? [last] : [], note: '', confidence: 1,
    });
  });
}

function aiCoverageCard(coverage) {
  if (!coverage || !coverage.summary) return '';
  const { reused = [], missing = [] } = coverage;
  const row = (item, kind) =>
    `<div class="inline-cov-row ${kind}"><span class="inline-cov-no">${esc(item.step_no)}</span>`
    + `<span class="inline-cov-name">${esc(item.name || '')}</span>`
    + (kind === 'reused' ? `<span class="inline-cov-code">${esc(item.step_code)}</span>`
      : `<span class="inline-cov-code new">需新建</span>`) + `</div>`;
  let html = `<section class="inline-card"><div class="inline-card-title">工艺库覆盖</div>`
    + `<div class="inline-totals"><span><strong>${reused.length}</strong>库内已有</span>`
    + `<span><strong>${missing.length}</strong>缺失待建</span></div>`;
  html += reused.length ? reused.map(item => row(item, 'reused')).join('')
    : `<div class="inline-hint">没有可沿用的库内组装工序。</div>`;
  if (missing.length) {
    html += `<div class="inline-cov-split">以下工序库内没有，需新建工艺文件：</div>`
      + missing.map(item => row(item, 'missing')).join('');
  }
  return html + `</section>`;
}

function aiProcessLibraryCard(report) {
  if (!report) return '';
  const route = report.route;
  let html = `<section class="inline-card inline-library"><div class="inline-card-title">库内依据 · 工艺库</div>`;
  if (route) {
    html += `<div class="inline-row"><b>路线模板</b>${esc(route.route_code)} ${esc(route.name || '')}</div>`
      + `<div class="inline-hint">${esc(route.source || '')} · ${(route.steps || []).length} 道工序</div>`;
    (route.steps || []).forEach(step => {
      html += `<div class="inline-lib-step"><code>${esc(step.step_code || '')}</code> ${esc(step.name || '')}`
        + `<small>准备 ${step.setup_min ?? '—'} min · 设备类 ${esc(step.equipment_class || '未指定')}</small></div>`;
    });
  } else {
    html += `<div class="inline-warn">⚠ 库内无适用的组装路线模板，本次工艺为全新编制</div>`;
  }
  (report.notes || []).forEach(note => { html += `<div class="inline-hint">${esc(note)}</div>`; });
  return html + `</section>`;
}

function aiOpenQuestionsCard(assumptions, questions) {
  const list = questions || [];
  const notes = assumptions || [];
  if (!list.length && !notes.length) return '';
  let html = `<section class="inline-card"><div class="inline-card-title">假设与待澄清</div>`;
  notes.forEach(item => { html += `<div class="inline-assumption">• ${esc(item)}</div>`; });
  list.forEach(question => {
    html += `<div class="inline-question">❓ ${esc(question.field)}：${esc(question.reason)}`
      + (question.guess ? `（猜测：${esc(question.guess)}）` : '') + `</div>`;
  });
  return html + `</section>`;
}

// --------------------------------------------------------------------------- 渲染入口
function aiRender() {
  const renderers = {
    drawings: aiRenderDrawings, params: aiRenderParams, process: aiRenderProcess,
  };
  document.querySelectorAll('#aiTabs [data-ai-tab]').forEach(button => {
    button.classList.toggle('active', button.dataset.aiTab === aiTab);
  });
  const blocked = aiBlocker(aiTab);
  const state = aiData?.status || {};
  // 父工作台（tech-workbench）process 阶段底栏按“是否已完成整合分析”反转主次：
  // 唯一判定是参数推荐与组装工艺都已生成，与 #aiToFinance 是否可点无关 —— 只生成
  // 一项、busy 或失败都不算完成。每次 render 都同步，重新生成 / Agent 改动后同样生效。
  const analyzed = Boolean(state.has_params && state.has_process);
  document.body.dataset.integrationAnalyzed = analyzed ? 'true' : 'false';
  const done = { drawings: state.drawings > 0, params: state.params_confirmed,
                 process: state.process_confirmed };
  document.querySelectorAll('#aiStepBody [data-ai-tab]').forEach(button => {
    button.classList.toggle('active', button.dataset.aiTab === aiTab);
    button.classList.toggle('done', Boolean(done[button.dataset.aiTab]));
  });
  $ai('aiBody').innerHTML = (blocked ? `<div class="inline-warn">⚠ ${esc(blocked)}</div>` : '') + renderers[aiTab]();
  aiRenderActions();
  aiRenderFiles();
  aiRenderOps();
  aiBindBody();
  const stat = aiData?.param_checklist?.summary;
  $ai('aiSideState').textContent =
    `图纸 ${state.drawings || 0} · 参数 ${stat ? `${stat.filled}/${stat.total}` : '—'}`
    + ` · 工艺 ${state.process_confirmed ? '已确认' : state.has_process ? '待确认' : '—'}`;
  const name = aiPlan().params?.assembly_name;
  $ai('aiTitle').textContent = name ? `组装与整合 · ${name}` : '组装与整合';
  aiPublishState();
}

/* 每次渲染收尾重新发布一次动作快照：父壳左侧「确认工艺并发送财务」等按钮的
   busy / 可见性不会停在上一帧。同一帧内多次渲染合并成一次；独立打开阶段页
   （没有 TechBoardRuntime）时无副作用。 */
let aiPublishTimer = null;
function aiPublishState() {
  const runtime = window.TechBoardRuntime;
  if (!runtime || typeof runtime.refreshState !== 'function') return;
  if (aiPublishTimer) return;
  aiPublishTimer = setTimeout(() => {
    aiPublishTimer = null;
    try { runtime.refreshState(); } catch (error) { /* 快照刷新失败不影响页内渲染 */ }
  }, 0);
}

/* ------------------------------------------------------- 两个对外动作
 * 写入数据库 / 确认工艺并发送至报价。两者都打到 tech_app，再由它回调一体化服务
 * （远程 Postgres 与报价工作流都在那一侧）。成本没算完一律不给点 —— 写进主数据的
 * 单价是要拿去报价的，宁可拦住，也不要写一个 0 进去。
 */

/* 「确认工艺并发送财务」的 L1 硬门禁：只判**生成依赖** —— 参数推荐 / 组装工艺跑过没有。
   没有这两样，财务连成本都算不出来，签字也不放行（L1 不可豁免）。
   L2（报价必填缺口、三项内部确认）不在这里拦：那是「仍要继续」的签字范围，
   见 aiFinanceGaps() 与 aiConfirmProcessAndSendToFinance()。
   aiRenderOps() 的页内 why 与左侧动作快照的 run() 共用这一份，两处不许漂移。 */
function aiFinanceBlocker() {
  const state = aiData?.status || {};
  if (!state.has_params) return '请先完成参数推荐';
  if (!state.has_process) return '请先完成组装工艺';
  return '';
}

/* 「这批缺口是不是已经被同一份签字放行过」——与后端 integration.waiver_covers() 逐条
   对应：签字的字段编码集合要盖住当前缺口，顺带放行的确认项也要盖住还没点确认的环节；
   签字之后新冒出来的缺口（集合变大）不算覆盖，必须重新签；空集合视为已被覆盖。
   纯函数：不引用页面上的任何东西，单测可以直接求值（与后端同一套判定，不另写一份）。 */
function aiWaiverCoversGaps(waiver, missingCodes, pending) {
  if (!waiver || typeof waiver !== 'object') return false;
  const signed = new Set((waiver.missing_codes || []).map(String));
  const released = new Set((waiver.waived_confirmations || []).map(String));
  const covers = (items, pool) => (items || []).every(item => pool.has(String(item)));
  return covers(missingCodes, signed) && covers(pending, released);
}

/* L2 缺口的完整描述（报价必填缺口 + 三项内部确认）：只描述、不拦截。
   「仍要继续」弹窗正文与页内提示共用这一份 —— 缺口一次说全，不让人逐页倒查；
   点「仍要继续」才带着本人签字往下发，点「取消」即停。 */
function aiFinanceGaps() {
  const state = aiData?.status || {};
  const gaps = aiRequiredGaps();
  // 三项内部确认是否都点过。只用来把缺口说清楚 —— 点不点都不再当硬门禁（可签字放行）。
  const confirmed = Boolean(state.params_confirmed && state.process_confirmed);
  const pending = [];
  const pendingCodes = [];
  if (!state.params_final) { pending.push('「确认参数已齐」'); pendingCodes.push('params_final'); }
  if (!state.params_confirmed) { pending.push('「确认参数推荐」'); pendingCodes.push('params_confirmed'); }
  if (!state.process_confirmed) { pending.push('「确认组装工艺」'); pendingCodes.push('process_confirmed'); }
  const parts = [];
  if (gaps.required_missing > 0) {
    const names = gaps.fields.map(field => field.name || field.code).join('、');
    parts.push(`报价必填的成品参数还缺 ${gaps.required_missing} 项：${names}`);
  }
  if (pending.length) parts.push(`还没点确认的环节：${pending.join('、')}`);
  // 这批缺口是不是已经签过字：读后端落库的那份签字（status.waiver），判定与后端
  // waiver_covers() 同一套规则 —— 前端不另写一份，也不拿本地状态猜。
  const waiver = state.waiver || null;
  const covered = aiWaiverCoversGaps(waiver, gaps.fields.map(field => field.code),
    pendingCodes);
  return {
    required_total: gaps.required_total,
    required_filled: gaps.required_filled,
    required_missing: gaps.required_missing,
    fields: gaps.fields,
    params_final: !!state.params_final,
    params_confirmed: !!state.params_confirmed,
    process_confirmed: !!state.process_confirmed,
    confirmed: confirmed,
    covered: covered,
    waiver: waiver,
    text: parts.length ? `${parts.join('；')}。` : '',
  };
}

function aiRenderOps() {
  const plan = aiPlan();
  const state = aiData?.status || {};
  const nameInput = $ai('aiProductName');
  if (nameInput && !nameInput.value && plan.params?.assembly_name) {
    nameInput.value = plan.params.assembly_name;
  }
  // 本步不再显示成品成本 —— 成本是 4 成本测算的产出。这里只说工艺交到哪一步了。
  const box = $ai('aiOpCost');
  const finance = plan.finance_handoff;
  box.textContent = finance
    ? `已交给${finance.target_role_name || '财务经理'}（任务 ${finance.task_no || ''}）`
    : '确认参数推荐与组装工艺后，把任务交给财务经理测算成本';

  const financeBtn = $ai('aiToFinance');
  // L2 缺口不再把按钮置灰：参数推荐与组装工艺跑过（L1）就能点，缺什么由「仍要继续」
  // 签字放行。忙的时候仍然禁用 —— 那是并发保护，不是业务门禁。
  const ready = Boolean(state.has_params && state.has_process) && !aiBusy;
  if (financeBtn) financeBtn.disabled = !ready;

  const gaps = aiFinanceGaps();
  const gapsWhy = gaps.text && gaps.covered ? `${gaps.text}（这批缺口已签字放行）` : gaps.text;
  const why = aiBusy ? '正在处理…' : (aiFinanceBlocker() || gapsWhy);
  if (financeBtn) {
    financeBtn.title = why || '把工艺、参数与用量交给财务经理，由他在 4 成本测算成本';
  }
  const badge = $ai('aiOpsWhy');
  if (badge) {
    badge.textContent = why;
    badge.hidden = !why;
  }

  const done = [];
  if (finance) {
    done.push(`已${esc(aiHandoffWhom(finance))}`
      + (finance.task_no ? ` · 任务 ${esc(finance.task_no)}` : '')
      + ` · ${esc(finance.sent_at || '')}`);
  }
  (plan.material_writes || []).forEach(item => {
    done.push(`已写入主数据：<b>${esc(item.number)}</b> ${esc(item.name)}`
      + ` · 单价 ${aiMoney(item.material_unit_price)} 元 · ${esc(item.written_at || '')}`);
  });
  const hint = $cr_hint();
  hint.innerHTML = (done.length
    ? done.map(line => `<div class="ai-op-done">✓ ${line}</div>`).join('')
      + `<div style="margin-top:6px">再点一次可以换个派发方式重发（旧任务会被作废）。</div>`
    : '工艺与整机参数在这一步定稿（报价必填参数在「参数推荐」里补齐并确认）；'
      + '<strong>成本由财务经理在 4 成本测算完成</strong>，写入数据库与发送至报价也都移到了那一步。')
    + (why ? '' : '');
}

/** 说明区节点。抽出来只是为了上面那段读起来短一点。 */
function $cr_hint() { return $ai('aiOpsHint'); }

/** 这条任务交到哪儿了 —— 三种派发方式各有各的说法，别一律写成"已发送至财务经理"。 */
function aiHandoffWhom(finance) {
  const name = finance.target_name || finance.target_role_name || '财务经理';
  if (finance.target_type === 'public') return '发布到公共任务池（谁都能领取）';
  if (finance.target_type === 'user') return `指派给${name}`;
  return `发给${name}（该角色的人都能领取）`;
}

/* 「发送至财务」的派发弹窗 —— 与报价助手的「转交任务」同构。
   成本测算的默认收件角色是财务经理，但公司里做这件事的往往不止一个人：
   点一下就自动群发给整个角色，等于替发起人做了他本来要做的选择。
   三种方式都给：发给某个角色 / 指派给某个人 / 发布到公共任务池。 */
const AI_FINANCE_ROLE = 'finance_mgr';

async function aiWfApi(path) {
  if (!window.cpqAuth || !window.cpqAuth.api) throw new Error('未加载登录模块');
  return window.cpqAuth.api(path);
}

async function aiOpenFinanceDialog(waiver) {
  if (aiBusy) {
    const message = '正在处理中，请稍后再发送财务。';
    aiStatus(message, true);
    return { ok: false, error: { code: 'busy', message: message } };
  }
  // 名单取不到也要能发：那时只剩「发给财务经理」这一种，与改造前的行为一致。
  let roles = [];
  let users = [];
  let listError = '';
  try {
    roles = (await aiWfApi('/auth/roles')).roles || [];
    users = (await aiWfApi('/auth/users')).users || [];
  } catch (error) {
    listError = error.message || '读取人员名单失败';
  }
  const me = (window.cpqAuth && window.cpqAuth.user && window.cpqAuth.user()) || {};
  const roleNameOf = code =>
    (roles.find(r => r.role_code === code) || {}).role_name || '财务经理';

  const mask = document.createElement('div');
  mask.className = 'ai-send-mask';
  mask.id = 'aiSendMask';
  mask.innerHTML =
    `<div class="ai-send-box">
      <div class="ai-send-head"><h3>发送至财务做成本测算</h3>
        <p>工艺、整机参数与用量在这一步定稿。接手人会在技术工艺 4 成本测算逐件测算零件成本、
           组装成本并汇总，再决定写入数据库、发送至报价，或退回给你复核。</p></div>
      <div class="ai-send-body">
        <div class="ai-send-row"><label for="aiSendWay">推送方式</label>
          <select id="aiSendWay">
            <option value="role">发给某个角色（该角色的人都能领取）</option>
            <option value="user">指派给某个人</option>
            <option value="public">发布到公共任务池（谁都能领取）</option>
          </select></div>
        <div class="ai-send-row" id="aiSendRoleRow"><label for="aiSendRole">目标角色</label>
          <select id="aiSendRole">${
            (roles.length ? roles : [{ role_code: AI_FINANCE_ROLE, role_name: '财务经理' }])
              .map(r => `<option value="${aiAttr(r.role_code)}"${
                r.role_code === AI_FINANCE_ROLE ? ' selected' : ''}>${esc(r.role_name)}</option>`)
              .join('')}</select></div>
        <div class="ai-send-row" id="aiSendUserRow" hidden>
          <label for="aiSendUser">目标人员（按姓名 / 角色搜索）</label>
          <label class="ai-send-only"><input type="checkbox" id="aiSendOnly" checked />只看${esc(roleNameOf(AI_FINANCE_ROLE))}</label>
          <input id="aiSendSearch" placeholder="输入姓名/角色筛选" />
          <select id="aiSendUser" size="5"></select></div>
        <div class="ai-send-row"><label for="aiSendNote">备注（可选）</label>
          <input id="aiSendNote" maxlength="200" placeholder="给财务的说明，如核算批量、特殊工艺" /></div>
      </div>
      <div class="ai-send-msg" id="aiSendMsg">${listError ? esc(listError) + '：只能按角色发送。' : ''}</div>
      <div class="ai-send-foot">
        <button type="button" class="cancel" id="aiSendCancel">取消</button>
        <button type="button" class="send" id="aiSendGo">发送</button>
      </div>
    </div>`;
  document.body.append(mask);
  mask.addEventListener('mousedown', event => { if (event.target === mask) mask.remove(); });

  const fillUsers = () => {
    const q = ($ai('aiSendSearch').value || '').trim().toLowerCase();
    const onlyFinance = $ai('aiSendOnly').checked;
    $ai('aiSendUser').innerHTML = users
      .filter(x => String(x.user_id) !== String(me.user_id))
      .filter(x => !onlyFinance || x.role_code === AI_FINANCE_ROLE)
      .filter(x => !q || `${x.display_name}${x.role_name}${x.username}`.toLowerCase().includes(q))
      .map(x => `<option value="${aiAttr(x.user_id)}">${esc(x.display_name)}（${esc(x.role_name)}）</option>`)
      .join('') || '<option value="" disabled>没有匹配的人员</option>';
  };
  const syncRows = () => {
    const way = $ai('aiSendWay').value;
    $ai('aiSendRoleRow').hidden = way !== 'role';
    $ai('aiSendUserRow').hidden = way !== 'user';
    if (way === 'user') fillUsers();
  };
  syncRows();
  $ai('aiSendWay').onchange = syncRows;
  $ai('aiSendSearch').oninput = fillUsers;
  $ai('aiSendOnly').onchange = fillUsers;
  $ai('aiSendCancel').onclick = () => mask.remove();
  $ai('aiSendGo').onclick = () => {
    const way = $ai('aiSendWay').value;
    const dispatch = { target_type: way, note: $ai('aiSendNote').value.trim() };
    if (way === 'role') dispatch.target_role_code = $ai('aiSendRole').value;
    if (way === 'user') {
      dispatch.target_user_id = $ai('aiSendUser').value;
      if (!dispatch.target_user_id) { $ai('aiSendMsg').textContent = '请选择目标人员'; return; }
    }
    // 「带缺口继续」的签字随请求一起提交：缺口以服务端算出的为准，前端只表达本人意愿。
    if (waiver) dispatch.waiver = waiver;
    mask.remove();
    aiRunOp('send-to-finance', dispatch);
  };
}

/* 「确认工艺并发送财务」的链路：组装工艺还没确认就先走既有 /process/confirm 把它
   确认掉（确认没通过就带真实原因停下），再由既有 aiFinanceBlocker 判定 L1 生成依赖；
   L2 缺口（报价必填 + 三项内部确认）不硬拦 —— 用既有 aiAskProceed 取得本人签字，
   点「仍要继续」才带着签字打开既有接收人弹窗，点「取消」即停、不发送不落库。
   弹窗要人操作，所以整条链由 aiSendToFinanceInBackground 在后台跑。 */
async function aiConfirmProcessAndSendToFinance() {
  const state = aiStatusState();
  if (state.has_process && !state.process_confirmed) {
    await aiConfirmStep('process');
    if (!aiStatusState().process_confirmed) {
      return { ok: false, error: { code: 'confirm-failed',
        message: '组装工艺确认没有通过，请查看右侧看板提示。' } };
    }
  }
  const why = aiFinanceBlocker();
  if (why) return { ok: false, error: { code: 'not-ready', message: why } };
  const gaps = aiFinanceGaps();
  let waiver = null;
  if (gaps.covered && gaps.text) {
    // 同一批缺口在「参数推荐」已经签过字（后端已落库，就是这份 status.waiver）：
    // 不再弹第二次「仍要继续」，只把风险按普通会话输出说清楚，然后照旧走接收人弹窗 ——
    // 传 null 让后端复用那次签字，前端不伪造第二次签字，也不另开一条发送通道。
    const signed = gaps.waiver || {};
    aiSay(`这批缺口上一环节已经签过字，不再重复询问：${gaps.text}`
      + `报价测算单上对应的格子仍是空白；签字：${signed.waived_by || '本人'}`
      + `${signed.waived_at ? `（${signed.waived_at}）` : ''}`
      + `${signed.reason ? `，事由：${signed.reason}` : ''}。`
      + `现在把工艺、参数与用量发给财务，缺口按那次签字放行。`);
    return aiOpenFinanceDialog(null);
  }
  if (gaps.text && !gaps.covered) {
    const go = await aiAskProceed(
      `${gaps.text}\n\n这些项平台推不出来，需要有人给个值。现在继续的话，报价测算单上`
      + `这几格会是空白，内部确认也按你的签字放行；平台会记下是你签的字，`
      + `之后不再按同一批缺口拦你。确定要带着缺口发送财务吗？`,
      { title: '这一步还有没完成的项' });
    if (!go) {
      return { ok: false, error: { code: 'required-missing',
        message: `${gaps.text}已停在发送财务这一步：补填参数或点确认后再重试。` } };
    }
    waiver = { reason: `带缺口继续：${gaps.text}` };
  }
  return aiOpenFinanceDialog(waiver);
}

/* 发送财务的后台链路：动作条目只启动它、秒级回执，由这里在真正结束时自报收尾。
   父壳那 20 秒超时只用来抓「看板没响应」，不再误伤要人选接收人的弹窗。 */
async function aiSendToFinanceInBackground() {
  try {
    const result = await aiConfirmProcessAndSendToFinance();
    if (result && result.ok === false) {
      aiSettleIfNeeded('task-failed', 'sendIntegrationToFinance',
        { message: (result.error && result.error.message) || '发送财务失败，请查看右侧看板提示。' });
      return;
    }
    aiSettleIfNeeded('task-completed', 'sendIntegrationToFinance');
  } catch (error) {
    aiSettleIfNeeded('task-failed', 'sendIntegrationToFinance',
      { message: (error && error.message) || '发送财务失败，请查看右侧看板提示。' });
  } finally {
    aiDeferredBusy = false;
    window.TechBoardRuntime.updateActionState('sendIntegrationToFinance', { busy: false });
  }
}

async function aiRunOp(kind, dispatch) {
  if (aiBusy) return;
  const labels = { 'send-to-finance': '确认工艺并发送至财务做成本测算' };
  aiBusy = true;
  aiRenderOps();
  aiRenderActions();
  // 用户点按钮触发：先出用户气泡，再出执行卡。
  if (typeof aiUserSay === "function") aiUserSay(`${labels[kind]}。`);
  const card = aiProcessCard(labels[kind]);
  aiStatus(`${labels[kind]}中…`);
  const opTask = `integration-${kind}`;
  aiPublishTask('task-progress', { taskId: opTask, label: labels[kind],
                                   progress: `${labels[kind]}中…` });
  try {
    const payload = Object.assign(
      { product_name: $ai('aiProductName')?.value.trim() || '' }, dispatch || {});
    // 「带缺口继续」的签字只在自己带上来时提交：后端以它算出的缺口为准落库，
    // 前端伪造不了豁免范围。
    if (dispatch && dispatch.waiver) payload.waiver = dispatch.waiver;
    aiData = await api(aiUrl(`/${kind}`), { method: 'POST', body: JSON.stringify(payload) });
    const finance = aiData.finance || {};
    const whom = aiHandoffWhom(finance);
    card.log([`任务 ${finance.task_no || ''} 已${whom}`,
      `  他将在技术工艺 4 成本测算逐件测算零件成本与组装成本`,
      `  写入数据库与发送至报价也都在那一步完成`]);
    aiSay(`工艺已确认，任务 ${finance.task_no || ''} 已${whom}做成本测算。
`
      + `他会在 4 成本测算逐个零件加整机算完成本，然后选择写入数据库、发送至报价，`
      + `或把结果退回给你复核工艺与用量。报价必填参数已在前面「参数推荐」里定稿。`);
    card.done(true);
    aiStatus(`${labels[kind]}完成`);
    aiPublishTask('task-completed', { taskId: opTask, label: labels[kind],
                                      status: 'succeeded' });
  } catch (error) {
    card.done(false, error.message || '失败');
    aiStatus(`${labels[kind]}失败：${error.message}`, true);
    aiToast(error.message || `${labels[kind]}失败`, true);
    aiPublishTask('task-failed', { taskId: opTask, label: labels[kind],
                                   status: 'failed', error: error.message || '失败' });
  } finally {
    aiBusy = false;
    aiRender();
  }
}

function aiBindBody() {
  document.querySelectorAll('[data-ai-drop]').forEach(button => {
    button.onclick = () => aiDropDrawing(button.dataset.aiDrop);
  });
  const addStep = $ai('aiAddStep');
  if (addStep) addStep.onclick = aiAddProcessStep;
  document.querySelectorAll('[data-ai-del-step]').forEach(button => {
    button.onclick = () => aiMutateProcess(
      plan => plan.steps.splice(Number(button.dataset.aiDelStep), 1));
  });
  // 成本明细的增删行随成本一起搬去了 4 成本测算（cost-review），这里不再绑。
}

/* 右侧「任务文件」。与 2.1 同一个清单接口 —— 那边分散在各步骤里的产出（原图、技术
   文档、几何、2D 图、导出表格）都由后端 /files 汇总，前端不再各自去猜哪一步生成过
   什么。这一页在它前面多一组"本步上传的整合图纸"，后面多一段用户需求描述。

   manifest 单独缓存：aiRender() 每次重绘都要画这块，但不该每次都去打一次接口。 */
const AI_FILE_ICON = { image: '🖼', doc: '📄', model: '🧊', table: '📊' };
let aiManifest = null;

function aiFileRow(file) {
  return `<div class="oc-file"><span class="oc-file-icon" aria-hidden="true">`
    + `${AI_FILE_ICON[file.kind] || '📄'}</span><div class="oc-file-body">`
    + `<a class="oc-file-name" target="_blank" rel="noopener" href="${aiAttr(file.url)}">`
    + `${esc(file.name)}</a>`
    + (file.note ? `<div class="oc-file-note">${esc(file.note)}</div>` : '')
    + `</div></div>`;
}

function aiFileGroup(title, count, inner) {
  return `<section class="oc-file-group"><div class="oc-file-group-head"><span>${esc(title)}</span>`
    + (count == null ? '' : `<span class="oc-file-group-count">${count}</span>`)
    + `</div>${inner}</section>`;
}

async function aiLoadManifest() {
  try {
    aiManifest = await api(`/api/projects/${encodeURIComponent(aiPid)}/files`);
  } catch { aiManifest = aiManifest || { groups: [], total: 0, note: '' }; }
  aiRenderFiles();
}

function aiRenderFiles() {
  const drawings = aiPlan().drawings || [];
  const manifest = aiManifest || { groups: [], total: 0, note: '' };
  const sections = [];

  sections.push(aiFileGroup('整合图纸（本步上传）', drawings.length, drawings.length
    ? drawings.map(drawing => aiFileRow({
        kind: 'image', name: drawing.filename, note: drawing.uploaded_at || '',
        url: aiUrl(`/drawings/${encodeURIComponent(drawing.filename)}`),
      })).join('')
    : `<div class="oc-file-empty">还没有整合图纸。用输入框左侧 ＋ 上传装配图或爆炸图。</div>`));

  (manifest.groups || []).forEach(group => sections.push(
    aiFileGroup(group.title, (group.files || []).length,
                (group.files || []).map(aiFileRow).join(''))));

  // 用户需求描述：1.x/首页填的那段，和本步的「整合需求」是两回事，分开列出来。
  // 参数推荐会把两段一起喂给模型（services/integration.py::build_context），
  // 所以这一栏不是装饰 —— 它就是这一步的输入之一。
  const note = String(manifest.note || '').trim();
  const own = String(aiPlan().requirement_note || '').trim();
  sections.push(aiFileGroup('用户需求描述', null,
    (note ? `<div class="oc-file-note oc-file-text">${esc(note)}</div>` : '')
    + (own ? `<div class="oc-file-group-head" style="margin-top:8px"><span>本步整合需求</span></div>`
             + `<div class="oc-file-note oc-file-text">${esc(own)}</div>` : '')
    || `<div class="oc-file-empty">还没有需求描述。可在左侧对话框「整合需求」里补充。</div>`));

  $ai('aiFilesCount').textContent = (drawings.length + (manifest.total || 0)) || '—';
  $ai('aiFilesBody').innerHTML = sections.join('');
}

async function aiSaveEdits(tab) {
  const payloads = {
    params: () => ['/params', aiCollectParams(), '整机参数已保存'],
    process: () => ['/process', aiCollectProcess(), '组装工艺已保存'],
  };
  const [path, payload, message] = payloads[tab]();
  aiEditing[tab] = false;
  if (!await aiPut(path, payload, message)) aiEditing[tab] = true;
}

async function aiDropDrawing(filename) {
  try {
    aiData = await api(aiUrl(`/drawings/${encodeURIComponent(filename)}`), { method: 'DELETE' });
    aiRender();
    aiToast('已移除该整合图纸');
  } catch (error) { aiToast(error.message || '移除失败', true); }
}

async function aiUploadDrawings(files) {
  if (!files.length) return;
  const form = new FormData();
  [...files].forEach(file => form.append('files', file));
  form.append('note', $ai('aiRequirement')?.value.trim() || '');
  aiStatus('上传整合图纸…');
  try {
    aiData = await api(aiUrl('/drawings'), { method: 'POST', body: form });
    aiRender();
    aiSay(`已收到 ${files.length} 张整合图纸：${[...files].map(file => file.name).join('、')}。`
      + `接下来可以点「生成参数推荐」。`);
    aiStatus('整合图纸已上传');
  } catch (error) {
    aiStatus(`上传失败：${error.message}`, true);
    aiToast(error.message || '上传失败', true);
  }
}

/** 一键跑完三个环节。任一环节失败就停在那里 —— 后面两个本来就依赖它的结果。 */
async function aiRunAll() {
  const note = $ai('aiRequirement').value.trim();
  await api(aiUrl(''), {
    method: 'PUT',
    body: JSON.stringify({ requirement_note: note, quantity: aiPlan().quantity || 1 }),
  }).then(data => { aiData = data; }).catch(() => {});
  aiSay('开始整合分析：参数推荐 → 组装工艺，两步依次进行。'
    + '成本测算由财务经理在第 4 阶段做（左边「确认工艺并发送财务」之后）。');
  for (const step of ['params', 'process']) {
    await aiGenerate(step);
    if (!aiData?.status?.[{ params: 'has_params', process: 'has_process' }[step]]) {
      aiSay(`「${AI_TABS[step]}」没有产出结果，后面两步依赖它，先停在这里。`);
      return;
    }
    // 参数刚生成出来就把空格子补掉并落库：这一步不补，用户进「参数推荐」看到的
    // 就是满屏「必填未给出」，还得自己回来点「智能补全」。
    if (step === 'params') await aiAutoFillParams();
  }
  // 确认不代跑：那是人对结果点头，自动点等于没确认。
  aiTab = 'process';
  aiRender();
  aiSay('参数推荐与组装工艺都已生成。逐项核对后，分别点「确认参数推荐」与「确认组装工艺」，'
    + '再把任务发给财务经理做成本测算（4 成本测算）。');
}

/* ------------------------------------------------------- 模型参数设置
 * 面板实现共用 llm-settings-panel.js（首页、2.1 Agent 小窗、这里都是同一份）——
 * 三处各写一套表单正是之前"首页改的和智能体里显示的对不上"的根因。
 * 这里只负责把它挂进一个贴着药丸的弹层，并把唯一当前模型显示在药丸上。
 */
let aiPop = null;

function aiClosePop() {
  if (!aiPop) return;
  aiPop.remove();
  aiPop = null;
  document.removeEventListener('click', aiClosePop);
}

function aiOpenSettings(anchor) {
  aiClosePop();
  const pop = document.createElement('div');
  pop.className = 'oc-pop oc-settings';
  // 点面板内部（下拉、输入框）不该把弹层本身关掉。
  pop.addEventListener('click', event => event.stopPropagation());
  const host = document.createElement('div');
  host.className = 'llm-set-host';
  pop.append(host);
  document.body.append(pop);
  // 左窗贴着屏幕左边，弹层往右让开药丸本身，超出视口时再往回收。
  const rect = anchor.getBoundingClientRect();
  const height = pop.offsetHeight;
  pop.style.top = `${Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - height - 12))}px`;
  pop.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - pop.offsetWidth - 12))}px`;
  aiPop = pop;
  setTimeout(() => document.addEventListener('click', aiClosePop), 0);
  if (!window.LlmSettingsPanel) {
    host.innerHTML = '<div class="llm-set-status err">模型设置面板未加载</div>';
    return;
  }
  window.LlmSettingsPanel.mount(host, {
    onSaved: settings => { aiSetModelLabel(settings); aiToast('模型设置已保存，全局生效。'); },
  });
}

function aiSetModelLabel(settings) {
  const pill = $ai('aiModelPill');
  if (!pill) return;
  // 唯一模型口径：报价 /api/settings 只给一个 model + 同一份 options，技术工艺
  // 不再维护两套模型候选；药丸与 title 只显示这一个模型。
  const options = settings?.options || [];
  const modelId = String(settings?.model || '').trim();
  const option = options.find(item => item && item.id === modelId);
  const text = (option && option.label) || modelId || '未配置模型';
  pill.querySelector('[data-model-name]').textContent = text;
  pill.title = `当前模型：${text}`;
}

async function aiLoadModelLabel() {
  if (!window.LlmSettingsPanel) { aiSetModelLabel(null); return; }
  try { aiSetModelLabel(await window.LlmSettingsPanel.load()); }
  catch { $ai('aiModelPill')?.querySelector('[data-model-name]').replaceChildren('未连接'); }
}

// --------------------------------------------------------------------------- 启动
function aiBindShell() {
  document.querySelectorAll('[data-ai-tab]').forEach(button => {
    button.onclick = () => { aiTab = button.dataset.aiTab; aiRender(); };
  });
  // 左边已经是 56px 导航条，没有可折叠的卡片了；右窗仍是浮动小窗，折叠方式与 2.1
  // 一致：把 collapsed 打在 dock 上（agent-chat.css 里同时管宽度与箭头方向）。
  $ai('aiFilesToggle').onclick = () => {
    const collapsed = $ai('aiFilesDock').classList.toggle('collapsed');
    $ai('aiFilesToggle').setAttribute('aria-expanded', String(!collapsed));
  };
  $ai('aiModelPill').onclick = event => { event.stopPropagation(); aiOpenSettings(event.currentTarget); };
  $ai('aiFilesRefresh').onclick = () => aiLoadManifest();
  $ai('aiDrawingInput').onchange = event => {
    aiUploadDrawings(event.target.files);
    event.target.value = '';
  };
  $ai('aiPlus').onclick = () => $ai('aiDrawingInput').click();
  $ai('aiStart').onclick = () => aiRunAll();
  // 不直接发：先走与左侧动作同一条链路 —— 组装工艺没确认就先确认，L2 缺口由「仍要继续」
  // 取得本人签字，最后才让人选派发方式（角色 / 指定人 / 公共任务池），和报价助手一致。
  $ai('aiToFinance').onclick = () => aiConfirmProcessAndSendToFinance();
  $ai('aiSend').onclick = () => aiSendNote();
  $ai('aiInput').onkeydown = event => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); aiSendNote(); }
  };
  $ai('aiRequirement').onchange = () => aiSaveSettings();
  $ai('aiPrev').onclick = () => window.CadWorkflowNavigation?.navigate('2.1');
  $ai('aiNext').onclick = () => window.CadWorkflowNavigation?.navigate('5.1');
  $ai('aiConfirm').onclick = () => aiConfirm();
}

async function aiSaveSettings() {
  const body = {
    requirement_note: $ai('aiRequirement').value.trim(),
    // 核算批量搬去 4 成本测算了（成本才用得上它），这里原样保留当前值。
    quantity: aiPlan().quantity || 1,
  };
  try {
    aiData = await api(aiUrl(''), { method: 'PUT', body: JSON.stringify(body) });
    aiStatus('整合需求已保存');
  } catch (error) { aiToast(error.message || '保存失败', true); }
}

/* --------------------------------------------------------------- Agent 对话
 * 这一页原来的"对话框"其实只是个输入框：说什么都只会被追加进「整合需求」，
 * 问不到状态、也改不了任何东西。现在接的是 2.1 同一个 open-claude 会话
 * （/api/projects/{id}/agent/send，SSE），工具见 services/oc_agent.py 里
 * GetIntegrationState / ListIntegrationParams / UpdateIntegrationParams /
 * UpdateIntegrationProcess / RequestIntegrationStep —— 所以在这里说
 * 「把 20 工序工时改成 8 分钟」是真的会写进业务数据的。
 */
const AI_TOOL_LABEL = {
  GetIntegrationState: '读取 3 组装与整合当前状态',
  ListIntegrationParams: '查整机参数与报价缺口',
  UpdateIntegrationParams: '写入整机参数',
  UpdateIntegrationProcess: '修改组装工序',
  RequestIntegrationStep: '请求平台重跑环节',
  GetProjectState: '读取项目状态', ListParts: '查 2.1 零件清单',
  GetPartDetail: '查零件详情', LookupProcessLibrary: '检索企业工艺库',
  LookupCostLibrary: '检索企业成本库', LookupComponentLibrary: '检索零部件库',
};

function aiToolLabel(name) { return AI_TOOL_LABEL[name] || name; }

async function aiSendNote() {
  const input = $ai('aiInput');
  const text = input.value.trim();
  if (!text || aiChatBusy) return;
  input.value = '';
  aiUserSay(text);
  await aiAgentTurn(text);
}

let aiChatBusy = false;

async function aiAgentTurn(message) {
  aiChatBusy = true;
  $ai('aiSend').disabled = true;
  const bubble = aiThreadAppend(`<div class="oc-amsg">
    <div class="oc-abody"><div class="oc-atxt oc-streaming"></div></div></div>`);
  const body = bubble.querySelector('.oc-atxt');
  let text = '';
  const actions = new Set();
  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(aiPid)}/agent/send`, {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, aiAuthHeaders()),
      body: JSON.stringify({ message, page_context: '3 组装与整合' }),
    });
    if (!response.ok || !response.body) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `助手不可用（${response.status}）`);
    }
    await aiReadSse(response.body, event => {
      if (event.type === 'text') {
        text += event.text || '';
        body.textContent = text;
        $ai('aiThread').scrollTop = $ai('aiThread').scrollHeight;
      } else if (event.type === 'tool_use') {
        body.insertAdjacentHTML('beforebegin',
          `<div class="ai-tool"><span class="ai-tool-dot">●</span>${esc(aiToolLabel(event.name))}</div>`);
        if (event.ui_action) actions.add(`${event.ui_action}:${(event.input || {}).step || ''}`);
      } else if (event.type === 'error') {
        throw new Error(event.error || '助手出错');
      }
    });
  } catch (error) {
    body.classList.add('err');
    body.textContent = `助手不可用：${error.message}`;
  } finally {
    body.classList.remove('oc-streaming');
    aiChatBusy = false;
    $ai('aiSend').disabled = false;
  }
  await aiApplyAgentActions(actions);
}

/** 助手用不了要提前说，别等用户敲了一段话才发现发不出去。 */
async function aiLoadAgentMeta() {
  const disc = $ai('aiDisc');
  try {
    const meta = await api(`/api/projects/${encodeURIComponent(aiPid)}/agent/meta`);
    if (meta.available === false) {
      $ai('aiInput').placeholder = '工艺助手未就绪，四个环节的按钮仍可正常使用';
      $ai('aiInput').disabled = true;
      $ai('aiSend').disabled = true;
      if (disc) disc.textContent = `工艺助手暂不可用：${meta.reason || '未知原因'}。`
        + '这不影响参数推荐、组装工艺、成本测算这几步 —— 它们走的是平台自己的流水线。';
      return;
    }
    if (disc && meta.model) {
      disc.textContent = `与 2.1 同一个工艺助手（${meta.model}）。`
        + '它能查本步状态与参数缺口，也能直接改整机参数、改组装工序、重跑某个环节。';
    }
  } catch (error) {
    if (disc) disc.textContent = `工艺助手状态未知：${error.message}`;
  }
}

function aiAuthHeaders() {
  const token = localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 逐块读 SSE。事件形状与 2.1 的 agent-chat.js 一致（text / tool_use / error / done）。 */
async function aiReadSse(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 2);
      if (!chunk.startsWith('data:')) continue;
      let event;
      try { event = JSON.parse(chunk.slice(5)); } catch { continue; }
      if (event.type === 'done') return;
      onEvent(event);
    }
  }
}

/** 工具改完数据要让界面跟上；请求重跑环节就替用户点那一步（同一条流水线）。 */
async function aiApplyAgentActions(actions) {
  if (!actions.size) return;
  const steps = [...actions].filter(item => item.startsWith('integration-step:'))
    .map(item => item.split(':')[1]).filter(step => ['params', 'process', 'cost'].includes(step));
  if ([...actions].some(item => item.startsWith('refresh-integration'))) {
    try {
      aiData = await api(aiUrl(''));
      aiRender();
      aiStatus('助手已更新本步内容');
    } catch (error) { aiToast(error.message || '刷新失败', true); }
  }
  for (const step of steps) {
    const blocked = aiBlocker(step);
    if (blocked) { aiSay(blocked); continue; }
    await aiGenerate(step);
  }
}

async function aiConfirm() {
  try {
    aiData = await api(aiUrl('/confirm'), { method: 'POST' });
    aiRender();
    aiToast('3 组装与整合已确认');
    // 确认不拦缺口（型号未定的方案也要能往下走），但必须说出来 ——
    // 报价必填项没齐，这台成品到了报价那头就是几个空格。
    const stat = aiData?.param_checklist?.summary;
    const shortfall = stat ? stat.required_total - stat.required_filled : 0;
    aiSay(shortfall > 0
      ? `本步结果已确认。注意还有 ${shortfall} 项报价必填的成品参数没有值，`
        + `报价测算单上这几格会是空的，进 3.1 之前建议先补齐。`
      : '本步结果已确认，报价成品参数的必填项已齐，可以进入 3.1 汇总结果了。');
  } catch (error) { aiToast(error.message || '确认失败', true); }
}

/** 2.1 的零件与它们已测算的单件成本 —— 整合图纸页要拿它说明"底价从哪来"。 */
async function aiLoadParts() {
  try {
    const aggregate = await api(`/api/projects/${encodeURIComponent(aiPid)}/summary`);
    const parts = aggregate?.ir?.parts || [];
    const costs = await Promise.all(parts.map(part =>
      api(`/api/projects/${encodeURIComponent(aiPid)}/parts/${encodeURIComponent(part.part_id)}/cost`)
        .catch(() => ({}))));
    return parts.map((part, index) => {
      const total = costs[index]?.summary?.computed_total;
      return {
        part_id: part.part_id, name: part.name, quantity: part.quantity || 1,
        hasCost: total != null, unitCost: total,
      };
    });
  } catch { return []; }
}

async function aiStart() {
  if (!aiPid) { if(window.TechEmbed&&window.TechEmbed.embedded){window.TechEmbed.exitToTechHome();return;} location.href = 'home.html'; return; }
  aiBindShell();
  $ai('aiSideProject').textContent = `项目 ${aiPid}`;
  aiLoadModelLabel();
  aiLoadManifest();
  aiLoadAgentMeta();
  try {
    const [payload, parts] = await Promise.all([api(aiUrl('')), aiLoadParts()]);
    aiData = payload;
    aiParts = parts;
    $ai('aiRequirement').value = aiPlan().requirement_note || '';
    const state = aiData.status || {};
    // 落在"下一件该做的事"上。
    aiTab = state.has_process ? 'process' : state.has_params ? 'params' : 'drawings';
    aiRender();
    await aiReplayTimeline();
    aiStatus(state.confirmed ? '本步已确认' : '就绪');
    if (!parts.length) aiSay('还没有拿到 2.1 的零件清单。请先完成 2.1 图纸解析 —— 3 组装与整合是把那些零件装回整机。');
  } catch (error) {
    $ai('aiBody').innerHTML = `<div class="inline-empty error">读取失败：${esc(error.message)}</div>`;
    aiStatus(`读取失败：${error.message}`, true);
  }
}

aiStart();

/* 统一看板协议：3 组装与整合的整合分析与发送财务注册成语义化动作，内部页签注册成语义化视图。
 * 父壳底栏 / 标题行只发动作名 / 视图名，页面上的原按钮继续走同一份实现。 */
(function aiRegisterTechBoardActions() {
  if (!window.TechBoardRuntime || typeof window.TechBoardRuntime.registerActions !== 'function') return;
  // aiStatusState / aiAnalyzed / aiSetTab 都挂在模块作用域（见文件上方）：
  // 闭包外的收口函数要用同一份实现，这里不再重复声明。
  window.TechBoardRuntime.registerActions({
    runIntegration: {
      label: '一键分析整合图纸',
      deferred: true,
      run: () => {
        const button = $ai('aiStart');
        if (button && button.disabled) {
          return { ok: false, error: { code: 'not-ready', message: '当前不能开始整合分析，请先补齐图纸与需求信息。' } };
        }
        if (aiDeferredBusy) {
          return { ok: false, error: { code: 'busy', message: '已有任务在执行，请稍候。' } };
        }
        // 整合分析是长任务（参数推荐 + 组装工艺，两轮模型调用）：只启动，秒级回执。
        aiDeferredBusy = true;
        aiLastSettle = null;
        window.TechBoardRuntime.updateActionState('runIntegration', { busy: true });
        aiRunIntegrationInBackground();
        return { ok: true };
      },
      getState: () => {
        const analyzed = aiAnalyzed();
        const busy = aiBusy || aiDeferredBusy;
        // 开始整合分析是「整合图纸」页的起点：整条链（参数推荐 + 组装工艺）从这里发起，
        // 参数页与工艺页各自有本步的生成 / 确认动作，不再重复出现这颗按钮。
        const show = aiTab === 'drawings';
        return { visible: show, enabled: true, busy: busy, analyzed: analyzed,
                 role: analyzed ? 'aux' : 'primary', order: 10,
                 hint: '生成参数推荐与组装工艺' };
      },
    },
    // 组装工艺页的收口动作：工艺生成后它就是这一步的主按钮 —— 没确认过就先按既有
    // /process/confirm 确认（不做死按钮），再由既有弹窗选接收人把任务交给财务。
    // 弹窗要人操作，所以是 deferred：条目只启动、秒级回执，成败由后台链路自报。
    sendIntegrationToFinance: {
      label: '确认工艺并发送财务',
      deferred: true,
      run: () => {
        // 同步回执里只判 L1 生成依赖（参数推荐 / 组装工艺跑过没有），并如实说清楚；
        // 「组装工艺还没确认」与其它 L2 缺口由后台链路的「仍要继续」签字处理。
        const why = aiFinanceBlocker();
        if (why) { aiStatus(why, true); return { ok: false, error: { code: 'not-ready', message: why } }; }
        if (aiDeferredBusy) {
          return { ok: false, error: { code: 'busy', message: '已有任务在执行，请稍候。' } };
        }
        aiDeferredBusy = true;
        aiLastSettle = null;
        window.TechBoardRuntime.updateActionState('sendIntegrationToFinance', { busy: true });
        aiSendToFinanceInBackground();
        return { ok: true };
      },
      getState: () => {
        const analyzed = aiAnalyzed();
        // 它的闸门要求参数推荐与组装工艺都跑过，因此只在「组装工艺」页出现；
        // 工艺生成后它是本页唯一主按钮，生成前主按钮仍是「一键生成组装工艺」。
        const show = aiTab === 'process';
        return { visible: show, enabled: true, busy: aiBusy || aiDeferredBusy,
                 analyzed: analyzed, role: (show && aiHasProcess()) ? 'primary' : 'aux', order: 42,
                 hint: '确认工艺并把任务交给财务做成本测算' };
      },
    },
    // 整合图纸页的收口动作：分析出结果后，「确认图纸并进入参数推荐」才是这一步的下一步，
    // 财务交接不抢主按钮（它只属于组装工艺页）。role 与可见性同一个条件，全页签只此一颗。
    confirmDrawingsAndNext: {
      label: '确认图纸并进入参数推荐',
      order: 15,
      run: () => aiConfirmDrawingsAndNext(),
      getState: () => {
        const show = aiTab === 'drawings' && aiAnalyzed();
        return { visible: show, enabled: true, busy: aiBusy,
                 analyzed: show, role: show ? 'primary' : 'aux', order: 15,
                 hint: '整合结果核对无误后进入参数推荐' };
      },
    },
    // 参数推荐 / 组装工艺两个页签的专属动作：全部复用既有实现（aiGenerate / aiSaveEdits /
    // aiConfirmStep / aiParamsAutofill / aiParamsFinalize），不另写一套。与 runIntegration
    // 一样都是长任务：只启动、立即回执（deferred），进度与收尾由本页 aiPublishTask 自报。
    // visible 由当前页签决定，左侧工具栏按这份快照渲染、右侧页内按钮在嵌入态隐藏。
    generateIntegrationParams: {
      label: '一键生成参数推荐',
      role: 'aux',
      order: 30,
      deferred: true,
      // 一次点完就是完整链路：aiGenerate('params') 生成 → aiParamsAutofill() 自动补全
      // 报价必填缺口 → aiParamsFinalize(false) 把补上的值落库。三步都是既有实现与既有接口，
      // 左侧因此不再需要「智能补全 / 保存补填 / 确认参数已齐」三颗按钮。
      run: () => { aiGenerateParamsFully(); return { ok: true }; },
      getState: () => {
        const show = aiTab === 'params';
        // 未生成时它就是参数页的主按钮；生成之后主按钮交给「确认并进入下一步」。
        return { visible: show, enabled: true, busy: aiBusy,
                 role: aiHasParams() ? 'aux' : 'primary', order: 30 };
      },
    },
    // 参数页的收口动作：确认参数已齐（按报价必填校验）+ 确认参数推荐 + 切到组装工艺，
    // 用户只点一次；必填不齐时返回真实原因、不切页。
    confirmParamsAndNext: {
      label: '确认并进入下一页签',
      order: 35,
      // 缺项时要弹确认框等人点「仍要继续」—— 这段等待不能算进桥的 20s 超时，
      // 所以 run 只启动后台链路并立即回执；成败由链路自己播报（标题行 + 普通会话输出）。
      deferred: true,
      run: () => {
        Promise.resolve(aiConfirmParamsAndNext()).then((result) => {
          if (result && result.ok === true) return;
          const message = (result && result.error && result.error.message)
            || '确认参数推荐失败，请查看右侧看板提示。';
          aiStatus(message, true);
          aiSay(`⚠ ${message}`);
        }).catch((error) => {
          const message = (error && error.message) || '确认参数推荐失败，请稍后重试。';
          aiStatus(message, true);
          aiSay(`⚠ ${message}`);
        });
        return { ok: true };
      },
      // role 只在这里声明一次：整个 3 组装与整合页只允许有「当前那一个」主按钮。
      getState: () => ({ visible: aiTab === 'params' && aiHasParams(),
                         enabled: true, busy: aiBusy, role: 'primary', order: 35 }),
    },
    generateIntegrationProcess: {
      label: '一键生成组装工艺',
      role: 'aux',
      order: 40,
      deferred: true,
      run: () => { aiGenerate('process'); return { ok: true }; },
      getState: () => {
        const show = aiTab === 'process';
        // 未生成工艺时它就是本页主按钮；生成之后主按钮交给「确认并进入下一步」。
        return { visible: show, enabled: true, busy: aiBusy,
                 role: aiHasProcess() ? 'aux' : 'primary', order: 40 };
      },
    },
    // 组装工艺页的收口不再给「去 4 成本测算」单独一颗按钮：成本测算是财务经理那一步，
    // 工艺经理在这里只需把任务交给财务。工艺生成后的主按钮是「确认工艺并发送财务」，
    // 它自己会先把组装工艺确认掉 —— 所以这一颗退出左侧栏（动作注册与实现照旧保留，
    // Agent / 内部链路仍按名字调用）。
    confirmProcessAndNext: {
      label: '确认并进入下一步',
      order: 45,
      run: () => aiConfirmProcessAndNext(),
      getState: () => ({ visible: false, enabled: true, busy: aiBusy,
                         role: 'aux', order: 45 }),
    },
    saveIntegrationParams: {
      label: '保存参数',
      role: 'aux',
      order: 50,
      run: () => { aiSaveEdits('params'); return { ok: true }; },
      // 参数表一直可编辑，保存只是链路内部的一步：不再占左侧栏一颗按钮。
      getState: () => ({ visible: false, enabled: true, busy: aiBusy }),
    },
    confirmIntegrationParams: {
      label: '确认参数推荐',
      role: 'aux',
      order: 60,
      deferred: true,
      run: () => { aiConfirmStep('params'); return { ok: true }; },
      // 已并入「确认并进入下一步」；动作本身保留，Agent / 内部链路仍可调用。
      getState: () => ({ visible: false, enabled: true, busy: aiBusy }),
    },
    confirmIntegrationProcess: {
      label: '确认组装工艺',
      role: 'aux',
      order: 70,
      deferred: true,
      run: () => { aiConfirmStep('process'); return { ok: true }; },
      // 已并入「确认并进入下一步」；动作本身保留，Agent / 内部链路仍可调用。
      getState: () => ({ visible: false, enabled: true, busy: aiBusy }),
    },
    autofillIntegrationParams: {
      label: '智能补全',
      role: 'aux',
      order: 80,
      deferred: true,
      run: () => { aiParamsAutofill(); return { ok: true }; },
      // 已并入「生成参数推荐」的自动补全；动作本身保留。
      getState: () => ({ visible: false, enabled: true, busy: aiBusy }),
    },
    saveIntegrationParamsFinal: {
      label: '保存补填',
      role: 'aux',
      order: 90,
      deferred: true,
      run: () => { aiParamsFinalize(false); return { ok: true }; },
      // 已并入「生成参数推荐」的链路；动作本身保留。
      getState: () => ({ visible: false, enabled: true, busy: aiBusy }),
    },
    confirmIntegrationParamsFinal: {
      label: '确认参数已齐',
      role: 'aux',
      order: 100,
      deferred: true,
      run: () => { aiParamsFinalize(true); return { ok: true }; },
      // 已并入「确认并进入下一步」；动作本身保留。
      getState: () => ({ visible: false, enabled: true, busy: aiBusy }),
    },
    // Agent 改完参数 / 工序后让看板重新拉取并渲染（复用 aiStart 的读取路径）。
    refreshIntegration: {
      label: '刷新整合看板',
      role: 'aux',
      order: 110,
      silent: true,
      run: async () => { await aiStart(); return { ok: true }; },
      // 只退出左侧栏：刷新仍由 refresh-data 命令与 Agent 工具走 executeAction 触发。
      getState: () => ({ visible: false, enabled: true, busy: aiBusy, analyzed: aiAnalyzed() }),
    },
    // 左侧 Agent 请求跑某一环节：复用既有 aiGenerate(step)，真正在 3 组装与整合内跑流水线。
    integrationStep: {
      label: '运行整合环节',
      role: 'aux',
      order: 120,
      deferred: true,
      // 会真的跑起来：按这一次的 step（必要时带上零件号）给左侧一条用户口吻的回声。
      prompt:(payload) => {
        const step = String((payload && payload.step) || '').toLowerCase();
        const partId = String((payload && payload.part_id) || '').trim();
        if (step === 'params') return partId ? `帮我生成 ${partId} 的参数推荐。` : '帮我生成参数推荐。';
        if (step === 'process') return partId ? `帮我生成 ${partId} 的组装工艺。` : '帮我生成组装工艺。';
        return '';
      },
      run: (payload) => {
        const step = String((payload && payload.step) || '').toLowerCase();
        // 成本测算属于第 4 阶段（成本页），不再从本步发起 —— 这里只跑参数推荐与组装工艺。
        const labels = { params: '参数推荐', process: '组装工艺' };
        if (!labels[step]) {
          return { ok: false, error: { code: 'bad-step',
                                       message: 'step 只能是 params / process；成本测算请在成本步骤发起' } };
        }
        if (aiDeferredBusy) {
          return { ok: false, error: { code: 'busy', message: '已有任务在执行，请稍候。' } };
        }
        const options = { label: labels[step] };
        aiDeferredBusy = true;
        aiLastSettle = null;
        window.TechBoardRuntime.updateActionState('integrationStep', { busy: true });
        aiIntegrationStepInBackground(step, options);
        return { ok: true };
      },
      // 只给 Agent 工具用（RequestIntegrationStep → tech_ui "integration-step"）：
      // 它必须拿到 payload.step，而左侧栏只发 { label, role }，用户点了必然报 bad-step。
      // 隐藏入口不影响执行 —— runEntry 只认动作名，execute-action 照旧能跑到这里。
      getState: () => ({ visible: false, enabled: true, busy: aiBusy }),
    },
    // 图纸上传只能由用户完成：切到「整合图纸」页签并聚焦上传入口，不代传二进制。
    openIntegrationDrawings: {
      label: '整合图纸',
      role: 'aux',
      order: 130,
      // 会真的跑起来（切到整合图纸页签并聚焦上传入口）：说清要干什么。
      prompt:'把整合图纸打开，我要补一张图纸。',
      run: async () => {
        aiSetTab('drawings');
        const button = $ai('aiUploadBtn');
        if (button) button.focus();
        return { ok: true };
      },
      // 同样只给 Agent 工具用（UploadIntegrationDrawing → "open-integration-drawings"）：
      // 对用户而言标题行右侧的子页签已经是同一目标的入口，左侧栏不再重复一颗。
      getState: () => ({ visible: false, enabled: true, busy: false,
                         active: aiTab === 'drawings' ? 'drawings' : null }),
    },
  });
  window.TechBoardRuntime.registerViews({
    drawings: { run: () => aiSetTab('drawings'), getState: () => ({ active: aiTab === 'drawings' ? 'drawings' : null }) },
    params: { run: () => aiSetTab('params'), getState: () => ({ active: aiTab === 'params' ? 'params' : null }) },
    process: { run: () => aiSetTab('process'), getState: () => ({ active: aiTab === 'process' ? 'process' : null }) },
  });
})();
