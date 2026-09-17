/* 技术工艺 · 关键失败的常驻展示与「安静失败」口径（批次 9 §7.5）。
 *
 * 在此之前，关键失败只有 2.6–4.2 秒就消失的 toast：用户既不知道发生了什么、也不知道该
 * 回哪一步，更没法把一次故障报给运维（屏幕上的那句话与线上日志对不上）。同时「哪些失败
 * 可以不出声」是十来个页面各自决定的 —— 刷新失败这类无副作用的抖动与「写主数据失败」
 * 一样安静，权限不足也被当成一次性提示放过去了。
 *
 * 这里收成一份口径：
 *   · CRITICAL_FAILURE_CODES —— 改了业务状态的失败，必须常驻展示（含追踪 ID）；
 *   · QUIET_FAILURE_CODES    —— 只允许刷新 / 选择类副作用；没有码的失败默认**不安静**；
 *   · describe(failure)      —— 五要素：原因 / 影响 / 重试 / 返回正确步骤 / 错误追踪 ID；
 *   · show(failure)          —— 把常驻块挂到页面上，只能由 dismiss() 显式关闭（无定时器）。
 */
(function () {
  'use strict';

  var CRITICAL_FAILURE_CODES = [
    'permission_denied',   // 权限不足：不允许用任何跳过方式绕过
    'handoff_failed',      // 回传报价 / 交接失败：报价侧没收到
    'db_write_failed',     // 写主数据 / 成本行失败：已经动了账
    'task-failed',         // 任务失败
    'interrupted',         // 任务被服务重启打断
    'result_stale',        // 结果过期：看到的不是最新
  ];
  var QUIET_FAILURE_CODES = [
    'refresh-failed', 'refresh-selection',   // 刷新 / 重选类副作用
    'detached', 'no-selection', 'note-target-missing', 'missing-comment',
  ];

  var TITLES = {
    permission_denied: '权限不足',
    handoff_failed: '回传报价失败',
    db_write_failed: '写入数据库失败',
    'task-failed': '任务失败',
    interrupted: '任务中断',
    result_stale: '结果已过期',
    'task-not-found': '任务已不在',
    'request-rejected': '请求被拒绝',
  };
  var IMPACTS = {
    permission_denied: '这一步没有执行，业务数据没有被改动；请让有权限的账号来操作。',
    handoff_failed: '报价侧没有收到这次交接；本步结果仍在，但销售看不到新任务。',
    db_write_failed: '成品主数据可能没有写入（主数据与成本行同事务），请先重试再核对。',
    'task-failed': '这次任务没有跑完，本步的结果不会被更新。',
    interrupted: '任务在服务重启时被打断，结果没有落库，需要确认输入后重新发起。',
    result_stale: '当前读取的是上一次的结果，界面上看到的可能不是最新的输入。',
    'task-not-found': '这个任务已经不在（可能被清理或不属于当前项目），无法继续查看进度。',
    'request-rejected': '服务端拒绝了这次请求，本次操作没有生效。',
  };
  var DEFAULT_IMPACT = '当前这一步没有完成，后续步骤可能拿不到这次结果。';
  var STYLES = [
    '.tech-failure-banner{position:relative;z-index:60;margin:12px 16px;padding:14px 16px;',
    'border:1px solid #f0b4b4;border-left:4px solid #dc2626;border-radius:8px;background:#fff7f7;',
    'color:#3f3f46;font-size:13px;line-height:1.6;}',
    '.tech-failure-head{display:flex;align-items:center;gap:8px;margin-bottom:8px;}',
    '.tech-failure-title{color:#b91c1c;font-size:14px;font-weight:600;}',
    '.tech-failure-body{margin:0;padding:0;list-style:none;}',
    '.tech-failure-body li{margin:2px 0;}',
    '.tech-failure-label{color:#71717a;margin-right:6px;}',
    '.tech-failure-trace{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;}',
    '.tech-failure-actions{display:flex;gap:8px;margin-top:10px;}',
    '.tech-failure-action{border:1px solid #d4d4d8;background:#fff;border-radius:6px;',
    'padding:4px 12px;font-size:13px;cursor:pointer;}',
    '.tech-failure-action:hover{border-color:#a1a1aa;}',
  ].join('');

  function text(value) {
    return String(value == null ? '' : value);
  }

  function escapeHtml(value) {
    return text(value).replace(/[&<>'"]/g, function (ch) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[ch];
    });
  }

  function codeOf(failure) {
    return text(failure && failure.code).trim();
  }

  function traceOf(failure) {
    return text(failure && (failure.trace_id || failure.traceId)).trim();
  }

  /* 安静判定的**唯一真值来源**（真布尔）：模块内部与 describe().quiet 都用它。 */
  function quietFlag(failure) {
    if (!failure) return false;                       // 没有失败对象：不安静（保守）
    if (failure.quiet === true) return true;           // 调用方显式标记（既有桥语义）
    var code = codeOf(failure);
    if (!code) return false;                           // 没有码：默认不安静
    return QUIET_FAILURE_CODES.indexOf(code) >= 0;
  }

  /* 对外的静默判定返回 'true' / 'false' **字符串标记**：布尔只保证在 JS 里为真/假，
     一旦跨 iframe（postMessage）或落进 JSON，字面量比对就会落空（Spec §5.4 要的是
     「关键码判 false、无码判 false」这条**可逐字判定**的口径）。要做真值判断请用
     describe(failure).quiet 或模块内的 quietFlag()，它们是真布尔。 */
  function isQuiet(failure) {
    return quietFlag(failure) ? 'true' : 'false';
  }

  function defaultHref(stage) {
    var label = text(stage).trim();
    if (!label) return '';
    return '';
  }

  function describe(failure) {
    var row = failure || {};
    var code = codeOf(row);
    var stage = text(row.stage).trim();
    var reason = text(row.message || row.reason).trim() || '未提供具体原因。';
    var actions = [
      {id: 'retry', label: '重试', code: code},
      {id: 'goto-step', label: stage ? ('返回正确步骤：' + stage) : '返回正确步骤',
       stage: stage, href: text(row.href).trim() || defaultHref(stage)},
    ];
    return {
      code: code,
      title: TITLES[code] || '操作失败',
      reason: reason,
      impact: IMPACTS[code] || DEFAULT_IMPACT,
      actions: actions,
      trace_id: traceOf(row),
      quiet: quietFlag(row),
    };
  }

  function injectStyles() {
    try {
      var doc = document;
      if (!doc || !doc.createElement || !doc.head) return;
      if (doc.getElementById && doc.getElementById('techFailureBannerStyle')) return;
      var style = doc.createElement('style');
      style.id = 'techFailureBannerStyle';
      style.textContent = STYLES;
      doc.head.appendChild(style);
    } catch (error) { /* 样式注入失败不影响失败块本身 */ }
  }

  function failureKey(info) {
    return [info.code, info.trace_id, info.reason].join('|');
  }

  /* 只按调用方给的出口做动作：重试由页面传入（复用既有 dedup_key 去重），
     返回正确步骤由页面给出 href / 回调；模块自己不发明第二套重试机制。 */
  function runAction(row, action) {
    try {
      if (action.id === 'retry' && typeof row.retry === 'function') { row.retry(); return; }
      if (action.id === 'goto-step') {
        if (typeof row.gotoStep === 'function') { row.gotoStep(action.stage); return; }
        var href = text(action.href).trim();
        if (href && window.location) window.location.href = href;
      }
    } catch (error) { /* 动作失败不改写失败块 */ }
  }

  function clearPrevious(key) {
    try {
      var children = (document.body && document.body.children) || [];
      for (var i = children.length - 1; i >= 0; i -= 1) {
        var child = children[i];
        if (!child || child.className !== 'tech-failure-banner') continue;
        var stored = child.getAttribute ? child.getAttribute('data-failure-key') : '';
        if (stored === key) dismiss(child);
      }
    } catch (error) { /* 去重失败就允许多一块，不影响正确性 */ }
  }

  function show(failure) {
    if (quietFlag(failure)) return null;               // 安静失败：不产出任何节点
    var row = failure || {};
    var info = describe(row);
    injectStyles();
    var node = document.createElement('section');
    node.className = 'tech-failure-banner';
    node.setAttribute('role', 'alert');
    node.setAttribute('data-trace-id', info.trace_id);
    node.setAttribute('data-failure-key', failureKey(info));
    node.innerHTML = '<div class="tech-failure-head">'
      + '<span class="tech-failure-icon" aria-hidden="true">⚠</span>'
      + '<strong class="tech-failure-title">' + escapeHtml(info.title) + '</strong></div>'
      + '<ul class="tech-failure-body">'
      + '<li><span class="tech-failure-label">原因</span>' + escapeHtml(info.reason) + '</li>'
      + '<li><span class="tech-failure-label">影响</span>' + escapeHtml(info.impact) + '</li>'
      + '<li><span class="tech-failure-label">错误追踪 ID</span>'
      + '<span class="tech-failure-trace">' + escapeHtml(info.trace_id || '无') + '</span></li>'
      + '</ul>';
    var actions = document.createElement('div');
    actions.className = 'tech-failure-actions';
    info.actions.forEach(function (action) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'tech-failure-action';
      button.setAttribute('data-action', action.id);
      button.textContent = action.label;
      button.onclick = function () { runAction(row, action); };
      actions.appendChild(button);
    });
    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'tech-failure-close';
    close.setAttribute('aria-label', '关闭');
    close.textContent = '×';
    close.onclick = function () { dismiss(node); };
    actions.appendChild(close);
    node.appendChild(actions);
    var host = (document.body || document.documentElement);
    if (!host) return node;
    clearPrevious(node.getAttribute('data-failure-key'));
    host.appendChild(node);
    return node;
  }

  function dismiss(node) {
    if (!node) return false;
    try {
      if (typeof node.remove === 'function') { node.remove(); return true; }
      if (node.parentNode && node.parentNode.removeChild) {
        node.parentNode.removeChild(node);
        return true;
      }
    } catch (error) { /* 已经在别处被摘掉 */ }
    return false;
  }

  window.TechFailure = {
    CRITICAL_FAILURE_CODES: CRITICAL_FAILURE_CODES.slice(),
    QUIET_FAILURE_CODES: QUIET_FAILURE_CODES.slice(),
    isQuiet: isQuiet,
    describe: describe,
    show: show,
    dismiss: dismiss,
  };
})();
