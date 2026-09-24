/* 逆向快速报价 —— 快速报价面板（Spec docs/specs/quick-quote-1-mode-and-case-model.md §2.6）
 *
 * 只做一件事：把报价的「精准 / 快速」两条路径在**报价侧**分开，并让销售看到标准案例库
 * 现在到底有什么 —— 哪些案例能直接用来出快速报价、哪些不能（以及为什么不能）。
 *
 * 批 3 追加：字段工作区的四列对比表 `renderDiffTable(rows)`（Spec
 * docs/specs/quick-quote-3-field-workspace-and-delta-price.md §2.6）—— 仍然不算价，
 * 只渲染后端给的行结构；未确认（pending）的行加可见标记。
 *
 * 边界（刻意为之）：
 *   · 不碰技术工艺链路：不引用技术工艺的任何接口与模块（红测按字符串逐个断言）；
 *   · 不做检索排序、不算差异价、不出最终价 —— 那是批 2/3/4；本模块只列现状与资格，
 *     并把「转精准报价」的出口交给调用方（否则用户点进快速报价就出不去）；
 *   · 模式字符串与后端 QUOTE_MODES 同值，页面不自己造。
 *
 * 全局唯一入口：window.QuickQuotePanel
 */
(function (global) {
  "use strict";

  var MODE_PRECISE = "precise";
  var MODE_QUICK = "quick";
  var QUOTE_MODES = [MODE_PRECISE, MODE_QUICK];
  var MODE_LABELS = { precise: "精准报价", quick: "快速报价" };
  /* 案例列表路由：唯一事实源是 cpq_quick_quote_case.QUICK_QUOTE_CASES_PATH；
     这里只写相对路径，基址由 agentBase() 给（页面同源 /agents/quote）。 */
  var CASES_PATH = "/api/quick-quote/cases";
  /* 解析路由（Spec 批 11 C1）：唯一事实源是 cpq_quick_quote_file.QUICK_QUOTE_PARSE_PATH；
     这里只写相对路径，基址由 agentBase() 给。图纸只从这里进 —— 转换与语义都在服务端，
     浏览器不转换、不调技术工艺接口、不把图纸送视觉模型。 */
  var PARSE_PATH = "/api/quick-quote/parse";
  /* 案例维护的两条写路由（Spec 批 12 §2.5）：模板的唯一事实源是
     cpq_quick_quote_case.QUICK_QUOTE_CASE_FIELDS_PATH / QUICK_QUOTE_CASE_REVIEW_PATH。
     这里由 CASES_PATH 拼出来（同值，但**不写第二份路径字面量**）——后端模板一改，这里跟着走。 */
  var CASE_FIELDS_PATH = CASES_PATH + "/{case_code}/fields";
  var CASE_REVIEW_PATH = CASES_PATH + "/{case_code}/review";
  /* 上传入口的 accept（Spec 批 11 C2）：图纸 + 常见需求文件。 */
  var PARSE_ACCEPT = ".dwg,.dxf,.pdf,.xlsx,.xls,.csv,.txt,.png,.jpg,.jpeg,.webp";

  /* 快速报价工作区的 HTTP 闭环（Spec `e2e-quick-quote-executable-path.md` §3–§5）：
     业务实例建一次，后面所有命令都挂在它下面。`quick_quote_session_id` 就是"确认后还能
     再打开"的那个身份（首页卡片与刷新都靠它），不再靠页面局部变量。 */
  var SESSIONS_PATH = "/api/quick-quote/sessions";
  /* 写命令的幂等头（Spec §3 末句）：同一次确认重发不产生重复卡片。 */
  var IDEMPOTENCY_HEADER = "X-Idempotency-Key";

  /*: 在建状态：最新一次匹配 / 基准 / 工作区 / 差异行 / 报价 + 业务实例身份 + 服务端工作流状态。 */
  var workspaceState = {
    quick_quote_session_id: "",
    inputs: {}, match: {}, baseline: {}, workspace: {}, diff: [], quote: {},
    /* 服务端状态（Spec `quick-quote-full-flow-state-and-recovery.md` §3/§4.4）：七步按钮只消费
       它，前端不拿 `eligible_total` 或本地内存当"这一步能不能点"。 */
    workflow_state: "created", allowed_actions: [], revision: 0, can_confirm: false
  };

  /*: 重试必须复用同一个幂等键（Spec §5）：按命令缓存，**成功之后**才丢弃。 */
  var quickQuoteOperations = {};

  /** 差异行的唯一取值入口：后端放在顶层 `diff`（Spec §4.3），`diff.rows` 只是历史形状兜底。 */
  function quickQuoteDiffRows() {
    var diff = workspaceState.diff;
    if (Array.isArray(diff)) return diff;
    return (diff && diff.rows) || [];
  }

  /*: 最近一次解析的视图模型：面板重绘（案例列表加载完）时不把用户刚上传的结果擦掉。 */
  var lastParseView = null;

  var REASON_LABELS = {
    ok: "可用",
    missing_fields: "缺必需字段",
    retired: "已停用",
    industry_mismatch: "非包装案例",
    source_not_authoritative: "来源不权威",
    not_reviewed: "未审核",
    expired: "已过期"
  };
  var SOURCE_LABELS = {
    workbook: "对照表工作簿",
    dwg_confirmed: "DWG 实样确认",
    demo: "演示数据",
    unknown: "未分类"
  };

  /* ============ 需求原文 → 结构化匹配输入（Spec
     docs/specs/quick-quote-entry-routing-and-packaging-isolation.md §4）================
     契约键集与后端 `cpq_quick_quote_match.QUICK_MATCH_INPUT_KEYS` **逐字同值** —— 快速报价
     只认一套匹配协议，页面不造第二份；`requirement_text` 只当证据留着，**绝不**直接送
     `match_cases()`（那样匹配器拿不到盒型/尺寸，只能全库瞎比）。

     抽取是**确定性字面匹配**：命中就给值并记下它是从哪个片段来的，没命中就进 `missing_inputs`
     让用户补 —— 不猜、不推断、不补造（Spec §4 末句）。 */
  var QUICK_MATCH_INPUT_KEYS = [
    "box_type", "box_family", "closure_type",
    "inner_length", "inner_width", "inner_height",
    "grey_board_gsm", "face_paper_gsm",
    "insert_type", "print_colors", "lamination", "hot_stamping", "v_groove", "magnet",
    "quantity"
  ];
  var QUICK_INPUT_LABELS = {
    box_type: "盒型编码", box_family: "盒型族", closure_type: "闭合方式",
    inner_length: "内长(mm)", inner_width: "内宽(mm)", inner_height: "内高(mm)",
    grey_board_gsm: "灰板克重(g/㎡)", face_paper_gsm: "面纸克重(g/㎡)",
    insert_type: "内托类型", print_colors: "印刷色数", lamination: "覆膜",
    hot_stamping: "烫印", v_groove: "V 槽", magnet: "磁铁", quantity: "数量"
  };
  /*: 关键词闭集（从左到右第一个命中为准）。只做字面包含判断，不做语义推断。 */
  var QUICK_CLOSURE_KEYWORDS = [
    {re: /双开门|对开/, value: "双开门/对开"},
    {re: /天地盖/, value: "天地盖"},
    {re: /书型盒|书形盒|书型/, value: "书型盒/双开门礼盒"},
    {re: /抽屉/, value: "抽屉盒"},
    {re: /翻盖|翻盖盒/, value: "翻盖盒"}
  ];
  var QUICK_FAMILY_KEYWORDS = [
    {re: /圆盘盒|圆盒/, value: "圆盘盒"},
    {re: /酒盒/, value: "书型盒/双开门礼盒"},
    {re: /礼盒/, value: "书型盒/双开门礼盒"},
    {re: /彩盒|纸盒/, value: "彩盒"}
  ];
  var QUICK_INSERT_KEYWORDS = [
    {re: /EVA/i, value: "EVA内托"},
    {re: /灰板内托/, value: "灰板内托"},
    {re: /纸[质浆]内托|纸托/, value: "纸质内托"},
    {re: /海绵/, value: "海绵内托"},
    {re: /吸塑/, value: "吸塑内托"},
    {re: /内托/, value: "内托"}
  ];

  function qqText(value) {
    return value === undefined || value === null ? "" : String(value);
  }

  function qqBlank(value) {
    if (value === undefined || value === null) return true;
    if (typeof value === "string") return value.trim() === "";
    if (Array.isArray(value)) return value.length === 0;
    return false;
  }

  function qqNumber(value) {
    var raw = qqText(value).replace(/,/g, "").trim();
    if (!raw) return null;
    var num = Number(raw);
    return isFinite(num) ? num : null;
  }

  function qqKeyword(text, table) {
    for (var i = 0; i < table.length; i += 1) {
      if (table[i].re.test(text)) return table[i].value;
    }
    return "";
  }

  /** 需求原文 → `{ok, inputs, missing_inputs, evidence}`（纯函数，不碰 DOM / 网络）。 */
  function extractQuickQuoteInputs(text) {
    var raw = qqText(text);
    var inputs = {};
    var evidence = [];
    var put = function (key, value, from) {
      inputs[key] = value;
      if (qqBlank(value)) return;
      evidence.push({key: key, label: QUICK_INPUT_LABELS[key] || key,
                     value: value, source: "text", from: from});
    };

    /* 盒型编码：唯一可追溯的锚点（YT-…／QQ-…），原文抄下来，不做模糊匹配。 */
    var code = /\b((?:YT|QQ)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)\b/.exec(raw);
    put("box_type", code ? code[1] : "", code ? "盒型编码" : "");

    /* 尺寸串：220.5×90×90 / 408×408×50.5 —— 顺序即 长×宽×高（Spec §4 示例）。 */
    var dim = /(\d+(?:\.\d+)?)\s*[×xX*]\s*(\d+(?:\.\d+)?)(?:\s*[×xX*]\s*(\d+(?:\.\d+)?))?/.exec(raw);
    put("inner_length", dim ? qqNumber(dim[1]) : "", dim ? "尺寸串" : "");
    put("inner_width", dim ? qqNumber(dim[2]) : "", dim ? "尺寸串" : "");
    put("inner_height", dim && dim[3] ? qqNumber(dim[3]) : "", dim && dim[3] ? "尺寸串" : "");

    /* 克重：必须带单位（g / 克 / gsm）才算，「灰板内托 / 1000只」里的 1000 不会被当成克重。 */
    var face = /(?:面纸|面纸克重|face\s*paper)[^\d\n]{0,6}?(\d{2,4})\s*(?:g|gsm|克)/i.exec(raw);
    put("face_paper_gsm", face ? qqNumber(face[1]) : "", face ? "面纸克重" : "");
    var grey = /(?:灰板|灰板克重|grey\s*board|gray\s*board)[^\d\n]{0,6}?(\d{2,4})\s*(?:g|gsm|克)/i.exec(raw);
    put("grey_board_gsm", grey ? qqNumber(grey[1]) : "", grey ? "灰板克重" : "");

    put("box_family", qqKeyword(raw, QUICK_FAMILY_KEYWORDS), "盒型族关键词");
    put("closure_type", qqKeyword(raw, QUICK_CLOSURE_KEYWORDS), "闭合方式关键词");
    put("insert_type", qqKeyword(raw, QUICK_INSERT_KEYWORDS), "内托关键词");

    var colors = /(\d+)\s*色/.exec(raw);
    put("print_colors", colors ? (colors[1] + "色") : "", colors ? "印刷色数" : "");
    put("lamination", /覆膜|哑膜|光膜|过胶|淋膜/.test(raw) ? true : "", "");
    put("hot_stamping", /烫金|烫银|烫印/.test(raw) ? true : "", "");
    put("v_groove", /V\s*槽|V-?groove/i.test(raw) ? true : "", "");
    put("magnet", /磁铁|磁扣|磁吸|磁石/.test(raw) ? true : "", "");

    var qty = /(?:数量|qty)\s*[:：]?\s*(\d+)/i.exec(raw)
      || /(\d+)\s*(?:只|个|件|pcs|套)/i.exec(raw);
    put("quantity", qty ? qqNumber(qty[1]) : "", qty ? "数量" : "");

    var missing = [];
    QUICK_MATCH_INPUT_KEYS.forEach(function (key) {
      if (qqBlank(inputs[key])) missing.push(key);
    });
    /* 键集与顺序必须与后端一致：少一个键就是协议漂移，宁可在这里显式补空值。 */
    QUICK_MATCH_INPUT_KEYS.forEach(function (key) {
      if (!(key in inputs)) inputs[key] = "";
    });
    return {ok: true, engine: "quick-quote-text-extract/1", requirement_text: raw,
            inputs: inputs, missing_inputs: missing, evidence: evidence};
  }

  /** 候选案例上屏（Spec §3 第 4/5 条）：逐条候选 + 逐字段证据 + 一眼可点的「选为基准」。
      只渲染后端 match 结果（行、分数、是否可选用都是后端给的），前端不改口径、不自算相似度。 */
  function renderQuickCandidates(match, options) {
    options = options || {};
    match = match || {};
    var box = el("div", "qq-candidates");
    box.appendChild(el("div", "qq-ws-head", "候选案例（标准成交案例库）"));
    var rows = match.candidates || [];
    if (!rows.length) {
      box.appendChild(el("p", "qq-summary",
        "没有候选案例（硬筛选后一条都不剩）。可以改字段重算，或转精准报价。"));
    } else {
      var table = el("table", "qq-candidates-table");
      var head = el("tr");
      ["候选", "状态", "相似度", "标准单价", "差异项", "选为基准"]
        .forEach(function (title) { head.appendChild(el("th", "", title)); });
      table.appendChild(head);
      rows.forEach(function (row) {
        var tr = el("tr");
        tr.appendChild(cell(row.case_code));
        tr.appendChild(cell(REASON_LABELS[row.reason_code] || row.status || ""));
        tr.appendChild(cell(fmtPercent(row.similarity)));
        tr.appendChild(cell(row.standard_price === null || row.standard_price === undefined
          ? "—" : row.standard_price));
        tr.appendChild(cell((row.diff_items || []).map(function (item) {
          return item.label || item.key || "";
        }).filter(Boolean).join("、") || "—"));
        var pickCell = el("td");
        var pick = el("button", "qq-pick", "选为基准");
        pick.disabled = !row.eligible;
        if (!row.eligible) pick.title = row.eligibility_reason || "这条案例不能用于快速报价";
        pick.addEventListener("click", function () {
          if (options.onPick) options.onPick(row.case_code);
        });
        pickCell.appendChild(pick);
        tr.appendChild(pickCell);
        table.appendChild(tr);
      });
      box.appendChild(table);
    }
    var missing = match.missing_inputs || [];
    if (missing.length) {
      box.appendChild(el("p", "qq-summary",
        "还缺 " + missing.length + " 项匹配输入：" + missing.map(function (key) {
          return QUICK_INPUT_LABELS[key] || key;
        }).join("、")));
    }
    return box;
  }

  /** 抽取结果上屏（Spec §4「抽取结果必须展示给用户核对」）：逐字段 + 出处 + 缺什么。 */
  function renderQuickInputs(view, options) {
    options = options || {};
    view = view || {};
    var box = el("div", "qq-inputs");
    box.appendChild(el("div", "qq-ws-head", "需求抽取（逐字段，未命中的必须人工补）"));
    var rows = [];
    QUICK_MATCH_INPUT_KEYS.forEach(function (key) {
      var value = (view.inputs || {})[key];
      if (qqBlank(value)) return;
      var hit = null;
      (view.evidence || []).forEach(function (row) {
        if (row.key === key && !hit) hit = row;
      });
      rows.push([QUICK_INPUT_LABELS[key] || key, value === true ? "是" : String(value),
                 (hit && hit.from) || "原文"]);
    });
    if (!rows.length) {
      box.appendChild(el("p", "qq-summary", "这段需求里没抽出任何可用的匹配字段，请按提示补全后再匹配。"));
    } else {
      var table = el("table", "qq-inputs-table");
      var head = el("tr");
      ["字段", "解析值", "出处"].forEach(function (title) { head.appendChild(el("th", "", title)); });
      table.appendChild(head);
      rows.forEach(function (row) {
        var tr = el("tr");
        row.forEach(function (text) { tr.appendChild(cell(text)); });
        table.appendChild(tr);
      });
      box.appendChild(table);
    }
    var missing = view.missing_inputs || [];
    if (missing.length) {
      var labels = missing.map(function (key) { return QUICK_INPUT_LABELS[key] || key; });
      box.appendChild(el("p", "qq-summary",
        "还要人工补 " + labels.length + " 项：" + labels.join("、")
        + "（缺字段不会被编造，也不参与匹配）。"));
    }
    if (options.plain) return box.textContent;
    return box;
  }

  /** Agent 基址：默认同源 /agents/quote，可被 localStorage 的 cpq:agentUrl 覆盖。 */
  function agentBase() {
    try {
      var saved = global.localStorage && global.localStorage.getItem("cpq:agentUrl");
      if (saved) return String(saved).replace(new RegExp("/+$"), "");
    } catch (e) { /* 受限环境：用默认基址 */ }
    if (global.location && global.location.protocol === "file:") {
      return "http://127.0.0.1:47292";
    }
    return "/agents/quote";
  }

  function apiFetch(url, options) {
    if (global.cpqAuthFetch) return global.cpqAuthFetch(url, options || {});
    return global.fetch(url, options || {});
  }

  /** 取案例库现状。返回原始 payload（ok / cases / notes）；失败不吞，交给调用方显示。 */
  function cases(options) {
    options = options || {};
    var query = [];
    if (options.includeExpired === false) query.push("include_expired=0");
    if (options.caseCode) query.push("case_code=" + encodeURIComponent(options.caseCode));
    var url = agentBase() + CASES_PATH + (query.length ? "?" + query.join("&") : "");
    return apiFetch(url, { method: "GET" }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok && !data.error) {
          data = { ok: false, error: "案例库读取失败（HTTP " + resp.status + "）" };
        }
        return data;
      });
    });
  }

  function el(tag, className, text) {
    var node = global.document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function cell(text) {
    return el("td", "", text === undefined || text === null ? "" : text);
  }

  /** 案例表格：不可用的案例也要显示 —— 销售得知道"库里有像的，但这条不能用"。 */
  /* 案例表格：不可用的案例也要显示 —— 销售得知道"库里有像的，但这条不能用"。
   * 可用的案例行**可选**（Spec `e2e-quick-quote-executable-path.md` §4）：点一下就把它设为基准，
   * 选出后回到页面由 selectQuickQuoteBaseline() 拉工作区（不改这里的渲染职责）。 */
  function renderCases(rows, options) {
    options = options || {};
    var table = el("table", "qq-cases");
    var head = el("thead");
    var hrow = el("tr");
    ["案例编号", "盒型", "来源", "审核", "标准单价", "有效期", "能否快速报价", "选为基准"].forEach(function (title) {
      hrow.appendChild(el("th", "", title));
    });
    head.appendChild(hrow);
    table.appendChild(head);
    var body = el("tbody");
    (rows || []).forEach(function (row) {
      var tr = el("tr", row.eligible ? "qq-case-ok" : "qq-case-blocked");
      tr.appendChild(cell(row.case_code));
      tr.appendChild(cell(row.box_type_code || "—"));
      tr.appendChild(cell(SOURCE_LABELS[row.source_type] || row.source_type || "—"));
      tr.appendChild(cell(row.review_status === "reviewed" ? "已审核"
        : (row.review_status === "retired" ? "已停用" : "草稿")));
      tr.appendChild(cell(row.standard_price === null || row.standard_price === undefined
        ? "—" : (Number(row.standard_price).toFixed(2) + " " + (row.currency || ""))));
      tr.appendChild(cell(row.valid_until || "—"));
      var verdict = el("td", row.eligible ? "qq-ok" : "qq-blocked",
        REASON_LABELS[row.reason_code] || row.reason_code || "");
      if (!row.eligible && row.reason) verdict.title = row.reason;
      tr.appendChild(verdict);
      var pick = el("td", "qq-case-pick");
      if (row.eligible) {
        var pickBtn = el("button", "qq-case-select", "选为基准");
        pickBtn.type = "button";
        pickBtn.setAttribute("data-qq-baseline", row.case_code || "");
        pickBtn.addEventListener("click", function () {
          selectQuickQuoteBaseline(row.case_code, options);
        });
        pick.appendChild(pickBtn);
      } else {
        pick.appendChild(el("span", "qq-case-disabled", "不可用"));
      }
      tr.appendChild(pick);
      body.appendChild(tr);
    });
    table.appendChild(body);
    return table;
  }

  /* 后端 readiness.next_actions 的动作 id → 页面元素名（唯一的映射，别处不许再写一份）。
   * 后端叫 `transfer_to_precise`，页面出口按既有约定写 `transfer_precise`。 */
  var QUOTE_ACTION_TARGETS = { transfer_to_precise: "transfer_precise",
                               import_standard_case: "import_standard_case",
                               fill_case_fields: "fill_case_fields",
                               review_case: "review_case",
                               refresh_case: "refresh_case" };

  /** 库现状（Spec 批 6 §2.4）：文案一律取自后端的 readiness —— headline / detail /
   *  blocked_by / next_actions 逐字渲染，前端**不自己判断**"有没有案例"（否则接口说
   *  2 条、页面说 0 条，现场没法对账）。返回的节点带 data-qq-readiness 与 data-qq-verdict。
   */
  function renderReadiness(readiness, options, payload) {
    readiness = readiness || {};
    options = options || {};
    payload = payload || {};
    var verdict = readiness.verdict || "unknown";
    var box = el("div", "qq-readiness qq-readiness-" + verdict);
    box.setAttribute("data-qq-readiness", verdict);
    box.setAttribute("data-qq-verdict", verdict);
    box.appendChild(el("p", "qq-readiness-headline", readiness.headline || ""));
    if (readiness.detail) {
      box.appendChild(el("p", "qq-readiness-detail", readiness.detail));
    }
    var groups = readiness.blocked_by || [];
    if (groups.length) {
      var list = el("ul", "qq-blocked");
      groups.forEach(function (row) {
        row = row || {};
        var item = el("li", "qq-blocked-row",
          (row.label || row.reason_code || "") + "：" + (row.count || 0) + " 条；" + (row.fix || ""));
        item.setAttribute("data-qq-reason", row.reason_code || "");
        list.appendChild(item);
      });
      box.appendChild(list);
    }
    var actions = readiness.next_actions || [];
    if (actions.length) {
      var bar = el("div", "qq-readiness-actions");
      actions.forEach(function (row) {
        row = row || {};
        var btn = el("button", "qq-action", row.label || row.action || "");
        btn.type = "button";
        btn.setAttribute("data-qq-action", QUOTE_ACTION_TARGETS[row.action] || row.action || "");
        if (row.hint) btn.title = row.hint;
        // 两个回调都没给时把"还没接线"标在 DOM 上，而不是点了没反应（Spec 批 12 §2.5）。
        if (!actionWired(row.action, options)) {
          btn.setAttribute("data-qq-action-pending", "1");
        }
        btn.addEventListener("click", function () {
          if (typeof options.onAction === "function") {
            options.onAction(row.action, row);
            return;
          }
          if (row.action === "fill_case_fields" && typeof options.onCaseFill === "function") {
            options.onCaseFill(actionCase(payload, options));
            return;
          }
          if (row.action === "review_case" && typeof options.onCaseReview === "function") {
            options.onCaseReview(actionCase(payload, options));
            return;
          }
          if (row.action === "transfer_to_precise" && typeof options.onPrecise === "function") {
            options.onPrecise();
            return;
          }
          btn.setAttribute("data-qq-action-pending", "1");
        });
        bar.appendChild(btn);
      });
      box.appendChild(bar);
    }
    return box;
  }

  /** 这个动作有没有接线（Spec 批 12 §2.5）：没有就标 `data-qq-action-pending`。 */
  function actionWired(action, options) {
    options = options || {};
    if (typeof options.onAction === "function") return true;
    if (action === "fill_case_fields") return typeof options.onCaseFill === "function";
    if (action === "review_case") return typeof options.onCaseReview === "function";
    if (action === "transfer_to_precise") return typeof options.onPrecise === "function";
    return true;
  }

  /** 动作作用在哪条案例上：优先 `options.caseCode`，否则案例表第一行。 */
  function actionCase(payload, options) {
    var wanted = (options || {}).caseCode;
    var rows = (payload || {}).cases || [];
    for (var i = 0; i < rows.length; i += 1) {
      if (!wanted || (rows[i] || {}).case_code === wanted) return rows[i];
    }
    return rows[0] || null;
  }

  /** 模板 → 具体路径（`{case_code}` 替换 + 编码）；基址沿用 agentBase()。 */
  function caseUrl(template, caseCode) {
    return agentBase() + String(template || "").replace("{case_code}",
                                                        encodeURIComponent(String(caseCode || "")));
  }

  /** `POST …/{case_code}/fields`：补齐案例字段（Spec 批 12 §2.4）。 */
  function fillCaseFields(caseCode, values) {
    return postJson(caseUrl(CASE_FIELDS_PATH, caseCode), { values: values || {} });
  }

  /** `POST …/{case_code}/review`：改审核状态（Spec 批 12 §2.4）。 */
  function reviewCase(caseCode, status, reason) {
    return postJson(caseUrl(CASE_REVIEW_PATH, caseCode),
                    { status: status, reason: reason || "" });
  }

  function postJson(url, body) {
    return apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok && !data.error) {
          data = { ok: false, error: "案例维护请求失败（HTTP " + resp.status + "）" };
        }
        return data;
      });
    });
  }

  function renderSteps(steps) {
    var box = el("ol", "qq-steps");
    (steps || []).forEach(function (step) {
      var item = el("li", "", (step && step.label) || (step && step.key) || "");
      item.setAttribute("data-step-key", (step && step.key) || "");
      box.appendChild(item);
    });
    return box;
  }

  /** 面板主体：两条路径 + 五步 + 案例表 + 说明 + 「转精准报价」出口。 */
  function render(target, payload, options) {
    if (!target) return null;
    options = options || {};
    target.innerHTML = "";
    var shell = el("div", "qq-shell");

    var head = el("div", "qq-head");
    head.appendChild(el("h3", "qq-title", "快速报价"));
    var modes = el("div", "qq-modes");
    QUOTE_MODES.forEach(function (mode) {
      var btn = el("button", "qq-mode" + (mode === MODE_QUICK ? " is-active" : ""),
        MODE_LABELS[mode]);
      btn.type = "button";
      btn.setAttribute("data-quote-mode", mode);
      if (mode === MODE_PRECISE) {
        btn.addEventListener("click", function () {
          if (typeof options.onPrecise === "function") options.onPrecise();
        });
      }
      modes.appendChild(btn);
    });
    head.appendChild(modes);
    var close = el("button", "qq-close", "关闭");
    close.type = "button";
    close.addEventListener("click", function () { hide(); });
    head.appendChild(close);
    shell.appendChild(head);

    shell.appendChild(el("p", "qq-intro",
      "拿一个跟以前做过的礼盒很接近的需求，从标准案例库里挑最像的成交案例，改几个差异项，"
      + "就出一份有依据的快速报价。全程只在报价侧：不生成技术工艺、不重建 BOM、不做成本重算。"));

    // 图纸 / 文件入口（Spec 批 11）：丢一个 DWG/DXF（或 PDF/Excel/文字需求）就能看到解析出来的
    // 匹配输入与候选案例。解析在服务端做，这里只上屏。
    shell.appendChild(renderParseEntry(lastParseView, options));

    if (payload && payload.ok) {
      shell.appendChild(renderSteps(payload.steps));
      shell.appendChild(el("p", "qq-summary",
        "案例库共 " + (payload.case_total || 0) + " 条，现在可用于快速报价的 "
        + (payload.eligible_total || 0) + " 条。"));
      // 现状话术来自后端 readiness（headline / detail 逐字渲染）：0 行时不再画一张空表，
      // 而是把"库为空"与"有案例但 0 条可用"分开说，并给出下一步动作与转精准出口。
      var readiness = payload.readiness || null;
      if (readiness) {
        shell.appendChild(renderReadiness(readiness, options, payload));
      }
      if ((payload.cases || []).length || !readiness) {
        shell.appendChild(renderCases(payload.cases, options));
      }
    } else {
      shell.appendChild(el("p", "qq-error",
        (payload && payload.error) || "案例库暂时读不到，请稍后重试。"));
    }

    var notes = el("ul", "qq-notes");
    ((payload && payload.notes) || []).forEach(function (note) {
      notes.appendChild(el("li", "", note));
    });
    shell.appendChild(notes);

    // 字段工作区（可改的差异项）+ 报价段（出价 / 转精准）：七个工作区命令都在本模块里，
    // 这里必须真的画出来 —— 否则"改差异项""出价"在人眼里根本不存在
    // （Spec `quick-quote-home-wiring-and-read-diagnostics.md` §C2）。
    // 行与金额一律取后端工作区（workspaceState.workspace / .quote），前端一个数字都不重算。
    var workspace = el("div", "qq-workspace");
    workspace.id = "quickQuoteWorkspaceInline";
    workspace.appendChild(el("h4", "qq-workspace-title", "字段工作区（差异项）"));
    workspace.appendChild(renderDiffTable(quickQuoteDiffRows()));
    var quoteSection = el("div", "qq-quote-section");
    quoteSection.appendChild(el("h4", "qq-workspace-title", "报价"));
    quoteSection.appendChild(renderQuote(workspaceState.quote, options));
    workspace.appendChild(quoteSection);
    shell.appendChild(workspace);

    target.appendChild(shell);
    return target;
  }

  function container() {
    var holder = global.document.getElementById("quickQuotePanel");
    if (!holder) {
      holder = global.document.createElement("div");
      holder.id = "quickQuotePanel";
      holder.className = "qq-mask";
      global.document.body.appendChild(holder);
    }
    return holder;
  }

  function hide() {
    var holder = global.document.getElementById("quickQuotePanel");
    if (holder) holder.style.display = "none";
  }

  /** 打开面板：先渲染"读取中"，再按案例库真实结果渲染（读不到就显示错误，不假装空库）。 */
  function open(options) {
    options = options || {};
    var holder = container();
    holder.style.display = "";
    holder.innerHTML = "";
    var box = el("div", "qq-panel");
    holder.appendChild(box);
    render(box, { ok: true, cases: [], case_total: 0, eligible_total: 0, steps: [],
                  notes: ["正在读取标准案例库…"] }, options);
    return cases({ caseCode: options.caseCode, includeExpired: options.includeExpired })
      .then(function (payload) {
        render(box, payload, options);
        return payload;
      })
      .catch(function (exc) {
        render(box, { ok: false, error: String(exc) }, options);
        return { ok: false, error: String(exc) };
      });
  }

  /* 四列对比表（Spec 批 3 §2.6）：参数 / 基准案例 / 当前报价 / 差异价格。
   *
   * 数据只消费后端工作区给的行结构（field_key / label / display_base /
   * display_current / delta_text / pending / priced / note），前端一个数字都不重算、
   * 也不自己判断该收多少钱：金额与单位已由后端排版好，这里只做展示与标黄。
   */
  var DIFF_HEADERS = ["参数", "基准案例", "当前报价", "差异价格"];

  /* 出价段的动作闭集（Spec 批 8 §2.3）：页面字符串必须与后端同值，只有这两个出口。 */
  var QUOTE_ACTIONS = ["save_quote", "transfer_precise"];
  var QUOTE_ACTION_LABELS = { save_quote: "出价（落版本）", transfer_precise: "转精准报价" };
  /* 费率权威段缺 `reason` 时的兜底文案（后端给了就用后端的，这里只兜底，不判"能不能用"）。 */
  var RATE_AUTHORITY_FALLBACK = "费率不是对照表工作簿费率：只能作为试算，出价前必须换成权威费率";

  function renderDiffTable(rows) {
    rows = rows || [];
    var table = el("table", "qq-diff-table");
    var head = el("thead");
    var hrow = el("tr");
    DIFF_HEADERS.forEach(function (title) {
      var th = el("th", "", title);
      th.setAttribute("scope", "col");
      hrow.appendChild(th);
    });
    head.appendChild(hrow);
    table.appendChild(head);

    var body = el("tbody");
    if (!rows.length) {
      var empty = el("tr", "qq-diff-empty");
      var hint = cell("还没有改动的参数：改动一项就会出现基准值与新报价的对比。");
      hint.setAttribute("colspan", String(DIFF_HEADERS.length));
      empty.appendChild(hint);
      body.appendChild(empty);
    }
    rows.forEach(function (row) {
      row = row || {};
      var tr = el("tr", "qq-diff-row" + (row.pending ? " is-pending" : ""));
      tr.setAttribute("data-field-key", row.field_key || "");
      var param = cell(row.label || row.field_key || "");
      if (row.pending) {
        var badge = el("span", "qq-pending", "待确认");
        badge.setAttribute("title", "这是 Agent 的建议，还没在右侧确认，不影响已确认报价");
        param.appendChild(badge);
      }
      tr.appendChild(param);
      tr.appendChild(cell(row.display_base));
      tr.appendChild(cell(row.display_current));
      var delta = cell(row.priced === false ? (row.note || "不单独计差") : row.delta_text);
      if (row.priced !== false) delta.className = "qq-diff-delta";
      tr.appendChild(delta);
      body.appendChild(tr);
    });
    table.appendChild(body);
    return table;
  }

  /* 出价段：单价 / 区间 / 偏差 / 依据 / 告警 + 两个动作入口（Spec 批 8 §2.3）。
   *
   * 权威口径一律取自后端 payload（rate_authority / warnings），前端不自己判断"这价能不能用"：
   * 非权威一律标 trial；演示费率的告警逐条渲染成 data-qq-warning 节点，**不折叠、不隐藏**。
   */
  function renderQuote(quote, options) {
    quote = quote || {};
    options = options || {};
    var authority = quote.rate_authority || {};
    var formal = authority.authoritative === true && Number(authority.authoritative_total || 0) > 0;
    var box = el("div", "qq-quote");
    box.setAttribute("data-qq-quote", quote.quick_quote_id || "");
    box.setAttribute("data-qq-rate-authority", formal ? "authoritative" : "trial");
    box.appendChild(el("h4", "qq-quote-title",
      formal ? "快速报价（权威费率，可用于正式报价）" : "快速报价（试算：费率未权威）"));

    var facts = el("ul", "qq-quote-facts");
    var currency = quote.base_currency || "CNY";
    facts.appendChild(el("li", "", "单价 " + fmtAmount(quote.unit_price) + " " + currency + "/件"
      + (quote.tax_included ? "（含税）" : "（不含税）")));
    var range = quote.price_range || {};
    if (range.low !== undefined && range.high !== undefined) {
      facts.appendChild(el("li", "", "建议区间 " + fmtAmount(range.low) + " – "
        + fmtAmount(range.high) + " " + currency + "/件"));
    }
    var deviation = quote.deviation || {};
    if (deviation.est_pct !== undefined) {
      facts.appendChild(el("li", "", "预估偏差 ±" + fmtPercent(deviation.est_pct)
        + "（" + (deviation.basis || "按无规则差异项放宽") + "）"));
    }
    if (quote.case_code) {
      facts.appendChild(el("li", "", "基准案例 " + quote.case_code + "（版本 "
        + (quote.case_version || 0) + "），有效期至 " + (quote.valid_until || "未标")));
    }
    box.appendChild(facts);

    // 告警逐条渲染：演示费率的告警必须显著（Spec 批 8 §2.3），不折叠不隐藏。
    var warnings = el("ul", "qq-warnings");
    (quote.warnings || []).forEach(function (item) {
      var li = el("li", "qq-warning", String(item));
      li.setAttribute("data-qq-warning", "");
      warnings.appendChild(li);
    });
    if (!authority.authoritative) {
      var authorityNote = el("li", "qq-warning qq-rate-authority",
        authority.reason || RATE_AUTHORITY_FALLBACK);
      authorityNote.setAttribute("data-qq-warning", "");
      warnings.appendChild(authorityNote);
    }
    box.appendChild(warnings);

    var basis = el("ul", "qq-quote-basis");
    (quote.basis || []).forEach(function (line) {
      basis.appendChild(el("li", "", line));
    });
    box.appendChild(basis);

    var actions = el("div", "qq-quote-actions");
    QUOTE_ACTIONS.forEach(function (action) {
      var btn = el("button", "qq-action", QUOTE_ACTION_LABELS[action] || action);
      btn.type = "button";
      btn.setAttribute("data-qq-action", action);
      btn.addEventListener("click", function () {
        if (typeof options.onAction === "function") options.onAction(action, quote);
      });
      actions.appendChild(btn);
    });
    box.appendChild(actions);
    return box;
  }

  /* ── 图纸 / 文件入口（Spec 批 11） ────────────────────────────────────────── */

  /* 解析结果 → 视图模型（Spec 批 11 C3）。
   *
   * 纯函数：体内不碰 DOM / 全局 / 网络，也不引用本模块其它函数 —— 红测会把这段函数体
   * 单独交给 node 执行，所以只能用语言内置能力。所有中文名一律取后端下发的 labels；
   * 计数与状态只从 payload 数出来，不在前端造第二份词表、也不判断"能不能用"。
   */
  function quickQuoteParseView(result) {
    result = (result && typeof result === "object") ? result : {};
    var ok = result.ok === true;
    var labels = (result.labels && typeof result.labels === "object") ? result.labels : {};
    var labelOf = function (key) {
      var got = labels[key];
      if (got === undefined || got === null || got === "") return String(key);
      return String(got);
    };
    var rawInputs = (result.inputs && typeof result.inputs === "object") ? result.inputs : {};
    var sources = (result.sources && typeof result.sources === "object") ? result.sources : {};
    var keys = Object.keys(rawInputs).sort();
    var inputs = [];
    for (var i = 0; i < keys.length; i++) {
      inputs.push({key: keys[i], label: labelOf(keys[i]), value: rawInputs[keys[i]],
                   source: sources[keys[i]] === undefined ? "" : String(sources[keys[i]])});
    }
    var rawMissing = (result.missing && result.missing.length) ? result.missing : [];
    var missing = [];
    for (var j = 0; j < rawMissing.length; j++) {
      missing.push({key: String(rawMissing[j]), label: labelOf(String(rawMissing[j]))});
    }
    var cap = (result.capability && typeof result.capability === "object") ? result.capability : {};
    var provider = cap.provider ? String(cap.provider) : "";
    var version = cap.provider_version ? String(cap.provider_version) : "";
    var dwgKnown = (cap.dwg === true || cap.dwg === false);
    var capability = {
      provider: provider,
      provider_version: version,
      dwg: cap.dwg === true,
      text: (provider && dwgKnown)
        ? ("解析器 " + provider + (version ? " " + version : "")
           + "；DWG：" + (cap.dwg === true ? "支持" : "不支持"))
        : "解析器能力未知"
    };
    var match = (result.match && typeof result.match === "object") ? result.match : {};
    var candidates = match.candidates || [];
    var parse = (result.parse && typeof result.parse === "object") ? result.parse : {};
    return {
      kind: ok ? String(parse.kind || "ok") : String(result.kind || "error"),
      ok: ok,
      headline: ok
        ? ("解析成功，读出 " + inputs.length + " 项匹配输入，还有 " + missing.length + " 项要人工补")
        : String(result.error || "解析失败"),
      advice: String(result.advice || ""),
      retryable: (!ok && String(result.kind || "") === "service_unavailable"),
      capability: capability,
      inputs: inputs,
      missing: missing,
      warnings: (result.warnings || []).slice(),
      candidates: candidates,
      candidate_total: candidates.length,
      suggested_case_code: String(match.suggested_case_code || ""),
      no_candidate_reason: String(match.no_candidate_reason || ""),
      inputs_complete: match.inputs_complete === true,
      engine_version: String(match.engine_version || "")
    };
  }

  /** 解析结果上屏（Spec 批 11 C3/C5/C6）：能力段、匹配输入、要补的字段、告警、候选。 */
  function renderParse(view, options) {
    view = view || {};
    options = options || {};
    var box = el("div", "qq-parse");
    box.setAttribute("data-qq-parse", view.kind || "");

    box.appendChild(el("p", view.ok ? "qq-summary" : "qq-error", view.headline || ""));

    var cap = el("p", "qq-parse-capability", (view.capability || {}).text || "解析器能力未知");
    cap.setAttribute("data-qq-parse-capability", (view.capability || {}).dwg ? "dwg" : "unknown");
    box.appendChild(cap);

    var inputs = view.inputs || [];
    var table = el("table", "qq-cases qq-parse-inputs");
    table.setAttribute("data-qq-parse-inputs", String(inputs.length));
    var head = el("thead");
    var hrow = el("tr");
    ["参数", "解析值", "来源"].forEach(function (title) { hrow.appendChild(el("th", "", title)); });
    head.appendChild(hrow);
    table.appendChild(head);
    var body = el("tbody");
    if (!inputs.length) {
      var emptyRow = el("tr", "qq-empty");
      var emptyCell = el("td", "", "这个文件没解析出可用的匹配输入。");
      emptyCell.setAttribute("colspan", "3");
      emptyRow.appendChild(emptyCell);
      body.appendChild(emptyRow);
    }
    inputs.forEach(function (row) {
      var tr = el("tr", "qq-parse-input");
      tr.setAttribute("data-qq-parse-key", row.key || "");
      tr.appendChild(el("td", "", row.label || row.key || ""));
      tr.appendChild(el("td", "", fmtValue(row.value)));
      tr.appendChild(el("td", "", row.source || ""));
      body.appendChild(tr);
    });
    table.appendChild(body);
    box.appendChild(table);

    var missing = view.missing || [];
    var missLine = el("p", "qq-parse-missing",
      missing.length
        ? ("还要人工补：" + missing.map(function (row) { return row.label || row.key; }).join("、"))
        : "匹配输入齐了。");
    missLine.setAttribute("data-qq-parse-missing", String(missing.length));
    box.appendChild(missLine);

    var warnings = el("ul", "qq-warnings");
    warnings.setAttribute("data-qq-parse-warnings", String((view.warnings || []).length));
    (view.warnings || []).forEach(function (item) {
      var li = el("li", "qq-warning", String(item));
      li.setAttribute("data-qq-parse-warning", "");
      warnings.appendChild(li);
    });
    box.appendChild(warnings);

    if (view.advice) {
      var advice = el("p", "qq-parse-advice", view.advice + (view.retryable ? "（可重试）" : ""));
      advice.setAttribute("data-qq-parse-advice", view.retryable ? "retryable" : "final");
      box.appendChild(advice);
    }

    var list = el("ul", "qq-parse-candidates");
    list.setAttribute("data-qq-parse-candidates", String(view.candidate_total || 0));
    if (!view.candidate_total) {
      var none = el("li", "qq-empty", view.no_candidate_reason || "没有可用于快速报价的标准案例。");
      none.setAttribute("data-qq-parse-no-candidate", "");
      list.appendChild(none);
    }
    (view.candidates || []).forEach(function (row) {
      row = row || {};
      var li = el("li", "qq-parse-candidate",
        (row.case_code || "") + "（" + (row.status || "") + "，相似度 "
        + fmtPercent((row.similarity_pct || 0) / 100) + "）");
      li.setAttribute("data-qq-parse-case", row.case_code || "");
      list.appendChild(li);
    });
    box.appendChild(list);
    return box;
  }

  function fmtValue(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  /** 选中的文件 → `POST /api/quick-quote/parse`（Spec 批 11 C2）：浏览器只读字节 + base64。 */
  function parseFile(file, options) {
    options = options || {};
    return new Promise(function (resolve, reject) {
      var reader = new global.FileReader();
      reader.onerror = function () {
        reject(new Error("文件读不出来：" + ((file && file.name) || "")));
      };
      reader.onload = function () {
        var text = String(reader.result || "");
        var comma = text.indexOf(",");
        resolve(comma >= 0 ? text.slice(comma + 1) : text);   // 去掉 data:…;base64, 前缀
      };
      reader.readAsDataURL(file);
    }).then(function (b64) {
      return apiFetch(agentBase() + PARSE_PATH, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({name: (file && file.name) || "", data: b64,
                              match: options.match === false ? false : true})
      });
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok && !data.error) {
          data = {ok: false, error: "解析请求失败（HTTP " + resp.status + "）"};
        }
        return data;
      });
    });
  }

  /** 上传入口 + 结果区（Spec 批 11 C2）：结果原地重绘，不动案例库那一段。 */
  function renderParseEntry(state, options) {
    options = options || {};
    var box = el("div", "qq-parse-entry");
    var head = el("div", "qq-parse-entry-head");
    var pick = el("button", "qq-action", "选择图纸／文件");
    pick.type = "button";
    pick.setAttribute("data-qq-parse-pick", "");
    var input = el("input", "qq-parse-file");
    input.type = "file";
    input.accept = PARSE_ACCEPT;
    input.setAttribute("data-qq-parse-input", "");
    input.style.display = "none";
    pick.addEventListener("click", function () { input.click(); });
    head.appendChild(pick);
    head.appendChild(input);
    head.appendChild(el("span", "qq-parse-hint",
      "图纸（DWG/DXF）由服务端统一解析；PDF / Excel / 文字需求同样可以丢进来。"));
    box.appendChild(head);

    var holder = el("div", "qq-parse-result");
    holder.setAttribute("data-qq-parse-result", "");
    if (state) holder.appendChild(renderParse(state, options));
    box.appendChild(holder);

    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) return;
      holder.innerHTML = "";
      holder.appendChild(el("p", "qq-summary", "正在解析 " + file.name + " …"));
      parseFile(file, options).then(function (result) {
        lastParseView = quickQuoteParseView(result);
        holder.innerHTML = "";
        holder.appendChild(renderParse(lastParseView, options));
      }).catch(function (exc) {
        lastParseView = quickQuoteParseView({ok: false, error: String(exc)});
        holder.innerHTML = "";
        holder.appendChild(renderParse(lastParseView, options));
      });
    });
    return box;
  }

  /* ============ 快速报价工作区命令（Spec `e2e-quick-quote-executable-path.md` §3–§5） ============
     一条命令一个函数，逐个对应后端路由：建实例 → 匹配 → 选基准 → 改参数(PUT) → 重算 → 确认 → 转精准。
     前端**不自己算金额**（§5：不得让大模型直接算金额，也不在这里复制公式），
     **不复制解析器**（图纸一律交服务端统一解析服务，见 transferDwgToDrawingFlow）。 */

  function newIdempotencyKey(prefix) {
    return String(prefix || "qq") + "-" + Date.now() + "-" + Math.floor(Math.random() * 1000000);
  }

  function quickQuoteSessionId() {
    return String(workspaceState.quick_quote_session_id || "");
  }

  function commandUrl(command) {
    return agentBase() + SESSIONS_PATH + "/" + encodeURIComponent(quickQuoteSessionId())
      + "/" + command;
  }

  /* 发一条工作区命令：写请求都带幂等键（同一次确认重发不产生重复卡片）。
     workspace 按 Spec §3 第 4 条走 PUT。返回后端原始 payload，失败不吞。 */
  /* 一次用户动作 = 一个 operation id（Spec §5）：超时重试与双击必须复用同一个
     `X-Idempotency-Key`。以前这里每次调用都 `newIdempotencyKey(command)`，于是"点了没反应、
     再点一次"在服务端就是两次新操作 —— 会落两个版本、两张卡。命令跑成功才丢弃这个键，
     失败（网络超时 / 5xx）留在缓存里等下一次重试。 */
  function operationId(command, options) {
    options = options || {};
    if (options.idempotencyKey) return String(options.idempotencyKey);
    if (options.newOperation) delete quickQuoteOperations[command];
    if (!quickQuoteOperations[command]) {
      quickQuoteOperations[command] = newIdempotencyKey(command);
    }
    return quickQuoteOperations[command];
  }

  function finishOperation(command) {
    delete quickQuoteOperations[command];
  }

  function postCommand(command, body, options) {
    options = options || {};
    var headers = {"Content-Type": "application/json"};
    var id = operationId(command, options);
    headers[IDEMPOTENCY_HEADER] = id;
    return apiFetch(commandUrl(command), {
      method: command === "workspace" ? "PUT" : "POST",
      headers: headers,
      body: JSON.stringify(body || {})
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok && !data.error && !data.code) {
          data = {ok: false, error: "快速报价命令失败（HTTP " + resp.status + "）", status: resp.status};
        }
        // 只有**成功**才算这次动作结束；失败保留同一个键，重试才是同一次操作。
        if (data && data.ok !== false) finishOperation(command);
        return data;
      });
    });
  }

  function rememberCommandResult(command, data) {
    data = data || {};
    /* 服务端状态先在（Spec §4.4）：`workflow_state` / `allowed_actions` / `can_confirm` /
       `revision` 每个成功写响应都带，页面按钮照它开关，不自己推断"到第几步了"。 */
    if (data.workflow_state) workspaceState.workflow_state = String(data.workflow_state);
    if (Array.isArray(data.allowed_actions)) workspaceState.allowed_actions = data.allowed_actions;
    if (data.revision !== undefined) workspaceState.revision = Number(data.revision || 0);
    if (data.can_confirm !== undefined) workspaceState.can_confirm = data.can_confirm === true;
    /* 差异行**在顶层** `diff`（Spec §4.3）：后端把行放这里，以前只存 workspace 并读
       `workspace.rows` —— 那个键后端从来不发，于是算得出差异却永远不上屏。 */
    if (Array.isArray(data.diff)) workspaceState.diff = data.diff;
    if (command === "match" && data.match) workspaceState.match = data.match;
    if (command === "baseline" && data.baseline) {
      workspaceState.baseline = data.baseline;
      workspaceState.workspace = data.workspace || {};
    }
    if (command === "workspace" && data.workspace) workspaceState.workspace = data.workspace;
    if (command === "price" && data.quote) workspaceState.quote = data.quote;
    /* 出价成功：首页同一张卡（版本 / 价格 / 状态）由后端 confirm 响应给（Spec §4.4）。 */
    if (command === "confirm" && data.card) workspaceState.card = data.card;
    if (command === "confirm" && data.quick_quote_session_id) {
      workspaceState.quick_quote_session_id = data.quick_quote_session_id;
    }
    return data;
  }

  /** 建（或复用）快速报价业务实例：确认后就是首页卡片要带的身份（Spec §3 第 1 条）。 */
  function openQuickQuoteSession(options) {
    options = options || {};
    var body = {
      session_id: options.sessionId || quickQuoteSessionId(),
      title: options.title || "", customer: options.customer || "",
      project_name: options.projectName || "", industry: options.industry || "packaging",
      /* 报价模式是业务状态（Spec §2）：建实例时就落到服务端，刷新/卡片再打开才能恢复
         对应工作区；面板是快速报价面板，缺省就是 quick。 */
      quote_mode: options.quoteMode || MODE_QUICK,
      // 建实例也必须幂等（Spec §5）：双击 / 超时重试复用同一个 operation id。
      idempotency_key: operationId("session-create", options)
    };
    return apiFetch(agentBase() + SESSIONS_PATH, {
      method: "POST",
      headers: {"Content-Type": "application/json", IDEMPOTENCY_HEADER: body.idempotency_key},
      body: JSON.stringify(body)
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; });
    }).then(function (data) {
      if (data && data.quick_quote_session_id) {
        workspaceState.quick_quote_session_id = data.quick_quote_session_id;
      }
      return rememberCommandResult("session", data);
    });
  }

  /** 刷新/再次打开：按业务实例读回基准、工作区与**已落卡**的那一版报价（Spec §4）。 */
  function openQuickQuoteWorkspace(sessionId, options) {
    options = options || {};
    if (sessionId) workspaceState.quick_quote_session_id = String(sessionId);
    return apiFetch(agentBase() + SESSIONS_PATH + "/" + encodeURIComponent(quickQuoteSessionId()), {
      method: "GET"
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; });
    }).then(function (data) {
      data = data || {};
      workspaceState.inputs = data.inputs || workspaceState.inputs;
      workspaceState.baseline = data.baseline || workspaceState.baseline;
      workspaceState.workspace = data.workspace || workspaceState.workspace;
      workspaceState.quote = data.quote || workspaceState.quote;
      /* 刷新 / 重启后要回到原处（Spec §6）：读契约里的差异、候选、状态、修订号一个都不许丢。 */
      workspaceState.diff = Array.isArray(data.diff) ? data.diff : workspaceState.diff;
      workspaceState.match = data.match || workspaceState.match;
      workspaceState.workflow_state = String(data.workflow_state || workspaceState.workflow_state);
      workspaceState.allowed_actions = Array.isArray(data.allowed_actions)
        ? data.allowed_actions : workspaceState.allowed_actions;
      workspaceState.revision = Number(data.revision || 0);
      var servedQuote = data.quote || {};
      workspaceState.can_confirm = (servedQuote.gaps || []).length === 0
        && (workspaceState.workflow_state === "priced" || workspaceState.workflow_state === "confirmed");
      return data;
    });
  }

  /** 匹配候选案例（只读）：返回排序结果与逐字段证据，供案例表与差异表渲染。 */
  function matchQuickQuoteCases(inputs, options) {
    options = options || {};
    workspaceState.inputs = inputs || workspaceState.inputs || {};
    return postCommand("match", {inputs: workspaceState.inputs, top_n: options.topN}, options)
      .then(function (data) { return rememberCommandResult("match", data); });
  }

  /** 选为基准（案例行点一下）：只有人工显式选过，才谈得上"基准价"（Spec §3 第 3 条）。 */
  function selectQuickQuoteBaseline(caseCode, options) {
    options = options || {};
    if (!quickQuoteSessionId() && options.sessionId) {
      workspaceState.quick_quote_session_id = String(options.sessionId);
    }
    if (!quickQuoteSessionId()) {
      // session 还没建立时**不许**发请求：拼出来的 `/api/quick-quote/sessions//baseline`
      // 与服务端正则 `[^/]+` 匹配不上，注定 404，用户看到的是"点了没反应"
      // （Spec `quick-quote-home-wiring-and-read-diagnostics.md` §C4）。先建实例再选基准。
      return openQuickQuoteSession(options).then(function () {
        if (!quickQuoteSessionId()) {
          return {ok: false, error: "还没建立快速报价实例，无法选择基准案例"};
        }
        return selectQuickQuoteBaseline(caseCode, options);
      });
    }
    return postCommand("baseline", {case_code: caseCode, inputs: workspaceState.inputs}, options)
      .then(function (data) { return rememberCommandResult("baseline", data); });
  }

  /** 保存工作区改动（PUT；只改白名单字段，后端拒绝越界字段）。 */
  function saveQuickQuoteWorkspace(edits, options) {
    options = options || {};
    return postCommand("workspace",
      // 后端 `validate_edits()` 只认 `{字段: 新值}` 字典：缺省值必须是 `{}` 而不是 `[]`，
      // 否则"保存改动"这一路第一次调用就被整批拒绝（`<edits>` 不是字典）。
      {workspace: workspaceState.workspace, edits: edits || {}, source: options.source || "workspace"},
      options).then(function (data) { return rememberCommandResult("workspace", data); });
  }

  /** 确定性重算价格：公式/来源/缺口由后端给；被门禁拦下时带 action=transfer_to_precise。 */
  function repriceQuickQuote(options) {
    options = options || {};
    return postCommand("price", {}, options)
      .then(function (data) { return rememberCommandResult("price", data); });
  }

  /** 确认 → 可见报价卡：返回可再次打开的身份（quick_quote_session_id）与版本号。 */
  function confirmQuickQuote(options) {
    options = options || {};
    return postCommand("confirm",
      {quote: workspaceState.quote, formal: !!options.formal}, options)
      .then(function (data) { return rememberCommandResult("confirm", data); });
  }

  /** 无合格案例 / 关键字段冲突：无损转精准报价（Spec §5 的出口）。 */
  function transferQuickQuoteToPrecise(options) {
    options = options || {};
    return postCommand("transfer-to-precise", {quote: workspaceState.quote}, options)
      .then(function (data) { return rememberCommandResult("transfer-to-precise", data); });
  }

  /* DWG/DXF 的唯一入口：交给服务端的统一解析服务处理（技术工艺侧的图纸链路，报价侧只当客户端）。
     报价侧**不**本地转换、不复制解析器、不把图纸送视觉模型 —— 这里只把文件交出去并回传结果。 */
  function transferDwgToDrawingFlow(file, options) {
    options = options || {};
    var name = String((file && file.name) || "").toLowerCase();
    if (!/\.(dwg|dxf)$/.test(name)) {
      return Promise.resolve({ok: false, code: "not_a_drawing",
        error: "只有 .dwg/.dxf 走图纸解析链路；其它格式请走统一解析入口。"});
    }
    return parseFile(file, options).then(function (data) {
      var view = quickQuoteParseView(data);
      return {ok: !!(data && data.ok !== false), drawing_flow: true,
              file_name: (file && file.name) || "", parse: data, view: view,
              note: "图纸由服务端统一解析服务处理（复用技术工艺侧的图纸链路）："
                    + "报价侧不转换、不做几何求解、不送视觉模型。"};
    });
  }

  /* 差异项的内联编辑器（Spec §4.3）：业务字段**不许**走浏览器 `prompt()` —— 那里没有字段名、
   * 单位、允许范围与来源，也没法回显，改完只剩一行没人核对过的字符串。
   * 这里只画后端已给出的白名单差异行（`diff`），提交时把 `{字段: 新值}` 交给 `options.onSubmit`，
   * 由调用方走 `saveQuickQuoteWorkspace()`（PUT）—— 服务端仍会按白名单再校验一遍。
   */
  function renderQuickQuoteEditor(rows, options) {
    rows = rows || [];
    options = options || {};
    var box = el("div", "qq-editor");
    box.setAttribute("data-qq-editor", "");
    var table = el("table", "qq-diff-table qq-editor-table");
    var head = el("thead");
    var hrow = el("tr");
    ["参数", "基准案例", "当前值", "改为"].forEach(function (title) {
      var th = el("th", "", title);
      th.setAttribute("scope", "col");
      hrow.appendChild(th);
    });
    head.appendChild(hrow);
    table.appendChild(head);

    var body = el("tbody");
    var inputs = {};
    rows.forEach(function (row) {
      row = row || {};
      var key = String(row.field_key || "");
      if (!key) return;
      var tr = el("tr", "qq-editor-row");
      tr.setAttribute("data-field-key", key);
      tr.appendChild(cell(row.label || key));
      tr.appendChild(cell(row.display_base));
      tr.appendChild(cell(row.display_current));
      var td = el("td", "qq-editor-cell");
      var input = el("input", "qq-editor-input");
      input.type = "text";
      input.value = row.value === undefined || row.value === null ? "" : String(row.value);
      input.setAttribute("data-qq-edit-key", key);
      input.setAttribute("placeholder", row.display_current === undefined ? "" : String(row.display_current));
      inputs[key] = input;
      td.appendChild(input);
      tr.appendChild(td);
      body.appendChild(tr);
    });
    if (!Object.keys(inputs).length) {
      var empty = el("tr", "qq-editor-empty");
      var hint = cell("还没有可改的参数：先选基准案例、再「重算」，工作区才会给出可改字段。");
      hint.setAttribute("colspan", "4");
      empty.appendChild(hint);
      body.appendChild(empty);
    }
    table.appendChild(body);
    box.appendChild(table);

    var bar = el("div", "qq-editor-bar");
    var submit = el("button", "qq-ws-btn", "保存改动");
    submit.type = "button";
    submit.setAttribute("data-qq-edit-submit", "");
    submit.addEventListener("click", function () {
      var edits = {};
      Object.keys(inputs).forEach(function (key) {
        var input = inputs[key];
        var next = input.value === undefined || input.value === null ? "" : String(input.value);
        var before = input.getAttribute("placeholder") || "";
        if (next !== before) edits[key] = next;
      });
      if (typeof options.onSubmit === "function") options.onSubmit(edits);
    });
    bar.appendChild(submit);
    box.appendChild(bar);
    return box;
  }

  function fmtAmount(value) {
    if (value === null || value === undefined || isNaN(Number(value))) return "未标";
    return Number(value).toFixed(4);
  }

  function fmtPercent(value) {
    if (value === null || value === undefined || isNaN(Number(value))) return "未标";
    return (Number(value) * 100).toFixed(2) + "%";
  }

  global.QuickQuotePanel = {
    MODE_PRECISE: MODE_PRECISE,
    MODE_QUICK: MODE_QUICK,
    QUOTE_MODES: QUOTE_MODES,
    MODE_LABELS: MODE_LABELS,
    CASES_PATH: CASES_PATH,
    PARSE_PATH: PARSE_PATH,
    CASE_FIELDS_PATH: CASE_FIELDS_PATH,
    CASE_REVIEW_PATH: CASE_REVIEW_PATH,
    PARSE_ACCEPT: PARSE_ACCEPT,
    REASON_LABELS: REASON_LABELS,
    agentBase: agentBase,
    DIFF_HEADERS: DIFF_HEADERS,
    QUOTE_ACTIONS: QUOTE_ACTIONS,
    cases: cases,
    parseFile: parseFile,
    actionWired: actionWired,
    actionCase: actionCase,
    caseUrl: caseUrl,
    fillCaseFields: fillCaseFields,
    reviewCase: reviewCase,
    quickQuoteParseView: quickQuoteParseView,
    renderParse: renderParse,
    renderParseEntry: renderParseEntry,
    renderReadiness: renderReadiness,
    renderDiffTable: renderDiffTable,
    renderQuickQuoteEditor: renderQuickQuoteEditor,
    quickQuoteDiffRows: quickQuoteDiffRows,
    QUICK_MATCH_INPUT_KEYS: QUICK_MATCH_INPUT_KEYS,
    QUICK_INPUT_LABELS: QUICK_INPUT_LABELS,
    extractQuickQuoteInputs: extractQuickQuoteInputs,
    renderQuickCandidates: renderQuickCandidates,
    renderQuickInputs: renderQuickInputs,
    QUOTE_ACTION_LABELS: QUOTE_ACTION_LABELS,
    renderQuote: renderQuote,
    SESSIONS_PATH: SESSIONS_PATH,
    IDEMPOTENCY_HEADER: IDEMPOTENCY_HEADER,
    workspaceState: workspaceState,
    quickQuoteSessionId: quickQuoteSessionId,
    openQuickQuoteSession: openQuickQuoteSession,
    openQuickQuoteWorkspace: openQuickQuoteWorkspace,
    matchQuickQuoteCases: matchQuickQuoteCases,
    selectQuickQuoteBaseline: selectQuickQuoteBaseline,
    saveQuickQuoteWorkspace: saveQuickQuoteWorkspace,
    repriceQuickQuote: repriceQuickQuote,
    confirmQuickQuote: confirmQuickQuote,
    transferQuickQuoteToPrecise: transferQuickQuoteToPrecise,
    transferDwgToDrawingFlow: transferDwgToDrawingFlow,
    render: render,
    open: open,
    close: hide
  };
})(typeof window !== "undefined" ? window : this);
