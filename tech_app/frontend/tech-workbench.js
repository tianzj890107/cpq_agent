/* 技术工艺与成本测算统一工作台（统一壳）。
 *
 * 左侧：techChatPane —— 唯一常驻会话宿主，复用 agent-chat.js，切换 stage 不重建。
 * 右侧：techWorkspaceOutlet —— 唯一工作台出口，以同源 iframe 打开九个 stage 的既有页面，
 *       URL 一律追加 embed=1；子页面需要切步骤时向本壳 postMessage
 *       cpq:tech-workbench:navigate，本壳校验 event.origin 与 stage 白名单后更新 URL。
 *
 * URL 是可恢复导航状态的事实源：
 *   tech-workbench.html?project=<id>&stage=<stage>&task_id=<optional>
 * 刷新、浏览器前进/后退通过 popstate 恢复；project 缺失时展示明确错误态，不创建匿名项目。
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const STAGES = [
    { id: 'requirement-create',  no: '1.1', phase: 1, phaseTitle: '接受工艺评估需求', label: '创建',     page: 'requirement-create.html' },
    { id: 'requirement-confirm', no: '1.2', phase: 1, phaseTitle: '接受工艺评估需求', label: '确认',     page: 'requirement-confirm.html' },
    { id: 'requirement-review',  no: '1.3', phase: 1, phaseTitle: '接受工艺评估需求', label: '审核',     page: 'requirement-review.html' },
    { id: 'drawing',             no: '2.1', phase: 2, phaseTitle: '解析技术工艺过程', label: '图纸解析', page: 'index.html' },
    { id: 'process',             no: '2.2', phase: 2, phaseTitle: '解析技术工艺过程', label: '工艺方案/组装整合', page: 'assembly-integration.html' },
    { id: 'cost',                no: '2.3', phase: 2, phaseTitle: '解析技术工艺过程', label: '成本测算', page: 'cost-review.html' },
    { id: 'summary',             no: '3.1', phase: 3, phaseTitle: '输出工艺评估结果', label: '汇总结果', page: 'summary.html' },
    { id: 'report-review',       no: '3.2', phase: 3, phaseTitle: '输出工艺评估结果', label: '结果审核', page: 'report-review.html' },
    { id: 'report-publish',      no: '3.3', phase: 3, phaseTitle: '输出工艺评估结果', label: '发布并回传报价', page: 'report-publish.html' },
  ];
  const stages = new Set(STAGES.map((item) => item.id));

  // 顶部可见的五个大流程：九阶段仍是内部状态与 URL 的事实源，MAJOR_STEPS 只负责
  // 把 1.1/1.2/1.3 聚合到大流程 1、把 3.1/3.2/3.3 聚合到大流程 5 的顶部投影。
  // title 是右侧业务区域唯一的固定大标题（不接受子页面动态标题与状态刷新覆盖）；
  // label 仍是顶部流程导航上的原文，两者互不影响。
  const MAJOR_STEPS = [
    { no: '1', title: '工艺评估需求', label: '创建工艺评估需求', entry: 'requirement-create', stages: ['requirement-create', 'requirement-confirm', 'requirement-review'] },
    { no: '2', title: '图纸解析', label: '图纸解析', entry: 'drawing', stages: ['drawing'] },
    { no: '3', title: '组装与整合', label: '工艺方案/组装整合', entry: 'process', stages: ['process'] },
    { no: '4', title: '成本测算', label: '成本测算', entry: 'cost', stages: ['cost'] },
    { no: '5', title: '工艺评估报告', label: '输出工艺评估结果', entry: 'summary', stages: ['summary', 'report-review', 'report-publish'] },
  ];

  // 大流程 1、5 内部还有多步，需要在业务卡片标题行给一排上下文小流程按钮（大流程
  // 2-4 各自只有一个内部阶段或已有子页面页签，不在父壳重复生成）。按钮只显示名称，
  // stage id、页面文件和九阶段流转都不变。
  const CONTEXT_SUBSTEPS = {
    1: [
      { stage: 'requirement-create',  label: '创建' },
      { stage: 'requirement-confirm', label: '确认' },
      { stage: 'requirement-review',  label: '审核' },
    ],
    5: [
      { stage: 'summary',        label: '汇总结果' },
      { stage: 'report-review',  label: '结果审核' },
      { stage: 'report-publish', label: '发布并回传报价' },
    ],
  };

  // 大流程 3、4 的子页面在统一工作台里已经隐藏了自带的页签行：父壳把同一组页签渲染成
  // data-child-tab 按钮放到统一标题行右侧，点击只转发给同源 iframe 里既有的页签按钮，
  // active 也从子页面真实状态回读 —— 不复制业务状态，也不新建第二套业务逻辑。
  const CHILD_TAB_PROXY = {
    'process': {
      tabs: [
        { key: 'drawings', label: '整合图纸', selector: '#aiTabs [data-ai-tab="drawings"]' },
        { key: 'params', label: '参数推荐', selector: '#aiTabs [data-ai-tab="params"]' },
        { key: 'process', label: '组装工艺', selector: '#aiTabs [data-ai-tab="process"]' },
      ],
      activeSelector: '#aiTabs .active',
      keyAttr: 'aiTab',
    },
    'cost': {
      tabs: [
        { key: 'parts', label: '零件成本', selector: '#crTabs [data-cr-tab="parts"]' },
        { key: 'assembly', label: '组装成本', selector: '#crTabs [data-cr-tab="assembly"]' },
        { key: 'total', label: '汇总', selector: '#crTabs [data-cr-tab="total"]' },
        { key: 'params', label: '整合参数', selector: '#crTabs [data-cr-tab="params"]' },
      ],
      activeSelector: '#crTabs .active',
      keyAttr: 'crTab',
    },
  };

  function currentMajorStep() {
    return MAJOR_STEPS.find(major => major.stages.includes(state.stage)) || MAJOR_STEPS[0];
  }
  // 大流程完成态：组内全部内部 stage 完成（来自 /workflow、/summary 的真实完成集合）才算完成。
  function isMajorDone(major) {
    const done = state.progress && state.progress.done;
    return Boolean(done) && major.stages.every((stageId) => done.has(stageId));
  }

  // 每步“主操作/次要操作”代理：操作仍由右侧子页面自己的 DOM 与 API 执行，
  // 本壳只负责把按钮放到底栏并点击子页面对应控件，不复制业务实现。
  const STAGE_ACTIONS = {
    'requirement-create':  { primary: '#submitRequirement', primaryLabel: '提交确认', secondary: '#saveDraft', secondaryLabel: '保存草稿' },
    'requirement-confirm': { primary: '#confirmPass',       primaryLabel: '✓ 通过确认', secondary: '#returnDraft', secondaryLabel: '× 驳回' },
    'requirement-review':  { primary: '#submitReview',      primaryLabel: '提交审核意见', secondary: null, secondaryLabel: '' },
    'drawing':             { primary: '#btnParse',          primaryLabel: '▶ 开始解析', secondary: null, secondaryLabel: '' },
    'process':             { primary: '#aiToFinance',       primaryLabel: '✓ 确认工艺并发送财务', secondary: '#aiStart', secondaryLabel: '▶ 开始整合分析' },
    'cost':                { primary: '#crRunAll',          primaryLabel: '▶ 逐件测算并汇总', secondary: '#crConfirm', secondaryLabel: '✓ 确认成本' },
    'summary':             { primary: '#srSubmit',          primaryLabel: '提交审核', secondary: '#srSave', secondaryLabel: '保存' },
    'report-review':       { primary: '#rrPublish',         primaryLabel: '审核通过并发布', secondary: '#rrReject', secondaryLabel: '退回汇总' },
    'report-publish':      { primary: '#rpPrimary',         primaryLabel: '发布报告', secondary: null, secondaryLabel: '' },
  };

  // 大流程 2/3/4 的子页面在统一工作台里没有自己的可见会话栏：它们需要的阶段
  // 上下文、提示与可用操作统一接入父壳唯一的 #techChatPane。这里只登记这三个
  // stage；流程 1、5 不登记，父会话栏不出现子页面附加面板。操作只引用底栏已有的
  // STAGE_ACTIONS 代理角色，不复制任何业务逻辑。
  const STAGE_AGENT_CONTEXT = {
    drawing: {
      pageContext: '2.1 图纸解析',
      label: '图纸解析',
      hint: '右侧看板显示解析进度与零件结果；可以在这里追问解析结果，或直接说「开始解析」。',
      actions: ['primary'],
    },
    process: {
      pageContext: '2.2 组装与整合',
      label: '组装与整合',
      hint: '可让我上传整合图纸、生成参数推荐与组装工艺；确认结果与发送财务请用底栏操作。',
      actions: ['primary', 'secondary'],
    },
    cost: {
      pageContext: '2.3 成本测算',
      label: '成本测算',
      hint: '可追问成本构成与零件测算结果；重算与确认成本请用底栏操作。',
      actions: ['primary', 'secondary'],
    },
  };

  const IGNORE_PARAMS = new Set(['project', 'stage', 'task_id', 'tech_task', 'embed', 'embedding']);

  const state = { stage: '', project: '', taskId: '', progress: null };
  // 右侧项目标题：优先显示真实项目名称，拉取失败时退回“项目 <id>”。
  const projectNames = new Map();

  function params() { return new URLSearchParams(location.search); }

  function readFromUrl() {
    const q = params();
    state.project = q.get('project') || '';
    state.taskId = q.get('task_id') || q.get('tech_task') || '';
    state.stage = stages.has(q.get('stage')) ? q.get('stage') : '';
  }

  function extraParams() {
    const q = params();
    const out = [];
    q.forEach((value, key) => {
      if (!IGNORE_PARAMS.has(key)) out.push([key, value]);
    });
    return out;
  }

  function stageMeta(id) { return STAGES.find((item) => item.id === id) || null; }
  function stageIndex(id) {
    const meta = stageMeta(id);
    return meta ? STAGES.indexOf(meta) : -1;
  }

  /* ---------------------------------------------------------- URL 状态 */
  function urlFor(stageId, project, taskId) {
    const q = new URLSearchParams();
    if (project) q.set('project', project);
    q.set('stage', stageId);
    if (taskId) q.set('task_id', taskId);
    extraParams().forEach(([key, value]) => q.set(key, value));
    const path = location.pathname.split('/').pop() || 'tech-workbench.html';
    return `${path}?${q.toString()}`;
  }
  function pushState() {
    const q = new URLSearchParams();
    if (state.project) q.set('project', state.project);
    q.set('stage', state.stage);
    if (state.taskId) q.set('task_id', state.taskId);
    extraParams().forEach(([key, value]) => q.set(key, value));
    history.pushState({ stage: state.stage, project: state.project }, '', `${location.pathname}?${q.toString()}`);
  }
  window.addEventListener('popstate', () => {
    readFromUrl();
    if (!state.stage) state.stage = 'requirement-create';
    renderTop();
    mountStageFrame();
    syncAgentStageContext();
  });

  /* ---------------------------------------------------------- 渲染 */
  function renderTop() {
    const bar = $('techStepsBar');
    if (!bar) return;
    const current = stageMeta(state.stage);
    const currentIdx = stageIndex(state.stage);
    const canNav = Boolean(state.project || state.stage === 'requirement-create');
    const activeMajor = currentMajorStep();
    let html = '';
    MAJOR_STEPS.forEach((major, idx) => {
      const cls = ['tech-step-btn'];
      const isActive = major === activeMajor;
      const isDone = !isActive && isMajorDone(major);
      if (isActive) cls.push('active');
      else if (isDone) cls.push('done');
      const allowed = canNav || major.entry === 'requirement-create';
      const nodeInner = isDone ? '<i class="ti ti-check" aria-hidden="true"></i>' : major.no;
      html += `<button type="button" class="${cls.join(' ')}" data-major-step="${major.no}" data-entry="${major.entry}" ${allowed ? '' : 'disabled'} title="${major.no} ${major.label}">` +
        `<span class="tech-step-node">${nodeInner}</span><span class="tech-step-label">${major.label}</span></button>`;
      if (idx < MAJOR_STEPS.length - 1) html += '<span class="tech-step-line" aria-hidden="true"></span>';
    });
    bar.innerHTML = html;
    bar.querySelectorAll('[data-major-step]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const step = MAJOR_STEPS.find((item) => item.no === btn.dataset.majorStep) || MAJOR_STEPS[0];
        if (state.stage === step.entry) return;
        if (!state.project && step.entry !== 'requirement-create') {
          setStateView('error', '尚未绑定项目', '请先在「创建」中上传图纸并保存草稿创建项目，再进入后续步骤。');
          return;
        }
        applyStage(step.entry, { project: state.project });
      });
    });

    const now = $('techNowLabel');
    if (now) now.textContent = current ? current.label : '';
    updateProjectLabel();
    const prev = $('techPrev');
    const next = $('techNext');
    if (prev) prev.disabled = !canNav || currentIdx <= 0;
    if (next) next.disabled = !canNav || currentIdx < 0 || currentIdx >= STAGES.length - 1;
    renderContextSubsteps();
  }

  /* 同源 iframe 内的元素查询：iframe 未加载、跨源或文档不可用时返回 null，不抛异常。 */
  function childQuery(selector) {
    const frame = $('techStageFrame');
    const doc = frame && frame.contentDocument;
    if (!doc || !selector) return null;
    try { return doc.querySelector(selector); } catch (error) { return null; }
  }

  /* 代理页签的 active 以子页面真实状态为准，父壳只回读选择器，不自建状态。 */
  function syncChildTabActive(proxy) {
    const bar = $('techSubstepsBar');
    if (!bar || !proxy) return;
    const active = childQuery(proxy.activeSelector);
    const key = active ? (active.dataset[proxy.keyAttr] || '') : '';
    bar.querySelectorAll('[data-child-tab]').forEach((btn) => {
      btn.classList.toggle('active', Boolean(key) && btn.dataset.childTab === key);
    });
  }

  /* 统一标题行：大标题只读 MAJOR_STEPS[].title（固定名称，不被子页面动态标题或状态
     刷新覆盖），五个大流程都显示这一行；右侧同排一组分步骤/子页面页签。
     流程 1、5 = 父壳自己的 stage 按钮，点击回到 applyStage；
     流程 3、4 = 代理同源 iframe 中既有页签按钮，active 由子页面回读；
     流程 2 右侧为空 —— 只隐藏这组按钮，不隐藏大标题行。 */
  function renderContextSubsteps() {
    const bar = $('techSubstepsBar');
    const header = $('techContextHeader');
    const title = $('techContextTitle');
    if (!bar) return;
    const major = currentMajorStep();
    const substeps = CONTEXT_SUBSTEPS[major.no] || [];
    const proxy = CHILD_TAB_PROXY[state.stage] || null;
    const canNav = Boolean(state.project || state.stage === 'requirement-create');
    if (title) title.textContent = major.title || major.label;
    if (header) header.hidden = false;
    if (!substeps.length && !proxy) {
      bar.hidden = true;
      bar.replaceChildren();
      return;
    }
    bar.hidden = false;
    if (proxy) {
      bar.innerHTML = proxy.tabs.map((tab) =>
        `<button type="button" class="tech-substep-btn" data-child-tab="${escH(tab.key)}">${escH(tab.label)}</button>`).join('');
      bar.querySelectorAll('[data-child-tab]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const tab = proxy.tabs.find((item) => item.key === btn.dataset.childTab);
          if (!tab) return;
          const target = childQuery(tab.selector);
          if (target && !target.disabled) {
            target.click();
            syncChildTabActive(proxy);
          }
        });
      });
      syncChildTabActive(proxy);
      return;
    }
    bar.innerHTML = substeps.map((step) => {
      const cls = ['tech-substep-btn'];
      const done = Boolean(state.progress && state.progress.done && state.progress.done.has(step.stage));
      if (step.stage === state.stage) cls.push('active');
      else if (done) cls.push('done');
      const allowed = canNav || step.stage === 'requirement-create';
      return `<button type="button" class="${cls.join(' ')}" data-substep="${step.stage}" ${allowed ? '' : 'disabled'}>${step.label}</button>`;
    }).join('');
    bar.querySelectorAll('[data-substep]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const target = btn.dataset.substep;
        if (!stages.has(target) || target === state.stage) return;
        if (!state.project && target !== 'requirement-create') {
          setStateView('error', '尚未绑定项目', '请先在「创建」中上传图纸并保存草稿创建项目，再进入后续步骤。');
          return;
        }
        applyStage(target, { project: state.project });
      });
    });
  }

  // 任务显示优先级：title → source_label → task_kind_label → task_no → 关联任务。
  function taskDisplayName(task) {
    if (!task) return '';
    return String(task.title || task.source_label || task.task_kind_label || task.task_no || '').trim();
  }

  // 右上角项目区展示业务名称；原始 project / task id 只放进 tooltip，不做主文字。
  function renderProjectLabel() {
    const label = $('techProjectLabel');
    if (!label) return;
    if (!state.project) {
      label.textContent = '未绑定项目';
      label.title = '';
      return;
    }
    const known = projectNames.get(state.project) || {};
    const projectName = known.project || '未命名项目';
    const taskName = state.taskId ? (known.task || '') : '';
    label.textContent = taskName ? `${projectName} · ${taskName}` : projectName;
    label.title = `project=${state.project}` + (state.taskId ? ` task_id=${state.taskId}` : '');
  }

  function updateProjectLabel() {
    const label = $('techProjectLabel');
    if (!label) return;
    const project = state.project;
    const taskId = state.taskId;
    if (!project) {
      label.textContent = '未绑定项目';
      label.title = '';
      return;
    }
    const cached = projectNames.get(project);
    if (cached && cached.project && (!taskId || cached.task)) {
      renderProjectLabel();
      return;
    }
    label.textContent = '加载项目…';
    label.title = `project=${project}` + (taskId ? ` task_id=${taskId}` : '');
    resolveProjectNames(project, taskId);
  }

  // 项目元数据在 /api/projects/{id} 的 meta 里，需求标题在 /requirement.requirement.title，
  // 任务名称在 /wf/task.task；三者并行取，失败不清掉已拿到的名称。
  async function resolveProjectNames(project, taskId) {
    const id = encodeURIComponent(project);
    const [projectData, requirementData, taskData] = await Promise.all([
      fetch(`/api/projects/${id}`, { headers: authHeaders() })
        .then((response) => response.ok ? response.json() : null).catch(() => null),
      fetch(`/api/projects/${id}/requirement`, { headers: authHeaders() })
        .then((response) => response.ok ? response.json() : null).catch(() => null),
      taskId
        ? fetch(`/wf/task?task_id=${encodeURIComponent(taskId)}`, { headers: authHeaders() })
          .then((response) => response.ok ? response.json() : null).catch(() => null)
        : Promise.resolve(null),
    ]);
    // 竞态：返回时用户可能已经切了项目 / 任务，晚到的结果不能覆盖当前目标。
    if (state.project !== project || state.taskId !== taskId) return;
    const entry = projectNames.get(project) || {};
    const data = projectData || {};
    const requirement = (requirementData && requirementData.requirement) || {};
    const projectName = String(
      (data.meta && data.meta.project_name)
      || (requirement && requirement.title)
      || (data.meta && data.meta.device_name)
      || (data.meta && data.meta.source_filename)
      || '').trim();
    if (projectName) entry.project = projectName;
    const task = (taskData && taskData.task) || null;
    const taskName = taskDisplayName(task);
    if (taskName) entry.task = taskName;
    if (entry.project || entry.task) projectNames.set(project, entry);
    renderProjectLabel();
  }

  /* ---------------------------------------------------------- 状态区 */
  function setStateView(kind, title, message, actions) {
    const outlet = $('techWorkspaceOutlet');
    const frame = $('techStageFrame');
    if (frame) frame.remove();
    let html = `<div class="tech-wb-state ${kind === 'loading' ? '' : 'error'}">`;
    if (kind === 'loading') {
      html += '<div class="tech-wb-spinner"></div>';
    } else {
      html += '<div class="tech-wb-state-icon">⚠️</div>';
    }
    if (title) html += `<div class="tech-wb-state-title">${escH(title)}</div>`;
    if (message) html += `<div>${escH(message)}</div>`;
    if (actions && actions.length) {
      html += '<div class="tech-wb-state-actions">' + actions.map((action) =>
        `<button type="button" class="tech-wb-btn primary" data-wb-action="${escH(action.id)}">${escH(action.label)}</button>`).join('') + '</div>';
    }
    html += '</div>';
    outlet.innerHTML = html;
    outlet.querySelectorAll('[data-wb-action]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const action = (actions || []).find((item) => item.id === btn.dataset.wbAction);
        if (action && action.href) window.location.href = action.href;
        else if (action && action.stage) applyStage(action.stage, { project: state.project });
      });
    });
    syncActionBar(null);
  }
  function escH(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  /* ---------------------------------------------------------- 工作台出口 */
  function childUrl(stageId) {
    const meta = stageMeta(stageId);
    const q = new URLSearchParams();
    if (state.project) q.set('project', state.project);
    q.set('stage', stageId);
    if (state.taskId) q.set('task_id', state.taskId);
    q.set('embed', '1');
    extraParams().forEach(([key, value]) => q.set(key, value));
    let page = meta.page;
    // 新增工艺待办尚无 project：右侧以 embed=1 承载原 tech-task.html 建项流程，
    // 建项成功后由子页把真实 project 回写统一壳 URL，全程不离开两栏布局。
    if (stageId === 'requirement-create' && !state.project && state.taskId) {
      page = 'tech-task.html';
    }
    return `${page}?${q.toString()}`;
  }

  function mountStageFrame() {
    const outlet = $('techWorkspaceOutlet');
    if (!outlet) return;
    const meta = stageMeta(state.stage);
    if (!meta) {
      setStateView('error', '未知步骤', '当前 stage 不在固定白名单内，已拒绝加载。', [
        { id: 'go-create', label: '去「创建」', stage: 'requirement-create' },
        { id: 'go-home', label: '返回技术工艺首页', href: 'home.html' },
      ]);
      return;
    }
    if (!state.project && state.stage !== 'requirement-create') {
      setStateView('error', '缺少项目', 'URL 中未提供 project，工作台不会创建匿名项目。请先进入「创建」，或从项目列表进入。', [
        { id: 'go-create', label: '去「创建」', stage: 'requirement-create' },
        { id: 'go-home', label: '返回技术工艺首页', href: 'home.html' },
      ]);
      return;
    }
    outlet.innerHTML = '';
    if (!state.project) {
      const banner = document.createElement('div');
      banner.className = 'tech-wb-banner';
      banner.textContent = state.taskId
        ? '新增工艺任务：本步承载任务详情与图纸上传；创建项目后将自动进入「创建」并绑定左侧会话。'
        : '尚未绑定项目：请在本步上传 2D 工程图并保存草稿，系统会自动创建项目并绑定到左侧会话。';
      outlet.append(banner);
    }
    const iframe = document.createElement('iframe');
    iframe.id = 'techStageFrame';
    iframe.title = meta.label;
    iframe.setAttribute('data-stage', state.stage);
    iframe.src = childUrl(state.stage);
    iframe.addEventListener('load', () => {
      syncActionBar();
      syncAgentStageContext();
      // 子页面首次渲染完成后回读它真实的页签 active（流程 3、4 的代理页签）。
      renderContextSubsteps();
    });
    outlet.append(iframe);
    syncActionBar();
  }

  /* 底栏主/次操作代理：在子页面 DOM 上找按钮，读状态并转发点击。 */
  function syncActionBar() {
    const actions = STAGE_ACTIONS[state.stage] || null;
    const primary = $('techPrimary');
    const secondary = $('techSecondary');
    // 每次同步先清掉上一轮角色 class，避免切换 stage 或子页面重建后残留。
    if (primary) primary.classList.remove('is-filled', 'is-outline');
    if (secondary) secondary.classList.remove('is-filled', 'is-outline');
    if (!actions) {
      if (primary) primary.hidden = true;
      if (secondary) secondary.hidden = true;
      return;
    }
    const frame = $('techStageFrame');
    const doc = frame && frame.contentDocument;
    const probe = (selector) => {
      try { return doc && doc.querySelector(selector); } catch (e) { return null; }
    };
    // 2.2「组装与整合」：按子页面真实产出反转主次 —— 参数推荐与组装工艺都已生成时，
    // 主操作才是「确认工艺并发送财务」；只生成一项、busy、失败或尚未分析时，
    // 主操作仍是「开始整合分析」。状态来自子页面 dataset，不按按钮文案猜测，
    // 也不等同于 #aiToFinance 是否可点（确认闸门仍由子页面自己控制）。
    if (state.stage === 'process' && primary && secondary) {
      const analyzed = Boolean(doc && doc.body && doc.body.dataset
        && doc.body.dataset.integrationAnalyzed === 'true');
      if (analyzed) {
        primary.classList.add('is-filled');
        secondary.classList.add('is-outline');
      } else {
        secondary.classList.add('is-filled');
        primary.classList.add('is-outline');
      }
    }
    if (primary) {
      primary.hidden = !actions.primary;
      if (actions.primary) {
        primary.textContent = actions.primaryLabel || '执行';
        const el = probe(actions.primary);
        primary.disabled = !el || el.disabled || !state.project;
        primary.onclick = () => {
          const current = probe(actions.primary);
          if (current && !current.disabled) current.click();
        };
      }
    }
    if (secondary) {
      secondary.hidden = !actions.secondary;
      if (actions.secondary) {
        secondary.textContent = actions.secondaryLabel || '次要操作';
        const el = probe(actions.secondary);
        secondary.disabled = !el || el.disabled || !state.project;
        secondary.onclick = () => {
          const current = probe(actions.secondary);
          if (current && !current.disabled) current.click();
        };
      }
    }
  }

  /* ---------------------------------------------------- 左侧唯一 Agent 会话栏 */
  /* 阶段上下文只写进父页唯一的 #techChatPane：不克隆子页面 DOM、不清空历史会话、
     草稿或项目绑定；接口缺失、iframe 未加载或抛错时安全退出，流程切换照常。 */
  function stageAgentContext() {
    const context = STAGE_AGENT_CONTEXT[state.stage];
    if (!context) return null;
    const routing = STAGE_ACTIONS[state.stage] || {};
    const actions = (context.actions || []).map((role) => {
      const selector = role === 'secondary' ? routing.secondary : routing.primary;
      const label = (role === 'secondary' ? routing.secondaryLabel : routing.primaryLabel) || '';
      return selector && label ? { role, selector, label } : null;
    }).filter(Boolean);
    return {
      stage: state.stage,
      project: state.project,
      taskId: state.taskId,
      label: context.label,
      pageContext: context.pageContext,
      hint: context.hint,
      actions,
    };
  }

  function syncAgentStageContext() {
    const pane = $('techChatPane');
    if (!pane) return;
    let context = null;
    try { context = stageAgentContext(); } catch (error) { context = null; }
    // 父页先执行、agent-chat.js 后加载：除直接调用外再留一份全局供其首次承接。
    window.ocTechStageContext = context;
    try {
      const api = window.ocTechAgent;
      if (api && typeof api.setStageContext === 'function') api.setStageContext(context);
      window.dispatchEvent(new CustomEvent('cpq:tech-agent:stage-context', { detail: context }));
    } catch (error) {
      /* 上下文同步失败不影响会话与流程切换 */
    }
  }

  // 左侧上下文里的操作按钮只回传角色，由父壳点同源 iframe 中的既有业务按钮。
  window.addEventListener('cpq:tech-agent:stage-action', (event) => {
    const role = ((event.detail || {}).role) || 'primary';
    const routing = STAGE_ACTIONS[state.stage] || {};
    const selector = role === 'secondary' ? routing.secondary : routing.primary;
    const frame = $('techStageFrame');
    const doc = frame && frame.contentDocument;
    if (!selector || !doc) return;
    let target = null;
    try { target = doc.querySelector(selector); } catch (error) { target = null; }
    if (target && !target.disabled) target.click();
  });

  /* ---------------------------------------------------------- 步骤切换 */
  function applyStage(stageId, opts) {
    if (!stages.has(stageId)) {
      setStateView('error', '未知步骤', `stage=${stageId} 不在白名单内，已拒绝加载。`);
      return;
    }
    if (opts && opts.project) state.project = opts.project;
    if (opts && opts.taskId) state.taskId = opts.taskId;
    state.stage = stageId;
    renderTop();
    mountStageFrame();
    pushState();
    refreshProgress();
    syncAgentStageContext();
  }

  const prevBtn = $('techPrev');
  const nextBtn = $('techNext');
  if (prevBtn) prevBtn.addEventListener('click', () => {
    const idx = stageIndex(state.stage);
    if (idx <= 0) return;
    const target = STAGES[idx - 1];
    if (!state.project && target.id !== 'requirement-create') return;
    applyStage(target.id, { project: state.project });
  });
  if (nextBtn) nextBtn.addEventListener('click', () => {
    const idx = stageIndex(state.stage);
    if (idx < 0 || idx >= STAGES.length - 1) return;
    const target = STAGES[idx + 1];
    if (!state.project && target.id !== 'requirement-create') {
      setStateView('error', '尚未绑定项目', '请先在「创建」中保存草稿创建项目，再进入下一步。');
      return;
    }
    applyStage(target.id, { project: state.project });
  });
  /* ---------------------------------------------------------- 子页面导航消息 */
  window.addEventListener('message', (event) => {
    if (event.origin !== location.origin) return;              // 只接受同源消息
    const data = event.data || {};
    if (data.type === 'cpq:tech-workbench:exit') {
      const target = typeof data.url === 'string' && data.url ? data.url : 'home.html';
      window.location.href = target;
      return;
    }
    if (data.type !== 'cpq:tech-workbench:navigate') return;
    if (!stages.has(data.stage)) return;                       // stage 白名单校验
    applyStage(data.stage, {
      project: data.project || state.project,
      taskId: data.task_id || state.taskId,
    });
  });

  /* ---------------------------------------------------------- 后端进度（只读）
   * 步骤完成状态来自既有 /workflow、/summary 数据，仅用于步骤条打点，不冒充完成。 */
  function authHeaders() {
    const token = localStorage.getItem('authToken') || localStorage.getItem('cad_engine_token');
    return token ? { Authorization: `Bearer ${token}` } : {};
  }
  async function refreshProgress() {
    if (!state.project) return;
    const id = encodeURIComponent(state.project);
    try {
      const results = await Promise.all([
        fetch(`/api/projects/${id}/workflow`, { headers: authHeaders() }).then((r) => r.ok ? r.json() : {}),
        fetch(`/api/projects/${id}/summary`, { headers: authHeaders() }).then((r) => r.ok ? r.json() : {}),
      ]);
      const wf = results[0] || {};
      const aggregate = results[1] || {};
      const reqStatus = (wf.requirement || {}).status || '';
      const reportStatus = (wf.report || {}).status || '';
      const summary = wf.summary || {};
      const parts = (aggregate.aggregate && aggregate.aggregate.steps && aggregate.aggregate.steps.ir) ||
                    ((aggregate.steps || {}).ir);
      const irParts = (parts && parts.parts) || [];
      const integration = (aggregate.steps || {}).integration || {};
      const costReview = (aggregate.steps || {}).cost_review || {};
      const done = new Set();
      if (reqStatus) done.add('requirement-create');
      if (['pending_review', 'approved'].includes(reqStatus)) done.add('requirement-confirm');
      if (reqStatus === 'approved') done.add('requirement-review');
      if (irParts.length) done.add('drawing');
      if ((integration.process && integration.process.steps && integration.process.steps.length)) done.add('process');
      if (costReview.confirmed) done.add('cost');
      if (summary.confirmed_at || ['in_review', 'approved', 'published'].includes(reportStatus)) done.add('summary');
      if (['approved', 'published'].includes(reportStatus)) done.add('report-review');
      if (reportStatus === 'published') done.add('report-publish');
      state.progress = { done };
    } catch (e) {
      state.progress = { done: new Set() };
    }
    renderTop();
  }
  setInterval(() => syncActionBar(), 2500);

  /* ---------------------------------------------------------- 模型设置
   * 右上角模型文字与左侧「设置」共用同一个打开函数，卡片内容复用
   * llm-settings-panel.js（与首页、报价助手是同一份表单），不复制第二套。
   * 保存后立即刷新右上角模型文字；权限只读态由接口的 editable / secrets_editable 决定。 */
  function techModelLabel(settings) {
    const options = (settings && settings.text_options) || [];
    const id = String((settings && settings.text_model) || '').trim();
    const found = options.find((item) => item && item.id === id);
    return (found && found.label) || id || '未配置模型';
  }
  async function refreshTechModelLabel() {
    const node = $('techModelInfo');
    if (!node) return;
    try {
      let settings = null;
      if (window.LlmSettingsPanel && typeof window.LlmSettingsPanel.load === 'function') {
        settings = await window.LlmSettingsPanel.load();
      } else {
        const response = await fetch('/api/llm/settings', { headers: authHeaders() });
        settings = await response.json().catch(() => ({}));
      }
      const label = techModelLabel(settings);
      node.textContent = label === '未配置模型' ? label : `· ${label}`;
    } catch (error) {
      node.textContent = '· 未配置模型';
    }
  }

  let techSettingsPop = null;
  function closeTechSettings() {
    if (!techSettingsPop) return;
    techSettingsPop.remove();
    techSettingsPop = null;
    document.removeEventListener('click', closeTechSettings);
    document.removeEventListener('keydown', onTechSettingsKey);
  }
  function onTechSettingsKey(event) {
    if (event.key === 'Escape') closeTechSettings();
  }
  function openTechModelSettings(anchor) {
    if (!window.LlmSettingsPanel || typeof window.LlmSettingsPanel.mount !== 'function') {
      setStateView('error', '设置未就绪', '模型设置面板尚未加载，请稍后重试。');
      return;
    }
    closeTechSettings();
    const pop = document.createElement('div');
    pop.className = 'tech-model-settings';
    pop.id = 'techModelSettings';
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', '技术工艺模型设置');
    // 点卡片内部（下拉、输入框）不关闭；点外部或按 Esc 关闭。
    pop.addEventListener('click', (event) => event.stopPropagation());
    const host = document.createElement('div');
    host.className = 'llm-set-host';
    pop.append(host);
    document.body.append(pop);
    const rect = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
    const width = pop.offsetWidth || Math.min(420, window.innerWidth - 24);
    const height = pop.offsetHeight || 420;
    const left = rect ? Math.max(8, Math.min(rect.left, window.innerWidth - width - 12)) : 24;
    const below = rect ? rect.bottom + 8 : 24;
    const top = below + height <= window.innerHeight
      ? below
      : Math.max(8, (rect ? rect.top : 24) - height - 8);
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
    techSettingsPop = pop;
    window.LlmSettingsPanel.mount(host, { onSaved: () => refreshTechModelLabel() });
    setTimeout(() => {
      document.addEventListener('click', closeTechSettings);
      document.addEventListener('keydown', onTechSettingsKey);
    }, 0);
  }

  /* ---------------------------------------------------------- 左侧导航
   * Logo / 返回主页是普通链接；新对话复用 agent-chat 的会话重置能力（ocTechAgent），
   * 消息与账户复用 cpqMsg / cpqAuth，设置复用与报价助手同一份模型设置面板。 */
  function bindTechNav() {
    const newChat = $('techNewChat');
    if (newChat) newChat.addEventListener('click', () => {
      if (!state.project) {
        setStateView('error', '尚未绑定项目', '新对话需要先绑定项目：请先在「创建」中上传图纸并保存草稿。');
        return;
      }
      if (window.ocTechAgent && typeof window.ocTechAgent.resetTask === 'function') {
        window.ocTechAgent.resetTask();
      } else {
        setStateView('error', '会话未就绪', '对话组件尚未完成加载，请稍后重试。');
      }
    });
    const history = $('techHistory');
    if (history) history.addEventListener('click', openTechHistory);
    const msg = $('techMsgBtn');
    if (msg) msg.addEventListener('click', () => {
      if (window.cpqMsg && typeof window.cpqMsg.open === 'function') window.cpqMsg.open();
    });
    const settings = $('techSettings');
    if (settings) settings.addEventListener('click', () => openTechModelSettings(settings));
    const modelTrigger = $('techModelInfo');
    if (modelTrigger) modelTrigger.addEventListener('click', () => openTechModelSettings(modelTrigger));
    const auth = $('techAuthBtn');
    if (auth) auth.addEventListener('click', () => {
      if (window.cpqAuth && typeof window.cpqAuth.open === 'function') window.cpqAuth.open();
    });
  }

  /* ---------------------------------------------------------- 技术项目历史
   * “历史记录”打开当前用户的技术项目历史抽屉：项目列表来自既有 /api/projects，
   * 点击项目后按既有 /workflow 与项目数据换算当前 stage，在同一壳内恢复。 */
  let historyTimer = 0;
  function ensureHistoryUi() {
    if ($('techHistoryDrawer')) return;
    const mask = document.createElement('div');
    mask.className = 'tech-history-mask';
    mask.id = 'techHistoryMask';
    mask.hidden = true;
    mask.addEventListener('click', closeTechHistory);
    const drawer = document.createElement('aside');
    drawer.className = 'tech-history-drawer';
    drawer.id = 'techHistoryDrawer';
    drawer.hidden = true;
    drawer.setAttribute('aria-label', '技术项目历史');
    drawer.innerHTML =
      '<div class="tech-history-head">' +
      '<span class="tech-history-title"><i class="ti ti-history" aria-hidden="true"></i>技术项目历史</span>' +
      '<button type="button" class="tech-history-close" aria-label="关闭历史抽屉">×</button>' +
      '</div>' +
      '<div class="tech-history-list" id="techHistoryList"></div>';
    drawer.querySelector('.tech-history-close').addEventListener('click', closeTechHistory);
    document.body.append(mask, drawer);
  }
  function openTechHistory() {
    ensureHistoryUi();
    const mask = $('techHistoryMask');
    const drawer = $('techHistoryDrawer');
    if (!mask || !drawer) return;
    mask.hidden = false;
    drawer.hidden = false;
    loadTechHistory();
  }
  function closeTechHistory() {
    const mask = $('techHistoryMask');
    const drawer = $('techHistoryDrawer');
    if (!mask || !drawer) return;
    window.clearTimeout(historyTimer);
    historyTimer = window.setTimeout(() => {
      mask.hidden = true;
      drawer.hidden = true;
    }, 200);
  }
  async function loadTechHistory() {
    ensureHistoryUi();
    const box = $('techHistoryList');
    if (!box) return;
    box.innerHTML = '<div class="tech-history-empty">加载历史项目…</div>';
    let projects = [];
    try {
      const response = await fetch('/api/projects', { headers: authHeaders() });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json().catch(() => ({}));
      projects = Array.isArray(data) ? data : (data && Array.isArray(data.projects) ? data.projects : []);
    } catch (error) {
      box.innerHTML = `<div class="tech-history-empty error">无法读取技术项目历史：${escH(error.message)}</div>`;
      return;
    }
    if (!projects.length) {
      box.innerHTML = '<div class="tech-history-empty">还没有技术项目历史。<br/>在技术工艺主页上传图纸或新建工艺评估后，会出现在这里。</div>';
      return;
    }
    const rows = projects.filter((row) => row && (row.project_id || row.id));
    rows.sort((a, b) => String(b.created_at || b.updated_at || '').localeCompare(String(a.created_at || a.updated_at || '')));
    box.innerHTML = '';
    rows.forEach((row) => {
      const id = row.project_id || row.id;
      const title = row.project_name || row.device_name || row.source_filename || `PRJ-${id}`;
      const meta = `PRJ-${id}${row.created_at ? ` · ${String(row.created_at).slice(0, 10)}` : ''}`;
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'tech-history-item';
      item.innerHTML = '<span class="tech-history-item-title"></span><span class="tech-history-item-meta"></span>';
      item.querySelector('.tech-history-item-title').textContent = title;
      item.querySelector('.tech-history-item-meta').textContent = meta;
      item.addEventListener('click', () => techHistoryRestore(id));
      box.append(item);
    });
  }
  function techStageFromProject(flow, project) {
    const status = String(((flow.requirement || {}).status) || '').trim();
    if (status === 'draft' || status === 'rejected') return 'requirement-create';
    if (status === 'pending_confirmation') return 'requirement-confirm';
    if (status === 'pending_review') return 'requirement-review';
    const report = flow.report || null;
    if (report) {
      if (report.status === 'in_review') return 'report-review';
      if (report.status === 'approved' || report.status === 'published') return 'report-publish';
      return 'summary';
    }
    const hasIr = Boolean(
      (project.ir && project.ir.parts && project.ir.parts.length) ||
      (project.meta && project.meta.has_ir) || project.has_ir ||
      (project.stages && project.stages.parsed),
    );
    return hasIr ? 'process' : 'drawing';
  }
  async function techHistoryRestore(projectId) {
    if (!projectId) return;
    closeTechHistory();
    try {
      const results = await Promise.all([
        fetch(`/api/projects/${encodeURIComponent(projectId)}/workflow`, { headers: authHeaders() })
          .then((r) => r.ok ? r.json() : {}),
        fetch(`/api/projects/${encodeURIComponent(projectId)}`, { headers: authHeaders() })
          .then((r) => r.ok ? r.json() : {}),
      ]);
      const stage = techStageFromProject(results[0] || {}, results[1] || {});
      applyStage(stage, { project: projectId });
    } catch (error) {
      setStateView('error', '无法恢复历史项目', `打开项目 ${projectId} 失败：${error.message}`, [
        { id: 'go-create', label: '去「创建」', stage: 'requirement-create' },
      ]);
    }
  }

  /* ---------------------------------------------------------- 启动 */
  readFromUrl();
  if (!state.stage) state.stage = 'requirement-create';
  renderTop();
  mountStageFrame();
  syncAgentStageContext();
  bindTechNav();
  refreshTechModelLabel();
  if (state.project) refreshProgress();
})();
