/* 技术工艺统一工作台 —— 旧步骤页嵌入协议（每个旧页面在 <head> 最先加载）。
 *
 * 被 tech-workbench.html 以 embed=1 打开时：
 *   1) 不执行整页重定向；
 *   2) 在 <html> 上加 .tech-embed 并按页隐藏重复的平台导航、会话栏与固定页脚；
 *   3) 暴露 window.TechEmbed，把“切步骤/退出”转发给父工作台（同源 postMessage）。
 * 旧 URL（无 embed=1）直达步骤页时：重定向到统一工作台对应 stage，保留 project、
 *   task_id 及其它参数；project 缺失时不创建匿名项目（工作台侧会给出明确错误态）。
 * 若页面本身嵌在 iframe 且无 embed（异常嵌套），放弃重定向，避免套娃循环。
 */
(function () {
  'use strict';

  var qs = new URLSearchParams(location.search);
  var EMBEDDED = qs.has('embed');
  var IN_IFRAME = window.self !== window.top;
  var FILE = String(location.pathname.split('/').pop() || '').toLowerCase();

  // 旧页面 → 工作台 stage（九个固定 stage 的唯一映射表）。
  var STAGE_OF_FILE = {
    // 「新增工艺」任务专属页不是九个 stage 之一：直达时汇聚到 1.1 创建，
    // 由统一壳在无 project 时以 embed=1 反向承载它完成建项。
    'tech-task.html': 'requirement-create',
    'requirement-create.html': 'requirement-create',
    'requirement-confirm.html': 'requirement-confirm',
    'requirement-review.html': 'requirement-review',
    'index.html': 'drawing',
    'process.html': 'process',
    'assembly-integration.html': 'process',
    'cost.html': 'cost',
    'cost-review.html': 'cost',
    'summary.html': 'summary',
    'report-review.html': 'report-review',
    'report-publish.html': 'report-publish',
  };
  var FILE_OF_STAGE = {
    'requirement-create': 'requirement-create.html',
    'requirement-confirm': 'requirement-confirm.html',
    'requirement-review': 'requirement-review.html',
    'drawing': 'index.html',
    'process': 'assembly-integration.html',
    'cost': 'cost-review.html',
    'summary': 'summary.html',
    'report-review': 'report-review.html',
    'report-publish': 'report-publish.html',
  };
  var CURRENT_STAGE = STAGE_OF_FILE[FILE] || null;

  function projectId() {
    return qs.get('project') || localStorage.getItem('cad_engine_project_id') || '';
  }

  function basePath() {
    var slash = location.pathname.lastIndexOf('/');
    return location.pathname.slice(0, slash + 1);
  }

  function urlOf(targetFile, project, taskId, extra) {
    var q = new URLSearchParams();
    if (project) q.set('project', project);
    if (taskId) q.set('task_id', taskId);
    (extra || []).forEach(function (pair) { q.set(pair[0], pair[1]); });
    return basePath() + targetFile + (q.toString() ? '?' + q.toString() : '');
  }

  function extraPairs() {
    var out = [];
    qs.forEach(function (value, key) {
      if (['project', 'task_id', 'embed'].indexOf(key) !== -1) return;
      out.push([key, value]);
    });
    return out;
  }

  function postToParent(message) {
    if (!EMBEDDED || window.parent === window) return false;
    window.parent.postMessage(message, location.origin);
    return true;
  }

  function requestNavigate(stageId, project, taskId) {
    if (!stageId || !FILE_OF_STAGE[stageId]) return false;
    project = project || projectId();
    taskId = taskId || qs.get('task_id') || '';
    if (EMBEDDED) {
      return postToParent({
        type: 'cpq:tech-workbench:navigate',
        stage: stageId,
        project: project,
        task_id: taskId,
      });
    }
    window.location.href = urlOf(FILE_OF_STAGE[stageId], project, taskId, extraPairs());
    return true;
  }

  function navigateFile(file, project) {
    var stageId = STAGE_OF_FILE[String(file || '').toLowerCase()] || CURRENT_STAGE;
    if (stageId) return requestNavigate(stageId, project);
    return false;
  }

  function exitToTechHome() {
    if (EMBEDDED) {
      postToParent({ type: 'cpq:tech-workbench:exit', url: 'home.html' });
      return true;
    }
    window.location.href = basePath() + 'home.html';
    return true;
  }

  /* ------------------------------------------------------------ 旧 URL 兼容跳转 */
  if (!EMBEDDED && CURRENT_STAGE && !IN_IFRAME) {
    var q = new URLSearchParams();
    qs.forEach(function (value, key) { if (key !== 'embed') q.set(key, value); });
    q.set('stage', CURRENT_STAGE);
    window.location.replace(basePath() + 'tech-workbench.html?' + q.toString());
    return;
  }

  /* ------------------------------------------------------------ 嵌入模式 */
  window.TechEmbed = {
    get embedded() { return EMBEDDED; },
    get stage() { return CURRENT_STAGE; },
    requestNavigate: requestNavigate,
    navigateToFile: navigateFile,
    exitToTechHome: exitToTechHome,
  };

  if (EMBEDDED) {
    document.documentElement.classList.add('tech-embed');
    // 折叠整页壳：顶部三阶段步骤条由父工作台顶栏承担；平台返回浮钮、图标导航、
    // 悬浮文件窗、抽屉、固定页脚都属于重复 chrome。子页面自身业务操作由父壳底栏
    // 代理（同源点击），阶段上下文由父壳唯一的 #techChatPane 承载。
    var style = document.createElement('style');
    style.textContent = [
      '.tech-embed .global-home-link, .tech-embed .global-back-link { display:none !important; }',
      '.tech-embed .workflow-section { display:none !important; }',
      '.tech-embed .oc-rail, .tech-embed .oc-dock, .tech-embed .oc-drawer,',
      '.tech-embed .oc-drawer-backdrop { display:none !important; }',
      '.tech-embed .footer-bar, .tech-embed .proc-actions { display:none !important; }',
      // 唯一会话栏在父页 #techChatPane：嵌入页的子会话/说明栏永远隐藏，且不再有
      // 任何展开入口（原“子页面板”开关已从工作台底栏删除）。
      '.tech-embed .oc-agent-pane { display:none !important; }',
      // 右侧业务看板满宽：清掉旧页面的 1200/1560px 宽度上限、左右各 236/196/78px
      // 的双栏留白与自动居中，隐藏子会话栏后 .oc-work 退化为真正的单列。
      '.tech-embed, .tech-embed body { width:100% !important; max-width:none !important; min-width:0 !important; margin:0 !important; }',
      // 嵌入态阶段页去掉 workbench.css 的页面灰底（独立打开阶段页没有 .tech-embed，
      // 灰底与 .page-container 的 20px 内边距照旧）。
      '.tech-embed body { background:#fff !important; }',
      '.tech-embed .oc-shell { width:100% !important; max-width:none !important; min-width:0 !important; margin:0 !important; }',
      '.tech-embed .oc-work { display:block !important; width:100% !important; min-width:0 !important; gap:0 !important; }',
      // 四条内边距用 longhand 写全，避免与 workbench.css 的 padding 简写互相覆盖；
      // 四周间距是独立打开时（上下 20px / 左右 18px）的一半。
      '.tech-embed .oc-shell .page-container { width:100% !important; max-width:none !important; min-width:0 !important; margin-left:0 !important; margin-right:0 !important; padding-top:10px !important; padding-bottom:10px !important; padding-left:9px !important; padding-right:9px !important; }',
      '.tech-embed .oc-work .center-panel { width:100% !important; min-width:0 !important; max-width:none !important; flex:1 1 100% !important; max-height:none !important; }',
      // 子页面不再显示第二份大标题、第二份阶段页签与整块标题区（统一标题行由父壳
      // #techContextHeader 承载）。嵌入态整块 .title-section 退出布局，避免残留空白行；
      // 只作用于 .tech-embed，独立打开阶段页（没有 .tech-embed）的标题与状态照旧。
      '.tech-embed .title-section { display: none !important; }',
      '.tech-embed .ai-tabs { display:none !important; }',
      // 步骤状态那一行改为并入父壳标题行（board-status → #techContextNotice）：嵌入态下
      // 不再让它在页内单独占一行。独立打开阶段页（没有 .tech-embed）时状态行照旧显示。
      '.tech-embed .title-row .status-badge { display:none !important; }',
      '.tech-embed #status { display:none !important; }',
      '.tech-embed .ai-status { display:none !important; }',
      // 业务按钮唯一入口在左侧 #techChatActions（由看板动作快照动态渲染）：嵌入态下
      // 第二份页内按钮行只隐藏、不删节点、不动绑定，独立打开阶段页照旧；每一条都限定
      // 在 .tech-embed 作用域。卡片里的产品名 / 数量输入与说明文字一律保持可见。
      '.tech-embed #btnParse { display:none !important; }',
      '.tech-embed #aiStart { display:none !important; }',
      '.tech-embed #aiActions { display:none !important; }',
      '.tech-embed #aiOpsCard .ai-ops { display:none !important; }',
      '.tech-embed #crRunAll { display:none !important; }',
      '.tech-embed #crOpsCard .ai-ops { display:none !important; }',
      '.tech-embed #btnAiExtract { display:none !important; }',
      '.tech-embed body { padding-bottom:9px !important; }',
    ].join('\n');
    document.head.appendChild(style);

    // 捕获锚点点击：同源旧步骤页链接 → 转交父壳切换 stage；下载/API/外链放行。
    document.addEventListener('click', function (event) {
      var anchor = event.target.closest ? event.target.closest('a[href]') : null;
      if (!anchor || anchor.target === '_blank') return;
      var href = anchor.getAttribute('href') || '';
      if (href.indexOf('#') === 0) return;
      var url;
      try { url = new URL(href, location.href); } catch (e) { return; }
      if (url.origin !== location.origin) return;
      var file = String(url.pathname.split('/').pop() || '').toLowerCase();
      if (!STAGE_OF_FILE[file]) return;
      event.preventDefault();
      event.stopPropagation();
      requestNavigate(STAGE_OF_FILE[file], url.searchParams.get('project') || projectId());
    }, true);
  }
})();
