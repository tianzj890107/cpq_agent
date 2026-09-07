/* 【CPQ 定制 · 本文件不来自 process_drawing，同步上游时保留】
 *
 * 报价成品参数表 —— 2.2「参数推荐」与 2.3「整合参数」共用的一张表。
 *
 * 它原来长在 2.2 里；成本测算拆成 2.3 之后，「整合参数」这个收口环节跟着搬了过去
 * （报价必填项要在**出成本、发报价之前**补齐，那是财务那一步的事）。两页都要画同一张
 * 表，所以抽到这里 —— 复制一份的话，DA 字典改了就会有一页悄悄跟不上。
 *
 * 调用方要提供三个全局函数：esc（转义）、以及自己的 aiAttr 等价物由本模块内建。
 */
(function () {
  'use strict';

  const attr = value => esc(value).replace(/"/g, '&quot;');

  /* 「成品编码写进主数据了没有」—— 两页各自持有自己的 payload（2.2 是 aiData，
     2.3 是 crData），所以由调用方在渲染前用 setWritten() 告诉本模块，模块不去猜
     页面上有哪个全局变量。没告诉就退回 2.2 的 aiData（那页一直是这么用的）。 */
  let written = null;
  const setWritten = value => { written = value === undefined ? null : Boolean(value); };
  const writtenToMaster = () => (written !== null ? written
    : (typeof aiData !== 'undefined' && Boolean(aiData?.status?.has_material_code)));

  /*
   * 报价成品参数表。这张表的**行是字典定的**（亿纬锂能 DA 梳理 · 产品技术参数），
   * 不是模型输出多少就显示多少 —— 报价那头按字段取数，少一项就是报价单上一个空格，
   * 只列"模型给了什么"的话，缺了哪几项永远看不出来。
   */
  function card(checklist, editing) {
    if (!checklist || !checklist.groups.length) {
      return `<section class="inline-card"><div class="inline-card-title">报价成品参数</div>`
        + `<div class="inline-hint">未加载到报价成品参数字典（agent_knowledge/rules/quote_product_params.json），`
        + `本次只展示模型自由给出的参数。</div></section>`;
    }
    const stat = checklist.summary;
    const shortfall = stat.required_total - stat.required_filled;
    let html = `<section class="inline-card"><div class="inline-card-title">报价成品参数 · ${esc(checklist.family_name)}</div>`
      + `<div class="inline-hint">来自亿纬锂能 DA 梳理（报价助手 · 产品技术参数）。`
      + `报价测算单上的成品行按这些字段取数。</div>`
      + `<div class="inline-totals"><span><strong>${stat.filled}</strong>/${stat.total} 已给出</span>`
      + `<span><strong>${stat.required_filled}</strong>/${stat.required_total} 报价必填</span></div>`;
    if (!stat.filled) {
      html += `<div class="inline-warn">⚠ 本次模型一条参数都没给出。下表的行是报价字典定的，`
        + `<b>可以直接在「值」列里逐项填写</b>，填完点上方「保存参数」；`
        + `也可以补充整合需求或上传更清楚的图纸后重新生成。</div>`;
    } else if (shortfall > 0) {
      html += `<div class="inline-warn">⚠ 还有 ${shortfall} 项报价必填参数没有值，`
        + `这台成品现在进不了报价测算单。可以直接在下表「值」列里补，或补充需求后重新生成。</div>`;
    }
    const mismatched = checklist.groups.flatMap(group => group.fields).filter(field => field.unit_mismatch);
    if (mismatched.length) {
      html += `<div class="inline-warn">⚠ ${mismatched.length} 项的单位与字典不一致，数值需要换算后再改：`
        + mismatched.map(field => `${esc(field.name)}（写的是 ${esc(field.given_unit)}，字段单位 ${esc(field.unit)}）`).join('、')
        + `。平台不会替你换算 —— 直接改单位会把数值改错一个量级。</div>`;
    }
    html += `<div class="inline-cost-table-wrap"><table class="inline-cost-table"><thead><tr>`
      + `<th>字段编码</th><th>参数</th><th>值</th><th>单位</th><th>依据</th><th>来源</th>`
      + `</tr></thead><tbody>`;
    checklist.groups.forEach(group => {
      html += `<tr class="ai-group-row"><td colspan="6">${esc(group.name)}</td></tr>`;
      group.fields.forEach(field => { html += row(field, editing); });
    });
    return html + `</tbody></table></div></section>`;
  }

  function row(field, editing) {
    const flag = field.required && !field.filled && !field.generated ? ' ai-missing' : '';
    // 平台生成项（成品编码）不给输入框：这一格由「写入数据库」产生 92022xxx，
    // 手填一个号进去，报价那边按它去匹配规则只会匹配到不存在的产品。
    if (field.generated) {
      const label = `<code>${esc(field.code)}</code>`;
      // 没写过库却有值 = 早先被人工/模型填进来的号，主数据里并不存在。
      // 报价按成品编码匹配定价规则，拿这种号过去只会匹配到一个不存在的产品。
      const unverified = field.filled && !writtenToMaster();
      const value = field.filled
        ? `<span class="number">${esc(field.value)}</span>`
          + (unverified ? `<span class="ai-off">主数据里没有这个号，写库时会用真实编码覆盖</span>` : '')
        : `<span class="ai-blank">由系统生成（点「写入数据库」或直接发送至报价时）</span>`;
      return `<tr><td>${label}</td>`
        + `<td>${esc(field.name)}<span class="ai-req">必填</span>`
        + `<small class="ai-hint">由平台生成，不用手填</small></td>`
        + `<td>${value}</td><td>${esc(field.unit)}</td>`
        + `<td class="source">${esc(field.basis || '写入主数据时生成')}</td>`
        + `<td class="source">${esc(field.source || '')}</td></tr>`;
    }
    const label = `<code>${esc(field.code)}</code>`;
    const name = esc(field.name) + (field.required ? `<span class="ai-req">必填</span>` : '');
    const hint = field.options.length ? `取值：${esc(field.options.join(' / '))}`
      : field.example ? `示例：${esc(field.example.slice(0, 40))}` : '';
    // 校验提示在编辑态同样要给：取值越界、单位不符，正是填的时候最该看见的东西。
    // 早先只在只读态渲染，改成常驻编辑后这两条提示就整个消失了。
    const flags = (field.off_option ? `<span class="ai-off">不在字典取值内</span>` : '')
      + (field.unit_mismatch
          ? `<span class="ai-off">按 ${esc(field.given_unit)} 写的，字段单位是 ${esc(field.unit)}，请换算</span>`
          : '')
      + (field.required && !field.filled ? `<span class="ai-blank">必填未给出</span>` : '');
    if (editing) {
      return `<tr class="${flag.trim()}" data-ai-qp="${attr(field.code)}" data-ai-name="${attr(field.name)}" data-ai-unit="${attr(field.unit)}">`
        + `<td>${label}</td><td>${name}${hint ? `<small class="ai-hint">${hint}</small>` : ''}</td>`
        + `<td><input data-f="value" value="${attr(field.value)}" placeholder="${attr(field.options[0] || '')}"/>${flags}</td>`
        + `<td>${esc(field.unit)}</td>`
        + `<td><input data-f="basis" value="${attr(field.basis || '')}"/></td>`
        + `<td class="source">${esc(field.source || '')}</td></tr>`;
    }
    const value = field.filled
      ? `<span class="number">${esc(field.value)}</span>`
        + (field.off_option ? `<span class="ai-off">不在字典取值内</span>` : '')
        + (field.unit_mismatch ? `<span class="ai-off">按 ${esc(field.given_unit)} 写的，字段单位是 ${esc(field.unit)}，请换算</span>` : '')
      : `<span class="ai-blank">未给出</span>`;
    return `<tr class="${flag.trim()}"><td>${label}</td>`
      + `<td>${name}${hint && !field.filled ? `<small class="ai-hint">${hint}</small>` : ''}</td>`
      + `<td>${value}</td><td>${esc(field.unit)}</td>`
      + `<td class="source">${esc(field.basis || '')}</td>`
      + `<td class="source">${esc(field.source || '')}</td></tr>`;
  }

  /** 字典之外的参数。留一张独立的表，免得和报价字段混成一锅。 */
  function extraCard(params, checklist, editing) {
    const rows = (params.params || [])
      .map((row, index) => ({ row, index }))
      .filter(item => !item.row.param_code);
    if (!rows.length) return '';   // 没有字典外参数时不摆一张空表
    let html = `<section class="inline-card"><div class="inline-card-title">补充参数（字典之外）</div>`
      + `<div class="inline-hint">对本产品重要、但不在报价成品参数字典里的参数。`
      + `它们不会进报价测算单的成品行。</div>`
      + `<div class="inline-cost-table-wrap"><table class="inline-cost-table"><thead><tr>`
      + `<th>参数</th><th>值</th><th>单位</th><th>依据</th><th>来源</th><th>置信</th>`
      + `</tr></thead><tbody>`;
    rows.forEach(({ row, index }) => {
      if (editing) {
        html += `<tr data-ai-xp="${index}"><td><input data-f="name" value="${attr(row.name || '')}"/></td>`
          + `<td><input data-f="value" value="${attr(row.value || '')}"/></td>`
          + `<td><input data-f="unit" value="${attr(row.unit || '')}"/></td>`
          + `<td><input data-f="basis" value="${attr(row.basis || '')}"/></td>`
          + `<td><input data-f="source" value="${attr(row.source || '')}"/></td>`
          + `<td>${Math.floor(Number(row.confidence || 0) * 100)}%</td></tr>`;
      } else {
        html += `<tr><td>${esc(row.name)}</td><td class="number">${esc(row.value)}</td>`
          + `<td>${esc(row.unit || '')}</td><td class="source">${esc(row.basis || '')}</td>`
          + `<td class="source">${esc(row.source || '')}</td>`
          + `<td class="number">${Math.floor(Number(row.confidence || 0) * 100)}%</td></tr>`;
      }
    });
    return html + `</tbody></table></div></section>`;
  }

  /** 表格里逐行收集「值」。依据/来源由后端按"人工补填"统一标注。 */
  function collect() {
    const values = {};
    document.querySelectorAll('[data-ai-qp]').forEach(node => {
      const input = node.querySelector('[data-f="value"]');
      if (!input) return;
      values[node.dataset.aiQp] = { value: input.value.trim() };
    });
    return values;
  }

  /** 把智能补全的建议值填进输入框并标出来（不写库）。返回填了几项。 */
  function applyFills(fills) {
    let applied = 0;
    (fills || []).forEach(fill => {
      const node = document.querySelector(`[data-ai-qp="${CSS.escape(fill.code)}"]`);
      const input = node && node.querySelector('[data-f="value"]');
      if (!input || input.value.trim()) return;   // 已经有值的格子不覆盖
      input.value = fill.value;
      const basis = node.querySelector('[data-f="basis"]');
      if (basis && !basis.value.trim()) basis.value = fill.basis || 'AI 建议';
      node.classList.add('ai-suggested');
      const cell = input.parentElement;
      if (cell && !cell.querySelector('.ai-sug')) {
        cell.insertAdjacentHTML('beforeend',
          `<span class="ai-sug">AI 建议 · 置信度 ${Math.round((fill.confidence ?? 0.5) * 100)}%</span>`);
      }
      applied += 1;
    });
    return applied;
  }

  window.QuoteParams = { card, row, extraCard, collect, applyFills, setWritten };
})();
