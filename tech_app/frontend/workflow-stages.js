/* 技术工艺「5 阶段 × 13 子步骤」流程栏的**唯一前端口径**
   （Spec `packaging-stage-order-equals-dependency.md` §2.3）。

   与后端 `tech_app/backend/services/workflow_stages.py` 同口径（行序 = 依赖顺序：
   图纸解析夹在「创建需求」与「确认需求」之间）。其它页面 —— `workflow.js` /
   `requirement-create.js` / `requirement-confirm-page.js` / `report-publish-result.js` ——
   一律**不许**再抄第二份编号表：要显示子步骤标签就调 `CpqWorkflowStages.subLabel()`，
   要遍历阶段就调 `.phases` / `.phaseRows`。 */
(function () {
  'use strict';
  // [[阶段号, 阶段标题, [[子步骤号, 子步骤标题], ...]], ...]
  var PHASE_ROWS = [
    [1, '工艺评估需求', [['1.1', '创建'], ['1.2', '确认'], ['1.3', '审核']]],
    [2, '图纸解析', [['2.1', '图纸解析']]],
    [3, '组装与整合', [['3.1', '整合图纸'], ['3.2', '参数推荐'], ['3.3', '组装工艺']]],
    [4, '成本测算', [['4.1', '零件成本'], ['4.2', '组装成本'], ['4.3', '汇总']]],
    [5, '工艺评估报告', [['5.1', '汇总结果'], ['5.2', '结果审核'], ['5.3', '发布并回传报价']]],
  ];

  function subLabel(no) {
    var wanted = String(no || '');
    for (var i = 0; i < PHASE_ROWS.length; i += 1) {
      var subs = PHASE_ROWS[i][2];
      for (var j = 0; j < subs.length; j += 1) {
        if (subs[j][0] === wanted) { return subs[j][0] + ' ' + subs[j][1]; }
      }
    }
    return wanted;
  }

  function find(no) {
    var wanted = String(no || '');
    for (var i = 0; i < PHASE_ROWS.length; i += 1) {
      var subs = PHASE_ROWS[i][2];
      for (var j = 0; j < subs.length; j += 1) {
        if (subs[j][0] === wanted) { return subs[j]; }
      }
    }
    return null;
  }

  function subTitle(no) {
    var pair = find(no);
    return pair ? pair[1] : '';
  }

  function subPair(no) {
    var pair = find(no);
    return pair ? [pair[0], pair[1]] : [String(no || ''), ''];
  }

  window.CpqWorkflowStages = {
    phaseRows: PHASE_ROWS,
    phases: PHASE_ROWS.map(function (row) {
      return { no: row[0], title: row[1], subs: row[2] };
    }),
    subLabel: subLabel,
    subTitle: subTitle,
    subPair: subPair,
  };
})();
