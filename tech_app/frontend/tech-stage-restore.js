/* 项目阶段恢复的唯一判定实现（纯函数，可被单测直接执行）。
 *
 * 「历史记录」与「首页项目卡片 / 清单」打开项目时，都用这里把四路只读信号换算成
 * 9 个 stage id 之一：需求 / 报告（workflow）、是否已解析（project）、3 组装与整合的计划
 * （summary.steps.integration）与 4 成本测算（summary.steps.cost_review /
 * cost-review）。调用点只负责取数并委托，不再各留一份判定分支。
 *
 * 只读入参，不碰任何页面环境：接口地址、请求头、兜底文案都归调用点。
 */
(function (root) {
  'use strict';

  function filled(value) {
    return value != null && String(value).trim() !== '';
  }

  // 「图纸解析完成」的唯一判定，供工作台进度点与阶段恢复共用。
  // 解析成功才会写 IR 文档 / `stages.parsed`(_3d) / `ir_revision`；零件数量是解析的
  // **结果**，不能当完成标志 —— 解析成功但确实是 0 个零件的整机同样算完成。
  function drawingParsed(input) {
    var data = input || {};
    var meta = data.meta || {};
    var stages = data.stages || {};
    var metaStages = meta.stages || {};
    var ir = data.ir;
    if (ir && typeof ir === 'object' && !Array.isArray(ir) && Object.keys(ir).length) return true;
    if (filled(stages.parsed) || filled(stages.parsed_3d)) return true;
    if (filled(metaStages.parsed) || filled(metaStages.parsed_3d)) return true;
    if (Number(meta.ir_revision) >= 1) return true;
    if (Number(meta.ir_input_revision) >= 1) return true;
    return false;
  }

  // project（GET /api/projects/{id}）里的「有 IR」信号：一律交给 drawingParsed。
  function hasIrSignal(projectData) {
    var project = projectData || {};
    if (project.has_ir) return true;
    return drawingParsed(project);
  }

  function hasCostPart(costDetail) {
    var rows = (costDetail && costDetail.parts) || [];
    for (var i = 0; i < rows.length; i += 1) {
      if (rows[i] && rows[i].has_cost) return true;
    }
    return false;
  }

  // 「4 成本测算已开始」判定。注意：integration.cost.items 是 3 组装与整合自己算的整机成本，
  // 不算 4 成本测算已开始 —— 否则工艺刚确认、成本还没交给财务的项目会被误判进 4 成本测算。
  function costStarted(integration, costReview, costDetail) {
    var plan = integration || {};
    var review = costReview || {};
    var detail = costDetail || {};
    if (review.confirmed) return true;
    if (detail.ready) return true;
    if (hasCostPart(detail)) return true;
    var handoff = plan.finance_handoff;
    if (handoff) {
      if (typeof handoff === 'string') {
        if (handoff.trim()) return true;
      } else if (Object.keys(handoff).length) {
        return true;
      }
    }
    var markers = ['confirmed_by', 'confirmed_at', 'received_at', 'note', 'actions'];
    for (var i = 0; i < markers.length; i += 1) {
      var value = review[markers[i]];
      var filled = Array.isArray(value) ? value.length : (value != null && String(value).trim());
      if (filled) return true;
    }
    return false;
  }

  function fromSignals(flow, projectData, signals) {
    var wf = flow || {};
    var project = projectData || {};
    var data = signals || {};
    var requirement = wf.requirement || {};
    var status = String(requirement.status == null ? '' : requirement.status).trim();

    // 1-3 需求单状态优先：草稿 / 已退回、待确认、待审核各有自己的页面。
    if (status === 'draft' || status === 'rejected') return 'requirement-create';
    if (status === 'pending_confirmation') return 'requirement-confirm';
    if (status === 'pending_review') return 'requirement-review';

    // 4 已有报告时以报告状态为准（草稿回到汇总页继续写）。
    var report = wf.report || null;
    if (report) {
      if (report.status === 'in_review') return 'report-review';
      if (report.status === 'approved' || report.status === 'published') return 'report-publish';
      return 'summary';
    }

    var parsed = hasIrSignal(project);

    // 5 既没有需求单也没有解析结果：只能从 1.1 重新开始。
    if (!status && !parsed) return 'requirement-create';

    // 6 财务把成本退回工艺经理复核：落到第 5 阶段（与报价侧同义任务的既有落点一致）。
    var actions = (data.cost_review || {}).actions;
    if (Array.isArray(actions)) {
      for (var i = 0; i < actions.length; i += 1) {
        if (actions[i] && actions[i].kind === 'return-to-process') return 'summary';
      }
    }

    // 7 4 成本测算已经开始（已发财务 / 已有逐件成本 / 已确认）：不能被退回 3 组装与整合。
    if (costStarted(data.integration, data.cost_review, data.cost_detail)) return 'cost';

    // 8 已解析但还没进 4 成本测算：3 组装与整合。
    if (parsed) return 'process';

    // 9 有需求单、还没解析：2.1 图纸解析。
    return 'drawing';
  }

  var api = { fromSignals: fromSignals, drawingParsed: drawingParsed };
  root.TechStageRestore = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
