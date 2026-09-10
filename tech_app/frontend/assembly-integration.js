/* embed=1：本页在技术工艺统一工作台（tech-workbench.html）右侧打开，会话宿主在父壳 techChatPane。 */
const __techEmbedMode__ = new URLSearchParams(location.search).get('embed') === '1';
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
// 2.2 只剩三个环节：成本测算与整合参数都搬去了 2.3（财务经理的步骤）。
// 工艺经理在这里交的是**工艺、参数与用量**，成本的数字不由他给。
const AI_TABS = { drawings: '整合图纸', params: '参数推荐', process: '组装工艺' };
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
const aiEditing = { params: false, process: false };

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
    host.innerHTML = `<button type="button" class="inline-action" id="aiUploadBtn">上传整合图纸</button>`
      + `<span class="ai-hint">装配图 / 爆炸图 / 接线图都可以，支持多选。它们会作为参数推荐与组装工艺的视觉输入。</span>`;
    $ai('aiUploadBtn').onclick = () => $ai('aiDrawingInput').click();
    return;
  }
  const has = { params: aiData?.status?.has_params, process: aiData?.status?.has_process }[aiTab];
  // 参数推荐这一环节也要有人按下确认：整机参数、连接关系与 BOM 是后面工艺与成本的输入，
  // 没人点过头就往下走，错的口径会一路带到报价。
  // 参数推荐与组装工艺各有一个确认键。组装工艺那个是闸门：确认之后才允许把任务
  // 推给财务经理去 2.3 测算成本 —— 工序和用量没定稿，算出来的成本没有意义。
  const confirmBtn = aiTab === 'params'
    ? `<button type="button" class="inline-action" id="aiParamsConfirm" ${!has || aiBusy ? 'disabled' : ''}>`
      + `${aiData?.status?.params_confirmed ? '重新确认' : '确认参数推荐'}</button>`
    : aiTab === 'process'
    ? `<button type="button" class="inline-action" id="aiProcessConfirm" ${!has || aiBusy ? 'disabled' : ''}>`
      + `${aiData?.status?.process_confirmed ? '重新确认' : '确认组装工艺'}</button>`
      + `<span class="ai-hint">${aiData?.status?.process_confirmed
          ? '已确认，可以在左边把任务发给财务经理做成本测算。'
          : '确认后才能把任务推给财务经理 —— 工序与用量定稿了，成本才算得准。'}</span>`
    : '';
  const label = `${has ? '重新生成' : '生成'}${AI_TABS[aiTab]}`;
  // 参数表始终可填：它的行是报价字典定死的，模型没推出来的那些正是要人工补的。
  // 藏在「编辑」后面的话，一旦模型一条都没给，按钮连出现的机会都没有。
  const alwaysEditable = aiTab === 'params';
  host.innerHTML =
    `<button type="button" class="inline-action" id="aiGenerate" ${aiBusy ? 'disabled' : ''}>`
    + (aiBusy ? `<span class="parse-spinner" aria-hidden="true"></span><span>${esc(label)}中…</span>` : esc(label))
    + `</button>`
    + `<button type="button" class="inline-action" id="aiEdit" ${alwaysEditable || !has || aiEditing[aiTab] || aiBusy ? 'hidden' : ''}>编辑</button>`
    + `<button type="button" class="inline-action save" id="aiSave" ${alwaysEditable || aiEditing[aiTab] ? '' : 'hidden'} ${aiBusy ? 'disabled' : ''}>保存参数</button>`
    + confirmBtn
;
  $ai('aiGenerate').onclick = () => aiGenerate(aiTab);
  const paramsConfirm = $ai('aiParamsConfirm');
  if (paramsConfirm) paramsConfirm.onclick = () => aiConfirmStep('params');
  const processConfirm = $ai('aiProcessConfirm');
  if (processConfirm) processConfirm.onclick = () => aiConfirmStep('process');
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
  return aiPost(tab, { form });
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

  html += QuoteParams.card(checklist, editing);
  html += QuoteParams.extraCard(params, checklist, editing);

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

/** 确认某个环节。参数推荐缺口不拦（型号未定的方案也要能往下走），但要说出来；
    组装工艺的确认是把任务推给财务的前提。 */
async function aiConfirmStep(step) {
  if (aiBusy) return;
  aiBusy = true;
  aiRenderActions();
  const label = AI_TABS[step];
  try {
    aiData = await api(aiUrl(`/${step}/confirm`), { method: 'POST' });
    const missing = aiData?.status?.required_missing || 0;
    aiStatus(`${label}已确认`);
    aiToast(`${label}已确认`);
    if (step === 'process') {
      aiSay('组装工艺已确认。现在可以点左边「确认工艺并发送至财务做成本测算」，'
        + '把任务交给财务经理 —— 他会在 2.3 逐件算零件成本与组装成本。');
    } else {
      aiSay(missing
        ? `参数推荐已确认。注意还有 ${missing} 项报价必填的成品参数没有值 —— `
          + `它们由财务经理在 2.3「整合参数」里补齐（那里有智能补全）。`
        : '参数推荐已确认，可以继续排组装工艺了。');
    }
  } catch (error) {
    aiStatus(`确认失败：${error.message}`, true);
    aiToast(error.message || '确认失败', true);
  } finally {
    aiBusy = false;
    aiRender();
  }
}

/** 把参数表里改过的值收回成 IntegrationParamPlan。2.3 的「整合参数」走的是另一条
    （只回传值，由后端按"人工补填"标注），这里要连依据与来源一起存。 */
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
    drawings: aiRenderDrawings, params: aiRenderParams, process: aiRenderProcess,
  };
  $ai('aiPanelTitle').textContent = AI_TABS[aiTab];
  document.querySelectorAll('#aiTabs [data-ai-tab]').forEach(button => {
    button.classList.toggle('active', button.dataset.aiTab === aiTab);
  });
  const blocked = aiBlocker(aiTab);
  const state = aiData?.status || {};
  // 父工作台（tech-workbench）process 阶段底栏按“是否已完成整合分析”反转主次：
  // 唯一判定是参数推荐与组装工艺都已生成，与 #aiToFinance 是否可点无关 —— 只生成
  // 一项、busy 或失败都不算完成。每次 render 都同步，重新生成 / Agent 改动后同样生效。
  const analyzed = Boolean(state.has_params && state.has_process);
  document.body.dataset.integrationAnalyzed = analyzed ? 'true' : 'false';
  const done = { drawings: state.drawings > 0, params: state.params_confirmed,
                 process: state.process_confirmed };
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
  $ai('aiSideState').textContent =
    `图纸 ${state.drawings || 0} · 参数 ${stat ? `${stat.filled}/${stat.total}` : '—'}`
    + ` · 工艺 ${state.process_confirmed ? '已确认' : state.has_process ? '待确认' : '—'}`;
  const name = aiPlan().params?.assembly_name;
  $ai('aiTitle').textContent = name ? `组装与整合 · ${name}` : '组装与整合';
}

/* ------------------------------------------------------- 两个对外动作
 * 写入数据库 / 确认工艺并发送至报价。两者都打到 tech_app，再由它回调一体化服务
 * （远程 Postgres 与报价工作流都在那一侧）。成本没算完一律不给点 —— 写进主数据的
 * 单价是要拿去报价的，宁可拦住，也不要写一个 0 进去。
 */

function aiRenderOps() {
  const plan = aiPlan();
  const state = aiData?.status || {};
  const nameInput = $ai('aiProductName');
  if (nameInput && !nameInput.value && plan.params?.assembly_name) {
    nameInput.value = plan.params.assembly_name;
  }
  // 2.2 不再显示成品成本 —— 成本是 2.3 的产出。这里只说工艺交到哪一步了。
  const box = $ai('aiOpCost');
  const finance = plan.finance_handoff;
  box.textContent = finance
    ? `已交给${finance.target_role_name || '财务经理'}（任务 ${finance.task_no || ''}）`
    : '确认参数推荐与组装工艺后，把任务交给财务经理测算成本';

  const financeBtn = $ai('aiToFinance');
  // 闸门：参数与工艺都**确认过**才允许推给财务 —— 工序和用量没定稿，算出来的成本没意义。
  const ready = Boolean(state.params_confirmed && state.process_confirmed) && !aiBusy;
  if (financeBtn) financeBtn.disabled = !ready;

  const why = aiBusy ? '正在处理…'
    : !state.has_params ? '请先完成参数推荐'
    : !state.has_process ? '请先完成组装工艺'
    : !state.params_confirmed ? '请先在「参数推荐」里点「确认参数推荐」'
    : !state.process_confirmed ? '请先在「组装工艺」里点「确认组装工艺」'
    : '';
  if (financeBtn) {
    financeBtn.title = why || '把工艺、参数与用量交给财务经理，由他在 2.3 测算成本';
  }
  const badge = $ai('aiOpsWhy');
  if (badge) {
    badge.textContent = why;
    badge.hidden = !why;
  }

  const done = [];
  if (finance) {
    done.push(`已${esc(aiHandoffWhom(finance))}`
      + (finance.task_no ? ` · 任务 ${esc(finance.task_no)}` : '')
      + ` · ${esc(finance.sent_at || '')}`);
  }
  (plan.material_writes || []).forEach(item => {
    done.push(`已写入主数据：<b>${esc(item.number)}</b> ${esc(item.name)}`
      + ` · 单价 ${aiMoney(item.material_unit_price)} 元 · ${esc(item.written_at || '')}`);
  });
  const hint = $cr_hint();
  hint.innerHTML = (done.length
    ? done.map(line => `<div class="ai-op-done">✓ ${line}</div>`).join('')
      + `<div style="margin-top:6px">再点一次可以换个派发方式重发（旧任务会被作废）。</div>`
    : '工艺与整机参数在这一步定稿；<strong>成本由财务经理在 2.3 测算</strong>，'
      + '写入数据库与发送至报价也都移到了那一步。')
    + (why ? '' : '');
}

/** 说明区节点。抽出来只是为了上面那段读起来短一点。 */
function $cr_hint() { return $ai('aiOpsHint'); }

/** 这条任务交到哪儿了 —— 三种派发方式各有各的说法，别一律写成"已发送至财务经理"。 */
function aiHandoffWhom(finance) {
  const name = finance.target_name || finance.target_role_name || '财务经理';
  if (finance.target_type === 'public') return '发布到公共任务池（谁都能领取）';
  if (finance.target_type === 'user') return `指派给${name}`;
  return `发给${name}（该角色的人都能领取）`;
}

/* 「发送至财务」的派发弹窗 —— 与报价助手的「转交任务」同构。
   成本测算的默认收件角色是财务经理，但公司里做这件事的往往不止一个人：
   点一下就自动群发给整个角色，等于替发起人做了他本来要做的选择。
   三种方式都给：发给某个角色 / 指派给某个人 / 发布到公共任务池。 */
const AI_FINANCE_ROLE = 'finance_mgr';

async function aiWfApi(path) {
  if (!window.cpqAuth || !window.cpqAuth.api) throw new Error('未加载登录模块');
  return window.cpqAuth.api(path);
}

async function aiOpenFinanceDialog() {
  if (aiBusy) return;
  // 名单取不到也要能发：那时只剩「发给财务经理」这一种，与改造前的行为一致。
  let roles = [];
  let users = [];
  let listError = '';
  try {
    roles = (await aiWfApi('/auth/roles')).roles || [];
    users = (await aiWfApi('/auth/users')).users || [];
  } catch (error) {
    listError = error.message || '读取人员名单失败';
  }
  const me = (window.cpqAuth && window.cpqAuth.user && window.cpqAuth.user()) || {};
  const roleNameOf = code =>
    (roles.find(r => r.role_code === code) || {}).role_name || '财务经理';

  const mask = document.createElement('div');
  mask.className = 'ai-send-mask';
  mask.id = 'aiSendMask';
  mask.innerHTML =
    `<div class="ai-send-box">
      <div class="ai-send-head"><h3>发送至财务做成本测算</h3>
        <p>工艺、整机参数与用量在这一步定稿。接手人会在技术工艺 2.3 逐件测算零件成本、
           组装成本并汇总，再决定写入数据库、发送至报价，或退回给你复核。</p></div>
      <div class="ai-send-body">
        <div class="ai-send-row"><label for="aiSendWay">推送方式</label>
          <select id="aiSendWay">
            <option value="role">发给某个角色（该角色的人都能领取）</option>
            <option value="user">指派给某个人</option>
            <option value="public">发布到公共任务池（谁都能领取）</option>
          </select></div>
        <div class="ai-send-row" id="aiSendRoleRow"><label for="aiSendRole">目标角色</label>
          <select id="aiSendRole">${
            (roles.length ? roles : [{ role_code: AI_FINANCE_ROLE, role_name: '财务经理' }])
              .map(r => `<option value="${aiAttr(r.role_code)}"${
                r.role_code === AI_FINANCE_ROLE ? ' selected' : ''}>${esc(r.role_name)}</option>`)
              .join('')}</select></div>
        <div class="ai-send-row" id="aiSendUserRow" hidden>
          <label for="aiSendUser">目标人员（按姓名 / 角色搜索）</label>
          <label class="ai-send-only"><input type="checkbox" id="aiSendOnly" checked />只看${esc(roleNameOf(AI_FINANCE_ROLE))}</label>
          <input id="aiSendSearch" placeholder="输入姓名/角色筛选" />
          <select id="aiSendUser" size="5"></select></div>
        <div class="ai-send-row"><label for="aiSendNote">备注（可选）</label>
          <input id="aiSendNote" maxlength="200" placeholder="给财务的说明，如核算批量、特殊工艺" /></div>
      </div>
      <div class="ai-send-msg" id="aiSendMsg">${listError ? esc(listError) + '：只能按角色发送。' : ''}</div>
      <div class="ai-send-foot">
        <button type="button" class="cancel" id="aiSendCancel">取消</button>
        <button type="button" class="send" id="aiSendGo">发送</button>
      </div>
    </div>`;
  document.body.append(mask);
  mask.addEventListener('mousedown', event => { if (event.target === mask) mask.remove(); });

  const fillUsers = () => {
    const q = ($ai('aiSendSearch').value || '').trim().toLowerCase();
    const onlyFinance = $ai('aiSendOnly').checked;
    $ai('aiSendUser').innerHTML = users
      .filter(x => String(x.user_id) !== String(me.user_id))
      .filter(x => !onlyFinance || x.role_code === AI_FINANCE_ROLE)
      .filter(x => !q || `${x.display_name}${x.role_name}${x.username}`.toLowerCase().includes(q))
      .map(x => `<option value="${aiAttr(x.user_id)}">${esc(x.display_name)}（${esc(x.role_name)}）</option>`)
      .join('') || '<option value="" disabled>没有匹配的人员</option>';
  };
  const syncRows = () => {
    const way = $ai('aiSendWay').value;
    $ai('aiSendRoleRow').hidden = way !== 'role';
    $ai('aiSendUserRow').hidden = way !== 'user';
    if (way === 'user') fillUsers();
  };
  syncRows();
  $ai('aiSendWay').onchange = syncRows;
  $ai('aiSendSearch').oninput = fillUsers;
  $ai('aiSendOnly').onchange = fillUsers;
  $ai('aiSendCancel').onclick = () => mask.remove();
  $ai('aiSendGo').onclick = () => {
    const way = $ai('aiSendWay').value;
    const dispatch = { target_type: way, note: $ai('aiSendNote').value.trim() };
    if (way === 'role') dispatch.target_role_code = $ai('aiSendRole').value;
    if (way === 'user') {
      dispatch.target_user_id = $ai('aiSendUser').value;
      if (!dispatch.target_user_id) { $ai('aiSendMsg').textContent = '请选择目标人员'; return; }
    }
    mask.remove();
    aiRunOp('send-to-finance', dispatch);
  };
}

async function aiRunOp(kind, dispatch) {
  if (aiBusy) return;
  const labels = { 'send-to-finance': '确认工艺并发送至财务做成本测算' };
  aiBusy = true;
  aiRenderOps();
  aiRenderActions();
  const card = aiProcessCard(labels[kind]);
  aiStatus(`${labels[kind]}中…`);
  try {
    const payload = Object.assign(
      { product_name: $ai('aiProductName')?.value.trim() || '' }, dispatch || {});
    aiData = await api(aiUrl(`/${kind}`), { method: 'POST', body: JSON.stringify(payload) });
    const finance = aiData.finance || {};
    const whom = aiHandoffWhom(finance);
    card.log([`任务 ${finance.task_no || ''} 已${whom}`,
      `  他将在技术工艺 2.3 逐件测算零件成本与组装成本`,
      `  整合参数、写入数据库与发送至报价都在那一步完成`]);
    aiSay(`工艺已确认，任务 ${finance.task_no || ''} 已${whom}做成本测算。
`
      + `他会在 2.3 逐个零件加整机算完成本、补齐报价参数，然后选择写入数据库、发送至报价，`
      + `或把结果退回给你复核工艺与用量。`);
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
  // 成本明细的增删行随成本一起搬去了 2.3（cost-review），这里不再绑。
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
  for (const step of ['params', 'process']) {
    await aiGenerate(step);
    if (!aiData?.status?.[{ params: 'has_params', process: 'has_process' }[step]]) {
      aiSay(`「${AI_TABS[step]}」没有产出结果，后面两步依赖它，先停在这里。`);
      return;
    }
  }
  // 确认不代跑：那是人对结果点头，自动点等于没确认。
  aiTab = 'process';
  aiRender();
  aiSay('参数推荐与组装工艺都已生成。逐项核对后，分别点「确认参数推荐」与「确认组装工艺」，'
    + '再把任务发给财务经理做成本测算（2.3）。');
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
  $ai('aiDrawingInput').onchange = event => {
    aiUploadDrawings(event.target.files);
    event.target.value = '';
  };
  $ai('aiPlus').onclick = () => $ai('aiDrawingInput').click();
  $ai('aiStart').onclick = () => aiRunAll();
  // 不直接发：先让人选派发方式（角色 / 指定人 / 公共任务池），和报价助手一致。
  $ai('aiToFinance').onclick = () => aiOpenFinanceDialog();
  $ai('aiSend').onclick = () => aiSendNote();
  $ai('aiInput').onkeydown = event => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); aiSendNote(); }
  };
  $ai('aiRequirement').onchange = () => aiSaveSettings();
  $ai('aiPrev').onclick = () => window.CadWorkflowNavigation?.navigate('2.1');
  $ai('aiNext').onclick = () => window.CadWorkflowNavigation?.navigate('3.1');
  $ai('aiConfirm').onclick = () => aiConfirm();
}

async function aiSaveSettings() {
  const body = {
    requirement_note: $ai('aiRequirement').value.trim(),
    // 核算批量搬去 2.3 了（成本才用得上它），这里原样保留当前值。
    quantity: aiPlan().quantity || 1,
  };
  try {
    aiData = await api(aiUrl(''), { method: 'PUT', body: JSON.stringify(body) });
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
  if (!aiPid) { if(window.TechEmbed&&window.TechEmbed.embedded){window.TechEmbed.exitToTechHome();return;} location.href = 'home.html'; return; }
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
    const state = aiData.status || {};
    // 落在"下一件该做的事"上。
    aiTab = state.has_process ? 'process' : state.has_params ? 'params' : 'drawings';
    aiRender();
    aiStatus(state.confirmed ? '本步已确认' : '就绪');
    if (!parts.length) aiSay('还没有拿到 2.1 的零件清单。请先完成 2.1 图纸解析 —— 2.2 是把那些零件装回整机。');
  } catch (error) {
    $ai('aiBody').innerHTML = `<div class="inline-empty error">读取失败：${esc(error.message)}</div>`;
    aiStatus(`读取失败：${error.message}`, true);
  }
}

aiStart();
