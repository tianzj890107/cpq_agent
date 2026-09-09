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

  const IGNORE_PARAMS = new Set(['project', 'stage', 'task_id', 'embed', 'embedding']);

  const state = { stage: '', project: '', taskId: '', progress: null };

  function params() { return new URLSearchParams(location.search); }

  function readFromUrl() {
    const q = params();
    state.project = q.get('project') || '';
    state.taskId = q.get('task_id') || '';
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
    render();
    mountStageFrame();
  });

  /* ---------------------------------------------------------- 渲染 */
  function renderTop() {
    const bar = $('techStepsBar');
    if (!bar) return;
    const current = stageMeta(state.stage);
    const currentIdx = stageIndex(state.stage);
    const canNav = Boolean(state.project || state.stage === 'requirement-create');
    let html = '';
    STAGES.forEach((stage, idx) => {
      if (idx === 0 || STAGES[idx - 1].phase !== stage.phase) {
        if (idx) html += '<span class="tech-phase-arrow">›</span>';
        html += `<div class="tech-step-phase"><span class="tech-phase-title">${stage.phaseTitle}</span><div class="tech-phase-steps">`;
      }
      const cls = ['tech-step-btn'];
      if (stage.id === state.stage) cls.push('active');
      else if (state.progress && state.progress.done && state.progress.done.has(stage.id)) cls.push('done');
      const allowed = canNav || stage.id === 'requirement-create';
      html += `<button type="button" class="${cls.join(' ')}" data-stage="${stage.id}" ${allowed ? '' : 'disabled'} title="${stage.no} ${stage.label}">` +
        `<span class="tech-step-no">${stage.no}</span>${stage.label}</button>`;
      if (idx === STAGES.length - 1 || STAGES[idx + 1].phase !== stage.phase) html += '</div></div>';
    });
    bar.innerHTML = html;
    bar.querySelectorAll('[data-stage]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (state.stage === btn.dataset.stage) return;
        if (!state.project && btn.dataset.stage !== 'requirement-create') {
          setStateView('error', '尚未绑定项目', '请先在 1.1 创建中上传图纸并保存草稿创建项目，再进入后续步骤。');
          return;
        }
        applyStage(btn.dataset.stage, { project: state.project });
      });
    });

    const phase = current ? `${current.phase} · ${current.phaseTitle}` : '';
    const phaseLabel = $('techPhaseLabel');
    if (phaseLabel) phaseLabel.textContent = phase ? `当前：${phase}` : '';
    const now = $('techNowLabel');
    if (now) now.textContent = current ? `${current.no} ${current.label}` : '';
    const projectLabel = $('techProjectLabel');
    if (projectLabel) {
      projectLabel.textContent = state.project ? `项目 ${state.project}${state.taskId ? ` · 任务 ${state.taskId}` : ''}` : '未绑定项目';
    }
    const prev = $('techPrev');
    const next = $('techNext');
    if (prev) prev.disabled = !canNav || currentIdx <= 0;
    if (next) next.disabled = !canNav || currentIdx < 0 || currentIdx >= STAGES.length - 1;
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
    return `${meta.page}?${q.toString()}`;
  }

  function mountStageFrame() {
    const outlet = $('techWorkspaceOutlet');
    if (!outlet) return;
    const meta = stageMeta(state.stage);
    if (!meta) {
      setStateView('error', '未知步骤', '当前 stage 不在固定白名单内，已拒绝加载。', [
        { id: 'go-create', label: '去 1.1 创建', stage: 'requirement-create' },
        { id: 'go-home', label: '返回技术工艺首页', href: 'home.html' },
      ]);
      return;
    }
    if (!state.project && state.stage !== 'requirement-create') {
      setStateView('error', '缺少项目', 'URL 中未提供 project，工作台不会创建匿名项目。请先进入 1.1 创建，或从项目列表进入。', [
        { id: 'go-create', label: '去 1.1 创建', stage: 'requirement-create' },
        { id: 'go-home', label: '返回技术工艺首页', href: 'home.html' },
      ]);
      return;
    }
    outlet.innerHTML = '';
    if (!state.project) {
      const banner = document.createElement('div');
      banner.className = 'tech-wb-banner';
      banner.textContent = '尚未绑定项目：请在本步上传 2D 工程图并保存草稿，系统会自动创建项目并绑定到左侧会话。';
      outlet.append(banner);
    }
    const stateLine = document.createElement('div');
    stateLine.className = 'tech-wb-state has-frame';
    stateLine.innerHTML = '<div class="tech-wb-spinner" style="width:14px;height:14px;border-width:2px"></div><div id="techStageMessage">正在加载本步页面…</div>';
    outlet.append(stateLine);
    const iframe = document.createElement('iframe');
    iframe.id = 'techStageFrame';
    iframe.title = `${meta.no} ${meta.label}`;
    iframe.setAttribute('data-stage', state.stage);
    iframe.src = childUrl(state.stage);
    iframe.addEventListener('load', () => {
      const msg = outlet.querySelector('#techStageMessage');
      if (msg) msg.textContent = `${meta.no} ${meta.label} 已就绪`;
      syncActionBar();
    });
    outlet.append(iframe);
    syncActionBar();
  }

  /* 底栏主/次操作代理：在子页面 DOM 上找按钮，读状态并转发点击。 */
  function syncActionBar() {
    const actions = STAGE_ACTIONS[state.stage] || null;
    const primary = $('techPrimary');
    const secondary = $('techSecondary');
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
  }

  const prevBtn = $('techPrev');
  const nextBtn = $('techNext');
  const panelToggle = $('techPanelToggle');
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
      setStateView('error', '尚未绑定项目', '请先在 1.1 创建中保存草稿创建项目，再进入下一步。');
      return;
    }
    applyStage(target.id, { project: state.project });
  });
  if (panelToggle) panelToggle.addEventListener('click', () => {
    const frame = $('techStageFrame');
    if (!frame || !frame.contentDocument) return;
    const root = frame.contentDocument.documentElement;
    root.classList.toggle('show-child-chat');
    panelToggle.textContent = root.classList.contains('show-child-chat') ? '收起子页面板' : '子页面板';
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

  /* ---------------------------------------------------------- 启动 */
  readFromUrl();
  if (!state.stage) state.stage = 'requirement-create';
  renderTop();
  mountStageFrame();
  if (state.project) refreshProgress();
})();
