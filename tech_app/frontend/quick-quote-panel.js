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
    workbook: "权威工作簿",
    dwg_confirmed: "DWG 实样确认",
    demo: "演示数据",
    unknown: "未分类"
  };

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
  function renderCases(rows) {
    var table = el("table", "qq-cases");
    var head = el("thead");
    var hrow = el("tr");
    ["案例编号", "盒型", "来源", "审核", "标准单价", "有效期", "能否快速报价"].forEach(function (title) {
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
      body.appendChild(tr);
    });
    table.appendChild(body);
    return table;
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

    if (payload && payload.ok) {
      shell.appendChild(renderSteps(payload.steps));
      shell.appendChild(el("p", "qq-summary",
        "案例库共 " + (payload.case_total || 0) + " 条，现在可用于快速报价的 "
        + (payload.eligible_total || 0) + " 条。"));
      shell.appendChild(renderCases(payload.cases));
      if (!payload.case_total) {
        shell.appendChild(el("p", "qq-empty",
          "标准案例库还是空的。案例有两个来源：业务工作簿固化的成交案例（人工审到「已审核」），"
          + "或从既有报价沉淀（默认草稿，需人工审核）。"));
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

  global.QuickQuotePanel = {
    MODE_PRECISE: MODE_PRECISE,
    MODE_QUICK: MODE_QUICK,
    QUOTE_MODES: QUOTE_MODES,
    MODE_LABELS: MODE_LABELS,
    CASES_PATH: CASES_PATH,
    REASON_LABELS: REASON_LABELS,
    agentBase: agentBase,
    DIFF_HEADERS: DIFF_HEADERS,
    cases: cases,
    renderDiffTable: renderDiffTable,
    render: render,
    open: open,
    close: hide
  };
})(typeof window !== "undefined" ? window : this);
