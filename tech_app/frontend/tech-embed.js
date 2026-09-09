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
    // 代理（同源点击）或通过 .show-child-chat 临时展开使用。
    var style = document.createElement('style');
    style.textContent = [
      '.tech-embed .global-home-link, .tech-embed .global-back-link { display:none !important; }',
      '.tech-embed .workflow-section { display:none !important; }',
      '.tech-embed .oc-rail, .tech-embed .oc-dock, .tech-embed .oc-drawer,',
      '.tech-embed .oc-drawer-backdrop { display:none !important; }',
      '.tech-embed .footer-bar, .tech-embed .proc-actions { display:none !important; }',
      '.tech-embed:not(.show-child-chat) .oc-agent-pane { display:none !important; }',
      '.tech-embed .oc-shell .page-container { max-width:none !important; padding-left:18px !important; padding-right:18px !important; }',
      '.tech-embed .oc-work { min-height:0 !important; }',
      '.tech-embed body { padding-bottom:18px !important; }',
      '.tech-embed .oc-work .center-panel { max-height:none !important; }',
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
