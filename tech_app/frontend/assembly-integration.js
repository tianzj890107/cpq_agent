/*
 * 2.2 组装与整合。
 *
 * 2.1 把一张图纸拆成零件、逐件出工艺与成本；这一页把零件装回一台整机，四个环节：
 *   ① 整合图纸 → ② 参数推荐 → ③ 组装工艺 → ④ 成本测算
 * 顺序是硬的：后一环节拿前一环节的结论当输入，后端也照这个顺序挡（缺参数不给排工艺、
 * 缺工艺不给算成本）。前端这里只是把同样的规则提前告诉人，别让他撞一个 400 才知道。
 *
 * 渲染刻意沿用 2.1 内嵌分析面板的 .inline-* 类：工艺路线和成本明细在两页是同一种东西，
 * 长得不一样只会让人以为算法也不一样。
 */
const aiPid = new URLSearchParams(location.search).get('project')
  || localStorage.getItem('cad_engine_project_id') || '';
const AI_TABS = {
  drawings: '整合图纸', params: '参数推荐', process: '组装工艺', cost: '成本测算',
  // 第五个环节不调模型：成本算完之后，把报价要的成品参数逐项收口，缺的人工补上。
  // 它是 2.2 与报价之间的验收口径 —— 必填缺一格，报价测算单上就是一格空白。
  finalize: '整合参数',
};
const AI_TYPE_LABEL = {
  blank: '下料/备料', turning: '车', milling: '铣', drilling: '钻', boring: '镗',
  grinding: '磨', bench: '钳工', sheet_metal: '钣金', welding: '焊接',
  heat_treat: '热处理', surface: '表面处理', assembly: '装配', inspection: '检验', other: '其他',
};
const AI_CAT_LABEL = {
  material: '材料费', labor: '人工费', machining: '加工费用',
  standard_part: '标准件/外购', heat_treat: '热处理',
  surface: '表面处理', welding: '焊接', assembly: '装配', inspection: '检验',
  tooling: '工装摊销', logistics: '物流包装', overhead: '管理费', profit: '利润', other: '其他',
};

let aiData = null;          // 后端 payload：plan / status / *_validation / *_lookup
// 2.1 的零件与它们的单件成本单独放：aiData 会被每次任务结果和 PUT 响应整体替换，
// 混在一起的话零件清单会在第一次生成之后凭空消失。
let aiParts = [];
let aiTab = 'drawings';
let aiBusy = false;
const aiEditing = { params: false, process: false, cost: false };

const $ai = id => document.getElementById(id);
const aiUrl = (suffix = '') => `/api/projects/${encodeURIComponent(aiPid)}/integration${suffix}`;
const aiAttr = value => esc(value).replace(/"/g, '&quot;');
const aiMoney = value => value == null ? '—'
  : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
const aiSleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const aiPlan = () => aiData?.plan || {};

// --------------------------------------------------------------------------- 对话区
function aiThreadAppend(html) {
  $ai('aiEmpty')?.remove();
  const node = document.createElement('div');
  node.innerHTML = html;
  const element = node.firstElementChild;
  $ai('aiTinner').append(element);
  $ai('aiThread').scrollTop = $ai('aiThread').scrollHeight;
  return element;
}

function aiSay(text) {
  aiThreadAppend(`<div class="oc-amsg"><div class="oc-aav" aria-hidden="true">✦</div>
    <div class="oc-abody"><div class="oc-atxt">${esc(text)}</div></div></div>`);
}

function aiUserSay(text) {
  aiThreadAppend(`<div class="oc-ubub">${esc(text)}</div>`);
}

/** 处理过程卡：任务进度逐条落在这里，和 2.1 的 Agent 对话框一个样子。 */
function aiProcessCard(title) {
  const card = aiThreadAppend(`<div class="oc-amsg"><div class="oc-aav" aria-hidden="true">✦</div>
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
      $ai('aiThread').scrollTop = $ai('aiThread').scrollHeight;
    },
    done(ok, message) {
      const state = card.querySelector('.oc-process-state');
      state.className = `oc-process-state ${ok ? 'ok' : 'err'}`;
      state.textContent = ok ? '已完成' : '失败';
      if (message) this.log([`  ${message}`]);
    },
  };
}

// --------------------------------------------------------------------------- 状态与提示
function aiStatus(message, error = false) {
  const badge = $ai('status');
  if (badge && !badge.dataset.fatal) badge.textContent = message;
  const panel = $ai('aiPanelStatus');
  panel.textContent = message || '';
  panel.classList.toggle('error', error);
}

function aiToast(message, error = false) {
  document.querySelectorAll('.ai-toast').forEach(node => node.remove());
  const el = document.createElement('div');
  el.className = `ai-toast${error ? ' error' : ''}`;
  el.textContent = message;
  document.body.append(el);
  setTimeout(() => el.remove(), 3200);
}

/** 这一环节能不能开跑。返回空串表示可以，否则是拦下来的理由。 */
function aiBlocker(tab) {
  const state = aiData?.status || {};
  if (tab === 'params') return '';
  if (tab === 'process' && !state.has_params) return '请先完成「参数推荐」：组装工序要按整机 BOM 与连接关系来排。';
  if (tab === 'cost' && !state.has_process) return '请先完成「组装工艺」：组装成本要按工序工时逐道算。';
  if (tab === 'finalize' && !state.has_cost) return '请先完成「成本测算」：整合参数是本步最后的收口。';
  return '';
}

// --------------------------------------------------------------------------- 请求
async function aiPost(path, { form, label, quantity } = {}) {
  if (aiBusy) return;
  const blocked = aiBlocker(path);
  if (blocked) { aiToast(blocked, true); return; }
  aiBusy = true;
  aiRenderActions();
  const title = label || AI_TABS[path];
  aiStatus(`${title}生成中…`);
  const card = aiProcessCard(title);
  try {
    const query = quantity ? `?quantity=${quantity}` : '';
    const submitted = await api(aiUrl(`/${path}${query}`), { method: 'POST', body: form || new FormData() });
    aiData = await aiPollTask(submitted.task_id, card);
    aiEditing[path] = false;
    card.done(true);
    aiStatus(`${title}已完成`);
    aiTab = path;
    aiRender();
  } catch (error) {
    card.done(false, error.message || '失败');
    aiStatus(`${title}失败：${error.message}`, true);
    aiToast(error.message || `${title}失败`, true);
  } finally {
    aiBusy = false;
    aiRenderActions();
  }
}

async function aiPollTask(taskId, card) {
  while (true) {
    await aiSleep(1200);
    const task = await api(`/api/projects/${encodeURIComponent(aiPid)}/tasks/${encodeURIComponent(taskId)}`);
    card.log(Array.isArray(task.progress_log) ? task.progress_log : []);
    if (task.status === 'succeeded') return task.result;
    if (task.status === 'failed') throw new Error(task.error || '任务失败');
  }
}

async function aiPut(path, payload, message) {
  try {
    aiData = await api(aiUrl(path), { method: 'PUT', body: JSON.stringify(payload) });
    aiRender();
    aiStatus(message);
    aiToast(message);
    return true;
  } catch (error) {
    aiStatus(`保存失败：${error.message}`, true);
    aiToast(error.message || '保存失败', true);
    return false;
  }
}

// --------------------------------------------------------------------------- 顶部动作条
function aiRenderActions() {
  const host = $ai('aiActions');
  if (!host) return;
  if (aiTab === 'drawings') {
    host.innerHTML = `<button type="button" class="inline-action primary start-parse-btn" id="aiUploadBtn">上传整合图纸</button>`
      + `<span class="ai-hint">装配图 / 爆炸图 / 接线图都可以，支持多选。它们会作为参数推荐与组装工艺的视觉输入。</span>`;
    $ai('aiUploadBtn').onclick = () => $ai('aiDrawingInput').click();
    return;
  }
  if (aiTab === 'finalize') {
    // 这一步不从零推参数，只补缺口：智能补全给建议、人过目、保存、确认。
    const state = aiData?.status || {};
    const missing = state.required_missing || 0;
    host.innerHTML =
      `<button type="button" class="inline-action primary start-parse-btn" id="aiAutofill" ${aiBusy ? 'disabled' : ''}>`
      + (aiBusy ? `<span class="parse-spinner" aria-hidden="true"></span><span>智能补全中…</span>` : '✦ 智能补全')
      + `</button>`
      + `<button type="button" class="inline-action save" id="aiFinalSave" ${aiBusy ? 'disabled' : ''}>保存补填</button>`
      + `<button type="button" class="inline-action" id="aiFinalConfirm" ${aiBusy ? 'disabled' : ''}>`
      + `${state.params_final ? '重新确认' : '确认参数已齐'}</button>`
      + `<span class="ai-hint">${missing
          ? `还差 ${missing} 项报价必填参数。「智能补全」按 2.1 零件、已排工艺与已算成本给建议值，填完再确认。`
          : state.params_final ? '已确认，可以发送至报价。' : '必填项都有值了，确认后即可发送至报价。'}</span>`;
    $ai('aiAutofill').onclick = () => aiAutofill();
    $ai('aiFinalSave').onclick = () => aiFinalize(false);
    $ai('aiFinalConfirm').onclick = () => aiFinalize(true);
    return;
  }
  const has = { params: aiData?.status?.has_params, process: aiData?.status?.has_process, cost: aiData?.status?.has_cost }[aiTab];
  // 参数推荐这一环节也要有人按下确认：整机参数、连接关系与 BOM 是后面工艺与成本的输入，
  // 没人点过头就往下走，错的口径会一路带到报价。
  const confirmBtn = aiTab === 'params'
    ? `<button type="button" class="inline-action" id="aiParamsConfirm" ${!has || aiBusy ? 'disabled' : ''}>`
      + `${aiData?.status?.params_confirmed ? '重新确认' : '确认参数推荐'}</button>`
    : '';
  const label = `${has ? '重新生成' : '生成'}${AI_TABS[aiTab]}`;
  // 参数表始终可填：它的行是报价字典定死的，模型没推出来的那些正是要人工补的。
  // 藏在「编辑」后面的话，一旦模型一条都没给，按钮连出现的机会都没有。
  const alwaysEditable = aiTab === 'params';
  host.innerHTML =
    `<button type="button" class="inline-action primary start-parse-btn" id="aiGenerate" ${aiBusy ? 'disabled' : ''}>`
    + (aiBusy ? `<span class="parse-spinner" aria-hidden="true"></span><span>${esc(label)}中…</span>` : esc(label))
    + `</button>`
    + `<button type="button" class="inline-action" id="aiEdit" ${alwaysEditable || !has || aiEditing[aiTab] || aiBusy ? 'hidden' : ''}>编辑</button>`
    + `<button type="button" class="inline-action save" id="aiSave" ${alwaysEditable || aiEditing[aiTab] ? '' : 'hidden'} ${aiBusy ? 'disabled' : ''}>保存参数</button>`
    + confirmBtn
    + (aiTab === 'cost' ? `<label class="inline-analysis-qty"><span>批量</span><input id="aiCostQty" type="number" min="1" value="${aiPlan().quantity || 1}" /></label>` : '');
  $ai('aiGenerate').onclick = () => aiGenerate(aiTab);
  const paramsConfirm = $ai('aiParamsConfirm');
  if (paramsConfirm) paramsConfirm.onclick = () => aiConfirmParams();
  const edit = $ai('aiEdit');
  if (edit) edit.onclick = () => { aiEditing[aiTab] = true; aiRender(); };
  const save = $ai('aiSave');
  if (save) save.onclick = () => aiSaveEdits(aiTab);
  if (alwaysEditable) save.textContent = '保存参数';
}

function aiGenerate(tab) {
  const form = new FormData();
  const note = $ai('aiRequirement')?.value.trim() || '';
  if (note) form.append('note', note);
  const quantity = tab === 'cost'
    ? Math.max(1, parseInt($ai('aiCostQty')?.value, 10) || aiPlan().quantity || 1) : 0;
  return aiPost(tab, { form, quantity });
}

// --------------------------------------------------------------------------- 面板：整合图纸
function aiRenderDrawings() {
  const plan = aiPlan();
  const parts = aiParts;
  let html = `<section class="inline-card"><div class="inline-card-title">已上传的整合图纸</div>`;
  if (!(plan.drawings || []).length) {
    html += `<div class="inline-empty">还没有整合图纸。装配图不是必需的 —— 没有它也能按 2.1 的零件推参数，`
      + `但连接关系只能靠零件角色推断，结果会明显更粗。</div>`;
  } else {
    plan.drawings.forEach(drawing => {
      const href = `${aiUrl(`/drawings/${encodeURIComponent(drawing.filename)}`)}`;
      html += `<div class="ai-drawing"><a href="${aiAttr(href)}" target="_blank" rel="noopener">${esc(drawing.filename)}</a>`
        + `<small>${esc(drawing.uploaded_at || '')}${drawing.note ? ` · ${esc(drawing.note)}` : ''}</small>`
        + `<button type="button" class="inline-action" data-ai-drop="${aiAttr(drawing.filename)}">移除</button></div>`;
    });
  }
  html += `</section>`;

  html += `<section class="inline-card"><div class="inline-card-title">来自 2.1 的零件（参数推荐的另一路输入）</div>`;
  if (!parts.length) {
    html += `<div class="inline-warn">⚠ 尚未拿到 2.1 的零件清单，请先完成图纸解析。</div>`;
  } else {
    html += `<div class="inline-totals"><span><strong>${parts.length}</strong>个零件</span>`
      + `<span><strong>${parts.filter(part => part.hasCost).length}</strong>个已测算成本</span></div>`;
    parts.forEach(part => {
      html += `<div class="inline-cov-row ${part.hasCost ? 'reused' : 'missing'}">`
        + `<span class="inline-cov-no">${esc(part.part_id)}</span>`
        + `<span class="inline-cov-name">${esc(part.name)} ×${part.quantity || 1}</span>`
        + `<span class="inline-cov-code${part.hasCost ? '' : ' new'}">`
        + (part.hasCost ? `单件 ${aiMoney(part.unitCost)} 元` : '未测算成本') + `</span></div>`;
    });
    if (parts.some(part => !part.hasCost)) {
      html += `<div class="inline-warn">⚠ 有零件在 2.1 还没做成本测算。整机成本会缺这几项的底价，`
        + `模型只能估 —— 建议先回 2.1 把它们算完。</div>`;
    }
  }
  return html + `</section>`;
}

// --------------------------------------------------------------------------- 面板：参数推荐
function aiRenderParams() {
  const params = aiPlan().params;
  if (!params) {
    return `<div class="inline-empty">尚未生成整机参数。点击上方「生成参数推荐」，`
      + `平台会把<strong>你写的整合需求 + 已上传的整合图纸 + 2.1 已确认的零件</strong>一起交给模型，`
      + `按<strong>报价成品参数字典</strong>（亿纬锂能 DA 梳理 · 产品技术参数）逐项推荐整机参数，`
      + `并给出零件间连接关系与整机 BOM。</div>`;
  }
  const editing = true;   // 参数表一直可填，见 aiRenderActions 的 alwaysEditable
  let html = `<section class="inline-card"><div class="inline-card-title">整机概览</div>`;
  html += `<div class="inline-row"><b>整机</b>${esc(params.assembly_name || '—')}</div>`;
  const checklist = aiData?.param_checklist;
  if (checklist) html += `<div class="inline-row"><b>产品族</b>${esc(checklist.family_name)}`
    + `<small class="ai-hint">（决定报价要哪些成品参数）</small></div>`;
  if (params.assembly_category) html += `<div class="inline-row"><b>库内类别</b>${esc(params.assembly_category)}<small class="ai-hint">（用来召回库内组装工艺路线）</small></div>`;
  if (params.material_spec) html += `<div class="inline-row"><b>整机层耗材</b>${esc(params.material_spec)}</div>`;
  if (params.summary) html += `<div class="inline-row"><b>整合思路</b>${esc(params.summary)}</div>`;
  html += `</section>`;

  html += aiQuoteParamCard(checklist, editing);
  html += aiExtraParamCard(params, checklist, editing);

  html += `<section class="inline-card"><div class="inline-card-title">零件间连接</div>`;
  if (!(params.interfaces || []).length) html += `<div class="inline-hint">未识别出连接关系。</div>`;
  (params.interfaces || []).forEach(row => {
    html += `<div class="inline-step"><div class="inline-sno">⇄</div><div class="inline-sbody">`
      + `<div class="inline-step-title">${esc(row.name)}`
      + (row.method ? `<span class="inline-type">${esc(row.method)}</span>` : '') + `</div>`
      + `<div class="inline-step-grid">`
      + (row.parts?.length ? `<div><span class="inline-key">零件:</span> ${esc(row.parts.join('、'))}</div>` : '')
      + (row.spec ? `<div><span class="inline-key">规格:</span> ${esc(row.spec)}</div>` : '')
      + (row.control ? `<div><span class="inline-key">控制:</span> ${esc(row.control)}</div>` : '')
      + `</div>` + (row.note ? `<div class="inline-dep">${esc(row.note)}</div>` : '')
      + `</div></div>`;
  });
  html += `</section>`;

  html += `<section class="inline-card"><div class="inline-card-title">整机 BOM（单台用量）</div>`
    + `<div class="inline-cost-table-wrap"><table class="inline-cost-table"><thead><tr>`
    + `<th>零件编号</th><th>名称</th><th>用量</th><th>作用</th><th>来源</th></tr></thead><tbody>`;
  (params.part_refs || []).forEach(row => {
    html += `<tr><td>${esc(row.part_id || '（新增）')}</td><td>${esc(row.name)}</td>`
      + `<td class="number">${row.quantity || 1}</td><td class="source">${esc(row.role || '')}</td>`
      + `<td><span class="inline-cat-tag">${esc(row.source || '')}</span></td></tr>`;
  });
  html += `</tbody></table></div></section>`;
  return html + aiOpenQuestionsCard(params.assumptions, params.open_questions);
}

/*
 * 报价成品参数表。这张表的**行是字典定的**（亿纬锂能 DA 梳理 · 产品技术参数），
 * 不是模型输出多少就显示多少 —— 报价那头按字段取数，少一项就是报价单上一个空格，
 * 只列"模型给了什么"的话，缺了哪几项永远看不出来。
 */
function aiQuoteParamCard(checklist, editing) {
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
    group.fields.forEach(field => { html += aiQuoteParamRow(field, editing); });
  });
  return html + `</tbody></table></div></section>`;
}

function aiQuoteParamRow(field, editing) {
  const flag = field.required && !field.filled && !field.generated ? ' ai-missing' : '';
  // 平台生成项（成品编码）不给输入框：这一格由「写入数据库」产生 92022xxx，
  // 手填一个号进去，报价那边按它去匹配规则只会匹配到不存在的产品。
  if (field.generated) {
    const label = `<code>${esc(field.code)}</code>`;
    // 没写过库却有值 = 早先被人工/模型填进来的号，主数据里并不存在。
    // 报价按成品编码匹配定价规则，拿这种号过去只会匹配到一个不存在的产品。
    const unverified = field.filled && !aiData?.status?.has_material_code;
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
    return `<tr class="${flag.trim()}" data-ai-qp="${aiAttr(field.code)}" data-ai-name="${aiAttr(field.name)}" data-ai-unit="${aiAttr(field.unit)}">`
      + `<td>${label}</td><td>${name}${hint ? `<small class="ai-hint">${hint}</small>` : ''}</td>`
      + `<td><input data-f="value" value="${aiAttr(field.value)}" placeholder="${aiAttr(field.options[0] || '')}"/>${flags}</td>`
      + `<td>${esc(field.unit)}</td>`
      + `<td><input data-f="basis" value="${aiAttr(field.basis || '')}"/></td>`
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
function aiExtraParamCard(params, checklist, editing) {
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
      html += `<tr data-ai-xp="${index}"><td><input data-f="name" value="${aiAttr(row.name || '')}"/></td>`
        + `<td><input data-f="value" value="${aiAttr(row.value || '')}"/></td>`
        + `<td><input data-f="unit" value="${aiAttr(row.unit || '')}"/></td>`
        + `<td><input data-f="basis" value="${aiAttr(row.basis || '')}"/></td>`
        + `<td><input data-f="source" value="${aiAttr(row.source || '')}"/></td>`
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

/* --------------------------------------------------------------- 面板：整合参数
 * 本步最后一个环节，也是唯一一个不调模型的：报价测算单按 DA 字段取数，
 * 前面几步推出来的参数难免有缺口，这里逐项收口，缺的人工补上再确认。
 */
function aiRenderFinalize() {
  const checklist = aiData?.param_checklist;
  const state = aiData?.status || {};
  const plan = aiPlan();
  if (!checklist) {
    return `<div class="inline-empty">还没有整机参数。请先完成「参数推荐」，`
      + `再回到这一步把报价要的字段补齐。</div>`;
  }
  const stat = checklist.summary;
  const missing = state.required_missing || 0;
  let html = `<section class="inline-card"><div class="inline-card-title">交给报价前的收口</div>`
    + `<div class="inline-hint">报价测算单上的成品行按下面这些 DA 字段取数。`
    + `确认之后，「确认工艺并发送至报价」会把它们连同成品编码与成本一起带回报价。</div>`
    + `<div class="inline-totals"><span><strong>${stat.filled}</strong>/${stat.total} 已给出</span>`
    + `<span><strong>${stat.required_filled}</strong>/${stat.required_total} 报价必填</span>`
    + `<span class="total">${plan.params_final ? '已确认' : missing ? `还缺 ${missing} 项` : '待确认'}</span></div>`;
  if (missing) {
    html += `<div class="inline-warn">⚠ 还有 ${missing} 项报价必填参数没有值。`
      + `可以直接在下表「值」列里补填，填完点上方「保存补填」，都齐了再点「确认参数已齐」。`
      + `<b>没确认之前不能发送至报价</b> —— 缺的那几格到了报价那头就是空白。</div>`;
  } else if (plan.params_final) {
    html += `<div class="inline-row"><b>已确认</b>${esc(plan.params_final_by || '')}`
      + ` · ${esc(plan.params_final_at || '')}</div>`;
  } else {
    html += `<div class="inline-hint">必填项都有值了。核对无误后点「确认参数已齐」。</div>`;
  }
  html += `</section>`;
  html += aiQuoteParamCard(checklist, true);
  html += aiExtraParamCard(plan.params, checklist, false);
  return html;
}

/** 收口这一步只回传「值」：依据/来源这些由后端按"人工补填"统一标注。 */
function aiCollectFinalizeValues() {
  const values = {};
  document.querySelectorAll('[data-ai-qp]').forEach(row => {
    const input = row.querySelector('[data-f="value"]');
    if (!input) return;
    values[row.dataset.aiQp] = { value: input.value.trim() };
  });
  return values;
}

/** 确认「参数推荐」这一环节。缺口不拦（型号未定的方案也要能往下走），但要说出来。 */
async function aiConfirmParams() {
  if (aiBusy) return;
  aiBusy = true;
  aiRenderActions();
  try {
    aiData = await api(aiUrl('/params/confirm'), { method: 'POST' });
    const missing = aiData?.status?.required_missing || 0;
    aiStatus('参数推荐已确认');
    aiToast('参数推荐已确认');
    aiSay(missing
      ? `参数推荐已确认。注意还有 ${missing} 项报价必填的成品参数没有值，`
        + `到第五步「整合参数」可以用「智能补全」给建议，或直接手填。`
      : '参数推荐已确认，可以继续排组装工艺了。');
  } catch (error) {
    aiStatus(`确认失败：${error.message}`, true);
    aiToast(error.message || '确认失败', true);
  } finally {
    aiBusy = false;
    aiRender();
  }
}

/* 智能补全：只补还缺的格子，给的是**建议**不是结论。
   结果直接填进表格的输入框并标出来，由人逐项过目后再点「保存补填」——
   补全里必然混着靠常识凑的值，直接落库的话报价那头分不清哪些是算出来的、哪些是猜的。 */
async function aiAutofill() {
  if (aiBusy) return;
  aiBusy = true;
  aiRenderActions();
  const card = aiProcessCard('整合参数 · 智能补全');
  aiStatus('智能补全中…');
  try {
    const form = new FormData();
    const note = $ai('aiRequirement')?.value.trim() || '';
    if (note) form.append('note', note);
    const submitted = await api(aiUrl('/params/autofill'), { method: 'POST', body: form });
    const result = await aiPollTask(submitted.task_id, card);
    const fills = result?.fills || [];
    const unresolved = result?.unresolved || [];
    aiBusy = false;
    aiRender();                       // 先把表格画回来，再往输入框里填
    const applied = aiApplyFills(fills);
    card.done(true);
    aiStatus(`智能补全给出 ${applied} 项建议`);
    aiSay(applied
      ? `已为 ${applied} 项参数填入建议值（表格里标了「AI 建议」）。`
        + `**这是建议不是结论** —— 请逐项核对，改完点「保存补填」才会写进参数表。`
        + (unresolved.length ? `\n另有 ${unresolved.length} 项确实推不出来：${unresolved.join('、')}，需要人工确定。` : '')
      : `没有可以推出来的参数${unresolved.length ? `：${unresolved.join('、')} 都需要人工确定。` : '。'}`);
  } catch (error) {
    card.done(false, error.message || '失败');
    aiStatus(`智能补全失败：${error.message}`, true);
    aiToast(error.message || '智能补全失败', true);
    aiBusy = false;
    aiRender();
  }
}

/** 把建议值填进表格的输入框，并在行上标出来（不写库）。返回填了几项。 */
function aiApplyFills(fills) {
  let applied = 0;
  fills.forEach(fill => {
    const row = document.querySelector(`[data-ai-qp="${CSS.escape(fill.code)}"]`);
    const input = row && row.querySelector('[data-f="value"]');
    if (!input || input.value.trim()) return;   // 已经有值的格子不覆盖
    input.value = fill.value;
    const basis = row.querySelector('[data-f="basis"]');
    if (basis && !basis.value.trim()) basis.value = fill.basis || 'AI 建议';
    row.classList.add('ai-suggested');
    const cell = input.parentElement;
    if (cell && !cell.querySelector('.ai-sug')) {
      cell.insertAdjacentHTML('beforeend',
        `<span class="ai-sug">AI 建议 · 置信度 ${Math.round((fill.confidence ?? 0.5) * 100)}%</span>`);
    }
    applied += 1;
  });
  return applied;
}

async function aiFinalize(confirm) {
  if (aiBusy) return;
  aiBusy = true;
  aiRenderActions();
  aiStatus(confirm ? '确认整合参数…' : '保存补填…');
  try {
    aiData = await api(aiUrl('/params/finalize'), {
      method: 'POST',
      body: JSON.stringify({ values: aiCollectFinalizeValues(), confirm: !!confirm }),
    });
    aiStatus(confirm ? '整合参数已确认' : '补填已保存');
    if (confirm) {
      aiSay('整合参数已确认，报价必填的成品参数都有值了。'
        + '现在可以点左侧「确认工艺并发送至报价」，参数会跟着一起回到报价那边。');
    }
    aiToast(confirm ? '整合参数已确认' : '已保存');
  } catch (error) {
    aiStatus(`${confirm ? '确认' : '保存'}失败：${error.message}`, true);
    aiToast(error.message || '操作失败', true);
  } finally {
    aiBusy = false;
    aiRender();
  }
}

function aiCollectParams() {
  const params = JSON.parse(JSON.stringify(aiPlan().params || {}));
  params.params = params.params || [];
  // 补充参数按下标就地改
  document.querySelectorAll('[data-ai-xp]').forEach(row => {
    const target = params.params[Number(row.dataset.aiXp)];
    if (!target) return;
    row.querySelectorAll('[data-f]').forEach(input => {
      target[input.dataset.f] = input.value.trim() || null;
    });
    target.name = target.name || '未命名参数';
    target.value = target.value || '';
  });
  // 报价字典行按 param_code 找；字典里有、参数表里还没有的，填了值才新建一行 ——
  // 空行也建的话，一次编辑就会往参数表里塞进十几条空参数。
  const byCode = new Map();
  params.params.forEach(row => {
    if (row.param_code && !byCode.has(row.param_code)) byCode.set(row.param_code, row);
  });
  document.querySelectorAll('[data-ai-qp]').forEach(row => {
    const read = field => row.querySelector(`[data-f="${field}"]`)?.value.trim() || '';
    const value = read('value');
    let target = byCode.get(row.dataset.aiQp);
    if (!target) {
      if (!value) return;
      target = {
        name: row.dataset.aiName, value: '', unit: row.dataset.aiUnit || null,
        param_code: row.dataset.aiQp, category: null, basis: null,
        source: '人工填写', confidence: 0.9,
      };
      params.params.push(target);
      byCode.set(row.dataset.aiQp, target);
    }
    target.value = value;
    target.basis = read('basis') || null;
  });
  return params;
}

// --------------------------------------------------------------------------- 面板：组装工艺
function aiRenderProcess() {
  const plan = aiPlan().process;
  if (!plan) {
    return `<div class="inline-empty">尚未生成组装工艺。平台会先按整机类别检索企业工艺库里的<strong>组装路线模板</strong>，`
      + `再让模型在库内工序上排产 —— 工序编号与标准工时都来自库，不是模型自己编的。</div>`;
  }
  const validation = aiData?.process_validation || {};
  const editing = aiEditing.process;
  const steps = plan.steps || [];
  let html = `<div class="inline-process-stack"><section class="inline-card"><div class="inline-card-title">工序明细</div>`;
  if (!steps.length) {
    html += `<div class="inline-warn">⚠ 本次没有排出任何组装工序。点上方「编辑」后可以<b>逐道手工添加</b>，`
      + `也可以补充整合需求或先完善参数推荐后重新生成。</div>`;
  }
  html += `<div class="inline-steps">`;
  steps.forEach((step, index) => { html += aiProcessStep(step, index, editing); });
  html += `</div>`;
  // 编辑态给「添加工序」：原来编辑只能改已有的行，模型一道都没排出来时就无从下手了。
  if (editing) {
    html += `<button type="button" class="inline-action" id="aiAddStep">＋ 添加工序</button>`;
  }
  html += `</section>`;

  html += `<section class="inline-card"><div class="inline-card-title">装配方案</div>`;
  if (plan.blank) html += `<div class="inline-row"><b>来料构成</b>${esc(plan.blank)}</div>`;
  if (plan.summary) html += `<div class="inline-row"><b>思路</b>${esc(plan.summary)}</div>`;
  html += `<div class="inline-totals"><span><strong>${validation.step_count ?? (plan.steps || []).length}</strong>工序数</span>`;
  if (validation.total_duration_min != null) html += `<span><strong>${validation.total_duration_min}</strong>合计工时(分钟/台)</span>`;
  html += `</div>`;
  (validation.warnings || []).forEach(warning => { html += `<div class="inline-warn">⚠ ${esc(warning)}</div>`; });
  (plan.rule_warnings || []).forEach(warning => { html += `<div class="inline-warn">⚠ ${esc(warning)}</div>`; });
  html += `</section>`;
  html += aiCoverageCard(aiData?.process_coverage);
  html += aiProcessLibraryCard(aiData?.process_lookup);
  html += aiOpenQuestionsCard(null, plan.open_questions);
  return html + `</div>`;
}

function aiProcessStep(step, index, editing) {
  if (editing) {
    const options = Object.keys(AI_TYPE_LABEL).map(type =>
      `<option value="${type}"${type === step.type ? ' selected' : ''}>${AI_TYPE_LABEL[type]}</option>`).join('');
    return `<div class="inline-step" data-ai-step data-i="${index}"><div class="inline-sno">${esc(step.step_no)}</div>`
      + `<div class="inline-sbody"><div class="inline-edit-grid">`
      + `<label>工序号</label><input data-f="step_no" type="number" value="${aiAttr(step.step_no)}"/>`
      + `<label>名称</label><input data-f="name" value="${aiAttr(step.name || '')}"/>`
      + `<label>类型</label><select data-f="type">${options}</select>`
      + `<label>内容</label><textarea data-f="description" rows="2">${esc(step.description || '')}</textarea>`
      + `<label>设备</label><input data-f="equipment" value="${aiAttr(step.equipment || '')}"/>`
      + `<label>工装</label><input data-f="fixture" value="${aiAttr(step.fixture || '')}"/>`
      + `<label>质量要求</label><input data-f="quality" value="${aiAttr(step.quality || '')}"/>`
      + `<label>工时(分)</label><input data-f="duration_min" type="number" step="0.1" value="${step.duration_min != null ? aiAttr(step.duration_min) : ''}"/>`
      + `</div><button type="button" class="inline-action ai-row-del" data-ai-del-step="${index}">删除本道工序</button>`
      + `</div></div>`;
  }
  const kv = (key, value) => value ? `<div><span class="inline-key">${key}:</span> ${esc(value)}</div>` : '';
  return `<div class="inline-step"><div class="inline-sno">${esc(step.step_no)}</div><div class="inline-sbody">`
    + `<div class="inline-step-title">${esc(step.name || '')}`
    + `<span class="inline-type">${AI_TYPE_LABEL[step.type] || esc(step.type)}</span></div>`
    + (step.description ? `<div class="inline-description">${esc(step.description)}</div>` : '')
    + `<div class="inline-step-grid">${kv('设备', step.equipment)}${kv('工装', step.fixture)}${kv('质量', step.quality)}`
    + (step.duration_min != null ? `<div><span class="inline-key">工时:</span> ${esc(step.duration_min)} 分</div>` : '')
    + `</div>`
    + ((step.depends_on || []).length ? `<div class="inline-dep">前序依赖：工序 ${step.depends_on.join('、')}</div>` : '')
    + (step.note ? `<div class="inline-dep">${esc(step.note)}</div>` : '')
    + `</div></div>`;
}

function aiCollectProcess() {
  const plan = JSON.parse(JSON.stringify(aiPlan().process || {}));
  plan.steps = plan.steps || [];
  document.querySelectorAll('[data-ai-step][data-i]').forEach(row => {
    const step = plan.steps[Number(row.dataset.i)];
    if (!step) return;
    row.querySelectorAll('[data-f]').forEach(input => {
      const field = input.dataset.f;
      const value = input.value;
      if (field === 'step_no') step.step_no = parseInt(value, 10) || step.step_no;
      else if (field === 'duration_min') step.duration_min = value.trim() === '' ? null : parseFloat(value);
      else step[field] = value.trim() === '' ? null : value;
    });
  });
  plan.steps.sort((a, b) => a.step_no - b.step_no);
  return plan;
}

/* 增删行的通用套路：先把界面上已经填的内容收回来，再改结构，最后重画。
   顺序反过来的话，点一下「添加工序」就会把这一轮手打的字全丢掉。 */
function aiMutateProcess(mutate) {
  const plan = aiCollectProcess();
  mutate(plan);
  aiData.plan.process = plan;
  aiRender();
}

function aiAddProcessStep() {
  aiMutateProcess(plan => {
    const last = plan.steps.length ? Math.max(...plan.steps.map(s => Number(s.step_no) || 0)) : 0;
    plan.steps.push({
      step_no: last + 10, name: '', type: 'assembly', description: '',
      equipment: '', fixture: '', quality: '', duration_min: null,
      depends_on: last ? [last] : [], note: '', confidence: 1,
    });
  });
}

function aiCoverageCard(coverage) {
  if (!coverage || !coverage.summary) return '';
  const { reused = [], missing = [] } = coverage;
  const row = (item, kind) =>
    `<div class="inline-cov-row ${kind}"><span class="inline-cov-no">${esc(item.step_no)}</span>`
    + `<span class="inline-cov-name">${esc(item.name || '')}</span>`
    + (kind === 'reused' ? `<span class="inline-cov-code">${esc(item.step_code)}</span>`
      : `<span class="inline-cov-code new">需新建</span>`) + `</div>`;
  let html = `<section class="inline-card"><div class="inline-card-title">工艺库覆盖</div>`
    + `<div class="inline-totals"><span><strong>${reused.length}</strong>库内已有</span>`
    + `<span><strong>${missing.length}</strong>缺失待建</span></div>`;
  html += reused.length ? reused.map(item => row(item, 'reused')).join('')
    : `<div class="inline-hint">没有可沿用的库内组装工序。</div>`;
  if (missing.length) {
    html += `<div class="inline-cov-split">以下工序库内没有，需新建工艺文件：</div>`
      + missing.map(item => row(item, 'missing')).join('');
  }
  return html + `</section>`;
}

function aiProcessLibraryCard(report) {
  if (!report) return '';
  const route = report.route;
  let html = `<section class="inline-card inline-library"><div class="inline-card-title">库内依据 · 工艺库</div>`;
  if (route) {
    html += `<div class="inline-row"><b>路线模板</b>${esc(route.route_code)} ${esc(route.name || '')}</div>`
      + `<div class="inline-hint">${esc(route.source || '')} · ${(route.steps || []).length} 道工序</div>`;
    (route.steps || []).forEach(step => {
      html += `<div class="inline-lib-step"><code>${esc(step.step_code || '')}</code> ${esc(step.name || '')}`
        + `<small>准备 ${step.setup_min ?? '—'} min · 设备类 ${esc(step.equipment_class || '未指定')}</small></div>`;
    });
  } else {
    html += `<div class="inline-warn">⚠ 库内无适用的组装路线模板，本次工艺为全新编制</div>`;
  }
  (report.notes || []).forEach(note => { html += `<div class="inline-hint">${esc(note)}</div>`; });
  return html + `</section>`;
}

// --------------------------------------------------------------------------- 面板：成本测算
function aiRenderCost() {
  const analysis = aiPlan().cost;
  if (!analysis) {
    return `<div class="inline-empty">尚未生成整机成本。平台会以 <strong>2.1 各零件已测算的单件成本</strong>为底，`
      + `按组装工艺逐道工序叠加人工与设备费率，再套用库内计价系数 —— 零件成本原样引用，不重新估价。</div>`;
  }
  const summary = aiData?.cost_summary || {};
  const editing = aiEditing.cost;
  const currency = summary.currency || analysis.currency || 'CNY';
  let html = `<div class="inline-cost-content"><section class="inline-card"><div class="inline-card-title">整机成本概览</div>`
    + `<div class="inline-cost-total"><strong>${aiMoney(summary.computed_total)}</strong>`
    + `<span>元 / 台（${esc(currency)}）</span><em>核算批量 ${analysis.quantity || 1} 台</em></div>`;
  if (analysis.summary) html += `<div class="inline-row">${esc(analysis.summary)}</div>`;
  const byCategory = summary.by_category || {};
  const categories = Object.keys(byCategory).sort((a, b) => byCategory[b] - byCategory[a]);
  const max = Math.max(1, ...Object.values(byCategory).map(Number));
  if (categories.length) {
    html += `<div class="inline-cat-bars">`;
    categories.forEach(category => {
      html += `<div class="inline-cat-bar"><div><span>${esc(AI_CAT_LABEL[category] || category)}</span>`
        + `<span>${aiMoney(byCategory[category])} 元</span></div>`
        + `<i><b style="width:${Math.floor(Number(byCategory[category]) / max * 100)}%"></b></i></div>`;
    });
    html += `</div>`;
  }
  (summary.warnings || []).forEach(warning => { html += `<div class="inline-warn">⚠ ${esc(warning)}</div>`; });
  html += `</section>`;
  html += aiCostModelCard(analysis.cost_model);

  html += `<section class="inline-card"><div class="inline-card-title">成本明细</div>`;
  if (!(analysis.items || []).length) {
    html += `<div class="inline-warn">⚠ 本次没有算出任何成本明细。点上方「编辑」可以<b>手工添加材料行</b>；`
      + `人工/制造费用/加工费用会按材料成本自动推导，不用自己填。</div>`;
  }
  html += `<div class="inline-cost-table-wrap"><table class="inline-cost-table"><thead><tr>`
    + `<th>类别</th><th>分项</th><th>计算依据</th><th>数量</th><th>单位</th><th>单价</th><th>金额(元)</th><th>来源</th>`
    + (editing ? `<th></th>` : '')
    + `</tr></thead><tbody>`;
  (analysis.items || []).forEach((item, index) => {
    if (editing) {
      const options = Object.keys(AI_CAT_LABEL).map(cat =>
        `<option value="${cat}"${cat === item.category ? ' selected' : ''}>${AI_CAT_LABEL[cat]}</option>`).join('');
      html += `<tr data-ai-cost data-i="${index}"><td><select data-f="category">${options}</select></td>`
        + `<td><input data-f="name" value="${aiAttr(item.name || '')}"/></td>`
        + `<td><input data-f="basis" value="${aiAttr(item.basis || '')}"/></td>`
        + `<td><input data-f="quantity" type="number" step="any" value="${item.quantity != null ? aiAttr(item.quantity) : ''}"/></td>`
        + `<td><input data-f="unit" value="${aiAttr(item.unit || '')}"/></td>`
        + `<td><input data-f="unit_price" type="number" step="any" value="${item.unit_price != null ? aiAttr(item.unit_price) : ''}"/></td>`
        + `<td><input data-f="amount" type="number" step="any" value="${item.amount != null ? aiAttr(item.amount) : ''}"/></td>`
        + `<td><input data-f="source" value="${aiAttr(item.source || '')}"/></td>`
        + `<td><button type="button" class="inline-action ai-row-del" data-ai-del-cost="${index}">删除</button></td></tr>`;
    } else {
      html += `<tr><td><span class="inline-cat-tag">${esc(AI_CAT_LABEL[item.category] || item.category)}</span></td>`
        + `<td>${esc(item.name)}</td><td class="source">${esc(item.basis || '')}</td>`
        + `<td class="number">${item.quantity != null ? esc(item.quantity) : ''}</td><td>${esc(item.unit || '')}</td>`
        + `<td class="number">${item.unit_price != null ? aiMoney(item.unit_price) : ''}</td>`
        + `<td class="number amount">${item.amount != null ? aiMoney(item.amount) : ''}</td>`
        + `<td class="source">${esc(item.source || '')}</td></tr>`;
    }
  });
  html += `</tbody></table></div>`
    + (editing
        ? `<button type="button" class="inline-action" id="aiAddCost">＋ 添加成本项</button>`
          + `<div class="inline-hint">提示：保存后平台会按数量×单价重算金额，`
          + `并按企业口径重新推导人工/制费/加工与合计。</div>`
        : '')
    + `</section>`;
  html += aiCostLibraryCard(aiData?.cost_lookup);
  if ((analysis.search_sources || []).length) {
    html += `<section class="inline-card"><div class="inline-card-title">检索来源（可点击核查）</div>`;
    analysis.search_sources.forEach(source => {
      html += `<div class="inline-source">🔗 <a href="${aiAttr(source.url)}" target="_blank" rel="noopener">${esc(source.title || source.url)}</a></div>`;
    });
    html += `</section>`;
  }
  return html + aiOpenQuestionsCard(analysis.assumptions, analysis.open_questions) + `</div>`;
}

function aiMutateCost(mutate) {
  const analysis = aiCollectCost();
  mutate(analysis);
  aiData.plan.cost = analysis;
  aiRender();
}

function aiAddCostItem() {
  // 只让加材料行：人工/制费/加工由后端按材料成本推导，手填了也会被覆盖。
  aiMutateCost(analysis => analysis.items.push({
    category: 'material', name: '', basis: '', quantity: null, unit: '',
    unit_price: null, amount: null, source: '人工填写', confidence: 1,
  }));
}

function aiCollectCost() {
  const analysis = JSON.parse(JSON.stringify(aiPlan().cost || {}));
  analysis.items = analysis.items || [];
  document.querySelectorAll('[data-ai-cost][data-i]').forEach(row => {
    const item = analysis.items[Number(row.dataset.i)];
    if (!item) return;
    row.querySelectorAll('[data-f]').forEach(input => {
      const field = input.dataset.f;
      const value = input.value;
      if (['quantity', 'unit_price', 'amount'].includes(field)) item[field] = value.trim() === '' ? null : parseFloat(value);
      else item[field] = value.trim() === '' ? (field === 'category' ? 'other' : null) : value;
    });
  });
  return analysis;
}

/* 企业成本口径的四项。这张卡回答的是「合计这个数怎么来的」：材料逐项累加，
   另外三项由材料乘固定系数 —— 系数写在明面上，报价那头才核得动。 */
function aiCostModelCard(model) {
  if (!model) return '';
  const constants = model.constants || {};
  const rows = [
    ['材料', model.material, '明细逐项累加（数量 × 单价）'],
    ['人工', model.labor, `材料 / ${constants.tax_divisor} / ${constants.material_share} × ((1 - ${constants.material_share}) × ${constants.labor_ratio})`],
    ['制造费用', model.overhead, `材料 / ${constants.tax_divisor} / ${constants.material_share} × ((1 - ${constants.material_share}) × ${constants.overhead_ratio})`],
    ['加工费用', model.machining, `材料 / ${constants.tax_divisor} / ${constants.material_share} × ((1 - ${constants.material_share}) × ${constants.processing_ratio})`],
  ];
  let html = `<section class="inline-card"><div class="inline-card-title">成本口径 · 材料 / 人工 / 制费 / 加工</div>`
    + `<div class="inline-hint">写入物料成本配置的 material_unit_price 就是这四项的合计。</div>`
    + `<div class="inline-cost-table-wrap"><table class="inline-cost-table"><thead><tr>`
    + `<th>项目</th><th>金额(元)</th><th>计算依据</th></tr></thead><tbody>`;
  rows.forEach(([label, amount, basis]) => {
    html += `<tr><td>${esc(label)}</td><td class="number amount">${aiMoney(amount)}</td>`
      + `<td class="source">${esc(basis)}</td></tr>`;
  });
  html += `<tr class="ai-group-row"><td>合计</td><td class="number amount">${aiMoney(model.total)}</td>`
    + `<td>材料 + 人工 + 制造费用 + 加工费用</td></tr>`;
  return html + `</tbody></table></div></section>`;
}

function aiCostLibraryCard(report) {
  if (!report) return '';
  const material = report.material || {};
  const price = material.price;
  let html = `<section class="inline-card inline-library"><div class="inline-card-title">库内依据 · 成本库</div>`
    + `<div class="inline-hint">取价时点 ${esc(report.priced_at || '')} · 核算批量 ${report.quantity || 1}</div>`;
  if (price) {
    html += `<div class="inline-row"><b>整机层物料价</b>${esc(material.material_code || '')} ${esc(material.name || '')}`
      + ` — <strong>${aiMoney(price.price)}</strong> ${esc(price.currency || '')}/${esc(price.unit || '')}</div>`;
  }
  (report.rates || []).forEach(rate => {
    html += `<div class="inline-lib-step${rate.fallback ? ' extra' : ''}"><code>${esc(rate.rate_code)}</code> ${esc(rate.name || rate.rate_type)}`
      + ` — ${aiMoney(rate.value)} ${esc(rate.unit || '')}`
      + (rate.fallback ? `<small>库内无 ${esc(rate.requested_scope || '')} 作用域费率，已回退全厂通用值</small>` : '')
      + `</div>`;
  });
  if ((report.factors || []).length) {
    html += `<div class="inline-row"><b>计价系数</b>`
      + report.factors.map(factor => `${esc(factor.factor_type)}=${esc(String(factor.value))}`).join('、') + `</div>`;
  }
  (report.gaps || []).forEach(gap => { html += `<div class="inline-warn">⚠ ${esc(gap)}</div>`; });
  return html + `</section>`;
}

// --------------------------------------------------------------------------- 公共卡片
function aiOpenQuestionsCard(assumptions, questions) {
  const list = questions || [];
  const notes = assumptions || [];
  if (!list.length && !notes.length) return '';
  let html = `<section class="inline-card"><div class="inline-card-title">假设与待澄清</div>`;
  notes.forEach(item => { html += `<div class="inline-assumption">• ${esc(item)}</div>`; });
  list.forEach(question => {
    html += `<div class="inline-question">❓ ${esc(question.field)}：${esc(question.reason)}`
      + (question.guess ? `（猜测：${esc(question.guess)}）` : '') + `</div>`;
  });
  return html + `</section>`;
}

// --------------------------------------------------------------------------- 渲染入口
function aiRender() {
  const renderers = {
    drawings: aiRenderDrawings, params: aiRenderParams,
    process: aiRenderProcess, cost: aiRenderCost, finalize: aiRenderFinalize,
  };
  $ai('aiPanelTitle').textContent = AI_TABS[aiTab];
  document.querySelectorAll('#aiTabs [data-ai-tab]').forEach(button => {
    button.classList.toggle('active', button.dataset.aiTab === aiTab);
  });
  const blocked = aiBlocker(aiTab);
  const state = aiData?.status || {};
  const done = { drawings: state.drawings > 0, params: state.has_params, process: state.has_process,
                 cost: state.has_cost, finalize: state.params_final };
  document.querySelectorAll('#aiStepBody [data-ai-tab]').forEach(button => {
    button.classList.toggle('active', button.dataset.aiTab === aiTab);
    button.classList.toggle('done', Boolean(done[button.dataset.aiTab]));
  });
  $ai('aiBody').innerHTML = (blocked ? `<div class="inline-warn">⚠ ${esc(blocked)}</div>` : '') + renderers[aiTab]();
  aiRenderActions();
  aiRenderFiles();
  aiRenderOps();
  aiBindBody();
  const stat = aiData?.param_checklist?.summary;
  // 批量收进了小面板，平时看不见；把它写进图标的悬停提示，免得改过之后无从确认。
  $ai('aiQtyTip').textContent = `${aiPlan().quantity || 1} 台`;
  $ai('aiSideState').textContent =
    `图纸 ${state.drawings || 0} · 参数 ${stat ? `${stat.filled}/${stat.total}` : '—'}`
    + ` · 工艺 ${state.has_process ? '✓' : '—'} · 成本 ${state.has_cost ? '✓' : '—'}`;
  const name = aiPlan().params?.assembly_name;
  $ai('aiTitle').textContent = name ? `组装与整合 · ${name}` : '组装与整合';
}

/* ------------------------------------------------------- 两个对外动作
 * 写入数据库 / 确认工艺并发送至报价。两者都打到 tech_app，再由它回调一体化服务
 * （远程 Postgres 与报价工作流都在那一侧）。成本没算完一律不给点 —— 写进主数据的
 * 单价是要拿去报价的，宁可拦住，也不要写一个 0 进去。
 */
const AI_COST_LABEL = { material: '材料', labor: '人工', overhead: '制造费用', machining: '加工费用' };

function aiRenderOps() {
  const plan = aiPlan();
  const model = plan.cost?.cost_model;
  const box = $ai('aiOpCost');
  const nameInput = $ai('aiProductName');
  if (nameInput && !nameInput.value && plan.params?.assembly_name) {
    nameInput.value = plan.params.assembly_name;
  }
  if (model) {
    box.innerHTML = Object.keys(AI_COST_LABEL)
      .map(key => `<span><i>${AI_COST_LABEL[key]}</i>${aiMoney(model[key])}</span>`).join('')
      + `<span class="total"><i>合计</i>${aiMoney(model.total)} 元/台</span>`;
  } else {
    box.textContent = '完成成本测算后显示成品成本';
  }
  const state = aiData?.status || {};
  const ready = Boolean(model) && !aiBusy;
  // 写库只要成本；发报价还要参数收口 —— 回传给报价的就是那份参数，缺一格都不该发。
  const missing = state.required_missing || 0;
  // 成品编码只能由系统生成，但**不构成前置操作**：发送时若还没有编码，后端会顺手
  // 生成一个再发（main.py::integration_send_to_quote）。所以这两个按钮各自独立，
  // 「写入数据库」只是想单独写库时才点。
  const noCode = !(state.has_material_code ?? (plan.material_writes || []).length);
  const writeBtn = $ai('aiWriteDb');
  const quoteBtn = $ai('aiToQuote');
  writeBtn.disabled = !ready;
  quoteBtn.disabled = !ready || missing > 0;
  // 灰按钮必须自己说得出为什么灰。原来原因只写在下面那段说明里，一旦上面有
  // 「✓ 已写入主数据」之类的完成记录，说明整段被顶掉，按钮就成了一个哑的黑块。
  const why = !Boolean(model) ? '请先完成成本测算'
    : aiBusy ? '正在处理…'
    : missing > 0 ? `还差 ${missing} 项报价必填参数（见「整合参数」）`
    : '';
  quoteBtn.title = why || (noCode
    ? '会先自动生成成品编码，再把整机参数与成本一并送回报价'
    : '把整机参数、成品编码与成本一并送回报价');
  writeBtn.title = ready ? '单独写一次主数据（发送至报价时也会自动生成编码）' : '请先完成成本测算';
  const badge = $ai('aiOpsWhy');
  if (badge) {
    badge.textContent = why;
    badge.hidden = !why;
  }

  const done = [];
  (plan.material_writes || []).forEach(item => {
    done.push(`已写入主数据：<b>${esc(item.number)}</b> ${esc(item.name)}`
      + ` · 单价 ${aiMoney(item.material_unit_price)} 元 · ${esc(item.written_at || '')}`);
  });
  if (plan.quote_handoff) {
    const handoff = plan.quote_handoff;
    const whom = handoff.returned_to_sender
      ? `已退回给${esc(handoff.target_name || '发起人')}`
        + (handoff.target_role_name ? `（${esc(handoff.target_role_name)}）` : '')
        + (handoff.source_task_no ? ` · 来源任务 ${esc(handoff.source_task_no)}` : '')
      : handoff.target_role_name ? `已通知${esc(handoff.target_role_name)}` : '已推送任务';
    done.push(`已发送至报价：卡片进入第 ${handoff.next_step_no || 3} 步`
      + `「${esc(handoff.next_step_name || '定价-利润加成')}」 · ${whom}`
      + ((handoff.returned_sections || []).length
          ? ` · 整机参数已写回报价第 2 步（${handoff.returned_sections.length} 张表）` : '')
      + ` · ${esc(handoff.sent_at || '')}`);
  }
  // 「为什么还不能发」要一直说得出来 —— 早先它只在没有任何已完成动作时才显示，
  // 于是写完库之后提示条整个换成"✓ 已写入主数据"，发报价按钮灰着却没人知道为什么。
  const blockers = [];
  if (missing) {
    blockers.push(`<b>还差 ${missing} 项报价必填参数</b>，请先在「整合参数」环节补齐并确认。`);
  }
  const hint = $ai('aiOpsHint');
  hint.innerHTML = (done.length
    ? done.map(line => `<div class="ai-op-done">✓ ${line}</div>`).join('')
      + `<div style="margin-top:6px">再次点击会新建一个成品编码。</div>`
    : '写入数据库：新建成品编码，写入物料主数据与物料成本配置。<br/>'
      + '发送至报价：任务退回给当初发起「新增工艺」的人，卡片推进到「定价-利润加成」，'
      + '整机参数（含成品编码）随任务一起带回。'
      + (noCode ? '<br/>两者互不依赖：直接发送时会先自动生成一个成品编码。' : ''))
    + (blockers.length ? `<div style="margin-top:6px">${blockers.join('<br/>')}</div>` : '');
}

async function aiRunOp(kind) {
  if (aiBusy) return;
  const labels = { 'material-write': '写入数据库', 'send-to-quote': '确认工艺并发送至报价' };
  aiBusy = true;
  aiRenderOps();
  aiRenderActions();
  const card = aiProcessCard(labels[kind]);
  aiStatus(`${labels[kind]}中…`);
  try {
    const payload = { product_name: $ai('aiProductName')?.value.trim() || '' };
    const data = await api(aiUrl(`/${kind}`), { method: 'POST', body: JSON.stringify(payload) });
    aiData = data;
    if (kind === 'material-write') {
      const written = data.written || {};
      card.log([`成品编码 ${written.number}`,
        `  产品名称 ${written.name}`,
        `  材料单价（四项合计）${written.material_unit_price} 元`,
        `  已写入 ${(written.tables || []).join('、')}`]);
      aiSay(`已写入数据库：成品编码 ${written.number}「${written.name}」，`
        + `材料单价 ${written.material_unit_price} 元（材料+人工+制费+加工）。`);
    } else {
      const handoff = data.handoff || {};
      // 没写过库时后端会顺手生成一个编码再发。主数据里确实多了一行，必须说出来。
      const auto = data.auto_written;
      // 主数据写不进去（库连不上/没权限）时改用本地临时号 —— 更要说，
      // 否则人会以为已经入库了，回头对不上账。
      const fallback = data.code_fallback;
      if (auto) {
        card.log([`自动生成成品编码 ${auto.number}`,
          `  产品名称 ${auto.name}`,
          `  材料单价（四项合计）${auto.material_unit_price} 元`,
          `  已写入 ${(auto.tables || []).join('、')}`]);
      } else if (fallback) {
        card.log([`使用临时成品编码 ${fallback.number}（未写入主数据）`,
          `  原因：${fallback.reason}`,
          `  报价的定价与加价按产品行里的编码匹配，临时号照样能算；`,
          `  等业务库恢复后请点「写入数据库」补写`]);
      }
      const whom = handoff.returned_to_sender
        ? `${handoff.target_name || '发起人'}（${handoff.target_role_name || ''}）`
        : (handoff.target_role_name || '销售经理');
      card.log([`报价卡片进入第 ${handoff.next_step_no || 3} 步「${handoff.next_step_name || '定价-利润加成'}」`,
        `  ${handoff.returned_to_sender ? '已退回给' : '已通知'}${whom}`
        + (handoff.source_task_no ? `（来源任务 ${handoff.source_task_no}）` : ''),
        ...((handoff.returned_sections || []).length
            ? [`  整机参数已写回报价第 2 步：${handoff.returned_sections.join('、')}`] : [])]);
      aiSay((auto ? `已自动生成成品编码 ${auto.number}「${auto.name}」并写入主数据。\n` : '')
        + (fallback ? `⚠ 主数据暂时写不进去（${fallback.reason}），`
            + `本次改用**临时成品编码 ${fallback.number}**继续发送 —— 报价的定价与加价是按`
            + `产品行里的编码匹配的，所以那边照样算得出来；等业务库恢复后请回来点`
            + `「写入数据库」补写主数据。\n` : '')
        + `工艺已确认并发送至报价：卡片进入「${handoff.next_step_name || '定价-利润加成'}」，`
        + (handoff.returned_to_sender
            ? `任务已**退回给当初发起「新增工艺」的 ${whom}**`
            : `${whom}会在报价助手里收到这条任务`)
        + (handoff.returned_sections?.length
            ? `；整机参数（成品编码、技术参数、单件成本）已一并带回，他打开卡片就能直接定价。`
            : `。`));
    }
    card.done(true);
    aiStatus(`${labels[kind]}完成`);
  } catch (error) {
    card.done(false, error.message || '失败');
    aiStatus(`${labels[kind]}失败：${error.message}`, true);
    aiToast(error.message || `${labels[kind]}失败`, true);
  } finally {
    aiBusy = false;
    aiRender();
  }
}

function aiBindBody() {
  document.querySelectorAll('[data-ai-drop]').forEach(button => {
    button.onclick = () => aiDropDrawing(button.dataset.aiDrop);
  });
  const addStep = $ai('aiAddStep');
  if (addStep) addStep.onclick = aiAddProcessStep;
  document.querySelectorAll('[data-ai-del-step]').forEach(button => {
    button.onclick = () => aiMutateProcess(
      plan => plan.steps.splice(Number(button.dataset.aiDelStep), 1));
  });
  const addCost = $ai('aiAddCost');
  if (addCost) addCost.onclick = aiAddCostItem;
  document.querySelectorAll('[data-ai-del-cost]').forEach(button => {
    button.onclick = () => aiMutateCost(
      analysis => analysis.items.splice(Number(button.dataset.aiDelCost), 1));
  });
}

/* 右侧「任务文件」。与 2.1 同一个清单接口 —— 那边分散在各步骤里的产出（原图、技术
   文档、几何、2D 图、导出表格）都由后端 /files 汇总，前端不再各自去猜哪一步生成过
   什么。这一页在它前面多一组"本步上传的整合图纸"，后面多一段用户需求描述。

   manifest 单独缓存：aiRender() 每次重绘都要画这块，但不该每次都去打一次接口。 */
const AI_FILE_ICON = { image: '🖼', doc: '📄', model: '🧊', table: '📊' };
let aiManifest = null;

function aiFileRow(file) {
  return `<div class="oc-file"><span class="oc-file-icon" aria-hidden="true">`
    + `${AI_FILE_ICON[file.kind] || '📄'}</span><div class="oc-file-body">`
    + `<a class="oc-file-name" target="_blank" rel="noopener" href="${aiAttr(file.url)}">`
    + `${esc(file.name)}</a>`
    + (file.note ? `<div class="oc-file-note">${esc(file.note)}</div>` : '')
    + `</div></div>`;
}

function aiFileGroup(title, count, inner) {
  return `<section class="oc-file-group"><div class="oc-file-group-head"><span>${esc(title)}</span>`
    + (count == null ? '' : `<span class="oc-file-group-count">${count}</span>`)
    + `</div>${inner}</section>`;
}

async function aiLoadManifest() {
  try {
    aiManifest = await api(`/api/projects/${encodeURIComponent(aiPid)}/files`);
  } catch { aiManifest = aiManifest || { groups: [], total: 0, note: '' }; }
  aiRenderFiles();
}

function aiRenderFiles() {
  const drawings = aiPlan().drawings || [];
  const manifest = aiManifest || { groups: [], total: 0, note: '' };
  const sections = [];

  sections.push(aiFileGroup('整合图纸（本步上传）', drawings.length, drawings.length
    ? drawings.map(drawing => aiFileRow({
        kind: 'image', name: drawing.filename, note: drawing.uploaded_at || '',
        url: aiUrl(`/drawings/${encodeURIComponent(drawing.filename)}`),
      })).join('')
    : `<div class="oc-file-empty">还没有整合图纸。用输入框左侧 ＋ 上传装配图或爆炸图。</div>`));

  (manifest.groups || []).forEach(group => sections.push(
    aiFileGroup(group.title, (group.files || []).length,
                (group.files || []).map(aiFileRow).join(''))));

  // 用户需求描述：1.x/首页填的那段，和本步的「整合需求」是两回事，分开列出来。
  // 参数推荐会把两段一起喂给模型（services/integration.py::build_context），
  // 所以这一栏不是装饰 —— 它就是这一步的输入之一。
  const note = String(manifest.note || '').trim();
  const own = String(aiPlan().requirement_note || '').trim();
  sections.push(aiFileGroup('用户需求描述', null,
    (note ? `<div class="oc-file-note oc-file-text">${esc(note)}</div>` : '')
    + (own ? `<div class="oc-file-group-head" style="margin-top:8px"><span>本步整合需求</span></div>`
             + `<div class="oc-file-note oc-file-text">${esc(own)}</div>` : '')
    || `<div class="oc-file-empty">还没有需求描述。可在左侧对话框「整合需求」里补充。</div>`));

  $ai('aiFilesCount').textContent = (drawings.length + (manifest.total || 0)) || '—';
  $ai('aiFilesBody').innerHTML = sections.join('');
}

async function aiSaveEdits(tab) {
  const payloads = {
    params: () => ['/params', aiCollectParams(), '整机参数已保存'],
    process: () => ['/process', aiCollectProcess(), '组装工艺已保存'],
    cost: () => ['/cost', aiCollectCost(), '整机成本已保存'],
  };
  const [path, payload, message] = payloads[tab]();
  aiEditing[tab] = false;
  if (!await aiPut(path, payload, message)) aiEditing[tab] = true;
}

async function aiDropDrawing(filename) {
  try {
    aiData = await api(aiUrl(`/drawings/${encodeURIComponent(filename)}`), { method: 'DELETE' });
    aiRender();
    aiToast('已移除该整合图纸');
  } catch (error) { aiToast(error.message || '移除失败', true); }
}

async function aiUploadDrawings(files) {
  if (!files.length) return;
  const form = new FormData();
  [...files].forEach(file => form.append('files', file));
  form.append('note', $ai('aiRequirement')?.value.trim() || '');
  aiStatus('上传整合图纸…');
  try {
    aiData = await api(aiUrl('/drawings'), { method: 'POST', body: form });
    aiRender();
    aiSay(`已收到 ${files.length} 张整合图纸：${[...files].map(file => file.name).join('、')}。`
      + `接下来可以点「生成参数推荐」。`);
    aiStatus('整合图纸已上传');
  } catch (error) {
    aiStatus(`上传失败：${error.message}`, true);
    aiToast(error.message || '上传失败', true);
  }
}

/** 一键跑完三个环节。任一环节失败就停在那里 —— 后面两个本来就依赖它的结果。 */
async function aiRunAll() {
  const note = $ai('aiRequirement').value.trim();
  await api(aiUrl(''), {
    method: 'PUT',
    body: JSON.stringify({ requirement_note: note, quantity: aiPlan().quantity || 1 }),
  }).then(data => { aiData = data; }).catch(() => {});
  aiSay('开始整合分析：参数推荐 → 组装工艺 → 成本测算，三步依次进行。');
  for (const step of ['params', 'process', 'cost']) {
    await aiGenerate(step);
    if (!aiData?.status?.[{ params: 'has_params', process: 'has_process', cost: 'has_cost' }[step]]) {
      aiSay(`「${AI_TABS[step]}」没有产出结果，后面两步依赖它，先停在这里。`);
      return;
    }
  }
  // 第五个环节不代跑：它要人核对、补填、按下确认，自动跑没有意义。
  aiTab = 'finalize';
  aiRender();
  const missing = aiData?.status?.required_missing || 0;
  aiSay(missing
    ? `三个环节都已完成。最后一步「整合参数」：还有 ${missing} 项报价必填的成品参数没有值，`
      + `在这一页逐项补填后点「确认参数已齐」，才能发送至报价。`
    : '三个环节都已完成，报价必填的成品参数也齐了。到「整合参数」核对一遍并点「确认参数已齐」，'
      + '就可以发送至报价了。');
}

/** 核算批量：导航条收窄之后它进了图标旁边的小面板，开合状态跟着 aria-expanded 走。 */
function aiToggleQty(force) {
  const pop = $ai('aiQtyPop');
  const open = force === undefined ? pop.hidden : force;
  pop.hidden = !open;
  $ai('aiQtyBtn').setAttribute('aria-expanded', String(open));
  if (open) {
    // 点空白处收起。与模型设置弹层是同一套做法，两个面板互不打架。
    setTimeout(() => document.addEventListener('click', aiCloseQty), 0);
    $ai('aiQuantity').focus();
  }
}
function aiCloseQty() {
  aiToggleQty(false);
  document.removeEventListener('click', aiCloseQty);
}

/* ------------------------------------------------------- 模型参数设置
 * 面板实现共用 llm-settings-panel.js（首页、2.1 Agent 小窗、这里都是同一份）——
 * 三处各写一套表单正是之前"首页改的和智能体里显示的对不上"的根因。
 * 这里只负责把它挂进一个贴着药丸的弹层，并把当前语言模型显示在药丸上。
 */
let aiPop = null;

function aiClosePop() {
  if (!aiPop) return;
  aiPop.remove();
  aiPop = null;
  document.removeEventListener('click', aiClosePop);
}

function aiOpenSettings(anchor) {
  aiClosePop();
  const pop = document.createElement('div');
  pop.className = 'oc-pop oc-settings';
  // 点面板内部（下拉、输入框）不该把弹层本身关掉。
  pop.addEventListener('click', event => event.stopPropagation());
  const host = document.createElement('div');
  host.className = 'llm-set-host';
  pop.append(host);
  document.body.append(pop);
  // 左窗贴着屏幕左边，弹层往右让开药丸本身，超出视口时再往回收。
  const rect = anchor.getBoundingClientRect();
  const height = pop.offsetHeight;
  pop.style.top = `${Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - height - 12))}px`;
  pop.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - pop.offsetWidth - 12))}px`;
  aiPop = pop;
  setTimeout(() => document.addEventListener('click', aiClosePop), 0);
  if (!window.LlmSettingsPanel) {
    host.innerHTML = '<div class="llm-set-status err">模型设置面板未加载</div>';
    return;
  }
  window.LlmSettingsPanel.mount(host, {
    onSaved: settings => { aiSetModelLabel(settings); aiToast('模型设置已保存，全局生效。'); },
  });
}

function aiSetModelLabel(settings) {
  const pill = $ai('aiModelPill');
  if (!pill) return;
  const options = [...(settings?.text_options || []), ...(settings?.vision_options || [])];
  const label = id => options.find(option => option.id === id)?.label || id;
  const text = settings?.text_model ? label(settings.text_model) : '未配置模型';
  pill.querySelector('[data-model-name]').textContent = text;
  // 参数推荐带图时走多模态，工艺/成本走语言模型 —— 两个都写进 title，免得
  // 药丸上只显示一个，改完另一个还以为没生效。
  pill.title = `语言模型：${settings?.text_model || '未配置'}\n`
    + `多模态模型：${settings?.vision_model || '未配置'}`;
}

async function aiLoadModelLabel() {
  if (!window.LlmSettingsPanel) { aiSetModelLabel(null); return; }
  try { aiSetModelLabel(await window.LlmSettingsPanel.load()); }
  catch { $ai('aiModelPill')?.querySelector('[data-model-name]').replaceChildren('未连接'); }
}

// --------------------------------------------------------------------------- 启动
function aiBindShell() {
  document.querySelectorAll('[data-ai-tab]').forEach(button => {
    button.onclick = () => { aiTab = button.dataset.aiTab; aiRender(); };
  });
  // 左边已经是 56px 导航条，没有可折叠的卡片了；右窗仍是浮动小窗，折叠方式与 2.1
  // 一致：把 collapsed 打在 dock 上（agent-chat.css 里同时管宽度与箭头方向）。
  $ai('aiFilesToggle').onclick = () => {
    const collapsed = $ai('aiFilesDock').classList.toggle('collapsed');
    $ai('aiFilesToggle').setAttribute('aria-expanded', String(!collapsed));
  };
  $ai('aiModelPill').onclick = event => { event.stopPropagation(); aiOpenSettings(event.currentTarget); };
  $ai('aiFilesRefresh').onclick = () => aiLoadManifest();
  // 初始状态由 JS 定死，不靠 HTML 上那个 hidden 属性 —— markup 改错了这里也能兜住。
  aiToggleQty(false);
  $ai('aiQtyBtn').onclick = event => { event.stopPropagation(); aiToggleQty(); };
  // 面板里点击（改数字、按上下箭头）不该把它自己关掉。
  $ai('aiQtyPop').onclick = event => event.stopPropagation();
  $ai('aiDrawingInput').onchange = event => {
    aiUploadDrawings(event.target.files);
    event.target.value = '';
  };
  $ai('aiPlus').onclick = () => $ai('aiDrawingInput').click();
  $ai('aiStart').onclick = () => aiRunAll();
  $ai('aiWriteDb').onclick = () => aiRunOp('material-write');
  $ai('aiToQuote').onclick = () => aiRunOp('send-to-quote');
  $ai('aiSend').onclick = () => aiSendNote();
  $ai('aiInput').onkeydown = event => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); aiSendNote(); }
  };
  $ai('aiQuantity').onchange = () => aiSaveSettings();
  $ai('aiRequirement').onchange = () => aiSaveSettings();
  $ai('aiPrev').onclick = () => window.CadWorkflowNavigation?.navigate('2.1');
  $ai('aiNext').onclick = () => window.CadWorkflowNavigation?.navigate('3.1');
  $ai('aiConfirm').onclick = () => aiConfirm();
}

async function aiSaveSettings() {
  const body = {
    requirement_note: $ai('aiRequirement').value.trim(),
    quantity: Math.max(1, parseInt($ai('aiQuantity').value, 10) || 1),
  };
  try {
    aiData = await api(aiUrl(''), { method: 'PUT', body: JSON.stringify(body) });
    $ai('aiQtyTip').textContent = `${body.quantity} 台`;
    aiStatus('整合需求已保存');
  } catch (error) { aiToast(error.message || '保存失败', true); }
}

/* --------------------------------------------------------------- Agent 对话
 * 这一页原来的"对话框"其实只是个输入框：说什么都只会被追加进「整合需求」，
 * 问不到状态、也改不了任何东西。现在接的是 2.1 同一个 open-claude 会话
 * （/api/projects/{id}/agent/send，SSE），工具见 services/oc_agent.py 里
 * GetIntegrationState / ListIntegrationParams / UpdateIntegrationParams /
 * UpdateIntegrationProcess / RequestIntegrationStep —— 所以在这里说
 * 「把 20 工序工时改成 8 分钟」是真的会写进业务数据的。
 */
const AI_TOOL_LABEL = {
  GetIntegrationState: '读取 2.2 当前状态',
  ListIntegrationParams: '查整机参数与报价缺口',
  UpdateIntegrationParams: '写入整机参数',
  UpdateIntegrationProcess: '修改组装工序',
  RequestIntegrationStep: '请求平台重跑环节',
  GetProjectState: '读取项目状态', ListParts: '查 2.1 零件清单',
  GetPartDetail: '查零件详情', LookupProcessLibrary: '检索企业工艺库',
  LookupCostLibrary: '检索企业成本库', LookupComponentLibrary: '检索零部件库',
};

function aiToolLabel(name) { return AI_TOOL_LABEL[name] || name; }

async function aiSendNote() {
  const input = $ai('aiInput');
  const text = input.value.trim();
  if (!text || aiChatBusy) return;
  input.value = '';
  aiUserSay(text);
  await aiAgentTurn(text);
}

let aiChatBusy = false;

async function aiAgentTurn(message) {
  aiChatBusy = true;
  $ai('aiSend').disabled = true;
  const bubble = aiThreadAppend(`<div class="oc-amsg"><div class="oc-aav" aria-hidden="true">✦</div>
    <div class="oc-abody"><div class="oc-atxt oc-streaming"></div></div></div>`);
  const body = bubble.querySelector('.oc-atxt');
  let text = '';
  const actions = new Set();
  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(aiPid)}/agent/send`, {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, aiAuthHeaders()),
      body: JSON.stringify({ message, page_context: '2.2 组装与整合' }),
    });
    if (!response.ok || !response.body) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `助手不可用（${response.status}）`);
    }
    await aiReadSse(response.body, event => {
      if (event.type === 'text') {
        text += event.text || '';
        body.textContent = text;
        $ai('aiThread').scrollTop = $ai('aiThread').scrollHeight;
      } else if (event.type === 'tool_use') {
        body.insertAdjacentHTML('beforebegin',
          `<div class="ai-tool"><span class="ai-tool-dot">●</span>${esc(aiToolLabel(event.name))}</div>`);
        if (event.ui_action) actions.add(`${event.ui_action}:${(event.input || {}).step || ''}`);
      } else if (event.type === 'error') {
        throw new Error(event.error || '助手出错');
      }
    });
  } catch (error) {
    body.classList.add('err');
    body.textContent = `助手不可用：${error.message}`;
  } finally {
    body.classList.remove('oc-streaming');
    aiChatBusy = false;
    $ai('aiSend').disabled = false;
  }
  await aiApplyAgentActions(actions);
}

/** 助手用不了要提前说，别等用户敲了一段话才发现发不出去。 */
async function aiLoadAgentMeta() {
  const disc = $ai('aiDisc');
  try {
    const meta = await api(`/api/projects/${encodeURIComponent(aiPid)}/agent/meta`);
    if (meta.available === false) {
      $ai('aiInput').placeholder = '工艺助手未就绪，四个环节的按钮仍可正常使用';
      $ai('aiInput').disabled = true;
      $ai('aiSend').disabled = true;
      if (disc) disc.textContent = `工艺助手暂不可用：${meta.reason || '未知原因'}。`
        + '这不影响参数推荐、组装工艺、成本测算这几步 —— 它们走的是平台自己的流水线。';
      return;
    }
    if (disc && meta.model) {
      disc.textContent = `与 2.1 同一个工艺助手（${meta.model}）。`
        + '它能查本步状态与参数缺口，也能直接改整机参数、改组装工序、重跑某个环节。';
    }
  } catch (error) {
    if (disc) disc.textContent = `工艺助手状态未知：${error.message}`;
  }
}

function aiAuthHeaders() {
  const token = localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 逐块读 SSE。事件形状与 2.1 的 agent-chat.js 一致（text / tool_use / error / done）。 */
async function aiReadSse(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 2);
      if (!chunk.startsWith('data:')) continue;
      let event;
      try { event = JSON.parse(chunk.slice(5)); } catch { continue; }
      if (event.type === 'done') return;
      onEvent(event);
    }
  }
}

/** 工具改完数据要让界面跟上；请求重跑环节就替用户点那一步（同一条流水线）。 */
async function aiApplyAgentActions(actions) {
  if (!actions.size) return;
  const steps = [...actions].filter(item => item.startsWith('integration-step:'))
    .map(item => item.split(':')[1]).filter(step => ['params', 'process', 'cost'].includes(step));
  if ([...actions].some(item => item.startsWith('refresh-integration'))) {
    try {
      aiData = await api(aiUrl(''));
      aiRender();
      aiStatus('助手已更新本步内容');
    } catch (error) { aiToast(error.message || '刷新失败', true); }
  }
  for (const step of steps) {
    const blocked = aiBlocker(step);
    if (blocked) { aiSay(blocked); continue; }
    await aiGenerate(step);
  }
}

async function aiConfirm() {
  try {
    aiData = await api(aiUrl('/confirm'), { method: 'POST' });
    aiRender();
    aiToast('2.2 组装与整合已确认');
    // 确认不拦缺口（型号未定的方案也要能往下走），但必须说出来 ——
    // 报价必填项没齐，这台成品到了报价那头就是几个空格。
    const stat = aiData?.param_checklist?.summary;
    const shortfall = stat ? stat.required_total - stat.required_filled : 0;
    aiSay(shortfall > 0
      ? `本步结果已确认。注意还有 ${shortfall} 项报价必填的成品参数没有值，`
        + `报价测算单上这几格会是空的，进 3.1 之前建议先补齐。`
      : '本步结果已确认，报价成品参数的必填项已齐，可以进入 3.1 汇总结果了。');
  } catch (error) { aiToast(error.message || '确认失败', true); }
}

/** 2.1 的零件与它们已测算的单件成本 —— 整合图纸页要拿它说明"底价从哪来"。 */
async function aiLoadParts() {
  try {
    const aggregate = await api(`/api/projects/${encodeURIComponent(aiPid)}/summary`);
    const parts = aggregate?.ir?.parts || [];
    const costs = await Promise.all(parts.map(part =>
      api(`/api/projects/${encodeURIComponent(aiPid)}/parts/${encodeURIComponent(part.part_id)}/cost`)
        .catch(() => ({}))));
    return parts.map((part, index) => {
      const total = costs[index]?.summary?.computed_total;
      return {
        part_id: part.part_id, name: part.name, quantity: part.quantity || 1,
        hasCost: total != null, unitCost: total,
      };
    });
  } catch { return []; }
}

async function aiStart() {
  if (!aiPid) { location.href = 'home.html'; return; }
  aiBindShell();
  $ai('aiSideProject').textContent = `项目 ${aiPid}`;
  aiLoadModelLabel();
  aiLoadManifest();
  aiLoadAgentMeta();
  try {
    const [payload, parts] = await Promise.all([api(aiUrl('')), aiLoadParts()]);
    aiData = payload;
    aiParts = parts;
    $ai('aiRequirement').value = aiPlan().requirement_note || '';
    $ai('aiQuantity').value = aiPlan().quantity || 1;
    const state = aiData.status || {};
    // 成本算完但参数还没收口时直接落在「整合参数」——那才是此刻待办的事。
    aiTab = state.has_cost ? (state.params_final ? 'cost' : 'finalize')
      : state.has_process ? 'process' : state.has_params ? 'params' : 'drawings';
    aiRender();
    aiStatus(state.confirmed ? '本步已确认' : '就绪');
    if (!parts.length) aiSay('还没有拿到 2.1 的零件清单。请先完成 2.1 图纸解析 —— 2.2 是把那些零件装回整机。');
  } catch (error) {
    $ai('aiBody').innerHTML = `<div class="inline-empty error">读取失败：${esc(error.message)}</div>`;
    aiStatus(`读取失败：${error.message}`, true);
  }
}

aiStart();
