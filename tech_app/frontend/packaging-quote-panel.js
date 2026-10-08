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
  /* 定价基址：与报价页其它 Agent 调用同源（页面上的 AGENT_URL = "/agents/quote"）。
     可注入（options.base / options.agentUrl / options.agentBase，或全局 PACKAGING_QUOTE_BASE），
     缺省取页面 AGENT_URL，最后才退回基址相对路径 —— 根相对的 /api/* 在 8010 上会被代理给
     技术工艺侧，包装定价拿不到（34 实测 405，Spec packaging-quote-draft-and-card-visibility §1.2）。 */
  var DEFAULT_AGENT_BASE = "/agents/quote";
  var BASE_KEYS = ["base", "agentUrl", "agentBase"];

  function agentBase(options) {
    var injected = "";
    if (options && typeof options === "object") {
      for (var i = 0; i < BASE_KEYS.length; i += 1) {
        if (options[BASE_KEYS[i]]) { injected = options[BASE_KEYS[i]]; break; }
      }
    }
    if (!injected && global.PACKAGING_QUOTE_BASE) injected = global.PACKAGING_QUOTE_BASE;
    if (!injected && typeof global.AGENT_URL === "string") injected = global.AGENT_URL;
    if (!injected) injected = DEFAULT_AGENT_BASE;
    return String(injected).replace(/\/$/, "");
  }

  /** 定价 URL：基址 + PRICE_PATH；基址为空（显式要求基址相对）时退回 PRICE_PATH。 */
  function pricePath(options) {
    var base = agentBase(options);
    return base ? base + PRICE_PATH : PRICE_PATH;
  }
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

  /** 定价：唯一算法在服务端 cpq_packaging_quote.price()，前端只负责传参。
      `publish` 缺省 false = 出草稿（缺口如实带出）；显式 true 才要求缺口清零。 */
  function price(options) {
    options = options || {};
    var body = { package: options.package || {} };
    ["gross_margin_rate", "markup_rate", "pricing_mode", "addons", "discount",
      "tax_rate", "quote_quantity", "publish"].forEach(function (key) {
      if (options[key] !== undefined) body[key] = options[key];
    });
    var request = { base: agentBase(options) };
    return apiFetch(pricePath(request), body).then(function (resp) { return resp.json(); });
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
        appendValue(dd, field.value);
        dl.appendChild(dt);
        dl.appendChild(dd);
      });
      box.appendChild(dl);
    }
    if (rows.length) {
      var table = document.createElement("table");
      table.className = "pkg-quote-rows";
      var keys = [];
      rows.forEach(function (row) { Object.keys(row).forEach(function (key) {
        if (keys.indexOf(key) < 0) keys.push(key);
      }); });
      var header = document.createElement("tr");
      keys.forEach(function (key) {
        var th = document.createElement("th"); th.textContent = key; header.appendChild(th);
      });
      table.appendChild(header);
      rows.forEach(function (row) {
        var tr = document.createElement("tr");
        keys.forEach(function (key) {
          var td = document.createElement("td");
          td.className = "pkg-quote-cell-" + key;
          appendValue(td, row[key]);
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

  var FIELD_LABELS = {
    requirement: '包装需求', bom: '零件与组成', items: '明细', sections: '组成部分',
    route: '工艺路线', steps: '工艺步骤', cost: '成本测算', source: '来源与版本',
    box_type: '盒型', name: '名称', material_text: '材料', material: '材料',
    quantity: '用量', length_mm: '长度（mm）', width_mm: '宽度（mm）',
    confirmed_size: '确认尺寸', estimated_size: '参考尺寸', total_cost: '总成本',
    gaps: '待补项', status: '状态', item_key: '明细编号', parent_part_code: '父零件编码',
    section_id: '组成编号', route_steps: '工艺步骤', bom_items: '零件明细',
    cost_totals: '成本分项', cost_categories: '成本类别', version: '版本',
    packaging_product_name: '包装产品名称', packaging_category: '包装类别',
    box_type_code: '盒型编码', closure_type: '闭合方式', quote_quantity: '报价数量',
    inner_length: '内长（mm）', inner_width: '内宽（mm）', inner_height: '内高（mm）',
    face_paper_gsm: '面纸克重', v_groove: 'V 槽', customer_name: '客户',
    part_code: '零件编码', part_name: '零件名称', bom_category: '明细类别',
    unit: '单位', thickness_mm: '厚度（mm）', size_source_json: '尺寸来源',
    unit_cost: '单位成本', amount: '金额', process_name: '工序名称',
    step_no: '工序序号', dependencies: '前置工序', material_total: '材料费用',
    process_total: '工艺费用', labor_total: '人工费用', tooling_total: '模具费用',
    freight_total: '运输费用', packaging_total: '包装费用', other_total: '其他费用',
    subtotal: '小计', loss_amount: '损耗费用', result_version: '结果版本',
    evidence: '依据', sections_complete: '组成完整', section_gaps: '组成缺口'
  };
  function appendValue(target, value) {
    if (value === undefined || value === null) { target.textContent = '—'; return; }
    if (typeof value !== 'object') { target.textContent = String(value); return; }
    if (Array.isArray(value)) {
      if (!value.length) { target.textContent = '暂无明细'; return; }
      value.forEach(function (item, index) {
        var group = document.createElement('details');
        group.open = value.length <= 4;
        var title = document.createElement('summary');
        title.textContent = (item && (item.name || item.part_name || item.title || item.item_key)) || ('明细 ' + (index + 1));
        group.appendChild(title);
        var content = document.createElement('div'); appendValue(content, item); group.appendChild(content);
        target.appendChild(group);
      }); return;
    }
    var dl = document.createElement('dl'); dl.className = 'pkg-quote-fields';
    Object.keys(value).forEach(function (key) {
      var dt = document.createElement('dt'); dt.textContent = FIELD_LABELS[key] || key;
      var dd = document.createElement('dd'); appendValue(dd, value[key]);
      dl.appendChild(dt); dl.appendChild(dd);
    }); target.appendChild(dl);
  }
  function renderPackage(target, pkg) {
    if (!target || !pkg) return null;
    target.innerHTML = '';
    Object.keys(pkg).forEach(function (key) {
      var section = document.createElement('details'); section.className = 'pkg-quote-section';
      section.open = ['requirement', 'bom', 'route', 'cost'].indexOf(key) >= 0;
      var title = document.createElement('summary'); title.textContent = FIELD_LABELS[key] || key;
      section.appendChild(title);
      var content = document.createElement('div'); appendValue(content, pkg[key]); section.appendChild(content);
      target.appendChild(section);
    }); return target;
  }
  function ensureStyles() {
    if (!document.head || document.getElementById('packagingQuoteStyles')) return;
    var style = document.createElement('style'); style.id = 'packagingQuoteStyles';
    style.textContent = '.pkg-quote-section{background:#fff;border:1px solid var(--color-border,#e5e7eb);border-radius:12px;padding:12px;margin:10px 0;overflow:auto}.pkg-quote-section summary{cursor:pointer;padding:8px;border-radius:6px}.pkg-quote-section summary:hover{background:var(--color-primary-light,#eff6ff)}.pkg-quote-fields{display:grid;grid-template-columns:minmax(100px,160px) minmax(0,1fr);gap:8px 12px}.pkg-quote-fields dt{color:var(--color-text-muted,#64748b)}.pkg-quote-fields dd{margin:0;overflow-wrap:anywhere}.pkg-quote-rows{width:100%;border-collapse:collapse}.pkg-quote-rows th,.pkg-quote-rows td{padding:8px;text-align:left;border-bottom:1px solid var(--color-border,#e5e7eb)}@media(max-width:640px){.pkg-quote-fields{grid-template-columns:1fr}.pkg-quote-fields dd{padding-bottom:8px}}';
    document.head.appendChild(style);
  }

  function render(target, payload) {
    if (!target) return null;
    ensureStyles();
    target.innerHTML = "";
    var quote = (payload && payload.quote) || {};
    if (quote.draft || quote.publish_blocked) {
      var warning = document.createElement("div");
      warning.className = "pkg-quote-warning";
      warning.setAttribute("data-pkg-incomplete-cost", "1");
      warning.textContent = "非完整成本：本报价仅供内部试算，仍有 "
        + String(quote.gap_count || (quote.gaps || []).length || 0)
        + " 条成本缺口，不能作为正式报价对外发布。";
      target.appendChild(warning);
    }
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
    Object.keys(options).forEach(function (key) {
      if (BASE_KEYS.indexOf(key) >= 0) return;          // 基址是调用参数，不进请求体
      body[key] = options[key];
    });
    body.package = pkg;
    body.base = agentBase(options);
    return price(body).then(function (result) {
      render(target, result);
      return result;
    });
  }

  global.PackagingQuotePanel = {
    PRICE_PATH: PRICE_PATH,
    DEFAULT_AGENT_BASE: DEFAULT_AGENT_BASE,
    agentBase: agentBase,
    pricePath: pricePath,
    SECTION_ORDER: SECTION_ORDER,
    packageOf: packageOf,
    isPackaging: isPackaging,
    price: price,
    render: render,
    renderPackage: function (target, pkg) { ensureStyles(); return renderPackage(target, pkg); },
    renderCard: renderCard
  };
})(typeof window !== "undefined" ? window : this);
