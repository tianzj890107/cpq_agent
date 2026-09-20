/* 包装报价分区渲染入口（包装第 8 批，Spec docs/specs/packaging-quote-close-loop.md §4.2 / §4.4）
 *
 * 只做一件事：把报价卡片第 2 步快照里的包装整包（packaging_package）交给
 * POST /api/packaging-quote/price，把返回的 s3_markup / s4_markup / s5_basic / s5_detail
 * 四段分区渲染出来。
 *
 * 为什么单独成文件：三个原行业的第 3/4 步加价走的是「SQL 取规则 + 模型判断」
 * (/api/markup/fill)，包装的定价口径是确定的除式（成本 ÷ (1-毛利率)），且**不依赖模型**。
 * 两套口径并存，互不接管：本模块只在快照里确实有 packaging_package 时才动手，
 * 非包装卡片原样走既有工作台（Spec §9）。
 *
 * 全局唯一入口：window.PackagingQuotePanel
 */
(function (global) {
  "use strict";

  var PRICE_PATH = "/api/packaging-quote/price";
  var SECTION_ORDER = ["s3_markup", "s4_markup", "s5_basic", "s5_detail"];
  var SECTION_TITLES = {
    s3_markup: "定价-利润加成",
    s4_markup: "报价-加价与折扣",
    s5_basic: "报价基本信息",
    s5_detail: "报价明细"
  };

  /** 快照里到底有没有包装整包 —— 没有就按"非包装卡片"处理，不报错、不改写快照。 */
  function packageOf(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return null;
    var pkg = snapshot.packaging_package;
    if (pkg && typeof pkg === "object") return pkg;
    return null;
  }

  function isPackaging(snapshot) {
    return packageOf(snapshot) !== null;
  }

  function apiFetch(path, body) {
    if (global.cpqAuthFetch) return global.cpqAuthFetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
  }

  /** 定价：唯一算法在服务端 cpq_packaging_quote.price()，前端只负责传参。 */
  function price(options) {
    options = options || {};
    var body = { package: options.package || {} };
    ["gross_margin_rate", "markup_rate", "pricing_mode", "addons", "discount",
      "tax_rate", "quote_quantity"].forEach(function (key) {
      if (options[key] !== undefined) body[key] = options[key];
    });
    return apiFetch(PRICE_PATH, body).then(function (resp) { return resp.json(); });
  }

  function cell(row, keys) {
    for (var i = 0; i < keys.length; i += 1) {
      if (row && row[keys[i]] !== undefined && row[keys[i]] !== null && row[keys[i]] !== "") {
        return String(row[keys[i]]);
      }
    }
    return "";
  }

  /** 分区 → DOM。形状与 cpq_ui 的 {kind,title,fields|rows} 一致。 */
  function renderSection(id, section) {
    var box = document.createElement("section");
    box.className = "pkg-quote-section";
    box.setAttribute("data-pkg-section", id);
    var title = document.createElement("h4");
    title.className = "pkg-quote-section-title";
    title.textContent = (section && section.title) || SECTION_TITLES[id] || id;
    box.appendChild(title);

    var fields = (section && section.fields) || [];
    var rows = (section && section.rows) || [];
    if (fields.length) {
      var dl = document.createElement("dl");
      dl.className = "pkg-quote-fields";
      fields.forEach(function (field) {
        var dt = document.createElement("dt");
        dt.textContent = field.label || field.key || "";
        var dd = document.createElement("dd");
        dd.textContent = field.value === undefined || field.value === null ? "" : String(field.value);
        dl.appendChild(dt);
        dl.appendChild(dd);
      });
      box.appendChild(dl);
    }
    if (rows.length) {
      var table = document.createElement("table");
      table.className = "pkg-quote-rows";
      rows.forEach(function (row) {
        var tr = document.createElement("tr");
        ["项目", "值", "公式", "来源"].forEach(function (key) {
          if (row[key] === undefined) return;
          var td = document.createElement("td");
          td.className = "pkg-quote-cell-" + key;
          td.textContent = String(row[key]);
          tr.appendChild(td);
        });
        if (tr.childNodes.length === 0) {
          var fallback = document.createElement("td");
          fallback.textContent = cell(row, ["label", "value"]);
          tr.appendChild(fallback);
        }
        table.appendChild(tr);
      });
      box.appendChild(table);
    }
    return box;
  }

  function render(target, payload) {
    if (!target) return null;
    target.innerHTML = "";
    var sections = (payload && payload.sections) || {};
    SECTION_ORDER.forEach(function (id) {
      if (sections[id]) target.appendChild(renderSection(id, sections[id]));
    });
    if (!SECTION_ORDER.some(function (id) { return sections[id]; })) {
      var empty = document.createElement("p");
      empty.className = "pkg-quote-empty";
      empty.textContent = (payload && payload.error) || "这份包装卡片还没有可显示的报价分区。";
      target.appendChild(empty);
    }
    return target;
  }

  /** 卡片入口：快照有包装整包才算包装卡；定价 → 渲染。返回 null 表示"不是包装卡片"。 */
  function renderCard(snapshot, target, options) {
    var pkg = packageOf(snapshot);
    if (!pkg) return null;
    options = options || {};
    var body = {};
    Object.keys(options).forEach(function (key) { body[key] = options[key]; });
    body.package = pkg;
    return price(body).then(function (result) {
      render(target, result);
      return result;
    });
  }

  global.PackagingQuotePanel = {
    PRICE_PATH: PRICE_PATH,
    SECTION_ORDER: SECTION_ORDER,
    packageOf: packageOf,
    isPackaging: isPackaging,
    price: price,
    render: render,
    renderCard: renderCard
  };
})(typeof window !== "undefined" ? window : this);
