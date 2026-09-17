/* 技术工艺首页唯一口径模块（批次 10A）。
 *
 * 首页只做两件事：**入口定义**与**卡片渲染口径**。项目卡上的每一个字段（谁的项目、
 * 现在第几步、在等谁、最后一次业务动作、有没有异常、主操作）都由后端算好放在
 * `row.card` 里，本模块只原样透出 —— 不再维护任何 `status → 中文` 的本地映射表。
 *
 * 「谁的项目」永远问后端（entries 里只有「最近访问」是本机口径）；本机只记住
 * 「先给你看哪几条」，即 localStorage 里的最近访问 id 列表。
 */
(function (root) {
  'use strict';

  var ENTRIES = [
    { id: 'mine', label: '我的项目', scope: 'mine', source: 'server' },
    { id: 'all', label: '全部项目', scope: 'all', source: 'server' },
    { id: 'todo', label: '待办任务', scope: 'todo', source: 'server' },
    { id: 'recent', label: '最近访问', scope: '', source: 'local' },
    { id: 'archived', label: '已归档', scope: 'archived', source: 'server' }
  ];
  var RECENT_KEY = 'tech:recentProjects';
  var MAX_RECENT = 20;
  var STAGE_EMPTY = '—';

  function entries() {
    return ENTRIES.map(function (entry) {
      return { id: entry.id, label: entry.label, scope: entry.scope, source: entry.source };
    });
  }

  // 原样返回 row.card；没有就 null，绝不现造。
  function cardOf(row) {
    if (!row || typeof row !== 'object') return null;
    var card = row.card;
    return (card && typeof card === 'object') ? card : null;
  }

  function stageText(card) {
    var stage = card && card.stage;
    if (!stage || !String(stage.code || '').trim()) return STAGE_EMPTY;
    var title = String(stage.title || '').trim();
    return String(stage.code).trim() + (title ? ' ' + title : '');
  }

  function waitingText(card) {
    var waiting = card && card.waiting_for;
    if (!waiting) return STAGE_EMPTY;
    var kind = String(waiting.kind || '').trim();
    if (kind === 'none') return '';
    if (kind === 'role' || kind === 'user') return String(waiting.label || '');
    return STAGE_EMPTY;
  }

  function readRecent() {
    try {
      var raw = root.localStorage ? root.localStorage.getItem(RECENT_KEY) : null;
      var rows = raw ? JSON.parse(raw) : [];
      return Array.isArray(rows) ? rows.map(String) : [];
    } catch (error) {
      return [];
    }
  }

  function localRecent() {
    return readRecent();
  }

  function rememberRecent(projectId) {
    var id = String(projectId == null ? '' : projectId).trim();
    if (!id) return readRecent();
    var rows = readRecent().filter(function (value) { return value !== id; });
    rows.unshift(id);
    if (rows.length > MAX_RECENT) rows = rows.slice(0, MAX_RECENT);
    try {
      if (root.localStorage) root.localStorage.setItem(RECENT_KEY, JSON.stringify(rows));
    } catch (error) { /* 隐私模式等写不进去时只影响本机排序 */ }
    return rows;
  }

  var api = {
    ENTRIES: ENTRIES,
    RECENT_KEY: RECENT_KEY,
    entries: entries,
    cardOf: cardOf,
    stageText: stageText,
    waitingText: waitingText,
    rememberRecent: rememberRecent,
    localRecent: localRecent
  };

  root.TechHomeBoard = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
