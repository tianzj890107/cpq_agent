/* 技术工艺首页唯一口径模块（批次 10A）。
 *
 * 首页只做两件事：**入口定义**与**卡片渲染口径**。项目卡上的每一个字段（谁的项目、
 * 现在第几步、在等谁、最后一次业务动作、有没有异常、主操作）都由后端算好放在
 * `row.card` 里，本模块只原样透出 —— 不再维护任何 `status → 中文` 的本地映射表。
 *
 * 首页固定三个页签：「我的项目 / 待办任务 / 全部项目」（## 139 取代批次 10A 的
 * 五入口决定）。「待办任务」的数据源是任务列表（`GET /wf/tasks`），口径只由本模块的
 * `isTodoTask / todoTasks / todoCount` 决定，报价与技术工艺共用同一份 ——
 * 待办 = 别人转交给我 ∪ 定向我的角色 ∪ 公共池 ∪ 我已领取未完成。
 *
 * 「谁的项目」永远问后端；本机 localStorage 只保留「先给你看哪几条」的排序能力
 * （RECENT_KEY / rememberRecent / localRecent），它不再是任何页签的数据源。
 */
(function (root) {
  'use strict';

  var ENTRIES = [
    { id: 'mine', label: '我的项目', scope: 'mine', source: 'server' },
    { id: 'todo', label: '待办任务', scope: '', source: 'tasks' },
    { id: 'all', label: '全部项目', scope: 'all', source: 'server' }
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

  // ===== 待办任务唯一口径（## 139）=====
  // 计入待办：`open` 且不是我发起的，且（公共池 / 定向我的角色 / 定向给我）；
  // 或者 `claimed` 且领取人是我。其余（我发起未领取的、终态、别人领走的、
  // 缺字段的脏数据、项目归属）一律不是待办。
  function uidOf(value) {
    return value == null ? '' : String(value);
  }

  function isTodoTask(task, user) {
    if (!task || typeof task !== 'object') return false;
    var me = uidOf(user && (user.user_id != null ? user.user_id : user.userId)).trim();
    if (!me) return false;
    var status = uidOf(task.status).trim();
    if (status === 'claimed') return uidOf(task.claimed_by_user_id).trim() === me;
    if (status !== 'open') return false;
    if (uidOf(task.from_user_id).trim() === me) return false;
    var target = uidOf(task.target_type).trim();
    if (target === 'public') return true;
    if (target === 'role') {
      var myRole = uidOf(user && (user.role_code || user.roleCode)).trim();
      return !!myRole && uidOf(task.target_role_code).trim() === myRole;
    }
    if (target === 'user') return uidOf(task.target_user_id).trim() === me;
    return false;
  }

  function todoTasks(tasks, user) {
    var rows = Array.isArray(tasks) ? tasks : [];
    return rows.filter(function (task) { return isTodoTask(task, user); });
  }

  function todoCount(tasks, user) {
    return todoTasks(tasks, user).length;
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
    isTodoTask: isTodoTask,
    todoTasks: todoTasks,
    todoCount: todoCount,
    rememberRecent: rememberRecent,
    localRecent: localRecent
  };

  root.TechHomeBoard = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
