/* 2.1 图纸解析页的 Agent 对话框。
 *
 * 后端是 open-claude 的 Conversation（backend/services/oc_agent.py），通过
 * /api/projects/{id}/agent/* 以 SSE 交互，事件格式沿用 open-claude web 桥：
 *   text / tool_use / tool_result / error / done
 *
 * 与页面的关系：
 *   - #intent 与 #btnParse 就在对话第一条消息里，因此 app.js 原有的赋值和点击
 *     绑定不用改动 —— 设计意图天然"出现在对话内容中"，开始解析按钮天然在它下方。
 *   - 左侧 ＋ 能力菜单与零件清单 / 待澄清问题 / 解析报告 / 任务文件入口只负责导航：
 *     统一工作台（tech-workbench.html）里把看板视图名交给 TechBoardBridge.navigateView；
 *     独立 2.1 页没有父壳桥，由同页看板模块（window.TechBoardViews）就地打开同一份面板。
 *   - 具体内容一律留在右侧看板内部：零件清单、待澄清问题、解析报告、解析视图、版本
 *     与校核、任务文件都由看板（app.js）注册成视图后展开，父壳不再持有业务抽屉。
 */
(() => {
  if (new URLSearchParams(location.search).has("embed")) return; // 统一工作台内嵌（embed=1）：会话宿主在父壳 techChatPane，子页不再自建/自连会话。
  const projectId = new URLSearchParams(location.search).get("project") || "";
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
    const info = $("techModelInfo");
    if (!info) return;
    const value = String(text || "").trim();
    const stateWord = ["未连接", "未就绪", "未选择", "未知模型", "未配置", "Agent"].some(word => value.includes(word));
    info.textContent = stateWord || !value ? value : `· ${value}`;
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

  function clearEmpty() { $("ocEmpty")?.remove(); }
  function addUser(text) { clearEmpty(); tinner.append(el("div", "oc-ubub", text)); scrollDown(); }
  function pushSystem(text) {
    clearEmpty();
    const wrap = el("div", "oc-amsg");
    const avatar = el("div", "oc-aav", "!");
    const body = el("div", "oc-abody");
    body.append(el("div", "oc-err-line", `⚠ ${text}`));
    wrap.append(avatar, body);
    tinner.append(wrap);
    scrollDown();
  }
  function addAssistant() {
    clearEmpty();
    const wrap = el("div", "oc-amsg");
    const avatar = el("div", "oc-aav", "✦");
    const body = el("div", "oc-abody");
    const text = el("div", "oc-atxt");
    body.append(text);
    wrap.append(avatar, body);
    tinner.append(wrap);
    scrollDown();
    return { body, text, cards: {}, full: "" };
  }
  function addToolCard(ctx, event) {
    const card = el("div", "oc-art");
    const tile = el("div", "oc-atile", toolIcon(event.name));
    const mid = el("div");
    mid.style.cssText = "flex:1;min-width:0;";
    mid.append(el("div", "oc-art-name", event.name));
    mid.append(el("div", "oc-art-sub", toolSubtitle(event.name, event.input)));
    const state = el("div", null, "");
    state.innerHTML = '<span class="oc-spin">◌</span>';
    card.append(tile, mid, state);
    const result = el("pre", "oc-tool-result");
    result.style.display = "none";
    ctx.body.append(card, result);
    ctx.cards[event.id] = { state, result };
    scrollDown();
  }
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
      Promise.resolve(call).catch(error => pushSystem(`刷新右侧看板失败：${(error && error.message) || "看板未响应"}。`));
      return;
    }
    for (const detail of edits) {
      window.dispatchEvent(new CustomEvent("cad-engine:workbench-chat-edit", { detail }));
    }
  }

  function handleEvent(ctx, event) {
    if (event.type === "text") {
      ctx.full += event.text;
      ctx.text.textContent = ctx.full;
      scrollDown();
      return;
    }
    if (event.type === "tool_use") {
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
      ctx.body.append(el("div", "oc-err-line", `⚠ ${event.error}`));
      scrollDown();
      return;
    }
    if (event.type === "done") {
      if (ctx.full) {
        ctx.text.classList.add("rendered");
        ctx.text.innerHTML = renderMarkdown(ctx.full);
      }
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
      ctx.body.append(el("div", "oc-err-line", `⚠ ${error.message || "连接错误"}`));
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
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
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
        pushSystem(`开始解析失败：${(error && error.message) || "右侧看板未响应"}。`);
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
      pushSystem(`需求解析失败：${(error && error.message) || "右侧看板未响应"}。`);
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
      pushSystem(`刷新需求看板失败：${(error && error.message) || "右侧看板未响应"}。`);
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
      pushSystem(`整合看板操作失败：${(error && error.message) || "右侧看板未响应"}。`);
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
      pushSystem(`成本看板操作失败：${(error && error.message) || "右侧看板未响应"}。`);
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
      pushSystem(`成本看板操作失败：${(error && error.message) || "右侧看板未响应"}。`);
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
      pushSystem(`报告看板操作失败：${(error && error.message) || "右侧看板未响应"}。`);
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
      pushSystem(`报告看板操作失败：${(error && error.message) || "右侧看板未响应"}。`);
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
    wrap.append(el("div", "oc-aav", "✦"));
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
      pushSystem(`带入确认意见失败：${(error && error.message) || "右侧看板未响应"}。`);
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
      pushSystem(`带入审核意见失败：${(error && error.message) || "右侧看板未响应"}。`);
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
    wrap.append(el("div", "oc-aav", "✦"));
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

  function noteInThread(text) {
    clearEmpty();
    const wrap = el("div", "oc-amsg");
    wrap.append(el("div", "oc-aav", "✦"));
    const body = el("div", "oc-abody");
    body.append(el("div", "oc-atxt", text));
    wrap.append(body);
    tinner.append(wrap);
    scrollDown();
  }

  // 解析结果以按钮形式常驻对话**底部**。
  // 原本它挂在设计意图卡（对话第一条）里，聊上几轮就被顶到上面，要往回翻才找得到；
  // 现在每次刷新都把它重新 append 到 tinner 末尾 —— append 已存在的节点是"移动"，
  // 所以不会产生第二份，chips 永远停在最新一条消息下面。
  const resultBox = $("ocResultActions");
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
    if (!resultBox) return;
    // 统一父壳：数量与可用状态只来自看板桥播报的解析摘要，不读 iframe DOM、
    // 也不另拉一份零件 / 问题数据。
    if (inUnifiedWorkbench) { applyDrawingResultSummary(); return; }
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
    if (hasTree) resultBox.append(agentResultChip("零件清单", parts, "ocPartsAction", false));
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
  // 统一父壳渲染进 #ocTaskProgressHost；独立 2.1 页没有该宿主时退回消息流。
  const taskProgressCards = new Map();

  function taskProgressHost() {
    return $("ocTaskProgressHost") || tinner;
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
    return { queued: "排队中", running: "进行中", succeeded: "已完成", failed: "失败" }[status] || "进行中";
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
    let wrapper = box;
    if (host === tinner) {
      wrapper = el("div", "oc-amsg");
      wrapper.append(el("div", "oc-aav", "✦"));
      const body = el("div", "oc-abody");
      body.append(box);
      wrapper.append(body);
    }
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
    card.box.classList.remove("is-queued", "is-running", "is-succeeded", "is-failed");
    card.box.classList.add(`is-${status}`);
    card.state.textContent = taskStatusWord(status);
  }
  function renderTaskProgress(raw) {
    const detail = sanitizeTaskDetail(raw);
    const taskId = String(detail.taskId || detail.task_id || "");
    const label = detail.label || "处理中";
    const requested = String(detail.status || "running");
    const status = requested === "completed" ? "succeeded" : requested;
    const card = ensureTaskCard(taskId, label);
    setTaskStatus(card, status);
    const log = Array.isArray(detail.log) ? detail.log : [];
    if (log.length > card.cursor) {
      for (const entry of log.slice(card.cursor)) {
        const line = String(entry || "").replace(/\s+$/, "");
        if (line.trim()) pushTaskStep(card, line, toneOf(line));
      }
      card.cursor = log.length;
    } else if (!log.length) {
      // 兼容还没有 progress_log 的旧任务记录：退回单条进度。
      const line = String(detail.progress || "").trim();
      if (line && line !== card.lastFallback) {
        card.lastFallback = line;
        pushTaskStep(card, line, toneOf(line));
      }
    }
    if (status === "succeeded") {
      card.done = true;
      refreshResultChips();          // 任务跑完，结果按钮重新置底并刷新数量
      loadFiles();                   // 几何、2D 图、导出表格都是任务产出
    }
    if (status === "failed") {
      card.done = true;
      const message = detail.error || "任务失败";
      if (!card.errorNode) {
        card.errorNode = el("div", "oc-task-error", message);
        card.box.append(card.errorNode);
      } else {
        card.errorNode.textContent = message;
      }
    }
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
    wrap.append(el("div", "oc-aav", "✦"));
    const body = el("div", "oc-abody");
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
        pushSystem(`重新检索失败：${error.message}`);
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
  const capabilityMenu = $("ocCapabilityMenu");
  const capabilityItems = capabilityMenu ? [...capabilityMenu.querySelectorAll("[data-tech-capability]")] : [];
  const partsActionButton = $("ocPartsAction");
  const questionsActionButton = $("ocQuestionsAction");
  const reportActionButton = $("ocReportAction");
  const filesActionButton = $("ocFilesAction");
  const boardFilesCount = $("ocFilesCount");
  // 摘要按 stage 记忆：切换大流程后上一阶段的解析摘要不能继续显示。
  let boardResultSummary = null;
  if (capabilityMenu) capabilityMenu.tabIndex = -1;

  function boardBridge() {
    return (window.TechBoardBridge && typeof window.TechBoardBridge.snapshot === "function")
      ? window.TechBoardBridge : null;
  }
  function boardStage() {
    const bridge = boardBridge();
    const snapshot = bridge ? bridge.snapshot() : null;
    return String((snapshot && snapshot.stage) || "");
  }

  const plusButton = $("ocPlus");

  function firstEnabledCapability() {
    return capabilityItems.find(item => !item.disabled && !item.hidden) || null;
  }
  function closeCapabilityMenu(restoreFocus) {
    if (capabilityMenu && !capabilityMenu.hidden) capabilityMenu.hidden = true;
    if (plusButton) plusButton.setAttribute("aria-expanded", "false");
    if (restoreFocus && plusButton) plusButton.focus();
  }
  function openCapabilityMenu() {
    if (!capabilityMenu || !plusButton) return;
    capabilityMenu.hidden = false;
    plusButton.setAttribute("aria-expanded", "true");
    // 打开后焦点进入第一个可用菜单项，键盘用户不用再 Tab 才能进菜单。
    (firstEnabledCapability() || capabilityMenu).focus();
  }
  function toggleCapabilityMenu() {
    if (!capabilityMenu) return;
    if (capabilityMenu.hidden) openCapabilityMenu();
    else closeCapabilityMenu(true);
  }

  // 左侧会话栏的入口 → 看板视图：显式、单一来源的映射表。键就是左侧控件（结果
  // 按钮 / 任务文件用控件 id），＋ 菜单项用 capability:<name>；值是看板视图名。
  const TECH_BOARD_VIEW_ENTRIES = {
    ocPartsAction: 'parts',
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
      ["ocPartsAction", "parts"], ["ocQuestionsAction", "questions"],
      ["ocReportAction", "report"], ["ocFilesAction", "files"],
    ].map(([nodeId, fallback]) => [$(nodeId), TECH_BOARD_VIEW_ENTRIES[nodeId] || fallback]);
    capabilityItems.forEach(item => pairs.push([
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
    const label = (payload && payload.label) || CAPABILITY_LABELS[view] || view || "该视图";
    const reason = (error && error.message) || "看板未响应";
    noteInThread(`打开「${label}」失败：${reason}。`);
    const retry = el("button", "oc-chip oc-chip-retry", "重试");
    retry.type = "button";
    retry.addEventListener("click", () => {
      retry.disabled = true;
      Promise.resolve(boardNavigateView(view, payload)).finally(() => { retry.disabled = false; });
    });
    const row = el("div", "oc-retry-row");
    row.append(retry);
    (tinner || thread).append(row);
    scrollDown();
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

  // 统一分派：＋ 的四个能力入口与任务文件按钮只把语义化名字交给同一条导航出口；
  // 不查找 #secUpload 等旧面板，也不另起一套业务数据。
  // ＋ 菜单混了两类入口：视图入口走 navigate-view，业务动作入口走 execute-action；
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
      noteInThread(`「${label}」执行失败：${(error && error.message) || "看板未响应"}。`);
      return null;
    });
  }

  function setChipCount(node, count) {
    if (!node) return;
    // 只改文本，不重建节点，也不抢输入焦点；数字变化可被 aria-live 读屏感知。
    const next = Number(count) > 0 ? String(Math.floor(Number(count))) : "0";
    if (node.textContent !== next) node.textContent = next;
  }

  // 解析摘要 → 左侧结果入口。数量只来自看板桥；没有结果时整组隐藏，
  // 有结果时把同一个 #ocResultActions 节点 append 到消息流末尾（append 已存在
  // 节点是“移动”，不会出现第二份）。
  function applyDrawingResultSummary() {
    const stage = boardStage();
    const isDrawing = stage === "drawing";
    const summary = (boardResultSummary && boardResultSummary.stage === stage) ? boardResultSummary : null;
    const results = (summary && summary.results) || null;
    const partInfo = (isDrawing && results && results.parts) || {};
    const questionInfo = (isDrawing && results && results.questions) || {};
    const reportInfo = (isDrawing && results && results.report) || {};
    const fileInfo = (results && results.files) || {};
    const showParts = partInfo.available === true;
    const showQuestions = questionInfo.available === true;
    const showReport = reportInfo.available === true;
    setChipCount($("ocPartsCount"), partInfo.count);
    setChipCount($("ocQuestionsCount"), questionInfo.count);
    if (partsActionButton) partsActionButton.disabled = !showParts;
    if (questionsActionButton) questionsActionButton.disabled = !showQuestions;
    if (reportActionButton) reportActionButton.disabled = !showReport;
    if (boardFilesCount) {
      const total = Number(fileInfo.count) > 0 ? String(Math.floor(Number(fileInfo.count))) : "—";
      if (boardFilesCount.textContent !== total) boardFilesCount.textContent = total;
    }
    // 能力入口是 2.1 专属：其它阶段关菜单并禁用，切回 drawing 按最新状态恢复。
    capabilityItems.forEach(item => { item.disabled = !isDrawing; });
    if (plusButton) plusButton.disabled = !isDrawing;
    if (!isDrawing) closeCapabilityMenu(false);
    const resultActions = $("ocResultActions");
    if (!resultActions) return;
    const anyResult = isDrawing && (showParts || showQuestions || showReport);
    resultActions.hidden = !anyResult;
    if (anyResult && tinner && tinner.lastElementChild !== resultActions) tinner.append(resultActions);
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
      const payload = (event && event.payload) || {};
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
        boardResultSummary = null;
        taskProgressCards.clear();
        applyDrawingResultSummary();
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

  // 上下文菜单与结果按钮统一绑定：只分派语义化能力名，不拆业务。
  plusButton?.addEventListener("click", event => {
    event.stopPropagation();
    if (inUnifiedWorkbench) toggleCapabilityMenu();
    else plusMenu(event.currentTarget);
  });
  capabilityItems.forEach(item => {
    item.addEventListener("click", () => {
      closeCapabilityMenu(false);
      dispatchDrawingCapability(item.dataset.techCapability, {});
    });
  });
  document.addEventListener("click", event => {
    if (!capabilityMenu || capabilityMenu.hidden) return;
    if (capabilityMenu.contains(event.target)) return;
    if (plusButton && plusButton.contains(event.target)) return;
    closeCapabilityMenu(false);
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && capabilityMenu && !capabilityMenu.hidden) closeCapabilityMenu(true);
  });
  // 任务文件入口沿用同一张映射表与唯一出口。
  filesActionButton?.addEventListener("click", () => dispatchDrawingCapability("files", {}));
  // 三颗结果按钮：控件 id ↔ 看板视图名成对登记，点击只把视图名交给唯一出口。
  [["ocPartsAction", "parts"], ["ocQuestionsAction", "questions"], ["ocReportAction", "report"]]
    .forEach(([nodeId, view]) => {
      const target = TECH_BOARD_VIEW_ENTRIES[nodeId] || view;
      $(nodeId)?.addEventListener("click", () => boardNavigateView(target, { label: CAPABILITY_LABELS[target] }));
    });

  // ---------------------------------------------------------------- 绑定
  sendBtn.onclick = send;
  input.addEventListener("input", autoSize);
  input.addEventListener("keydown", event => {
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
  // 统一工作台只有父页 #techChatPane 这一个会话宿主：右侧 iframe 流程（2/3/4）需要的
  // 阶段说明与可用操作由父壳 syncAgentStageContext() 注入到这里。只更新这一小块，
  // 不跨 iframe 搬运或克隆 DOM，也不触碰消息历史、草稿、滚动位置与项目绑定；
  // 父壳传 null（流程 1、5）时整块移除。
  let stageContext = null;

  function contextHost() {
    const pane = $("techChatPane");
    if (!pane) return null;
    let host = $("ocStageContext");
    if (host) return host;
    host = el("div", "oc-stage-context");
    host.id = "ocStageContext";
    const header = pane.querySelector(".tech-chat-header");
    if (header && header.parentNode === pane) header.insertAdjacentElement("afterend", host);
    else pane.prepend(host);
    return host;
  }

  function renderStageContext() {
    const existing = $("ocStageContext");
    if (!stageContext) { existing?.remove(); return; }
    const host = existing || contextHost();
    if (!host) return;
    host.replaceChildren();
    const head = el("div", "oc-stage-context-head");
    head.append(el("span", "oc-stage-context-title", stageContext.label || "当前步骤"));
    if (stageContext.pageContext) head.append(el("span", "oc-stage-context-step", stageContext.pageContext));
    host.append(head);
    if (stageContext.hint) host.append(el("div", "oc-stage-context-hint", stageContext.hint));
    const actions = stageContext.actions || [];
    if (actions.length) {
      const row = el("div", "oc-stage-context-actions");
      actions.forEach((action) => {
        const button = el("button", "oc-stage-context-btn", action.label);
        button.type = "button";
        // 复用父壳底栏的代理定义：点击只把角色回传给父壳，由父壳点同源 iframe 里
        // 的既有业务按钮，这里不复制任何业务逻辑。
        button.addEventListener("click", () => {
          window.dispatchEvent(new CustomEvent("cpq:tech-agent:stage-action", { detail: action }));
        });
        row.append(button);
      });
      host.append(row);
    }
  }

  function setStageContext(context) {
    stageContext = context && context.label ? context : null;
    try { renderStageContext(); } catch { /* 上下文渲染失败不影响会话本体 */ }
  }

  function currentPageContext() {
    return (stageContext && stageContext.pageContext) || "2.1 图纸解析";
  }

  window.addEventListener("cpq:tech-agent:stage-context", (event) => setStageContext(event.detail || null));

  // 供统一工作台左侧导航复用（新对话 / 设置 / 阶段上下文），旧页面不受影响。
  window.ocTechAgent = {
    resetTask: () => resetTaskFlow(),
    openSettings: (anchor) => settingsPanel(anchor),
    setStageContext: (context) => setStageContext(context),
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
  if (projectId) loadMeta();
  else {
    techShellConn("未连接", false);
    setPillLabel("未选择项目");
  }
})();
