/*
 * 流程栏统一导航。页面的流程栏保留原有 DOM 和样式；本文件只补充可点击、
 * 展开/收起和基于项目真实状态的跳转校验。
 */
(() => {
  'use strict';

  const stages = {
    1: ['1.1 创建', '1.2 确认', '1.3 审核'],
    // 【CPQ 定制】技术工艺阶段：2.1 图纸解析、2.2 组装与整合、2.3 成本测算（财务经理）；上游的
    // （材料定性/清洗/组装检测/产能）不在 CPQ 的流程内。本表是流程栏、跳转和门禁的
    // 唯一事实源。后端报告送审门禁另有一份口径（backend/config.py 的 TECH_SUBSTEPS），
    // 那里管的是上游那六个子步骤，2.2 不在其中 —— 它是 CPQ 自己新增的一步，
    // 有意不做成 3.1 的硬前置：老项目没有 2.2 的结果，挡住就没法出报告了。
    2: ['2.1 图纸解析', '2.2 组装与整合', '2.3 成本测算'],
    3: ['3.1 汇总结果', '3.2 审核报告', '3.3 发布报告'],
  };
  const stepKey = {
    '1.1':'create', '1.2':'confirm', '1.3':'review', '2.1':'drawing', '2.2':'integration',
    // 2.3 成本测算：财务经理的步骤（工艺经理在 2.2 末尾把项目交过来）
    '2.3':'costReview',
    '3.1':'summary', '3.2':'reportReview', '3.3':'publish',
  };
  let cachedProject = null;
  let loadingProject = null;

  function projectId() {
    const q = new URLSearchParams(location.search).get('project');
    return q || localStorage.getItem('cad_engine_project_id') || localStorage.getItem('currentProject') || '';
  }
  function authHeaders() {
    const token = localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token');
    return token ? { Authorization: `Bearer ${token}` } : {};
  }
  function notify(message, error = false) {
    document.querySelectorAll('.workflow-nav-toast').forEach((el) => el.remove());
    const el = document.createElement('div');
    el.className = `workflow-nav-toast${error ? ' error' : ''}`;
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3600);
  }
  function labelCode(label) { return String(label || '').match(/([123]\.[123456])/u)?.[1] || ''; }
  function stageOfCode(code) { return Number(String(code).split('.')[0]) || 0; }
  function activeStage(root) {
    const active = root.querySelector('.workflow-step.active,.main-step.active,.tp-flow-step.active,.detail-step.active,.workflow-nav-link.active,.sub-label.active,.tp-flow-substep.active');
    const code = active ? stageOfCode(active.textContent) : 0;
    if (code) return code;
    const text = active?.textContent || '';
    if (text.includes('接受')) return 1;
    if (text.includes('解析')) return 2;
    if (text.includes('输出')) return 3;
    return 0;
  }
  function isActiveStage(node) { return node.classList.contains('active'); }
  function subContainer(wrapper, variant) {
    let el = wrapper.querySelector('.workflow-sub,.sub-labels-row,.tp-flow-substeps');
    if (el) return el;
    el = document.createElement('div');
    el.className = variant === 'tp' ? 'tp-flow-substeps' : variant === 'workflow' ? 'workflow-sub' : 'sub-labels-row';
    wrapper.appendChild(el);
    return el;
  }
  function stageLabel(step) {
    const text = step.textContent || '';
    if (text.includes('接受')) return 1;
    if (text.includes('解析')) return 2;
    if (text.includes('输出')) return 3;
    return 0;
  }
  function hasCode(container, code) {
    return [...container.querySelectorAll('[data-workflow-code],span,button')].some((el) => labelCode(el.textContent) === code);
  }
  function linkMarkup(container, label, variant) {
    const code = labelCode(label);
    const stateClass = container.querySelector(`[data-workflow-code="${code}"]`)?.className || '';
    const b = document.createElement('button');
    b.type = 'button'; b.className = `${variant === 'tp' ? 'tp-flow-substep' : 'sub-label'} workflow-nav-link ${stateClass}`;
    b.dataset.workflowCode = code; b.textContent = label;
    return b;
  }
  function populateSubsteps(container, stage, variant) {
    // 【CPQ 定制】页面里写死的流程栏可能还残留 2.2–2.6；stages 是唯一事实源，
    // 多出来的小步骤连同它前面的箭头一起移除，免得点进去无处可去。
    const allowed = new Set(stages[stage].map(labelCode));
    [...container.children].forEach((node) => {
      const code = labelCode(node.textContent);
      if (!code || allowed.has(code)) return;
      const prev = node.previousElementSibling;
      if (prev && prev.textContent.trim() === '→') prev.remove();
      node.remove();
    });
    const current = [...container.children];
    const codeNodes = current.filter((node) => labelCode(node.textContent));
    codeNodes.forEach((node) => {
      if (node.tagName === 'BUTTON') { node.dataset.workflowCode = labelCode(node.textContent); return; }
      // 文案也以 stages 为准：2.2/2.3 这两个号在上游是「材料定性 / 工艺路径」，
      // 我们换成了「组装与整合 / 成本测算」。只按号留下节点会把旧名字留在栏里。
      const canonical = stages[stage].find(label => labelCode(label) === labelCode(node.textContent));
      const replacement = linkMarkup(container, (canonical || node.textContent).trim(), variant);
      replacement.className = `${replacement.className} ${node.className || ''}`;
      node.replaceWith(replacement);
    });
    stages[stage].forEach((label, index) => {
      const code = labelCode(label);
      if (hasCode(container, code)) return;
      if (container.children.length && !container.lastElementChild?.classList.contains('workflow-nav-arrow')) {
        const arrow = document.createElement('span');
        arrow.className = variant === 'tp' ? 'tp-flow-subarrow workflow-nav-arrow' : 'sub-label-arrow workflow-nav-arrow';
        arrow.textContent = '→'; container.appendChild(arrow);
      }
      container.appendChild(linkMarkup(container, label, variant));
    });
  }
  function makeTrigger(step, stage) {
    if (step.dataset.workflowBound) return;
    step.dataset.workflowBound = '1'; step.dataset.workflowStage = String(stage);
    step.classList.add('workflow-nav-trigger'); step.setAttribute('role', 'button'); step.tabIndex = 0;
    step.setAttribute('aria-label', `展开${stage}的步骤`);
    const toggle = () => toggleStage(step);
    step.addEventListener('click', toggle);
    step.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); } });
  }
  function getWrapper(step) { return step.closest('.workflow-group,.main-step-wrapper,.tp-flow-wrapper,.workflow-nav-detail-wrapper') || step.parentElement; }
  function toggleStage(step) {
    const wrapper = getWrapper(step); if (!wrapper) return;
    const stage = Number(step.dataset.workflowStage || stageLabel(step));
    const sub = wrapper.querySelector('.workflow-sub,.sub-labels-row,.tp-flow-substeps');
    if (!sub) return;
    const collapsed = sub.classList.toggle('workflow-nav-sub-collapsed');
    // 流程栏采用手风琴：展开当前阶段前，收起另外两个阶段，防止小步骤换行。
    if (!collapsed) {
      const root = step.closest('.workflow-main,.main-workflow,.tp-top-workflow,.detail-main-workflow');
      root?.querySelectorAll('.workflow-sub,.sub-labels-row,.tp-flow-substeps').forEach((other) => {
        if (other === sub) return;
        other.classList.add('workflow-nav-sub-collapsed');
        const otherStep = other.parentElement?.querySelector('.workflow-step,.main-step,.tp-flow-step,.detail-step');
        if (otherStep) otherStep.setAttribute('aria-expanded', 'false');
      });
    }
    step.setAttribute('aria-expanded', String(!collapsed));
  }
  function firstCurrentCode(root) {
    const active = root.querySelector('.workflow-nav-link.active,.sub-label.active,.tp-flow-substep.active');
    return active ? labelCode(active.textContent) : '';
  }
  function bindWorkflow(root) {
    if (!root || root.dataset.workflowNavigationReady === '1') return;
    const isTp = root.classList.contains('tp-top-workflow');
    const isDetail = root.classList.contains('detail-main-workflow');
    const steps = root.querySelectorAll('.workflow-step,.main-step,.tp-flow-step,.detail-step');
    if (!steps.length) return;
    root.dataset.workflowNavigationReady = '1';
    const currentStage = activeStage(root);
    steps.forEach((step) => {
      const stage = stageLabel(step); if (!stage) return;
      let wrapper = getWrapper(step);
      // 早期的拆解/成本页没有每个主步骤的容器；就地包一层，不动原有布局。
      if (isDetail && !wrapper.classList.contains('workflow-nav-detail-wrapper')) {
        const holder = document.createElement('div');
        holder.className = 'workflow-nav-detail-wrapper';
        step.parentNode.insertBefore(holder, step); holder.appendChild(step); wrapper = holder;
      }
      const variant = isTp ? 'tp' : root.querySelector('.workflow-step') ? 'workflow' : 'main';
      const sub = subContainer(wrapper, variant);
      populateSubsteps(sub, stage, variant);
      // 主阶段已经完成时，其下小步骤同样作为完成态展示，避免展开后变成默认灰色。
      if (stage < currentStage) {
        sub.querySelectorAll('.workflow-nav-link').forEach((link) => {
          link.classList.remove('active');
          link.classList.add('done');
        });
      }
      // 当前阶段默认显示；所有前序完成和后续未开始阶段先收起。
      if (stage !== currentStage) sub.classList.add('workflow-nav-sub-collapsed');
      makeTrigger(step, stage);
    });
    root.addEventListener('click', async (event) => {
      const link = event.target.closest('.workflow-nav-link'); if (!link) return;
      event.preventDefault(); event.stopPropagation();
      await navigate(link.dataset.workflowCode || labelCode(link.textContent));
    });
    if (projectId()) loadProject().then((progress) => applyProgress(root, progress));
  }
  function findRoots() {
    document.querySelectorAll('.workflow-main,.main-workflow,.tp-top-workflow,.detail-main-workflow').forEach(bindWorkflow);
  }
  async function loadProject() {
    const id = projectId();
    if (!id) return null;
    if (cachedProject?.id === id) return cachedProject;
    if (loadingProject) return loadingProject;
    loadingProject = Promise.all([
      fetch(`/api/projects/${encodeURIComponent(id)}/workflow`, { headers: authHeaders() }).then((r) => r.ok ? r.json() : null),
      fetch(`/api/projects/${encodeURIComponent(id)}/summary`, { headers: authHeaders() }).then((r) => r.ok ? r.json() : null),
    ]).then(([workflow, aggregate]) => (cachedProject = { id, workflow: workflow || {}, aggregate: aggregate || {} }))
      .catch(() => (cachedProject = { id, workflow: {}, aggregate: {} }))
      .finally(() => { loadingProject = null; });
    return loadingProject;
  }
  function hasStepData(progress, key) {
    const value = progress?.aggregate?.steps?.[key];
    if (!value || typeof value !== 'object') return false;
    const timing = value.timing || {};
    return timing.completed === true || timing.status === 'done' || Object.keys(value).some((k) => !['timing', 'project_id', 'updated_at', 'history'].includes(k) && value[k]);
  }
  // 2.2 组装与整合：参数与组装工艺都有产出才算做完。
  // **不再要求成本** —— 成本从 2.2 拆出去了，归财务经理在 2.3 做。
  function integrationDone(progress) {
    const doc = progress?.aggregate?.steps?.integration;
    if (!doc || typeof doc !== 'object') return false;
    return Boolean(doc.params?.params?.length) && Boolean(doc.process?.steps?.length);
  }
  function stepFinished(progress, key) {
    const timing = progress?.aggregate?.steps?.[key]?.timing || {};
    return timing.completed === true || timing.status === 'done';
  }
  function setState(node, state, isMain = false) {
    if (!node) return;
    node.classList.remove('active', 'done', 'completed', 'pending', 'workflow-nav-stage-done', 'workflow-nav-stage-active');
    if (state === 'done') node.classList.add('done', 'completed', ...(isMain ? ['workflow-nav-stage-done'] : []));
    else if (state === 'active') node.classList.add('active', ...(isMain ? ['workflow-nav-stage-active'] : []));
    else node.classList.add('pending');
  }
  function applyProgress(root, progress) {
    // 网络失败时保留页面原有状态，避免静态流程栏被错误重置成灰色。
    if (!progress?.workflow?.project) return;
    const req = progress.workflow.requirement || {};
    const report = progress.workflow.report || {};
    const summary = progress.workflow.summary || {};
    const reqStatus = req.status || '';
    const pageStage = activeStage(root);
    const drawingDone = Boolean(progress.aggregate?.ir?.parts?.length);
    // 【CPQ 定制】2.x 只剩 2.1：技术工艺阶段的开始与完成都以图纸解析出零件 IR 为准。
    const techDone = Boolean(summary.confirmed) || drawingDone;
    const techStarted = drawingDone;
    const reportExists = Boolean(report.status);
    // 兼容早期已发布项目：旧数据可能没有 requirement/summary 文档，
    // 但既然已经生成报告，前序阶段在业务上必然已完成。
    const requirementDone = reqStatus === 'approved' || techStarted || techDone || reportExists;
    const technicalDone = techDone || reportExists;
    const stageStates = {
      1: requirementDone ? 'done' : reqStatus ? 'active' : pageStage === 1 ? 'active' : 'pending',
      2: technicalDone || pageStage >= 2 ? 'done' : techStarted ? 'active' : 'pending',
      3: report.status === 'published' ? 'done' : reportExists ? 'active' : pageStage === 3 ? 'active' : 'pending',
    };
    const doneCodes = new Set();
    if (requirementDone) ['1.1', '1.2', '1.3'].forEach((code) => doneCodes.add(code));
    else {
      if (reqStatus && reqStatus !== 'draft') doneCodes.add('1.1');
      if (['pending_review', 'approved'].includes(reqStatus)) doneCodes.add('1.2');
    }
    if (technicalDone || drawingDone) doneCodes.add('2.1');
    // 2.2 以"三个环节都有产出"为完成，不要求人工点确认 —— 确认与否是 2.2 页面自己的事，
    // 流程栏只回答"这一步做没做"。这里不能用 hasStepData：只要在 2.2 存过一次
    // 整合需求，文档里就有 quantity 之类的非空字段，那套通用判据会直接判成已完成。
    if (integrationDone(progress)) doneCodes.add('2.2');
    // 2.3 以"财务确认过成本"为完成 —— 算过但没确认不算，那是还在核的状态。
    if (progress?.aggregate?.steps?.cost_review?.confirmed) doneCodes.add('2.3');
    if (['in_review', 'approved', 'published'].includes(report.status)) doneCodes.add('3.1');
    if (['approved', 'published'].includes(report.status)) doneCodes.add('3.2');
    if (report.status === 'published') doneCodes.add('3.3');

    const mainSteps = [...root.querySelectorAll('.workflow-step,.main-step,.tp-flow-step,.detail-step')];
    mainSteps.forEach((node) => setState(node, stageStates[stageLabel(node)] || 'pending', true));
    root.querySelectorAll('.workflow-nav-link').forEach((node) => {
      const code = node.dataset.workflowCode || labelCode(node.textContent);
      setState(node, doneCodes.has(code) ? 'done' : node.classList.contains('active') ? 'active' : 'pending');
    });
    const connectors = [...root.querySelectorAll('.workflow-connector,.main-connector,.tp-flow-line,.detail-connector')];
    connectors.forEach((connector, index) => {
      const state = stageStates[index + 2] || 'pending';
      connector.classList.remove('active', 'done', 'workflow-nav-connector-active', 'workflow-nav-connector-done');
      if (state === 'done') connector.classList.add('workflow-nav-connector-done');
      else if (state === 'active') connector.classList.add('workflow-nav-connector-active');
    });
  }
  function gate(code, progress) {
    const req = progress?.workflow?.requirement || {};
    const report = progress?.workflow?.report || {};
    const summary = progress?.workflow?.summary || {};
    const status = req.status || '';
    const approved = status === 'approved';
    const readyReq = ['pending_confirmation', 'pending_review', 'approved'].includes(status);
    const readyConfirm = ['pending_review', 'approved'].includes(status);
    const readyDrawing = approved || Boolean(progress?.aggregate?.ir?.parts?.length);
    // 【CPQ 定制】3.1 的前置从「2.6 产能评估」改成「2.1 图纸解析已出结果」。
    const readySummary = Boolean(progress?.aggregate?.ir?.parts?.length)
      || Boolean(summary?.confirmed_at) || Boolean(report?.id || report?.status);
    const readyReview = ['in_review', 'approved', 'published'].includes(report.status);
    const readyPublish = ['approved', 'published'].includes(report.status);
    const checks = {
      '1.1': [true, ''], '1.2': [readyReq, '请先在 1.1 创建中保存并提交工艺评估需求。'],
      '1.3': [readyConfirm, '请先在 1.2 确认工艺评估需求后再进入审核。'],
      '2.1': [readyDrawing, '请先完成 1.3 审核并通过工艺评估需求。'],
      // 2.2 要的是"零件已经拆出来了"，光有需求审批没用 —— 组装是把 2.1 的零件装回整机。
      '2.2': [Boolean(progress?.aggregate?.ir?.parts?.length),
              '请先完成 2.1 图纸解析并生成零件清单，2.2 要把这些零件装回整机。'],
      // 2.3 要的是"工艺与整机参数已定稿"：成本按 2.2 的 BOM 与工序算。
      '2.3': [Boolean(progress?.aggregate?.steps?.integration?.process?.steps?.length),
              '请先完成 2.2 组装与整合：成本要按整机 BOM 与组装工序来算。'],
      '3.1': [readySummary, '请先完成 2.1 图纸解析并生成解析结果。'],
      '3.2': [readyReview, '请先在 3.1 汇总结果中保存并提交评估报告。'],
      '3.3': [readyPublish, '请先完成 3.2 审核报告并获得通过。'],
    };
    return checks[code] || [false, '该流程步骤暂不可进入。'];
  }
  function urlFor(code, id) {
    const q = id ? `?project=${encodeURIComponent(id)}` : '';
    const routes = {
      '1.1': `/requirement-create.html${q}`, '1.2': `/requirement-confirm.html${q}`, '1.3': `/requirement-review.html${q}`,
      '2.1': `/index.html${q}`, '2.2': `/assembly-integration.html${q}`,
      '2.3': `/cost-review.html${q}`, '3.1': `/summary.html${q}`, '3.2': `/report-review.html${q}`, '3.3': `/report-publish.html${q}`,
    };
    return routes[code] || '/home.html';
  }
  async function navigate(code) {
    if (!code) return;
    const id = projectId();
    if (!id && code !== '1.1') { notify('请先在 1.1 创建并保存一个工艺评估需求。', true); return; }
    const progress = await loadProject();
    const [allowed, message] = gate(code, progress);
    if (!allowed) { notify(message, true); return; }
    if (id) { localStorage.setItem('cad_engine_project_id', id); localStorage.setItem('currentProject', id); }
    location.href = urlFor(code, id);
  }
  window.CadWorkflowNavigation = { navigate, refresh: () => { cachedProject = null; findRoots(); } };
  const observer = new MutationObserver(() => findRoots());
  const start = () => { findRoots(); observer.observe(document.body, { childList: true, subtree: true }); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
