/* 全局模型设置面板 —— 报价、配置、规则、技术工艺四个入口共用这一份实现。
 *
 * 唯一事实源是报价的 cpq_settings.json，唯一读写接口是 /api/settings：
 * 模型只有一个字段（图纸解析、文档分析、工艺推荐、成本测算和 Agent 对话都用它），
 * 不再有"助手模型 / 技术工艺模型"两套页签，也不再保留任何技术工艺专属设置接口。
 *
 * 字段：模型、Temperature、最大输出 Tokens、深度思考、当前 provider 的 API Key。
 * API Key 只写不读：接口回的是 configured / 打码提示，输入框保存成功后立即清空。
 *
 * 两层粒度（本批新增）：上面一块是「平台默认（不设置时使用）」，下面一块是
 * 「我的模型与密钥」—— 每个登录账号能给自己选一个模型、配一把自己的 Key，不设置
 * 就回落平台默认。账号级只能改 model 与 api_key 两项（推理参数仍是全局）。报价三端
 * 没有 /api/my/settings（404/405），那块整段隐藏，不报错。
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

  // 「我的模型与密钥」只有技术工艺后端提供；报价 / 配置 / 规则三端没有这个接口，
  // 返回 404 / 405 时整块隐藏（报价三端共用这一份面板）。
  async function loadMine() {
    try {
      const response = await fetch("/api/my/settings", { headers: authHeaders() });
      if (response.status === 404 || response.status === 405) return null;
      const data = await response.json().catch(() => ({}));
      if (!response.ok) return null;
      return data;
    } catch (error) {
      return null;
    }
  }

  function mount(container, options = {}) {
    container.replaceChildren(el("div", "llm-set-status", "正在读取模型设置…"));
    Promise.all([load(), loadMine()])
      .then(([settings, mine]) => render(container, settings, options, mine))
      .catch(error => {
        container.replaceChildren(
          el("div", "llm-set-status err", `读取模型设置失败：${error.message}`));
      });
  }

  function render(container, settings, options, mine) {
    container.replaceChildren();
    if (mine) container.append(mineSection(container, settings, mine, options));
    container.append(el("div", "llm-set-title", "平台默认（不设置时使用）"));
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
    const effectiveLine = String(settings.effective_model || "").trim();
    modelRow.append(el("div", "llm-set-hint",
      "图纸解析、文档分析、工艺推荐、成本测算与 Agent 对话都用这一个模型；"
      + "必须支持图像，否则图纸解析会明确报错而不是偷偷换模型。"
      + (effectiveLine && settings.effective_source !== "account"
          ? ` 当前生效：${effectiveLine}（平台默认）。` : "")));
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

  /* ------------------------------------------------------------ 我的模型与密钥
   * 每个登录账号管自己那一层：一个模型 + 各 provider 的个人 Key。不设置就回落上面
   * 的「平台默认」，所以这里的空选项写的是"跟随平台默认（不设置）"。
   * 写接口只有 /api/my/settings 与 DELETE /api/my/settings/keys/{provider}，
   * 一律把发起账号留给后端从 current_user 取 —— 面板不把用户名放进请求体。 */
  function mineSection(container, settings, mine, options) {
    const section = el("div", "llm-set-section");
    section.append(el("div", "llm-set-title", "我的模型与密钥"));
    const isMine = mine.effective_source === "account";
    const status = el("div", "llm-set-status", "");

    async function save(patch) {
      status.textContent = "保存中…";
      status.classList.remove("err");
      try {
        const response = await fetch("/api/my/settings", {
          method: "PUT", headers: authHeaders(true), body: JSON.stringify(patch),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        status.textContent = data.message || "已保存，只对本账号生效";
        options.onSaved?.(data);
        return true;
      } catch (error) {
        status.textContent = `保存失败：${error.message}`;
        status.classList.add("err");
        return false;
      }
    }

    async function removeKey(provider) {
      status.textContent = "删除中…";
      status.classList.remove("err");
      try {
        const response = await fetch(
          `/api/my/settings/keys/${encodeURIComponent(provider)}`,
          { method: "DELETE", headers: authHeaders() });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        return true;
      } catch (error) {
        status.textContent = `删除失败：${error.message}`;
        status.classList.add("err");
        return false;
      }
    }

    const effective = String(mine.effective_model || "").trim();
    const effectiveLine = el("div", "llm-set-hint",
      effective
        ? `当前生效：${effective}（来源：${isMine ? "我的" : "平台默认"}）`
        : "当前生效模型尚未配置，请先设置平台默认或我的模型。");
    if (isMine) effectiveLine.append(el("span", "llm-set-chip", "我的"));
    section.append(effectiveLine);

    const modelRow = el("div", "llm-set-row");
    modelRow.append(el("label", "llm-set-label", "我的模型"));
    const select = el("select", "llm-set-input");
    const follow = el("option", null, "跟随平台默认（不设置）");
    follow.value = "";
    select.append(follow);
    (settings.options || []).forEach(model => {
      const option = el("option", null, model.label || model.id);
      option.value = model.id;
      if (model.id === mine.model) option.selected = true;
      select.append(option);
    });
    select.onchange = async () => {
      // 换模型会连带动用的 Key 一起换，保存成功后整块重绘。
      if (await save({ model: select.value })) mount(container, options);
    };
    modelRow.append(select);
    modelRow.append(el("div", "llm-set-hint",
      "只影响当前账号；清空即「跟随平台默认」。Temperature、最大 Tokens、深度思考仍是"
      + "平台默认，不随账号变。"));
    section.append(modelRow);

    (mine.providers || settings.keys || []).forEach(item => {
      section.append(mineSecretRow(item, save, removeKey, () => mount(container, options)));
    });
    section.append(status);
    return section;
  }

  function mineSecretRow(provider, save, removeKey, refresh) {
    const row = el("div", "llm-set-row");
    row.append(el("label", "llm-set-label", `${provider.label} API Key`));
    row.append(el("div", "llm-set-hint",
      (provider.configured
        ? `我的 Key 已配置：${provider.hint || "已配置"}`
        : "我的 Key 未配置，使用平台默认 Key")
      + (provider.base_url ? ` · 网关 ${provider.base_url}` : "")));
    const input = el("input", "llm-set-input");
    input.type = "password";
    input.autocomplete = "new-password";
    input.placeholder = "留空则不修改";
    row.append(input);
    const apply = el("button", "llm-set-btn", "保存我的 Key");
    apply.type = "button";
    apply.onclick = async () => {
      const value = input.value.trim();
      if (!value) return;
      // 保存成功立刻清空：密钥不该留在 DOM 里等着被截图或被自动填充读走。
      if (await save({ api_key: value, api_key_provider: provider.provider })) input.value = "";
    };
    row.append(apply);
    if (provider.configured) {
      const clear = el("button", "llm-set-btn", "删除我的 Key（改用平台默认）");
      clear.type = "button";
      clear.onclick = async () => {
        if (await removeKey(provider.provider)) refresh();
      };
      row.append(clear);
    }
    row.append(el("div", "llm-set-hint", "保存后不会再显示明文。"));
    return row;
  }

  window.LlmSettingsPanel = { mount, load, render };
})();
