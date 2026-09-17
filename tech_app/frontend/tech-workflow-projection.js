/* 技术工艺统一流程投影（批次 5B）—— 前端唯一的投影消费者。
 *
 * 后端 GET /api/projects/<id>/workflow/projection 给出 5 阶段 × 13 子步骤的
 * 状态、完成、能否执行、缺什么、谁来做（见 docs/specs/tech-unified-workflow-projection.md）。
 * 本模块只做一件事：把这唯一投影换算成前端渲染要用的索引，绝不再自己拼完成态。
 *
 * 返回 { done, status, blocked, actionable, stale }：
 *   · 全部按「子步骤号」（1.1…5.3）与「stage id」双索引 —— 老代码按 stage id 打点，
 *     新口径按子步骤号，两种 key 都能查到同一份结论。
 *   · done 是 Set，status / blocked / actionable 是 Map。
 * 纯函数、无 DOM / 无存储 / 无请求依赖，可在 node 里直接跑。
 */
(function (global) {
  'use strict';

  function keysOf(row) {
    const key = String((row && (row.key || row.sub)) || '');
    const stageId = String((row && row.stage_id) || '');
    const out = [];
    if (key) out.push(key);
    if (stageId && stageId !== key) out.push(stageId);
    return out;
  }

  function progress(projection) {
    const done = new Set();
    const status = new Map();
    const blocked = new Map();
    const actionable = new Map();
    const stale = new Set();
    const rows = (projection && projection.stages) || [];
    rows.forEach((row) => {
      if (!row) return;
      keysOf(row).forEach((id) => {
        if (row.completed) done.add(id);
        if (row.status) status.set(id, row.status);
        const reasons = row.blocked_reasons;
        if (reasons && reasons.length) blocked.set(id, reasons.slice());
        actionable.set(id, Boolean(row.actionable));
        if (row.stale) stale.add(id);
      });
    });
    return { done: done, status: status, blocked: blocked, actionable: actionable, stale: stale };
  }

  const api = { progress: progress };
  global.TechWorkflowProjection = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
