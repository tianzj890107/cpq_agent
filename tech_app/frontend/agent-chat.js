/* 2.1 图纸解析页的 Agent 对话框。
 *
 * 后端是 open-claude 的 Conversation（backend/services/oc_agent.py），通过
 * /api/projects/{id}/agent/* 以 SSE 交互，事件格式沿用 open-claude web 桥：
 *   text / tool_use / tool_result / error / done
 *
 * 与页面的关系：
 *   - #intent 与 #btnParse 就在对话第一条消息里，因此 app.js 原有的赋值和点击
 *     绑定不用改动 —— 设计意图天然"出现在对话内容中"，开始解析按钮天然在它下方。
 *   - 补充需求图纸 / 技术文档与视图 / 导入已有模型 / 版本与校核审查 这几组能力的
 *     原始面板仍是 app.js 操作的那些 DOM，只是被移进右侧抽屉，由 ＋ 菜单打开。
 *   - 解析完成后，零件清单与待澄清问题以按钮形式出现在对话结果里，点开抽屉查看。
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

  // ---------------------------------------------------------------- 抽屉
  const drawer = $("ocDrawer");
  const backdrop = $("ocDrawerBackdrop");
  const drawerTitle = $("ocDrawerTitle");
  const drawerBody = $("ocDrawerBody");

  // 抽屉分组：一个入口可能对应多个原有面板。
  const DRAWER_GROUPS = {
    upload: { title: "补充需求图纸", sections: ["secUpload"] },
    evidence: { title: "解析视图", sections: ["secEvidence"] },
    import3d: { title: "导入已有 3D 模型", sections: ["secImport3d"] },
    review: { title: "版本与校核审查", sections: ["secVersions", "verificationDetails", "modelLookupDetails"] },
    parts: { title: "零件清单", sections: ["secParts"] },
    questions: { title: "待澄清问题", sections: ["secQuestions"] },
  };

  function openDrawer(key) {
    const group = DRAWER_GROUPS[key];
    if (!group || !drawer) return;
    drawerTitle.textContent = group.title;
    // 面板始终留在 DOM 里（app.js 持有它们的引用），只切换可见性。
    drawerBody.querySelectorAll("[data-drawer-section]").forEach(section => {
      const visible = group.sections.includes(section.id);
      if (visible) section.removeAttribute("data-drawer-hidden");
      else section.setAttribute("data-drawer-hidden", "true");
      if (visible && section.tagName === "DETAILS") section.open = true;
    });
    drawer.hidden = false;
    backdrop.hidden = false;
    drawer.querySelector("[data-drawer-close]")?.focus();
  }
  function closeDrawer() {
    if (!drawer) return;
    drawer.hidden = true;
    backdrop.hidden = true;
  }
  drawer?.querySelector("[data-drawer-close]")?.addEventListener("click", closeDrawer);
  backdrop?.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && drawer && !drawer.hidden) closeDrawer();
  });

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
        onSaved: data => setModelLabel(data.agent_model || data.text_model || ""),
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
        item.onclick = () => { closePop(); openDrawer(key); };
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
    const options = (settings && settings.text_options) || [];
    const id = String((settings && settings.text_model) || "").trim();
    const option = options.find(item => item && item.id === id);
    return (option && option.label) || id || "未配置模型";
  }
  async function refreshTechShellModel() {
    try {
      let settings = null;
      if (window.LlmSettingsPanel && typeof window.LlmSettingsPanel.load === "function") {
        settings = await window.LlmSettingsPanel.load();
      } else {
        const response = await fetch("/api/llm/settings", { headers: authHeaders() });
        settings = await response.json().catch(() => ({}));
      }
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
      return;
    }
    if (event.type === "tool_result") {
      setToolResult(ctx, event);
      if (pendingEdits.has(event.tool_use_id)) {
        pendingEdits.set(event.tool_use_id, parseEditResult(event));
      }
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
        body: JSON.stringify({ message: text, page_context: "2.1 图纸解析" }),
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
    const button = $("btnParse");
    if (!button) return;
    if (button.disabled) {
      pushSystem("当前还不能解析：请先在 ＋ →「补充需求图纸」里上传需求原图并创建评估任务。");
      openDrawer("upload");
      return;
    }
    if (origin === "Agent") noteInThread("Agent 已请求开始解析，平台流水线正在执行。");
    button.click();
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
    const parts = document.querySelectorAll("#tree .part").length;
    const questions = document.querySelectorAll("#extras .extra-item, #extras .standard-item").length;
    const hasTree = parts > 0;
    const extrasText = ($("extras")?.textContent || "").trim();
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
    if (hasTree) resultBox.append(chip("零件清单", parts, "parts", false));
    if (hasQuestions) resultBox.append(chip("待澄清问题", questions || "", "questions", true));
    // 排最后：前两个是"看某一部分结果"，解析报告是"看整份"，读下来是收束关系。
    if (reportButton) resultBox.append(reportButton);
    resultBox.hidden = false;
    tinner.append(resultBox);        // 置底
  }
  function chip(label, count, drawerKey, warn) {
    const button = el("button", `oc-chip${warn ? " warn" : ""}`);
    button.type = "button";
    button.append(document.createTextNode(label));
    if (count !== "" && count != null) button.append(el("span", "oc-chip-count", String(count)));
    button.onclick = () => openDrawer(drawerKey);
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
  let processCard = null;
  function ensureProcessCard(label) {
    if (processCard && processCard.label === label && !processCard.done) return processCard;
    clearEmpty();
    const wrap = el("div", "oc-amsg");
    wrap.append(el("div", "oc-aav", "✦"));
    const body = el("div", "oc-abody");
    const card = el("div", "oc-process-card");
    const head = el("div", "oc-process-head");
    head.append(el("span", "oc-process-title", label));
    const state = el("span", "oc-process-state", "进行中");
    head.append(state);
    const steps = el("div", "oc-process-steps");
    card.append(head, steps);
    body.append(card);
    wrap.append(body);
    tinner.append(wrap);
    scrollDown();
    // cursor：已渲染到 progress_log 的第几条。用下标而不是文本去重 ——
    // 同一句进度（比如两个零件都"库内无同类件"）本来就该出现两次。
    processCard = { label, card, steps, state, done: false, cursor: 0 };
    return processCard;
  }
  function pushProcessStep(text, tone) {
    if (!processCard || !text) return;
    // 后端用前导空格 + ↳ / · 表示「这一条是上一步的结果或依据」，
    // 前端据此缩进，动作与结果才分得开。
    const raw = String(text);
    const sub = /^\s{2,}/.test(raw);
    const body = raw.replace(/^[\s]*[↳·]?\s*/, "");
    const step = el("div", `oc-process-step${sub ? " sub" : ""}${tone ? ` ${tone}` : ""}`);
    step.append(el("span", "oc-process-dot",
      tone === "hit" ? "●" : tone === "miss" ? "○" : sub ? "↳" : "•"));
    step.append(el("span", "oc-process-text", body));
    processCard.steps.append(step);
    scrollDown();
  }
  function finishProcessCard(ok, text) {
    if (!processCard || processCard.done) return;
    processCard.done = true;
    processCard.state.textContent = ok ? "已完成" : "失败";
    processCard.state.classList.add(ok ? "ok" : "err");
    if (text) pushProcessStep(text, ok ? "" : "err");
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

  window.addEventListener("agent:task-progress", event => {
    const detail = event.detail || {};
    const card = ensureProcessCard(detail.label || "处理中");
    // 后端把进度存成只增不改的日志，这里从上次的游标接着渲染，
    // 一次轮询里后端走了多少步就补多少步 —— 中间步骤不会因为轮询间隔被吞掉。
    const log = Array.isArray(detail.log) ? detail.log : [];
    if (log.length > card.cursor) {
      for (const raw of log.slice(card.cursor)) {
        const line = String(raw || "").replace(/\s+$/, "");
        if (line.trim()) pushProcessStep(line, toneOf(line));
      }
      card.cursor = log.length;
    } else if (!log.length) {
      // 兼容还没有 progress_log 的旧任务记录：退回单条进度。
      const line = String(detail.progress || "").trim();
      if (line && line !== card.lastFallback) {
        card.lastFallback = line;
        pushProcessStep(line, toneOf(line));
      }
    }
    if (detail.status === "succeeded") {
      finishProcessCard(true);
      refreshResultChips();          // 任务跑完，结果按钮重新置底并刷新数量
      // 几何、2D 图、导出表格都是任务产出，跑完就该出现在文件小窗里。
      loadFiles();
    }
    if (detail.status === "failed") finishProcessCard(false, detail.error || "任务失败");
  });

  // ------------------------------------------------------ 零部件库检索结果
  async function renderComponentMatch() {
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
  $("ocPlus")?.addEventListener("click", event => { event.stopPropagation(); plusMenu(event.currentTarget); });
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
    processCard = null;
    hasParsedIR = false;
    resultBox && (resultBox.hidden = true);
    noteInThread(`本次任务已重置（清除 ${(result.cleared || []).length} 项结果）。`
                 + "图纸与技术文档仍在，点「开始解析」可重新开始。");
    loadFiles();
    // 右侧工作区仍显示旧结果，刷一次才是真的回到起点。
    setTimeout(() => location.reload(), 1200);
  }
  $("ocNewChat")?.addEventListener("click", resetTaskFlow);
  // 供统一工作台左侧导航复用（新对话 / 设置），旧页面不受影响。
  window.ocTechAgent = {
    resetTask: () => resetTaskFlow(),
    openSettings: (anchor) => settingsPanel(anchor),
  };
  document.querySelectorAll("[data-open-drawer]").forEach(button => {
    button.addEventListener("click", () => openDrawer(button.dataset.openDrawer));
  });

  $("ocFilesRefresh")?.addEventListener("click", () => loadFiles());

  autoSize();
  refreshResultChips();
  loadFiles();
  renderComponentMatch();
  refreshTechShellModel();
  if (projectId) loadMeta();
  else {
    techShellConn("未连接", false);
    setPillLabel("未选择项目");
  }
})();
