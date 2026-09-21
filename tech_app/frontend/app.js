/* embed=1：本页在技术工艺统一工作台（tech-workbench.html）右侧打开，会话宿主在父壳 techChatPane。 */
const __techEmbedMode__ = new URLSearchParams(location.search).get('embed') === '1';
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

const API = "";  // 同源
// 两个键是历史遗留：auth.js / account.js 登录时两个都写，其余页面读的时候都做
// `authToken || cad_engine_token` 兜底。2.1（app.js + agent-chat.js）原来只读前者，
// 于是会话只剩 cad_engine_token 时，唯独这一步的请求不带 Authorization ——
// /api/projects/{id} 401、openProject 抛错，图纸、任务文件、「开始解析」全停在初始态，
// 看起来就像"前面步骤上传的东西没传过来"。
const readToken = () =>
  localStorage.getItem("authToken") || localStorage.getItem("cad_engine_token") || "";
let authToken = readToken();
let currentUser = null;
let authEnabled = false;

// 给同源 /api 请求自动带上令牌(鉴权开启时)
const _fetch = window.fetch.bind(window);
window.fetch = (url, opts = {}) => {
  const blocked = writeProjectMismatch(url, opts);
  if (blocked) {
    try { status(blocked); } catch (error) { /* status 初始化前不显示 */ }
    return Promise.reject(new Error(blocked));
  }
  const hasAuth = opts.headers && (opts.headers.Authorization || opts.headers.authorization);
  if (typeof url === "string" && url.indexOf("/api/") !== -1 && authToken && !hasAuth) {
    opts = Object.assign({}, opts, {
      headers: Object.assign({}, opts.headers, { Authorization: "Bearer " + authToken }),
    });
  }
  return _fetch(url, opts);
};
// 媒体 URL(<img>/STL 无法带请求头)用 ?token= 透传
const mediaUrl = (u) =>
  authToken ? u + (u.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(authToken) : u;

const processNavIcon = '<svg fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" aria-hidden="true"><path d="M0 0h16v16H0z"/><path fill-rule="evenodd" fill="#8F9299" d="M2.667 3.833H5.5v-.5c0-.253.045-.487.134-.703.09-.216.224-.414.403-.593.179-.179.376-.313.592-.403.217-.09.451-.134.704-.134h1.334c.253 0 .487.045.703.134.216.09.414.224.593.403.179.179.313.377.403.593.09.216.134.45.134.703v.5h2.833c.253 0 .488.045.704.135.216.089.414.223.593.402.179.18.313.377.402.593.09.216.135.45.135.704v1c0 .253-.045.487-.135.703a1.82 1.82 0 0 1-.402.593 1.82 1.82 0 0 1-.797.472v4.232c0 .253-.044.487-.134.703-.09.216-.224.414-.403.593a1.82 1.82 0 0 1-.592.403c-.216.09-.451.134-.704.134H4a1.82 1.82 0 0 1-.704-.134 1.82 1.82 0 0 1-.592-.403 1.821 1.821 0 0 1-.403-.593 1.82 1.82 0 0 1-.134-.703V8.435a1.821 1.821 0 0 1-.796-.472 1.821 1.821 0 0 1-.403-.593 1.82 1.82 0 0 1-.135-.703v-1c0-.253.045-.488.135-.704.089-.216.223-.414.402-.593a1.82 1.82 0 0 1 .593-.402c.216-.09.45-.135.704-.135Zm0 3.667H13.335c.277 0 .485-.07.623-.208.14-.14.209-.348.209-.625v-1c0-.278-.07-.486-.209-.625-.139-.14-.347-.209-.625-.209H10a.481.481 0 0 1-.354-.146.481.481 0 0 1-.146-.354v-1c0-.277-.07-.486-.208-.625-.14-.139-.348-.208-.625-.208H7.333c-.278 0-.486.07-.625.208-.139.14-.208.348-.208.625v1a.481.481 0 0 1-.147.354.481.481 0 0 1-.353.146H2.667c-.278 0-.487.07-.625.209-.14.139-.209.347-.209.625v1c0 .277.07.486.209.625.138.139.347.208.625.208Z" data-follow-fill="#8F9299"/></svg>';
const costNavIcon = '<svg fill="none" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" aria-hidden="true"><path d="M0 0h16v16H0z"/><path fill="#8F9299" d="m2.53 9.074.467.92c-.32.315-.497.658-.497 1.006 0 1.286 2.427 2.5 5.5 2.5s5.5-1.214 5.5-2.5c0-.348-.178-.69-.497-1.005l.476-.913c.645.54 1.021 1.194 1.021 1.918 0 2.027-2.946 3.5-6.5 3.5S1.5 13.027 1.5 11c0-.728.38-1.384 1.03-1.926Z" data-follow-fill="#8F9299"/><path fill="#8F9299" d="m2.53 6.074.467.92C2.677 7.31 2.5 7.653 2.5 8c0 1.286 2.427 2.5 5.5 2.5s5.5-1.214 5.5-2.5c0-.348-.178-.69-.497-1.005l.476-.913C14.124 6.622 14.5 7.276 14.5 8c0 2.027-2.946 3.5-6.5 3.5S1.5 10.027 1.5 8c0-.728.38-1.384 1.03-1.926Z" data-follow-fill="#8F9299"/><path fill="#8F9299" d="M8 8.5C4.446 8.5 1.5 7.027 1.5 5S4.446 1.5 8 1.5s6.5 1.473 6.5 3.5S11.554 8.5 8 8.5Zm0-1c3.073 0 5.5-1.214 5.5-2.5S11.073 2.5 8 2.5 2.5 3.714 2.5 5 4.927 7.5 8 7.5Z" data-follow-fill="#8F9299"/></svg>';
const processWrenchNavIcon = '<svg width="24" height="24" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M30.4417 5C32.406 5 34.265 5.44776 35.9207 6.24607L32.7172 9.42668C30.8706 11.2601 30.8706 14.2327 32.7172 16.0661C34.5638 17.8995 37.5578 17.8995 39.4044 16.0661L42.2571 13.2337C42.7379 14.5558 43 15.9818 43 17.4685C43 24.3547 37.3775 29.937 30.4417 29.937C28.7825 29.937 27.1985 29.6176 25.7486 29.0373L13.07 41.6253C11.2238 43.4582 8.2307 43.4582 6.38459 41.6253C4.53847 39.7924 4.53847 36.8207 6.38459 34.9877L18.9523 22.5099C18.2651 20.9684 17.8834 19.2627 17.8834 17.4685C17.8834 10.5823 23.5059 5 30.4417 5Z" fill="none" stroke="#333" stroke-width="4" stroke-linejoin="round"/></svg>';
window.cadWorkbenchIcons = { process: processWrenchNavIcon, cost: costNavIcon };

let currentProject = null;
let currentIR = null;
let currentGeometry = null;
let currentDrawings = null;
// GET /api/projects/{id} 给的逐件过期状态：改一个零件只标那一个，不再整份清空。
let artifact_status = null;
let currentIsImg = true;       // 是否为"图→IR"项目(3D 导入项目不可改参重生)
let currentDrawingEntry = "vision";  // 入口判定结果 renderDrawingEntry()，决定 2.1 走哪条链路
let currentSelectedId = null;  // 当前选中的零件 id
let viewerBroken = false;      // WebGL 不可用：3D 预览降级，其余流程照常
let diffPick = [];             // 版本对比已选的两个版本号
const chatSessions = new Map(); // 项目级会话仅保存在当前浏览器内，不写入业务数据
let chatBusy = false;

const $ = (id) => document.getElementById(id);

// 看板内部零件层级视图（技术工艺 Agent 能力恢复第 6 步）：
// drawing-overview → parts-list → part-detail → part-process / part-cost。
// 这些视图只存在于右侧 iframe 看板内部；父壳 tech-workbench 里不出现它们的名字。
const PART_VIEW_PARENT = {
  "part-cost": "part-detail",
  "part-process": "part-detail",
  "part-detail": "parts-list",
  "parts-list": "drawing-overview",
};
const PART_VIEW_FOR = { process: "part-process", cost: "part-cost" };
const PART_VIEW_MODE = { "part-process": "process", "part-cost": "cost" };
const PART_FLOW_VIEWS = ["drawing-overview", "parts-list", "part-detail", "part-process", "part-cost"];
const status = (msg, busy = false) => {
  const text = (busy ? "处理中 · " : "") + msg;
  $("status").textContent = text;
  const card = $("statusCard");
  if (card) card.dataset.busy = String(busy);
  // 同一段文字原样上报给父壳，由统一标题行的提示位显示；独立打开（无运行时）时不通信。
  const runtime = window.TechBoardRuntime;
  if (runtime && typeof runtime.publishStatus === "function") runtime.publishStatus(text, "info");
};

// 抽屉里的折叠块（解析视图、待澄清问题、零件参数…）一律默认展开，由 HTML 上的
// open 属性决定。原来这里会在解析完成后反过来把「解析视图」收起 —— 而标注视图恰好
// 是解析完成才有内容的，收起等于刚出结果就藏起来。折叠与否现在只由用户点击决定。

// 输入原图与技术文档的清单已经统一由右侧「任务文件」小窗承载，左侧抽屉只保留
// 解析后的标注视图，因此这里原有的 renderProjectEvidence / renderAttachmentImageGallery
// 一并去掉 —— 同一份清单在两处渲染，改一处就会两边对不上。

function safeExternalUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function renderModelLookup(report) {
  const root = $("modelLookupResult");
  if (!root) return;
  root.replaceChildren();
  if (!report || !report.generated_at) {
    root.textContent = "完成图纸解析后，可在“更多功能”中发起型号联网核验。结果先独立保存，人工确认后才写入零件/BOM。";
    return;
  }
  const intro = document.createElement("div");
  intro.className = "lookup-summary";
  intro.textContent = report.summary || "型号联网核验已完成。";
  root.appendChild(intro);
  const meta = document.createElement("div");
  meta.className = "lookup-meta";
  meta.textContent = `模型：${report.model || "—"} · 搜索 ${report.search_count || 0} 次 · ${report.generated_at}`;
  root.appendChild(meta);
  const confirmations = report.confirmations || {};
  const appliedChanges = report.applied_changes || [];
  (report.identifications || []).forEach(item => {
    const card = document.createElement("div");
    card.className = "lookup-item";
    const title = document.createElement("div");
    title.className = "lookup-title";
    title.textContent = item.candidate_model || "未命名型号";
    const statusTag = document.createElement("span");
    statusTag.className = `lookup-status ${item.status || "ambiguous"}`;
    statusTag.textContent = ({ matched: "已匹配", ambiguous: "待确认", not_found: "未找到", not_a_model: "非型号" })[item.status] || "待确认";
    title.appendChild(statusTag);
    card.appendChild(title);
    const body = document.createElement("div");
    body.className = "lookup-body";
    const name = [item.manufacturer, item.identified_part_name || item.category].filter(Boolean).join(" · ");
    const target = item.related_part_id ? `\n关联零件：${item.related_part_id}` : "\n关联范围：BOM/标准件候选";
    body.textContent = `${name || "未形成可靠零件结论"}${target}${item.specification_summary ? `\n${item.specification_summary}` : ""}${item.evidence_summary ? `\n依据：${item.evidence_summary}` : ""}`;
    card.appendChild(body);
    const confidence = document.createElement("div");
    confidence.className = "lookup-confidence";
    const applied = appliedChanges.find(change => String(change.candidate_model || "").trim().toUpperCase() === String(item.candidate_model || "").trim().toUpperCase());
    confidence.textContent = applied
      ? `联网匹配置信度：${Math.round(Number(item.confidence || 0) * 100)}% · 人工确认后已同步至${applied.target === "part" ? "零件清单" : "BOM"}并创建新版本`
      : `联网匹配置信度：${Math.round(Number(item.confidence || 0) * 100)}% · 等待人工确认，不会自动写入`;
    card.appendChild(confidence);
    const confirmation = confirmations[item.candidate_model] || confirmations[String(item.candidate_model || "").trim()] || null;
    const actions = document.createElement("div");
    actions.className = "lookup-actions";
    if (applied) {
      const confirmed = document.createElement("span");
      confirmed.className = "lookup-confirmed confirmed";
      confirmed.textContent = "已确认并同步（可在版本记录中回溯）";
      actions.appendChild(confirmed);
    } else if (confirmation) {
      const confirmed = document.createElement("span");
      confirmed.className = `lookup-confirmed ${confirmation.decision}`;
      confirmed.textContent = confirmation.decision === "confirmed" ? `已确认：${confirmation.by || ""}` : `已驳回：${confirmation.by || ""}`;
      actions.appendChild(confirmed);
    } else {
      [
        ["confirmed", "标记已复核"],
        ["rejected", "驳回结论"],
      ].forEach(([decision, text]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = decision === "confirmed" ? "lookup-confirm" : "lookup-reject";
        button.textContent = text;
        button.onclick = () => confirmModelLookup(item.candidate_model, decision);
        actions.appendChild(button);
      });
    }
    card.appendChild(actions);
    root.appendChild(card);
  });
  if (report.product_summary || (report.proposed_components || []).length || (report.process_designs || []).length) {
    const research = document.createElement("div");
    research.className = "lookup-product-research";
    const heading = document.createElement("div");
    heading.className = "lookup-title";
    heading.textContent = "产品级结构与工艺推演";
    research.appendChild(heading);
    if (report.product_summary) {
      const summary = document.createElement("div");
      summary.className = "lookup-body";
      summary.textContent = report.product_summary;
      research.appendChild(summary);
    }
    if ((report.proposed_components || []).length) {
      const componentTitle = document.createElement("div");
      componentTitle.className = "lookup-research-heading";
      componentTitle.textContent = "联网推演候选部件（仅供参考，尚未写入 BOM）";
      research.appendChild(componentTitle);
      report.proposed_components.forEach(item => {
        const line = document.createElement("div");
        line.className = "lookup-research-item";
        line.textContent = `${item.name}${item.category ? ` · ${item.category}` : ""}${item.role ? `：${item.role}` : ""}${item.evidence_summary ? `\n依据：${item.evidence_summary}` : ""}`;
        research.appendChild(line);
      });
    }
    if ((report.process_designs || []).length) {
      const processTitle = document.createElement("div");
      processTitle.className = "lookup-research-heading";
      processTitle.textContent = "公开资料中的技术 / 工艺要点（待工程确认）";
      research.appendChild(processTitle);
      report.process_designs.forEach(item => {
        const line = document.createElement("div");
        line.className = "lookup-research-item";
        line.textContent = `${item.name}${item.related_component ? ` · ${item.related_component}` : ""}\n${item.design_summary || ""}${(item.key_controls || []).length ? `\n需确认：${item.key_controls.join("；")}` : ""}`;
        research.appendChild(line);
      });
    }
    root.appendChild(research);
  }
  if ((report.search_sources || []).length) {
    const sources = document.createElement("div");
    sources.className = "lookup-sources";
    const heading = document.createElement("div");
    heading.textContent = "联网搜索来源";
    sources.appendChild(heading);
    report.search_sources.forEach(source => {
      const link = document.createElement("a");
      const href = safeExternalUrl(source.url);
      link.textContent = source.title || source.url;
      if (href) { link.href = href; link.target = "_blank"; link.rel = "noopener"; }
      else link.removeAttribute("href");
      sources.appendChild(link);
    });
    root.appendChild(sources);
  }
  if ((report.open_questions || []).length) {
    const questions = document.createElement("div");
    questions.className = "lookup-questions";
    questions.textContent = `待确认：${report.open_questions.join("；")}`;
    root.appendChild(questions);
  }
}

async function loadModelLookup(projectId) {
  try {
    const response = await fetch(`${API}/api/projects/${projectId}/model-lookup`);
    if (!response.ok) return renderModelLookup(null);
    let report = await response.json();
    if ((report.applied_changes || []).length && currentProject === projectId) {
      const project = await fetch(`${API}/api/projects/${projectId}`).then(r => r.ok ? r.json() : null);
      if (project?.ir) { currentIR = project.ir; renderIR(currentIR); }
    }
    renderModelLookup(report);
  } catch { renderModelLookup(null); }
}

async function confirmModelLookup(candidateModel, decision) {
  if (!currentProject) return;
  const note = prompt(decision === "confirmed" ? "填写确认说明（可选）" : "填写驳回原因（可选）", "") || "";
  try {
    const response = await fetch(`${API}/api/projects/${currentProject}/model-lookup/confirm`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_model: candidateModel, decision, note }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || response.status);
    renderModelLookup(payload);
    if (decision === "confirmed" && (payload.applied_changes || []).length) {
      const project = await fetch(`${API}/api/projects/${currentProject}`).then(r => r.ok ? r.json() : null);
      if (project?.ir) { currentIR = project.ir; renderIR(currentIR); loadVersions(); }
    }
    const applied = (payload.applied_changes || []).some(change =>
      String(change.candidate_model || "").trim().toUpperCase() === String(candidateModel || "").trim().toUpperCase()
    );
    status(decision === "rejected"
      ? `已驳回型号 ${candidateModel} 的联网结论`
      : applied
        ? `已确认型号 ${candidateModel}，可靠结论已同步至零件清单/BOM`
        : `已记录型号 ${candidateModel} 的人工复核；该结论未达到写入条件，零件/BOM 保持不变`);
  } catch (error) { status("记录确认失败: " + error.message); }
}

function renderVerification(report) {
  const root = $("verificationResult");
  if (!root) return;
  root.replaceChildren();
  const pending = report?.pending_changes || [];
  if (!pending.length) {
    root.textContent = report?.applied_changes?.length
      ? `本次校核的 ${report.applied_changes.length} 项强证据修改已应用，没有待确认项。`
      : "当前没有待确认的字段级校核修改。";
    return;
  }
  pending.forEach(change => {
    const card = document.createElement("div"); card.className = "verification-item";
    const path = document.createElement("div"); path.className = "verification-path"; path.textContent = change.field;
    const values = document.createElement("div"); values.className = "verification-change";
    values.textContent = `${change.old_value || "null"} → ${change.new_value || "null"}`;
    const reason = document.createElement("div"); reason.className = "verification-reason";
    reason.textContent = `${change.reason || "AI 校核建议"} · 置信度 ${Math.round(Number(change.confidence || 0) * 100)}%`;
    const actions = document.createElement("div"); actions.className = "verification-actions";
    [["confirmed", "确认修改", "lookup-confirm"], ["rejected", "保留原值", "lookup-reject"]].forEach(([decision, label, cls]) => {
      const button = document.createElement("button"); button.type = "button"; button.className = cls; button.textContent = label;
      button.onclick = () => decideVerification(change.field, decision); actions.appendChild(button);
    });
    card.append(path, values, reason, actions); root.appendChild(card);
  });
}

async function loadVerification(projectId) {
  try {
    const response = await fetch(`${API}/api/projects/${projectId}/verification`);
    renderVerification(response.ok ? await response.json() : null);
  } catch { renderVerification(null); }
}

async function decideVerification(field, decision) {
  if (!currentProject) return;
  try {
    const response = await fetch(`${API}/api/projects/${currentProject}/verification/decide`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ field, decision, note: "" }),
    });
    const report = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(report.detail || response.status);
    renderVerification(report);
    if (decision === "confirmed") {
      const project = await fetch(`${API}/api/projects/${currentProject}`).then(r => r.json());
      if (project.ir) { currentIR = project.ir; renderIR(currentIR); }
    }
    status(decision === "confirmed" ? "已应用并留版该字段修改" : "已保留原值并记录驳回");
  } catch (error) { status("处理校核修改失败: " + error.message); }
}

const WORKFLOW = ["upload", "parse", "generate"];
function setWorkflow(stage, hint) {
  // 当前工作台承载的是 2.1 图纸解析及其校核/几何处理；
  // 只有正式输出报告时才应进入第 3 步，因此生成 CAD 后仍保持第 2 步高亮。
  const normalized = (stage === "review" || stage === "generate") ? "parse" : stage;
  const current = Math.max(0, WORKFLOW.indexOf(normalized));
  document.querySelectorAll(".main-step-wrapper[data-step]").forEach(wrapper => {
    const index = WORKFLOW.indexOf(wrapper.dataset.step);
    const step = wrapper.querySelector(".main-step");
    if (!step) return;
    const parseStageCompleted = normalized === "parse" && index === current;
    step.classList.toggle("active", index === current && !parseStageCompleted);
    step.classList.toggle("completed", index < current || parseStageCompleted);
    step.classList.toggle("pending", index > current);
  });
  document.querySelectorAll(".main-connector").forEach((line, index) =>
    line.classList.toggle("active", index < current));
  const el = $("workflowHint");
  if (el && hint) el.textContent = hint;
}

function questionStorageKey(question) {
  return [question.field || "", question.reason || "", question.guess || ""].join("\u001f");
}

function readQuestionConfirmations() {
  if (!currentProject) return {};
  try {
    return JSON.parse(localStorage.getItem(`cad-engine:clarifications:${currentProject}`) || "{}") || {};
  } catch {
    return {};
  }
}

function saveQuestionConfirmation(question, note) {
  if (!currentProject) return;
  const confirmations = readQuestionConfirmations();
  confirmations[questionStorageKey(question)] = note;
  try {
    localStorage.setItem(`cad-engine:clarifications:${currentProject}`, JSON.stringify(confirmations));
  } catch {
    // 浏览器禁用本地存储时仍保留本次页面内的确认输入。
  }
}

function renderClarifications(ir) {
  const ex = $("extras");
  ex.innerHTML = "";
  const standards = ir.standard_parts || [];
  const questions = ir.open_questions || [];

  if (standards.length) {
    const heading = document.createElement("div");
    heading.className = "standard-parts-heading";
    heading.textContent = "BOM / 外购及联网推演部件";
    ex.appendChild(heading);
    standards.forEach(s => {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = `🔩 ${s.spec} ×${s.quantity}`;
      ex.appendChild(tag);
    });
  }

  if (!questions.length) {
    const item = document.createElement("div");
    item.className = "issue-item issue-empty";
    const title = document.createElement("div");
    title.className = "issue-title";
    title.textContent = "人工确认说明";
    const desc = document.createElement("div");
    desc.className = "issue-desc";
    desc.textContent = "当前图纸未返回必须澄清的字段；如有补充约束，请在此确认。";
    const suggestion = document.createElement("div");
    suggestion.className = "issue-suggestion";
    suggestion.textContent = "AI 初步判断：现有信息可用于当前解析。";
    const action = document.createElement("div");
    action.className = "issue-action";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "issue-input";
    input.placeholder = "输入确认说明...";
    const emptyQuestion = { field: "人工确认说明", reason: desc.textContent, guess: suggestion.textContent };
    const confirmations = readQuestionConfirmations();
    input.value = confirmations[questionStorageKey(emptyQuestion)] || "";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "issue-btn";
    button.textContent = input.value ? "更新确认" : "确认";
    button.onclick = () => {
      const note = input.value.trim();
      if (!note) { input.focus(); status("请输入确认说明后再提交。"); return; }
      saveQuestionConfirmation(emptyQuestion, note);
      button.textContent = "更新确认";
      status("已记录人工确认说明。");
    };
    action.append(input, button);
    item.append(title, desc, suggestion, action);
    ex.appendChild(item);
    return;
  }

  const heading = document.createElement("div");
  heading.className = "clarification-heading";
  heading.textContent = standards.length ? "待澄清问题" : "请确认以下信息";
  ex.appendChild(heading);

  const confirmations = readQuestionConfirmations();
  questions.forEach(question => {
    const item = document.createElement("div");
    item.className = "issue-item";
    const title = document.createElement("div");
    title.className = "issue-title";
    title.textContent = question.field || "待确认项";
    const desc = document.createElement("div");
    desc.className = "issue-desc";
    desc.textContent = question.reason || "请补充确认说明。";
    item.append(title, desc);

    const suggestion = document.createElement("div");
    suggestion.className = "issue-suggestion";
    suggestion.textContent = `AI 初步判断：${question.guess || "图纸信息不足，需人工确认后继续。"}`;
    item.appendChild(suggestion);

    const action = document.createElement("div");
    action.className = "issue-action";
    const input = document.createElement("input");
    input.type = "text";
    input.className = "issue-input";
    input.placeholder = "输入确认说明...";
    input.value = confirmations[questionStorageKey(question)] || "";
    input.setAttribute("aria-label", `${question.field || "待确认项"}的确认说明`);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "issue-btn";
    button.textContent = input.value ? "更新确认" : "确认";
    button.onclick = () => {
      const note = input.value.trim();
      if (!note) {
        input.focus();
        status("请输入确认说明后再提交。");
        return;
      }
      saveQuestionConfirmation(question, note);
      button.textContent = "更新确认";
      status(`已记录「${question.field || "待确认项"}」的确认说明。`);
    };
    action.append(input, button);
    item.appendChild(action);
    ex.appendChild(item);
  });
}

// --------------------------------------------------------------------------- //
// 健康检查 + 历史项目
// --------------------------------------------------------------------------- //
const ROLE_LABEL_CN = { viewer: "只读", engineer: "工程师", reviewer: "校核/审签", admin: "管理员" };

async function init() {
  // 3D 预览是这一页的附属功能，不能拖垮整页。initViewer() 里 new THREE.WebGLRenderer()
  // 在没有 WebGL 的环境（远程桌面、虚拟机、显卡驱动被禁、浏览器关了硬件加速）会直接抛，
  // 原来抛出去就没人接：afterAuth() 不再执行 → 项目不加载 → 「开始解析」一直是灰的、
  // #intent 停在默认文案。而任务文件由 agent-chat.js（普通脚本）单独加载，照常显示 ——
  // 于是看起来像"文档传过来了，就是不让解析"。
  try {
    initViewer();
  } catch (error) {
    viewerBroken = true;
    const el = $("viewer");
    if (el) {
      el.innerHTML = "";
      const tip = document.createElement("div");
      tip.className = "view-3d-placeholder";
      tip.textContent = `3D 预览不可用（${error.message}）。解析、参数编辑与导出不受影响；`
        + "如需查看模型，请在浏览器设置中开启硬件加速 / WebGL 后刷新。";
      el.appendChild(tip);
    }
  }
  let h;
  try { h = await fetch(`${API}/api/health`).then(r => r.json()); }
  catch (error) {
    // #health 是 hidden 的，只写它等于什么也没说。启动失败必须写到看得见的状态栏。
    $("health").textContent = "后端未连接";
    status(`后端未连接（${error.message}）：「开始解析」等功能都不可用。`);
    return;
  }
  $("health").textContent =
    `CAD内核 ${h.cadquery_available ? "✓" : "✗(未装cadquery)"}`;
  authEnabled = !!h.auth_enabled;
  if (authEnabled && !(await refreshMe())) { $("loginOverlay").style.display = "flex"; return; }
  afterAuth();
}

async function refreshMe() {
  if (!authToken) return false;
  try {
    const r = await fetch(`${API}/api/me`);
    if (!r.ok) return false;
    currentUser = (await r.json()).user;
    return true;
  } catch { return false; }
}

function afterAuth() {
  renderUserBox();
  // 深链/恢复: 只认 URL ?project=&part=。项目身份走共享模块，没有 project 就是一个
  // 明确的错误态 —— 不再用「最近访问」里记的项目自动打开「上一次那个项目」，
  // 否则共享终端上会静默加载别人的项目。
  const q = new URLSearchParams(location.search);
  const pid = TechProjectContext.bind().project;
  if (!pid) {
    // 静默 return 的话，页面就停在"等待上传图纸"，而用户明明是从 1.3 走过来的。
    status("本页没有拿到项目编号（URL 缺 ?project=），因此没有加载任何图纸。"
           + "请从流程栏的「2.1 图纸解析」进入，或回首页选择项目。");
    return;
  }
  openProject(pid).then(() => {
    replayDrawingTimeline();
    const partId = q.get("part");
    if (!partId) return;
    const p = (currentIR && currentIR.parts || []).find(x => x.part_id === partId);
    if (p) selectPart(p);
  }).catch(error => {
    // 打不开就必须说出来。原来这里静默吞掉：页面看上去一切正常，「开始解析」等按钮
    // 却全是灰的，用户无从判断是"还没建任务"还是"上次那个项目已经没了"。
    // openProject 在校验前就写了 currentProject，失败后要清掉，否则对话框会以为
    // 还开着一个项目，把消息发给一个不存在的 id。
    localStorage.removeItem("lastProject");
    currentProject = null;
    status(`上次的项目打不开（${error.message}）。请在「＋ → 补充需求图纸」新建评估任务，`
           + "或从首页重新进入一个项目。");
  });
}

/* 写请求前的项目身份一致性校验（项目身份唯一来源 = tech-project-context.js）。
   规则与 9 个阶段页共用的 workflow.js::projectWriteGuard 一致：PUT/POST/DELETE/PATCH 且
   URL 指向 /api/projects/<id>/… 时，<id> 必须等于本页解析出的项目；本页身份为空
   （还没有 project 的合法链路）或非项目级 URL（POST /api/projects 建项）一律放行。
   放在 afterAuth 之后：项目身份在本页由 afterAuth 里的 TechProjectContext.bind() 解析，
   函数声明会提升，上面 window.fetch 包装里的调用不受位置影响。 */
function writeProjectMismatch(url, opts = {}) {
  const method = String(opts.method || "GET").toUpperCase();
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") return "";
  const match = String(url || "").match(/\/api\/projects\/([^/?#]+)(?:[/?#]|$)/);
  if (!match) return "";
  let target = match[1];
  try { target = decodeURIComponent(target); } catch (error) { /* 非法转义按原样比对 */ }
  let current = "";
  try {
    current = (typeof TechProjectContext !== "undefined" && TechProjectContext.current().project) || "";
  } catch (error) { current = ""; }
  if (!current || current === target) return "";
  return `本页的项目是 ${current}，不能把这次写入发给 ${target}；已拒绝发送。请从统一工作台重新进入该项目。`;
}

// 「更多功能 ▾」里补上 2.1 的两项能力入口：直接复用既有看板视图（import3d / review），
// 点完顺手收起菜单，不新建第二套面板。
$("btnMoreImport3d").onclick = () => { $("actionSheet").hidden = true; $("btnMoreActions").setAttribute("aria-expanded", "false"); return runBoardView("import3d"); };
$("btnMoreReview").onclick = () => { $("actionSheet").hidden = true; $("btnMoreActions").setAttribute("aria-expanded", "false"); return runBoardView("review"); };
$("btnMoreActions").onclick = () => {
  const sheet = $("actionSheet");
  sheet.hidden = !sheet.hidden;
  $("btnMoreActions").setAttribute("aria-expanded", String(!sheet.hidden));
};
document.addEventListener("click", (event) => {
  const sheet = $("actionSheet"), trigger = $("btnMoreActions");
  if (sheet && !sheet.hidden && !sheet.contains(event.target) && event.target !== trigger) {
    sheet.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
  }
});

// 看板弹卡片（导入已有 3D 模型 / 版本与校核 / 任务文件）：
// 关闭三条路径统一走 closeBoardCard()，不在各处各写一份收起逻辑。
if ($("boardCardClose")) $("boardCardClose").onclick = () => { closeBoardCard(); };
if ($("boardCardMask")) {
  $("boardCardMask").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeBoardCard();
  });
}
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeBoardCard();
});

$("btnPrevious").onclick = () => {
  if (history.length > 1) history.back();
  else if (__techEmbedMode__ && window.TechEmbed && window.TechEmbed.embedded) { window.TechEmbed.exitToTechHome(); } else location.href = "index.html";
};
$("btnReport").onclick = () => {
  if (!currentProject) {
    status("请先创建或打开一个项目，再查看工艺解析报告。");
    return;
  }
  location.href = `report.html?project=${encodeURIComponent(currentProject)}`;
};
// 【CPQ 定制】下一步 = 2.2 组装与整合（上游的 2.3–2.6 仍不在流程中）。路由与前置校验
// 都交给流程栏那套（workflow-navigation.js），这里不另写一份 —— 否则「解析没做完能不能进
// 下一步」会出现两个说法。
$("btnNext").onclick = () => {
  if (!currentProject) {
    status("请先创建或打开一个项目，再进入下一步。");
    return;
  }
  window.CadWorkflowNavigation?.navigate("2.2");
};

// 「技术工艺管理」「报价与决策」两个入口已从「更多功能」移除，openBusinessWorkbench
// 随之一并删掉 —— 留着就是没人调的死函数，下次有人照着改会以为还有这条路。
// 子应用本身没动，仍可从 /apps/tech-process/ 与解析报告页进入。

function activeChatSession() {
  if (!currentProject) return [];
  if (!chatSessions.has(currentProject)) chatSessions.set(currentProject, []);
  return chatSessions.get(currentProject);
}

function chatPartLabel(part) {
  if (!part) return "当前项目整体";
  return `${part.part_id} · ${part.name || "未命名零件"}`;
}

function renderChat() {
  const box = $("chatMessages");
  if (!box) return;
  const messages = activeChatSession();
  box.innerHTML = "";
  if (!messages.length) {
    box.innerHTML = `<div class="chat-empty"><div class="chat-empty-mark">AI</div><strong>图纸工艺助手</strong><p>选择零件后，可以询问结构、材料、工艺路径或待澄清事项。</p></div>`;
    return;
  }
  messages.forEach(message => {
    const row = document.createElement("div");
    row.className = `chat-message ${message.role}${message.error ? " error" : ""}`;
    const avatar = document.createElement("span");
    avatar.className = "chat-avatar";
    avatar.textContent = message.role === "user" ? "U" : "AI";
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    bubble.textContent = message.content;
    row.append(avatar, bubble);
    box.appendChild(row);
  });
  box.scrollTop = box.scrollHeight;
}

function renderChatTyping() {
  const box = $("chatMessages");
  if (!box) return;
  const row = document.createElement("div");
  row.className = "chat-message assistant";
  row.id = "chatTyping";
  row.innerHTML = `<span class="chat-avatar">AI</span><div class="chat-bubble chat-typing"><i></i><i></i><i></i></div>`;
  box.appendChild(row);
  box.scrollTop = box.scrollHeight;
}

function updateChatContext(part = null) {
  const context = $("chatContext");
  const reference = $("chatReference");
  const badge = $("chatModelBadge");
  if (!context || !reference || !badge) return;
  if (!currentProject) {
    context.textContent = "选择项目后可结合图纸和零件信息提问";
    reference.textContent = "当前未引用图纸";
    badge.textContent = "项目上下文";
    return;
  }
  const device = (currentIR && currentIR.device_name) || "当前图纸";
  context.textContent = `当前上下文：${device} · ${chatPartLabel(part)}`;
  reference.textContent = `已引用：${chatPartLabel(part)}、设计意图与解析结果`;
  badge.textContent = "AI 工艺助手";
}

function appendChatMessage(role, content, error = false) {
  activeChatSession().push({ role, content, error });
  renderChat();
}

async function refreshAfterChatEdit(edit) {
  if (!edit || !currentProject || !edit.part_id) return;
  status(`AI 已保存 ${edit.part_id} 的参数修改，正在刷新工作台…`, true);
  try {
    if (edit.requires_regeneration) {
      const response = await fetch(`${API}/api/projects/${currentProject}/parts/${encodeURIComponent(edit.part_id)}/regenerate`, { method: "POST" });
      const generated = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(generated.detail || "几何重生失败");
      upsertLocal(currentGeometry, generated.geometry);
      upsertLocal(currentDrawings, generated.drawings);
    }
    const project = await fetch(`${API}/api/projects/${currentProject}`).then(r => r.ok ? r.json() : null);
    if (project?.ir) {
      currentIR = project.ir;
      currentGeometry = project.geometry || currentGeometry;
      currentDrawings = project.drawings || currentDrawings;
      renderIR(currentIR);
      const part = (currentIR.parts || []).find(item => item.part_id === edit.part_id);
      if (part) selectPart(part);
    }
    loadVersions();
    status(edit.requires_regeneration ? "AI 修改已保存，3D 与工程图已重新生成。" : "AI 修改已保存，并已创建新版本。");
  } catch (error) {
    status(`AI 参数已保存，但几何/工程图刷新失败：${error.message}`);
  }
}

// 右下角统一对话入口在 2.1 会读取当前选择的零件，并在 AI 受控改参后通知本页刷新。
window.cadWorkbenchContext = {
  get projectId() { return currentProject || ""; },
  get selectedPartId() { return currentSelectedId || ""; },
};
window.addEventListener("cad-engine:workbench-chat-edit", event => {
  refreshAfterChatEdit(event.detail || null);
});

async function sendWorkbenchChat() {
  const input = $("chatInput");
  if (!input) return;
  const message = input.value.trim();
  if (!message || chatBusy) return;
  if (!currentProject) {
    appendChatMessage("assistant", "请先创建或打开项目，再结合图纸与零件信息提问。", true);
    return;
  }
  const selected = (currentIR && currentIR.parts || []).find(p => p.part_id === currentSelectedId);
  appendChatMessage("user", message);
  input.value = "";
  input.style.height = "auto";
  chatBusy = true;
  $("btnChatSend").disabled = true;
  renderChatTyping();
  try {
    const history = activeChatSession().slice(-7, -1).map(item => ({
      role: item.role,
      content: item.content,
    }));
    const response = await fetch(`${API}/api/projects/${currentProject}/workbench-chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, part_id: selected ? selected.part_id : "", history }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || response.status);
    appendChatMessage("assistant", data.answer || "未获得可用回复，请换一种问法。\n");
    if (data.model) $("chatModelBadge").textContent = data.model;
    if (data.edit_applied) await refreshAfterChatEdit(data.edit_applied);
  } catch (error) {
    appendChatMessage("assistant", `对话暂时失败：${error.message || "请稍后重试"}`, true);
  } finally {
    document.getElementById("chatTyping")?.remove();
    chatBusy = false;
    $("btnChatSend").disabled = false;
    $("chatInput").focus();
  }
}

// 旧三栏对话已收起；保留这段绑定的兼容分支，避免历史页面引用时报错。
if ($("chatForm")) {
  $("chatForm").onsubmit = (event) => {
    event.preventDefault();
    sendWorkbenchChat();
  };
  $("chatInput").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendWorkbenchChat();
    }
  });
  $("chatInput").addEventListener("input", event => {
    event.target.style.height = "auto";
    event.target.style.height = `${Math.min(event.target.scrollHeight, 96)}px`;
  });
  $("btnChatUseDrawing").onclick = () => {
    const selected = (currentIR && currentIR.parts || []).find(p => p.part_id === currentSelectedId);
    const input = $("chatInput");
    input.value = selected
      ? `请结合当前图纸，分析 ${chatPartLabel(selected)} 的制造工艺与风险。`
      : "请结合当前图纸，概述零件结构、关键工艺和待澄清风险。";
    input.focus();
    input.dispatchEvent(new Event("input"));
  };
}

function bindFilePicker(inputId, nameId) {
  const input = $(inputId);
  const name = $(nameId);
  input.addEventListener("change", () => {
    const files = Array.from(input.files || []);
    if (!files.length) name.textContent = "未选择文件";
    else if (files.length === 1) name.textContent = files[0].name;
    else name.textContent = `已选择 ${files.length} 个文件`;
  });
}
bindFilePicker("fileInput", "fileInputName");
bindFilePicker("attachInput", "attachInputName");
bindFilePicker("file3d", "file3dName");

function renderUserBox() {
  // 2.1 工作台不再放账户/登出入口，账户资料和权限统一由首页右上角进入。
}

$("loginForm").onsubmit = async (e) => {
  e.preventDefault();
  $("loginErr").textContent = "";
  const username = $("loginUser").value.trim();
  const password = $("loginPass").value;
  try {
    const r = await fetch(`${API}/api/login`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const d = await r.json();
    if (!r.ok) { $("loginErr").textContent = d.detail || "登录失败"; return; }
    authToken = d.token; currentUser = d.user;
    // 两个键都写，和 auth.js / account.js 一致 —— 只写一个，另一半页面会当你没登录。
    localStorage.setItem("authToken", authToken);
    localStorage.setItem("cad_engine_token", authToken);
    $("loginOverlay").style.display = "none";
    afterAuth();
  } catch { $("loginErr").textContent = "网络错误"; }
};


// --------------------------------------------------------------------------- //
// 流程: 上传 -> 解析 -> 拆解 -> 生成
// --------------------------------------------------------------------------- //
$("btnUpload").onclick = async () => {
  const f = $("fileInput").files[0];
  if (!f) { status("请先选择图片"); return; }
  status("上传中...", true);
  const fd = new FormData();
  fd.append("file", f);
  fd.append("note", $("noteInput").value || "");
  for (const a of $("attachInput").files) fd.append("attachments", a);
  const res = await fetch(`${API}/api/projects`, { method: "POST", body: fd }).then(r => r.json());
  currentProject = res.project_id;
  currentIR = null; currentGeometry = null; currentDrawings = null;
  currentIsImg = true; currentSelectedId = null;
  diffPick = []; $("versions").innerHTML = ""; $("diffView").innerHTML = "";
  updateChatContext();
  renderChat();
  const phEl = document.querySelector(".image-wrap .placeholder");
  if (phEl) phEl.remove();
  $("sourceImg").style.display = "";
  $("sourceImg").src = mediaUrl(`${API}/api/projects/${currentProject}/source?t=${Date.now()}`);
  $("btnParse").disabled = false;
  $("btnVerify").disabled = true;
  $("btnDecompose").disabled = true;
  $("btnModelLookup").disabled = true;
  $("btnGenerate").disabled = true;
  $("btnDrawings").disabled = true;
  $("btnBom").disabled = true;
  renderModelLookup(null);
  const nAtt = $("attachInput").files.length;
  status(`已创建项目 ${currentProject}（补充说明${$("noteInput").value ? "✓" : "—"}，佐证文件 ${nAtt} 个），可解析`);
  setWorkflow("parse", "图纸已保存。确认后可开始 AI 解析。");
};

/* 2.1 的入口判定：决定这个项目走哪条解析链路。
   · DWG / DXF → drawing_flow（服务端：DWG→DXF→CAD IR→包装语义，能力只有服务端一份）；
   · PNG / JPG / WEBP / GIF / BMP → vision（既有视觉模型路径，本批不动）；
   · STEP / IGES / STL → blocked_3d（几何来自原始实体，不该按图纸重建）；
   · 其余（PDF / DOCX / 空名）→ blocked_other。
   纯函数：不碰 DOM，可被 node 直接执行（与 resolveInitialIndustry 同一条纪律）。 */
function renderDrawingEntry(filename) {
  const name = String(filename || "").trim().toLowerCase();
  const dot = name.lastIndexOf(".");
  const ext = dot < 0 ? "" : name.slice(dot);
  if (ext === ".dwg" || ext === ".dxf") return "drawing_flow";
  if (ext === ".png" || ext === ".jpg" || ext === ".jpeg" || ext === ".webp"
      || ext === ".gif" || ext === ".bmp") return "vision";
  if (ext === ".step" || ext === ".stp" || ext === ".iges" || ext === ".igs"
      || ext === ".stl") return "blocked_3d";
  return "blocked_other";
}

/* 2.1「这一步做到哪了」的唯一判定：有解析结果（零件或标准件）才算有效。
   左侧主按钮的 role、收口动作的可见性都由它推导，不许各处各写一份。 */
function drawingParsed() {
  return Boolean(currentIR && ((currentIR.parts || []).length || (currentIR.standard_parts || []).length));
}

// #actionSheet 八颗按钮唯一的启用判定：解析成功与打开已有项目两处共用，
// 不许各写一份（#btnMoreImport3d / #btnMoreReview 曾只在解析成功那一刻启用，
// 打开已有项目后永远置灰，用户点不了 —— 那是缺陷不是设计）。
function syncActionSheet(ir) {
  const data = ir || currentIR;
  const isImg = Boolean(currentIsImg);
  $("btnVerify").disabled = !isImg || !data;
  $("btnDecompose").disabled = !data;
  $("btnModelLookup").disabled = !data;
  $("btnGenerate").disabled = !isImg || !data;
  $("btnDrawings").disabled = !isImg || !data;
  $("btnBom").disabled = !data;
  // 导入已有 3D 模型 / 版本与校核审查只依赖「有项目」：它们原先只在解析成功
  // 那一刻被启用，打开已有项目后永远置灰，用户点不了。
  const hasProject = Boolean(currentProject);
  $("btnMoreImport3d").disabled = !hasProject;
  $("btnMoreReview").disabled = !hasProject;
}

// 具名函数：原 #btnParse 点击逻辑原样搬过来，按钮和统一看板动作共用同一份实现。
let parseDrawingError = "";
async function parseDrawing() {
  if (!currentProject) { parseDrawingError = "尚未绑定项目，无法开始解析。"; return null; }
  const parseButton = $("btnParse");
  parseButton.disabled = true;
  parseButton.setAttribute("aria-busy", "true");
  parseButton.setAttribute("aria-label", "正在解析");
  parseButton.innerHTML = '<span class="parse-spinner" aria-hidden="true"></span><span>正在解析…</span>';
  try {
    // DWG / DXF 不走视觉模型：服务端已有图纸解析链路，前端只点一次、把结果摆出来。
    if (currentDrawingEntry === "drawing_flow") {
      await runDrawingFlowParse();
      parseDrawingError = "";
      return null;
    }
    status("模型正在解析图纸与技术文档需求为结构化 IR（含视觉理解，稍候）...", true);
    currentIR = await runTask(currentProject, `/api/projects/${currentProject}/parse`, "解析");
    renderIR(currentIR);
    syncActionSheet(currentIR);
    const documentCount = await fetch(`${API}/api/projects/${currentProject}/attachments`)
      .then(r => r.ok ? r.json() : { attachments: [] })
      .then(payload => (payload.attachments || []).length)
      .catch(() => 0);
    status(`解析完成（已结合 ${documentCount} 份技术资料；平均置信度 ${avgConfidence(currentIR)}）`);
    setWorkflow("review", "AI 已完成解析，请确认零件、材料与待澄清项。");
    // 统一工作台里零部件库检索明细属于右侧看板：解析后把后端顺手算好、已落盘的
    // 报告读到看板内部（独立 2.1 页仍由对话侧渲染，不重复画第二份）。
    if (__techEmbedMode__) refreshComponentMatchResult();
    // 通知 2.1 的 Agent 对话框：把零件清单与待澄清问题渲染成结果按钮。
    window.dispatchEvent(new CustomEvent("agent:parse-done", {
      detail: {
        summary: `解析完成：识别 ${(currentIR.parts || []).length} 个零件、`
          + `${(currentIR.standard_parts || []).length} 项标准件，`
          + `平均置信度 ${avgConfidence(currentIR)}。`,
      },
    }));
    // 解析成功后自动串行生成几何与 2D 工程图：用户不必再进「更多功能 ▾」点两次。
    await autoGenerateAfterParse();
    parseDrawingError = "";
    return currentIR;
  } catch (e) { parseDrawingError = e.message; status("解析失败: " + e.message); return null; }
  finally {
    parseButton.disabled = false;
    parseButton.removeAttribute("aria-busy");
    parseButton.setAttribute("aria-label", "开始解析");
    parseButton.textContent = "▶ 开始解析";
  }
}
/* ---------------- 2.1 DWG / DXF：服务端图纸解析链路 ---------------- */
// 步骤名与状态的中文口径只在这里一份；未知值原样显示，不臆造。
const DRAWING_FLOW_STEP_LABEL = {
  file_preflight: "文件预检", dwg_convert: "DWG → DXF 转换", cad_ir_parse: "CAD 矢量解析",
  packaging_semantics: "包装语义识别", field_write: "字段回填",
  pending_confirm: "待确认", downstream_prepare: "下游准备",
};
const DRAWING_FLOW_STATUS_LABEL = {
  completed: "已完成", running: "进行中", pending: "待执行",
  failed: "失败", unavailable: "不可用", blocked: "被阻断", skipped: "已跳过",
};

function ensureDrawingFlowPanel() {
  let panel = document.getElementById("drawingFlowPanel");
  if (panel) return panel;
  panel = document.createElement("div");
  panel.id = "drawingFlowPanel";
  panel.className = "drawing-flow-panel";
  const anchor = document.querySelector(".image-wrap");
  if (anchor && anchor.parentNode) anchor.parentNode.appendChild(panel);
  else document.body.appendChild(panel);
  return panel;
}

function drawingFlowSteps(payload) {
  const flow = (payload && payload.flow) || payload || {};
  return Array.isArray(flow.steps) ? flow.steps : [];
}

// cad_ir 摘要只读服务端给的事实（步骤 detail 与步骤带回的 ir），前端不重解析 DWG。
function cadIrSummaryOf(payload) {
  const steps = drawingFlowSteps(payload);
  const step = steps.filter(s => s && s.step_id === "cad_ir_parse")[0] || {};
  const detail = step.detail || {};
  const ir = (step.ir && typeof step.ir === "object") ? step.ir : {};
  const entities = Array.isArray(ir.entities) ? ir.entities : [];
  const counts = {};
  entities.forEach(e => {
    const type = String((e && e.type) || "").toUpperCase();
    if (type) counts[type] = (counts[type] || 0) + 1;
  });
  const fallback = (detail.counts && typeof detail.counts === "object") ? detail.counts : {};
  Object.keys(fallback).forEach(k => { counts[k] = Number(fallback[k] || 0) || 0; });
  return {
    ir_id: String(detail.ir_id || ir.ir_id || ""),
    entities: entities.length || Number(detail.entity_total || 0) || 0,
    layers: (Array.isArray(ir.layers) ? ir.layers.length : 0) || Number(detail.layer_total || 0) || 0,
    unit_status: String(detail.unit_status || ""),
    counts: counts,
  };
}

function renderDrawingFlowPanel(payload) {
  const panel = ensureDrawingFlowPanel();
  panel.hidden = false;
  const steps = drawingFlowSteps(payload);
  const summary = cadIrSummaryOf(payload);
  const rows = steps.map(s => {
    const id = String((s && s.step_id) || "");
    const status = String((s && s.status) || "pending");
    const code = String((s && s.error_code) || "");
    const message = String((s && s.error_message) || "");
    // 失败必须看得见原因：稳定错误码 + 服务端原文，不许收敛成"解析失败，请重试"。
    const err = (code || message)
      ? `<div class="drawing-flow-error">${esc((code ? "[" + code + "] " : "") + message)}</div>` : "";
    return `<tr class="drawing-flow-row drawing-flow-${esc(status)}">`
      + `<td class="drawing-flow-step">${esc(DRAWING_FLOW_STEP_LABEL[id] || id)}</td>`
      + `<td class="drawing-flow-status">${esc(DRAWING_FLOW_STATUS_LABEL[status] || status)}</td>`
      + `<td class="drawing-flow-detail">${err || esc(String((s && s.title) || ""))}</td></tr>`;
  }).join("");
  const types = Object.keys(summary.counts).sort()
    .map(k => `${esc(k)} ${summary.counts[k]}`).join(" / ");
  const cadIr = (summary.entities || summary.layers)
    ? `<div class="drawing-flow-cad-ir">CAD IR：实体 <b>${summary.entities}</b> · 图层 <b>${summary.layers}</b>`
      + `${summary.unit_status ? " · 单位 " + esc(summary.unit_status) : ""}`
      + `${types ? `<div class="drawing-flow-types">${types}</div>` : ""}</div>`
    : '<div class="drawing-flow-cad-ir drawing-flow-empty">CAD IR：尚无解析结果</div>';
  panel.innerHTML = '<div class="drawing-flow-title">图纸解析链路（DWG / DXF）</div>'
    + (rows ? `<table class="drawing-flow-table"><tbody>${rows}</tbody></table>` : "")
    + cadIr;
}

async function fetchDrawingFlowState() {
  const res = await fetch(`${API}/api/projects/${currentProject}/drawing-flow`);
  if (!res.ok) return null;
  return res.json().catch(() => null);
}

async function loadDrawingFlowPanel() {
  const state = await fetchDrawingFlowState().catch(() => null);
  if (state) renderDrawingFlowPanel(state);
}

async function runDrawingFlowParse() {
  status("正在跑图纸解析链路（DWG → DXF → CAD IR → 包装语义），稍候…", true);
  const res = await fetch(`${API}/api/projects/${currentProject}/drawing-flow/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt: "" }),
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (payload && (payload.detail || payload.message)) || "";
    throw new Error(typeof detail === "string" && detail
      ? detail : `图纸解析请求失败（HTTP ${res.status}）`);
  }
  const state = await fetchDrawingFlowState().catch(() => null);
  renderDrawingFlowPanel(state || payload);
  const summary = cadIrSummaryOf(state || payload);
  status(summary.entities
    ? `图纸解析完成：CAD IR 实体 ${summary.entities} · 图层 ${summary.layers}`
    : "图纸解析链路已跑完，详见下方步骤表。");
  setWorkflow("review", "DWG / DXF 已由服务端图纸解析链路处理，请核对步骤与 CAD IR 摘要。");
  return payload;
}

$("btnParse").onclick = parseDrawing;

// 具名函数：原 #btnModelLookup 点击逻辑原样搬过来，页面按钮与统一看板动作
// （TechBoardRuntime 的 modelLookup）共用同一份实现，不复制第二份核验逻辑。
async function runModelLookup() {
  if (!currentProject || !currentIR) {
    return { ok: false, error: { code: "not-ready", message: "请先完成图纸解析，再做型号联网核验。" } };
  }
  status("AI 正在联网核验型号候选与公开零件资料（会产生联网搜索与模型 token 消耗）...", true);
  try {
    const report = await runTask(currentProject, `/api/projects/${currentProject}/model-lookup`, "型号联网核验");
    renderModelLookup(report);
    $("modelLookupDetails").open = true;
    status(`型号联网核验完成（${(report.identifications || []).length} 个候选，搜索 ${report.search_count || 0} 次；确认后才会同步至零件清单/BOM）`);
    publishResultSummary(currentIR);
    return { ok: true, result: { candidates: (report.identifications || []).length } };
  } catch (error) {
    status("型号联网核验失败: " + error.message);
    return { ok: false, error: { code: "model-lookup-failed", message: error.message } };
  }
}
$("btnModelLookup").onclick = runModelLookup;

// 具名函数：原 #btnVerify 点击逻辑原样搬过来，页面按钮与统一看板动作
// （TechBoardRuntime 的 verify）共用同一份实现。
async function runVerification() {
  if (!currentProject) {
    return { ok: false, error: { code: "not-ready", message: "还没有选择项目，无法做校验修正。" } };
  }
  const before = avgConfidence(currentIR);
  status("模型正在对照原图自校验（会产生一次额外调用费用）...", true);
  try {
    const result = await runTask(currentProject, `/api/projects/${currentProject}/verify`, "校验");
    if (result.verification && result.verification.status === "rejected") {
      currentIR = result.ir;
      renderIR(currentIR);
      status(result.verification.message + " 详情：" + result.verification.detail);
      return { ok: false, error: { code: "verification-rejected", message: result.verification.message || "校验未通过。" } };
    }
    currentIR = result.ir || result;
    renderIR(currentIR);
    const verification = result.verification || {};
    renderVerification({
      applied_changes: verification.applied_changes || [],
      pending_changes: verification.pending_changes || [],
    });
    if ((verification.pending_changes || []).length) $("verificationDetails").open = true;
    status(`${verification.message || "校验完成"}（平均置信度 ${before} → ${avgConfidence(currentIR)}）`);
    setWorkflow("review", "校验完成，可继续确认识别结果或生成 CAD。");
    publishResultSummary(currentIR);
    return { ok: true, result: { pending: (verification.pending_changes || []).length } };
  } catch (e) {
    status("自校验失败: " + e.message);
    return { ok: false, error: { code: "verification-failed", message: e.message } };
  }
}
$("btnVerify").onclick = runVerification;

/* 零部件库检索：复用既有 /component-match 接口与既有轮询，不复制第二份打分逻辑。
 * refreshOnly=true 表示后端已有落盘报告（解析 / 拆解后由后端顺手重跑），只读取渲染。 */
async function refreshComponentMatchResult() {
  if (!currentProject) return null;
  const report = await fetch(`${API}/api/projects/${encodeURIComponent(currentProject)}/component-match`)
    .then(r => (r.ok ? r.json() : null))
    .catch(() => null);
  renderComponentMatchResult(report);
  return report;
}

async function runComponentMatch(payload) {
  if (!currentProject) {
    return { ok: false, error: { code: "no-project", message: "还没有选择项目，无法检索零部件库。" } };
  }
  const refreshOnly = Boolean(payload && payload.refreshOnly);
  try {
    if (!refreshOnly) {
      status("正在重新检索零部件库…", true);
      const response = await fetch(`${API}/api/projects/${encodeURIComponent(currentProject)}/component-match`, { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      if (data.task_id) await pollTask(currentProject, data.task_id, "零部件库检索");
    }
    const report = await refreshComponentMatchResult();
    publishResultSummary(currentIR);
    const summary = (report && report.summary) || {};
    status(`零部件库检索完成（可复用 ${summary.reuse || 0}、可改制 ${summary.modify || 0}、未匹配 ${summary.new || 0}）`);
    return { ok: true, result: { items: ((report && report.items) || []).length } };
  } catch (error) {
    status("零部件库检索失败: " + error.message);
    return { ok: false, error: { code: "component-match-failed", message: error.message } };
  }
}

// 检索报告渲染在右侧看板内部（零件清单面板里），不搬进父壳会话。
function renderComponentMatchResult(report) {
  const section = document.getElementById("secParts");
  if (!section) return;
  let slot = document.getElementById("componentMatchResult");
  if (!slot) {
    slot = document.createElement("div");
    slot.id = "componentMatchResult";
    slot.className = "component-match-result";
    section.append(slot);
  }

  // ---- 看板顶部：零件库这次到底查到没有（## 92）----
  // 「库里确实没有可复用零件」是一份正常报告；「压根没查到」没有任何数字可谈。
  // 两者在界面上必须一眼分开，而且状态是后端落盘的：切换看板、刷新页面后仍在，
  // 直到一次成功检索把它清掉（后端 save_component_match 会清）。
  const unavailable = (report && report.unavailable) || null;
  let banner = document.getElementById("componentMatchBanner");
  if (!unavailable) {
    if (banner) banner.remove();
  } else {
    const headline = "零件库连不上，本次未检索";
    const notes = [];
    if (unavailable.message && String(unavailable.message) !== headline) {
      notes.push(String(unavailable.message));
    }
    if (unavailable.error) notes.push(String(unavailable.error));
    if (unavailable.at) notes.push(String(unavailable.at));
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "componentMatchBanner";
      banner.className = "component-match-unavailable";
      // 挂在看板容器最顶部：它不属于零件清单本身，更不能跟着清单滚走
      // （.drawing-parts-column 是独立滚动的，塞在清单里会滚出台面）。
      // 容器取不到时才退回 #secParts 的父节点、紧贴清单之前插。
      const panel = (section.closest ? section.closest(".center-panel") : null)
        || document.querySelector(".center-panel");
      if (panel && panel.prepend) panel.prepend(banner);
      else if (section.parentNode && section.parentNode.insertBefore) {
        section.parentNode.insertBefore(banner, section);
      } else section.append(banner);
    }
    banner.className = "component-match-unavailable";
    banner.textContent = headline + (notes.length ? `（${notes.join(" · ")}）` : "");
  }

  const items = (report && report.items) || [];
  const summary = (report && report.summary) || {};
  // 每条结论由三段独立节点组成（件号/名称 · 命中件 · 判定胶囊），不再拼成一段文字：
  // 判定进胶囊后就不需要外层括号，也就不会出现「（未匹配（…」这种嵌套括号。
  // 匹配度只在真的命中（有 component_code）时出现 —— 未匹配行上的分数只说明"最像的
  // 候选也不太像"，挂在结论里会误导。
  const rowOf = (item) => {
    const row = document.createElement("div");
    row.className = `component-match-item ${item.decision || "new"}`;
    const part = document.createElement("span");
    part.className = "component-match-part";
    part.textContent = `${item.part_id || "?"} ${item.part_name || ""}`.trim();
    const hit = document.createElement("span");
    hit.className = "component-match-hit";
    hit.textContent = item.component_code
      ? `${item.component_code} ${item.component_name || ""}`.trim() : "库内无同类件";
    const tag = document.createElement("span");
    tag.className = `component-match-tag ${item.decision || "new"}`;
    tag.textContent = item.decision_label || "未匹配";
    row.append(part, hit, tag);
    if (item.component_code && item.score) {
      const score = document.createElement("span");
      score.className = "component-match-score";
      score.textContent = `匹配度 ${Math.round(Number(item.score) * 100)}%`;
      row.append(score);
    }
    return row;
  };
  const listOf = (rows) => {
    const node = document.createElement("div");
    node.className = "component-match-list";
    rows.forEach(item => node.append(rowOf(item)));
    return node;
  };

  slot.replaceChildren();
  if (unavailable) {
    // 本次没有任何结论：结论区只写「未检索」，绝不拿旧数字冒充本次结果
    // （「库内 0 条」「可复用 0」这类把故障当结论的文案一律不出现）。
    const head = document.createElement("div");
    head.className = "component-match-unavailable-note";
    head.textContent = "未检索（库连不上）";
    slot.append(head);
    if (!items.length) return;
    const stale = document.createElement("div");
    stale.className = "component-match-stale";
    stale.textContent = "上一次的结论（可能已过期）";
    slot.append(stale);
    slot.append(listOf(items));
    return;
  }
  if (!items.length) {
    slot.textContent = "还没有零部件库检索结果（解析完成后自动生成）。";
    return;
  }
  const head = document.createElement("div");
  head.className = "component-match-summary";
  head.textContent = `零部件库检索：可复用 ${summary.reuse || 0} · 可改制 ${summary.modify || 0} · 未匹配 ${summary.new || 0}`
    + `（库内 ${report.library_size || 0} 条${report.generated_at ? " · " + report.generated_at : ""}）`;
  slot.append(head);
  slot.append(listOf(items));
}

$("btnDecompose").onclick = async () => {
  if (!currentProject) return;
  status("模型正在做拆解推荐增强...", true);
  try {
    currentIR = await runTask(currentProject, `/api/projects/${currentProject}/decompose`, "拆解推荐");
    renderIR(currentIR);
    // 拆解会改写零件清单，后端已顺手重跑检索；对话里那张卡片得跟着换新，
    // 否则界面上还挂着按旧清单算的结论。
    window.dispatchEvent(new CustomEvent("agent:component-match-updated"));
    status("拆解推荐完成");
    setWorkflow("review", "拆解建议已更新，可确认后生成 CAD。");
  } catch (e) { status("拆解失败: " + e.message); }
};

// 具名函数：原 #btnGenerate 点击逻辑原样搬过来，页面按钮与「解析后自动生成 3D」共用
// 同一份实现；回执是结构化摘要（C5），自动流程按 infrastructure_ok / processable 判定。
// 批量回执 → 结构化摘要：C5。partial 也算「基础设施正常」（有成功件就是成功），
// 只有 HTTP 503 / 任务 failed / IR 并发变更 / 提交响应 status=failed 才是不可用。
function batchSummary(payload) {
  const parts = Array.isArray(payload && payload.parts) ? payload.parts : [];
  const succeeded = parts.filter(p => p.ok).length;
  const skipped = parts.filter(p => !p.ok && p.skipped).length;
  const failed = parts.length - succeeded - skipped;
  const blocked = Array.isArray(payload && payload.blocked) ? payload.blocked : [];
  const total = Number(payload && payload.total) || parts.length;
  const reported = Number(payload && payload.processable);
  return {
    infrastructure_ok: !(payload && payload.status === "failed" && !payload.task_id),
    total,
    processable: Number.isInteger(reported) ? reported : succeeded + failed,
    succeeded, failed, skipped, blocked,
  };
}

async function generateGeometry() {
  if (!currentProject) {
    return { infrastructure_ok: false, total: 0, processable: 0,
             succeeded: 0, failed: 0, skipped: 0, blocked: [] };
  }
  status("CAD 内核正在生成几何(STEP/STL)并校验...", true);
  try {
    const d = await runTask(currentProject, `/api/projects/${currentProject}/generate`, "几何生成");
    const summary = batchSummary(d);
    if (!Array.isArray(d && d.parts)) {
      // 逐件预检同步判定整批不可执行（processable === 0）：没有 task_id 就不起任务、
      // 不去轮询 null，直接把 blocked 写进状态行 —— 确定性缺参数不该再堆失败任务。
      status(`几何生成未提交：${summary.blocked.length} 个零件待补参数（可生成 ${summary.processable}/${summary.total} 件），可在零件清单里补充`);
      return { ...summary, infrastructure_ok: false };
    }
    currentGeometry = d;
    renderIR(currentIR);  // 重渲染以挂上几何状态
    showGeneratedResult();   // 生成完直接显示，不让用户再点一次
    status(`几何生成完成（${summary.succeeded}/${summary.total} 件）：${summary.succeeded} 成功、${summary.skipped} 待补`);
    setWorkflow("generate", "CAD 几何已生成，可查看 3D、工程图和 BOM。");
    return summary;
  } catch (e) {
    status("几何生成失败: " + e.message);
    return { infrastructure_ok: false, total: 0, processable: 0,
             succeeded: 0, failed: 0, skipped: 0, blocked: [] };
  }
}
$("btnGenerate").onclick = generateGeometry;

// 具名函数：原 #btnDrawings 点击逻辑原样搬过来，页面按钮与「解析后自动生成 2D」共用
// 同一份实现。
async function generateDrawings() {
  if (!currentProject) {
    return { infrastructure_ok: false, total: 0, processable: 0,
             succeeded: 0, failed: 0, skipped: 0, blocked: [] };
  }
  status("CAD 内核正在投影 2D 工程图(三视图 SVG + 下料 DXF)...", true);
  try {
    const d = await runTask(currentProject, `/api/projects/${currentProject}/drawings`, "2D 工程图");
    const summary = batchSummary(d);
    if (!Array.isArray(d && d.parts)) {
      status(`2D 工程图未提交：${summary.blocked.length} 个零件待补参数（可生成 ${summary.processable}/${summary.total} 件），可在零件清单里补充`);
      return { ...summary, infrastructure_ok: false };
    }
    currentDrawings = d;
    renderIR(currentIR);
    showGeneratedResult();
    status(`2D 工程图完成（${summary.succeeded}/${summary.total} 件）：${summary.succeeded} 成功、${summary.skipped} 待补`);
    return summary;
  } catch (e) {
    status("2D 工程图生成失败: " + e.message);
    return { infrastructure_ok: false, total: 0, processable: 0,
             succeeded: 0, failed: 0, skipped: 0, blocked: [] };
  }
}
$("btnDrawings").onclick = generateDrawings;

// 解析成功后的唯一自动入口：只服务「图→IR」项目（3D 导入项目的几何与工程图在导入阶段
// 已有，直接跳过）。先生成 3D 再生成 2D，串行执行。
async function autoGenerateAfterParse() {
  if (!currentIsImg || !currentIR) return false;
  const gate = await generateGeometry();
  // 只有基础设施 / IR 级失败才停：单个零件预检失败不得阻断其余零件的 2D。
  if (!gate || gate.infrastructure_ok === false) return false;
  // 一个可生成零件都没有时不必空跑 2D（没有输入，只会产出一批空壳）。
  if (!gate.processable) return false;
  return await generateDrawings();
}

$("btnBom").onclick = () => {
  if (!currentProject) return;
  window.open(mediaUrl(`${API}/api/projects/${currentProject}/bom.csv`), "_blank");
};

$("btnImport3d").onclick = async () => {
  const f = $("file3d").files[0];
  if (!f) { status("请选择 STEP/STP 文件"); return; }
  status("OCCT 正在解析 3D 模型并生成结构树/几何/2D 工程图...", true);
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await fetch(`${API}/api/projects/3d`, { method: "POST", body: fd });
    const res = await r.json();
    if (!r.ok) throw new Error(res.detail || r.status);
    const out = await pollTask(res.project_id, res.task_id, "3D 解析");
    await openProject(res.project_id);
    window.dispatchEvent(new CustomEvent("agent:component-match-updated"));
    status(`3D 导入完成：解析出 ${out.parts} 个零件，已生成 3D/2D/结构树/BOM`);
  } catch (e) { status("3D 导入失败: " + e.message); }
};

async function postJSON(path) {
  const r = await fetch(`${API}${path}`, { method: "POST" });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
  return r.json();
}

// --------------------------------------------------------------------------- //
// 异步任务: 提交 -> 轮询状态/进度 -> 取结果(替代原来的阻塞式调用)
// --------------------------------------------------------------------------- //
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
// 同一页面中相同任务只允许一个轮询链，双击不会重复提交/重复计费。
const activeTaskPromises = new Map();

// 同一份任务 detail 既要播给同窗口的 Agent 对话框（agent:task-progress），也要经看板
// 运行时按状态转给统一父壳 —— 统一工作台里看板是 iframe，同窗口事件跨不过去。
// 独立打开（无父壳）时 TechBoardRuntime 仍存在，但不会向父窗口发送任何消息，行为不变。
function forwardTaskDetail(detail) {
  const runtimeEvent = detail.status === "failed" ? "task-failed"
    : (detail.status === "succeeded" || detail.status === "partial") ? "task-completed"
    : "task-progress";
  if (window.TechBoardRuntime && window.TechBoardRuntime.publish) {
    window.TechBoardRuntime.publish(runtimeEvent, "board-task", detail);
  }
  persistDrawingTask(detail);
  return detail;
}

async function pollTask(projectId, taskId, label) {
  while (true) {
    await sleep(1200);
    let t;
    try { t = await fetch(`${API}/api/projects/${projectId}/tasks/${taskId}`).then(r => r.json()); }
    catch { continue; }  // 网络抖动则继续轮询
    // 把任务进度同时播给 Agent 对话框，让处理过程显示在对话里。
    // 传整份 progress_log 而不是单条 progress：轮询间隔内后端可能已经走完好几步，
    // 只传"最新一条"的话中间步骤全都丢了（检索类任务尤其明显）。
    window.dispatchEvent(new CustomEvent("agent:task-progress", {
      detail: forwardTaskDetail({
        label, taskId, status: t.status,
        progress: t.progress || "",
        log: Array.isArray(t.progress_log) ? t.progress_log : [],
        // 过程事件序列（model/tool/progress）：带 process_log 时按它以 seq 顺序渲染。
        process: Array.isArray(t.process_log) ? t.process_log : null,
        error: t.error || "",
      }),
    }));
    // partial 与 succeeded 一样是终态：不再等待，也不当失败（C4）。
    if (t.status === "succeeded" || t.status === "partial") return t.result;
    if (t.status === "failed") throw new Error(t.error || "任务失败");
    status(`${label}：${t.progress || "正在处理"}…`, true);
  }
}

// ---------------------------------------------------------------- 会话时间线（2.1）
// 2.1 的任务进度卡除了播给会话宿主（同窗口的 agent-chat.js / 统一工作台的父壳看板桥），
// 也按项目落库：seq 由服务端分配、key 固定 task:<taskId> 幂等，重进项目或重载 iframe 后
// 由统一的 tech-session-timeline.js 回放 —— 不再只存在 DOM 里、也不再钉在会话底部。
const DRAWING_STAGE = "drawing";
// 每个任务已经落库过的进度行数：轮询只提交新出现的行，不重复追加整段。
const drawingTimelineCursor = new Map();
// 会话时间线的两个出口都写全路径（POST /agent/event 落库、GET /agent/events 回放），
// 避免各处再拼一份路径、也方便和父壳的同一份契约对照。
function drawingTimelineEventUrl() {
  return `/api/projects/${encodeURIComponent(currentProject)}/agent/event`;
}
function drawingTimelineEventsUrl() {
  return `/api/projects/${encodeURIComponent(currentProject)}/agent/events?stage=drawing&source=board`;
}
function persistDrawingTask(detail) {
  const taskId = String(detail.taskId || detail.task_id || "");
  if (!currentProject || !taskId) return;
  const log = Array.isArray(detail.log)
    ? detail.log.map(line => String(line).replace(/\s+$/, "")).filter(Boolean) : [];
  const fresh = log.slice(drawingTimelineCursor.get(taskId) || 0);
  drawingTimelineCursor.set(taskId, log.length);
  if (!fresh.length && !detail.status) return;
  fetch(drawingTimelineEventUrl(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kind: "task", source: "board", stage: DRAWING_STAGE,
      text: String(detail.label || ""), key: `task:${taskId}`,
      task: { id: taskId, label: String(detail.label || ""),
              status: String(detail.status || ""), steps: fresh,
              error: String(detail.error || "") },
    }),
  }).catch(() => { /* 落库失败不影响看板与本地会话可见性 */ });
}
// 回放本阶段已落库条目：交给会话宿主按同一套 (ts, seq) 顺序渲染；
// 任务卡走 agent:task-progress，宿主按 task.id 就地合并，重复回放不会叠加。
async function replayDrawingTimeline() {
  if (!currentProject) return;
  let payload = null;
  try {
    payload = await fetch(drawingTimelineEventsUrl())
      .then(response => (response.ok ? response.json() : null));
  } catch (error) { return; }
  const rows = (window.TechSessionTimeline && payload)
    ? window.TechSessionTimeline.forStage({ stage: DRAWING_STAGE, events: payload.events || [] })
    : [];
  rows.filter(row => row.kind === "task" && row.task).forEach(row => {
    window.dispatchEvent(new CustomEvent("agent:task-progress", {
      detail: { label: row.task.label || row.text || "", taskId: row.task.id,
                status: row.task.status || "running",
                log: row.task.steps || [], error: row.task.error || "" },
    }));
  });
}

async function runTask(projectId, submitPath, label) {
  const key = `${projectId}:${submitPath}`;
  const active = activeTaskPromises.get(key);
  if (active) {
    status(`${label}已在处理中，复用当前任务…`, true);
    return active;
  }

  const task = (async () => {
    const r = await fetch(`${API}${submitPath}`, { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.status);
    // 提交响应没有 task_id：逐件预检同步判定整批不可执行，直接把它交给调用方
    // 按 blocked 处理 —— 绝不能拿 null 去轮询。
    if (!d.task_id) return d;
    status(`${label}已提交(任务 ${String(d.task_id).slice(0, 6)})，处理中…`, true);
    return pollTask(projectId, d.task_id, label);
  })();
  activeTaskPromises.set(key, task);
  try {
    return await task;
  } finally {
    if (activeTaskPromises.get(key) === task) activeTaskPromises.delete(key);
  }
}

// --------------------------------------------------------------------------- //
// 打开历史项目
// --------------------------------------------------------------------------- //
async function openProject(pid) {
  currentProject = pid;
  currentSelectedId = null;
  diffPick = []; $("diffView").innerHTML = "";
  const data = await fetch(`${API}/api/projects/${pid}`).then(r => r.json());
  if (!data || !data.meta) {  // 项目不存在(可能已删)
    localStorage.removeItem("lastProject");
    throw new Error("项目不存在");
  }
  localStorage.setItem("lastProject", pid);  // 记住,供刷新/返回时恢复
  currentIR = data.ir;
  currentGeometry = data.geometry;
  currentDrawings = data.drawings;
  artifact_status = data.artifact_status || null;

  // 原图区: 图片项目显示原图; 3D 导入项目无 2D 原图,显示占位
  const fname = (data.meta && data.meta.source_filename) || "";
  const isImg = /\.(png|jpe?g|webp|gif|bmp)$/i.test(fname);
  const is3d = /\.(step|stp|iges|igs|stl)$/i.test(fname);
  currentIsImg = isImg;
  // 走哪条链路由 renderDrawingEntry() 一处判定；这里只把结论翻译成界面事实。
  const entry = renderDrawingEntry(fname);
  currentDrawingEntry = entry;
  const blockedReason = (entry === "vision" || entry === "drawing_flow") ? ""
    : entry === "blocked_3d"
      ? `这是 3D 模型导入项目（${fname}）：几何已由原始实体生成，无需也不应再按图纸重建。`
      : `「${fname}」既不是位图也不是 DWG / DXF。2.1 支持 PNG / JPG / WEBP / GIF / BMP（走视觉模型）`
        + "与 DWG / DXF（走服务端图纸解析链路）；其它格式请先转成 PNG / JPG（或另存 DXF）再上传。";
  const img = $("sourceImg");
  const wrap = document.querySelector(".image-wrap");
  const ph = wrap.querySelector(".placeholder");
  if (isImg) {
    img.style.display = "";
    img.src = mediaUrl(`${API}/api/projects/${pid}/source?t=${Date.now()}`);
    if (ph) ph.remove();
  } else {
    img.style.display = "none";
    if (ph) ph.remove();
    // drawing_flow 项目有链路状态可看，不该落进"为什么不能解析"的占位分支；
    // 占位只服务 blocked_3d / blocked_other。
    if (blockedReason) {
      const d = document.createElement("div");
      d.className = "placeholder";
      d.textContent = blockedReason;
      wrap.appendChild(d);
    }
  }
  if (entry === "drawing_flow") loadDrawingFlowPanel();

  // 3D 导入项目: 几何/2D 已由原始实体生成,禁用"基于图/特征重建"的按钮,避免覆盖精确几何
  // 可用性由入口判定给结论：位图走视觉、DWG/DXF 走服务端链路，两者都可点；
  // 3D 与未知格式置灰并写明原因（灰按钮必须自己说明为什么灰）。
  $("btnParse").disabled = !(entry === "vision" || entry === "drawing_flow");
  $("btnParse").title = blockedReason || (entry === "drawing_flow"
    ? "DWG / DXF 图纸：走服务端图纸解析链路（DWG → DXF → CAD IR → 包装语义）"
    : "");
  syncActionSheet(currentIR);
  if (data.ir) renderIR(data.ir);
  loadModelLookup(pid);
  loadVerification(pid);
  updateChatContext();
  renderChat();
  loadVersions();
  const note = data.meta && data.meta.note ? data.meta.note : "";
  const atts = data.meta && data.meta.attachments ? data.meta.attachments.length : 0;
  // 不能解析时，状态栏说的是"为什么不能"，而不是一句无用的"已打开项目"——
  // 这条必须放在最后，前面写了也会被这里覆盖掉。
  status(blockedReason
    || `已打开项目 ${pid}（补充说明${note ? "✓" : "—"}，佐证文件 ${atts} 个）`);
  setWorkflow(data.geometry ? "generate" : data.ir ? "review" : "parse");
}

function avgConfidence(ir) {
  const ps = (ir && ir.parts) || [];
  if (!ps.length) return "—";
  const avg = ps.reduce((s, p) => s + (p.confidence || 0), 0) / ps.length;
  return (avg * 100 | 0) + "%";
}

// --------------------------------------------------------------------------- //
// 渲染拆解树 + 标准件 + 待澄清
// --------------------------------------------------------------------------- //
// 生成几何 / 工程图跑完后，把结果直接显示出来。
// 原来只做了 renderIR（更新零件清单上的状态标记），右侧还停在生成前的样子，
// 用户得再自己点一次零件 —— 而他刚点的就是"生成"，等的就是这个结果。
// selectPart 本身已经负责挂 3D、渲染工程图、切回模型视图，这里只需要选对零件。
function showGeneratedResult() {
  const parts = (currentIR && currentIR.parts) || [];
  if (!parts.length) return;
  const target =
    parts.find(p => p.part_id === currentSelectedId)          // 用户已选的优先
    || parts.find(p => (geomFor(p.part_id) || {}).ok)          // 否则挑第一个成功的
    || parts.find(p => (drawingsFor(p.part_id) || {}).ok)
    || parts[0];
  if (!target) return;
  selectPart(target);
  // 解析 / 生成跑完是一次性时机：该零件若已有工艺推荐就自动展开一次（用户手动点零件
  // 一律留在 3D，见 selectPart）。判定与渲染仍复用既有只读探查与工艺推荐入口。
  autoOpenGeneratedProcess(target);
}

function geomFor(partId) {
  if (!currentGeometry) return null;
  return currentGeometry.parts.find(p => p.part_id === partId);
}

function drawingsFor(partId) {
  if (!currentDrawings) return null;
  return currentDrawings.parts.find(p => p.part_id === partId);
}

function confClass(c) { return c >= 0.75 ? "hi" : c >= 0.5 ? "mid" : "lo"; }

// 由 ir.assemblies + parts.parent_id 构建层级树(含环路保护)
function buildClientTree(ir) {
  const asms = ir.assemblies || [];
  const byId = {};
  asms.forEach(a => { byId[a.assembly_id] = a; });
  const nodes = {};
  asms.forEach(a => {
    nodes[a.assembly_id] = {
      type: "assembly", id: a.assembly_id, name: a.name,
      role: a.role, quantity: a.quantity, children: [],
    };
  });
  const root = { type: "equipment", name: ir.device_name || "设备", children: [] };
  const safeParent = (id, pid) => {
    if (!pid || pid === id || !byId[pid]) return null;
    let cur = pid, chain = new Set([id]);
    while (cur) { if (chain.has(cur)) return null; chain.add(cur); cur = byId[cur] ? byId[cur].parent_id : null; }
    return pid;
  };
  asms.forEach(a => {
    const pid = safeParent(a.assembly_id, a.parent_id);
    (pid ? nodes[pid].children : root.children).push(nodes[a.assembly_id]);
  });
  (ir.parts || []).forEach(p => {
    const pid = (p.parent_id && byId[p.parent_id]) ? p.parent_id : null;
    (pid ? nodes[pid].children : root.children).push({ type: "part", id: p.part_id });
  });
  return root;
}

// 逐件过期标记：数据来自后端 artifact_status（不是整份判过期）。
// 过期件只是带标记，3D / 2D 文件仍在，用户照样能点开对比、再点「重新生成零件」。
function partStaleMark(partId) {
  const status = artifact_status || {};
  const staleParts = (status.geometry_parts_stale || []).concat(status.drawings_parts_stale || []);
  if (staleParts.indexOf(partId) >= 0) return " · 结果已过期";
  if (((status.stale_attributes || {})[partId] || []).length) return " · 质量待重算";
  return "";
}

/** renderNode(node, container, partById)
 *  零件清单缩进：总成一行、零件固定深一级；层级深度不参与计算 —— 同一层的东西必须
 *  看起来在同一层，否则「这个零件属于谁」要靠猜。 */
function renderNode(node, container, partById) {
  // 调用签名固定为 renderNode(node, container, partById)：不再接收 depth，
  // 缩进只分「总成 / 零件」两档，与层级深度无关。
  const ASM_INDENT = 6;    // 总成：一层
  const PART_INDENT = 20;  // 零件：固定比总成深一级
  // 兼容旧调用写法 (node, container, depth, partById)：缩进已与层级解绑，
  // 这里只把真正的 partById 取出来，depth 一律忽略。
  if (typeof partById === "number") partById = arguments[3] || {};
  if (node.type === "assembly") {
    const div = document.createElement("div");
    div.className = "asm";
    div.style.paddingLeft = ASM_INDENT + "px";
    div.innerHTML = `<span class="asm-id">▸ ${esc(node.id)}</span> ${esc(node.name)}` +
      `<span class="asm-meta">${node.role ? esc(node.role) + " · " : ""}总成 ×${node.quantity || 1}</span>`;
    container.appendChild(div);
    (node.children || []).forEach(c => renderNode(c, container, partById));
    return;
  }
  // part
  const p = partById[node.id];
  if (!p) return;
  const g = geomFor(p.part_id);
  const dw = drawingsFor(p.part_id);
  const div = document.createElement("div");
  div.className = "part part-item confirmed";
  div.dataset.partId = p.part_id;
  div.style.marginLeft = PART_INDENT + "px";
  const feats = (p.features || []).map(f => f.type).join(", ");
  // 被逐件预检挡下的零件带结构化 issues：就地标「待补参数」而不是笼统的几何✗，
  // 用户才知道该去补参数而不是点「再次生成」。
  const gstat = g ? (g.ok ? " · 几何✓"
    : (g.issues && g.issues.length ? " · 待补参数" : " · 几何✗")) : "";
  const dstat = dw ? (dw.ok ? " · 2D✓"
    : (dw.issues && dw.issues.length ? " · 2D待补参数" : " · 2D✗")) : "";
  const mark = partStaleMark(p.part_id);
  div.innerHTML =
    `${partThumbnail(p)}<div class="part-info">` +
    `<div class="part-name">${esc(p.part_id)} ${esc(p.name)}` +
    `<span class="part-confidence">${(p.confidence * 100 | 0)}%</span></div>` +
    `<div class="part-type">${esc(feats)} · ${p.material ? esc(p.material.spec) : "材料待确认"} · ${p.quantity}件${gstat}${dstat}${mark}</div>` +
    (p.recommendation ? `<div class="recommend">${esc(p.recommendation)}</div>` : "") +
    `</div>`;
  div.onclick = () => selectPart(p);
  container.appendChild(div);
  container.appendChild(buildPartSubActions(p));
}

// 零件下的子操作：工艺推荐 / 成本测算。它们原先在右侧零件详情里，
// 现在跟着零件一起放在左侧 —— 左侧负责"选什么、看什么结论"，
// 右侧只保留 3D 视图与零件信息。
function buildPartSubActions(part) {
  const PART_INDENT = 20;  // 与零件行同一档：子操作属于它上面那个零件
  const wrap = document.createElement("div");
  wrap.className = "part-subactions";
  wrap.dataset.partActions = part.part_id;
  wrap.hidden = true;
  // 「工艺推荐」属于它上面那个零件：缩进跟着零件走，不能落在总成那一档。
  wrap.style.marginLeft = PART_INDENT + "px";

  const row = document.createElement("div");
  row.className = "part-nav part-subactions-row";   // 复用原右侧按钮的既有样式
  // 2.1 只出工艺，不出成本 —— 成本测算已拆成 2.3，由财务经理做（services/cost_review.py）。
  // 工艺经理在这里交的是工序与用量，成本的数字不该由他给。
  [["process", "工艺推荐", processWrenchNavIcon]]
    .forEach(([mode, label, icon]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `part-subaction btn-${mode}`;
      // 单件入口只把模式写进零件行的 data（data.partAnalysis = mode）：批量入口按模式
      // 判断缺失 / 已成功 / 失败项，不抓取也不模拟点击这里的按钮。
      const data = button.dataset;
      data.partAnalysis = mode;
      button.innerHTML = `${icon}<span>${label}</span>`;
      button.onclick = event => {
        event.stopPropagation();   // 不要触发外层零件行的再次选中
        openPartAnalysis(part, mode);
      };
      row.appendChild(button);
    });

  // 这里原来还挂了一个 inline-analysis-host，分析结果就渲染在零件行下面。
  // 现在结果去右侧工作区了，留着会变成第二个渲染目标 —— 两个宿主迟早对不上。
  wrap.append(row);
  return wrap;
}

// 右侧工作区的两种内容是互斥的：看几何（3D/2D/参数）与看结论（工艺推荐/成本测算）。
// modelTitle 记住切走之前的标题，切回来时不用重新拼一遍零件名。
let modelTitle = "3D 视图 · 选择零件后查看";
function setRightPane(which, title) {
  const analysis = $("analysisPanel");
  const model = $("modelPanes");
  const label = $("viewerPartName");
  if (!analysis || !model) return;
  const showAnalysis = which === "analysis";
  if (!showAnalysis && !analysis.hidden) $("analysisHost").innerHTML = "";
  analysis.hidden = !showAnalysis;
  model.hidden = showAnalysis;
  if (!label) return;
  if (showAnalysis) modelTitle = label.textContent;   // 先存，再改
  label.textContent = showAnalysis ? title : modelTitle;
}

// 工艺推荐 / 成本测算在看板内部是两个子视图：入口统一走视图状态机，渲染仍然复用
// 既有 CadInlineAnalysis，不另写一套工艺 / 成本算法或第二套数据源。
function openPartAnalysis(part, mode) {
  if (!part) return { ok: false, error: { code: "no-part", message: "未选择零件，无法查看工艺或成本。" } };
  const view = PART_VIEW_FOR[mode] || "part-detail";
  if (window.TechBoardPartViews && typeof window.TechBoardPartViews.show === "function") {
    return window.TechBoardPartViews.show(view, { partId: part.part_id });
  }
  return renderPartAnalysis(part, mode);
}

// 真正的渲染：仍然渲染到右侧工作区，仍然复用既有 CadInlineAnalysis。
function renderPartAnalysis(part, mode) {
  const host = $("analysisHost");
  if (!host || !window.CadInlineAnalysis) {
    return { ok: false, error: { code: "no-analysis-host", message: "当前看板没有可用的分析渲染区。" } };
  }
  // 渲染到右侧而不是左侧零件行下面：这两份结论是本步骤的主要产出，
  // 挤在零件清单里既窄又要滚动，而右边正好是一整块工作区。
  const label = mode === "process" ? "工艺推荐" : "成本测算";
  setRightPane("analysis", `${part.part_id} ${part.name || ""} · ${label}`.trim());
  exitBoardViewHost();
  // 右边换了内容，左边得知道。生成任务的每一步由 agent:task-progress 单独播，
  // 这里只负责说清楚"现在右侧在看谁的什么"。
  window.dispatchEvent(new CustomEvent("agent:part-analysis-opened", {
    detail: { partId: part.part_id, partName: part.name || "", label },
  }));
  window.CadInlineAnalysis.open(mode, {
    host,
    projectId: currentProject,
    part,
    status,
    // 关闭后右侧切回 3D 视图，而不是留一块空白。
    onClose: () => setRightPane("model"),
  });
  notePartView(PART_VIEW_FOR[mode] || "part-detail");
  return { ok: true, result: { view: PART_VIEW_FOR[mode] || "part-detail", partId: part.part_id } };
}

// 展开的零件只保留一个：两个零件同时露出「工艺推荐/成本测算」，
// 点哪个、右边显示的是谁的结论，就说不清了。
function togglePartSubActions(partId) {
  document.querySelectorAll("#tree .part-subactions").forEach(node => {
    node.hidden = node.dataset.partActions !== partId;
  });
}

// 根据已经解析出的几何特征显示一个轻量缩略图。它完全由本地 IR 生成，
// 不读取或生成新的图纸，也不会发起模型调用。
function partThumbnail(part) {
  const types = new Set((part.features || []).map(f => f && f.type).filter(Boolean));
  let kind = "generic";
  if (types.has("hole_pattern")) kind = "pattern";
  else if (types.has("cylinder")) kind = "cylinder";
  else if (types.has("box")) kind = "box";
  else if (types.has("plate")) kind = "plate";
  else if (types.has("hole")) kind = "hole";

  const drawings = {
    plate: `<svg viewBox="0 0 32 32" aria-hidden="true"><rect x="6" y="9" width="20" height="14" rx="2"/><path d="M9 13h14M9 19h14"/></svg>`,
    box: `<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M7 11 16 6l9 5v11l-9 5-9-5z"/><path d="M7 11l9 5 9-5M16 16v11"/></svg>`,
    cylinder: `<svg viewBox="0 0 32 32" aria-hidden="true"><ellipse cx="16" cy="9" rx="8" ry="3.5"/><path d="M8 9v13c0 2 16 2 16 0V9"/><path d="M8 22c0 2 16 2 16 0"/></svg>`,
    pattern: `<svg viewBox="0 0 32 32" aria-hidden="true"><rect x="5" y="6" width="22" height="20" rx="3"/><circle cx="11" cy="12" r="1.7"/><circle cx="21" cy="12" r="1.7"/><circle cx="11" cy="20" r="1.7"/><circle cx="21" cy="20" r="1.7"/></svg>`,
    hole: `<svg viewBox="0 0 32 32" aria-hidden="true"><rect x="5" y="8" width="22" height="16" rx="3"/><circle cx="16" cy="16" r="4"/></svg>`,
    generic: `<svg viewBox="0 0 32 32" aria-hidden="true"><path d="m16 5 9 5v12l-9 5-9-5V10z"/><path d="m7 10 9 5 9-5M16 15v12"/></svg>`,
  };
  const labels = { plate: "板件", box: "方体件", cylinder: "圆柱件", pattern: "孔阵列件", hole: "带孔件", generic: "零件" };
  return `<div class="part-icon part-icon-${kind}" title="${labels[kind]}" aria-label="${labels[kind]}">${drawings[kind]}</div>`;
}

// 在原图上叠加各零件的 bbox(provenance.bbox 归一化 [x,y,w,h]),按置信度着色
function renderBboxes(ir) {
  const layer = $("bboxLayer");
  if (!layer) return;
  layer.innerHTML = "";
  if (!currentIsImg) return;
  (ir.parts || []).forEach(p => {
    const bb = p.provenance && p.provenance.bbox;
    if (!bb || bb.length < 4) return;
    const [x, y, w, h] = bb;
    const box = document.createElement("div");
    box.className = "bbox " + confClass(p.confidence);
    box.dataset.partId = p.part_id;
    box.style.left = (x * 100) + "%";
    box.style.top = (y * 100) + "%";
    box.style.width = (w * 100) + "%";
    box.style.height = (h * 100) + "%";
    box.innerHTML = `<span class="tag">${esc(p.part_id)} ${(p.confidence * 100 | 0)}%</span>`;
    box.onclick = () => selectPart(p);
    if (p.part_id === currentSelectedId) box.classList.add("active");
    layer.appendChild(box);
  });
}

// 零件清单树：只重建 #tree 内部（2.1 固定左栏），不碰右栏 3D 画布。
function renderTree(ir) {
  const tree = $("tree");
  if (!tree) return;
  tree.innerHTML = "";
  tree.classList.toggle("empty-state", !(ir.parts || []).length);
  const partById = {};
  (ir.parts || []).forEach(p => { partById[p.part_id] = p; });
  const root = buildClientTree(ir);
  root.children.forEach(c => renderNode(c, tree, partById));
}

function renderIR(ir) {
  $("deviceName").textContent = ir.device_name || "当前解析任务";
  $("intent").innerHTML =
    `<b>设计意图:</b> ${esc(ir.design_intent || "")}<br>` +
    (ir.overall_dims ? `<b>总体尺寸:</b> ${esc(ir.overall_dims)}<br>` : "") +
    (ir.assembly_notes ? `<b>装配:</b> ${esc(ir.assembly_notes)}` : "");
  $("partsMetric").textContent = `${(ir.parts || []).length}`;
  $("confidenceMetric").textContent = avgConfidence(ir);

  renderTree(ir);

  // 标准件 + 待澄清：确认说明只在本地保存，不会触发新的 AI 调用。
  renderClarifications(ir);

  renderBboxes(ir);
  // 重渲染后保持选中态(高亮 tree/box)
  if (currentSelectedId) {
    const sel = (ir.parts || []).find(p => p.part_id === currentSelectedId);
    if (sel) {
      markSelection(currentSelectedId);
      // 树是整棵重建的，选中零件的子按钮要跟着重新展开。
      togglePartSubActions(currentSelectedId);
    }
  }
  loadVersions();
  // 零件清单/待澄清已重新渲染：先把解析摘要播给父壳看板桥，再通知 Agent 对话框刷新
  // 结果按钮的数量。左侧入口的显示 / 计数只认看板播回的解析摘要，不读本页 DOM。
  // 重渲染只重建清单行，不动右栏 3D 画布；选中态在上面已按 part_id 恢复。
  publishResultSummary(ir);
  // 重新打开项目时按当前零件表只读探一次（失败不影响渲染）。
  probePartsProcessState();
  window.dispatchEvent(new CustomEvent("agent:ir-rendered"));
}

// --------------------------------------------------------------------------- //
// 版本与校核审签(PRD 6.5): 每次保存 IR 自动留版,可对比/送审/通过/驳回/恢复
// --------------------------------------------------------------------------- //
const STATUS_LABEL = { draft: "草稿", in_review: "送审中", approved: "已通过", rejected: "已驳回" };
const VERSION_STAGE_LABEL = {
  parsed: "AI 图纸解析完成", parsed_3d: "导入 3D 模型解析", verified: "AI 校验修正完成",
  decomposed: "AI 拆解推荐已同步", edited: "人工编辑零件参数", ai_chat_edited: "AI 对话修改零件参数",
  model_lookup_applied: "联网型号核验同步清单", restored: "恢复历史版本",
};

async function loadVersions() {
  if (!currentProject) { $("versions").innerHTML = ""; return; }
  let data;
  try {
    data = await fetch(`${API}/api/projects/${currentProject}/versions`).then(r => r.json());
  } catch { return; }
  renderVersions(data.versions || []);
}

function renderVersions(versions) {
  const box = $("versions");
  box.innerHTML = "";
  if (!versions.length) {
    box.innerHTML = `<div class="versions-empty">暂无版本（解析/编辑后自动留版）</div>`;
    return;
  }
  versions.slice().reverse().forEach(v => {  // 最新在上
    const div = document.createElement("div");
    div.className = "ver" + (diffPick.includes(v.version) ? " active" : "");
    const picked = diffPick.includes(v.version);
    const conf = v.avg_confidence != null ? ` · 置信${(v.avg_confidence * 100 | 0)}%` : "";
    const changeSummary = v.change_summary || "暂无变更说明";
    const last = (v.review || []).slice(-1)[0];
    const reviewNote = last
      ? `<div class="vmeta">审签: ${esc(last.actor)} · ${STATUS_LABEL[last.status] || last.status}` +
        `${last.comment ? " · " + esc(last.comment) : ""}</div>`
      : "";
    div.innerHTML =
      `<div class="ver-top"><span class="vn">v${v.version}</span>` +
        `<span class="st ${v.status}">${STATUS_LABEL[v.status] || v.status}</span>` +
        `<span class="vstage">${esc(VERSION_STAGE_LABEL[v.stage] || v.stage)}</span></div>` +
      `<div class="vmeta">${esc(v.ts)} · ${esc(v.author)} · ${v.parts}零件${conf}</div>` +
      `<button class="ver-summary" type="button" aria-expanded="false">` +
        `<span>相对上一版本：${esc(changeSummary)}</span><span class="ver-toggle">展开</span></button>` +
      `<div class="ver-details" hidden>` +
        (v.note ? `<div class="vmeta">本次操作：${esc(v.note)}</div>` : "") +
        reviewNote + `</div>` +
        `<div class="vacts">` +
        `<button class="pick ${picked ? "on" : ""}" data-act="pick" data-v="${v.version}">` +
          `${picked ? "✓对比" : "选作对比"}</button>` +
        (v.status === "draft" || v.status === "rejected"
          ? `<button data-act="submit" data-v="${v.version}">送审</button>` : "") +
        (v.status === "in_review"
          ? `<button class="ok" data-act="approve" data-v="${v.version}">通过</button>` +
            `<button class="no" data-act="reject" data-v="${v.version}">驳回</button>` : "") +
        `<button class="rs" data-act="restore" data-v="${v.version}">恢复</button>` +
        `</div>`;
    box.appendChild(div);
  });
  box.querySelectorAll(".ver-summary").forEach(summary => {
    summary.onclick = () => {
      const details = summary.nextElementSibling;
      const expanded = !details.hidden;
      details.hidden = expanded;
      summary.setAttribute("aria-expanded", String(!expanded));
      summary.querySelector(".ver-toggle").textContent = expanded ? "展开" : "收起";
      summary.closest(".ver").classList.toggle("expanded", !expanded);
    };
  });
  box.querySelectorAll("button[data-act]").forEach(b => {
    b.onclick = () => versionAction(b.dataset.act, +b.dataset.v);
  });
}

async function versionAction(act, v) {
  if (act === "pick") return togglePick(v);
  if (act === "restore") {
    if (!confirm(`恢复 v${v} 为当前 IR?(会另存为新版本,不覆盖历史)`)) return;
    status(`正在恢复 v${v} ...`, true);
    const r = await fetch(`${API}/api/projects/${currentProject}/versions/${v}/restore`, { method: "POST" });
    const ir = await r.json();
    if (!r.ok) { status("恢复失败: " + (ir.detail || r.status)); return; }
    currentIR = ir; renderIR(ir);
    status(`已恢复 v${v}(已另存为新版本)`);
    return;
  }
  let actor = "", comment = "";
  if (act === "approve" || act === "reject") {
    actor = prompt("审签人 姓名/工号:", "") || "";
    comment = prompt("审签意见(可选):", "") || "";
  }
  const r = await fetch(`${API}/api/projects/${currentProject}/versions/${v}/${act}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor, comment }),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    status("操作失败: " + (e.detail || r.status));
    return;
  }
  status(`v${v} ${{ submit: "已送审", approve: "已通过", reject: "已驳回" }[act]}`);
  loadVersions();
}

function togglePick(v) {
  const i = diffPick.indexOf(v);
  if (i >= 0) diffPick.splice(i, 1);
  else { diffPick.push(v); if (diffPick.length > 2) diffPick.shift(); }
  loadVersions();
  if (diffPick.length === 2) showDiff(diffPick[0], diffPick[1]);
  else $("diffView").innerHTML = diffPick.length === 1
    ? `<div class="none">已选 v${diffPick[0]}，再选一个版本进行对比</div>` : "";
}

async function showDiff(a, b) {
  const [lo, hi] = a < b ? [a, b] : [b, a];
  try {
    const r = await fetch(`${API}/api/projects/${currentProject}/versions/${lo}/diff/${hi}`).then(r => r.json());
    renderDiff(r.from, r.to, r.diff);
  } catch (e) { $("diffView").innerHTML = `<div class="none">对比失败</div>`; }
}

function renderDiff(from, to, d) {
  const el = $("diffView");
  let html = `<h4>v${from} → v${to} 差异(共 ${d.total_changes} 处)</h4>`;
  if (!d.total_changes) { el.innerHTML = html + `<div class="none">两版无差异</div>`; return; }
  (d.header || []).forEach(c => {
    html += `<div class="chg"><span class="f">${esc(c.field)}</span>: ` +
      `<span class="o">${esc(c.old)}</span> → <span class="n">${esc(c.new)}</span></div>`;
  });
  const P = d.parts || {};
  (P.added || []).forEach(p =>
    html += `<div class="chg"><span class="add">+ 新增 ${esc(p.part_id)} ${esc(p.name || "")}</span></div>`);
  (P.removed || []).forEach(p =>
    html += `<div class="chg"><span class="del">− 删除 ${esc(p.part_id)} ${esc(p.name || "")}</span></div>`);
  (P.modified || []).forEach(m => {
    html += `<div class="chg"><span class="f">${esc(m.part_id)} ${esc(m.name || "")}</span>`;
    m.changes.forEach(c => {
      html += `<div>· ${esc(c.field)}: <span class="o">${esc(c.old)}</span> → ` +
        `<span class="n">${esc(c.new)}</span></div>`;
    });
    html += `</div>`;
  });
  const S = d.standard_parts || {};
  (S.added || []).forEach(item =>
    html += `<div class="chg"><span class="add">+ 新增 BOM / 外购件 ${esc(item.spec || item.model_no || "")}</span></div>`);
  (S.removed || []).forEach(item =>
    html += `<div class="chg"><span class="del">− 删除 BOM / 外购件 ${esc(item.spec || item.model_no || "")}</span></div>`);
  (S.modified || []).forEach(item => {
    html += `<div class="chg"><span class="f">BOM / 外购件 ${esc(item.spec || item.key || "")}</span>`;
    item.changes.forEach(c => {
      html += `<div>· ${esc(c.field)}: <span class="o">${esc(c.old)}</span> → ` +
        `<span class="n">${esc(c.new)}</span></div>`;
    });
    html += `</div>`;
  });
  el.innerHTML = html;
}

// --------------------------------------------------------------------------- //
// 选中零件: 详情 + 3D
// --------------------------------------------------------------------------- //
function markSelection(partId) {
  document.querySelectorAll("#tree .part").forEach(d =>
    d.classList.toggle("active", d.dataset.partId === partId));
  document.querySelectorAll("#bboxLayer .bbox").forEach(b =>
    b.classList.toggle("active", b.dataset.partId === partId));
}

// 可编辑的数值字段(按特征类型)
const FEAT_FIELDS = {
  plate: ["length", "width", "thickness"],
  box: ["length", "width", "height"],
  cylinder: ["diameter", "height"],
  hole: ["diameter", "x", "y"],
  hole_pattern: ["diameter", "count_x", "count_y", "spacing_x", "spacing_y"],
  fillet: ["radius"],
  chamfer: ["distance"],
};

// 空特征零件只能在这三种简化外形里选一个 —— 唯一的基体白名单，键集与后端
// part_edit.BASE_FEATURE_FIELDS 一致。只给固定模板字段，不给「自己加特征 /
// 自己加字段」的入口：基体之外的类型（hole / fillet / chamfer…）一律不出现。
const BASE_FEATURE_TYPES = ["plate", "box", "cylinder"];
const BASE_FEATURE_LABELS = { plate: "板件", box: "长方体", cylinder: "圆柱体" };

// 基体模板的尺寸输入：字段取自该类型的固定模板，默认全空 —— 空即待补，不猜尺寸。
function baseFeatureDimsHtml(type) {
  return (FEAT_FIELDS[type] || [])
    .map(k => `<label>${k}</label><input data-base-dim="${k}" type="number" value=""/>`)
    .join("");
}

// features 为空时的受控基体编辑器：类型三选一 + 该类型固定的尺寸字段。
function baseFeatureEditorHtml() {
  return `<div class="feat-edit parameter-base-feature">` +
    `<div class="feat-type">该零件当前没有可建模特征。请选择简化外形并填写该类型的` +
    `全部尺寸；留空即待补，可由人工或 Agent 后补。</div>` +
    `<div class="edit-grid"><label>简化外形</label><select data-base-type>` +
    BASE_FEATURE_TYPES.map(t => `<option value="${t}">${BASE_FEATURE_LABELS[t]}</option>`).join("") +
    `</select></div>` +
    `<div class="edit-grid" data-base-dims>` +
    baseFeatureDimsHtml(BASE_FEATURE_TYPES[0]) + `</div></div>`;
}

function selectPart(part) {
  if (!part) return;
  window.CadInlineAnalysis?.reset();
  // 换了零件就切回 3D —— 否则右边还留着上一个零件的工艺推荐，标题却换成了新零件。
  setRightPane("model");
  // 零件详情是看板内部视图 part-detail：退出看板内容宿主，回到 3D / 零件信息 / 参数。
  exitBoardViewHost();
  currentSelectedId = part.part_id;
  markSelection(part.part_id);
  togglePartSubActions(part.part_id);
  const g = geomFor(part.part_id);
  const dw = drawingsFor(part.part_id);
  $("viewerPartName").textContent = `${part.part_id} ${part.name}`;
  updateChatContext(part);

  let summaryHtml = "";
  if (part.model_no || part.manufacturer || part.model_specification) {
    summaryHtml += `<div class="model-identity"><b>联网型号核验</b>` +
      `${part.model_no ? `<div>型号：${esc(part.model_no)}</div>` : ""}` +
      `${part.manufacturer ? `<div>制造商：${esc(part.manufacturer)}</div>` : ""}` +
      `${part.model_specification ? `<div>公开规格：${esc(part.model_specification)}</div>` : ""}` +
      `${part.model_lookup_evidence ? `<div>核验依据：${esc(part.model_lookup_evidence)}</div>` : ""}` +
      `</div>`;
  }
  let parameterHtml = "";

  // 行内可编辑特征(仅"图→IR"项目;3D 导入项目几何来自真实实体,不改参重生)
  if (currentIsImg) {
    parameterHtml += `<div class="parameter-columns"><div class="parameter-base"><div class="edit-grid">` +
      `<label>名称</label><input data-pf="name" value="${esc(part.name || "")}"/>` +
      `<label>数量</label><input data-pf="quantity" type="number" value="${part.quantity || 1}"/>` +
      `<label>材料</label><input data-pf="material" value="${esc(part.material ? part.material.spec : "")}"/>` +
      `</div></div><div class="parameter-features">`;
    if (!part.features || !part.features.length) {
      // 没有可建模特征：不摆空白参数区，给受控的三种基体模板（留空即待补）。
      parameterHtml += baseFeatureEditorHtml();
    } else {
      (part.features || []).forEach((f, fi) => {
        const fields = FEAT_FIELDS[f.type] || [];
        parameterHtml += `<div class="feat-edit"><div class="feat-type">特征 #${fi + 1}: ${f.type}` +
          `${f.purpose ? " · " + esc(f.purpose) : ""}</div><div class="edit-grid">`;
        fields.forEach(k => {
          const v = f[k];
          parameterHtml += `<label>${k}</label><input data-fi="${fi}" data-fk="${k}" type="number" ` +
            `value="${v != null ? v : ""}"/>`;
        });
        parameterHtml += `</div></div>`;
      });
    }
    parameterHtml += `</div></div><div class="parameter-actions"><button class="btn-regen" id="btnSaveParams" type="button">保存零件参数</button><button class="btn-regen" id="btnRegen" type="button">重新生成零件</button></div>`;
  } else {
    parameterHtml += "<table>";
    (part.features || []).forEach(f => {
      const dims = Object.entries(f).filter(([k, v]) =>
        v != null && !["type", "purpose"].includes(k)).map(([k, v]) => `${k}=${v}`).join(", ");
      parameterHtml += `<tr><td>${f.type}</td><td>${dims}${f.purpose ? " · " + esc(f.purpose) : ""}</td></tr>`;
    });
    parameterHtml += "</table>";
  }

  const downloadLinks = [];
  if (g) {
    if (g.bbox) summaryHtml += `<div>包围盒: ${g.bbox.join(" × ")} mm</div>`;
    if (g.volume_mm3) summaryHtml += `<div>体积: ${g.volume_mm3} mm³</div>`;
    if (g.mass_g) summaryHtml += `<div>质量: ${g.mass_g} g</div>`;
    (g.warnings || []).forEach(w => summaryHtml += `<div class="warn">⚠ ${esc(w)}</div>`);
    if (g.error) summaryHtml += `<div class="err">✗ ${esc(g.error)}</div>`;
    if (g.ok) {
      downloadLinks.push(`<a href="${mediaUrl(API + g.step_url)}" download>下载 STEP</a>`);
      downloadLinks.push(`<a href="${mediaUrl(API + g.stl_url)}" download>下载 STL</a>`);
      loadSTL(mediaUrl(`${API}${g.stl_url}`));
    }
  } else {
    summaryHtml += `<div class="warn">尚未生成几何，请使用底部的「生成 CAD 几何」。</div>`;
    clearViewer();
  }

  if (dw && dw.dxf_url) {
    downloadLinks.push(`<a href="${mediaUrl(API + dw.dxf_url)}" download>下载 DXF(下料图)</a>`);
  }

  // 2D 工程图(三视图 + 等轴测 SVG + 下料 DXF)
  let lowerHtml = "";
  if (dw && dw.ok) {
    const labels = { front: "主视图", top: "俯视图", right: "侧视图", iso: "等轴测" };
    lowerHtml += `<h4 class="dwh">2D 工程图</h4><div class="views">`;
    ["front", "top", "right", "iso"].forEach(v => {
      if (dw.views[v]) {
        lowerHtml += `<figure class="view"><img src="${mediaUrl(API + dw.views[v])}" alt="${v}"/>` +
                `<figcaption>${labels[v]}</figcaption></figure>`;
      }
    });
    lowerHtml += `</div>`;
    (dw.warnings || []).forEach(w => lowerHtml += `<div class="warn">⚠ ${esc(w)}</div>`);
  }
  if (downloadLinks.length) lowerHtml += `<div class="dl download-actions">${downloadLinks.join("")}</div>`;

  // 工艺推荐与成本测算已移到左侧零件清单的子按钮下；右侧只保留 3D 与零件信息。
  // 版本面板不再内嵌进零件详情 —— 它改由「更多功能 ▾ → 版本与校核」的弹卡片承载，
  // 节点本体留在抽屉里，只换呈现位置、不复制。
  const html = `<div class="part-summary">${summaryHtml}</div>` +
    `<div id="inlineAnalysisHost" class="inline-analysis-host">${lowerHtml}</div>`;

  $("partDetail").innerHTML = html;
  $("parameterEditor").innerHTML = parameterHtml || "此零件暂无可编辑参数。";
  // 换简化外形就按新模板重渲染尺寸输入（默认全空）—— 不允许上一类型的字段残留。
  const baseTypeSel = document.querySelector("#parameterEditor [data-base-type]");
  if (baseTypeSel) {
    const baseDims = document.querySelector("#parameterEditor [data-base-dims]");
    baseTypeSel.onchange = () => {
      if (baseDims) baseDims.innerHTML = baseFeatureDimsHtml(baseTypeSel.value);
    };
  }
  // 详情内要能直接进「工艺推荐」：复用零件清单同一份子动作构造，不另写按钮逻辑。
  const detailActions = buildPartSubActions(part);
  detailActions.hidden = false;
  $("partDetail").append(detailActions);
  const sb = document.getElementById("btnSaveParams");
  if (sb) sb.onclick = () => savePartEdits(part.part_id, false);
  const rb = document.getElementById("btnRegen");
  if (rb) rb.onclick = () => savePartEdits(part.part_id, true);
  // 选中零件 = 看板内部进入 part-detail；只上报视图，不触发父壳导航。
  notePartView("part-detail");
  // 点零件就停在 3D：不看这个零件有没有工艺推荐，也不替用户切结论 —— 进工艺推荐
  // 只有零件行下的「工艺推荐」子按钮一个入口（openPartAnalysis）。已生成即自动展开
  // 挪到解析 / 生成完成的时机（showGeneratedResult / 批量收尾），不再绑在选中零件上。
}

// 读取行内编辑 -> 更新 IR -> 保存；按需继续单零件重生并刷新
async function savePartEdits(partId, regenerate = false) {
  if (!currentProject || !currentIR) return;
  const part = (currentIR.parts || []).find(p => p.part_id === partId);
  if (!part) return;
  const detail = $("parameterEditor");

  // 零件级字段
  detail.querySelectorAll("[data-pf]").forEach(inp => {
    const k = inp.dataset.pf;
    if (k === "name") part.name = inp.value;
    else if (k === "quantity") part.quantity = parseInt(inp.value) || 1;
    else if (k === "material") {
      const spec = inp.value.trim();
      part.material = spec ? { spec, density: part.material ? part.material.density : null } : null;
    }
  });
  // 基体模板（空特征零件）：只认模板里的字段，留空写 null 而不是 0 / 默认值，
  // 写进 features[0]（features 为空时先补成 [基体]）。
  const baseTypeSel = detail.querySelector("[data-base-type]");
  if (baseTypeSel) {
    const baseObj = { type: baseTypeSel.value };
    detail.querySelectorAll("[data-base-dim]").forEach(inp => {
      const raw = inp.value.trim();
      baseObj[inp.dataset.baseDim] = raw === "" ? null : parseFloat(raw);
    });
    part.features = part.features || [];
    part.features[0] = baseObj;
  }

  // 特征数值
  detail.querySelectorAll("[data-fi]").forEach(inp => {
    const fi = +inp.dataset.fi, fk = inp.dataset.fk;
    if (!part.features[fi]) return;
    const raw = inp.value.trim();
    part.features[fi][fk] = raw === "" ? null : (Number.isInteger(+raw) && fk.startsWith("count") ? parseInt(raw) : parseFloat(raw));
  });

  status(regenerate ? `正在保存参数并重生 ${partId} ...` : `正在保存 ${partId} 的零件参数 ...`, true);
  try {
    await fetch(`${API}/api/projects/${currentProject}/ir`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentIR),
    }).then(r => { if (!r.ok) throw new Error("保存IR失败"); });

    if (!regenerate) {
      status(`${partId} 零件参数已保存`);
      return;
    }

    const res = await fetch(`${API}/api/projects/${currentProject}/parts/${partId}/regenerate`,
      { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || res.status);

    // 更新本地几何/2D 结果中的该零件条目
    upsertLocal(currentGeometry, data.geometry);
    upsertLocal(currentDrawings, data.drawings);
    renderIR(currentIR);
    selectPart(part);
    const g = data.geometry;
    status(g.ok ? `${partId} 已重生(体积 ${g.volume_mm3} mm³)` : `${partId} 重生失败: ${g.error || ""}`);
  } catch (e) { status("重生失败: " + e.message); }
}

function upsertLocal(coll, entry) {
  if (!coll || !entry) return;
  coll.parts = coll.parts || [];
  const i = coll.parts.findIndex(p => p.part_id === entry.part_id);
  if (i >= 0) coll.parts[i] = entry; else coll.parts.push(entry);
}

// --------------------------------------------------------------------------- //
// three.js STL 查看器
// --------------------------------------------------------------------------- //
let scene, camera, renderer, controls, mesh;

function initViewer() {
  const el = $("viewer");
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0xf8fafc);
  camera = new THREE.PerspectiveCamera(45, el.clientWidth / el.clientHeight, 0.1, 100000);
  camera.position.set(120, 120, 120);
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(el.clientWidth, el.clientHeight);
  el.appendChild(renderer.domElement);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.06;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 4;

  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const dir = new THREE.DirectionalLight(0xffffff, 0.8);
  dir.position.set(1, 1, 1);
  scene.add(dir);
  scene.add(new THREE.AxesHelper(50));

  (function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  })();

  const resizeToBox = () => {
    if (!renderer || !camera || !el.clientWidth || !el.clientHeight) return;
    camera.aspect = el.clientWidth / el.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(el.clientWidth, el.clientHeight);
  };
  window.addEventListener("resize", resizeToBox);
  // 2.1 改成固定两栏后，右栏宽度会随窗口 / 布局变化，单独观察右栏避免 canvas 停在旧尺寸。
  if (typeof ResizeObserver === "function") {
    const column = document.querySelector(".drawing-model-column") || el;
    new ResizeObserver(resizeToBox).observe(column);
  }
}

function clearViewer() {
  if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); mesh = null; }
}

function loadSTL(url) {
  // 没有 scene 就不要再往下走：回调里 scene.add 会抛在异步栈里，只留一条谁也看不见的
  // 报错，而 STEP/STL 下载入口本身仍然可用。
  if (viewerBroken) return;
  clearViewer();
  new STLLoader().load(url, (geo) => {
    geo.computeVertexNormals();
    geo.center();
    const mat = new THREE.MeshStandardMaterial({ color: 0x6fa8dc, metalness: 0.3, roughness: 0.6 });
    mesh = new THREE.Mesh(geo, mat);
    scene.add(mesh);
    // 自动取景
    geo.computeBoundingSphere();
    const r = geo.boundingSphere.radius || 50;
    camera.position.set(r * 2, r * 2, r * 2);
    controls.target.set(0, 0, 0);
    controls.update();
  });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

// init() 里任何一处没预料到的异常，原来只会进控制台 —— 而用户看到的是一个功能齐全、
// 按钮却全是灰的页面，无从判断发生了什么。兜底把原因写到状态栏。
init().catch(error => {
  status(`页面初始化失败：${error && error.message || error}。请刷新重试；若持续出现，`
         + "请把这句话反馈给维护者。");
});

/* 2.1 零件批量工艺推荐（一键生成全部工艺推荐）
 * 复用既有单零件接口 POST /api/projects/{project_id}/parts/{part_id}/process 与既有任务
 * 查询链路 pollTask：前端受控串行（for + await），绝不一次并发全部模型调用；已有成功
 * 工艺的零件默认 skip，不覆盖人工编辑后的工艺，也不再产生一次模型费用；单件失败记录
 * 真实原因后继续下一件，最后统一汇总成功 / 失败 / 跳过数并刷新看板。 */
let allPartsProcessBusy = false;

function allPartsProcessPublish(state) {
  const runtime = window.TechBoardRuntime;
  if (!runtime || typeof runtime.publish !== "function") return;
  // 会话只给「有真实执行明细」的任务出卡：这里把逐件进度累积成 log，和 progress 一起上报，
  // 否则批量推荐只剩一句通用文本，会被父壳的降噪闸门挡掉、看不到跑到第几件。
  const line = state.running
    ? `正在生成工艺推荐 ${Math.min(state.done + 1, state.total)}/${state.total}：${state.running}`
    : "";
  if (!Array.isArray(state.log)) state.log = [];
  if (line && state.log[state.log.length - 1] !== line) state.log.push(line);
  runtime.publish("task-progress", "runAllPartProcesses", {
    action: "runAllPartProcesses",
    total: state.total,
    done: state.done,
    succeeded: state.succeeded,
    failed: state.failed,
    skipped: state.skipped,
    progress: line,
    log: state.log.slice(),
  });
}

function allPartsProcessSettle(event, payload) {
  const runtime = window.TechBoardRuntime;
  if (!runtime || typeof runtime.publish !== "function") return;
  runtime.publish(event, "runAllPartProcesses",
    Object.assign({ action: "runAllPartProcesses" }, payload || {}));
}

/* 2.1 主按钮三段式的同步判定：哪些零件已经有工艺推荐。
   getState() 只能同步读，所以这里只读本地缓存，不发请求；真实探测走 probePartsProcessState。 */
const processReadyParts = new Set();
function partProcessKey(projectId, partId) {
  return `${projectId || ""}:${partId || ""}`;
}
function markPartProcessReady(projectId, partId) {
  if (!projectId || !partId) return;
  processReadyParts.add(partProcessKey(projectId, partId));
}
function partsProcessComplete() {
  if (!drawingParsed()) return false;
  const parts = (currentIR && currentIR.parts) || [];
  if (!parts.length) return false;
  return parts.every((part) => processReadyParts.has(partProcessKey(currentProject, part.part_id)));
}

/* 结论变化后主动把新快照推给父壳：主按钮要自己翻面，不能等用户再点一次某个按钮。 */
function refreshBoardActionState() {
  const runtime = window.TechBoardRuntime;
  if (runtime && typeof runtime.refreshState === "function") runtime.refreshState();
}

/* 首次进入已有 IR 的项目：按当前零件表各只读探一次，复用 partHasExistingProcess；
   同一份零件表只探一次，并发只放一个在跑（单飞）。 */
let processProbeKey = "";
let processProbeBusy = false;
async function probePartsProcessState() {
  const projectId = currentProject;
  const parts = ((currentIR && currentIR.parts) || []).slice();
  if (!projectId || !parts.length) return;
  const key = projectId + "|" + parts.map((part) => part.part_id).join(",");
  if (processProbeBusy || key === processProbeKey) return;
  processProbeBusy = true;
  processProbeKey = key;
  let changed = false;
  try {
    for (const part of parts) {
      if (processReadyParts.has(partProcessKey(projectId, part.part_id))) continue;
      const ready = await partHasExistingProcess(projectId, part.part_id);
      if (ready) {
        markPartProcessReady(projectId, part.part_id);
        changed = true;
      }
    }
  } finally {
    processProbeBusy = false;
  }
  if (changed) refreshBoardActionState();
}

// 已有工艺判定：读既有 GET .../parts/{part_id}/process，库里已有工序就 skip，避免重复
// 计费、避免覆盖人工编辑后的工艺路线。
async function partHasExistingProcess(projectId, partId) {
  try {
    const data = await fetch(
      `${API}/api/projects/${encodeURIComponent(projectId)}/parts/${encodeURIComponent(partId)}/process`,
    ).then((response) => (response.ok ? response.json() : null));
    return Boolean(data && data.plan && (data.plan.steps || []).length);
  } catch (error) {
    return false;
  }
}

// 已生成工艺推荐 → 选中零件即自动展开（2.1 看板内部切到 part-process）。
// 判定完全复用上面的只读 GET，渲染复用既有 openPartAnalysis()：不发新请求、不触发生成、
// 不新建面板；同一零件只自动展开一次，之后用户仍可用「工艺推荐」按钮自由切换。
const autoOpenedProcessParts = new Set();
function autoOpenGeneratedProcess(part) {
  if (!part || !currentProject) return;
  if (autoOpenedProcessParts.has(part.part_id)) return;
  return partHasExistingProcess(currentProject, part.part_id).then((ready) => {
    if (!ready) return;
    markPartProcessReady(currentProject, part.part_id);
    const outcome = openPartAnalysis(part, "process");
    if (!outcome || outcome.ok !== false) autoOpenedProcessParts.add(part.part_id);
  }).catch(() => { /* 读取失败就静默回落 3D，不影响选中零件的既有行为 */ });
}

// 单件：只调用既有单零件接口拿 task_id，再复用既有 pollTask 等到这一件真正算完。
async function runOnePartProcess(projectId, part) {
  const response = await fetch(
    `${API}/api/projects/${encodeURIComponent(projectId)}/parts/${encodeURIComponent(part.part_id)}/process`,
    { method: "POST", body: new FormData() },
  );
  const submitted = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(submitted.detail || `HTTP ${response.status}`);
  if (!submitted.task_id) throw new Error("工艺推荐任务提交失败：没有返回 task_id");
  return pollTask(projectId, submitted.task_id, "工艺推荐");
}

// 批量执行：受控串行跑完全部待处理零件，逐件上报进度，部分失败 continue 下一件。
async function runAllPartProcesses(options = {}) {
  const projectId = currentProject;
  if (!projectId) throw new Error("还没有选择项目，无法生成工艺推荐。");
  const parts = ((currentIR && currentIR.parts) || []).slice();
  if (!parts.length) throw new Error("还没有零件，请先完成图纸解析再生成工艺推荐。");
  const force = Boolean(options.force);
  const state = { total: parts.length, done: 0, running: "", succeeded: 0, failed: 0, skipped: 0 };
  const failures = [];
  for (const part of parts) {
    const name = `${part.part_id} ${part.name || ""}`.trim();
    if (!force && await partHasExistingProcess(projectId, part.part_id)) {
      markPartProcessReady(projectId, part.part_id);
      state.skipped += 1;
      state.done += 1;
      allPartsProcessPublish(state);
      continue;
    }
    state.running = name;
    allPartsProcessPublish(state);
    status(`正在生成工艺推荐 ${state.done + 1}/${state.total}：${name}`, true);
    try {
      await runOnePartProcess(projectId, part);
      markPartProcessReady(projectId, part.part_id);
      state.succeeded += 1;
    } catch (error) {
      state.failed += 1;
      failures.push({
        part_id: part.part_id,
        name: name,
        message: (error && error.message) || "工艺推荐失败",
      });
      state.done += 1;
      state.running = "";
      allPartsProcessPublish(state);
      continue;
    }
    state.done += 1;
    state.running = "";
    allPartsProcessPublish(state);
  }
  // 记住这一轮的失败清单：看板「仅重试失败项」据此只补跑失败的零件，不重算已成功件。
  // （globalThis：页面是模块作用域，桥 / 测试驱动与它共用同一份记忆。）
  globalThis.__techPartProcessFailures = failures.map((item) => ({
    part_id: item.part_id, name: item.name, message: item.message,
  }));
  // 全部零件都已有工艺推荐后，主按钮自己从「一键生成全部工艺推荐」翻成「确认解析结果」。
  refreshBoardActionState();
  return { state: state, failures: failures };
}

// 完成后刷新看板：复用既有 IR 渲染与解析摘要通道，不新增数据源，也不必让用户逐个点开
// 零件才知道批量任务已经完成。
function requestBoardSummary() {
  if (currentIR) renderIR(currentIR);
  else publishResultSummary(currentIR);
  return { ok: true };
}

async function runAllPartProcessesInBackground(options) {
  try {
    const result = await runAllPartProcesses(options);
    requestBoardSummary();
    const state = result.state;
    const summary = `全部工艺推荐已执行：成功 ${state.succeeded}、失败 ${state.failed}、跳过 ${state.skipped}`;
    if (result.failures.length) {
      // 有成功件就是「部分完成」，不是整批失败：整批失败会把已经生成的工艺说成没跑。
      const failedIds = result.failures.map((item) => item.part_id).join("、");
      allPartsProcessSettle(state.succeeded > 0 ? "task-partial" : "task-failed", {
        message: `${summary}；失败零件：${failedIds}`,
        total: state.total, succeeded: state.succeeded, failed: state.failed,
        skipped: state.skipped, failures: result.failures,
      });
    } else {
      allPartsProcessSettle("task-completed", {
        message: summary, total: state.total, succeeded: state.succeeded,
        failed: state.failed, skipped: state.skipped,
      });
    }
    status(summary);
    // 批量跑完后，当前选中的零件若是这一轮刚生成出来的，同样按「已生成即展开」处理。
    const selected = ((currentIR && currentIR.parts) || [])
      .find((item) => item.part_id === currentSelectedId);
    autoOpenGeneratedProcess(selected);
  } catch (error) {
    allPartsProcessSettle("task-failed", { message: (error && error.message) || "批量工艺推荐失败。" });
    status(`批量工艺推荐失败：${(error && error.message) || ""}`);
  } finally {
    allPartsProcessBusy = false;
    const runtime = window.TechBoardRuntime;
    if (runtime && typeof runtime.updateActionState === "function") {
      runtime.updateActionState("runAllPartProcesses", { busy: false });
    }
  }
}

/* 「仅重试失败项」：只把上一轮失败的零件交给既有单件链路重跑，已成功件不重跑
 * （不重复计费）。结算口径与整批一致：有成功件 → task-partial，一件都没成功 → task-failed。 */
async function retryFailedPartProcesses(options = {}) {
  // 与整批同一道忙碌闸门：重试期间再点一次不会变成第二串并行的模型调用（重复计费）。
  if (allPartsProcessBusy) return { state: null, failures: [] };
  const projectId = currentProject;
  if (!projectId) throw new Error("还没有选择项目，无法重试。");
  const remembered = globalThis.__techPartProcessFailures || [];
  const wanted = new Set(remembered.map((item) => String(item.part_id || item.id || "")).filter(Boolean));
  const parts = ((currentIR && currentIR.parts) || []).filter((part) => wanted.has(String(part.part_id)));
  if (!parts.length) {
    status("没有需要重试的零件。");
    return { state: null, failures: [] };
  }
  allPartsProcessBusy = true;
  const runtime = window.TechBoardRuntime;
  if (runtime && typeof runtime.updateActionState === "function") {
    runtime.updateActionState("retryFailedPartProcesses", { busy: true });
  }
  try {
    const state = { total: parts.length, done: 0, running: "", succeeded: 0, failed: 0, skipped: 0 };
    const failures = [];
    for (const part of parts) {
      const name = `${part.part_id} ${part.name || ""}`.trim();
      state.running = name;
      allPartsProcessPublish(state);
      status(`正在重试工艺推荐 ${state.done + 1}/${state.total}：${name}`, true);
      try {
        await runOnePartProcess(projectId, part);
        markPartProcessReady(projectId, part.part_id);
        state.succeeded += 1;
      } catch (error) {
        state.failed += 1;
        failures.push({
          part_id: part.part_id,
          name: name,
          message: (error && error.message) || "工艺推荐失败",
        });
      }
      state.done += 1;
      state.running = "";
      allPartsProcessPublish(state);
    }
    globalThis.__techPartProcessFailures = failures.map((item) => ({
      part_id: item.part_id, name: item.name, message: item.message,
    }));
    refreshBoardActionState();
    const summary = `失败项重试完成：成功 ${state.succeeded}、失败 ${state.failed}`;
    allPartsProcessSettle(failures.length === 0 ? "task-completed"
      : (state.succeeded > 0 ? "task-partial" : "task-failed"), {
      message: failures.length ? `${summary}；仍是失败的零件：${failures.map((item) => item.part_id).join("、")}` : summary,
      total: state.total, succeeded: state.succeeded, failed: state.failed,
      skipped: state.skipped, failures: failures,
    });
    status(summary);
    requestBoardSummary();
    return { state: state, failures: failures };
  } finally {
    allPartsProcessBusy = false;
    if (runtime && typeof runtime.updateActionState === "function") {
      runtime.updateActionState("retryFailedPartProcesses", { busy: false });
    }
  }
}

// 批量入口：只启动后台链路并秒级回执（deferred），真正的完成 / 失败由后台自己上报。
function startAllPartProcesses(options) {
  if (allPartsProcessBusy) {
    return { ok: false, error: { code: "busy", message: "正在生成工艺推荐，请稍候。" } };
  }
  if (!currentProject) {
    return { ok: false, error: { code: "no-project", message: "还没有选择项目，无法生成工艺推荐。" } };
  }
  if (!currentIR || !(currentIR.parts || []).length) {
    return { ok: false, error: { code: "no-parts", message: "还没有零件，请先完成图纸解析。" } };
  }
  allPartsProcessBusy = true;
  const runtime = window.TechBoardRuntime;
  if (runtime && typeof runtime.updateActionState === "function") {
    runtime.updateActionState("runAllPartProcesses", { busy: true });
  }
  runAllPartProcessesInBackground(options);
  return { ok: true };
}

/* 统一看板协议：2.1 的「开始解析」注册成 parseDrawing，父壳底栏 / Agent 只发动作名，
 * 页面上的原按钮继续走同一个函数，独立打开时行为不变。 */
if (window.TechBoardRuntime && typeof window.TechBoardRuntime.registerActions === "function") {
  // 解析是长任务（视觉模型一轮常常远超桥的 20 秒默认超时）：动作条目声明 deferred，
  // 只负责启动后台链路并秒级回执，真正的完成 / 失败由后台结束时自己推给父壳。
  let parseDrawingBusy = false;
  function parseDrawingSettle(event, message) {
    try {
      window.TechBoardRuntime.publish(event, "parseDrawing",
        message ? { action: "parseDrawing", message: message } : { action: "parseDrawing" });
    } catch (error) { /* 独立打开无运行时 */ }
  }
  // 后台链路放在注册表外：动作条目只负责启动它并秒级回执（deferred），
  // 真正的完成 / 失败由这里解析完后推给父壳。
  async function parseDrawingInBackground() {
    try {
      const result = await parseDrawing();
      if (result) parseDrawingSettle("task-completed");
      else parseDrawingSettle("task-failed", parseDrawingError || "图纸解析未完成。");
    } catch (error) {
      parseDrawingSettle("task-failed", (error && error.message) || "图纸解析失败。");
    } finally {
      parseDrawingBusy = false;
      window.TechBoardRuntime.updateActionState("parseDrawing", { busy: false });
    }
  }
  window.TechBoardRuntime.registerActions({
    parseDrawing: {
      label: "一键解析图纸",
      // 解析完成前它是本页唯一主按钮；有解析结果之后让位给「确认解析结果」。
      role: "aux",
      order: 10,
      deferred: true,
      // 这一步会真的跑起来：给左侧一条用户口吻的回声（文案属执行方，是每次调用的入参）。
      prompt:"帮我解析这张图纸。",
      run: () => {
        const button = $("btnParse");
        if (button && button.disabled) {
          return { ok: false, error: { code: "not-ready", message: "当前没有可解析的图纸，请先上传 2D 工程图。" } };
        }
        if (parseDrawingBusy) {
          return { ok: false, error: { code: "busy", message: "正在解析图纸，请稍候。" } };
        }
        // 先告诉父壳 busy，后台链路无论成功失败都恢复；这里不等解析结果。
        parseDrawingBusy = true;
        window.TechBoardRuntime.updateActionState("parseDrawing", { busy: true });
        parseDrawingInBackground();
        return { ok: true };
      },
      getState: () => {
        const button = $("btnParse");
        return {
          visible: true,
          enabled: Boolean(button) && !button.disabled,
          busy: Boolean(button && button.getAttribute("aria-busy") === "true"),
          role: drawingParsed() ? "aux" : "primary",
        };
      },
    },
    // 2.1 的收口动作：解析结果确认无误后进 2.2 组装与整合。确认不是本地布尔冒充完成 ——
    // 先回读后端真实状态（GET /api/projects/<id>），解析结果确实在才走既有嵌入导航通道
    // （tech-embed.js 的 requestNavigate），独立打开时由它整页跳转。
    confirmDrawingResult: {
      label: "确认解析结果",
      role: "aux",
      order: 25,
      run: async () => {
        if (!currentProject) {
          return { ok: false, error: { code: "no-project", message: "请先打开项目。" } };
        }
        try {
          const project = await fetch(`${API}/api/projects/${currentProject}`)
            .then(r => r.ok ? r.json() : null);
          if (project && project.ir) { currentIR = project.ir; renderIR(currentIR); }
        } catch (error) { /* 回读失败按下面的真实状态判定，不假装确认成功 */ }
        if (!drawingParsed()) {
          status("还没有可确认的解析结果：请先点「一键解析图纸」。", true);
          return { ok: false, error: { code: "not-parsed",
            message: "还没有可确认的解析结果，请先完成图纸解析。" } };
        }
        if (!(window.TechEmbed && typeof window.TechEmbed.requestNavigate === "function")) {
          return { ok: false, error: { code: "no-navigation",
            message: "当前页面缺少嵌入导航通道，无法进入下一步。" } };
        }
        window.TechEmbed.requestNavigate("process", currentProject);
        return { ok: true };
      },
      // 单行对象字面量：与同文件既有条目一致，避免多行 `})` 打断按行取块的静态契约。
      // 主按钮三段式：只有全部零件都有工艺推荐之后，确认才是主按钮。
      getState: () => ({ visible: drawingParsed(), enabled: true, busy: false,
                         role: partsProcessComplete() ? "primary" : "aux" }),
    },
    runAllPartProcesses: {
      label: "一键生成全部工艺推荐",
      role: "aux",
      order: 15,
      deferred: true,
      run: () => startAllPartProcesses(),
      getState: () => {
        return {
          visible: true,
          enabled: Boolean(currentProject) && Boolean(currentIR && (currentIR.parts || []).length)
            && !allPartsProcessBusy,
          busy: Boolean(allPartsProcessBusy),
          role: (drawingParsed() && !partsProcessComplete()) ? "primary" : "aux",
        };
      },
    },
    // 仅重试失败项：只在上一轮真留下失败清单时出现（没失败就没有可重试的东西）。
    retryFailedPartProcesses: {
      label: "仅重试失败项",
      role: "aux",
      order: 16,
      deferred: true,
      run: () => retryFailedPartProcesses().then(() => ({ ok: true })),
      // 与 runAllPartProcesses 同款写法：块式 getState 不写 `})` 收尾，
      // 否则会被按 `\n\s*})` 截取注册表的既有走查提前截断。
      getState: () => {
        return {
          visible: ((globalThis.__techPartProcessFailures || []).length) > 0,
          enabled: !allPartsProcessBusy,
          busy: Boolean(allPartsProcessBusy),
          role: "aux",
        };
      },
    },
    // 联网核验 / 校验修正：左侧操作栏不再重复提供 —— 两者已在 2.1 页内「更多功能 ▾」
    // 菜单里（index.html 的 #btnModelLookup / #btnVerify）。动作本体保留，Agent 与看板
    // 内部仍可按名字分派；这里只关掉左侧栏的可见性。
    modelLookup: { label: "联网核验", role: "aux", order: 30, run: () => runModelLookup(), getState: () => ({ visible: false, enabled: Boolean(currentIR), busy: false }) },
    verify: { label: "校验修正", role: "aux", order: 40, run: () => runVerification(), getState: () => ({ visible: false, enabled: Boolean(currentIR), busy: false }) },
    searchComponents: { label: "重新检索零部件库", role: "aux", order: 50, run: (payload) => runComponentMatch(payload || {}), getState: () => ({ visible: true, enabled: Boolean(currentProject), busy: false }) },
  });
}
/* --------------------------------------------------------------------------- //
 * 统一看板视图（技术工艺 Agent 能力恢复第 5 步）
 * ---------------------------------------------------------------------------
 * 左侧会话栏的入口只发 navigate-view，真正的呈现留在右侧看板内部：这里把 2.1 页
 * 既有面板（#secParts / #secQuestions / #secEvidence / #secVersions / #secUpload /
 * #secImport3d / #verificationDetails / #modelLookupDetails）、既有文件接口与既有
 * 函数复用进一个看板内容宿主，不复制业务实现、不新建第二套数据源。独立的旧页面
 * 打开时没有父壳桥，window.TechBoardViews 让同页就地打开同一份面板。
 * ------------------------------------------------------------------------- */

function boardStageName() {
  return new URLSearchParams(location.search).get("stage") || (__techEmbedMode__ ? "drawing" : "");
}

let boardFileManifest = null;
let boardCardTrigger = null;  // 弹卡片的触发按钮：关闭时把焦点还回去

// 左侧入口 → 看板视图：run 全部复用既有的面板 / 既有接口（card: true 的三项走弹卡片）。
const BOARD_VIEW_SPECS = {
  // 零件清单是 2.1 固定左栏的常驻内容（#secParts / #tree 全页唯一），
  // 不再是可搬运的覆盖式视图：run 只聚焦左栏，不把节点搬进结果宿主。
  parts: { title: "零件清单", focus: "parts" },
  questions: { title: "待澄清问题", sections: ["secQuestions"] },
  evidence: { title: "解析视图", sections: ["secEvidence"] },
  review: { title: "版本与校核", sections: ["secVersions", "verificationDetails", "modelLookupDetails"], card: true },
  upload: { title: "补充需求图纸", sections: ["secUpload"] },
  import3d: { title: "导入已有 3D 模型", sections: ["secImport3d"], card: true },
  files: { title: "任务文件", files: true, card: true },
  report: { title: "解析报告", report: true },
};

// 解析摘要 → 父壳看板桥。左侧入口的显示 / 计数只来自这条 result-summary。
function publishResultSummary(ir) {
  const runtime = window.TechBoardRuntime;
  if (!runtime || typeof runtime.emit !== "function") return;
  const source = ir || currentIR || null;
  const parts = (source && source.parts) || [];
  const extras = $("extras");
  const questions = extras ? extras.querySelectorAll(".extra-item, .standard-item").length : 0;
  const extrasText = (extras ? extras.textContent || "" : "").trim();
  const hasQuestions = questions > 0 || Boolean(extrasText && extrasText !== "暂无待澄清问题");
  const total = Number((boardFileManifest && boardFileManifest.total) || 0);
  runtime.emit("state", "result-summary", {
    stage: boardStageName(),
    parsed: Boolean(source),
    results: {
      parts: { available: parts.length > 0, count: parts.length },
      questions: { available: hasQuestions, count: questions },
      report: { available: Boolean(source) },
      files: { available: total > 0, count: total },
    },
  });
}

function boardViewHost() {
  let host = document.getElementById("boardViewHost");
  if (host) return host;
  const panel = document.querySelector(".oc-work .center-panel")
    || document.querySelector(".oc-work") || document.querySelector(".oc-shell") || document.body;
  host = document.createElement("section");
  host.id = "boardViewHost";
  host.className = "board-view-host";
  host.hidden = true;
  const head = document.createElement("div");
  head.className = "board-view-head";
  const title = document.createElement("div");
  title.className = "board-view-title";
  title.id = "boardViewTitle";
  const back = document.createElement("button");
  back.type = "button";
  back.className = "board-view-back";
  back.textContent = "← 返回 3D 视图";
  // 返回一律按看板内部父子视图回到上一层，不顺带导航父壳。
  back.addEventListener("click", () => boardBackToParent());
  head.append(title, back);
  const body = document.createElement("div");
  body.className = "board-view-body";
  body.id = "boardViewBody";
  host.append(head, body);
  panel.insertBefore(host, panel.firstElementChild);
  return host;
}

// 面板节点始终是同一份：切换视图时把不需要的放回抽屉容器，再移入目标面板。
function resetBoardViewBody(body) {
  if (!body) return;
  // 预览态被容器重建 / 关闭时也要释放 objectURL（长会话反复预览不能一路泄漏）。
  if (filePreviewState.container && body.contains(filePreviewState.container)) {
    releaseFilePreviewUrl();
    filePreviewState = { container: null, onBack: null };
  }
  body.querySelectorAll("[data-board-generated]").forEach((node) => node.remove());
  const drawerBody = document.getElementById("ocDrawerBody");
  if (!drawerBody) return;
  Array.from(body.children).forEach((node) => {
    if (!node.hasAttribute("data-drawer-section")) return;
    node.setAttribute("data-drawer-hidden", "true");
    drawerBody.append(node);
  });
}

// 任务文件预览：唯一一份实现，弹卡片与 2.1 悬浮小窗共用。
// 文件 url 是受鉴权保护的同源 /api 路径：必须走 fetch —— 本文件顶部的 window.fetch 包装
// 会自动带 Authorization。不能再发裸链接：顶层导航不带请求头、也没有 ?token=，
// 后端 app 级鉴权会回 401「请先在配置报价 CPQ 中登录」，浏览器把那句 JSON 当网页显示。
const TEXT_PREVIEW_LIMIT = 2000;      // 文本预览最多显示的行数
const TEXT_PREVIEW_BYTES_LIMIT = 200 * 1024;   // 文本预览最多显示的字符量（Spec：2000 行或 200KB）
const TEXT_FILE_PATTERN = /\.(txt|md|csv|json|log|yaml|yml)$/i;
const IMAGE_FILE_PATTERN = /\.(png|jpe?g|gif|webp|svg|bmp)$/i;
let filePreviewUrl = null;
let filePreviewState = { container: null, onBack: null };

function releaseFilePreviewUrl() {
  if (filePreviewUrl) { URL.revokeObjectURL(filePreviewUrl); filePreviewUrl = null; }
}

function filePreviewKind(file) {
  const name = String((file && file.name) || "");
  if ((file && file.kind === "image") || IMAGE_FILE_PATTERN.test(name)) return "image";
  if (/\.pdf$/i.test(name)) return "pdf";
  if ((file && file.kind === "model") || /\.(stl|step|stp)$/i.test(name)) return "model";
  if ((file && file.kind === "table") || TEXT_FILE_PATTERN.test(name)) return "text";
  return "other";
}

const mapFilePreviewError = (status) =>
  (status === 401 || status === 403)
    ? "登录状态已失效，请刷新页面后重新登录。"
    : `读取失败：HTTP ${status}`;

function downloadPreviewFile(file) {
  if (!filePreviewUrl) return;
  const link = document.createElement("a");
  link.href = filePreviewUrl;
  link.download = file.name || "下载";
  document.body.append(link);
  link.click();
  link.remove();
}

function renderPreviewShell(file) {
  const wrap = document.createElement("div");
  wrap.className = "file-preview";
  const head = document.createElement("div");
  head.className = "file-preview-head";
  const name = document.createElement("span");
  name.className = "file-preview-name";
  name.textContent = file.name || "未命名文件";
  const back = document.createElement("button");
  back.type = "button";
  back.className = "file-preview-back";
  back.textContent = "← 返回文件列表";
  back.addEventListener("click", () => window.CadFilePreview.close());
  const download = document.createElement("button");
  download.type = "button";
  download.className = "file-preview-download";
  download.textContent = "下载";
  download.addEventListener("click", () => downloadPreviewFile(file));
  head.append(name, back, download);
  const content = document.createElement("div");
  content.className = "file-preview-content";
  content.textContent = "正在读取…";
  wrap.append(head, content);
  return { wrap, content };
}

async function openFilePreview(file, container, onBack) {
  if (!container || !file) return;
  releaseFilePreviewUrl();
  filePreviewState = { container, onBack: typeof onBack === "function" ? onBack : null };
  const { wrap, content } = renderPreviewShell(file);
  container.replaceChildren(wrap);
  let response;
  try {
    response = await fetch(file.url);
  } catch (error) {
    content.textContent = mapFilePreviewError(0);   // 网络层失败：不暴露底层报文
    return;
  }
  if (!response.ok) {
    content.textContent = mapFilePreviewError(response.status);
    return;
  }
  let blob;
  try {
    blob = await response.blob();
  } catch (error) {
    content.textContent = mapFilePreviewError(response.status);
    return;
  }
  filePreviewUrl = URL.createObjectURL(blob);
  const kind = filePreviewKind(file);
  if (kind === "image") {
    const img = document.createElement("img");
    img.className = "file-preview-image";
    img.src = filePreviewUrl;
    img.alt = file.name || "预览图片";
    content.replaceChildren(img);
  } else if (kind === "pdf") {
    const frame = document.createElement("iframe");
    frame.className = "file-preview-frame";
    frame.src = filePreviewUrl;
    frame.title = file.name || "PDF 预览";
    content.replaceChildren(frame);
  } else if (kind === "text") {
    let text = "";
    try { text = await blob.text(); } catch (error) { text = ""; }
    // 行数与字符量取先到者：压缩 JSON / 内嵌 base64 这类超长单行也一定被截。
    const tooLong = text.length > TEXT_PREVIEW_BYTES_LIMIT;
    const lines = (tooLong ? text.slice(0, TEXT_PREVIEW_BYTES_LIMIT) : text).split("\n");
    const tooManyLines = lines.length > TEXT_PREVIEW_LIMIT;
    const truncated = tooLong || tooManyLines;
    const pre = document.createElement("pre");
    pre.className = "file-preview-text";
    pre.textContent = (tooManyLines ? lines.slice(0, TEXT_PREVIEW_LIMIT).join("\n") : lines.join("\n"))
      + (truncated ? "\n…（已截断）" : "");
    content.replaceChildren(pre);
  } else if (kind === "model") {
    const note = document.createElement("div");
    note.className = "file-preview-note";
    note.textContent = "STL / STEP 不在卡片内预览，3D 请在零件详情里看。";
    content.replaceChildren(note);
  } else {
    const note = document.createElement("div");
    note.className = "file-preview-note";
    note.textContent = "该类型暂不支持预览。";
    content.replaceChildren(note);
  }
}

function closeFilePreview() {
  releaseFilePreviewUrl();
  const onBack = filePreviewState.onBack;
  filePreviewState = { container: null, onBack: null };
  if (typeof onBack === "function") onBack();
}

window.CadFilePreview = { open: openFilePreview, close: closeFilePreview };

// 任务文件清单渲染：renderBoardFiles() 与预览「返回文件列表」共用，不重新请求 /files。
function renderFileList(box, manifest, openFile) {
  box.replaceChildren();
  ((manifest && manifest.groups) || []).forEach((group) => {
    const section = document.createElement("section");
    section.className = "board-file-group";
    const head = document.createElement("div");
    head.className = "board-file-group-head";
    const name = document.createElement("span");
    name.textContent = group.title || "文件";
    const count = document.createElement("span");
    count.className = "board-file-count";
    count.textContent = String((group.files || []).length);
    head.append(name, count);
    section.append(head);
    (group.files || []).forEach((file) => {
      const link = document.createElement("button");
      link.type = "button";
      link.className = "board-file-link";
      link.textContent = file.name || "未命名文件";
      if (file.note) link.title = file.note;
      link.addEventListener("click", () => openFile(file));
      section.append(link);
    });
    box.append(section);
  });
  if (!((manifest && manifest.groups) || []).length) {
    box.append(document.createTextNode((manifest && manifest.note) || "还没有任何文件。"));
  }
}

// 任务文件：复用既有 /files 接口与既有小窗 DOM 语义，正文留在看板内部。
function renderBoardFiles(body) {
  const project = currentProject || new URLSearchParams(location.search).get("project") || "";
  const box = document.createElement("div");
  box.className = "board-files";
  box.setAttribute("data-board-generated", "true");
  box.textContent = "正在读取任务文件…";
  body.append(box);
  if (!project) {
    box.textContent = "还没有选择项目，无法读取任务文件。";
    return { ok: false, error: { code: "no-project", message: "还没有选择项目。" } };
  }
  // 文件行点击统一交给 window.CadFilePreview（app.js 里的唯一一份预览实现）。
  const openFile = (file) => window.CadFilePreview.open(
    file, box, () => renderFileList(box, boardFileManifest, openFile));
  return fetch(`${API}/api/projects/${encodeURIComponent(project)}/files`)
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((manifest) => {
      boardFileManifest = manifest || { groups: [], total: 0 };
      renderFileList(box, boardFileManifest, openFile);
      publishResultSummary();
      return { ok: true, result: { view: "files", total: boardFileManifest.total || 0 } };
    })
    .catch((error) => {
      box.textContent = `任务文件读取失败：${error.message}`;
      return { ok: false, error: { code: "files-failed", message: error.message } };
    });
}

// 解析报告：复用既有 report.html 入口，但放在看板文档内的 iframe 里 —— 不开新窗口、
// 不在父层弹层，协议通道保持有效。
function renderBoardReport(body) {
  const project = currentProject || new URLSearchParams(location.search).get("project") || "";
  if (!project) return { ok: false, error: { code: "no-project", message: "还没有选择项目，无法生成解析报告。" } };
  const frame = document.createElement("iframe");
  frame.className = "board-report-frame";
  frame.setAttribute("data-board-generated", "true");
  frame.title = "图纸解析报告";
  frame.src = `report.html?project=${encodeURIComponent(project)}`;
  body.append(frame);
  return { ok: true, result: { view: "report" } };
}

// 零件清单里不再有批量按钮（用户要求）：批量能力只在左侧会话操作栏保留唯一入口 —— 看板动作
// runAllPartProcesses → startAllPartProcesses()，与 Agent 分派同一条通道。零件行上的单件
// 「工艺推荐」入口不变，仍用于查看 / 编辑 / 单件重算 / 定点处理失败项。

// 视图正文的唯一搬运口径：sections 搬既有节点（不复制），files 复用同一份
// renderBoardFiles()，report 复用同一份 renderBoardReport()。工作区与弹卡片共用。
function fillBoardViewBody(body, view, spec) {
  let outcome = { ok: true, result: { view: view, title: spec.title } };
  if (spec.report) outcome = renderBoardReport(body);
  else if (spec.files) outcome = renderBoardFiles(body);
  else (spec.sections || []).forEach((id) => {
    const node = document.getElementById(id);
    if (!node) return;
    node.removeAttribute("data-drawer-hidden");
    if (node.tagName === "DETAILS") node.open = true;
    body.append(node);
  });
  return outcome;
}

// 弹卡片：导入已有 3D 模型 / 版本与校核 / 任务文件不再占用工作区宿主 ——
// 渲染进 #boardCardBody 并显示居中的 #boardCardMask，工作区内容原样留在原地。
function openBoardCard(view, spec) {
  const mask = document.getElementById("boardCardMask");
  const body = document.getElementById("boardCardBody");
  const title = document.getElementById("boardCardTitle");
  if (!mask || !body) return { ok: false, error: { code: "no-card", message: "看板卡片未就绪。" } };
  // 记住触发按钮，关闭时把焦点还回去（键盘用户不丢上下文）。
  boardCardTrigger = document.activeElement && typeof document.activeElement.focus === "function"
    ? document.activeElement : null;
  resetBoardViewBody(body);
  if (title) title.textContent = spec.title;
  // 任务文件仍走既有的 renderBoardFiles()（同一份实现，不复制第二份）。
  const outcome = spec.files ? renderBoardFiles(body) : fillBoardViewBody(body, view, spec);
  mask.hidden = false;
  const card = document.getElementById("boardCard");
  if (card && typeof card.focus === "function") card.focus();
  return outcome;
}

function closeBoardCard() {
  const mask = document.getElementById("boardCardMask");
  if (!mask || mask.hidden) return false;
  resetBoardViewBody(document.getElementById("boardCardBody"));
  mask.hidden = true;
  // 焦点归还（Spec D2）：触发按钮若已被收起，就退回到常驻的菜单按钮 ——
  // 「更多功能 ▾」里那两颗按钮的 onclick 会先把 #actionSheet 收起，隐藏子树上的 focus()
  // 是空操作，焦点会掉在 body 上（实测：focus() 调了但 activeElement 仍是卡片）。
  const back = boardCardTrigger;
  boardCardTrigger = null;
  let target = null;
  if (back && document.contains(back)) {
    target = back.closest("[hidden]") ? document.getElementById("btnMoreActions") : back;
  }
  if (target && typeof target.focus === "function") {
    try { target.focus(); } catch { /* 触发按钮可能已不在文档里 */ }
  }
  return true;
}

function openBoardView(view) {
  const spec = BOARD_VIEW_SPECS[view];
  if (!spec) return { ok: false, error: { code: "unknown-action", message: "看板未注册视图：" + view } };
  // 带 card: true 的视图走弹卡片，不占用工作区（不隐藏 #modelPanes / #analysisPanel）。
  if (spec.card) return openBoardCard(view, spec);
  const host = boardViewHost();
  const body = document.getElementById("boardViewBody");
  resetBoardViewBody(body);
  const title = document.getElementById("boardViewTitle");
  if (title) title.textContent = spec.title;
  const outcome = fillBoardViewBody(body, view, spec);
  const panes = document.getElementById("modelPanes");
  const analysis = document.getElementById("analysisPanel");
  if (panes) panes.hidden = true;
  if (analysis) analysis.hidden = true;
  host.hidden = false;
  return outcome;
}

function closeBoardView() {
  // 卡片是同一套关闭出口：先收卡片，没有卡片再收工作区宿主。
  if (closeBoardCard()) return true;
  const host = document.getElementById("boardViewHost");
  resetBoardViewBody(document.getElementById("boardViewBody"));
  if (host) host.hidden = true;
  const panes = document.getElementById("modelPanes");
  if (panes) panes.hidden = false;
  return true;
}

function runBoardView(view, payload) {
  // 零件层级视图交给看板内部状态机：这里只转发，返回键也按内部父子关系走。
  const partViews = window.TechBoardPartViews;
  if (partViews && PART_FLOW_VIEWS.indexOf(view) !== -1) {
    return partViews.show(view, payload || {});
  }
  // 「零件清单」是 2.1 固定左栏：只聚焦，不打开覆盖式结果宿主。
  if (view === "parts") {
    return partViews ? partViews.show("parts-list", payload || {}) : focusPartsColumn();
  }
  try {
    return openBoardView(view);
  } catch (error) {
    return { ok: false, error: { code: "view-failed", message: String((error && error.message) || error) } };
  }
}

// 独立 2.1 页没有父壳桥：+ 菜单与结果按钮由 agent-chat.js 调用这里就地打开同一份面板。
window.TechBoardViews = { open: runBoardView, close: closeBoardView, specs: BOARD_VIEW_SPECS };

/* --------------------------------------------------------------------------- //
 * 看板内部零件层级视图状态机（技术工艺 Agent 能力恢复第 6 步）
 * ---------------------------------------------------------------------------
 * drawing-overview → parts-list → part-detail → part-process / part-cost。
 * 所有零件详情、3D/2D、参数、工艺、成本、版本都在右侧看板内部展开；返回只改看板
 * 视图，不关闭父壳、不重载页面、不清左侧会话。渲染全部复用既有 selectPart /
 * #partDetail / #parameterEditor / #viewer / CadInlineAnalysis / /versions，
 * 不新建第二套零件渲染或数据源。
 * ------------------------------------------------------------------------- */
let boardPartView = "drawing-overview";

function boardPartId(payload) {
  const data = payload || {};
  const raw = data.partId || data.part_id || currentSelectedId || "";
  return raw ? String(raw) : "";
}

function findBoardPart(partId) {
  if (!partId) return null;
  return ((currentIR && currentIR.parts) || []).find((p) => p.part_id === partId) || null;
}

// 每次切换视图都上报当前视图名（父壳据此高亮左侧入口），并同步看板内的返回控件。
function notePartView(name) {
  boardPartView = String(name || "");
  if (window.TechBoardRuntime && typeof window.TechBoardRuntime.setView === "function") {
    window.TechBoardRuntime.setView(boardPartView);
  }
  return boardPartView;
}


// 退出看板内容宿主：把复用的抽屉面板放回原处，再显示 3D / 零件信息 / 参数。
function exitBoardViewHost() {
  const host = document.getElementById("boardViewHost");
  if (host && !host.hidden) {
    resetBoardViewBody(document.getElementById("boardViewBody"));
    host.hidden = true;
  }
}

// part-detail：复用既有 selectPart 渲染 3D / 2D / 识别依据 / 参数 / 材料 / 数量，
// 并用既有 /versions 带出版本（版本面板随详情一起展示）。
function showPartDetail(payload) {
  const part = findBoardPart(boardPartId(payload));
  if (!part) return { ok: false, error: { code: "no-part", message: "还没有选择零件。" } };
  selectPart(part);
  loadVersions();
  return { ok: true, result: { view: "part-detail", partId: part.part_id } };
}

function focusPartsColumn() {
  const column = document.querySelector(".drawing-parts-column") || document.getElementById("secParts");
  if (!column) return false;
  if (typeof column.scrollIntoView === "function") {
    try { column.scrollIntoView({ block: "nearest" }); } catch (error) { /* 老浏览器忽略参数 */ }
  }
  if (typeof column.focus === "function") {
    if (!column.hasAttribute("tabindex")) column.setAttribute("tabindex", "-1");
    try { column.focus({ preventScroll: true }); } catch (error) { column.focus(); }
  }
  return true;
}

function showPartView(name, payload) {
  const view = String(name || "");
  if (view === "drawing-overview") {
    window.CadInlineAnalysis?.reset();
    closeBoardView();
    setRightPane("model");
    notePartView(view);
    return { ok: true, result: { view: view } };
  }
  if (view === "parts-list" || view === "parts") {
    // 清单与 3D 是同一屏的固定两栏：这里只聚焦左栏，不搬运节点、不隐藏右栏。
    focusPartsColumn();
    notePartView("parts-list");
    return { ok: true, result: { view: "parts-list" } };
  }
  if (view === "part-detail") return showPartDetail(payload);
  const mode = PART_VIEW_MODE[view];
  if (mode) {
    const part = findBoardPart(boardPartId(payload));
    if (!part) {
      return { ok: false, error: { code: "no-part", message: "还没有选择零件，无法打开" + (mode === "process" ? "工艺推荐" : "成本测算") + "。" } };
    }
    return renderPartAnalysis(part, mode);
  }
  return { ok: false, error: { code: "unknown-view", message: "看板未注册零件视图：" + view } };
}

// 返回只改右侧视图：按 PART_VIEW_PARENT 回到上一层，不看浏览器历史。
function backPartView() {
  const parent = PART_VIEW_PARENT[boardPartView] || "drawing-overview";
  return showPartView(parent, { partId: currentSelectedId });
}

function boardBackToParent() {
  if (window.TechBoardPartViews && typeof window.TechBoardPartViews.back === "function") {
    return window.TechBoardPartViews.back();
  }
  return closeBoardView();
}

window.TechBoardPartViews = {
  show: showPartView,
  back: backPartView,
  current: () => boardPartView,
};

/* 看板视图注册：左侧入口只发 navigate-view，这里给出真实的 run 实现（复用上面的既有
 * 面板与既有接口）。父壳据 runtime 发出的 action-state(view.active) 高亮左侧入口。 */
if (window.TechBoardRuntime && typeof window.TechBoardRuntime.registerViews === "function") {
  window.TechBoardRuntime.registerViews({
    parts: { label: "零件清单", run: () => runBoardView("parts") },
    questions: { label: "待澄清问题", run: () => runBoardView("questions") },
    report: { label: "解析报告", run: () => runBoardView("report") },
    evidence: { label: "解析视图", run: () => runBoardView("evidence") },
    review: { label: "版本与校核", run: () => runBoardView("review") },
    files: { label: "任务文件", run: () => runBoardView("files") },
    upload: { label: "补充需求图纸", run: () => runBoardView("upload") },
    import3d: { label: "导入已有 3D 模型", run: () => runBoardView("import3d") },
    "drawing-overview": { label: "图纸解析总览", run: (payload) => runBoardView("drawing-overview", payload) },
    "parts-list": { label: "零件清单", run: (payload) => runBoardView("parts-list", payload) },
    "part-detail": { label: "零件详情", run: (payload) => runBoardView("part-detail", payload) },
    "part-process": { label: "工艺推荐", run: (payload) => runBoardView("part-process", payload) },
    "part-cost": { label: "成本测算", run: (payload) => runBoardView("part-cost", payload) },
  });
}

/* 摘要刷新：父壳就绪后会发 refresh-data，这里把当前解析摘要重播一次，保证左侧入口
 * 的显示 / 计数与看板一致（不新增接口、不新增数据源）。 */
if (window.TechBoardRuntime && typeof window.TechBoardRuntime.registerActions === "function") {
  window.TechBoardRuntime.registerActions({
    refreshData: {
      label: "刷新看板数据",
      role: "aux",
      order: 60,
      silent: true,
      // 纯刷新（无 edits）只重播解析摘要；带 edits 时是 Agent 改完零件参数后经桥
      // 触发的刷新，复用既有 refreshAfterChatEdit（拉 IR / 重生几何 / 刷版本）。
      run: (payload) => {
        const edits = (payload && payload.edits) || [];
        return Promise.all(edits.map((edit) => refreshAfterChatEdit(edit))).then(() => {
          publishResultSummary(currentIR);
          return { ok: true, result: { edits: edits.length } };
        });
      },
      // 只退出左侧栏：刷新仍由 refresh-data 命令与 Agent 工具走 executeAction 触发。
      getState: () => ({ visible: false }),
    },
  });
}
