/* 全局模型设置面板 —— 报价、配置、规则、技术工艺四个入口共用这一份实现。
 *
 * 唯一事实源是报价的 cpq_settings.json，唯一读写接口是 /api/settings：
 * 模型只有一个字段（图纸解析、文档分析、工艺推荐、成本测算和 Agent 对话都用它），
 * 不再有"助手模型 / 技术工艺模型"两套页签，也不再保留任何技术工艺专属设置接口。
 *
 * 字段：模型、Temperature、最大输出 Tokens、深度思考、当前 provider 的 API Key。
 * API Key 只写不读：接口回的是 configured / 打码提示，输入框保存成功后立即清空。
 *
 * 用法：
 *   window.LlmSettingsPanel.mount(container, { onSaved })
 * container 既可以是弹层正文，也可以是对话框正文 —— 面板自己不管定位，
 * 居中/遮罩由各页面的外壳负责，四个入口共用同一套抽屉语义。
 */
(() => {
  "use strict";

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const authHeaders = (json = false) => {
    const headers = {};
    const token = localStorage.getItem("authToken") || localStorage.getItem("cad_engine_token");
    if (token) headers.Authorization = `Bearer ${token}`;
    if (json) headers["Content-Type"] = "application/json";
    return headers;
  };

  const NUMBER_FIELDS = [
    ["temperature", "Temperature", "0 ~ 1，留空用模型默认值", { min: 0, max: 1, step: 0.05 }],
    ["max_tokens", "最大输出 Tokens", "留空用默认值", { min: 256, max: 64000, step: 256 }],
  ];

  async function load() {
    const response = await fetch("/api/settings", { headers: authHeaders() });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }

  function mount(container, options = {}) {
    container.replaceChildren(el("div", "llm-set-status", "正在读取模型设置…"));
    load().then(settings => render(container, settings, options))
      .catch(error => {
        container.replaceChildren(
          el("div", "llm-set-status err", `读取模型设置失败：${error.message}`));
      });
  }

  function render(container, settings, options) {
    container.replaceChildren();
    const editable = Boolean(settings.editable);
    // API Key 比模型/参数高一级：前者是机密，只给管理员；后者是日常运维，工艺经理就能调。
    const secretsEditable = Boolean(settings.secrets_editable);
    const status = el("div", "llm-set-status", editable
      ? (secretsEditable
          ? "改动即时保存，对全平台所有任务生效。"
          : "模型与参数改动即时保存，对全平台所有任务生效；API Key 需系统管理员修改。")
      : "只有工艺经理或系统管理员可以修改；此处仅供查看当前配置。");

    async function save(patch) {
      status.textContent = "保存中…";
      status.classList.remove("err");
      try {
        const response = await fetch("/api/settings", {
          method: "PUT", headers: authHeaders(true), body: JSON.stringify(patch),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        Object.assign(settings, data);
        status.textContent = data.message || "已保存，全局生效";
        options.onSaved?.(data);
        return true;
      } catch (error) {
        status.textContent = `保存失败：${error.message}`;
        status.classList.add("err");
        return false;
      }
    }

    // 唯一模型字段：直接对应报价的 model，切换到哪个 provider 就展示哪家的 Key 状态。
    const modelRow = el("div", "llm-set-row");
    modelRow.append(el("label", "llm-set-label", "模型"));
    const select = el("select", "llm-set-input");
    const options_list = settings.options || [];
    if (!options_list.length) {
      select.append(el("option", null, "无可选模型"));
      select.disabled = true;
    } else {
      options_list.forEach(model => {
        const option = el("option", null, model.label || model.id);
        option.value = model.id;
        if (model.id === settings.model) option.selected = true;
        select.append(option);
      });
      select.disabled = !editable;
      select.onchange = async () => {
        // 换模型会换 provider，Key 行要跟着刷新，所以保存成功后整块重绘。
        if (await save({ model: select.value })) mount(container, options);
      };
    }
    modelRow.append(select);
    modelRow.append(el("div", "llm-set-hint",
      "图纸解析、文档分析、工艺推荐、成本测算与 Agent 对话都用这一个模型；"
      + "必须支持图像，否则图纸解析会明确报错而不是偷偷换模型。"));
    container.append(modelRow);

    NUMBER_FIELDS.forEach(([key, label, hint, range]) => {
      const row = el("div", "llm-set-row");
      row.append(el("label", "llm-set-label", label));
      const field = el("input", "llm-set-input");
      field.type = "number";
      Object.assign(field, range);
      field.value = settings[key] == null ? "" : settings[key];
      field.disabled = !editable;
      // 用 change 而不是 input：每敲一个数字就发一次请求毫无必要。
      field.onchange = () => save({ [key]: field.value === "" ? null : Number(field.value) });
      row.append(field);
      row.append(el("div", "llm-set-hint", hint));
      container.append(row);
    });

    const thinkingRow = el("label", "llm-set-check");
    const thinking = el("input");
    thinking.type = "checkbox";
    thinking.checked = Boolean(settings.thinking);
    thinking.disabled = !editable;
    thinking.onchange = () => save({ thinking: thinking.checked });
    thinkingRow.append(thinking, document.createTextNode("开启深度思考"));
    container.append(thinkingRow);

    (settings.keys || []).forEach(item => {
      container.append(secretRow(item, secretsEditable, save));
    });
    container.append(status);
  }

  function secretRow(provider, editable, save) {
    const row = el("div", "llm-set-row");
    row.append(el("label", "llm-set-label", `${provider.label} API Key`));
    row.append(el("div", "llm-set-hint",
      (provider.configured ? `当前：${provider.hint || "已配置"}` : "尚未配置")
      + (provider.base_url ? ` · 网关 ${provider.base_url}` : "")));
    const input = el("input", "llm-set-input");
    input.type = "password";
    input.autocomplete = "new-password";
    input.placeholder = "留空则不修改";
    input.disabled = !editable;
    row.append(input);
    const apply = el("button", "llm-set-btn", "更新");
    apply.type = "button";
    apply.disabled = !editable;
    apply.onclick = async () => {
      const value = input.value.trim();
      if (!value) return;
      // 保存成功立刻清空：密钥不该留在 DOM 里等着被截图或被自动填充读走。
      if (await save({ api_key: value, api_key_provider: provider.provider })) input.value = "";
    };
    row.append(apply);
    row.append(el("div", "llm-set-hint", "保存后不会再显示明文。"));
    return row;
  }

  window.LlmSettingsPanel = { mount, load, render };
})();
