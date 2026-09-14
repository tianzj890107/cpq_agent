/* 【CPQ 定制 · 本文件不来自 process_drawing，同步上游时保留】
 *
 * 2.3 成本测算 —— 财务经理的步骤。
 *
 * 分工：工艺经理在 2.1/2.2 出工艺、参数与用量，2.2 结束点「确认工艺并发送至财务做
 * 成本测算」把项目交过来；财务在这里逐个零件 + 整机算成本、汇总，然后选三个去向：
 * 写入数据库 / 回传销售经理继续报价 / 提交工艺经理确认。
 *
 * 两条要一直记着的口径：
 *   · **本步不联网**。只用企业成本库里已有的物料价、费率、系数，加模型的工程经验。
 *     库里没有的写进待确认让财务去询价 —— 一个"网上搜来的"单价，报价那头没法追溯。
 *   · **对外用整机成本，不是零件之和**。整机成本的材料项就是逐个零件引过来的，
 *     两者相加会把零件成本算两遍。界面上两个数都给，但送出去的只有整机那个。
 */
const crPid = new URLSearchParams(location.search).get('project')
  || localStorage.getItem('cad_engine_project_id') || '';
// 统一工作台从待办点进来时 URL 上带着来源 task_id / tech_task：完成正式去向时原样带回，
// 由后端把原来那条已领取的财务待办关闭（cpq_wf.complete_claimed_task，幂等）。
const crTaskId = new URLSearchParams(location.search).get('task_id')
  || new URLSearchParams(location.search).get('tech_task') || '';
const CR_TABS = { parts: '零件成本', assembly: '组装成本', total: '汇总' };
const CR_COST_LABEL = { material: '材料', labor: '人工', overhead: '制造费用', machining: '加工费用' };

let crData = null;
let crTab = 'parts';
let crBusy = false;
// deferred 长任务（runCostReview / costStep）自己的并发闸门：启动即回执，
// 后台链路没结束前不允许再排一条。
let crDeferredBusy = false;
/* 2.3 是财务经理的步骤。别人（工艺经理、销售）能打开这一页看数，但不能算、不能发。
   后端才是权威（main.py 的 COST_ROLES），这里只是提前说清楚 —— 否则要等点下去
   才收到 403，还容易被读成"系统坏了"。取不到身份时不拦：让后端去判。 */
let crUser = null;
const CR_COST_ROLES = ['finance_manager', 'admin'];
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
  // 与本地显示的同一段文字原样上报；独立打开（无运行时）时不通信。
  const runtime = window.TechBoardRuntime;
  if (runtime && typeof runtime.publishStatus === 'function') runtime.publishStatus(message || '', error ? 'error' : 'info');
}

function crToast(message, error = false) {
  const node = document.createElement('div');
  node.className = `ai-toast${error ? ' error' : ''}`;
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), error ? 4200 : 2400);
}

/* --------------------------------------------------------------- 任务轮询 */
/** 长任务进度经统一看板协议上报父壳：左侧进度卡按 taskId 去重、日志增量追加。 */
function crPublishTask(name, extra) {
  const runtime = window.TechBoardRuntime;
  if (!runtime || typeof runtime.publish !== 'function') return;
  try {
    runtime.publish(name, 'costStep', Object.assign({ action: 'costStep' }, extra || {}));
    crRememberSettle(name, 'costStep', extra);
  } catch { /* 进度上报失败不影响业务本身 */ }
}

// 最近一次收尾事件：长任务包装器据此决定要不要补一条属于本动作名的 task-completed，
// 已经发过同样事件时不再重复，失败时把真实原因原样带过去。
let crLastSettle = null;
function crRememberSettle(name, subject, extra) {
  if (name !== 'task-completed' && name !== 'task-failed') return;
  crLastSettle = { event: name, subject: subject,
                   message: (extra && (extra.error || extra.message)) || '' };
}

// 长任务（deferred）在后台真正结束时由本页自报收尾：主体必须是本动作名，
// 失败必须带真实错误文本（父壳拿来显示，不再只报「超时未响应」）。
function crSettleTask(event, action, extra) {
  try {
    window.TechBoardRuntime.publish(event, action, Object.assign({ action: action }, extra || {}));
  } catch (error) { /* 独立打开无运行时 */ }
  crRememberSettle(event, action, extra);
}

// 既有实现（crRunPart / crRunAssembly）已经就同一动作发过同一收尾事件时不再补发。
function crSettleIfNeeded(event, action, extra) {
  if (crLastSettle && crLastSettle.event === event && crLastSettle.subject === action) return;
  crSettleTask(event, action, extra);
}

// 后台链路放在注册表外：动作条目只启动它并秒级回执（deferred），逐件 + 整机
// 测算跑完后再由这里推 task-completed / task-failed。
async function crRunAllInBackground() {
  try {
    await crRunAll();
    if (crLastSettle && crLastSettle.event === 'task-failed') {
      crSettleTask('task-failed', 'runCostReview',
        { message: crLastSettle.message || '成本测算未完成，请查看右侧看板提示。' });
    } else {
      crSettleIfNeeded('task-completed', 'runCostReview');
    }
  } catch (error) {
    crSettleTask('task-failed', 'runCostReview',
      { message: (error && error.message) || '成本测算失败，请查看右侧看板提示。' });
  } finally {
    crDeferredBusy = false;
    window.TechBoardRuntime.updateActionState('runCostReview', { busy: false });
  }
}

// 单个环节（零件 / 组装 / 全量）的后台链路，同上：只启动，跑完自报。
async function crCostStepInBackground(work) {
  try {
    const result = await work();
    const failed = (result && result.ok === false)
      || Boolean(crLastSettle && crLastSettle.event === 'task-failed');
    if (failed) {
      const message = (crLastSettle && crLastSettle.message)
        || (result && result.error && result.error.message)
        || '成本测算未完成，请查看右侧看板提示。';
      crSettleIfNeeded('task-failed', 'costStep', { message: message });
    } else {
      crSettleIfNeeded('task-completed', 'costStep');
    }
  } catch (error) {
    crSettleIfNeeded('task-failed', 'costStep',
      { message: (error && error.message) || '成本测算失败，请查看右侧看板提示。' });
  } finally {
    crDeferredBusy = false;
    window.TechBoardRuntime.updateActionState('costStep', { busy: false });
  }
}

async function crPollTask(taskId, card, label) {
  for (;;) {
    await crSleep(1200);
    const task = await api(`/api/projects/${encodeURIComponent(crPid)}/tasks/${encodeURIComponent(taskId)}`);
    const log = Array.isArray(task.progress_log) ? task.progress_log : [];
    card.log(log);
    // progress_log 只增量追加：同一 taskId 的进度卡不会被后来的快照覆盖掉中间步骤。
    crPublishTask('task-progress', { taskId: taskId, label: label || '成本测算',
                                     status: 'running', log: log });
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

/* 「确认成本」的前置条件唯一判定：只读身份 / 缺零件 / 缺件数 / 整机未算 / 0 元行。
   只判前置条件，不含 busy；页内提示与左侧动作快照的 run() 共用这一份，两处不漂移。 */
function crConfirmBlocker() {
  if (crReadOnly()) return crReadOnlyWhy();
  const counts = crData?.counts || {};
  if (!counts.parts) return '还没有零件可以确认，请先完成成本测算';
  if ((counts.missing || []).length) return `还有 ${counts.missing.length} 个零件没算成本`;
  if (!counts.assembly_costed) return '整机（组装）成本还没算';
  if ((counts.zero || []).length) return `这些行是 0 元：${counts.zero.join('、')}`;
  return '';
}

function crRenderActions() {
  const host = $cr('crActions');
  // 确认按钮的悬浮原因与左侧动作快照共用同一份判定（crConfirmBlocker）。
  const confirmBtn = $cr('crConfirm');
  if (confirmBtn) confirmBtn.title = crConfirmBlocker() || '确认成本后，三个去向都以确认过的数为准';
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
                      total: crRenderTotal };
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
  crPublishState();
}

/* 每次渲染收尾重新发布一次动作快照：父壳「写入数据库 / 回传销售经理继续报价 /
   提交工艺经理确认」的可用性不会停在上一帧（确认成本与测算收尾都会走到这里）。
   同一帧内合并成一次；独立打开阶段页（没有 TechBoardRuntime）时无副作用。 */
let crPublishTimer = null;
function crPublishState() {
  const runtime = window.TechBoardRuntime;
  if (!runtime || typeof runtime.refreshState !== 'function') return;
  if (crPublishTimer) return;
  crPublishTimer = setTimeout(() => {
    crPublishTimer = null;
    try { runtime.refreshState(); } catch (error) { /* 快照刷新失败不影响页内渲染 */ }
  }, 0);
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

  // 去向提示：前置条件来自 crConfirmBlocker()（唯一判定），确认之后才是三个去向。
  const why = crBusy ? '正在处理…'
    : crConfirmBlocker()
    || (!review.confirmed ? '先点「确认成本」，写库与发报价都以确认过的数为准' : '');
  const badge = $cr('crWhy');
  badge.textContent = why;
  badge.hidden = !why;

  const done = (review.actions || []).map(action =>
    `<div class="ai-op-done">✓ ${esc(action.label)}：${esc(action.detail)}</div>`).join('');
  $cr('crHint').innerHTML = done
    || '确认成本：三个去向都以确认过的数为准。<br/>'
       + '提交工艺经理确认：把已确认的成本交给工艺经理，进第 5 大步做最终工艺确认与报告；'
       + '确实要返工，由第 5 大步明确退回第 3 大步。<br/>'
       + '回传销售经理继续报价：优先回到原报价会话，从第 3 步「定价-利润加成」继续。';
}

async function crRunOp(kind) {
  if (crBusy) return false;
  const labels = { 'material-write': '写入数据库', 'send-to-quote': '回传销售经理继续报价',
                   'return-to-process': '提交工艺经理确认' };
  crBusy = true;
  crRender();
  const card = crCard(labels[kind]);
  crStatus(`${labels[kind]}中…`);
  const opTask = `cost-op-${kind}`;
  crPublishTask('task-progress', { taskId: opTask, label: labels[kind],
                                   progress: `${labels[kind]}中…` });
  try {
    const body = {
      product_name: $cr('crProductName')?.value.trim() || '',
      note: $cr('crNote')?.value.trim() || '',
      // 来源待办：完成后后端据此关闭已领取的 tech_cost 任务，不让旧待办继续挂着。
      source_task_id: crTaskId,
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
      crSay(`成本已确认并回传销售经理继续报价：卡片进入「${handoff.next_step_name || '定价-利润加成'}」。`
        + (crData.already_sent ? '\n（这一版已经回传过，沿用已有交接，没有重复建任务。）' : '')
        + (crData.new_card
            ? `\n⚠ 这单没能认回原来那张报价卡片（需求单里既没有来源任务号、也没有报价会话号），`
              + `系统新建了一张。销售在报价里打不开这张卡片的对话历史，客户信息也只有技术侧填过的部分`
              + ` —— 请让销售从他自己那张报价单继续，或补上需求单里的报价来源。`
            : '')
        + (fallback ? `\n⚠ 主数据暂时写不进去，本次用了临时成品编码 ${fallback.number}。` : ''));
    } else {
      const returned = crData.returned || {};
      const source = crData.source_task || {};
      card.log([`任务 ${returned.task_no || ''} 已发给${returned.target_role_name || '工艺经理'}`,
        `  落点：第 5 大步「工艺评估报告」（stage=summary）`,
        `  随包带上参数、工艺路线、零件成本、组装成本与确认信息`,
        ...(source.closed ? [`  来源待办 ${source.task_no || crTaskId} 已完成`]
                          : (source.skipped ? [] : [`  来源待办未能关闭：${source.error || '未知原因'}`]))]);
      crSay(`已提交给${returned.target_role_name || '工艺经理'}确认`
        + `（任务 ${returned.task_no || ''}）。他会进入第 5 大步做最终工艺确认、汇总、审核与发布。`);
    }
    card.done(true);
    crStatus(`${labels[kind]}完成`);
    crPublishTask('task-completed', { taskId: opTask, label: labels[kind],
                                      status: 'succeeded' });
    return true;
  } catch (error) {
    card.done(false, error.message || '失败');
    crStatus(`${labels[kind]}失败：${error.message}`, true);
    crToast(error.message || `${labels[kind]}失败`, true);
    crPublishTask('task-failed', { taskId: opTask, label: labels[kind],
                                   status: 'failed', error: error.message || '失败' });
    return false;
  } finally {
    crBusy = false;
    crRender();
  }
}

/* --------------------------------------------------------------- 测算 */
async function crRunPart(partId, quantity) {
  if (crBusy) return false;
  crBusy = true;
  crRender();
  const label = `零件成本 ${partId}`;
  const card = crCard(`零件成本 · ${partId}`);
  crStatus(`${partId} 测算中…`);
  let taskKey = `cost-part-${partId}`;
  try {
    const submitted = await api(
      crUrl(`/parts/${encodeURIComponent(partId)}?quantity=${quantity || 1}`),
      { method: 'POST' });
    taskKey = String(submitted.task_id || taskKey);
    crPublishTask('task-progress', { taskId: taskKey, label: label, status: 'running',
                                     progress: `${partId} 已提交，正在测算…` });
    crData = await crPollTask(taskKey, card, label);
    card.done(true);
    crStatus(`${partId} 已测算`);
    crPublishTask('task-completed', { taskId: taskKey, label: label, status: 'succeeded' });
    return true;
  } catch (error) {
    card.done(false, error.message || '失败');
    crStatus(`${partId} 测算失败：${error.message}`, true);
    crToast(error.message || '测算失败', true);
    crPublishTask('task-failed', { taskId: taskKey, label: label, status: 'failed',
                                   error: error.message || '测算失败' });
    return false;
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
  if (crBusy) return false;
  crBusy = true;
  crRender();
  const label = '组装成本';
  const card = crCard('组装成本');
  crStatus('组装成本测算中…');
  let taskKey = 'cost-assembly';
  try {
    const submitted = await api(crUrl('/assembly'), { method: 'POST' });
    taskKey = String(submitted.task_id || taskKey);
    crPublishTask('task-progress', { taskId: taskKey, label: label, status: 'running',
                                     progress: '整机成本已提交，正在测算…' });
    crData = await crPollTask(taskKey, card, label);
    card.done(true);
    crStatus('组装成本已测算');
    crPublishTask('task-completed', { taskId: taskKey, label: label, status: 'succeeded' });
    return true;
  } catch (error) {
    card.done(false, error.message || '失败');
    crStatus(`组装成本测算失败：${error.message}`, true);
    crToast(error.message || '测算失败', true);
    crPublishTask('task-failed', { taskId: taskKey, label: label, status: 'failed',
                                   error: error.message || '测算失败' });
    return false;
  } finally {
    crBusy = false;
    crRender();
  }
}

async function crRunAll() {
  await crSaveNote();
  crSay('一键测算全部成本：先把每个零件算出来，再算整机（它要引用零件的单件成本）。本步不联网。');
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
  if (crBusy) return false;
  crBusy = true;
  crRender();
  crPublishTask('task-progress', { taskId: 'cost-confirm', label: '确认成本',
                                   progress: '正在确认成本…' });
  try {
    crData = await api(crUrl('/confirm'), { method: 'POST' });
    crStatus('成本已确认');
    crToast('成本已确认');
    crSay('成本已确认。现在可以写入数据库、回传销售经理继续报价，或提交工艺经理确认（第 5 大步）。');
    crPublishTask('task-completed', { taskId: 'cost-confirm', label: '确认成本',
                                      status: 'succeeded' });
    return true;
  } catch (error) {
    crStatus(`确认失败：${error.message}`, true);
    crToast(error.message || '确认失败', true);
    crPublishTask('task-failed', { taskId: 'cost-confirm', label: '确认成本',
                                   status: 'failed', error: error.message || '确认失败' });
    return false;
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
  // 唯一模型口径：与 assembly-integration.js / agent-chat.js 一致，只读报价
  // /api/settings 的单一 model + options。
  const options = settings?.options || [];
  const modelId = String(settings?.model || '').trim();
  const option = options.find(item => item && item.id === modelId);
  pill.querySelector('[data-model-name]').textContent =
    (option && option.label) || modelId || '未配置模型';
  pill.title = `当前模型：${(option && option.label) || modelId || '未配置模型'}`;
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
  if (!crPid) { if(window.TechEmbed&&window.TechEmbed.embedded){window.TechEmbed.exitToTechHome();return;} location.href = 'home.html'; return; }
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

/* 统一看板协议：2.3 的测算与确认成本注册成语义化动作，内部页签注册成语义化视图。 */
(function crRegisterTechBoardActions() {
  if (!window.TechBoardRuntime || typeof window.TechBoardRuntime.registerActions !== 'function') return;
  const crSetTab = (name) => { crTab = name; crRender(); };
  // 重新拉取并渲染（复用既有读取路径），供 Agent 改完说明 / 确认 / 去向之后刷新看板。
  const crRefresh = async () => {
    try {
      crData = await api(crUrl(''));
      crRender();
      crStatus('已刷新');
      return { ok: true };
    } catch (error) {
      crStatus(`刷新失败：${error.message}`, true);
      return { ok: false, error: { code: 'refresh-failed', message: error.message || '刷新失败' } };
    }
  };
  // 三个去向动作共用：各自复用既有 crRunOp(kind)（material-write / send-to-quote /
  // return-to-process），不复制任何对外调用。
  const crOpAction = (kind, label, role, order) => ({
    label: label,
    role: role || 'aux',
    order: order == null ? null : order,
    run: async () => {
      const ok = await crRunOp(kind);
      return ok ? { ok: true }
        : { ok: false, error: { code: 'op-failed', message: `${label}失败，请查看看板提示。` } };
    },
    getState: () => ({ visible: true, enabled: true, busy: Boolean(crBusy) }),
  });
  window.TechBoardRuntime.registerActions({
    runCostReview: {
      label: '一键测算全部成本',
      role: 'primary',
      order: 10,
      deferred: true,
      run: () => {
        if (crBusy) return { ok: false, error: { code: 'busy', message: '正在测算，请稍候。' } };
        if (crDeferredBusy) return { ok: false, error: { code: 'busy', message: '正在测算，请稍候。' } };
        // 逐件测算 + 整机汇总要跑很多轮模型调用：只启动，秒级回执。
        crDeferredBusy = true;
        crLastSettle = null;
        window.TechBoardRuntime.updateActionState('runCostReview', { busy: true });
        crRunAllInBackground();
        return { ok: true };
      },
      getState: () => ({ visible: true, enabled: true, busy: Boolean(crBusy) }),
    },
    confirmCostReview: {
      label: '确认成本',
      role: 'aux',
      order: 20,
      run: async () => {
        const why = crConfirmBlocker();
        if (why) { crStatus(why, true); return { ok: false, error: { code: 'not-ready', message: why } }; }
        const done = await crConfirmCost();
        return done ? { ok: true }
          : { ok: false, error: { code: 'confirm-failed', message: '确认成本失败，请查看右侧看板提示。' } };
      },
      getState: () => ({ visible: true, enabled: true, busy: Boolean(crBusy) }),
    },
    // Agent 改完说明 / 确认 / 去向之后让看板重新拉取并渲染。
    refreshCostReview: {
      label: '刷新成本看板',
      role: 'aux',
      order: 60,
      run: async () => crRefresh(),
      // 只退出左侧栏：刷新仍由 refresh-data 命令与 Agent 工具走 executeAction 触发。
      getState: () => ({ visible: false, enabled: true, busy: Boolean(crBusy) }),
    },
    // 左侧 Agent 请求跑某一环节：复用既有 crRunPart / crRunAssembly / crRunAll。
    costStep: {
      label: '运行成本测算',
      role: 'aux',
      order: 70,
      deferred: true,
      run: (payload) => {
        const step = String((payload && payload.step) || '').toLowerCase();
        const partId = String((payload && payload.part_id) || '').trim();
        if (step === 'part') {
          if (!partId) {
            return { ok: false, error: { code: 'missing-part',
                                         message: '缺少 part_id：请指定要测算的零件。' } };
          }
        } else if (step !== 'assembly' && step !== 'all') {
          return { ok: false, error: { code: 'bad-step', message: 'step 只能是 part / assembly / all' } };
        }
        if (crBusy || crDeferredBusy) {
          return { ok: false, error: { code: 'busy', message: '正在测算，请稍候。' } };
        }
        // 单件 / 整机 / 全量都是长任务：只启动后台链路，完成或失败由它自己上报。
        const quantity = Number((payload && payload.quantity) || 1);
        const work = step === 'all'
          ? () => crRunAll()
          : (step === 'assembly'
            ? () => crRunAssembly().then((done) => (done ? { ok: true }
                : { ok: false, error: { code: 'step-failed', message: '组装成本测算失败，请查看右侧看板提示。' } }))
            : () => crRunPart(partId, quantity).then((done) => (done ? { ok: true }
                : { ok: false, error: { code: 'step-failed', message: `${partId} 测算失败，请查看右侧看板提示。` } })));
        crDeferredBusy = true;
        crLastSettle = null;
        window.TechBoardRuntime.updateActionState('costStep', { busy: true });
        crCostStepInBackground(work);
        return { ok: true };
      },
      getState: () => ({ visible: true, enabled: true, busy: Boolean(crBusy) }),
    },
    // 三个去向：复用既有 crRunOp，不新增对外调用。
    writeCostReviewMaterial: crOpAction('material-write', '写入数据库', 'aux', 30),
    sendCostReviewToQuote: crOpAction('send-to-quote', '回传销售经理继续报价', 'aux', 40),
    returnCostReviewToProcess: crOpAction('return-to-process', '提交工艺经理确认', 'aux', 50),
  });
  window.TechBoardRuntime.registerViews({
    parts: { run: () => crSetTab('parts'), getState: () => ({ active: crTab === 'parts' ? 'parts' : null }) },
    assembly: { run: () => crSetTab('assembly'), getState: () => ({ active: crTab === 'assembly' ? 'assembly' : null }) },
    total: { run: () => crSetTab('total'), getState: () => ({ active: crTab === 'total' ? 'total' : null }) },
    // 「整合参数」已归位第 3 大步「组装与整合 · 参数推荐」，本步不再有对应看板。
    // 这里只留一个显式拒绝的兼容别名：旧调用会拿到可识别的失败，而不是静默什么都不做，
    // 也不会打开一个已经不存在的页签。
    params: {
      run: () => ({ ok: false, error: { code: 'moved-to-integration',
        message: '「整合参数」在第 3 大步「组装与整合 · 参数推荐」，本步只保留成本页签。' } }),
      getState: () => ({ visible: false, active: null }),
    },
  });
})();
