/* 【CPQ 定制 · 本文件不来自 process_drawing，同步上游时保留】
 *
 * 「新增工艺」任务专属页（/tech-task.html?tech_task=<task_id>）。
 *
 * 报价助手第 1 步匹配不到合适标品（cpq_match 最高分低于 70）时，销售经理发一条
 * task_kind=tech_new_product 的任务给工艺经理。工艺经理在报价首页或技术工艺首页的
 * 「待办任务」点开这条任务，就落到这一页。
 *
 * 原来这些内容是以横幅的形式插在技术工艺首页（home.html?tech_task=…）上的：任务详情
 * 和"我的清单/全部清单"挤在一屏，建单入口还得借用首页那个通用输入框。现在独立成页 ——
 * 首页只管把任务列出来，点开跳到这里，任务编码、客户需求、需求文档、图纸上传、建单
 * 一屏闭环。旧的 home.html?tech_task=… 链接由 cpq-tech-task.js 转到这里，不会失效。
 *
 * 建单走的还是首页那条接口（POST /api/projects → PUT 需求单 → 跳 1.1），不另起一套：
 * 两套建单逻辑迟早会跑偏。区别只是这里额外把报价来源（任务编码/会话）写进需求单，
 * 后面每一步都能追回这条报价是从哪来的。
 */
(function () {
  'use strict';

  const CPQ_TOKEN_KEY = 'cpq_auth_token';
  const taskId = new URLSearchParams(location.search).get('tech_task')
    || new URLSearchParams(location.search).get('task') || '';

  let task = null;
  const modelFiles = [];
  const documentFiles = [];

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function token() { try { return localStorage.getItem(CPQ_TOKEN_KEY) || ''; } catch (e) { return ''; } }
  function host() { return document.getElementById('app'); }

  function toast(message, isError) {
    const box = document.createElement('div');
    box.className = 'tt-toast' + (isError ? ' error' : '');
    box.textContent = message;
    document.body.appendChild(box);
    setTimeout(() => box.remove(), isError ? 4200 : 2600);
  }

  function fileSize(size) {
    if (!Number.isFinite(size)) return '';
    return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB`
      : `${(size / 1024 / 1024).toFixed(1)} MB`;
  }

  /* ------------------------------------------------------------------ 读任务 */
  /* 任务详情走 /wf/task（一体化服务的报价工作流接口，与本页同源）。技术工艺自己
     不连那套库，分工见 backend/services/cpq_sso.py。 */
  async function fetchTask() {
    const response = await fetch('/wf/task?task_id=' + encodeURIComponent(taskId), {
      headers: { Authorization: 'Bearer ' + token() },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `读取任务失败（${response.status}）`);
    return data.task;
  }

  /* ------------------------------------------------------------------ 渲染 */
  function docsHtml(documents) {
    if (!documents || !documents.length) {
      return '<div class="tt-row"><b>需求文档</b>'
        + '<div class="tt-text">本单没有随需求上传文档。</div></div>';
    }
    return '<div class="tt-row"><b>需求文档（' + documents.length + ' 份，随任务带过来的正文摘要）</b>'
      + documents.map(doc =>
        '<details class="tt-doc"><summary>' + esc(doc.name || '未命名文档')
        + (doc.truncated ? '（较长，已截断）' : '') + '</summary>'
        + '<pre>' + esc(doc.excerpt || doc.error || '（没有可读正文，请回报价会话查看原件）') + '</pre>'
        + '</details>').join('') + '</div>';
  }

  function render() {
    const payload = task.payload || {};
    const match = payload.match || {};
    host().innerHTML =
      '<h1 class="tt-title">新增工艺任务</h1>'
      + '<p class="tt-sub">报价那边没有匹配到合适的标准品，需要按客户需求新开一条工艺。'
      + '请在本页上传图纸并建单，后续进入 1.1 → 2.1 图纸解析 → 2.2 组装与整合。</p>'
      + '<section class="tt-card">'
      + '<div class="tt-head"><span>任务编码</span>'
      + '<span class="tt-no">' + esc(task.task_no || task.task_id) + '</span>'
      + '<span>来自报价「' + esc(task.title || '未命名报价') + '」'
      + (task.customer ? ' · 客户 ' + esc(task.customer) : '') + '</span>'
      + '<span class="tt-from">' + esc(task.from_display_name || '') + '（'
      + esc(task.from_role_name || '') + '）转来</span></div>'
      + '<div class="tt-body">'
      + (match.below_threshold
          ? '<div class="tt-row"><div class="tt-warn">⚠ 标品最高匹配分 '
            + esc(String(match.best_score == null ? '—' : match.best_score)) + ' 分，低于 '
            + esc(String(match.threshold == null ? 70 : match.threshold)) + ' 分'
            + (match.best_code ? '（最接近：' + esc(match.best_code) + ' ' + esc(match.best_name || '') + '）' : '')
            + '。' + esc(match.advice || '需要新建产品。') + '</div></div>'
          : '')
      + (task.note ? '<div class="tt-row"><b>销售备注</b><div class="tt-text">'
          + esc(task.note) + '</div></div>' : '')
      + '<div class="tt-row"><b>客户需求</b><div class="tt-text">'
      + esc(payload.requirement_text || '（报价会话没有留下需求正文）') + '</div></div>'
      + docsHtml(payload.documents)
      + '</div></section>'
      + '<section class="tt-panel">'
      + '<h2>上传图纸，创建工艺评估需求</h2>'
      + '<p class="tt-tip">图纸是必填项 —— 2.1 的解析、零件拆分与成本测算全部由它起头。'
      + '需求正文已按报价原文预填，可以直接改。</p>'
      + '<div class="tt-uploads">'
      + '<label class="tt-upload">＋ 上传模型图纸'
      + '<input type="file" id="ttModel" multiple '
      + 'accept="image/*,.pdf,.dwg,.dxf,.step,.stp,.sldprt,.stl,.sat"></label>'
      + '<label class="tt-upload">＋ 上传需求文档'
      + '<input type="file" id="ttDocs" multiple '
      + 'accept="image/*,.pdf,.txt,.md,.csv,.doc,.docx"></label>'
      + '</div>'
      + '<div class="tt-files" id="ttFiles"></div>'
      + '<label class="tt-field"><span>需求描述</span>'
      + '<textarea id="ttNote" rows="5" placeholder="客户需求、技术要求、交期等"></textarea></label>'
      + '<div class="tt-actions">'
      + '<button type="button" class="tt-primary" id="ttCreate">创建需求并开始</button>'
      + '<a class="tt-ghost" href="home.html">返回首页</a>'
      + '<span class="tt-note">建单后任务编码会写进需求单，报价那边可随时追溯。</span>'
      + '</div></section>';

    document.getElementById('ttModel').onchange = event => addFiles(event, modelFiles);
    document.getElementById('ttDocs').onchange = event => addFiles(event, documentFiles);
    document.getElementById('ttCreate').onclick = create;
    // 报价原文可能很长，这里不像首页那样限 200 字：需求单本身没有这个限制。
    document.getElementById('ttNote').value = (payload.requirement_text || '').trim();
    renderFiles();
  }

  function addFiles(event, bucket) {
    const added = [...event.target.files].filter(file => !bucket.some(existing =>
      existing.name === file.name && existing.size === file.size
      && existing.lastModified === file.lastModified));
    bucket.push(...added);
    event.target.value = '';
    renderFiles();
  }

  function renderFiles() {
    const list = document.getElementById('ttFiles');
    if (!list) return;
    const rows = [
      ...modelFiles.map((file, index) => ({ file, index, kind: 'model', label: '模型图纸' })),
      ...documentFiles.map((file, index) => ({ file, index, kind: 'doc', label: '需求文档' })),
    ];
    list.innerHTML = rows.length
      ? rows.map(row => '<div class="tt-file"><span>' + esc(row.file.name) + '</span>'
          + '<i>' + row.label + (row.file.size ? ' · ' + fileSize(row.file.size) : '') + '</i>'
          + '<button type="button" data-kind="' + row.kind + '" data-index="' + row.index
          + '" aria-label="移除">×</button></div>').join('')
      : '<p class="tt-required">还没有图纸，请先上传至少一份模型图纸。</p>';
    list.querySelectorAll('button[data-kind]').forEach(button => {
      button.onclick = () => {
        const bucket = button.dataset.kind === 'model' ? modelFiles : documentFiles;
        bucket.splice(Number(button.dataset.index), 1);
        renderFiles();
      };
    });
  }

  /* ------------------------------------------------------------------ 建单 */
  async function create() {
    const file = modelFiles[0];
    if (!file) { toast('请先上传至少一份模型图纸，再创建工艺需求。', true); return; }
    const description = document.getElementById('ttNote').value.trim();
    const button = document.getElementById('ttCreate');
    button.disabled = true;
    button.textContent = '正在创建…';
    try {
      const form = new FormData();
      form.append('file', file);
      modelFiles.slice(1).forEach(f => form.append('files', f));
      form.append('note', description.slice(0, 2000));
      documentFiles.forEach(f => form.append('attachments', f));
      const created = await api('/api/projects', { method: 'POST', body: form });
      // 图纸已经安全落盘了。写报价来源失败不该把人困在这一页：那只是一条溯源信息，
      // 1.1 照样能继续填。把原因说出来，然后照常进 1.1。
      try {
        await linkProject(created.project_id, description, file.name);
      } catch (linkError) {
        toast(`项目已创建，但报价来源没写进需求单：${linkError.message}`, true);
        await new Promise(resolve => setTimeout(resolve, 1200));
      }
      setProject(created.project_id);
      location.href = `requirement-create.html?project=${encodeURIComponent(created.project_id)}`;
    } catch (error) {
      toast(error.message || '创建需求失败', true);
      button.disabled = false;
      button.textContent = '创建需求并开始';
    }
  }

  /** 需求单里写清"这条技术工艺是哪张报价任务带来的"，1.1 之后每一步都能追回去。 */
  async function linkProject(projectId, description, filename) {
    const payload = task.payload || {};
    const title = (task.title || filename.replace(/\.[^.]+$/, '') || '新增工艺需求').slice(0, 80);
    const doc = await api(`/api/projects/${encodeURIComponent(projectId)}/requirement`)
      .then(d => d.requirement).catch(() => null);
    const data = Object.assign({}, (doc && doc.data) || {}, {
      title,
      description: description || payload.requirement_text || '',
      requirement_type: '工艺评估',
      source: 'CPQ 报价 · 新增工艺',
      source_task_id: String(task.task_id || ''),
      source_task_no: task.task_no || '',
      source_session_id: (payload.quote || {}).session_id || task.session_id || '',
      customer_name: task.customer || (doc && doc.data && doc.data.customer_name) || '',
    });
    await api(`/api/projects/${encodeURIComponent(projectId)}/requirement`, {
      method: 'PUT',
      body: JSON.stringify(Object.assign({}, doc || {}, {
        project_id: projectId, data, title,
        // requirement_no 在 RequirementDoc 里是**必填无默认**的。新建的项目还没有需求单
        // （上面那个 GET 返回 null），不显式给空串，整个 PUT 会被 422 挡下来 ——
        // 界面上就是"点了创建需求并开始之后报错、进不了 1.1"。真正的编号由后端生成。
        requirement_no: (doc && doc.requirement_no) || '',
        status: (doc && doc.status) || 'draft',
      })),
    });
  }

  function renderError(message) {
    host().innerHTML = '<h1 class="tt-title">新增工艺任务</h1>'
      + '<div class="tt-err">任务 ' + esc(taskId || '(缺少任务编号)') + ' 读取失败：'
      + esc(message) + '</div>'
      + '<div class="tt-actions" style="margin-top:16px">'
      + '<a class="tt-ghost" href="home.html">返回首页</a></div>';
  }

  async function start() {
    host().innerHTML = '<p class="tt-loading">正在读取任务…</p>';
    if (!taskId) { renderError('地址里没有带任务编号（tech_task）。'); return; }
    try {
      task = await fetchTask();
    } catch (error) {
      renderError(error.message || '未知错误');
      return;
    }
    if (!task) { renderError('没有找到这条任务。'); return; }
    if (task.task_kind !== 'tech_new_product') {
      renderError('该任务不是「新增工艺」任务，请回报价工作台处理。');
      return;
    }
    render();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
