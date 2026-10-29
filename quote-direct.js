/* 提示词准备 → 普通报价原下一步按钮 → 持久化六步。金额只由服务器计算。 */
(function () {
  'use strict';
  const host = window.CpqDirectHost;
  if (!host) return;
  const path = '/wf/card/direct-quote';
  const titles = ['确认需求配置', '报价成本确认', '定价-利润加成', '报价-其他加价项', '报价方案', '输出报价单'];
  let state = null, session = null, pending = false, viewing = 1, form = null;
  const entry = document.getElementById('qaDirectQuote');
  const panel = document.createElement('section');
  panel.id = 'directQuotePanel';
  panel.setAttribute('aria-label', '本单直接报价');
  panel.style.cssText = 'padding:20px;max-width:850px;margin:auto';
  const error = document.createElement('p');
  error.setAttribute('role', 'alert'); error.style.color = '#b42318';
  function el(tag, text, parent) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (parent) parent.appendChild(node);
    return node;
  }
  async function api(action, extra) {
    const sid = host.session();
    const options = action ? {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(Object.assign({session_id: sid, action}, extra || {}))} : {};
    const response = await cpqAuthFetch(action ? path : path + '?session_id=' + encodeURIComponent(sid), options);
    const result = await response.json();
    if (!response.ok || !result.ok) throw Error(result.error || '直接报价请求失败');
    if (sid !== host.session()) throw Error('报价会话已切换，请重新打开');
    session = sid; state = result.state;
    return result;
  }
  function field(name, label, value, type, readonly) {
    const row = el('label', label, form);
    row.style.cssText = 'display:block;margin:12px 0;font-size:14px';
    const input = el(type === 'textarea' ? 'textarea' : 'input', undefined, row);
    input.name = name;
    if (type !== 'textarea') input.type = type || 'text';
    input.style.cssText = 'display:block;width:100%;box-sizing:border-box;padding:9px;border:1px solid #cbd5e1;border-radius:6px;margin-top:5px';
    input.required = name !== 'product_code'; input.readOnly = readonly;
    input.value = value == null ? '' : value;
    if (type === 'number') { input.step = 'any'; input.min = '0'; }
    if (type === 'textarea') input.rows = 5;
    return input;
  }
  function summary(parent) {
    if (!state.quote) return;
    const quote = state.quote;
    if (quote.demo) el('p', '演示报价，不用于正式交易', parent);
    const table = el('table', undefined, parent);
    table.style.cssText = 'width:100%;border-collapse:collapse';
    [['product_name','产品'],['specification','规格材料'],['quantity','数量'],['currency','币种'],
      ['net_unit_price','未税单价'],['net_total','未税合计'],['tax_percent','税率（%）'],
      ['tax_amount','税额'],['gross_total','含税合计']].forEach(([key,label]) => {
      const row = el('tr', undefined, table);
      el('th', label, row).style.cssText = 'text-align:left;padding:8px;vertical-align:top';
      el('td', String(quote[key]), row).style.cssText = 'padding:8px;white-space:pre-wrap';
    });
  }
  async function download() {
    try {
      const response = await cpqAuthFetch(path + '/document?session_id=' + encodeURIComponent(host.session()));
      if (!response.ok) { const result = await response.json(); throw Error(result.error || '导出失败'); }
      const url = URL.createObjectURL(await response.blob());
      const a = el('a', undefined, document.body); a.href = url; a.download = '报价单.docx'; a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { error.textContent = e.message; }
  }
  function view(step) {
    if (!state || session !== host.session()) return;
    const current = Math.min(state.next_step, 6);
    viewing = Math.max(1, Math.min(Number(step) || current, current));
    const confirmed = viewing < state.next_step;
    const readonly = confirmed || !!state.auto_pricing || !!(host.readonly && host.readonly());
    panel.replaceChildren(); error.textContent = ''; form = null;
    el('h2', state.next_step === 7 && viewing === 6 ? '六步报价已完成' : '第 ' + viewing + ' 步 · ' + titles[viewing - 1], panel);
    panel.appendChild(error);
    if (confirmed) el('p', '本步已确认，以下为已保存内容。', panel);
    if (state.auto_pricing) {
      el('p', '客户规格已识别；成本、毛利率、税率和加价由系统有效记录提供，不需要客户补填。', panel);
      if (state.pricing_gaps && state.pricing_gaps.length) {
        el('p', '系统报价依据尚未齐备，需要内部维护：', panel);
        const list = el('ul', undefined, panel);
        state.pricing_gaps.forEach(gap => el('li', gap, list));
        if (viewing >= 2) {
          el('p', '客户需求已保存。内部依据补齐前，不能完成成本确认或输出报价单。', panel);
          host.mount(panel, current, viewing, false, titles[viewing - 1], true);
          return;
        }
      }
    }
    form = el('form', undefined, panel);
    const data = Object.assign({}, viewing === 1 ? host.seed() : {},
      (state.prompt_inputs || {})[String(viewing)] || {}, state.steps[String(viewing)] || {});
    if (viewing === 1) {
      [['product_name','产品名称'],['product_code','产品编码（可选）'],['customer','客户'],
        ['specification','规格与材料要求'],['quantity','数量']].forEach(([key,label]) => {
        const input = field(key, label, data[key], key === 'specification' ? 'textarea' : key === 'quantity' ? 'number' : 'text', readonly);
        if (key === 'quantity') { input.step = '1'; input.min = '1'; }
      });
    } else if (viewing === 2) {
      el('p', '核对每件未税成本、来源及用途。演示成本不能作为正式交易依据。', form);
      field('unit_cost', '每件未税成本', data.unit_cost, 'number', readonly);
      field('currency', '币种（三位代码）', data.currency || 'CNY', 'text', readonly);
      field('cost_source', '成本来源 / 核实依据', data.cost_source, 'textarea', readonly);
      const label = el('label', '成本用途 ', form);
      const select = el('select', undefined, label); select.name = 'cost_usage'; select.required = true; select.disabled = readonly;
      [['','请选择'],['actual','实际核实成本'],['demo','演示成本（报价单将标记演示）']].forEach(([value,text]) => {
        const option = el('option', text, select); option.value = value;
      });
      select.value = data.demo === true ? 'demo' : data.demo === false ? 'actual' : '';
      const confirm = el('label', undefined, form); confirm.style.cssText = 'display:block;margin:16px 0';
      const check = el('input', undefined, confirm); check.type = 'checkbox'; check.name = 'confirmed'; check.required = true;
      check.checked = data.confirmed === true; check.disabled = readonly;
      confirm.appendChild(document.createTextNode('我已核对成本、来源及用途，并确认采用'));
    } else if (viewing === 3) {
      field('margin_percent', '目标毛利率（%，成本 ÷ (1 − 毛利率)）', data.margin_percent, 'number', readonly).max = '99.9999';
    } else if (viewing === 4) {
      field('addon_per_unit', '每件未税其他加价（无加价请填0）', data.addon_per_unit, 'number', readonly);
      field('tax_percent', '税率（%，免税请填0）', data.tax_percent, 'number', readonly).max = '100';
    } else {
      summary(form);
      if (!readonly) el('p', viewing === 5 ? '核对方案后点击页面下方的下一步。' : '确认输出后可下载已保存报价单。', form);
    }
    form.onsubmit = event => { event.preventDefault(); next(); };
    if (state.next_step === 7 && viewing === 6) {
      const button = el('button', '下载报价单（Word）', panel); button.onclick = download;
    }
    host.mount(panel, current, viewing, state.next_step === 7, titles[viewing - 1]);
  }
  async function next() {
    if (pending || !state || session !== host.session() || state.next_step > 6 || (host.readonly && host.readonly())) return false;
    if (viewing !== state.next_step) { view(state.next_step); return false; }
    if (!form || !form.reportValidity()) return false;
    pending = true; host.lock(true); error.textContent = '';
    const step = state.next_step;
    const values = Object.fromEntries(new FormData(form));
    if (step === 2) {
      values.confirmed = form.elements.confirmed.checked;
      values.demo = state.auto_pricing ? state.prompt_inputs['2'].demo : values.cost_usage === 'demo';
    }
    try {
      await api('complete', {step, data: values});
      view(); await host.refresh();
      host.say('第' + step + '步已确认' + (state.next_step === 7 ? '，六步报价已完成，可下载报价单。' :
        state.auto_pricing && state.pricing_gaps.length ? '，系统报价依据未齐备，暂不能继续成本确认。' : '，请核对第' + state.next_step + '步并继续。'));
      return true;
    } catch (e) { error.textContent = e.message; return false; }
    finally { pending = false; host.lock(false); }
  }
  async function restore() { await api(); if (state) view(); }
  async function fromPrompt(prompt) {
    if (pending) return false;
    pending = true; host.lock(true);
    try {
      await api('prepare', {data: {prompt}});
      view(); await host.refresh();
      host.say(state.auto_pricing ? '已识别客户规格并检查系统报价依据，请核对右侧客户需求后点下一步。' :
        '已读取完整直接报价参数。请核对右侧第1步，然后使用原来的“下一步”逐步完成六步。');
      return true;
    } catch (e) {
      host.say((prompt.includes('【直接报价参数】') ? '直接报价参数未通过：' : '客户需求暂不能继续：') + e.message);
      return false;
    }
    finally { pending = false; host.lock(false); }
  }
  async function open() {
    if (pending) return;
    try {
      if (!host.user()) throw Error('请先登录销售经理账号');
      if (!host.session()) throw Error('请先创建普通报价');
      if (host.busy()) throw Error('请等待当前对话完成后再打开直接报价');
      await api(); if (!state) await api('enable');
      view(); await host.refresh();
    } catch (e) { window.alert(e.message); }
  }
  if (entry) entry.onclick = open;
  document.addEventListener('cpq-direct-state', event => {
    if (session && session !== event.detail.session) { state = null; session = null; panel.remove(); }
    if (entry) entry.textContent = event.detail.enabled ? '继续本单直接报价' : '本单直接报价';
  });
  document.addEventListener('cpq-auth-change', () => { state = null; session = null; panel.remove(); });
  function hasPrompt(text) {
    if (typeof text !== 'string') return false;
    if (text.includes('【直接报价参数】') || text.includes('【/直接报价参数】')) return true;
    const intent = text.replace(/不(?:转|经过)(?:技术工艺|工艺经理)|无需转(?:技术工艺|工艺经理)/g, '');
    if (/(?:转交|转|交给)(?:技术工艺|工艺经理)/.test(intent)) return false;
    return /酒盒|礼盒|包装盒/.test(text) && /尺寸\s*[:：]?/.test(text) && /盖面纸/.test(text);
  }
  window.CpqDirect = {open, next, view, restore, fromPrompt, download, hasPrompt};
})();
