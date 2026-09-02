/* 【CPQ 定制 · 本文件不来自 process_drawing，同步上游时保留】
 *
 * 旧入口兼容：home.html?tech_task=<task_id> → tech-task.html?tech_task=<task_id>。
 *
 * 「新增工艺」任务原来是以横幅的形式插在技术工艺首页上的，任务详情和"我的清单"挤在
 * 一屏，建单还得借用首页那个 200 字的通用输入框。现在它有了专属页（tech-task.js），
 * 首页只负责把任务列出来、点开跳过去。
 *
 * 这个文件之所以留着而不是直接删掉：报价那边发出去的消息、浏览器历史、以及工艺经理
 * 自己存的书签里，都可能还带着旧地址。用 replace 而不是 href —— 中转页不该占一格
 * 后退历史，否则从专属页点"返回"会弹回首页再被弹走，来回打转。
 */
(function () {
  'use strict';

  const taskId = new URLSearchParams(location.search).get('tech_task') || '';
  if (!taskId) return;
  location.replace('/tech-task.html?tech_task=' + encodeURIComponent(taskId));
})();
