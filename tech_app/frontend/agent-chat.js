/* 2.1 图纸解析页的 Agent 对话框。
 *
 * 后端是 open-claude 的 Conversation（backend/services/oc_agent.py），通过
 * /api/projects/{id}/agent/* 以 SSE 交互，事件格式沿用 open-claude web 桥：
 *   text / tool_use / tool_result / error / done
 *
 * 与页面的关系：
 *   - #intent 与 #btnParse 就在对话第一条消息里，因此 app.js 原有的赋值和点击
 *     绑定不用改动 —— 设计意图天然"出现在对话内容中"，开始解析按钮天然在它下方。
 *   - 左侧零件清单 / 待澄清问题 / 解析报告 / 任务文件入口与统一工作台 #techChatActions
 *     会话快捷能力按钮只负责导航：统一工作台（tech-workbench.html）把看板视图名交给
 *     TechBoardBridge.navigateView，业务动作走 executeAction；独立 2.1 页没有父壳桥，
 *     由同页看板模块（window.TechBoardViews）就地打开同一份面板。
 *   - 具体内容一律留在右侧看板内部：零件清单、待澄清问题、解析报告、解析视图、版本
 *     与校核、任务文件都由看板（app.js）注册成视图后展开，父壳不再持有业务抽屉。
 */
(() => {
  if (new URLSearchParams(location.search).has("embed")) return; // 统一工作台内嵌（embed=1）：会话宿主在父壳 techChatPane，子页不再自建/自连会话。
  // 统一工作台可以在不重载页面的情况下换项目（历史抽屉 / 首页卡片 / 看板 set_stage /
  // 上下一步都只 pushState），所以项目绑定必须是可重绑的 let，而不是加载时的一次性快照：
  // 一次性快照会让左侧永远停在首次进入时的项目，新项目的历史带不回来，还会串出上一个
  // 项目的对话、任务文件与进度卡。重绑入口见下面 setProject()。
  let projectId = new URLSearchParams(location.search).get("project") || "";
  const $ = id => document.getElementById(id);
  const thread = $("ocThread");
  const tinner = $("ocTinner");
  const input = $("ocInput");
  const sendBtn = $("ocSend");
  if (!thread || !tinner || !input || !sendBtn) return;

  let meta = null;
  let busy = false;
  // 本轮 UpdatePartParameters 的 tool_use id → 工具结果里的改写详情。
  // 改完不能当场刷新（tool_use 事件早于工具执行），所以攒到本轮结束再统一刷。
  const pendingEdits = new Map();

  const authHeaders = (json = false) => {
    const headers = {};
    // 两个键的兜底顺序要和其余页面一致，否则会话只剩 cad_engine_token 时
    // 对话请求会漏掉 Authorization。
    const token = localStorage.getItem("authToken")
      || localStorage.getItem("cad_engine_token") || "";
    if (token) headers.Authorization = `Bearer ${token}`;
    if (json) headers["Content-Type"] = "application/json";
    return headers;
  };
  const api = path => `/api/projects/${encodeURIComponent(projectId)}/agent${path}`;
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const scrollDown = () => { thread.scrollTop = thread.scrollHeight; };
  // 会话条目落库：统一走 POST /agent/event（回放走 GET /agent/events?stage=&source=）。
  // 渲染不依赖落库结果 —— 网络失败只留痕，不阻塞会话。回放期间不重复写回。
  function persistSessionEvent(event) {
    if (!projectId || replayingHistory) return null;
    const payload = Object.assign({ source: "shell", stage: boardStage() }, event || {});
    return fetch(api("/event"), {
      method: "POST", headers: authHeaders(true), body: JSON.stringify(payload),
    }).then(response => (response.ok ? response.json().catch(() => null) : null))
      .catch(() => null);
  }

  // 统一工作台（tech-workbench.html）与独立 2.1 页共用本脚本。父壳只有
  // #techChatPane 一个会话宿主，右侧九步看板是 iframe：父壳模式必须走持久控件 +
  // 看板桥（TechBoardBridge）；独立打开 2.1 页时由同页看板模块就地打开同一份面板。
  // 会话栏不承载任何业务抽屉 —— 零件、问题、报告、任务文件正文全部留在右侧看板。
  const inUnifiedWorkbench = Boolean($("techChatPane"));

  // ---------------------------------------------------------------- 弹层
  let openPop = null;
  const onDocClick = event => { if (openPop && !openPop.contains(event.target)) closePop(); };
  function closePop() {
    if (!openPop) return;
    openPop.remove();
    openPop = null;
    document.removeEventListener("click", onDocClick);
  }
  function showPop(anchor, build) {
    closePop();
    const pop = el("div", "oc-pop");
    // 构建在插入之前，所以 build 里一抛异常，弹层就整个不出现 —— 表现是"点了没反应"，
    // 连报错都看不见。兜住它，至少让失败可见。
    try {
      build(pop);
    } catch (error) {
      pop.replaceChildren(el("div", "oc-set-hint", `面板渲染失败：${error.message}`));
      console.error("[agent-chat] popover build failed", error);
    }
    document.body.append(pop);
    const rect = anchor.getBoundingClientRect();
    const width = pop.offsetWidth;
    const height = pop.offsetHeight;
    // 输入区的 ＋ 在页面底部，向上弹出才不会被裁掉。
    const below = rect.bottom + 6 + height <= window.innerHeight;
    pop.style.top = below ? `${rect.bottom + 6}px` : `${Math.max(8, rect.top - height - 6)}px`;
    pop.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 12))}px`;
    openPop = pop;
    setTimeout(() => document.addEventListener("click", onDocClick), 0);
  }

  // ------------------------------------------------- 模型参数设置
  // 面板实现在 llm-settings-panel.js 里，与首页「模型设置」是同一份 —— 两处各写
  // 一套表单正是之前配置对不上的根因。这里只负责把它挂进弹层。
  function settingsPanel(anchor) {
    showPop(anchor, pop => {
      pop.classList.add("oc-settings");
      // 点面板内部（下拉、输入框）不该把弹层本身关掉。
      pop.addEventListener("click", event => event.stopPropagation());
      const host = el("div", "llm-set-host");
      pop.append(host);
      if (!window.LlmSettingsPanel) {
        host.append(el("div", "llm-set-status err", "模型设置面板未加载"));
        return;
      }
      window.LlmSettingsPanel.mount(host, {
        onSaved: data => setModelLabel(data.model_label || data.model || ""),
      });
    });
  }

  // ＋ 菜单：把原左栏的四组能力收进来。
  const PLUS_ITEMS = [
    ["upload", "补充需求图纸", "上传或替换本次评估的需求原图"],
    ["evidence", "解析视图", "查看解析后的零件标注视图（输入文件见右侧「任务文件」）"],
    ["import3d", "导入已有模型", "STEP/STP 反向解析，不覆盖已有实体几何"],
    ["review", "版本与校核审查", "版本对比、送审、AI 校核与型号联网核验"],
  ];
  function plusMenu(anchor) {
    showPop(anchor, pop => {
      pop.append(el("div", "oc-cap-h", "补充资料与审查"));
      PLUS_ITEMS.forEach(([key, label, hint]) => {
        const item = el("button", "oc-popitem");
        item.type = "button";
        item.append(el("div", null, label));
        item.append(el("div", "d", hint));
        item.onclick = () => {
          closePop();
          // 独立 2.1 页没有父壳桥：同一套视图名交给本页看板模块就地打开。
          boardNavigateView(TECH_BOARD_VIEW_ENTRIES[`capability:${key}`] || key, { label });
        };
        pop.append(item);
      });
    });
  }

  // ---------------------------------------------------------------- 元信息
  // 统一工作台（tech-workbench.html）的会话列头部与右侧项目栏也展示连接/模型：
  // 有对应 DOM 时同步，旧页面（图纸解析页等）没有这些节点时静默跳过。
  function techShellConn(text, ok) {
    const dot = $("techConnDot");
    const label = $("techConnText");
    if (dot) {
      dot.classList.remove("ok", "err");
      if (ok === true) dot.classList.add("ok");
      else if (ok === false) dot.classList.add("err");
    }
    if (label) label.textContent = text;
  }
  async function loadMeta() {
    techShellConn("连接中…");
    try {
      const response = await fetch(api("/meta"), { headers: authHeaders() });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      if (data.available === false) {
        techShellConn("未连接", false);
        pushSystem(`Agent 暂不可用：${data.reason || "未知原因"}。图纸解析等平台功能不受影响。`);
        input.placeholder = "Agent 未就绪，仍可使用「开始解析」按钮";
        return;
      }
      meta = data;
      techShellConn("已连接", true);
      setModelLabel(data.model || "未知模型");
      const cwd = $("ocSideCwd");
      if (cwd) {
        cwd.textContent = (data.cwd || "").split(/[\\/]/).pop() || data.cwd || "项目工作区";
        cwd.title = data.cwd || "";
      }
      const profile = $("ocSideProfile");
      // 界面上只说"会话已就绪"，不外露底层运行时的 profile 概念。
      if (profile) profile.textContent = "会话已就绪";
    } catch (error) {
      techShellConn("未连接", false);
      pushSystem(`读取 Agent 信息失败：${error.message}`);
    }
  }
  // ---------------------------------------------------------------- 历史回放
  // 打开已绑定项目时先取回该项目持久化的完整会话（用户 / 助手 / 工具轨迹），再进入
  // 正常会话状态；空历史才保留空态，读取失败必须显式报错，绝不静默展示空会话。
  let historyLoaded = false;
  // 回放历史期间不再把同一条内容写回时间线（否则每打开一次项目就重复追加一遍）。
  let replayingHistory = false;
  async function loadHistory() {
    if (!projectId || historyLoaded) return;
    historyLoaded = true;
    try {
      const response = await fetch(api("/history"), { headers: authHeaders() });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
      if (data.available === false) throw new Error(data.reason || "会话历史暂不可用");
      renderHistory((data && data.messages) || [], (data && data.timeline) || []);
    } catch (error) {
      // 允许重新登录 / 服务恢复后再取一次，不把失败伪装成空会话。
      historyLoaded = false;
      pushSystem(`读取历史会话失败：${(error && error.message) || "未知错误"}`);
    }
  }

  // 换项目：左侧会话的项目绑定必须跟着统一工作台走，然后重新回放**目标项目**已持久化的
  // 完整会话。父壳（tech-workbench.js applyStage / popstate）只调这一个入口，不直接改会话
  // 内部状态。三件事必须同时成立：
  //   1. 只重绑 + 重放历史，绝不调用 /agent/new 或 resetTaskFlow() —— 换项目不是重置任务，
  //      后端已持久化的会话、解析结果与项目数据一个都不能动；
  //   2. 上一个项目留在会话里的可见内容（消息、任务卡映射 + DOM、结果条）全部清掉，
  //      否则 A 项目的对话会串到 B 项目上；任务进度宿主与设计意图空态是页面结构，只清内容；
  //   3. 同项目重复同步直接返回（幂等），用户正在输入还没发送的草稿保留。
  async function setProject(nextId) {
    const next = String(nextId || "");
    if (next === String(projectId || "")) return;
    projectId = next;
    historyLoaded = false;                 // 放开一次性闸门，允许回放新项目的历史
    tinner.querySelectorAll(".oc-amsg, .oc-ubub").forEach(node => node.remove());
    taskProgressCards.clear();
    pendingEdits.clear();
    const progressHost = $("ocTaskProgressHost");
    if (progressHost) progressHost.replaceChildren();
    boardResultSummary = null;
    hasParsedIR = false;
    if (resultBox) resultBox.hidden = true;
    if (!projectId) {
      techShellConn("未连接", false);
      setPillLabel("未选择项目");
      return;
    }
    techShellConn("连接中…");
    await loadHistory();
    await loadMeta();
    loadFiles();
    renderComponentMatch();
    refreshResultChips();
  }

  // 按统一时间线回放：Agent 消息、任务卡、阶段过程文字、工具卡与工具结果都由
  // TechSessionTimeline 的同一套 (ts, seq) 规则排成一条列表，不再「消息全在前、
  // 卡片钉底部」。tech_ui 不再跳过 —— 重进项目必须恢复结构化确认卡。
  function renderHistory(events, timeline) {
    const rows = window.TechSessionTimeline
      ? window.TechSessionTimeline.forShell({ messages: events || [], events: timeline || [] })
      : (events || []);
    replayingHistory = true;
    let ctx = null;
    rows.forEach(raw => {
      if (!raw || typeof raw !== "object") return;
      // 时间线条目用 kind（normalize 补齐），Agent JSONL 消息用 type：这里统一成 type，
      // 下面几个既有分支的写法与语义保持不变。
      const event = Object.assign({}, raw);
      if (!event.type && event.kind) event.type = event.kind;
      if (event.type === "user") {
        addUser(String(event.text || ""));
        ctx = null;
        return;
      }
      if (event.type === "assistant") {
        ctx = addAssistant();
        setAssistantState(ctx, "succeeded");
        ctx.full = String(event.text || "");
        ctx.text.classList.add("rendered");
        ctx.text.innerHTML = renderMarkdown(ctx.full);
        return;
      }
      if (event.type === "task") {
        replayTimelineTask(event);
        return;
      }
      if (event.type === "session-note") {
        noteInThread(String(event.text || ""));
        return;
      }
      if (event.type === "tool_use") {
        if (!ctx) ctx = addAssistant();
        setAssistantState(ctx, "succeeded");
        addToolCard(ctx, { id: event.id, name: event.name, input: event.input || {} });
        return;
      }
      if (event.type === "tool_result") {
        if (!ctx || !ctx.cards[event.tool_use_id]) return;
        setToolResult(ctx, {
          tool_use_id: event.tool_use_id,
          content: event.content,
          is_error: !!event.is_error,
        });
      }
    });
    replayingHistory = false;
    scrollDown();
  }
  // 回放持久化的任务卡：同一 task.id 只画一张，进度行按行去重追加、状态就地更新。
  function replayTimelineTask(event) {
    const task = (event && event.task) || {};
    const taskId = String(task.id || "");
    if (!taskId) return;
    // 走同一个渲染入口：进度行按 cursor 去重 —— 回放与看板桥播报撞在一起也不会叠加。
    renderTaskProgress({
      label: String(task.label || event.text || "任务"),
      taskId: taskId,
      status: String(task.status || "running"),
      log: (task.steps || []).slice(),
      error: String(task.error || ""),
    });
  }
  // 右上角模型文字只表达「模型设置」里的当前语言模型；Agent 会话是否可用是另一件事，
  // 不能用「未连接 / Agent 未就绪」覆盖真实模型。没有配置时才显示提示。
  function modelLabelFromSettings(settings) {
    const options = (settings && settings.options) || [];
    const id = String((settings && settings.model) || "").trim();
    const option = options.find(item => item && item.id === id);
    return (option && option.label) || id || "未配置模型";
  }
  // 模型口径统一到唯一事实源：报价的 /api/settings。技术工艺不再有任何独立设置接口，
  // 读取失败时只回「未配置模型」，不用连接状态或失败状态覆盖真实模型名。
  async function refreshTechShellModel() {
    try {
      const settings = window.LlmSettingsPanel && typeof window.LlmSettingsPanel.load === "function"
        ? await window.LlmSettingsPanel.load()
        : null;
      techShellModel(modelLabelFromSettings(settings));
    } catch (error) {
      techShellModel("未配置模型");
    }
  }

  function techShellModel(text) {
    const value = String(text || "").trim();
    const info = $("techModelInfo");
    if (info) {
      const stateWord = ["未连接", "未就绪", "未选择", "未知模型", "未配置", "Agent"].some(word => value.includes(word));
      info.textContent = stateWord || !value ? value : `· ${value}`;
    }
  }
  function setPillLabel(text) {
    const pill = $("ocModelPill");
    if (pill) pill.querySelector("[data-model-name]").textContent = text;
  }
  function setModelLabel(text) {
    setPillLabel(text);
    techShellModel(text);
  }

  // ---------------------------------------------------------------- 渲染
  const escapeHtml = value => String(value).replace(/[&<>]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[ch]));
  function renderMarkdown(source) {
    const blocks = [];
    let text = String(source).replace(/```([\s\S]*?)```/g, (_match, code) => {
      blocks.push(code.replace(/^[a-zA-Z0-9]*\n/, ""));
      return ` ${blocks.length - 1} `;
    });
    text = escapeHtml(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    text = text.split(/\n{2,}/).map(part => `<p>${part.replace(/\n/g, "<br>")}</p>`).join("");
    return text.replace(/ (\d+) /g,
      (_match, index) => `</p><pre><code>${escapeHtml(blocks[+index])}</code></pre><p>`);
  }

  const TOOL_ICONS = {
    Read: "📄", Glob: "🗂", Grep: "🔍", Skill: "🪄", Agent: "🤖",
    TaskCreate: "☑", TaskUpdate: "☑", TaskList: "☑", TaskGet: "☑",
    GetProjectState: "📋", ListParts: "🧩", GetPartDetail: "🔧",
    GetOpenQuestions: "❓", RequestParse: "▶",
    LookupComponentLibrary: "🔩", LookupProcessLibrary: "⚙", LookupCostLibrary: "💰",
    UpdatePartParameters: "✏",
  };
  const toolIcon = name => (name.startsWith("mcp__") ? "🔌" : TOOL_ICONS[name] || "🛠");
  function toolSubtitle(name, params) {
    const input_ = params || {};
    if (name === "Read") return input_.file_path || "";
    if (name === "Glob" || name === "Grep") {
      return `${input_.pattern || ""}${input_.path ? ` in ${input_.path}` : ""}`;
    }
    if (name === "Skill") return `/${input_.skill || ""}${input_.args ? ` ${input_.args}` : ""}`;
    if (name === "Agent") return `(${input_.subagent_type || "general-purpose"}) ${input_.description || ""}`;
    if (name === "GetPartDetail" || name === "LookupProcessLibrary") return input_.part_id || "";
    if (name === "LookupComponentLibrary") return input_.part_id || "全部零件";
    if (name === "LookupCostLibrary") {
      return `${input_.part_id || ""}${input_.quantity ? ` × ${input_.quantity}` : ""}`;
    }
    if (name === "RequestParse") return input_.reason || "请求开始解析";
    if (name === "UpdatePartParameters") {
      // 写操作要一眼看清改的是谁、改了什么，不能只显示零件号。
      const fields = [
        input_.name != null ? `名称=${input_.name}` : "",
        input_.quantity != null ? `数量=${input_.quantity}` : "",
        input_.material_spec != null ? `材料=${input_.material_spec}` : "",
        ...(input_.feature_updates || []).map(
          item => `特征#${(item.feature_index ?? 0) + 1}.${item.field}=${item.value}`),
      ].filter(Boolean);
      return `${input_.part_id || "?"} → ${fields.join("，") || "无改动"}`;
    }
    try { return JSON.stringify(input_).slice(0, 140); } catch { return ""; }
  }

  /* 工具轨迹：工具名 → 中文业务文案。主行只给业务语言，原始工具名 / 入参 / 结果
     收进默认折叠的详情块。这里只做展示，不发请求、不调看板通道、不写业务分支。 */
  const TOOL_TRACE_LABELS = {
    GetProjectState: "读取项目状态",
    ListParts: "读取零件清单",
    GetPartDetail: "读取零件详情",
    GetOpenQuestions: "读取待澄清问题",
    LookupComponentLibrary: "查询零部件库",
    LookupProcessLibrary: "查询工艺库",
    LookupCostLibrary: "查询成本库",
    UpdatePartParameters: "修改零件参数",
    GetIntegrationState: "读取整合状态",
    ListIntegrationParams: "读取整合参数",
    UpdateIntegrationParams: "修改整合参数",
    UpdateIntegrationProcess: "修改组装工序",
    RequestIntegrationStep: "发起整合生成",
    RequestParse: "请求开始解析图纸",
    GetRequirementDraft: "读取需求草案",
    UpdateRequirementFields: "修改需求字段",
    AttachRequirementFiles: "关联需求附件",
    ExtractRequirement: "解析需求",
    GetRequirementTask: "读取需求解析任务",
    GetRequirementAiFill: "读取需求智能补全",
    SubmitRequirementConfirmation: "提交需求确认",
    GetRequirementPrecheck: "读取需求预检查",
    GetRequirementClarifications: "读取需求待澄清问题",
    SaveRequirementConfirmationNote: "保存需求确认说明",
    ConfirmRequirement: "确认需求",
    ReturnRequirementToDraft: "退回需求草稿",
    GetRequirementReviewMaterials: "读取需求审核材料",
    GetRequirementReviewSummary: "读取需求审核摘要",
    SaveRequirementReviewNote: "保存需求审核意见",
    ApproveRequirementReview: "审核需求",
    RejectRequirementReview: "驳回需求审核",
    UploadIntegrationDrawing: "上传整合图纸",
    ConfirmIntegrationParams: "确认整合参数",
    ConfirmIntegrationProcess: "确认组装工序",
    SendIntegrationToFinance: "把工艺发送财务",
    GetCostReviewState: "读取成本测算状态",
    ListCostReviewParts: "读取成本测算零件",
    RunCostReviewPart: "测算单个零件成本",
    RunCostReviewAssembly: "测算整机成本",
    RunCostReviewAll: "逐件测算并汇总成本",
    UpdateCostReviewNote: "修改成本说明",
    ConfirmCostReview: "确认成本",
    WriteCostReviewMaterial: "写入成本物料",
    ReturnCostReviewToProcess: "把成本结果退回工艺",
    SendCostReviewToQuote: "把成本结果发送报价",
    GetSummaryData: "读取汇总数据",
    GetProcessReport: "读取工艺报告",
    GenerateProcessReportDraft: "生成报告草稿",
    UpdateProcessReportFields: "修改报告字段",
    SaveProcessReportDistribution: "保存报告发布范围",
    SubmitProcessReportReview: "提交报告审核",
    GetProcessReportReview: "读取报告审核信息",
    GetReportReviewSummary: "读取报告审核摘要",
    SaveReportReviewNote: "保存报告审核意见",
    ApproveProcessReport: "审核通过报告",
    RejectProcessReport: "退回报告",
    GetReportPublishState: "读取报告发布状态",
    ListReportPublishRecipients: "读取报告发布对象",
    UpdateReportDistribution: "更新报告发布范围",
    PublishProcessReport: "发布报告",
    SendReportToQuote: "回传报价",
    CreateReportNewVersion: "新建报告版本",
    GetReportPublishResult: "读取报告发布结果",
  };
  // tech_ui 没有单一业务含义，按八个固定 action 各自给中文文案。
  const TOOL_TRACE_UI_ACTIONS = {
    focus_view: "切换看板视图",
    refresh_view: "刷新看板",
    fill_fields: "回填看板字段",
    select_part: "选中看板零件",
    show_result_actions: "展示结果入口",
    show_progress: "展示任务进度",
    set_stage: "切换当前步骤",
    request_confirmation: "请求用户确认",
  };

  // 业务行文案：已登记工具查表；tech_ui 走 action 表（可拼上 stage）；未登记工具
  // 回退成含「调用」的中文行，绝不把原始英文工具名当主标题。
  function toolTraceLabel(name, params) {
    const tool = String(name || "");
    const input_ = params || {};
    if (tool === "tech_ui") {
      const action = String(input_.action || "");
      const label = TOOL_TRACE_UI_ACTIONS[action] || `看板操作（${action || "未指定动作"}）`;
      const stage = input_.stage ? ` · ${input_.stage}` : "";
      return { title: `${label}${stage}`, subtitle: input_.note || input_.label || input_.view || "" };
    }
    const title = TOOL_TRACE_LABELS[tool] || `调用工具 ${tool || "未知"}`;
    return { title: title, subtitle: toolSubtitle(tool, input_) };
  }

  function clearEmpty() { $("ocEmpty")?.remove(); }
  function addUser(text) { clearEmpty(); tinner.append(el("div", "oc-ubub", text)); scrollDown(); }
  // 身份行：技术侧不再有头像，助手 / 系统 / 检索结果卡统一靠这行蓝字表明身份（与报价同款）。
  function identityLabel(text) {
    const label = el("div", "oc-alabel");
    label.append(el("span", null, text || "技术工艺智能体"));
    return label;
  }
  function pushSystem(text) {
    clearEmpty();
    const wrap = el("div", "oc-amsg");
    const body = el("div", "oc-abody");
    // 身份行与技术回复同款，只是不带状态 chip：系统提示也是同一条会话流里的普通输出。
    const label = el("div", "oc-alabel");
    label.append(el("span", null, "技术工艺智能体"));
    body.append(label, el("div", "oc-atxt", text));
    wrap.append(body);
    tinner.append(wrap);
    scrollDown();
    persistSessionEvent({ kind: "session-note", source: "shell",
                          text: String(text || ""), key: `shell:${String(text || "")}` });
  }
  // 预期内失败码（口径与 tech-board-bridge.js 的 QUIET_FAILURE_CODES 一致）：切看板导致
  // 在途命令被取消、必填意见没填、当前视图没有目标输入框 —— 看板自己已经就地提示过，
  // 再往会话里塞一条 ⚠ 就是纯噪音。其余失败照旧可见，绝不在这里吞掉。
  const QUIET_BOARD_CODES = ["detached", "note-target-missing", "missing-comment", "no-selection"];
  // 「中断」不是「失败」：任务没有正常收尾 —— 服务重启把在途任务打断（interrupted）、
  // 切看板导致在途命令被取消（detached）、看板 20s 没回执（timeout）。这三种都标「中断」，
  // 沿用进行中的蓝色 chip，不刷红字、也不谎报成功。判定只此一处。
  const INTERRUPTED_CODES = ["interrupted", "detached", "timeout"];
  function isInterruptedCode(code) {
    return INTERRUPTED_CODES.indexOf(String(code || "")) >= 0;
  }
  function isQuietBoardCode(code) {
    const value = String(code || "");
    const bridge = window.TechBoardBridge;
    if (bridge && typeof bridge.isQuietFailure === "function" && bridge.isQuietFailure({ code: value })) {
      return true;
    }
    return QUIET_BOARD_CODES.indexOf(value) >= 0;
  }
  // 看板动作 / 导航 / 刷新失败的唯一出口：预期内失败直接忽略，其余把真实原因按普通输出
  // 写进会话（不是空 catch）。前缀沿用各调用点原有的「…失败：」措辞。
  function boardFailureNotice(prefix, error) {
    if ((error && error.quiet === true) || isQuietBoardCode(error && error.code)) return;
    const reason = (error && error.message) || "看板未响应";
    pushSystem(`${prefix}${reason}。`);
  }

  function addAssistant() {
    clearEmpty();
    const wrap = el("div", "oc-amsg");
    const body = el("div", "oc-abody");
    // 标题行：左侧蓝色身份行（与报价「报价单智能体」同款），右侧运行状态 chip。
    // 一轮回复只有这一张 chip，状态就地翻转，不新增第二行 / 第二张卡。
    const label = el("div", "oc-alabel");
    label.append(el("span", null, "技术工艺智能体"));
    const state = el("span", "oc-alabel-state is-running", "◌ 运行中");
    label.append(state);
    const text = el("div", "oc-atxt");
    body.append(label, text);
    wrap.append(body);
    tinner.append(wrap);
    scrollDown();
    return { body, text, cards: {}, full: "", label, state, thinking: null };
  }
  // 就地翻转同一张 chip：文本与配色都按状态切换，绝不另建节点。
  function setAssistantState(ctx, state) {
    const chip = ctx && ctx.state;
    if (!chip) return;
    const word = state === "succeeded" ? "✓ 已完成" : state === "failed" ? "⚠ 失败" : "◌ 运行中";
    chip.classList.remove("is-running", "is-succeeded", "is-failed");
    chip.classList.add(`is-${state || "running"}`);
    chip.textContent = word;
  }
  // 思考过程折叠块（默认关闭）——只有供应商真的推了 thinking 帧才出现，不建空块。
  function appendThinking(ctx, text) {
    const value = String(text || "");
    if (!value) return;
    if (!ctx.thinking) {
      const block = el("details", "oc-thinking");
      block.append(el("summary", null, "思考过程"));
      const inner = el("div", "oc-thinking-body");
      block.append(inner);
      ctx.thinking = block;
      ctx.thinkingBody = inner;
      // 插入顺序固定：身份行 → 思考过程 → 正文文本。
      ctx.body.insertBefore(block, ctx.text);
    }
    ctx.thinkingBody.textContent += value;
    scrollDown();
  }
  function addToolCard(ctx, event) {
    const card = el("div", "oc-art");
    const tile = el("div", "oc-atile", toolIcon(event.name));
    const label = toolTraceLabel(event.name, event.input);
    const mid = el("div");
    mid.style.cssText = "flex:1;min-width:0;";
    mid.append(el("div", "oc-art-name", label.title));
    if (label.subtitle) mid.append(el("div", "oc-art-sub", label.subtitle));
    const state = el("div", "oc-art-state", "");
    state.innerHTML = '<span class="oc-spin">◌</span>';
    // 原始工具名 / 入参 JSON / 工具结果收进默认折叠的原生 details：业务用户只看
    // 主行的中文业务文案，排障时展开仍能看到完整载荷与返回值。
    let rawInput = "";
    try { rawInput = JSON.stringify(event.input == null ? {} : event.input).slice(0, 300); }
    catch { rawInput = ""; }
    const details = el("details", "oc-art-detail");
    details.append(el("summary", null, "详情"));
    details.append(el("div", "oc-art-raw", event.name));
    details.append(el("pre", "oc-art-input", rawInput));
    const result = el("pre", "oc-tool-result");
    result.style.display = "none";
    details.append(result);
    card.append(tile, mid, state, details);
    ctx.body.append(card);
    ctx.cards[event.id] = { state, result };
    scrollDown();
  }
  // 工具结果仍写回详情里那一个 <pre class="oc-tool-result">：4000 字截断、失败红字
  // 与 .err、成功 / 失败状态位（✓ / ⚠ 与 #16a34a / #dc2626）全部保持不变。
  function setToolResult(ctx, event) {
    const card = ctx.cards[event.tool_use_id];
    if (!card) return;
    card.state.textContent = event.is_error ? "⚠" : "✓";
    card.state.style.color = event.is_error ? "#dc2626" : "#16a34a";
    const text = String(event.content || "").trim();
    if (text) {
      card.result.textContent = text.length > 4000 ? `${text.slice(0, 4000)}\n… (已截断)` : text;
      card.result.style.display = "block";
      if (event.is_error) card.result.classList.add("err");
    }
    scrollDown();
  }

  // UpdatePartParameters 的工具结果。只认 applied=true 的那些 —— 工具被拒绝
  // （零件不存在、字段不在白名单、值没变）时什么都没写，不该触发刷新。
  function parseEditResult(event) {
    if (event.is_error) return null;
    try {
      const data = JSON.parse(String(event.content || ""));
      if (!data || data.applied !== true || !data.part_id) return null;
      return { part_id: data.part_id, requires_regeneration: !!data.requires_regeneration };
    } catch { return null; }
  }

  // 本轮结束后统一刷新工作台。复用 app.js 已有的 refreshAfterChatEdit（它监听
  // cad-engine:workbench-chat-edit）—— 那条路径已经处理了重生几何、重拉 IR、
  // 刷新版本列表、重新选中零件。这里再写一份只会两边行为漂移。
  function flushPartEdits() {
    const edits = [...pendingEdits.values()].filter(Boolean);
    pendingEdits.clear();
    if (!edits.length) return;
    noteInThread(
      `Agent 已修改 ${edits.map(item => item.part_id).join("、")} 的参数，正在刷新工作台…`);
    // 统一父壳里看板是 iframe：同窗口事件跨不过去，必须经桥请右侧看板自己刷新
    // （看板收到 edits 后复用既有 refreshAfterChatEdit：拉 IR / 重生几何 / 刷版本）。
    if (inUnifiedWorkbench) {
      const bridge = boardBridge();
      const call = (bridge && typeof bridge.executeAction === "function")
        ? bridge.executeAction("refreshData", { action: "refreshData", edits })
        : pushSystem("零件参数已由 Agent 修改，但右侧看板尚未就绪，暂时无法刷新显示。");
      Promise.resolve(call).catch(error => boardFailureNotice("刷新右侧看板失败：", error));
      return;
    }
    for (const detail of edits) {
      window.dispatchEvent(new CustomEvent("cad-engine:workbench-chat-edit", { detail }));
    }
  }

  function handleEvent(ctx, event) {
    // 统一结构化 UI 事件（第 17 步）：Agent 只表达意图，前端按固定映射渲染。
    if (event.type === "tech_ui") {
      runTechUi(event.tech_ui || {});
      return;
    }
    if (event.type === "thinking") {
      appendThinking(ctx, event.text);
      return;
    }
    if (event.type === "text") {
      ctx.full += event.text;
      ctx.text.textContent = ctx.full;
      scrollDown();
      return;
    }
    if (event.type === "tool_use") {
      // tech_ui 走独立 tech_ui 帧渲染，不再画一张通用工具卡（避免两份说法）。
      if (event.name === "tech_ui") return;
      addToolCard(ctx, event);
      // Agent 决定开始解析：交给平台既有流水线执行，不在这里另起一套解析逻辑。
      if (event.ui_action === "parse") requestParse("Agent");
      // 改零件参数：这个事件在工具**跑之前**发出，当场刷新只会读到旧值，
      // 所以先占个位，等对应的 tool_result 回来再填详情。
      if (event.ui_action === "refresh-ir") pendingEdits.set(event.id, null);
      // 1.1 创建需求：Agent 请求一键解析 / 刷新需求看板时，只发语义化业务动作名，
      // 由右侧看板复用既有 extract-documents 流水线执行（左侧绝不直接调用该接口）。
      if (event.ui_action === "extract-requirement") requestRequirementExtract("Agent");
      if (event.ui_action === "refresh-requirement") refreshRequirementBoard();
      // 1.2 / 1.3：Agent 起草的确认 / 审核意见经桥带回看板（不落盘、不改状态）。
      if (event.ui_action === "fill-confirmation-note") applyConfirmationNoteAction(event.input);
      if (event.ui_action === "fill-review-note") applyReviewNoteAction(event.input);
      // 2.2 组装与整合：参数 / 工序改动后刷新看板；请求跑环节交给看板跑既有流水线；
      // 上传图纸只能由用户完成，这里只把看板切到「整合图纸」并聚焦上传入口。
      if (event.ui_action === "refresh-integration") refreshIntegrationBoard(event.input);
      if (event.ui_action === "integration-step") integrationBoardStep(event.input);
      if (event.ui_action === "open-integration-drawings") openIntegrationDrawings();
      // 2.3 成本测算：测算进度交给右侧看板跑既有成本流水线；
      // 说明 / 确认 / 写物料 / 发报价 / 退回工艺后刷新看板。
      if (event.ui_action === "cost-step") costReviewBoardStep(event.input);
      if (event.ui_action === "refresh-cost-review") refreshCostReviewBoard();
      // 3.1 / 3.2 / 3.3 报告：写入动作完成后刷新右侧报告看板；审核意见经桥带入看板。
      if (event.ui_action === "refresh-report") refreshProcessReportBoard();
      if (event.ui_action === "fill-report-review-note") applyReportReviewNoteAction(event.input);
      return;
    }
    if (event.type === "tool_result") {
      setToolResult(ctx, event);
      if (pendingEdits.has(event.tool_use_id)) {
        pendingEdits.set(event.tool_use_id, parseEditResult(event));
      }
      // 1.1 需求提取工具回执里的 document_extraction：抽取成需求解析摘要。
      const requirementSummary = requirementSummaryFromToolResult(event);
      if (requirementSummary) renderRequirementSummary(requirementSummary);
      const flowSummary = requirementFlowSummaryFromToolResult(event);
      if (flowSummary) renderRequirementFlowSummary(flowSummary);
      return;
    }
    if (event.type === "error") {
      // 失败不再是另一张红字卡片：原因并进同一条回复的正文（状态位仍置失败），
      // 和普通输出同一套排版，也不会额外占住会话底部。
      ctx.failed = true;
      setAssistantState(ctx, "failed");
      const reason = String(event.error || "未知错误");
      ctx.full = (ctx.full ? `${ctx.full}\n` : "") + `⚠ ${reason}`;
      ctx.text.classList.add("rendered");
      ctx.text.innerHTML = renderMarkdown(ctx.full);
      scrollDown();
      return;
    }
    if (event.type === "done") {
      if (ctx.full) {
        ctx.text.classList.add("rendered");
        ctx.text.innerHTML = renderMarkdown(ctx.full);
      }
      if (!ctx.failed) setAssistantState(ctx, "succeeded");
      if (event.model) setModelLabel(event.model);
    }
  }

  // ---------------------------------------------------------------- 发送
  async function send() {
    const text = input.value.trim();
    if (!text || busy) return;
    input.value = "";
    autoSize();
    busy = true;
    sendBtn.disabled = true;
    addUser(text);
    const ctx = addAssistant();
    try {
      const response = await fetch(api("/send"), {
        method: "POST", headers: authHeaders(true),
        body: JSON.stringify({ message: text, page_context: currentPageContext() }),
      });
      if (!response.ok || !response.body) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let index;
        while ((index = buffer.indexOf("\n\n")) >= 0) {
          const chunk = buffer.slice(0, index);
          buffer = buffer.slice(index + 2);
          const line = chunk.split("\n").find(item => item.startsWith("data:"));
          if (!line) continue;
          try { handleEvent(ctx, JSON.parse(line.slice(5).trim())); } catch { /* 跳过坏帧 */ }
        }
      }
    } catch (error) {
      // 连接失败同样并进正文：保留失败状态位，但不再画一张红色的独立卡片。
      ctx.failed = true;
      setAssistantState(ctx, "failed");
      const reason = (error && error.message) || "连接错误";
      ctx.full = (ctx.full ? `${ctx.full}\n` : "") + `⚠ ${reason}`;
      ctx.text.classList.add("rendered");
      ctx.text.innerHTML = renderMarkdown(ctx.full);
    } finally {
      busy = false;
      sendBtn.disabled = false;
      input.focus();
      flushPartEdits();
      // 一轮对话结束（含出错结束）后把结果按钮重新置底，让它始终跟在最新回复下面。
      refreshResultChips();
      scrollDown();
    }
  }

  function autoSize() {
    input.style.height = "auto";
    const contentHeight = input.scrollHeight;
    input.style.height = `${Math.min(contentHeight, 120)}px`;
    input.style.overflowY = contentHeight > 120 ? "auto" : "hidden";
  }

  // ---------------------------------------------------------------- 解析联动
  // 单一解析入口：始终点 app.js 绑定的 #btnParse，避免出现第二套解析实现。
  function requestParse(origin) {
    // 统一父壳里没有本页 #btnParse：解析动作属于右侧 2.1 看板，只发业务动作名。
    if (inUnifiedWorkbench) {
      if (origin === "Agent") noteInThread("Agent 已请求开始解析，平台流水线正在执行。");
      const bridge = boardBridge();
      if (!bridge) { pushSystem("当前还不能解析：右侧看板尚未就绪，请稍后重试。"); return; }
      bridge.executeAction("parseDrawing", { label: "开始解析" }).catch(error => {
        boardFailureNotice("开始解析失败：", error);
      });
      return;
    }
    const button = $("btnParse");
    if (!button) return;
    if (button.disabled) {
      pushSystem("当前还不能解析：请先在 ＋ →「补充需求图纸」里上传需求原图并创建评估任务。");
      return;
    }
    if (origin === "Agent") noteInThread("Agent 已请求开始解析，平台流水线正在执行。");
    // 不制造第二次点击事件：直接调用 app.js 绑定在按钮上的同一份实现。
    button.onclick?.();
  }
  // ---------------------------------------------- 1.1 创建需求：Agent 动作联动
  // Agent 决定一键解析技术资料 / 刷新需求看板时，与 requestParse 完全同模式：只把
  // 语义化业务动作名交给右侧 1.1 看板（TechBoardBridge.executeAction）。字段提取仍
  // 由看板复用既有 extract-documents 流水线执行，左侧不直接调用该接口，也不另写
  // 第二份提取逻辑。
  function requestRequirementExtract(origin) {
    if (origin === "Agent") noteInThread("Agent 已请求 AI 解析技术资料，正在带入 1.1 需求字段。");
    const bridge = boardBridge();
    if (!bridge || typeof bridge.executeAction !== "function") {
      pushSystem("当前还不能解析需求：右侧看板尚未就绪，请稍后重试。");
      return;
    }
    const call = bridge.executeAction("extractRequirement", { label: "一键解析需求" });
    Promise.resolve(call).catch(error => {
      boardFailureNotice("需求解析失败：", error);
    });
  }

  function refreshRequirementBoard() {
    const bridge = boardBridge();
    if (!bridge || typeof bridge.executeAction !== "function") {
      pushSystem("当前还不能刷新需求看板：右侧看板尚未就绪，请稍后重试。");
      return;
    }
    const call = bridge.executeAction("refreshData", { action: "refreshData", label: "刷新需求看板" });
    Promise.resolve(call).catch(error => {
      boardFailureNotice("刷新需求看板失败：", error);
    });
  }

  // ---------------------------------------------- 2.2 组装与整合：Agent 动作联动
  // 与 requestParse / 需求动作完全同模式：左侧只把语义化业务动作名交给右侧 2.2 看板
  // （TechBoardBridge.executeAction），生成与流转仍由看板复用既有实现执行；
  // 左侧不直接调用 2.2 的任何接口，也不另写第二份生成逻辑。
  function integrationBoardAction(start) {
    const bridge = boardBridge();
    if (!bridge || typeof bridge.executeAction !== "function") {
      pushSystem("当前还不能操作 2.2 整合看板：右侧看板尚未就绪，请稍后重试。");
      return;
    }
    Promise.resolve(start(bridge)).catch(error => {
      boardFailureNotice("整合看板操作失败：", error);
    });
  }

  function refreshIntegrationBoard() {
    noteInThread("Agent 已更新整合参数 / 工序，正在刷新右侧 2.2 看板。");
    integrationBoardAction(bridge => bridge.executeAction("refreshIntegration", { label: "刷新整合看板" }));
  }

  function integrationBoardStep(input) {
    const step = String((input && input.step) || "").trim().toLowerCase();
    const labels = { params: "参数推荐", process: "组装工艺", cost: "成本测算" };
    if (!labels[step]) {
      pushSystem("Agent 请求生成整合环节，但没有给出有效的 step（params / process / cost）。");
      return;
    }
    noteInThread(`Agent 已请求生成「${labels[step]}」，右侧看板正在跑平台既有流水线。`);
    integrationBoardAction(bridge => bridge.executeAction("integrationStep", { step: step, label: labels[step] }));
  }

  function openIntegrationDrawings() {
    noteInThread("Agent 请求上传整合图纸 —— 请在右侧看板「整合图纸」页签选择文件上传。");
    integrationBoardAction(bridge => bridge.executeAction("openIntegrationDrawings", { label: "整合图纸" }));
  }

  // ---------------------------------------------- 2.3 成本测算：Agent 动作联动
  // 与 2.2 完全同模式：左侧只发语义化业务动作名，测算与流转仍由右侧 2.3 看板
  // 复用既有成本实现执行；左侧不直接调用任何成本接口。
  function costReviewBoardStep(input) {
    const step = String((input && input.step) || "").trim().toLowerCase();
    const labels = { part: "单件成本测算", assembly: "组装成本测算", all: "全部成本测算" };
    if (!labels[step]) {
      pushSystem("Agent 请求成本测算，但没有给出有效的 step（part / assembly / all）。");
      return;
    }
    noteInThread(`Agent 已请求「${labels[step]}」，右侧看板正在跑平台既有成本流水线。`);
    const bridge = boardBridge();
    if (!bridge || typeof bridge.executeAction !== "function") {
      pushSystem("当前还不能操作 2.3 成本看板：右侧看板尚未就绪，请稍后重试。");
      return;
    }
    Promise.resolve(bridge.executeAction("costStep", {
      step: step,
      part_id: (input && (input.part_id || input.partId)) || "",
      quantity: (input && input.quantity) || 0,
      label: labels[step],
    })).catch(error => {
      boardFailureNotice("成本看板操作失败：", error);
    });
  }

  function refreshCostReviewBoard() {
    noteInThread("Agent 已更新成本说明 / 确认 / 流转，正在刷新右侧 2.3 看板。");
    const bridge = boardBridge();
    if (!bridge || typeof bridge.executeAction !== "function") {
      pushSystem("当前还不能操作 2.3 成本看板：右侧看板尚未就绪，请稍后重试。");
      return;
    }
    Promise.resolve(bridge.executeAction("refreshCostReview", { label: "刷新成本看板" })).catch(error => {
      boardFailureNotice("成本看板操作失败：", error);
    });
  }

  // ---------------------------------------------- 3.1 / 3.2 / 3.3 报告：Agent 动作联动
  // 与 2.2 / 2.3 完全同模式：左侧只发语义化业务动作名，报告读写、审核与发布仍在右侧
  // 看板复用既有流程执行；左侧不直接调用任何报告或汇总接口。
  function refreshProcessReportBoard() {
    noteInThread("Agent 已更新报告内容，正在刷新右侧报告看板。");
    const bridge = boardBridge();
    if (!bridge || typeof bridge.executeAction !== "function") {
      pushSystem("当前还不能操作报告看板：右侧看板尚未就绪，请稍后重试。");
      return;
    }
    Promise.resolve(bridge.executeAction("refreshProcessReport", { label: "刷新报告看板" })).catch(error => {
      boardFailureNotice("报告看板操作失败：", error);
    });
  }

  function applyReportReviewNoteAction(input) {
    const decision = String((input && input.decision) || "").trim().toLowerCase();
    const note = String((input && input.note) || "");
    if (!note) {
      pushSystem("Agent 请求带入审核意见，但没有给出意见正文。");
      return;
    }
    noteInThread("Agent 已起草审核意见，正在带入右侧审核看板；是否通过 / 退回仍需你人工确认。");
    const bridge = boardBridge();
    if (!bridge || typeof bridge.executeAction !== "function") {
      pushSystem("当前还不能操作报告看板：右侧看板尚未就绪，请稍后重试。");
      return;
    }
    Promise.resolve(bridge.executeAction("applyReportReviewNote", {
      decision: decision,
      note: note,
      label: "带入审核意见",
    })).catch(error => {
      boardFailureNotice("报告看板操作失败：", error);
    });
  }

  // 工具回执里的需求解析结果：document_extraction（或顶层同名字段）携带
  // filled_fields / recommended_fields / recommendation_confidence / recommendations，
  // 左侧据此渲染「需求解析摘要」。数据只来自 Agent 工具结果，父壳不另拉一份。
  function requirementSummaryFromToolResult(event) {
    if (event.is_error) return null;
    const text = String(event.content || "").trim();
    if (!text || text.charAt(0) !== "{") return null;
    let data = null;
    try { data = JSON.parse(text); } catch { return null; }
    if (!data || typeof data !== "object") return null;
    const extraction = (data.document_extraction && typeof data.document_extraction === "object")
      ? data.document_extraction : data;
    const filled = Array.isArray(extraction.filled_fields) ? extraction.filled_fields : [];
    const confidence = (extraction.recommendation_confidence && typeof extraction.recommendation_confidence === "object")
      ? extraction.recommendation_confidence : (data.recommendation_confidence || {});
    const recommendations = (extraction.recommendations && typeof extraction.recommendations === "object")
      ? extraction.recommendations : (data.recommendations || {});
    let recommended = Array.isArray(extraction.recommended_fields) ? extraction.recommended_fields
      : (Array.isArray(data.recommended_fields) ? data.recommended_fields : []);
    if (!recommended.length) recommended = Object.keys(recommendations);
    const missing = Array.isArray(extraction.missing_required_fields) ? extraction.missing_required_fields
      : (Array.isArray(data.missing_required_fields) ? data.missing_required_fields : []);
    if (!filled.length && !recommended.length) return null;
    return { filled, recommended, confidence, recommendations, missing };
  }

  let lastRequirementSummaryKey = "";
  function renderRequirementSummary(summary) {
    if (!summary) return;
    const signature = JSON.stringify([summary.filled, summary.recommended, summary.missing]);
    if (signature === lastRequirementSummaryKey) return;
    lastRequirementSummaryKey = signature;
    clearEmpty();
    const wrap = el("div", "oc-amsg");
    const body = el("div", "oc-abody");
    const card = el("div", "oc-req-summary");
    card.append(el("h4", null, "需求解析摘要"));
    if (summary.filled.length) {
      card.append(el("div", "oc-req-line",
        `AI 已带入 ${summary.filled.length} 个字段：${summary.filled.join("、")}`));
    }
    if (summary.recommended.length) {
      const detail = summary.recommended.map(key => {
        const value = summary.recommendations[key];
        const raw = summary.confidence[key];
        const score = typeof raw === "number" ? `（置信度 ${Math.round(raw * 100)}%）` : "";
        return value ? `${key}=${value}${score}` : `${key}${score}`;
      });
      card.append(el("div", "oc-req-line",
        `AI 推荐 ${summary.recommended.length} 个默认值：${detail.join("；")}`));
    }
    if (summary.missing.length) {
      card.append(el("div", "oc-req-line oc-req-missing",
        `仍缺必填项 ${summary.missing.length} 个：${summary.missing.join("、")}`));
    }
    body.append(card);
    wrap.append(body);
    tinner.append(wrap);
    scrollDown();
  }

  // ---------------------------------------------- 1.2 / 1.3：确认与审核意见带入看板
  // Agent 起草的确认意见 / 审核意见经桥交给右侧看板写入表单（复用各页既有写法）：
  // 左侧不直接调接口、不落盘、不改任何状态；通过 / 退回仍由看板的确认门执行。
  function applyConfirmationNoteAction(input) {
    const note = String((input && input.note) || "").trim();
    const bridge = boardBridge();
    if (!bridge || typeof bridge.executeAction !== "function") {
      pushSystem("确认意见暂未带入：右侧看板尚未就绪，请稍后重试。");
      return;
    }
    Promise.resolve(bridge.executeAction("applyConfirmationNote", { note })).catch(error => {
      boardFailureNotice("带入确认意见失败：", error);
    });
  }

  function applyReviewNoteAction(input) {
    const decision = String((input && input.decision) || "").trim();
    const note = String((input && input.note) || "").trim();
    const bridge = boardBridge();
    if (!bridge || typeof bridge.executeAction !== "function") {
      pushSystem("审核意见暂未带入：右侧看板尚未就绪，请稍后重试。");
      return;
    }
    Promise.resolve(bridge.executeAction("applyReviewNote", { decision, note })).catch(error => {
      boardFailureNotice("带入审核意见失败：", error);
    });
  }

  // 工具回执都是后端 json.dumps 出来的对象；解析失败一律返回 null，不猜内容。
  function toolResultJson(event) {
    if (event.is_error) return null;
    const text = String(event.content || "").trim();
    if (!text || text.charAt(0) !== "{") return null;
    try {
      const data = JSON.parse(text);
      return (data && typeof data === "object") ? data : null;
    } catch { return null; }
  }

  // 1.2 / 1.3 的工具回执 → 左侧摘要：确认门回执、确定性预检 / 待澄清、审核材料 / 摘要。
  function requirementFlowSummaryFromToolResult(event) {
    const data = toolResultJson(event);
    if (!data) return null;
    if (data.requires_confirmation === true) {
      return { kind: "confirm-gate", action: String(data.action || ""), note: String(data.note || "") };
    }
    if (data.review_summary || data.review_materials) {
      return { kind: "review", materials: data.review_materials || null, summary: data.review_summary || null };
    }
    const generated = typeof data.generated_note === "string" ? data.generated_note : "";
    const needs = Array.isArray(data.need_info) ? data.need_info
      : (Array.isArray(data.questions) ? data.questions : null);
    if (generated || needs) {
      return { kind: "confirmation", generated_note: generated, need_info: needs || [],
               status: String(data.status || "") };
    }
    return null;
  }

  const CONFIRM_GATE_LABELS = { confirm: "通过确认", return: "退回草稿", approve: "审核通过", reject: "审核退回" };
  let lastRequirementFlowKey = "";

  function requirementFlowSignature(summary) {
    if (summary.kind === "confirm-gate") return `gate:${summary.action}`;
    if (summary.kind === "review") return `review:${JSON.stringify(summary.summary || {})}`;
    return `confirm:${summary.status}:${summary.generated_note}:${(summary.need_info || []).length}`;
  }

  function renderRequirementFlowSummary(summary) {
    if (!summary) return;
    const signature = requirementFlowSignature(summary);
    if (signature === lastRequirementFlowKey) return;
    lastRequirementFlowKey = signature;
    clearEmpty();
    const wrap = el("div", "oc-amsg");
    const body = el("div", "oc-abody");
    const card = el("div", "oc-req-summary");
    if (summary.kind === "confirm-gate") {
      const label = CONFIRM_GATE_LABELS[summary.action] || "该操作";
      card.append(el("h4", null, "需要人工确认"));
      card.append(el("div", "oc-req-line oc-req-missing",
        `「${label}」需人工明确确认后才能执行，Agent 不会代替审批。`));
      if (summary.note) card.append(el("div", "oc-req-line", summary.note));
    } else if (summary.kind === "review") {
      card.append(el("h4", null, "审核摘要"));
      const inner = summary.summary || {};
      if (inner.summary) card.append(el("div", "oc-req-line", inner.summary));
      if (inner.generated_note) card.append(el("div", "oc-req-line", inner.generated_note));
      const needs = Array.isArray(inner.need_info) ? inner.need_info : [];
      if (needs.length) {
        card.append(el("div", "oc-req-line oc-req-missing",
          `仍有 ${needs.length} 个待补充项：${needs.map(row => (row && row.item) || "").filter(Boolean).join("、")}`));
      }
      const materials = summary.materials || {};
      const files = Array.isArray(materials.attachments) ? materials.attachments : [];
      if (materials.source_filename || files.length) {
        card.append(el("div", "oc-req-line",
          `审核材料：原始图纸 ${materials.source_filename || "—"}；附件 ${files.length} 份。`));
      }
    } else {
      card.append(el("h4", null, "需求确认摘要"));
      if (summary.generated_note) card.append(el("div", "oc-req-line", summary.generated_note));
      const needs = summary.need_info || [];
      const items = needs.map(row => (row && row.item) || "").filter(Boolean);
      card.append(el("div", needs.length ? "oc-req-line oc-req-missing" : "oc-req-line",
        needs.length ? `待补充 ${needs.length} 项：${items.join("、")}` : "确定性检查未发现待补充项。"));
      if (summary.status) card.append(el("div", "oc-req-line", `当前状态：${summary.status}`));
    }
    body.append(card);
    wrap.append(body);
    tinner.append(wrap);
    scrollDown();
  }

  // extras：同一条消息的附加控件（例如失败后的「重试」按钮）。它们必须和正文共享
  // 同一个气泡容器，按时间顺序留在会话流里；不要再另起一行 append 到 tinner 末尾 ——
  // 那种独立节点会永远钉在底部，后面聊多少轮都顶不走。
  function noteInThread(text, extras) {
    clearEmpty();
    const wrap = el("div", "oc-amsg");
    const body = el("div", "oc-abody");
    body.append(el("div", "oc-atxt", text));
    if (typeof extras === "function") extras(body);
    wrap.append(body);
    tinner.append(wrap);
    scrollDown();
    persistSessionEvent({ kind: "session-note", source: "shell",
                          text: String(text || ""), key: `shell:${String(text || "")}` });
  }

  // 解析结果以按钮形式常驻对话**底部**。
  // 原本它挂在设计意图卡（对话第一条）里，聊上几轮就被顶到上面，要往回翻才找得到；
  // 现在每次刷新都把它重新 append 到 tinner 末尾 —— append 已存在的节点是"移动"，
  // 所以不会产生第二份，chips 永远停在最新一条消息下面。
  const resultBox = document.querySelector(".oc-result-actions");
  // 解析报告是 index.html 里的静态节点，不是这里造的：跳转和「还没有项目」的兜底
  // 都写在 app.js 的 $("btnReport").onclick 里，前端再造一个就会有两套说法。
  //
  // 它绝不能被摘出 DOM，哪怕只是一瞬间：本脚本是普通 script，app.js 是 module（deferred），
  // 本脚本**先**执行。启动时还没有解析结果，若此时把它 remove 掉，app.js 顶层那句
  // $("btnReport").onclick 拿到的就是 null —— TypeError 让整个模块中止，init() 不再执行，
  // 页面所有功能（「开始解析」、图纸、项目加载）全停在 HTML 初始态。
  // 所以下面清空时按节点逐个删，不能用 replaceChildren()。
  const reportButton = $("btnReport");
  // renderIR 只在有 IR 时才发 agent:ir-rendered，所以这个标志就等于"解析过了"。
  // 不能只看零件数和问题数：一份零件为空、也没有待澄清的 IR 照样有报告可看，
  // 那种情况下按钮不出现，用户就没有入口了。
  let hasParsedIR = false;
  function refreshResultChips() {
    // 统一父壳：数量与可用状态只来自看板桥播报的解析摘要，不读 iframe DOM、
    // 也不另拉一份零件 / 问题数据；结果入口已迁到左侧 #techChatActions。
    if (inUnifiedWorkbench) { applyDrawingResultSummary(); return; }
    if (!resultBox) return;
    const tree = document.getElementById("tree");
    const extras = document.getElementById("extras");
    const parts = tree ? tree.querySelectorAll(".part").length : 0;
    const questions = extras ? extras.querySelectorAll(".extra-item, .standard-item").length : 0;
    const hasTree = parts > 0;
    const extrasText = (extras?.textContent || "").trim();
    const hasQuestions = questions > 0 || (extrasText && extrasText !== "暂无待澄清问题");
    // 只清掉上一轮生成的 chip，保留 #btnReport（原因见上）。
    for (const node of [...resultBox.children]) {
      if (node !== reportButton) node.remove();
    }
    if (!hasTree && !hasQuestions && !hasParsedIR) {
      // 整组隐藏，但节点仍留在 DOM 里 —— app.js 还要按 id 找它。
      resultBox.hidden = true;
      return;
    }
    if (hasQuestions) resultBox.append(agentResultChip("待澄清问题", questions || "", "ocQuestionsAction", true));
    // 排最后：前两个是"看某一部分结果"，解析报告是"看整份"，读下来是收束关系。
    if (reportButton) resultBox.append(reportButton);
    resultBox.hidden = false;
    tinner.append(resultBox);        // 置底
  }
  // 独立 2.1 页的结果按钮：仍复用同一张「控件 id ↔ 看板视图名」映射表，点击只发
  // 看板视图导航，不再打开父壳抽屉。
  function agentResultChip(label, count, entryKey, warn) {
    const button = el("button", `oc-chip${warn ? " warn" : ""}`);
    button.type = "button";
    button.append(document.createTextNode(label));
    if (count !== "" && count != null) button.append(el("span", "oc-chip-count", String(count)));
    button.onclick = () => boardNavigateView(TECH_BOARD_VIEW_ENTRIES[entryKey], { label });
    return button;
  }

  window.addEventListener("agent:ir-rendered", () => {
    hasParsedIR = true;
    refreshResultChips();
  });
  window.addEventListener("agent:parse-done", event => {
    const detail = event.detail || {};
    noteInThread(detail.summary || "解析完成。零件清单与待澄清问题见下方按钮。");
    // 汇总卡是异步取的，等它插完再置底，DOM 顺序才和视觉顺序一致（读屏与 Tab 序要用）。
    Promise.resolve(renderComponentMatch()).finally(refreshResultChips);
    loadFiles();
  });

  // ------------------------------------------------------ 任务文件（右侧悬浮小窗）
  // 输入的图纸文档，以及流程中产出的几何、2D 图、导出表格，全部走后端
  // /files 一个清单接口 —— 前端不再各自去猜哪一步生成过什么。
  const filesDock = $("ocFilesDock");
  const filesCount = $("ocFilesCount");
  const filesBody = $("ocFilesBody");

  const KIND_ICON = { image: "🖼", doc: "📄", model: "🧊", table: "📊" };

  function fileRow(file) {
    const row = el("div", "oc-file");
    row.append(el("span", "oc-file-icon", KIND_ICON[file.kind] || "📄"));
    const body = el("div", "oc-file-body");
    const link = el("a", "oc-file-name", file.name);
    link.href = file.url;
    link.target = "_blank";
    link.rel = "noopener";
    body.append(link);
    if (file.note) body.append(el("div", "oc-file-note", file.note));
    row.append(body);
    return row;
  }

  async function loadFiles() {
    // 统一父壳不读 /files：文件数量由看板桥播报，正文属于右侧看板。
    if (inUnifiedWorkbench) return;
    if (!filesDock || !projectId) return;
    let manifest = { groups: [], total: 0, note: "" };
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/files`,
                                  { headers: authHeaders() });
      if (response.ok) manifest = await response.json().catch(() => manifest);
    } catch { /* 清单读不到不影响对话本身 */ }

    filesCount.textContent = manifest.total ? String(manifest.total) : "—";
    filesBody.replaceChildren();
    (manifest.groups || []).forEach(group => {
      const section = el("section", "oc-file-group");
      const head = el("div", "oc-file-group-head");
      head.append(el("span", null, group.title));
      head.append(el("span", "oc-file-group-count", String((group.files || []).length)));
      section.append(head);
      (group.files || []).forEach(file => section.append(fileRow(file)));
      filesBody.append(section);
    });
    if (manifest.note) {
      const section = el("section", "oc-file-group");
      section.append(el("div", "oc-file-group-head", "补充技术说明"));
      section.append(el("div", "oc-file-note", manifest.note));
      filesBody.append(section);
    }
    if (!manifest.total && !manifest.note) {
      filesBody.append(el("div", "oc-file-empty",
        "还没有任何文件。用输入框左侧 ＋ →「补充需求图纸」上传需求原图与技术文档。"));
    }
  }

  // ------------------------------------------------------ Agent 处理过程
  // 解析、检索都跑在后台任务里，进度由 agent:task-progress 播过来；
  // 这里渲染成对话中的一条时间线，让每一步在干什么可见。
  // 统一的进度卡：按 taskId 去重，queued / running / succeeded / failed 四态；
  // progress_log 只从游标增量追加，已经显示的中间步骤不会被覆盖。
  // 任务卡和消息共用同一个插入点 #ocTinner：#ocTaskProgressHost 只保留节点（多个
  // 批次的防缩水守卫与 setProject 的清理逻辑引用它），卡片不再钉在会话底部。
  const taskProgressCards = new Map();

  function taskProgressHost() {
    return tinner;
  }
  // 进度只展示业务语义：Key / Authorization / 请求头等敏感字段一律不进 DOM。
  const SENSITIVE_TASK_KEYS = /^(api[-_]?key|authorization|headers|token|secret|password)$/i;
  function sanitizeTaskDetail(detail) {
    const out = {};
    Object.keys(detail || {}).forEach(key => {
      if (SENSITIVE_TASK_KEYS.test(key)) return;
      out[key] = detail[key];
    });
    return out;
  }
  function taskStatusWord(status) {
    return { queued: "排队中", running: "进行中", succeeded: "已完成", failed: "失败",
             interrupted: "中断" }[status] || "进行中";
  }
  function ensureTaskCard(taskId, label) {
    const key = String(taskId || label || "task");
    const existing = taskProgressCards.get(key);
    if (existing) return existing;
    clearEmpty();
    const host = taskProgressHost();
    const box = el("div", "oc-task-card is-queued");
    const head = el("div", "oc-task-head");
    head.append(el("span", "oc-task-title", label));
    const state = el("span", "oc-task-state", "排队中");
    head.append(state);
    const steps = el("div", "oc-task-steps");
    box.append(head, steps);
    // 任务卡是与助手卡同级的一张卡：直接进会话流，不再套一层 .oc-amsg ——
    // 套上去就是本批要消灭的卡中卡。两者边框 / 圆角 / 内边距 / 白底完全同款。
    const wrapper = box;
    host.append(wrapper);
    scrollDown();
    // cursor：已渲染到 progress_log 的第几条。用下标而不是文本去重 ——
    // 同一句进度（比如两个零件都"库内无同类件"）本来就该出现两次。
    const card = { key, label, box, wrapper, steps, state, cursor: 0, status: "queued", done: false };
    taskProgressCards.set(key, card);
    return card;
  }
  function pushTaskStep(card, text, tone) {
    if (!card || !text) return;
    // 后端用前导空格 + ↳ / · 表示「这一条是上一步的结果或依据」，
    // 前端据此缩进，动作与结果才分得开。
    const raw = String(text);
    const sub = /^\s{2,}/.test(raw);
    const body = raw.replace(/^[\s]*[↳·]?\s*/, "");
    const step = el("div", `oc-process-step${sub ? " sub" : ""}${tone ? ` ${tone}` : ""}`);
    step.append(el("span", "oc-process-dot",
      tone === "hit" ? "●" : tone === "miss" ? "○" : sub ? "↳" : "•"));
    step.append(el("span", "oc-process-text", body));
    card.steps.append(step);
    scrollDown();
  }
  function setTaskStatus(card, status) {
    if (!card || !status || card.status === status) return;
    card.status = status;
    card.box.classList.remove("is-queued", "is-running", "is-succeeded", "is-failed", "is-interrupted");
    card.box.classList.add(`is-${status}`);
    card.state.textContent = taskStatusWord(status);
  }
  function renderTaskProgress(raw) {
    const detail = sanitizeTaskDetail(raw);
    const taskId = String(detail.taskId || detail.task_id || "");
    const label = String(detail.label || "");
    const log = Array.isArray(detail.log) ? detail.log : [];
    const progressLine = String(detail.progress || "").trim();
    // label / taskId / log / progress 全空时不建空卡（不再把 label 兜底成「处理中」）。
    if (!label && !taskId && !log.length && !progressLine) return;
    const requested = String(detail.status || "running");
    // 中断是真实终态：服务重启 / 切看板 / 桥超时打断的在途任务标「中断」，
    // 既不谎报失败，也不能一直停在「进行中」。
    const interrupted = requested === "interrupted"
      || (requested !== "succeeded" && isInterruptedCode(detail.code));
    const status = requested === "completed" ? "succeeded"
      : interrupted ? "interrupted" : requested;
    // 会话只留「有真实执行内容」的卡：progress_log 明细才算内容。只有标题、只有状态、
    // 只有一句通用「正在…」进度的点击回执一律不建卡 —— 否则秒级同步动作每点一次都会
    // 留下一张「提交审核意见 · 已完成」。
    // 失败也不再单独建卡：这张卡长在 #ocTaskProgressHost（会话底部常驻宿主），建出来
    // 就永远钉在底部，聊多少轮都不动。失败原因照旧进标题行提示位与普通会话输出；
    // 已经在跑的卡（有真实 progress_log 明细）仍就地翻成失败态并显示原因。
    const failureReason = status === "failed"
      ? String(detail.error || detail.message || "").trim() : "";
    const interruptedReason = status === "interrupted"
      ? String(detail.error || detail.message || "").trim() : "";
    // 预期内失败（在途命令被取消、当前视图没有目标输入框…）看板自己已经就地提示过：
    // 已经在跑的卡不翻红，也不再往会话里补噪音。
    const quietFailure = status === "failed" && isQuietBoardCode(detail.code);
    const existingCard = taskProgressCards.has(String(taskId || label || "task"));
    const hasContent = log.length > 0 || existingCard;
    if (!hasContent) return;
    const card = ensureTaskCard(taskId, label);
    const freshSteps = log.length > card.cursor ? log.slice(card.cursor) : [];
    setTaskStatus(card, status);
    if (log.length > card.cursor) {
      for (const entry of log.slice(card.cursor)) {
        const line = String(entry || "").replace(/\s+$/, "");
        if (line.trim()) pushTaskStep(card, line, toneOf(line));
      }
      card.cursor = log.length;
    } else if (!log.length) {
      // 兼容还没有 progress_log 的旧任务记录：退回单条进度。
      const line = progressLine;
      if (line && line !== card.lastFallback) {
        card.lastFallback = line;
        pushTaskStep(card, line, toneOf(line));
      }
    }
    // 落库只提交新出现的进度行：服务端按行去重合并、就地更新同一张卡的状态。
    persistTaskCard(taskId, label, status, freshSteps, failureReason || interruptedReason);
    if (status === "succeeded") {
      card.done = true;
      refreshResultChips();          // 任务跑完，结果按钮重新置底并刷新数量
      loadFiles();                   // 几何、2D 图、导出表格都是任务产出
    }
    if (status === "failed") {
      card.done = true;
      if (quietFailure) return;
      const message = failureReason || "任务失败";
      if (!card.errorNode) {
        card.errorNode = el("div", "oc-task-error", message);
        card.box.append(card.errorNode);
      } else {
        card.errorNode.textContent = message;
      }
    }
    // 中断沿用蓝色 chip，原因进中性行（红字只留给真正的失败），落库与回放沿用同一套。
    if (status === "interrupted") {
      card.done = true;
      const message = interruptedReason || "任务已中断";
      if (!card.noteNode) {
        card.noteNode = el("div", "oc-task-note", message);
        card.box.append(card.noteNode);
      } else {
        card.noteNode.textContent = message;
      }
    }
  }

  // 在途任务卡（还没到终态）在「不可能再有收尾事件」时的唯一收尾：切看板与桥超时。
  // 它们以前留在「进行中」永远转圈；现在就地标成「中断」，并落库。
  function interruptRunningCards(reason) {
    const message = String(reason || "任务已中断").trim();
    taskProgressCards.forEach(card => {
      if (!card || card.done) return;
      if (["succeeded", "failed", "interrupted"].indexOf(String(card.status || "")) >= 0) return;
      renderTaskProgress({ taskId: card.key, label: card.label, status: "interrupted",
                           error: message, log: [] });
    });
  }

  // 任务卡落库：key 固定 task:<taskId>，与 store.append_session_event 的同一 task.id
  // 只留一张卡的口径一致（前端 applyTaskProgress 也复用这套合并规则）。
  function persistTaskCard(taskId, label, status, steps, error) {
    const id = String(taskId || label || "task");
    persistSessionEvent({
      kind: "task", source: "shell", stage: boardStage(), text: String(label || ""),
      key: `task:${id}`,
      task: { id: id, label: String(label || ""), status: String(status || ""),
              steps: (steps || []).map(line => String(line).replace(/\s+$/, "")),
              error: String(error || "") },
    });
  }

  function toneOf(line) {
    // 命中与未命中用不同标记，扫一眼就能看出库里有没有。
    return line.includes("命中") ? "hit" : line.includes("无同类件") ? "miss" : "";
  }

  // 零件清单里点「工艺推荐 / 成本测算」：右侧整块切过去，对话里同步留一条，
  // 否则右边换了内容、左边毫无反应，看不出这两件事是同一个动作。
  window.addEventListener("agent:part-analysis-opened", event => {
    const detail = event.detail || {};
    const who = `${detail.partId || ""} ${detail.partName || ""}`.trim() || "该零件";
    noteInThread(`已在右侧打开「${detail.label}」：${who}。`
      + `点面板里的「生成${detail.label}」后，运行步骤会逐条显示在这里。`);
  });

  // 凡是改写了零件清单的步骤（拆解推荐、STEP 导入…），后端都会顺手重跑检索。
  // 这里只负责把新报告画出来 —— 不重新发起检索，避免和后端各跑一遍。
  window.addEventListener("agent:component-match-updated", () => { renderComponentMatch(); });

  // 后端任务进度 → 统一进度卡（统一父壳也由看板桥播报，独立页沿用原有事件）。
  window.addEventListener("agent:task-progress", event => renderTaskProgress(event.detail || {}));

  // ------------------------------------------------------ 零部件库检索结果
  async function renderComponentMatch() {
    // 统一父壳不自己拉业务数据：零部件库检索明细属于右侧看板，这里只把语义化动作
    // 交给看板（refreshOnly：后端解析时已算好落盘，这里只让看板把报告读出来）。
    if (inUnifiedWorkbench) {
      const board = boardBridge();
      if (!board || typeof board.executeAction !== "function") return;
      try { await board.executeAction("searchComponents", { refreshOnly: true, label: "零部件库检索" }); }
      catch { /* 看板未就绪时忽略，不阻塞会话 */ }
      return;
    }
    if (!projectId) return;
    let report = null;
    try {
      const response = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/component-match`,
        { headers: authHeaders() });
      if (!response.ok) return;
      report = await response.json().catch(() => null);
    } catch { return; }
    const items = report?.items || [];
    if (!items.length) return;

    document.querySelector(".oc-match-card")?.closest(".oc-amsg")?.remove();
    clearEmpty();
    const wrap = el("div", "oc-amsg");
    const body = el("div", "oc-abody");
    body.append(identityLabel());
    const card = el("div", "oc-match-card");
    const summary = report.summary || {};
    card.append(el("h4", null, "零部件库检索结果"));
    const stats = el("div", "oc-match-stats");
    stats.append(statChip("可复用", summary.reuse || 0, "reuse"));
    stats.append(statChip("可改制", summary.modify || 0, "modify"));
    stats.append(statChip("未匹配", summary.new || 0, "new"));
    card.append(stats);
    card.append(el("div", "oc-match-note",
      `已比对库内 ${report.library_size || 0} 条零部件记录；打分为确定性规则，不经模型。`));
    const list = el("div", "oc-match-list");
    items.forEach(item => list.append(matchRow(item)));
    card.append(list);
    card.append(rematchButton(report.generated_at));
    body.append(card);
    wrap.append(body);
    tinner.append(wrap);
    scrollDown();
  }
  // 这份报告是解析时算完落盘的，之后一直照原样显示。库里新登记了零部件，
  // 旧报告不会自己变 —— 表现就是"库里明明有了，界面还是未匹配"。
  // 后端本来就有单独重跑的接口（纯本地 SQL，不调模型、不重解析），只是没有入口。
  function rematchButton(generatedAt) {
    const bar = el("div", "oc-match-actions");
    if (generatedAt) bar.append(el("span", "oc-match-time", `检索于 ${generatedAt}`));
    const button = el("button", "oc-chip");
    button.type = "button";
    button.textContent = "↻ 重新检索零部件库";
    button.onclick = async () => {
      // 统一父壳里没有本页检索入口：重检索是右侧看板的动作，只经桥发语义化动作名。
      if (inUnifiedWorkbench) {
        const board = boardBridge();
        button.disabled = true;
        try {
          if (board && typeof board.executeAction === "function") {
            await board.executeAction("searchComponents", { label: "重新检索零部件库" });
          } else {
            pushSystem("重新检索暂不可用：右侧看板尚未就绪。");
          }
        } finally { button.disabled = false; }
        return;
      }
      button.disabled = true;
      button.textContent = "检索中…";
      try {
        const response = await fetch(
          `/api/projects/${encodeURIComponent(projectId)}/component-match`,
          { method: "POST", headers: authHeaders() });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        await pollMatchTask(data.task_id);
        await renderComponentMatch();      // 整张卡重画，按钮跟着一起换新
      } catch (error) {
        button.disabled = false;
        button.textContent = "↻ 重新检索零部件库";
        boardFailureNotice("重新检索失败：", error);
      }
    };
    bar.append(button);
    return bar;
  }
  // 复用既有的进度时间线：检索的每一步照样播到对话里。
  async function pollMatchTask(taskId) {
    if (!taskId) return;
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      const response = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(taskId)}`,
        { headers: authHeaders() });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const task = await response.json();
      window.dispatchEvent(new CustomEvent("agent:task-progress", {
        detail: { label: "零部件库检索", taskId, status: task.status,
                  progress: task.progress || "",
                  log: Array.isArray(task.progress_log) ? task.progress_log : [],
                  error: task.error || "" },
      }));
      if (task.status === "succeeded") return;
      if (task.status === "failed") throw new Error(task.error || "检索失败");
      // 服务重启把在途任务打断了：停止轮询（卡上已按同一套判定标成「中断」）。
      if (task.status === "interrupted") {
        throw Object.assign(new Error(task.error || "服务重启中断，检索已中止。"), { code: "interrupted" });
      }
    }
  }
  function statChip(label, count, kind) {
    const node = el("span", `oc-match-stat ${kind}`);
    node.append(el("b", null, String(count)));
    node.append(document.createTextNode(` ${label}`));
    return node;
  }
  function matchRow(item) {
    const row = el("div", `oc-match-row ${item.decision}`);
    const head = el("div", "oc-match-row-head");
    head.append(el("span", "oc-match-part", `${item.part_id} ${item.part_name}`));
    head.append(el("span", `oc-match-tag ${item.decision}`, item.decision_label));
    row.append(head);
    if (item.component_code) {
      row.append(el("div", "oc-match-hit",
        `${item.component_code} ${item.component_name || ""} · 匹配度 ${Math.round((item.score || 0) * 100)}%`));
    }
    if (item.gap_notes) row.append(el("div", "oc-match-gap", `差异：${item.gap_notes}`));
    return row;
  }

  // ---------------------------------------------------------------- 看板桥（统一父壳）
  // 统一父壳里，左侧会话只做汇总与分派：2.1 的零件 / 问题 / 报告 / 文件
  // 数量全部来自右侧看板经 TechBoardBridge 播回来的 result-summary 与
  // action-state；父壳不读 iframe DOM，也不另拉一份业务数据。看板阶段（stage）为
  // 'drawing' 时展示 2.1 的能力与结果入口；其余阶段（流程 1、5 的会话上下文由
  // 父壳 syncAgentStageContext() 注入）禁用并隐藏这些入口，但卡片节点绝不删除。
  const CAPABILITY_LABELS = {
    upload: "补充需求图纸", evidence: "解析视图", import3d: "导入已有 3D 模型",
    review: "版本与校核审查", parts: "零件清单", questions: "待澄清问题",
    report: "解析报告", files: "任务文件",
    modelLookup: "联网核验", verify: "校验修正",
  };
  // 会话快捷能力按钮（原 ＋ 能力菜单的五项能力）：只查父壳 #techChatActions 里的
  // data-tech-capability，不读 iframe DOM，也不在别处再藏一份菜单副本。
  const capabilityButtons = [...document.querySelectorAll("#techChatActions [data-tech-capability]")];
  const questionsActionButton = $("ocQuestionsAction");
  const reportActionButton = $("ocReportAction");
  const filesActionButton = $("ocFilesAction");
  const boardFilesCount = $("ocFilesCount");
  // 摘要按 stage 记忆：切换大流程后上一阶段的解析摘要不能继续显示。
  let boardResultSummary = null;

  function boardBridge() {
    return (window.TechBoardBridge && typeof window.TechBoardBridge.snapshot === "function")
      ? window.TechBoardBridge : null;
  }
  function boardStage() {
    const bridge = boardBridge();
    const snapshot = bridge ? bridge.snapshot() : null;
    return String((snapshot && snapshot.stage) || "");
  }

  // 上传选中文件：复用既有 POST /api/projects/{id}/attachments（multipart，字段名
  // files），不新增第二套上传接口；进度、成功与真实错误都进会话时间线。
  async function uploadChatAttachments(files) {
    const list = [...(files || [])];
    if (!list.length) return;
    if (!projectId) {
      pushSystem("还没绑定项目，无法上传附件。请先选择项目后再试。");
      return;
    }
    const attachBtn = $("ocChatAttachBtn");
    if (attachBtn) attachBtn.disabled = true;
    const names = list.map(file => file.name).join("、");
    const taskId = `attach-${Date.now()}`;
    renderTaskProgress({ taskId, label: "上传附件", status: "running", log: [`正在上传：${names}`] });
    try {
      const form = new FormData();
      // 后端 UploadFile 列表的字段名固定为 files；每个文件追加一次，只传二进制，不打印内容。
      list.forEach(file => form.append("files", file, file.name));
      const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/attachments`,
                                  { method: "POST", headers: authHeaders(), body: form });
      if (!response.ok) {
        const detail = (await response.text().catch(() => "")).trim();
        throw new Error(detail || `上传失败（HTTP ${response.status}）`);
      }
      const payload = await response.json().catch(() => ({}));
      const total = Array.isArray(payload.attachments) ? payload.attachments.length : list.length;
      renderTaskProgress({
        taskId, label: "上传附件", status: "succeeded",
        log: [`上传成功：${names}`, `当前附件共 ${total} 个`],
      });
      noteInThread(`已上传 ${list.length} 个附件，任务文件已更新。`);
      refreshBoardAfterUpload();
    } catch (error) {
      const message = (error && error.message) || "上传失败";
      renderTaskProgress({ taskId, label: "上传附件", status: "failed", log: [`上传失败：${message}`], error: message });
      noteInThread(`上传附件失败：${message}`);
    } finally {
      if (attachBtn) attachBtn.disabled = false;
    }
  }

  // 上传成功后把新附件带进看板：统一父壳走看板桥既有刷新动作，独立 2.1 页退回本地清单。
  function refreshBoardAfterUpload() {
    const bridge = boardBridge();
    if (bridge && typeof bridge.refreshData === "function") {
      // 刷新失败不再被空 catch 吞掉：真实原因按普通输出写进会话，用户能看到并重试。
      Promise.resolve(bridge.refreshData({ action: "refreshData", label: "刷新任务文件" }))
        .catch((error) => {
          boardFailureNotice("刷新看板失败：", error);
        });
    }
    requestBoardSummary();
    loadFiles();
  }

  // 左侧会话栏的入口 → 看板视图：显式、单一来源的映射表。键就是左侧控件（结果
  // 按钮 / 任务文件用控件 id），会话快捷能力按钮用 capability:<name>；值是看板视图名。
  const TECH_BOARD_VIEW_ENTRIES = {
    ocQuestionsAction: 'questions',
    ocReportAction: 'report',
    'capability:evidence': 'evidence',
    'capability:review': 'review',
    ocFilesAction: 'files',
  };

  // 独立 2.1 页由同页看板模块（app.js 注册的 window.TechBoardViews）就地打开面板；
  // 统一父壳里没有它，走 TechBoardBridge。
  function boardLocalViews() {
    return (window.TechBoardViews && typeof window.TechBoardViews.open === "function")
      ? window.TechBoardViews : null;
  }
  function boardNavigateBridge() {
    const bridge = boardBridge();
    return (bridge && typeof bridge.navigateView === "function") ? bridge : null;
  }
  // 当前视图只从看板桥快照读，父壳不建第二份状态副本。
  function activeBoardView() {
    const bridge = boardBridge();
    if (!bridge || typeof bridge.snapshot !== "function") return "";
    try {
      const snapshot = bridge.snapshot();
      return String((snapshot && snapshot.view && snapshot.view.active) || "");
    } catch (error) { return ""; }
  }
  function boardEntryNodes() {
    const pairs = [
      ["ocQuestionsAction", "questions"],
      ["ocReportAction", "report"], ["ocFilesAction", "files"],
    ].map(([nodeId, fallback]) => [$(nodeId), TECH_BOARD_VIEW_ENTRIES[nodeId] || fallback]);
    capabilityButtons.forEach(item => pairs.push([
      item, TECH_BOARD_VIEW_ENTRIES[`capability:${item.dataset.techCapability}`] || item.dataset.techCapability,
    ]));
    return pairs.filter(([node]) => Boolean(node));
  }
  // 左侧入口高亮跟随看板回传的 view.active；active 只来自协议，不自行猜测。
  function syncBoardViewActive() {
    const active = activeBoardView();
    boardEntryNodes().forEach(([node, view]) => {
      const on = Boolean(active) && view === active;
      node.classList.toggle("is-active", on);
      if (on) node.setAttribute("aria-current", "true");
      else node.removeAttribute("aria-current");
    });
  }

  // 失败可见、可重试：把真实原因写进会话，并给一个重发同一视图 / 同一 payload 的按钮。
  function showBoardNavFailure(view, payload, error) {
    // 预期内失败（看板切换把在途导航取消、必填项没填）不再进会话：看板自己已经提示过。
    if ((error && error.quiet === true) || isQuietBoardCode(error && error.code)) return;
    const label = (payload && payload.label) || CAPABILITY_LABELS[view] || view || "该视图";
    const reason = (error && error.message) || "看板未响应";
    // 提示按普通输出进流：正文和重试按钮同属一条消息，会被后面的消息自然顶上去。
    // 这里以前额外往 tinner 末尾 append 一个独立提示行节点，且该节点从不移除 ——
    // 一次失败之后底部就永久挂着那行提示，再聊多少轮也不动（那套样式已随批删除）。
    noteInThread(`打开「${label}」失败：${reason}。`, (body) => {
      const retry = el("button", "oc-chip oc-chip-retry", "重试");
      retry.type = "button";
      retry.addEventListener("click", () => {
        retry.disabled = true;
        Promise.resolve(boardNavigateView(view, payload)).finally(() => { retry.disabled = false; });
      });
      body.append(retry);
    });
  }

  // 唯一导航出口：所有左侧入口都只经这里把看板视图名发出去。桥缺失 / 未就绪 /
  // 业务失败都给出可见提示，绝不静默失败。
  function boardNavigateView(view, payload) {
    if (!view) { pushSystem("无法打开看板视图：缺少视图名。"); return null; }
    const body = Object.assign(
      { label: (payload && payload.label) || CAPABILITY_LABELS[view] || view },
      payload || {});
    const bridge = boardNavigateBridge();
    if (bridge) {
      return Promise.resolve(bridge.navigateView(view, body)).catch(error => {
        showBoardNavFailure(view, body, error);
        return null;
      });
    }
    const local = boardLocalViews();
    if (local) {
      try {
        return Promise.resolve(local.open(view, body)).catch(error => {
          showBoardNavFailure(view, body, error);
          return null;
        });
      } catch (error) { showBoardNavFailure(view, body, error); return null; }
    }
    pushSystem(`「${body.label}」暂不可用：看板尚未就绪，请等待右侧步骤加载完成后重试。`);
    return null;
  }

  // 统一分派：会话快捷能力按钮与任务文件按钮只把语义化名字交给同一条导航出口；
  // 不查找 #secUpload 等旧面板，也不另起一套业务数据。
  // 快捷能力混了两类入口：视图入口走 navigate-view，业务动作入口走 execute-action；
  // 发错通道看板会回 unknown-action，所以这里显式分流，不靠名字猜。
  const DRAWING_ACTION_CAPABILITIES = ["modelLookup", "verify"];

  function dispatchDrawingCapability(name, payload) {
    const label = CAPABILITY_LABELS[name] || name;
    const body = Object.assign({ capability: name, label }, payload || {});
    const bridge = boardBridge();
    if (DRAWING_ACTION_CAPABILITIES.includes(name)) return dispatchBoardAction(name, body, bridge, label);
    const view = TECH_BOARD_VIEW_ENTRIES[`capability:${name}`] || name;
    if (bridge && typeof bridge.navigateView !== "function") pushSystem(`「${label}」暂不可用：看板桥不支持视图导航。`);
    else if (bridge && typeof bridge.isReady === "function" && !bridge.isReady()) pushSystem(`「${label}」暂不可用：右侧看板尚未就绪，请稍后重试。`);
    return boardNavigateView(view, body);
  }

  // 业务动作入口（联网核验 / 校验修正）只发 execute-action，绝不落到 navigate-view。
  function dispatchBoardAction(name, body, bridge, label) {
    if (!bridge || typeof bridge.executeAction !== "function") {
      pushSystem(`「${label}」暂不可用：右侧看板尚未就绪，请稍后重试。`);
      return null;
    }
    const call = name === "modelLookup"
      ? bridge.executeAction("modelLookup", body)
      : bridge.executeAction("verify", body);
    return Promise.resolve(call).catch(error => {
      boardFailureNotice(`「${label}」执行失败：`, error);
      return null;
    });
  }

  function setChipCount(node, count) {
    if (!node) return;
    // 只改文本，不重建节点，也不抢输入焦点；数字变化可被 aria-live 读屏感知。
    const next = Number(count) > 0 ? String(Math.floor(Number(count))) : "0";
    if (node.textContent !== next) node.textContent = next;
  }

  // 解析摘要 → 左侧操作栏结果入口（#techChatActions 内的待澄清问题 / 解析报告）。
  // 数量与可用状态只来自看板桥；非 drawing 阶段一律隐藏，切回按最新摘要恢复。
  function applyDrawingResultSummary() {
    const stage = boardStage();
    const isDrawing = stage === "drawing";
    const summary = (boardResultSummary && boardResultSummary.stage === stage) ? boardResultSummary : null;
    const results = (summary && summary.results) || null;
    const questionInfo = (isDrawing && results && results.questions) || {};
    const reportInfo = (isDrawing && results && results.report) || {};
    const fileInfo = (results && results.files) || {};
    const showQuestions = questionInfo.available === true;
    const showReport = reportInfo.available === true;
    setChipCount($("ocQuestionsCount"), questionInfo.count);
    // 两颗结果入口只在 drawing 阶段出现，其余阶段隐藏（不残留上一阶段状态）。
    if (questionsActionButton) {
      questionsActionButton.hidden = !isDrawing;
      questionsActionButton.disabled = !showQuestions;
    }
    if (reportActionButton) {
      reportActionButton.hidden = !isDrawing;
      reportActionButton.disabled = !showReport;
    }
    if (boardFilesCount) {
      const total = Number(fileInfo.count) > 0 ? String(Math.floor(Number(fileInfo.count))) : "—";
      if (boardFilesCount.textContent !== total) boardFilesCount.textContent = total;
    }
    // 五项能力入口是 2.1 专属：其它阶段隐藏并禁用，切回 drawing 按最新状态恢复。
    capabilityButtons.forEach(item => { item.hidden = !isDrawing; item.disabled = !isDrawing; });
  }

  function normalizeBoardSummary(payload) {
    const source = (payload && (payload.summary || payload.results)) || payload || {};
    const results = source.results || source;
    const cell = value => (value && typeof value === "object" ? value : {});
    return {
      stage: String(source.stage || (payload && payload.stage) || boardStage() || ""),
      parsed: source.parsed === true,
      results: {
        parts: { count: cell(results.parts).count, available: cell(results.parts).available === true },
        questions: { count: cell(results.questions).count, available: cell(results.questions).available === true },
        report: { available: cell(results.report).available === true },
        files: { count: cell(results.files).count, available: cell(results.files).available === true },
      },
    };
  }

  // 看板就绪后主动要一次解析摘要（result-summary）：左侧计数 / 可用态只来自看板，
  // 而不是父壳自己猜。刷新动作由看板侧注册（app.js 的 refreshData）。
  function requestBoardSummary() {
    const bridge = boardNavigateBridge();
    if (!bridge || typeof bridge.refreshData !== "function") return;
    try {
      Promise.resolve(bridge.refreshData({ action: "refreshData", label: "刷新看板摘要" }))
        .catch(() => { /* 摘要刷新失败不影响会话本体 */ });
    } catch (error) { /* 桥异常不影响会话本体 */ }
  }

  // 看板状态驱动左侧：就绪 / 动作状态 / 进度 / 选中 全部来自协议消息，
  // 父壳不做 setInterval + DOM 探测。无看板桥时安全退出。
  function bindBoardBridge() {
    const bridge = boardBridge();
    if (!bridge || typeof bridge.subscribe !== "function") return;
    bridge.subscribe(event => {
      const name = String((event && (event.name || event.type)) || "");
      const type = String((event && event.type) || "");
      const payload = (event && event.payload) || {};
      // 桥 20s 没回执不是业务失败：在途任务卡标「中断」，不再永远转圈。
      if (type === "error" && isInterruptedCode(payload.code)) {
        interruptRunningCards(payload.message || "看板超时未响应，任务已中断。");
        return;
      }
      if (name === "result-summary" || name === "summary") {
        boardResultSummary = normalizeBoardSummary(payload);
        applyDrawingResultSummary();
        return;
      }
      if (name === "task-progress") { renderTaskProgress(payload); return; }
      if (name === "task-completed") {
        renderTaskProgress(Object.assign({}, payload, { status: "succeeded" }));
        return;
      }
      if (name === "task-failed") {
        renderTaskProgress(Object.assign({}, payload, { status: "failed" }));
        return;
      }
      if (name === "attached") { boardResultSummary = null; applyDrawingResultSummary(); return; }
      if (name === "detached") {
        // 解绑只清摘要：已落库 / 已在会话里的条目一条都不动（重进项目要能恢复），
        // 也不清任务卡映射 —— 否则重新挂上时会再画一张同样的卡。
        boardResultSummary = null;
        applyDrawingResultSummary();
        // 在途命令已被桥取消，不会再有收尾事件：已建的任务卡标成「中断」。
        interruptRunningCards("看板已切换，任务已中断。");
        return;
      }
      if (name === "ready" || name === "action-state" || name === "selection-changed") {
        applyDrawingResultSummary();
        syncBoardViewActive();
        if (name === "ready") requestBoardSummary();
      }
    });
    try { applyDrawingResultSummary(); } catch { /* 看板桥尚未就绪时忽略 */ }
    try { syncBoardViewActive(); } catch { /* 入口高亮依赖看板快照，失败时忽略 */ }
    // 父壳可能在本脚本订阅之前就已经 ready：这里补一次摘要请求，避免左侧一直空着。
    try {
      const snapshot = bridge && typeof bridge.snapshot === "function" ? bridge.snapshot() : null;
      if (snapshot && snapshot.attached) requestBoardSummary();
    } catch { /* 桥快照不可用时忽略 */ }
  }

  // 附件与能力按钮统一绑定：只分派语义化能力名，不拆业务。
  // 回形针点击必须在同一次用户点击链路里直接触发隐藏文件输入框 —— 先请求、先导航或
  // 先弹层都会失去 user activation，系统文件选择器会被浏览器拦掉。
  const chatAttachBtn = $("ocChatAttachBtn");
  chatAttachBtn?.addEventListener("click", () => document.getElementById("ocChatFileInput").click());
  const chatFileInput = $("ocChatFileInput");
  chatFileInput?.addEventListener("change", () => {
    const files = chatFileInput.files ? [...chatFileInput.files] : [];
    chatFileInput.value = "";      // 清空后同名文件才能再次触发 change
    if (files.length) uploadChatAttachments(files);
  });
  capabilityButtons.forEach(item => {
    item.addEventListener("click", () => dispatchDrawingCapability(item.dataset.techCapability, {}));
  });
  // 独立 2.1 页（index.html）仍保留输入区 ＋ 能力菜单：那里没有父壳桥，由本页 plusMenu()
  // 就地打开同一批看板视图。统一工作台已删除 ＋，这里取不到按钮便自然不生效。
  const plusButton = $("ocPlus");
  plusButton?.addEventListener("click", event => {
    event.stopPropagation();
    if (!inUnifiedWorkbench) plusMenu(event.currentTarget);
  });
  // 任务文件入口沿用同一张映射表与唯一出口。
  filesActionButton?.addEventListener("click", () => dispatchDrawingCapability("files", {}));
  // 三颗结果按钮：控件 id ↔ 看板视图名成对登记，点击只把视图名交给唯一出口。
  [["ocQuestionsAction", "questions"], ["ocReportAction", "report"]]
    .forEach(([nodeId, view]) => {
      const target = TECH_BOARD_VIEW_ENTRIES[nodeId] || view;
      $(nodeId)?.addEventListener("click", () => boardNavigateView(target, { label: CAPABILITY_LABELS[target] }));
    });

  // ---------------------------------------------------------------- 绑定
  sendBtn.onclick = send;
  input.addEventListener("input", autoSize);
  input.addEventListener("keydown", event => {
    // 中文输入法里 Enter 是"上屏候选词"，不是发送：组合期间（isComposing / keyCode 229）
    // 直接放行，避免把没敲完的字当问题发出去。Shift+Enter 换行不受影响。
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); }
  });
  $("ocModelPill")?.addEventListener("click", event => {
    event.stopPropagation();
    settingsPanel(event.currentTarget);
  });
  // 右侧悬浮小窗能折叠成一条标题栏，让出工作区。左边已换成 56px 导航条
  // （tech-rail.css），本身就不占宽度，没有折叠开关 —— 这里保留可选链，
  // 是因为上游/旧页面里那个开关可能还在。
  [["ocSideToggle", "ocAgentDock"], ["ocFilesToggle", "ocFilesDock"]].forEach(([toggle, dock]) => {
    $(toggle)?.addEventListener("click", event => {
      const collapsed = $(dock)?.classList.toggle("collapsed");
      event.currentTarget.setAttribute("aria-expanded", String(!collapsed));
    });
  });
  // 本次任务从头开始：清对话 + 把 2.1 解析的产出退回起点。会丢结果，先确认。
  // 统一工作台左侧 56px 导航的“新对话”通过 window.ocTechAgent.resetTask 复用这里。
  async function resetTaskFlow() {
    const confirmed = window.confirm(
      "本次任务将从头开始：\n\n"
      + "· 清空 Agent 对话\n"
      + "· 清除解析结果、零部件匹配、工艺推荐、成本测算、几何与 2D 图\n\n"
      + "已上传的图纸、技术文档与需求单会保留，可以直接重新解析。\n"
      + "此操作不可撤销，确定继续吗？");
    if (!confirmed) return;
    let result = {};
    try {
      const response = await fetch(api("/new"), { method: "POST", headers: authHeaders() });
      result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    } catch (error) { pushSystem(`重置任务失败：${error.message}`); return; }
    // 设计意图卡是页面结构的一部分，必须保留，只清消息。
    tinner.querySelectorAll(".oc-amsg, .oc-ubub").forEach(node => node.remove());
    taskProgressCards.clear();
    hasParsedIR = false;
    resultBox && (resultBox.hidden = true);
    noteInThread(`本次任务已重置（清除 ${(result.cleared || []).length} 项结果）。`
                 + "图纸与技术文档仍在，点「开始解析」可重新开始。");
    loadFiles();
    // 右侧工作区仍显示旧结果，刷一次才是真的回到起点。
    setTimeout(() => location.reload(), 1200);
  }
  $("ocNewChat")?.addEventListener("click", resetTaskFlow);
  // ---------------------------------------------------------------- 阶段上下文
  // 统一工作台只有父页 #techChatPane 这一个会话宿主：父壳 syncAgentStageContext() 按当前
  // stage 注入的语义上下文只作为请求时的 page_context，不生成任何可见 UI；不跨 iframe
  // 搬运或克隆 DOM，也不触碰消息历史、草稿、滚动位置与项目绑定。
  let stageContext = null;
  // 独立打开的 2.1 图纸解析页（没有统一工作台 #techChatPane）没有父壳注入阶段
  // 上下文，它本身就是 2.1 步骤：只在发请求时用它当 page_context，不渲染额外上下文
  // 卡。统一工作台里由父壳按当前 stage 注入九阶段之一的上下文，这里不参与。
  function standalonePageContext() {
    return document.getElementById("techChatPane") ? "" : "2.1 图纸解析";
  }

  function setStageContext(context) {
    // 只保存模型请求需要的语义上下文：接收端不据此生成任何阶段介绍或阶段按钮。
    stageContext = context && context.pageContext ? context : null;
  }

  function currentPageContext() {
    return (stageContext && stageContext.pageContext) || standalonePageContext();
  }

  window.addEventListener("cpq:tech-agent:stage-context", (event) => setStageContext(event.detail || null));

  // ---------------------------------------------------------------- tech_ui（第 17 步）
  // 与报价 cpq_ui 同构的统一结构化界面事件：Agent 只表达意图，前端按固定映射落到既有
  // 宿主（#techChatActions 结果入口 / #ocTaskProgressHost）与看板桥；模型字符串只经 textContent
  // 呈现，绝不当 HTML 注入，未知 action 明确报错而不是静默忽略。
  const TECH_UI_STAGES = [
    "requirement-create", "requirement-confirm", "requirement-review",
    "drawing", "process", "cost", "summary", "report-review", "report-publish",
  ];
  // 当前 stage → 看板字段回填动作：复用既有具名动作，不为 tech_ui 新造业务函数。
  const TECH_UI_FILL_ACTIONS = {
    "requirement-confirm": "applyConfirmationNote",
    "requirement-review": "applyReviewNote",
    "summary": "updateProcessReportFields",
    "report-review": "applyReportReviewNote",
    "report-publish": "updateReportDistribution",
  };
  const TECH_UI_ACTIONS = {
    focus_view: (ui) => techUiFocusView(ui),
    refresh_view: () => techUiRefreshView(),
    fill_fields: (ui) => techUiFillFields(ui),
    select_part: (ui) => techUiSelectPart(ui),
    show_result_actions: (ui) => techUiShowResultActions(ui),
    show_progress: (ui) => techUiShowProgress(ui),
    set_stage: (ui) => techUiSetStage(ui),
    request_confirmation: (ui) => techUiRequestConfirmation(ui),
  };

  function techUiBridge() {
    return (window.TechBoardBridge && typeof window.TechBoardBridge.executeAction === "function")
      ? window.TechBoardBridge : null;
  }
  function techUiFocusView(ui) {
    const view = String((ui && ui.view) || "").trim();
    if (!view) { pushSystem("看板聚焦失败：缺少视图名。"); return; }
    boardNavigateView(view, { label: (ui && ui.label) || view });
  }
  function techUiRefreshView() {
    const bridge = techUiBridge();
    if (!bridge) { pushSystem("看板尚未就绪，暂时无法刷新视图。"); return; }
    Promise.resolve(bridge.executeAction("refreshData", { label: "刷新看板" }))
      .catch(error => boardFailureNotice("刷新看板失败：", error));
  }
  function techUiFillFields(ui) {
    const stage = String((ui && ui.stage) || boardStage() || "");
    const actionName = TECH_UI_FILL_ACTIONS[stage];
    if (!actionName) { pushSystem(`「${stage || "当前步骤"}」暂不支持自动回填字段。`); return; }
    const bridge = techUiBridge();
    if (!bridge) { pushSystem("看板尚未就绪，暂时无法回填字段。"); return; }
    const fields = (ui && ui.fields) || {};
    Promise.resolve(bridge.executeAction(actionName, { fields: fields, stage: stage }))
      .catch(error => boardFailureNotice("回填字段失败：", error));
  }
  function techUiSelectPart(ui) {
    const partId = String((ui && ui.part_id) || "").trim();
    if (!partId) { pushSystem("打开零件失败：缺少 part_id。"); return; }
    const bridge = techUiBridge();
    if (!bridge) { pushSystem("看板尚未就绪，暂时无法打开零件。"); return; }
    Promise.resolve(bridge.executeAction("selectPart", { part_id: partId }))
      .catch(error => boardFailureNotice("打开零件失败：", error));
  }
  function techUiShowResultActions(ui) {
    applyDrawingResultSummary();
    if (ui && ui.note) noteInThread(ui.note);
  }
  function techUiShowProgress(ui) {
    if (ui && (ui.taskId || ui.task_id || ui.progress || ui.log)) renderTaskProgress(ui);
    else if (ui && ui.note) noteInThread(ui.note);
  }
  function techUiSetStage(ui) {
    const stage = String((ui && ui.stage) || "").trim();
    if (TECH_UI_STAGES.indexOf(stage) < 0) { pushSystem(`切换步骤失败：未知步骤 ${stage || "(空)"}。`); return; }
    // 交给父壳既有切步通道；父壳再按同一份九阶段白名单校验后 applyStage。
    window.dispatchEvent(new CustomEvent("cpq:tech-agent:set-stage", { detail: { stage: stage } }));
  }
  // request_confirmation：固定确认卡片，只有用户点「确认」才执行 target，绝不自动执行。
  function techUiRequestConfirmation(ui) {
    const label = String((ui && ui.label) || (ui && ui.note) || "该操作需要你确认");
    const target = String((ui && ui.target) || "");
    const wrap = el("div", "oc-amsg");
    const body = el("div", "oc-abody");
    const card = el("div", "oc-confirm-card");
    card.append(el("div", "oc-confirm-text", label));
    const row = el("div", "oc-confirm-row");
    const okButton = el("button", "oc-confirm-ok", "确认");
    okButton.type = "button";
    const cancelButton = el("button", "oc-confirm-cancel", "取消");
    cancelButton.type = "button";
    cancelButton.addEventListener("click", () => wrap.remove());
    okButton.addEventListener("click", () => {
      wrap.remove();
      if (!target) return;
      const bridge = techUiBridge();
      if (bridge) {
        Promise.resolve(bridge.executeAction(target, { source: "tech_ui-confirmation" }))
          .catch(error => boardFailureNotice(`「${target}」执行失败：`, error));
      } else {
        boardNavigateView(target, { label: target });
      }
    });
    row.append(okButton, cancelButton);
    card.append(row);
    body.append(card);
    wrap.append(body);
    clearEmpty();
    tinner.append(wrap);
    scrollDown();
  }
  function runTechUi(ui) {
    const action = TECH_UI_ACTIONS[(ui && ui.action) || ""];
    if (!action) { pushSystem(`暂不支持的界面动作：${(ui && ui.action) || "(空)"}。`); return; }
    action(ui || {});
  }

  // 供统一工作台左侧导航复用（新对话 / 设置 / 阶段上下文），旧页面不受影响。
  window.ocTechAgent = {
    resetTask: resetTaskFlow,
    // 换项目重绑：统一工作台不重载页面就能换项目，会话要跟着换并回放目标项目的历史。
    setProject,
    openSettings: (anchor) => settingsPanel(anchor),
    setStageContext: (context) => setStageContext(context),
    // 父壳业务动作失败时把原因同步进左侧会话（pushSystem 是会话提示的唯一入口）。
    notice: pushSystem,
  };

  $("ocFilesRefresh")?.addEventListener("click", () => loadFiles());

  autoSize();
  refreshResultChips();
  loadFiles();
  renderComponentMatch();
  refreshTechShellModel();
  // 父壳（tech-workbench.js）先于本脚本执行，其阶段上下文放在全局供这里首次承接。
  setStageContext(window.ocTechStageContext || null);
  // 父壳模式下订阅看板状态：ready / action-state / result-summary / task-* 驱动左侧入口。
  if (inUnifiedWorkbench) bindBoardBridge();
  if (projectId) {
    // 先回放该项目已持久化的完整会话，再进入正常会话状态（空历史才保留空态）。
    loadHistory().then(() => loadMeta());
  }
  else {
    techShellConn("未连接", false);
    setPillLabel("未选择项目");
  }
})();
