/* 【CPQ 定制 · 本文件不来自 process_drawing，同步上游时保留】
 *
 * 2.3 成本测算 —— 财务经理的步骤。
 *
 * 分工：工艺经理在 2.1/2.2 出工艺、参数与用量，2.2 结束点「确认工艺并发送至财务做
 * 成本测算」把项目交过来；财务在这里逐个零件 + 整机算成本、汇总，然后选三个去向：
 * 写入数据库 / 发送至报价 / 退回工艺经理复核。
 *
 * 两条要一直记着的口径：
 *   · **本步不联网**。只用企业成本库里已有的物料价、费率、系数，加模型的工程经验。
 *     库里没有的写进待确认让财务去询价 —— 一个"网上搜来的"单价，报价那头没法追溯。
 *   · **对外用整机成本，不是零件之和**。整机成本的材料项就是逐个零件引过来的，
 *     两者相加会把零件成本算两遍。界面上两个数都给，但送出去的只有整机那个。
 */
const crPid = new URLSearchParams(location.search).get('project')
  || localStorage.getItem('cad_engine_project_id') || '';
const CR_TABS = { parts: '零件成本', assembly: '组装成本', total: '汇总', params: '整合参数' };
const CR_COST_LABEL = { material: '材料', labor: '人工', overhead: '制造费用', machining: '加工费用' };

let crData = null;
let crTab = 'parts';
let crBusy = false;
/* 2.3 是财务经理的步骤。别人（工艺经理、销售）能打开这一页看数，但不能算、不能发。
   后端才是权威（main.py 的 COST_ROLES），这里只是提前说清楚 —— 否则要等点下去
   才收到 403，还容易被读成"系统坏了"。取不到身份时不拦：让后端去判。 */
let crUser = null;
const CR_COST_ROLES = ['finance_mgr', 'admin'];
const crReadOnly = () =>
  Boolean(crUser && crUser.role_code) && !CR_COST_ROLES.includes(crUser.role_code);
const crReadOnlyWhy = () =>
  `2.3 成本测算是财务经理的步骤；当前登录的是「${crUser && crUser.role_name || '其他角色'}」，`
  + '这一页只能查看。请用财务经理账号登录后测算。';

const $cr = id => document.getElementById(id);
const crUrl = (suffix = '') => `/api/projects/${encodeURIComponent(crPid)}/cost-review${suffix}`;
const crAttr = value => esc(value).replace(/"/g, '&quot;');
const crMoney = value => value == null ? '—'
  : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
const crSleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/* --------------------------------------------------------------- 对话区 */
function crAppend(html) {
  $cr('crEmpty')?.remove();
  const node = document.createElement('div');
  node.innerHTML = html;
  const element = node.firstElementChild;
  $cr('crTinner').append(element);
  $cr('crThread').scrollTop = $cr('crThread').scrollHeight;
  return element;
}

function crSay(text) {
  crAppend(`<div class="oc-amsg"><div class="oc-aav" aria-hidden="true">¥</div>
    <div class="oc-abody"><div class="oc-atxt">${esc(text)}</div></div></div>`);
}

function crCard(title) {
  const card = crAppend(`<div class="oc-amsg"><div class="oc-aav" aria-hidden="true">¥</div>
    <div class="oc-abody"><div class="oc-process-card">
      <div class="oc-process-head"><span class="oc-process-title">${esc(title)}</span>
        <span class="oc-process-state">进行中</span></div>
      <div class="oc-process-steps"></div></div></div></div>`);
  const steps = card.querySelector('.oc-process-steps');
  const seen = new Set();
  return {
    log(lines) {
      for (const line of lines) {
        if (seen.has(line)) continue;
        seen.add(line);
        const sub = line.startsWith('  ');
        steps.insertAdjacentHTML('beforeend',
          `<div class="oc-process-step${sub ? ' sub' : ''}"><span class="oc-process-dot">${sub ? '·' : '●'}</span>`
          + `<span class="oc-process-text">${esc(line.trim())}</span></div>`);
      }
      $cr('crThread').scrollTop = $cr('crThread').scrollHeight;
    },
    done(ok, message) {
      const state = card.querySelector('.oc-process-state');
      state.className = `oc-process-state ${ok ? 'ok' : 'err'}`;
      state.textContent = ok ? '已完成' : '失败';
      if (message) this.log([`  ${message}`]);
    },
  };
}

function crStatus(message, error = false) {
  const box = $cr('crStatus');
  box.textContent = message || '';
  box.classList.toggle('error', Boolean(error));
}

function crToast(message, error = false) {
  const node = document.createElement('div');
  node.className = `ai-toast${error ? ' error' : ''}`;
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), error ? 4200 : 2400);
}

/* --------------------------------------------------------------- 任务轮询 */
async function crPollTask(taskId, card) {
  for (;;) {
    await crSleep(1200);
    const task = await api(`/api/projects/${encodeURIComponent(crPid)}/tasks/${encodeURIComponent(taskId)}`);
    card.log(Array.isArray(task.progress_log) ? task.progress_log : []);
    if (task.status === 'succeeded') return task.result;
    if (task.status === 'failed') throw new Error(task.error || '任务失败');
  }
}

/* --------------------------------------------------------------- 渲染 */
function crBreakdownRow(breakdown) {
  if (!breakdown || !Object.keys(breakdown).length) return '<span class="ai-blank">未测算</span>';
  return Object.keys(CR_COST_LABEL)
    .map(key => `<span class="cr-part"><i>${CR_COST_LABEL[key]}</i>${crMoney(breakdown[key])}</span>`)
    .join('');
}

function crRenderParts() {
  const rows = crData?.parts || [];
  if (!rows.length) {
    return `<div class="inline-empty">还没有零件清单。2.3 的零件成本按 2.1 已确认的零件逐件算，`
      + `请先让工艺经理完成图纸解析。</div>`;
  }
  const counts = crData.counts || {};
  let html = `<section class="inline-card"><div class="inline-card-title">零件成本（2.1 拆出来的每个零件）</div>`
    + `<div class="inline-hint">按企业成本库的物料价与费率算，<strong>不联网查行情</strong>；`
    + `库里没有的会写进待确认，请人工询价后在这里补。</div>`
    + `<div class="inline-totals"><span><strong>${counts.parts_costed || 0}</strong>/${counts.parts || 0} 已测算</span>`
    + `<span>零件成本合计 <strong>${crMoney((crData.parts_total || {}).total)}</strong> 元/台</span></div>`;
  if ((counts.missing || []).length) {
    html += `<div class="inline-warn">⚠ 还有 ${counts.missing.length} 个零件没算：`
      + `${esc(counts.missing.join('、'))}。整机成本以它们为底，缺一件整机就是偏的。</div>`;
  }
  if ((counts.zero || []).length) {
    html += `<div class="inline-warn">⚠ 这些行算出来是 0 元：${esc(counts.zero.join('、'))}。`
      + `多半是模型没给出材料明细，请重算或人工补 —— 0 元送到报价那头就是没有成本的产品。</div>`;
  }
  html += `<div class="inline-cost-table-wrap"><table class="inline-cost-table"><thead><tr>`
    + `<th>零件</th><th>单台用量</th><th>四项拆解</th><th>单件</th><th>小计</th><th>操作</th>`
    + `</tr></thead><tbody>`;
  rows.forEach(row => {
    const flag = !row.has_cost ? ' ai-missing' : row.unit_cost <= 0 ? ' ai-missing' : '';
    html += `<tr class="${flag.trim()}"><td><code>${esc(row.id)}</code> ${esc(row.name)}`
      + (row.open_questions ? `<small class="ai-hint">${row.open_questions} 项待确认</small>` : '')
      + `</td><td>×${row.quantity}</td>`
      + `<td class="cr-breakdown">${crBreakdownRow(row.breakdown)}</td>`
      + `<td><span class="number">${crMoney(row.unit_cost)}</span></td>`
      + `<td><span class="number">${crMoney(row.subtotal)}</span></td>`
      + `<td><button type="button" class="inline-action" data-cr-part="${crAttr(row.id)}"`
      + ` data-cr-qty="${row.quantity}" ${crBusy ? 'disabled' : ''}>`
      + `${row.has_cost ? '重算' : '测算'}</button></td></tr>`;
  });
  return html + `</tbody></table></div></section>`;
}

function crRenderAssembly() {
  const row = crData?.assembly;
  if (!row) return `<div class="inline-empty">还没有整机方案。</div>`;
  let html = `<section class="inline-card"><div class="inline-card-title">组装成本 · ${esc(row.name)}</div>`
    + `<div class="inline-hint">整机成本的材料项就是<strong>逐个零件引过来的</strong>，再加组装工序的人工与费用。`
    + `所以它<strong>已经包含</strong>零件成本，对外报价用的是这个数。</div>`;
  if (!row.has_cost) {
    html += `<div class="inline-warn">⚠ 还没算过整机成本。零件先算齐再算它，材料项才引得到。</div>`;
  } else if (row.unit_cost <= 0) {
    html += `<div class="inline-warn">⚠ 整机成本算出来是 0 元，多半是模型没给出材料明细，请重算。</div>`;
  }
  html += `<div class="inline-totals">${crBreakdownRow(row.breakdown)}`
    + `<span class="total">合计 <strong>${crMoney(row.unit_cost)}</strong> 元/台</span></div>`;
  if (row.summary) html += `<div class="inline-row"><b>成本构成</b>${esc(row.summary)}</div>`;
  if (row.open_questions) {
    html += `<div class="inline-row"><b>待确认</b>${row.open_questions} 项（库内缺价，需询价）</div>`;
  }
  return html + `</section>`;
}

function crRenderTotal() {
  const final = crData?.final || {};
  const partsTotal = crData?.parts_total || {};
  const counts = crData?.counts || {};
  let html = `<section class="inline-card"><div class="inline-card-title">汇总</div>`
    + `<div class="inline-hint">两个数回答的不是同一个问题，<strong>不要相加</strong>：`
    + `零件合计是"料工费加起来多少"；整机成本已含零件成本，是对外的口径。</div>`
    + `<div class="inline-cost-table-wrap"><table class="inline-cost-table"><thead><tr>`
    + `<th>口径</th><th>材料</th><th>人工</th><th>制造费用</th><th>加工费用</th><th>合计</th>`
    + `</tr></thead><tbody>`
    + `<tr><td>零件成本合计（${counts.parts_costed || 0}/${counts.parts || 0} 件）</td>`
    + ['material', 'labor', 'overhead', 'machining', 'total']
        .map(key => `<td><span class="number">${crMoney(partsTotal[key])}</span></td>`).join('')
    + `</tr><tr class="cr-final"><td><b>整机成本（对外口径）</b></td>`
    + ['material', 'labor', 'overhead', 'machining', 'total']
        .map(key => `<td><span class="number">${crMoney(final[key])}</span></td>`).join('')
    + `</tr></tbody></table></div>`;
  const coefficients = final.coefficients || {};
  if (coefficients.labor) {
    html += `<div class="inline-hint">口径：材料逐项累加；人工/制费/加工由材料成本按固定系数推导`
      + `（材料 ÷ 1.13 ÷ 0.791 × ((1-0.791) × 0.3556 / 0.1778 / 0.0944)`
      + ` = 材料 × ${coefficients.labor} / ${coefficients.overhead} / ${coefficients.machining}）。`
      + `零件与整机同一套口径。</div>`;
  }
  html += `</section>`;

  const review = crData?.review || {};
  html += `<section class="inline-card"><div class="inline-card-title">本步状态</div>`;
  if (review.received_from) {
    html += `<div class="inline-row"><b>来自</b>${esc(review.received_from)}`
      + ` · ${esc(review.received_at || '')}</div>`;
  }
  html += `<div class="inline-row"><b>确认</b>`
    + (review.confirmed ? `已确认 · ${esc(review.confirmed_by || '')} · ${esc(review.confirmed_at || '')}`
                        : '未确认')
    + `</div>`;
  (review.actions || []).forEach(action => {
    html += `<div class="inline-row"><b>${esc(action.label)}</b>${esc(action.detail)}`
      + ` · ${esc(action.by || '')} · ${esc(action.at || '')}</div>`;
  });
  return html + `</section>`;
}

/* --------------------------------------------------------------- 面板：整合参数
 * 从 2.2 搬过来的收口环节。放在 2.3 是因为它服务的是**出成本、发报价**这件事：
 * 报价测算单按 DA 字段取数，必填缺一格，那边就是一格空白。既然发报价已经是财务的
 * 动作，补齐参数自然也归他。
 */
function crRenderParams() {
  const checklist = crData?.param_checklist;
  const review = crData?.review || {};
  if (!checklist) {
    return `<div class="inline-empty">还没有整机参数。请先让工艺经理在 2.2 完成参数推荐，`
      + `再回到这一步把报价要的字段补齐。</div>`;
  }
  const stat = checklist.summary;
  const missing = crData?.required_missing || 0;
  let html = `<section class="inline-card"><div class="inline-card-title">交给报价前的收口</div>`
    + `<div class="inline-hint">报价测算单上的成品行按下面这些 DA 字段取数。`
    + `确认之后，「发送至报价」会把它们连同成品编码与成本一起带回报价。</div>`
    + `<div class="inline-totals"><span><strong>${stat.filled}</strong>/${stat.total} 已给出</span>`
    + `<span><strong>${stat.required_filled}</strong>/${stat.required_total} 报价必填</span>`
    + `<span class="total">${review.params_final ? '已确认' : missing ? `还缺 ${missing} 项` : '待确认'}</span></div>`;
  if (missing) {
    html += `<div class="inline-warn">⚠ 还有 ${missing} 项报价必填参数没有值。`
      + `可以直接在下表「值」列里补填，或用上方「✦ 智能补全」按 2.1 零件、已排工艺与`
      + `已算成本给建议值。填完点「保存补填」，都齐了再点「确认参数已齐」。</div>`;
  }
  html += `</section>`;
  QuoteParams.setWritten(crData?.has_material_code);
  html += QuoteParams.card(checklist, true);
  html += QuoteParams.extraCard(crData?.params_plan, checklist, false);
  return html;
}

async function crAutofill() {
  if (crBusy) return;
  crBusy = true;
  crRender();
  const card = crCard('整合参数 · 智能补全');
  crStatus('智能补全中…');
  try {
    const form = new FormData();
    const note = $cr('crNote')?.value.trim() || '';
    if (note) form.append('note', note);
    const submitted = await api(
      `/api/projects/${encodeURIComponent(crPid)}/integration/params/autofill`,
      { method: 'POST', body: form });
    const result = await crPollTask(submitted.task_id, card);
    const fills = result?.fills || [];
    const unresolved = result?.unresolved || [];
    crBusy = false;
    crRender();                     // 先把表画回来，再往输入框里填
    const applied = QuoteParams.applyFills(fills);
    card.done(true);
    crStatus(`智能补全给出 ${applied} 项建议`);
    crSay(applied
      ? `已为 ${applied} 项参数填入建议值（表格里标了「AI 建议」）。`
        + `**这是建议不是结论** —— 请逐项核对，改完点「保存补填」才会写进参数表。`
        + (unresolved.length ? `
另有 ${unresolved.length} 项确实推不出来：${unresolved.join('、')}。` : '')
      : `没有可以推出来的参数${unresolved.length ? `：${unresolved.join('、')} 都需要人工确定。` : '。'}`);
  } catch (error) {
    card.done(false, error.message || '失败');
    crStatus(`智能补全失败：${error.message}`, true);
    crToast(error.message || '智能补全失败', true);
    crBusy = false;
    crRender();
  }
}

async function crFinalize(confirm) {
  if (crBusy) return;
  crBusy = true;
  crRenderActions();
  crStatus(confirm ? '确认整合参数…' : '保存补填…');
  try {
    crData = await api(
      `/api/projects/${encodeURIComponent(crPid)}/integration/params/finalize`,
      { method: 'POST',
        body: JSON.stringify({ values: QuoteParams.collect(), confirm: !!confirm }) });
    // finalize 返回的是 2.2 的 payload，重新取一次 2.3 的全貌。
    crData = await api(crUrl(''));
    crStatus(confirm ? '整合参数已确认' : '补填已保存');
    crToast(confirm ? '整合参数已确认' : '已保存');
    if (confirm) crSay('整合参数已确认，报价必填的成品参数都有值了。可以发送至报价。');
  } catch (error) {
    crStatus(`${confirm ? '确认' : '保存'}失败：${error.message}`, true);
    crToast(error.message || '操作失败', true);
  } finally {
    crBusy = false;
    crRender();
  }
}

function crRenderActions() {
  const host = $cr('crActions');
  if (crTab === 'parts') {
    const missing = (crData?.counts?.missing || []).length;
    host.innerHTML =
      `<button type="button" class="inline-action primary start-parse-btn" id="crRunParts" ${crBusy ? 'disabled' : ''}>`
      + (crBusy ? `<span class="parse-spinner" aria-hidden="true"></span><span>测算中…</span>`
                : (missing ? `测算未完成的 ${missing} 个零件` : '重算全部零件'))
      + `</button><span class="ai-hint">逐件调用模型，按库内价格与费率算；不联网。</span>`;
    $cr('crRunParts').onclick = () => crRunParts(true);
    return;
  }
  if (crTab === 'assembly') {
    host.innerHTML =
      `<button type="button" class="inline-action primary start-parse-btn" id="crRunAssembly" ${crBusy ? 'disabled' : ''}>`
      + (crBusy ? `<span class="parse-spinner" aria-hidden="true"></span><span>测算中…</span>`
                : (crData?.assembly?.has_cost ? '重算组装成本' : '测算组装成本'))
      + `</button><span class="ai-hint">以各零件已测算的单件成本为底，叠加组装工序工时与整机费率。</span>`;
    $cr('crRunAssembly').onclick = () => crRunAssembly();
    return;
  }
  if (crTab === 'params') {
    const missing = crData?.required_missing || 0;
    const final = crData?.review?.params_final;
    host.innerHTML =
      `<button type="button" class="inline-action primary start-parse-btn" id="crAutofill" ${crBusy ? 'disabled' : ''}>`
      + (crBusy ? `<span class="parse-spinner" aria-hidden="true"></span><span>智能补全中…</span>` : '✦ 智能补全')
      + `</button>`
      + `<button type="button" class="inline-action save" id="crFinalSave" ${crBusy ? 'disabled' : ''}>保存补填</button>`
      + `<button type="button" class="inline-action" id="crFinalConfirm" ${crBusy ? 'disabled' : ''}>`
      + `${final ? '重新确认' : '确认参数已齐'}</button>`
      + `<span class="ai-hint">${missing
          ? `还差 ${missing} 项报价必填参数，补完再确认。`
          : final ? '已确认，可以发送至报价。' : '必填项都有值了，确认后即可发送至报价。'}</span>`;
    $cr('crAutofill').onclick = () => crAutofill();
    $cr('crFinalSave').onclick = () => crFinalize(false);
    $cr('crFinalConfirm').onclick = () => crFinalize(true);
    return;
  }
  host.innerHTML = `<span class="ai-hint">核对无误后，到左边点「确认成本」，再选择去向。</span>`;
}

/** 只读身份：把这一栏里的按钮全部灰掉。渲染完再统一处理，省得每个分支都写一遍。 */
function crDisableActions() {
  if (!crReadOnly()) return;
  (document.querySelectorAll('#crActions button') || []).forEach(btn => {
    btn.disabled = true;
    btn.title = crReadOnlyWhy();
  });
}

function crRender() {
  const renderers = { parts: crRenderParts, assembly: crRenderAssembly,
                      total: crRenderTotal, params: crRenderParams };
  $cr('crPanelTitle').textContent = CR_TABS[crTab];
  document.querySelectorAll('#crTabs [data-cr-tab]').forEach(button => {
    button.classList.toggle('active', button.dataset.crTab === crTab);
  });
  $cr('crBody').innerHTML = renderers[crTab]();
  crRenderActions();
  crDisableActions();
  crRenderOps();
  document.querySelectorAll('[data-cr-part]').forEach(button => {
    button.onclick = () => crRunPart(button.dataset.crPart, Number(button.dataset.crQty) || 1);
  });
  const counts = crData?.counts || {};
  $cr('crState').textContent = `零件 ${counts.parts_costed || 0}/${counts.parts || 0}`
    + ` · 整机 ${counts.assembly_costed ? '✓' : '—'}`
    + ` · ${crData?.review?.confirmed ? '已确认' : '未确认'}`;
  const name = crData?.assembly?.name;
  $cr('crTitle').textContent = name ? `2.3 成本测算 · ${name}` : '2.3 成本测算';
}

/* --------------------------------------------------------------- 三个去向 */
function crRenderOps() {
  const review = crData?.review || {};
  const final = crData?.final || {};
  const box = $cr('crOpCost');
  const nameInput = $cr('crProductName');
  if (nameInput && !nameInput.value && crData?.assembly?.name) {
    nameInput.value = crData.assembly.name;
  }
  if (final.total) {
    box.innerHTML = Object.keys(CR_COST_LABEL)
      .map(key => `<span><i>${CR_COST_LABEL[key]}</i>${crMoney(final[key])}</span>`).join('')
      + `<span class="total"><i>合计</i>${crMoney(final.total)} 元/台</span>`;
  } else {
    box.textContent = '完成测算后显示成品成本';
  }
  const readOnly = crReadOnly();
  const ready = Boolean(crData?.ready) && !crBusy && !readOnly;
  const confirmed = Boolean(review.confirmed) && !crBusy && !readOnly;
  $cr('crConfirm').disabled = !ready;
  $cr('crConfirm').textContent = review.confirmed ? '✓ 重新确认成本' : '✓ 确认成本';
  $cr('crWriteDb').disabled = !confirmed;
  $cr('crToQuote').disabled = !confirmed;
  $cr('crReturn').disabled = crBusy || readOnly;

  const counts = crData?.counts || {};
  const why = readOnly ? crReadOnlyWhy()
    : crBusy ? '正在处理…'
    : (counts.missing || []).length ? `还有 ${counts.missing.length} 个零件没算成本`
    : !counts.assembly_costed ? '整机（组装）成本还没算'
    : (counts.zero || []).length ? `这些行是 0 元：${counts.zero.join('、')}`
    : !review.confirmed ? '先点「确认成本」，写库与发报价都以确认过的数为准'
    : '';
  const badge = $cr('crWhy');
  badge.textContent = why;
  badge.hidden = !why;

  const done = (review.actions || []).map(action =>
    `<div class="ai-op-done">✓ ${esc(action.label)}：${esc(action.detail)}</div>`).join('');
  $cr('crHint').innerHTML = done
    || '确认成本：三个去向都以确认过的数为准。<br/>'
       + '退回工艺经理：成本高在工序或用量上时用它 —— 那是工艺的判断，不该由财务改。';
}

async function crRunOp(kind) {
  if (crBusy) return;
  const labels = { 'material-write': '写入数据库', 'send-to-quote': '发送至报价',
                   'return-to-process': '退回工艺经理' };
  crBusy = true;
  crRender();
  const card = crCard(labels[kind]);
  crStatus(`${labels[kind]}中…`);
  try {
    const body = {
      product_name: $cr('crProductName')?.value.trim() || '',
      note: $cr('crNote')?.value.trim() || '',
    };
    crData = await api(crUrl(`/${kind}`), { method: 'POST', body: JSON.stringify(body) });
    if (kind === 'material-write') {
      const written = crData.written || {};
      card.log([`成品编码 ${written.number}`, `  产品名称 ${written.name}`,
        `  材料单价（四项合计）${written.material_unit_price} 元`,
        `  已写入 ${(written.tables || []).join('、')}`]);
      crSay(`已写入数据库：成品编码 ${written.number}「${written.name}」，`
        + `单价 ${written.material_unit_price} 元。`);
    } else if (kind === 'send-to-quote') {
      const handoff = crData.handoff || {};
      const fallback = crData.code_fallback;
      // 落到**哪张**报价卡片，比"发出去了"更要紧：新建卡片时销售那边打不开会话历史
      // （报价助手按会话号取历史，技术项目号在那边不存在），客户也只剩技术侧填过的。
      const linked = { task: '回到了原来那张报价卡片（按「新增工艺」任务认回）',
                       session: '回到了原来那张报价卡片（按需求单里记的报价会话号认回）' }[crData.linked_by];
      card.log([`报价卡片进入第 ${handoff.next_step_no || 3} 步「${handoff.next_step_name || '定价-利润加成'}」`,
        `  ${handoff.returned_to_sender ? '已退回给' + (handoff.target_name || '发起人')
                                        : '已通知' + (handoff.target_role_name || '销售经理')}`,
        ...(linked ? [`  ${linked}`] : []),
        ...(crData.new_card ? ['  未认回原报价卡片，已新建一张'] : []),
        ...(fallback ? [`  主数据未写入（${fallback.reason}），改用临时编码 ${fallback.number}`] : [])]);
      crSay(`成本已确认并发送至报价：卡片进入「${handoff.next_step_name || '定价-利润加成'}」。`
        + (crData.new_card
            ? `\n⚠ 这单没能认回原来那张报价卡片（需求单里既没有来源任务号、也没有报价会话号），`
              + `系统新建了一张。销售在报价里打不开这张卡片的对话历史，客户信息也只有技术侧填过的部分`
              + ` —— 请让销售从他自己那张报价单继续，或补上需求单里的报价来源。`
            : '')
        + (fallback ? `\n⚠ 主数据暂时写不进去，本次用了临时成品编码 ${fallback.number}。` : ''));
    } else {
      const returned = crData.returned || {};
      card.log([`任务 ${returned.task_no || ''} 已发给${returned.target_role_name || '工艺经理'}`,
        `  他会复核工序与用量，改完再交回来`]);
      crSay(`已把成本结果退回给${returned.target_role_name || '工艺经理'}`
        + `（任务 ${returned.task_no || ''}）。请在说明里写清楚是哪一项偏高、怀疑在哪。`);
    }
    card.done(true);
    crStatus(`${labels[kind]}完成`);
  } catch (error) {
    card.done(false, error.message || '失败');
    crStatus(`${labels[kind]}失败：${error.message}`, true);
    crToast(error.message || `${labels[kind]}失败`, true);
  } finally {
    crBusy = false;
    crRender();
  }
}

/* --------------------------------------------------------------- 测算 */
async function crRunPart(partId, quantity) {
  if (crBusy) return;
  crBusy = true;
  crRender();
  const card = crCard(`零件成本 · ${partId}`);
  crStatus(`${partId} 测算中…`);
  try {
    const submitted = await api(
      crUrl(`/parts/${encodeURIComponent(partId)}?quantity=${quantity || 1}`),
      { method: 'POST' });
    crData = await crPollTask(submitted.task_id, card);
    card.done(true);
    crStatus(`${partId} 已测算`);
  } catch (error) {
    card.done(false, error.message || '失败');
    crStatus(`${partId} 测算失败：${error.message}`, true);
    crToast(error.message || '测算失败', true);
  } finally {
    crBusy = false;
    crRender();
  }
}

/** 逐件跑。串行是有意的：并行会把一次要花钱的调用变成 N 次同时打出去。 */
async function crRunParts(onlyMissing) {
  const rows = (crData?.parts || []).filter(row => !onlyMissing || !row.has_cost);
  const targets = rows.length ? rows : (crData?.parts || []);
  for (const row of targets) {
    await crRunPart(row.id, row.quantity);
    if (!crData?.parts?.find(item => item.id === row.id)?.has_cost) {
      crSay(`${row.id} 没算出结果，先停在这里 —— 后面的整机成本要以它为底。`);
      return false;
    }
  }
  return true;
}

async function crRunAssembly() {
  if (crBusy) return;
  crBusy = true;
  crRender();
  const card = crCard('组装成本');
  crStatus('组装成本测算中…');
  try {
    const submitted = await api(crUrl('/assembly'), { method: 'POST' });
    crData = await crPollTask(submitted.task_id, card);
    card.done(true);
    crStatus('组装成本已测算');
  } catch (error) {
    card.done(false, error.message || '失败');
    crStatus(`组装成本测算失败：${error.message}`, true);
    crToast(error.message || '测算失败', true);
  } finally {
    crBusy = false;
    crRender();
  }
}

async function crRunAll() {
  await crSaveNote();
  crSay('开始逐件测算：先把每个零件算出来，再算整机（它要引用零件的单件成本）。本步不联网。');
  if (!await crRunParts(false)) return;
  crTab = 'assembly';
  await crRunAssembly();
  crTab = 'total';
  crRender();
  const counts = crData?.counts || {};
  crSay((counts.zero || []).length
    ? `测算完成，但这些行是 0 元：${counts.zero.join('、')}，请重算或人工补明细后再确认。`
    : `测算完成。整机成本 ${crMoney((crData?.final || {}).total)} 元/台，`
      + `核对无误后点「确认成本」，再选择去向。`);
}

async function crConfirmCost() {
  if (crBusy) return;
  crBusy = true;
  crRender();
  try {
    crData = await api(crUrl('/confirm'), { method: 'POST' });
    crStatus('成本已确认');
    crToast('成本已确认');
    crSay('成本已确认。现在可以写入数据库、发送至报价，或把结果退回工艺经理复核。');
  } catch (error) {
    crStatus(`确认失败：${error.message}`, true);
    crToast(error.message || '确认失败', true);
  } finally {
    crBusy = false;
    crRender();
  }
}

async function crSaveNote() {
  try {
    crData = await api(crUrl(''), {
      method: 'PUT',
      body: JSON.stringify({
        note: $cr('crNote').value.trim(),
        quantity: Math.max(1, parseInt($cr('crQuantity').value, 10) || 1),
      }),
    });
    crStatus('说明已保存');
  } catch (error) { crToast(error.message || '保存失败', true); }
}

/* --------------------------------------------------------------- 模型设置 */
let crPop = null;
function crClosePop() {
  if (!crPop) return;
  crPop.remove();
  crPop = null;
  document.removeEventListener('click', crClosePop);
}
function crOpenSettings(anchor) {
  crClosePop();
  const pop = document.createElement('div');
  pop.className = 'oc-pop oc-settings';
  pop.addEventListener('click', event => event.stopPropagation());
  const host = document.createElement('div');
  host.className = 'llm-set-host';
  pop.append(host);
  document.body.append(pop);
  const rect = anchor.getBoundingClientRect();
  pop.style.top = `${Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - pop.offsetHeight - 12))}px`;
  pop.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - pop.offsetWidth - 12))}px`;
  crPop = pop;
  setTimeout(() => document.addEventListener('click', crClosePop), 0);
  if (!window.LlmSettingsPanel) {
    host.innerHTML = '<div class="llm-set-status err">模型设置面板未加载</div>';
    return;
  }
  window.LlmSettingsPanel.mount(host, {
    onSaved: settings => { crSetModelLabel(settings); crToast('模型设置已保存，全局生效。'); },
  });
}
function crSetModelLabel(settings) {
  const pill = $cr('crModelPill');
  if (!pill) return;
  const options = [...(settings?.text_options || []), ...(settings?.vision_options || [])];
  const label = id => options.find(option => option.id === id)?.label || id;
  pill.querySelector('[data-model-name]').textContent =
    settings?.text_model ? label(settings.text_model) : '未配置模型';
}
async function crLoadModelLabel() {
  if (!window.LlmSettingsPanel) { crSetModelLabel(null); return; }
  try { crSetModelLabel(await window.LlmSettingsPanel.load()); }
  catch { $cr('crModelPill')?.querySelector('[data-model-name]').replaceChildren('未连接'); }
}

/* --------------------------------------------------------------- 启动 */
function crBind() {
  document.querySelectorAll('[data-cr-tab]').forEach(button => {
    button.onclick = () => { crTab = button.dataset.crTab; crRender(); };
  });
  $cr('crGoParts').onclick = () => { crTab = 'parts'; crRender(); };
  $cr('crGoAssembly').onclick = () => { crTab = 'assembly'; crRender(); };
  $cr('crGoTotal').onclick = () => { crTab = 'total'; crRender(); };
  $cr('crGoParams').onclick = () => { crTab = 'params'; crRender(); };
  $cr('crModelPill').onclick = event => { event.stopPropagation(); crOpenSettings(event.currentTarget); };
  $cr('crRunAll').onclick = () => crRunAll();
  $cr('crConfirm').onclick = () => crConfirmCost();
  $cr('crWriteDb').onclick = () => crRunOp('material-write');
  $cr('crToQuote').onclick = () => crRunOp('send-to-quote');
  $cr('crReturn').onclick = () => crRunOp('return-to-process');
  $cr('crNote').onchange = () => crSaveNote();
  $cr('crQuantity').onchange = () => crSaveNote();
  $cr('crPrev').onclick = () => window.CadWorkflowNavigation?.navigate('2.2');
  $cr('crNext').onclick = () => window.CadWorkflowNavigation?.navigate('3.1');
}

async function crStart() {
  if (!crPid) { location.href = 'home.html'; return; }
  crBind();
  crUser = (window.cpqAuth && window.cpqAuth.user && window.cpqAuth.user()) || null;
  if (!crUser && window.cpqAuth && window.cpqAuth.refresh) {
    // 首屏可能还没拉到登录态：等它一次，别把有权限的人误判成只读。
    try { await window.cpqAuth.refresh(); } catch (error) { /* 后端会再判一次 */ }
    crUser = (window.cpqAuth.user && window.cpqAuth.user()) || null;
  }
  $cr('crProject').textContent = `项目 ${crPid}`;
  crLoadModelLabel();
  try {
    crData = await api(crUrl(''));
    $cr('crNote').value = crData.review?.note || '';
    $cr('crQuantity').value = crData.quantity || 1;
    crTab = crData.ready ? 'total' : (crData.counts?.missing || []).length ? 'parts' : 'assembly';
    crRender();
    crStatus(crReadOnly() ? '只读：本步归财务经理'
      : crData.review?.confirmed ? '成本已确认' : '就绪');
    if (crReadOnly()) crSay(crReadOnlyWhy());
    if (!(crData.parts || []).length) {
      crSay('还没有拿到 2.1 的零件清单。成本要按零件逐件算，请先完成图纸解析。');
    }
  } catch (error) {
    $cr('crBody').innerHTML = `<div class="inline-empty error">读取失败：${esc(error.message)}</div>`;
    crStatus(`读取失败：${error.message}`, true);
  }
}

crStart();
