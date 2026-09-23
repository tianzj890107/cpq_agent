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
      // 右栏先让给零件面板：隐藏 #viewer 并换掉「3D 视图」那句承诺（Spec §3.5）。
      enterDrawingFlowPanes();
      // 把链路终态交回后台链路（parseDrawingInBackground 据此决定看板事件），不许丢掉。
      const drawingFlowState = await runDrawingFlowParse();
      parseDrawingError = "";
      return { drawing_flow: drawingFlowState };
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
  // 读失败第一优先（Spec `packaging-drawing-flow-read-failure.md` §C4）：只说"这一次没读到"，
  // **不**渲染步骤表、**不**渲染 CAD IR 摘要 —— 那两处会把"读不到"伪装成"跑过但为空"。
  const readProblem = (payload && payload.read_problem && typeof payload.read_problem === "object")
    ? payload.read_problem : null;
  if (readProblem) {
    panel.innerHTML = '<div class="drawing-flow-title">图纸解析链路（DWG / DXF）</div>'
      + `<div class="drawing-flow-cad-ir drawing-flow-empty">`
      + `${esc(drawingFlowReadProblemText(readProblem))}</div>`;
    return panel;
  }
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
    const duration = drawingFlowStepDurationText(s);
    return `<tr class="drawing-flow-row drawing-flow-${esc(status)}">`
      + `<td class="drawing-flow-step">${esc(DRAWING_FLOW_STEP_LABEL[id] || id)}</td>`
      + `<td class="drawing-flow-status">${esc(DRAWING_FLOW_STATUS_LABEL[status] || status)}`
      + `${duration ? `<span class="drawing-flow-duration">${esc(duration)}</span>` : ""}</td>`
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

// 每一步花了多久（Spec C7）：服务端给 `duration_ms`（单调时钟），前端只做展示换算。
// 没量到（缺失 / 0 / 负数 / 非数字）一律回空串 —— 不许显示 `0.0 s`，那是把"没量到"说成"用了 0 秒"。
function drawingFlowStepDurationText(step) {
  const raw = (step && typeof step === "object") ? step.duration_ms : null;
  const value = (raw === null || raw === undefined) ? NaN : Number(raw);
  if (!Number.isFinite(value) || value <= 0) return "";
  if (value >= 60000) {
    const total = Math.round(value / 1000);
    return `${Math.floor(total / 60)} 分 ${total % 60} 秒`;
  }
  return `${(value / 1000).toFixed(1)} s`;
}

// 链路状态「读不到」的文案（Spec `packaging-drawing-flow-read-failure.md` §C1）：
// 与左栏零件 / 业务部件 / 平面图那几条读路径同一套口径 —— 读失败要自己一句话，
// 且**不**说成"这个项目没跑过一键解析"。纯函数：无 DOM / 无 `fetch(` / 无 `localStorage`。
function drawingFlowReadProblemText(problem) {
  const row = (problem && typeof problem === "object") ? problem : {};
  if (!String(row.code || "").trim()) return "";
  const status = Number(row.status) || 0;
  return status > 0
    ? `暂时读不到图纸解析链路状态（HTTP ${status}），这不代表这个项目没跑过一键解析`
    : "暂时读不到图纸解析链路状态（网络错误），这不代表这个项目没跑过一键解析";
}

// 读链路状态：404（端点未上线）仍回 `null`（既有路径逐字不变，走"还没跑过"那一态）；
// 其余非 2xx / 正文不可用 / 网络异常一律给带 `read_problem` 的空状态形状（Spec §C2）——
// 不许再折成 `null`，否则面板会沉默、把上一次的内容当成本次结果。
async function fetchDrawingFlowState() {
  const unavailable = (code, status) => ({
    steps: [], cad_ir: {},
    read_problem: {code: code, status: Number(status) || 0, message: ""},
  });
  let res = null;
  try {
    res = await fetch(`${API}/api/projects/${currentProject}/drawing-flow`);
  } catch (error) {
    return unavailable("drawing_flow_unavailable", 0);
  }
  if (Number(res.status) === 404) return null;
  if (!res.ok) return unavailable("drawing_flow_unavailable", res.status);
  let payload = null;
  try {
    payload = await res.json();
  } catch (error) {
    payload = null;
  }
  if (!payload || typeof payload !== "object") {
    return unavailable("drawing_flow_body_unexpected", res.status);
  }
  return payload;
}

async function loadDrawingFlowPanel() {
  const state = await fetchDrawingFlowState().catch(() => null);
  // 只有 404（端点未上线）才什么都不做；带 `read_problem` 的形状也要画出来（Spec §C3）。
  if (!state) return null;
  return renderDrawingFlowPanel(state);
}

/* ---------------- 2.1 一键解析的终态信号（Spec drawing-flow-parse-terminal-signal.md C1） ---- */
// 终态判定只有这一处：纯函数，不读 DOM / 全局 / 网络（node 可直接 eval 跑）。
// code / message 一律逐字取服务端载荷，前端不重新措辞、不拼接猜测。
function drawingFlowTerminalSignal(flowState, error) {
  const state = (flowState && typeof flowState === "object") ? flowState : null;
  const steps = (state && Array.isArray(state.steps)) ? state.steps : [];
  const firstWithStatus = wanted => {
    for (let index = 0; index < steps.length; index++) {
      const row = (steps[index] && typeof steps[index] === "object") ? steps[index] : {};
      if (wanted.indexOf(String(row.status || "")) >= 0) return row;
    }
    return null;
  };
  const detailOf = row => ((row && row.detail && typeof row.detail === "object") ? row.detail : {});
  // blocked 优先于 failed：缺前置条件时重试必然再失败，比"失败"更该先说。
  const blocked = firstWithStatus(["blocked"]);
  if (blocked) {
    return { event: "task-blocked", code: String(blocked.error_code || ""),
             message: String(blocked.error_message || ""),
             action: String(detailOf(blocked).action || ""), retryable: false };
  }
  const failed = firstWithStatus(["failed", "unavailable"]);
  if (failed) {
    return { event: "task-failed", code: String(failed.error_code || ""),
             message: String(failed.error_message || ""),
             action: String(detailOf(failed).action || ""),
             retryable: failed.retryable !== false };
  }
  const runId = state ? String(state.run_id || "") : "";
  const completed = steps.some(row => String((row && row.status) || "") === "completed");
  if (state && runId && completed) {
    return { event: "task-completed", code: "", message: "", action: "", retryable: true };
  }
  if (error) {
    return { event: "task-failed", code: "FLOW_RUN_REJECTED", message: String(error),
             action: "", retryable: true };
  }
  return { event: "task-failed", code: "FLOW_STATE_MISSING",
           message: "图纸解析链路没有返回状态，请重新解析。", action: "", retryable: true };
}

/* ---------------- 2.1 右栏零件面板（包装零件第 2 层，Spec packaging-parts-selectable-panel.md） ----------------
   图纸项目的零件行是可点的：点一件 → 右栏显示这一件的轮廓（后端给的坐标）、事实与证据。
   轮廓**不做任何前端几何求解**（环、面积、坐标变换一律后端算好），前端只用 viewBox 摆画布；
   open 件显示虚线 + 包围盒矩形，绝不画成闭合件的样子。 */
const PACKAGING_OUTLINE_COPY = {
  closed: "轮廓已闭合：尺寸与面积为真实轮廓口径。",
  open: "未找到闭合轮廓，以下尺寸来自分量包围盒，仅供估算",
  unavailable: "图纸单位未确认，不给出尺寸；请先确认单位后再解析。",
};
// 降级原因的人话：与后端 outline_reason 一一对应（Spec §3.4 第 2 行要求点名原因）。
// 新闭集（Spec packaging-parts-outline-chaining.md §2.4）点名的是**具体**原因：
// 「断口 > 1mm」「预算中止」「没算完」与"图纸真的没有闭合环"必须能分开说。
const PACKAGING_OUTLINE_REASONS = {
  no_closed_loop: "件内没有首尾相接的闭合环",
  loop_too_small: "找到了环但面积低于最小面积门槛",
  unit_unconfirmed: "图纸单位未确认",
  odd_endpoints: "这一件在图纸里没有闭合轮廓（缺口 > 1mm）",
  loop_budget_exhausted: "这一件的环搜索没算完（图太密），暂时给不出轮廓结论",
  no_curve_entity: "这一件里没有可用的曲线/坐标",
};
// 尺寸口径（后端 size_source）的人话，避免把"包围盒"说成"轮廓"。
const PACKAGING_SIZE_SOURCE_COPY = {
  closed_outline: "真实轮廓（闭合环）",
  component_bbox: "分量包围盒（求不出轮廓，仅供估算）",
  dwg_outline: "图纸自带包围盒（这一件没有可用坐标）",
};

//: 当前在右栏零件面板里看的零件（独立于视觉链路的 currentSelectedId，互不污染）。
let currentSelectedPanelPart = null;

// 未闭合原因的两条出路文案（Spec `packaging-open-outline-part-needs-a-way-out.md` §2.2）：
// `loop_budget_exhausted` 是"这一件没算完"（可重试 → 放额度重算），其余是"图纸真的没闭合"
// （要人处理 → 改图重传或按包围盒签字确认）。按钮与后端两条出口一一对应。
const PACKAGING_OUTLINE_EXITS = {
  loop_budget_exhausted: { action: "recompute", label: "重算轮廓" },
  no_curve_entity: { action: "confirm", label: "按包围盒签字确认" },
  unit_unconfirmed: { action: "confirm", label: "按包围盒签字确认" },
  odd_endpoints: { action: "confirm", label: "按包围盒签字确认" },
  loop_too_small: { action: "confirm", label: "按包围盒签字确认" },
};

function packagingOutlineExit(part) {
  const reason = String((part && part.outline_reason) || "");
  return PACKAGING_OUTLINE_EXITS[reason] || PACKAGING_OUTLINE_EXITS.odd_endpoints;
}

function pkgPartStatusText(part) {
  const status = String((part && part.outline_status) || "");
  const reason = String((part && part.outline_reason) || "");
  const base = PACKAGING_OUTLINE_COPY[status] || "";
  const why = PACKAGING_OUTLINE_REASONS[reason] || "";
  // 人签的字必须与几何闭合分开说（Spec §2.4）：几何状态仍是"未闭合"。
  const confirmation = (part && part.outline_confirmation) || null;
  const sign = (confirmation && confirmation.bound_by)
    ? `人工签字放行（${confirmation.bound_by}，按包围盒估算；几何状态仍是未闭合）` : "";
  const recompute = (part && part.outline_recompute) || null;
  const retried = (recompute && recompute.bound_by)
    ? `已重算轮廓（额度 ×${recompute.scale || "?"}，仍${recompute.changed ? "已闭合" : "未闭合"}）` : "";
  const head = why ? base + "（" + why + "）" : base;
  const notes = [retried, sign].filter(Boolean);
  if (!head) return notes.join(" · ");
  return notes.length ? head + " · " + notes.join(" · ") : head;
}

function pkgPartFactRow(label, value) {
  if (value === null || value === undefined || value === "") return "";
  return `<div class="packaging-part-fact"><span class="label">${esc(label)}</span>`
    + `<span class="value">${esc(String(value))}</span></div>`;
}

// 右栏零件面板：与 #viewer / #partDetail / #parameterEditor 互斥（不同时渲染两套零件内容）。
function renderPackagingPartPanel(payload) {
  const host = $("packagingPartPanel");
  if (!host) return null;
  // 缩略图是**业务部件**的东西（Spec `packaging-authority-thumbnail-media.md` §C6）：
  // 切到几何零件面板必须收起并清空，不许把上一件业务部件的图留在这里。
  const thumbHost = $("packagingPartThumbnail");
  if (thumbHost) { thumbHost.hidden = true; thumbHost.innerHTML = ""; }
  const part = (payload && payload.part) || {};
  const outline = (payload && payload.outline) || {};
  const status = String(outline.status || part.outline_status || "");
  currentSelectedPanelPart = part.part_code ? part : null;

  const title = $("packagingPartTitle");
  if (title) {
    const name = `${part.part_code || ""} ${part.name || ""}`.trim();
    title.textContent = name || "几何分量";
  }

  // 轮廓：只把后端给的点串成 viewBox + polygon；Y 轴用 <g> 的 transform 翻转，JS 里不做坐标运算。
  const outlineHost = $("packagingPartOutline");
  if (outlineHost) {
    const points = Array.isArray(outline.points) ? outline.points : [];
    const box = Array.isArray(outline.bbox) && outline.bbox.length === 4 ? outline.bbox : null;
    const width = box ? Math.max(1, Number(box[2]) - Number(box[0])) : 1;
    const height = box ? Math.max(1, Number(box[3]) - Number(box[1])) : 1;
    let svg = "";
    if (box && points.length >= 3) {
      const path = points.map(point => `${point[0]},${point[1]}`).join(" ");
      svg = `<svg class="packaging-part-svg" viewBox="0 0 ${width} ${height}"`
        + ` preserveAspectRatio="xMidYMid meet" role="img" aria-label="零件轮廓">`
        + `<g transform="translate(0,${height}) scale(1,-1)">`
        + `<polygon class="packaging-part-polygon is-${esc(status)}" points="${path}"/>`
        + `</g></svg>`;
    } else if (box) {
      svg = `<svg class="packaging-part-svg" viewBox="0 0 ${width} ${height}"`
        + ` preserveAspectRatio="xMidYMid meet" role="img" aria-label="分量包围盒">`
        + `<rect class="packaging-part-box" x="0" y="0" width="${width}" height="${height}"/></svg>`;
    }
    const note = pkgPartStatusText(part);
    outlineHost.innerHTML = (svg || `<div class="view-3d-placeholder">这一件没有可画的轮廓。</div>`)
      + (note ? `<div class="packaging-part-note">${esc(note)}</div>` : "");
  }

  const facts = $("packagingPartFacts");
  if (facts) {
    const length = part.unfolded_length_mm;
    const width = part.unfolded_width_mm;
    const size = (length === null || length === undefined) ? "—" : `${length}×${width} mm`;
    const layers = Array.isArray(part.layers) ? part.layers.join(" / ") : "";
    const box = Array.isArray(payload && payload.component_bbox) ? payload.component_bbox : [];
    facts.innerHTML = [
      pkgPartFactRow("展开尺寸", size),
      pkgPartFactRow("面积", part.area_mm2 === null || part.area_mm2 === undefined
        ? "—" : `${part.area_mm2} mm²`),
      pkgPartFactRow("尺寸口径", PACKAGING_SIZE_SOURCE_COPY[String(part.size_source || "")] || ""),
      pkgPartFactRow("图层", layers),
      pkgPartFactRow("角色", part.role),
      pkgPartFactRow("分量包围盒", box.length === 4 ? box.join(", ") : ""),
      pkgPartFactRow("与哪一件重复", part.repeat_of),
      pkgPartFactRow("实体条数", part.entity_total),
    ].join("");
  }

  // 两个下游按钮：可算性由 packagingPartProcessability() 判，灰按钮自己说明为什么灰。
  const actions = $("packagingPartActions");
  if (actions) {
    actions.innerHTML = packagingPartActionsHtml(part);
    const processButton = $("packagingPartProcess");
    if (processButton) processButton.addEventListener("click", () => packagingPartAnalyze("process"));
    // 包装项目**不再**把「平板挤出」当主流程入口（Spec
    // `packaging-business-parts-and-cad-plan-view.md` §6.1/§10）：右栏改成 CAD 平面图，
    // 这里只留工艺/成本两个按钮。既有挤出结论与 STL 仍作历史数据留在库里，不删。
  }

  const evidence = $("packagingPartEvidence");
  if (evidence) {
    const rows = Array.isArray(payload && payload.evidence) ? payload.evidence : [];
    evidence.innerHTML = packagingPartEvidenceRowsHtml(rows);
  }
  return host;
}

// 右栏切到"零件面板"：隐藏 3D 画布与视觉链路的零件详情/参数面板，避免两套内容同时出现。
function showPackagingPartPane() {
  const panel = $("packagingPartPanel");
  if (panel) panel.hidden = false;
  ["viewer", "partDetail", "parameterEditor"].forEach(id => {
    const node = $(id);
    if (!node) return;
    node.hidden = true;
    const wrapper = node.closest(".part-details, .parameter-panel");
    if (wrapper) wrapper.hidden = true;
  });
}

function hidePackagingPartPanel() {
  const panel = $("packagingPartPanel");
  if (panel) panel.hidden = true;
  currentSelectedPanelPart = null;
}

// 图纸项目进入时的右栏口径：右栏是零件面板，不是 3D（Spec §3.5，别留空白画布、别承诺 3D）。
function enterDrawingFlowPanes() {
  hidePackagingPartPanel();
  const viewer = $("viewer");
  if (viewer) viewer.hidden = true;
  const plan = packagingCadPlanViewer();
  if (plan) plan.hidden = !packagingCadPlanApplies();
  const label = $("viewerPartName");
  if (label) label.textContent = "几何分量（图纸零件）· 选中后看轮廓与证据";
  loadPackagingCadPlan();
}

// 点左栏零件行：选中 + 读单件详情 + 渲染面板。失败时面板里给可读原因，不留白。
async function selectPackagingPart(partCode) {
  const code = String(partCode || "").trim();
  if (!code) return null;
  currentSelectedPanelPart = null;
  setRightPane("model");
  showPackagingPartPane();
  markSelection(code);
  togglePartSubActions(code);
  const title = $("packagingPartTitle");
  if (title) title.textContent = code;
  const facts = $("packagingPartFacts");
  if (facts) facts.textContent = "正在读取零件详情…";
  const outlineHost = $("packagingPartOutline");
  if (outlineHost) outlineHost.innerHTML = "";
  const evidenceHost = $("packagingPartEvidence");
  if (evidenceHost) evidenceHost.innerHTML = "";
  // 读失败的两态分家（Spec `packaging-part-detail-read-failure.md` §C2）：
  //   · 404 + `PACKAGING_PART_NOT_FOUND` → 「没这件，重新选一件」；
  //   · 其它非 2xx（含 5xx）/ 网络异常 → 「暂时读不到，稍后重试」。
  // 文案一律由 `packagingPartDetailReadProblemText()` 产出 —— 不再贴服务端 / 浏览器原生文本。
  let res = null;
  try {
    res = await fetch(`${API}/api/projects/${currentProject}/requirement/`
      + `packaging-parts/${encodeURIComponent(code)}`);
  } catch (error) {
    if (facts) {
      facts.textContent = packagingPartDetailReadProblemText(
        {code: "parts_unavailable", status: 0});
    }
    return currentSelectedPanelPart;
  }
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (payload && payload.detail) || {};
    const detailCode = (detail && typeof detail === "object")
      ? String(detail.code || "") : "";
    if (facts) {
      facts.textContent = packagingPartDetailReadProblemText(
        {code: detailCode || "parts_unavailable", status: res.status});
    }
    return currentSelectedPanelPart;
  }
  renderPackagingPartPanel(payload);
  highlightPackagingBusinessPartSelection(code, payload);
  return currentSelectedPanelPart;
}


/* ---------------- 2.1 右栏零件面板的下游按钮（包装零件第 3 层，
   Spec packaging-parts-downstream-process-and-cost.md §5） ----------------
   可算性以后端 packaging_parts.processability() 的同一判据为准：前端只照着置灰并
   写明原因，绝不自己补默认材料/厚度；点击后仍复用既有 CadInlineAnalysis 渲染到
   右栏分析区，不新建第二套工艺 / 成本渲染。 */
function packagingPartProcessability(part) {
  const row = (part && typeof part === "object") ? part : {};
  if (String(row.outline_status || "") !== "closed") {
    return { ok: false, missing: ["outline"],
             reason: "未找到闭合轮廓（尺寸来自包围盒），不能直接排工艺" };
  }
  const missing = [];
  const spec = row.material && (row.material.spec || row.material.name || row.material);
  if (!spec) missing.push("material");
  if (!(Number(row.thickness_mm) > 0)) missing.push("thickness_mm");
  if (missing.length) {
    return { ok: false, missing: missing,
             reason: "缺材料或厚度：" + missing.join("、") + "（补全需求后重跑解析）" };
  }
  return { ok: true, missing: [], reason: "" };
}

function packagingPartActionsHtml(part) {
  const verdict = packagingPartProcessability(part);
  const off = verdict.ok ? "" : " disabled";
  const title = esc(verdict.reason || "");
  // 包装 2.1 的单件动作只剩「工艺推荐」一颗紧凑按钮（Spec
  // `packaging-28-part-auto-resolution-and-2d-board-cleanup.md` §6.2/§6.3）：成本属于第 4 阶段
  // （财务经理在成本工作台处理，2.1 不许提前生成或编辑成本），挤出预览与批量挤出也从这一页撤掉。
  return `<button id="packagingPartProcess" class="part-row-action" type="button"${off}`
    + ` title="${title}">工艺推荐</button>`
    + (verdict.ok ? "" : `<div class="packaging-part-note">${esc(verdict.reason)}</div>`);
}

/* ---------------- 平板挤出的原因文案（历史能力；包装主流程已改看 CAD 平面图，见
   Spec `packaging-business-parts-and-cad-plan-view.md` §6.1/§8） ----------------
   闭合轮廓 × 已知料厚的直线挤出仍由后端算好（ASCII STL），前端**复用既有 #viewer 与
   loadSTL()**（不新建画布、不新建第二套 THREE 初始化）；包装项目默认不再暴露这个入口，
   但结论与原因文案保留，供证据/诊断与历史数据回查。 */
// 闭集跟着后端 `packaging_part_solids.UNSUPPORTED_REASONS` 走：凹件不再是拒绝理由
// （Spec `packaging-parts-solid-coverage.md` §2.1：凹件必须耳切挤出），新增「轮廓自交」
// 与「轮廓退化」两个**真缺陷**理由 —— 都不许硬挤。
const PACKAGING_SOLID_COPY = {
  outline_open: "该件没有闭合轮廓，无法挤出",
  outline_unavailable: "图纸单位未确认，无法挤出",
  thickness_unknown: "缺厚度，无法挤出",
  self_intersecting: "该件轮廓自交（边与边相交），无法挤出",
  degenerate_polygon: "该件轮廓退化（面积为零或点重合），无法挤出",
  too_few_points: "轮廓点太少，无法挤出",
  too_many_points: "轮廓点太多，本版不做简化，暂不挤出",
};

function packagingPartSolidReason(reason) {
  return PACKAGING_SOLID_COPY[String(reason || "")] || "";
}

// 单件 3D 正文可用性（纯函数，Spec `packaging-solids-body-unusable.md` §2.2）：
// `res.ok` 只说明 HTTP 成功 —— 网关 HTML / 空正文时 `status` 是空的，那不是"这一件挤不出来"，
// 而是"这一次没读到"。有结论（ok / unsupported / 别的）→ 空串，结论仍由既有路径渲染。
function packagingSolidBodyProblemText(payload) {
  const doc = (payload && typeof payload === "object") ? payload : {};
  const status = typeof doc.status === "string" ? doc.status.trim() : "";
  return status
    ? ""
    : "这一次读不到这一件的挤出结果（正文里没有结论），请稍后重试；这不代表这一件挤不出来。";
}

// 3D 预览：先让后端现算一版（unsupported 也是结论），再复用 loadSTL 把它画出来。
async function packagingPartSolidPreview() {
  const part = currentSelectedPanelPart;
  if (!part) return { ok: false, error: { code: "no-part", message: "未选择零件。" } };
  if (String(part.outline_status || "") !== "closed") {
    const message = packagingPartSolidReason("outline_open");
    notePackagingPartSolid(message);
    return { ok: false, error: { code: "outline_open", message: message } };
  }
  const code = part.part_code || "";
  notePackagingPartSolid("正在计算 3D 挤出体…");
  let payload = {};
  try {
    const res = await fetch(`${packagingPartEndpoint()}/solid`, { method: "POST" });
    payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = (payload && payload.detail) || {};
      const message = typeof detail === "string" ? detail : String(detail.message || "");
      throw new Error(message || `3D 挤出失败（HTTP ${res.status}）`);
    }
  } catch (error) {
    const message = String((error && error.message) || error);
    notePackagingPartSolid(message);
    return { ok: false, error: { code: "solid-failed", message: message } };
  }
  const bodyProblem = packagingSolidBodyProblemText(payload);
  if (bodyProblem) {
    notePackagingPartSolid(bodyProblem);
    return { ok: false, error: { code: "solid_body_unexpected", message: bodyProblem } };
  }
  if (String(payload.status || "") !== "ok") {
    const message = packagingPartSolidReason(payload.reason)
      || `这一件暂时挤不出来（${payload.reason || "unknown"}）`;
    notePackagingPartSolid(message);
    return { ok: false, error: { code: String(payload.reason || ""), message: message } };
  }
  notePackagingPartSolid(`已挤出：${payload.triangles || 0} 个三角形 · `
    + `${payload.volume_mm3 || 0} mm³`);
  setRightPane("model");
  const label = $("viewerPartName");
  if (label) label.textContent = `${code} · 3D 预览（平板挤出）`;
  loadSTL(mediaUrl(`${packagingPartEndpoint()}/solid.stl`));
  return { ok: true, result: payload };
}

// 3D 结论写回面板：面板在 3D 预览时依然留在右栏，原因/体量都看得见。
function notePackagingPartSolid(message) {
  const host = $("packagingPartOutline");
  if (!host) return;
  const note = document.createElement("div");
  note.className = "packaging-part-note";
  note.textContent = message;
  host.appendChild(note);
}

// 图纸零件的两条下游入口：/api/projects/{pid}/requirement/packaging-parts/{code}/{process|cost}。
// 技术侧 /parts/{part_id} 在图纸项目里必然 404（图纸零件不写技术 IR），所以把数据源
// 换掉、渲染与任务轮询仍交给既有 CadInlineAnalysis。
function packagingPartEndpoint() {
  const part = currentSelectedPanelPart || {};
  return `${API}/api/projects/${currentProject}/requirement/packaging-parts/`
    + encodeURIComponent(part.part_code || "");
}

// 人工补料厚（Spec `packaging-parts-thickness-facts.md` §2.5）：料厚是下游（工艺 / 成本 /
// 3D）共同的卡点，推不出来的件必须有人能补。提交后就地更新这一行，不整页重载。
async function packagingPartSetThickness(partCode) {
  if (!currentProject || !partCode) return null;
  const input = window.prompt(`给 ${partCode} 补料厚（mm，必须 > 0）：`, "");
  if (input === null) return null;
  const value = Number(String(input).trim());
  if (!isFinite(value) || value <= 0) {
    window.alert("料厚必须是大于 0 的数字（不许用 0 表示没填）。");
    return null;
  }
  const reason = window.prompt("补录理由（可留空）：", "") || "";
  let res;
  try {
    res = await fetch(`${API}/api/projects/${currentProject}/requirement/packaging-parts/`
      + `${encodeURIComponent(partCode)}/thickness`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thickness_mm: value, reason: reason }),
    });
  } catch (error) {
    window.alert("补料厚失败：网络错误，请重试。");
    return null;
  }
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = payload && payload.detail;
    const message = (typeof detail === "string" ? detail : (detail && detail.message))
      || (payload && payload.message) || `HTTP ${res.status}`;
    window.alert(`补料厚失败：${message}`);
    return null;
  }
  // 与「补材料」逐字同范式（Spec §2.6）：先按服务端重读，读不到才退回回显补丁 —— 三态处置
  // 走共用入口（`reread.read_problem` 非空 = 读失败，保住已列出的零件）。
  const reread = await fetchPackagingParts();
  // 三态（Spec §2.2）：`reread.read_problem` 非空 = 读失败，不换文档、保住已列出的零件。
  const patch = { thickness_mm: payload.thickness_mm };
  if (payload.thickness_source) patch.thickness_source = payload.thickness_source;
  applyPackagingPartsReread(reread, partCode, patch);
  renderTree(currentIR || {});
  return payload;
}

// 人工补材料（Spec `packaging-parts-in-card-and-material-fill.md` §2.3 第 2 条）：与
// 「补料厚」同范式 —— 真图 64 件有 51 件卡在缺材料，页面上必须有地方能补，补完只重算这一件。
async function packagingPartSetMaterial(partCode) {
  if (!currentProject || !partCode) return null;
  const input = window.prompt(`给 ${partCode} 补材料（如 灰板 / 白卡纸，不能为空）：`, "");
  if (input === null) return null;
  const spec = String(input).trim();
  if (!spec) {
    window.alert("材料不能为空（不许用空串表示没填）。");
    return null;
  }
  const reason = window.prompt("补录理由（可留空）：", "") || "";
  let res;
  try {
    res = await fetch(`${API}/api/projects/${currentProject}/requirement/packaging-parts/`
      + `${encodeURIComponent(partCode)}/material`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec: spec, reason: reason }),
    });
  } catch (error) {
    window.alert("补材料失败：网络错误，请重试。");
    return null;
  }
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = payload && payload.detail;
    const message = (typeof detail === "string" ? detail : (detail && detail.message))
      || (payload && payload.message) || `HTTP ${res.status}`;
    window.alert(`补材料失败：${message}`);
    return null;
  }
  // Spec `packaging-part-manual-fill-must-land-on-the-part-row.md` §2.6：补录成功后那一行必须
  // 来自**服务端重读**（补录落回零件行之后，读回的值才是真实的、刷新后一致）；重读失败才退回
  // 用 POST 的回显打内存补丁（立刻可用，但不再是唯一来源）。
  const reread = await fetchPackagingParts();
  // 三态（Spec §2.2）：`reread.read_problem` 非空 = 读失败，不换文档、保住已列出的零件。
  const patch = { material: payload.material };
  if (payload.material_source) patch.material_source = payload.material_source;
  applyPackagingPartsReread(reread, partCode, patch);
  renderTree(currentIR || {});
  return payload;
}

// 件级轮廓出路（Spec `packaging-open-outline-part-needs-a-way-out.md` §2.3）：未闭合的件
// 以前在整条链上是死路（没有入口、也没人告诉你该干什么）。这里给两条与后端一一对应的出口，
// 与「补材料」/「补料厚」同范式：提交后按服务端重读那一份零件文档，不整页重载。
async function packagingPartOutlineExitPost(partCode, action) {
  if (!currentProject || !partCode) return null;
  const label = action === "recompute" ? "重算轮廓" : "按包围盒签字确认";
  if (action === "recompute") {
    const go = window.confirm(`用更大的搜索额度只重算 ${partCode} 这一件？（不会动其它件）`);
    if (!go) return null;
  } else {
    const go = window.confirm(
      `确认把 ${partCode} 按包围盒估算放行？\n`
      + `几何状态仍是「未闭合」，卡片上会标出这是人工签字（不是几何闭合）。`);
    if (!go) return null;
  }
  const reason = window.prompt(`${label}的理由（可留空）：`, "") || "";
  const url = `${API}/api/projects/${currentProject}/requirement/packaging-parts/`
    + `${encodeURIComponent(partCode)}/outline/${action === "recompute" ? "recompute" : "confirm"}`;
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason }),
    });
  } catch (error) {
    window.alert(`${label}失败：网络错误，请重试。`);
    return null;
  }
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = payload && payload.detail;
    const message = (typeof detail === "string" ? detail : (detail && detail.message))
      || (payload && payload.message) || `HTTP ${res.status}`;
    window.alert(`${label}失败：${message}`);
    return null;
  }
  if (action === "recompute" && !payload.changed) {
    window.alert(`${label}完成：这一件仍未闭合（${payload.outline_reason || ""}）。`
      + `${payload.message || ""}`);
  }
  const reread = await fetchPackagingParts();
  // 同上三态（`reread.read_problem` 非空 = 这一笔成功了但列表没读回来）：轮廓出路这条没有回显补丁。
  applyPackagingPartsReread(reread, partCode, null);
  renderTree(currentIR || {});
  return payload;
}

// 写后重读的三态处置（Spec `packaging-parts-reread-failure-after-write.md` §2.2）—— 三处
// 「补料厚 / 补材料 / 轮廓出路」共用这一个入口：
//   · `null`（404，端点未上线）→ 既有回显补丁路径，`currentPackagingParts` 一字不改；
//   · `reread.read_problem` 非空（读失败）→ **不许**替换文档、**不许**碰 `packagingPartsShown`，
//     打回显补丁并把读问题记在 `reread_problem` 上交给渲染（列表继续显示刚才那些零件）；
//   · 读到 → 既有 `currentPackagingParts = reread`，并清掉上一次的 `reread_problem`。
function applyPackagingPartsReread(reread, partCode, patch) {
  if (reread === null) {
    if (patch) patchPackagingPartRows(partCode, patch);
    return;
  }
  const problem = (reread && typeof reread === "object"
    && reread.read_problem && typeof reread.read_problem === "object")
    ? reread.read_problem : null;
  if (problem) {
    if (patch) patchPackagingPartRows(partCode, patch);
    currentPackagingParts = Object.assign({}, currentPackagingParts || {},
                                           { reread_problem: problem });
    return;
  }
  currentPackagingParts = reread;
  if (currentPackagingParts && typeof currentPackagingParts === "object") {
    currentPackagingParts.reread_problem = null;
  }
}

// 单件结论回填：整份文档的行、当前页的行、已累加的行都要改 —— 分页之后左栏渲染的是
// `packagingPartsShown`（Page 1 的行），只改 `doc.parts` 会让"补料厚"按钮补完还在。
function patchPackagingPartRows(partCode, patch) {
  const code = String(partCode || "");
  const lists = [(currentPackagingParts || {}).parts, (currentPackagingParts || {}).items,
                 packagingPartsShown];
  lists.forEach(list => {
    if (!Array.isArray(list)) return;
    list.forEach(row => {
      if (row && String(row.part_code || "") === code) Object.assign(row, patch);
    });
  });
}

async function packagingPartAnalyze(mode) {
  const part = currentSelectedPanelPart;
  if (!part) return { ok: false, error: { code: "no-part", message: "未选择零件。" } };
  const verdict = packagingPartProcessability(part);
  if (!verdict.ok) {
    return { ok: false, error: { code: "not-processable", message: verdict.reason } };
  }
  const host = $("analysisHost");
  if (!host || !window.CadInlineAnalysis) {
    return { ok: false,
             error: { code: "no-analysis-host", message: "当前看板没有可用的分析渲染区。" } };
  }
  exitBoardViewHost();
  const label = mode === "cost" ? "成本测算" : "工艺推荐";
  setRightPane("analysis",
               `${part.part_code || ""} ${part.name || ""} · ${label}`.trim());
  window.CadInlineAnalysis.open(mode, {
    host,
    projectId: currentProject,
    part: { part_id: part.part_id || part.part_code, name: part.name || part.part_code },
    endpointBase: packagingPartEndpoint,
    onClose: () => setRightPane("model"),
  });
  return { ok: true, result: { mode: mode, partCode: part.part_code || "" } };
}

// 板级「一键生成全部工艺推荐」在图纸项目下的口径（Spec
// `packaging-28-part-auto-resolution-and-2d-board-cleanup.md` §7/§8.10）：只遍历**业务部件**
// （默认取业务部件文档），不遍历几何分量；每一件先看有没有绑到闭合几何件，绑不到的再看权威
// 清单够不够排工艺，两条都走不通的计入 skipped 并逐条说明「为什么不能算」。
function startAllPackagingPartProcesses(rows) {
  const list = Array.isArray(rows) ? rows
    : ((currentPackagingParts && currentPackagingParts.parts) || []);
  const ready = [];
  const skipped = [];
  list.forEach(row => {
    const code = String((row && row.business_part_code) || "");
    const geometry = packagingBusinessPartDownstreamTarget(row, currentPackagingParts || {});
    if (geometry.ok) {
      ready.push({ code: code, target: String(geometry.part_code || ""), mode: "geometry" });
      return;
    }
    const authority = packagingBusinessPartProcessTarget(row);
    if (authority.ok) {
      ready.push({ code: code, target: code, mode: "authority" });
      return;
    }
    skipped.push({ part_code: code, reason: authority.message || geometry.message || "" });
  });
  if (!ready.length) {
    const detail = skipped.map(item => `${item.part_code}（${item.reason}）`).join("；");
    return { ok: false, error: { code: "no-parts",
      message: list.length ? `没有可算的业务部件：${detail}`
        : "还没有业务部件清单，请先导入权威清单或人工建立。" } };
  }
  // 逐件串行：一次只跑一件，右栏分析的标题与结论始终对得上。
  const queue = ready.slice();
  const step = () => {
    const item = queue.shift();
    if (!item) return;
    const run = item.mode === "authority"
      ? Promise.resolve(packagingBusinessPartProcessByAuthority(item.code))
      : packagingBusinessPartAnalyze("process", item.target);
    run.then(step, step);
  };
  step();
  return { ok: true, result: { total: list.length, started: ready.length, skipped: skipped } };
}

/* ---------------- 2.1 3D 覆盖率的三态（Spec packaging-parts-solid-coverage.md §2.3） ----------------
   覆盖率必须是**真值**：一件结论都没有时显示"未生成"，绝不许把 0.0 当"都失败"糊过去。 */
function packagingSolidCoverageText(doc) {
  const rows = Array.isArray(doc && doc.parts) ? doc.parts : [];
  if (!rows.length) return "3D 覆盖率未生成（还没有零件）。";
  const concluded = rows.filter(row => String((row && row.solid_status) || ""));
  if (!concluded.length) return "3D 覆盖率未生成（还没跑过批量挤出）。";
  const ok = concluded.filter(row => String(row.solid_status) === "ok").length;
  const text = `3D 覆盖率 ${Math.round(ok / rows.length * 100)}%（${ok}/${rows.length} 件可挤出）`;
  // 这份 3D 是不是**当前这一版零件**算的（Spec `packaging-solids-parts-version-binding.md` §2.3）：
  // 换版要说出来、比较不了也要说出来 —— 不许继续按"有 3D"的样式展示。
  return text + packagingSolidsStaleText(doc);
}

/* 角色是怎么查出来的（Spec `packaging-parts-role-lookup-disclosure.md` §2.4）：零件角色全
 * `unknown` 时，「语义层这一次没跑成」（可重试）与「图纸图层名不认识」（可补规则 / 人工指定）
 * 必须分家说人话 —— 今天这两种在返回体上逐字相同。返回节点；没有可说的就不给节点。 */
function packagingRoleLookupNote(doc) {
  const payload = doc || {};
  const stats = payload.stats || {};
  const state = stats.role_lookup || payload.role_lookup || {};
  const source = String(state.source || "");
  const layers = Array.isArray(state.unknown_layers) ? state.unknown_layers : [];
  const box = document.createElement("div");
  box.className = "part-role-lookup";
  if (source === "unavailable") {
    box.setAttribute("data-parts-role-lookup-unavailable", "1");
    box.textContent = state.message
      || "图层角色这一次没算出来，请稍后重试；零件尺寸不受影响。";
    return box;
  }
  if (source === "ir_layers") {
    box.setAttribute("data-parts-role-lookup-ir-layers", "1");
    box.textContent = state.message
      || "语义层这一次没算出来，角色取自图纸 IR 自带的图层角色（可重试，或人工指定角色）。";
    return box;
  }
  if (source === "semantics" && layers.length) {
    box.setAttribute("data-parts-role-lookup-unknown-layers", "1");
    box.textContent = state.message
      || ("这些图层名认不出角色：" + layers.join("、") + "（可补规则或人工指定）。");
    return box;
  }
  return null;
}

//: 3D 结论的版本对账人话（Spec `packaging-solids-parts-version-binding.md` §2.3）。
function packagingSolidsStaleText(doc) {
  const reason = String((doc && doc.solids_stale_reason) || "");
  if (!reason) return "";
  if (reason === "parts_unknown") return "；无法判断这份 3D 对应哪一版零件（没有留下版本），请重新生成后再用";
  if (doc && doc.solids_stale) {
    return `；这份 3D 是上一版零件算的（${reason}），请重新生成后再用`;
  }
  return "";
}

//: 逐件行上的一句话（同上）：`stale` 为真 = 这一件的 3D 是上一版零件算的。
/* ---------------- 包装 DWG 右栏：CAD 平面图查看器（Spec
   packaging-business-parts-and-cad-plan-view.md §6） ----------------
   包装 DWG 是二维刀模 / 展开图：右栏显示图纸**原本的** CAD 平面图（不是只画一个 bbox、
   也不是简化 polygon），点左侧业务部件时整张图仍在、其它图元降权、绑定图元高亮并缩放到
   部件范围；点图元可反查所属业务部件，未绑定的图元明说「几何证据，尚未归属业务部件」。

   数据全部来自服务端（CAD IR 派生的图元 / 图层 / 范围 + 业务部件绑定文档）：前端既不
   重新解析 DWG，也不在浏览器端重算业务尺寸、不重新拆件。

   非包装项目（industry !== "packaging"）的既有 3D 与 loadSTL() 一字不改 —— 平面图只接管
   packaging（图纸链路 drawing_flow）项目的右栏。 */
const PACKAGING_CAD_PLAN_LABEL = "CAD 平面图";
const PACKAGING_CAD_PLAN_ALIASES = ["包装展开图", "包装刀模图"];
const PACKAGING_CAD_PLAN_UNBOUND = "几何证据，尚未归属业务部件";
const PACKAGING_CAD_PLAN_EMPTY = "这份图纸还没有可显示的 CAD 图元。";
// 有图元但一个坐标都没有（证据层还没带上绘图包络）时的空态：不许留一块空白画布。
const PACKAGING_CAD_PLAN_NO_COORDS = "这批零件还没有图纸坐标，暂时画不出平面图（坐标要等 CAD IR 把折线顶点带进来）。";
// 平面图「读不到」时的文案（Spec `packaging-cad-plan-read-failure.md` §C1）：与左栏零件文档 /
// 业务部件清单同一套口径 —— 读失败要自己的一句话，`code` 为空就等于没有这件事；
// 有状态码说 HTTP，没有（含 `0`、网络异常）说"网络错误"，**不**贴浏览器原生英文文本。
function packagingCadPlanReadProblemText(problem) {
  const row = (problem && typeof problem === "object") ? problem : {};
  if (!String(row.code || "").trim()) return "";
  const status = Number(row.status) || 0;
  return status > 0
    ? `暂时读不到 CAD 平面图（HTTP ${status}），请稍后重试；这不代表这份图纸没有图元`
    : "暂时读不到 CAD 平面图（网络错误），请稍后重试；这不代表这份图纸没有图元";
}

// 平面图空态文案的**唯一出处**（Spec §C2）：读失败优先于 gap 文案与 `PACKAGING_CAD_PLAN_EMPTY`；
// 没有读失败迹象时与今天逐字等价（`gap.message || PACKAGING_CAD_PLAN_EMPTY`）。
function packagingCadPlanEmptyText(doc) {
  const problem = (doc && doc.read_problem && typeof doc.read_problem === "object")
    ? doc.read_problem : null;
  if (problem) return packagingCadPlanReadProblemText(problem);
  const gap = (doc && (doc.business_parts_gap || doc.gap)) || {};
  if (gap && gap.message) return String(gap.message);
  return PACKAGING_CAD_PLAN_EMPTY;
}
// 业务部件面板的轮廓说明（Spec `packaging-business-part-plan-click-and-bound-outline.md` §C2）：
// 画的是**绑定分量**的形状，业务尺寸仍以权威资料为准 —— 两者不许混为一谈。
const PACKAGING_BOUND_OUTLINE_NOTE = "这是绑定分量的形状；业务尺寸以权威资料为准。";
const PACKAGING_CAD_LAYER_COLORS = {
  cut: "#1f6feb", half_cut: "#2ea043", crease: "#d29922", v_groove: "#a371f7",
  glue_flap: "#0a3069", print: "#57606a", bleed: "#8b949e", frame: "#6e7781",
  hole: "#cf222e", unknown: "#8b949e",
};
let currentPackagingCadPlan = null;
let currentPackagingCadPlanBox = null;
let currentPackagingBusinessPartCode = "";

function packagingCadPlanViewer() {
  return $("packagingCadPlanViewer");
}

// 平面图只在包装（`drawing_flow`）项目里接管右栏；其它项目的 3D 视图不动。
function packagingCadPlanApplies() {
  return currentDrawingEntry === "drawing_flow";
}

function packagingCadPlanLabel() {
  return packagingCadPlanApplies() ? PACKAGING_CAD_PLAN_LABEL : "";
}

// CAD 平面图的取数口（Spec `packaging-28-part-auto-resolution-and-2d-board-cleanup.md` §5）：
// 读服务端从 CAD IR 派生的**完整**二维场景（制造曲线 / 压痕 / 标注 / 图框，每个图元带稳定
// `cad_entity_id`，另有 `layer_visibility` 图层显隐），而不是把过滤后的分量包围盒拼成一张图。
function packagingCadSceneEndpoint() {
  return `${API}/api/projects/${currentProject}/requirement/packaging-geometry`;
}

// 一组 bbox（`[minX, minY, maxX, maxY]`）的并集；坐标不可用时回 null（不猜）。
function packagingCadPlanRange(boxes) {
  let box = null;
  (boxes || []).forEach(raw => {
    if (!Array.isArray(raw) || raw.length < 4) return;
    const values = raw.slice(0, 4).map(Number);
    if (values.some(value => !Number.isFinite(value))) return;
    if (!box) { box = values.slice(); return; }
    box[0] = Math.min(box[0], values[0]); box[1] = Math.min(box[1], values[1]);
    box[2] = Math.max(box[2], values[2]); box[3] = Math.max(box[3], values[3]);
  });
  if (!box) return null;
  return { x: box[0], y: box[1], x2: box[2], y2: box[3],
           width: Math.max(box[2] - box[0], 1e-6), height: Math.max(box[3] - box[1], 1e-6) };
}

// 一件图元的画图框（Spec `packaging-cad-plan-drawing-coordinates.md` §C3）：
// `drawing_bbox` 是图纸坐标系里的包络，`bbox` 只是老文档/技术链路的兜底；都没有回 null（不猜）。
function packagingCadPlanComponentBox(component) {
  const row = component || {};
  const raw = row.drawing_bbox || row.bbox;
  if (!Array.isArray(raw) || raw.length < 4) return null;
  const values = raw.slice(0, 4).map(Number);
  return values.some(value => !Number.isFinite(value)) ? null : values;
}

// 平面图点选的落点（Spec `packaging-business-part-plan-click-and-bound-outline.md` §C1）：
// `data-business-part` 装的是**业务部件编码** —— 有归属开业务部件面板，没有就走未归属提示；
// 以前这里拿业务编码去调 `selectPackagingPart()`（几何零件通道），必然 404。
function packagingCadPlanClickTarget(businessPartCode) {
  const code = String(businessPartCode || "").trim();
  return code ? { kind: "business", code } : { kind: "unbound", code: "" };
}

// 业务部件绑定的分量（Spec §C2）：只按 `component_ids` 精确取（顺序跟证据层），取不到回空。
function packagingBusinessPartComponents(binding, components) {
  const ids = ((binding || {}).component_ids || []).map(id => String(id));
  if (!ids.length) return [];
  const wanted = new Set(ids);
  return (Array.isArray(components) ? components : [])
    .filter(component => wanted.has(String((component || {}).component_id || "")));
}

// 业务部件面板的轮廓（Spec §C2）：复用平面图那套取框/渲染/翻转，不在浏览器端算几何；
// 画不出来（没绑定 / 没坐标 / 一件都拼不出）就回空串，由调用方给状态文案。
function packagingBusinessPartOutlineHtml(binding, doc) {
  const evidence = (doc && doc.geometry_evidence) || {};
  const components = packagingBusinessPartComponents(binding, evidence.components);
  if (!components.length) return "";
  const range = packagingCadPlanRange(components.map(packagingCadPlanComponentBox));
  if (!range) return "";
  // 逐件调**单件渲染**再拼（`## 373` / `## 417` 的沙箱依赖清单里给的就是单件那一个：
  // `## 451` 一度改成整组渲染 `packagingCadPlanComponentsSvg()`，抽具名函数真跑时就
  // `not defined` 了）。**显式累加**而不是把分量数组直接映射到单件函数：`## 451` 的守卫把
  // 那种写法视为"拿分量盒拼左栏大图"，全文件不许再出现（左栏一律整张 CAD 图）；
  // 这里是面板的「绑定分量轮廓」，逐件拼与整组拼的输出逐字相同。
  let drawn = "";
  components.forEach(component => { drawn += packagingCadPlanComponentSvg(component); });
  if (!drawn) return "";
  // 折线被截断时在图上补一句（Spec `packaging-cad-plan-polyline-segments.md` §C5）：
  // 句子只由 `packagingCadPlanTruncationNote()` 产出，这里不另写文案。
  const notes = [];
  components.forEach(component => {
    const note = packagingCadPlanTruncationNote(component);
    if (note && notes.indexOf(note) < 0) notes.push(note);
  });
  const suffix = notes.map(text => `<div class="packaging-part-note"`
    + ` data-qqOutlineTruncated="1">${esc(text)}</div>`).join("");
  return `<svg class="packaging-part-svg" viewBox="${esc(packagingCadPlanViewBox(range))}"`
    + ` preserveAspectRatio="xMidYMid meet" role="img" aria-label="绑定分量的形状">${drawn}</svg>`
    + suffix;
}

// SVG 的 y 轴向下、DWG 的 y 轴向上：翻一次，图纸方向才与 CAD 里一致。
function packagingCadPlanViewBox(range) {
  if (!range) return "0 0 1 1";
  const pad = Math.max(range.width, range.height) * 0.02;
  return `${range.x - pad} ${-range.y2 - pad} ${range.width + pad * 2} ${range.height + pad * 2}`;
}

function fitPackagingCadPlan(range) {
  const host = packagingCadPlanViewer();
  const box = range || currentPackagingCadPlanBox;
  if (!host || !box) return null;
  const svg = host.querySelector("svg");
  if (!svg) return null;
  svg.setAttribute("viewBox", packagingCadPlanViewBox(box));
  return svg.getAttribute("viewBox");
}

// 选中业务部件：绑定图元高亮、缩放到部件范围；整张图仍在（Spec §6.2）。
function highlightPackagingBusinessPart(partCode, entity_ids) {
  const host = packagingCadPlanViewer();
  currentPackagingBusinessPartCode = String(partCode || "");
  if (!host) return { part: currentPackagingBusinessPartCode, targets: 0 };
  const wanted = new Set((entity_ids || []).map(item => String(item)));
  const boxes = [];
  host.querySelectorAll("[data-component-id], [data-cad-entity-id]").forEach(node => {
    const owned = node.getAttribute("data-business-part") || "";
    const componentId = String(node.getAttribute("data-component-id")
                               || node.getAttribute("data-cad-entity-id") || "");
    const match = wanted.size
      ? (wanted.has(componentId) || wanted.has(String(node.getAttribute("data-component-ref") || "")))
      : Boolean(owned) && owned === currentPackagingBusinessPartCode;
    node.classList.toggle("is-dimmed", wanted.size > 0 && !match);
    node.classList.toggle("is-highlighted", Boolean(match));
    if (match) boxes.push(node.getAttribute("data-bbox"));
  });
  const range = packagingCadPlanRange(boxes.map(text => String(text || "").split(",").map(Number)));
  if (range) fitPackagingCadPlan(range);
  return { part: currentPackagingBusinessPartCode, targets: boxes.length };
}

// 闭合件的真实轮廓环点串（Spec `packaging-cad-plan-true-outline-polygons.md` §C2）：
// 只认闭合件 + 至少 3 个点，逐点写成 `x,-y`（平面图的 viewBox 已经翻过 y 轴）；有一点不可用就回空串。
function packagingCadPlanOutlinePoints(component) {
  const row = component || {};
  const raw = Array.isArray(row.outline_points) ? row.outline_points : [];
  if (String(row.outline_status || "") !== "closed" || raw.length < 3) return "";
  const points = raw.map(point => {
    if (!Array.isArray(point) || point.length < 2) return "";
    const x = Number(point[0]);
    const y = Number(point[1]);
    return (Number.isFinite(x) && Number.isFinite(y)) ? `${x},${-y}` : "";
  });
  if (points.some(item => !item)) return "";
  return points.join(" ");
}

// 件内实体的折线段（Spec `packaging-cad-plan-polyline-segments.md` §C3）：每段一串 `x,-y`
// （平面图的 viewBox 已翻过 y 轴，与轮廓环点同口径）；不足 2 点 / 有不可用坐标的段整段丢掉。
function packagingCadPlanSegmentPolylines(component) {
  const row = component || {};
  const raw = Array.isArray(row.segments) ? row.segments : [];
  const lines = [];
  raw.forEach(segment => {
    if (!Array.isArray(segment) || segment.length < 2) return;
    const points = segment.map(point => {
      if (!Array.isArray(point) || point.length < 2) return "";
      const x = Number(point[0]);
      const y = Number(point[1]);
      return (Number.isFinite(x) && Number.isFinite(y)) ? `${x},${-y}` : "";
    });
    if (points.some(item => !item)) return;
    lines.push(points.join(" "));
  });
  return lines;
}

// 截断就不许装成画全了（Spec `packaging-cad-plan-polyline-segments.md` §C4）：没截断一个字都不说。
function packagingCadPlanTruncationNote(component) {
  const row = component || {};
  if (row.segments_truncated !== true) return "";
  const shown = packagingCadPlanSegmentPolylines(row).length;
  const raw = Number(row.segments_total);
  const total = (Number.isFinite(raw) && raw > 0) ? raw : shown;
  return `这一件的折线被截断（原 ${total} 段，图上 ${shown} 段），形状仅供定位。`;
}

// 一组几何分量 → SVG 片段（业务部件面板的"绑定分量"轮廓用；左栏的正图走完整场景）。
function packagingCadPlanComponentsSvg(components) {
  const list = Array.isArray(components) ? components : [];
  return list.map(row => packagingCadPlanComponentSvg(row)).filter(Boolean).join("");
}

function packagingCadPlanComponentSvg(component) {
  const range = packagingCadPlanRange([packagingCadPlanComponentBox(component)]);
  if (!range) return "";
  const role = String((component && component.role) || "unknown");
  const color = PACKAGING_CAD_LAYER_COLORS[role] || PACKAGING_CAD_LAYER_COLORS.unknown;
  const layers = ((component && component.layers) || []).join("、");
  // 多边形与矩形共用同一套数据属性：高亮/缩放/点选反查两套形状一视同仁。
  const attributes = ` data-component-id="${esc(String(component.component_id || ""))}"`
    + ` data-component-ref="${esc(String(component.geometry_component_ref || ""))}"`
    + ` data-business-part="${esc(String(component.business_part_code || ""))}"`
    + ` data-bbox="${esc([range.x, range.y, range.x2, range.y2].join(","))}"`
    + ` fill="none" stroke="${esc(color)}" stroke-width="1"`
    + ` data-layer="${esc(layers)}" data-role="${esc(role)}"`;
  const points = packagingCadPlanOutlinePoints(component);
  if (points) {
    return `<polygon class="packaging-cad-plan-entity" points="${esc(points)}"${attributes}></polygon>`;
  }
  // 没有环、但有顶点坐标 → 按实体折线画（Spec `packaging-cad-plan-polyline-segments.md` §C5）：
  // 高亮 / 缩放 / 点选那套 data 属性与方框**逐字同形**。
  const lines = packagingCadPlanSegmentPolylines(component);
  if (lines.length) {
    const flag = esc(String(component.segments_truncated === true ? 1 : 0));
    return lines.map((line, index) => `<polyline class="packaging-cad-plan-entity"`
      + ` points="${esc(line)}" data-segment="${index}"`
      + ` data-segments-truncated="${flag}"${attributes}></polyline>`).join("");
  }
  return `<rect class="packaging-cad-plan-entity"${attributes}`
    + ` x="${esc(String(range.x))}" y="${esc(String(-range.y2))}"`
    + ` width="${esc(String(range.width))}" height="${esc(String(range.height))}"></rect>`;
}

//: 完整 CAD 场景里一个图元的画法（Spec §5）：稳定 `cad_entity_id` + 图层 + 业务部件归属，
//: `layer_visibility` 里标了 `false` 的图层不画（显隐是**视图**，不改几何、不丢图元）。
function packagingCadSceneEntitySvg(entity, visibility) {
  const row = (entity && typeof entity === "object") ? entity : {};
  const layer = String(row.layer || "");
  if (visibility && visibility[layer] === false) return "";
  const bbox = Array.isArray(row.bbox) && row.bbox.length >= 4 ? row.bbox.slice(0, 4).join(",") : "";
  const color = PACKAGING_CAD_LAYER_COLORS[row.role] || PACKAGING_CAD_LAYER_COLORS.unknown;
  const data = ` data-cad-entity-id="${esc(String(row.cad_entity_id || ""))}"`
    + ` data-layer="${esc(layer)}" data-bbox="${esc(bbox)}"`
    + ` data-business-part="${esc(String(row.business_part_code || ""))}"`;
  // 文字图元（Spec §5「必要文字」）：IR 只留插入点与字高，画法是 `<text>`；坐标同样翻 y。
  if (String(row.kind || "") === "text") {
    const text = String(row.text || "");
    const x = Number(row.x);
    const y = Number(row.y);
    if (!text || !Number.isFinite(x) || !Number.isFinite(y)) return "";
    const size = Number(row.height) > 0 ? Number(row.height) : 2.5;
    return `<text class="packaging-cad-scene-text"${data} fill="${esc(color)}"`
      + ` x="${esc(String(x))}" y="${esc(String(-y))}"`
      + ` font-size="${esc(String(size))}">${esc(text)}</text>`;
  }
  const points = (Array.isArray(row.points) ? row.points : []).map(pair => {
    const values = Array.isArray(pair) ? pair : [];
    const x = Number(values[0]);
    const y = Number(values[1]);
    return (Number.isFinite(x) && Number.isFinite(y)) ? `${x},${-y}` : "";
  }).filter(Boolean);
  if (points.length < 2) return "";
  const attributes = data + ` fill="none" stroke="${esc(color)}" stroke-width="1"`;
  if (row.closed === true && points.length >= 3) {
    return `<polygon class="packaging-cad-scene-entity" points="${esc(points.join(" "))}"${attributes}></polygon>`;
  }
  return `<polyline class="packaging-cad-scene-entity" points="${esc(points.join(" "))}"${attributes}></polyline>`;
}

// 完整 CAD 场景 → 右栏（Spec §5）：图元全部来自服务端的 CAD IR 派生场景，前端只画与显隐。
function renderPackagingCadScene(host, doc, scene) {
  const entities = Array.isArray(scene && scene.entities) ? scene.entities : [];
  if (!host || !entities.length) return null;
  const visibility = (scene && scene.layer_visibility && typeof scene.layer_visibility === "object")
    ? scene.layer_visibility : {};
  const owners = {};
  ((doc && doc.business_parts) || []).forEach(row => {
    const binding = (row && row.geometry_binding) || {};
    (binding.entity_ids || []).forEach(id => { owners[String(id)] = String((row && row.business_part_code) || ""); });
    (binding.component_ids || []).forEach(id => { owners[String(id)] = String((row && row.business_part_code) || ""); });
  });
  entities.forEach(entity => {
    if (entity && !entity.business_part_code) {
      entity.business_part_code = owners[String(entity.cad_entity_id || "")] || "";
    }
  });
  const boxes = entities.map(entity => (entity || {}).bbox).filter(Boolean);
  const range = packagingCadPlanRange(boxes)
    || packagingCadPlanRange([(scene && scene.extents) || null]);
  if (!range) {
    host.innerHTML = `<div class="view-3d-placeholder">${esc(PACKAGING_CAD_PLAN_NO_COORDS)}</div>`;
    return null;
  }
  currentPackagingCadPlanBox = range;
  const drawn = entities.map(entity => packagingCadSceneEntitySvg(entity, visibility)).filter(Boolean).join("");
  const layers = Object.keys(visibility).sort();
  const toggles = layers.map(layer => `<label class="packaging-cad-layer-toggle">`
    + `<input type="checkbox" data-layer-toggle="${esc(layer)}"`
    + `${visibility[layer] === false ? "" : " checked"}> ${esc(layer)}</label>`).join("");
  const total = Number(scene.entity_total || entities.length) || entities.length;
  host.innerHTML = `<div class="packaging-cad-plan-hint">${esc(PACKAGING_CAD_PLAN_LABEL)}`
    + ` · 完整场景 ${esc(String(entities.length))}/${esc(String(total))} 个图元</div>`
    + (toggles ? `<div class="packaging-cad-layer-bar">图层：${toggles}</div>` : "")
    + `<svg class="packaging-cad-plan-svg" role="img" aria-label="${esc(PACKAGING_CAD_PLAN_LABEL)}"`
    + ` viewBox="${esc(packagingCadPlanViewBox(range))}"`
    + ` preserveAspectRatio="xMidYMid meet">${drawn}</svg>`;
  host.querySelectorAll("[data-layer-toggle]").forEach(input => {
    input.addEventListener("change", () => {
      visibility[input.getAttribute("data-layer-toggle")] = Boolean(input.checked);
      host.querySelectorAll(`[data-cad-entity-id][data-layer="${input.getAttribute("data-layer-toggle")}"]`)
        .forEach(node => { node.hidden = !input.checked; });
    });
  });
  const svg = host.querySelector("svg");
  if (svg) {
    svg.addEventListener("click", event => {
      const node = (event.target && event.target.closest)
        ? event.target.closest("[data-cad-entity-id], [data-component-id]") : null;
      if (!node) return;
      const target = packagingCadPlanClickTarget(node.getAttribute("data-business-part"));
      if (target.kind === "business") { openPackagingBusinessPart(target.code); return; }
      notePackagingPartPanel(PACKAGING_CAD_PLAN_UNBOUND);
    });
  }
  if (currentPackagingBusinessPartCode) openPackagingBusinessPart(currentPackagingBusinessPartCode);
  return doc;
}

function renderPackagingCadPlan(doc) {
  const host = packagingCadPlanViewer();
  currentPackagingCadPlan = (doc && typeof doc === "object") ? doc : null;
  if (!host) return null;
  // 读失败第一优先（Spec §C3）：不许被 gap 文案 / 「还没有图元」/ 「有图元没坐标」抢先。
  const readProblem = (doc && doc.read_problem && typeof doc.read_problem === "object")
    ? doc.read_problem : null;
  if (readProblem) {
    host.innerHTML = `<div class="view-3d-placeholder">`
      + `${esc(packagingCadPlanReadProblemText(readProblem))}</div>`;
    currentPackagingCadPlanBox = null;
    return null;
  }
  // 完整 CAD 场景优先（Spec §5）：真有场景就画场景，不再用过滤后的分量包围盒拼图。
  const scene = (doc && doc.cad_scene && typeof doc.cad_scene === "object") ? doc.cad_scene : null;
  if (Array.isArray(scene && scene.entities) && scene.entities.length) {
    return renderPackagingCadScene(host, doc, scene);
  }
  const evidence = (doc && doc.geometry_evidence) || {};
  const components = Array.isArray(evidence.components) ? evidence.components.slice() : [];
  const owners = {};
  ((doc && doc.business_parts) || []).forEach(row => {
    const binding = (row && row.geometry_binding) || {};
    (binding.component_ids || []).forEach(id => {
      owners[String(id)] = String((row && row.business_part_code) || "");
    });
  });
  components.forEach(component => {
    component.business_part_code = owners[String(component.component_id || "")] || "";
  });
  const gap = (doc && (doc.business_parts_gap || doc.gap)) || {};
  if (!components.length) {
    host.innerHTML = `<div class="view-3d-placeholder">${esc(packagingCadPlanEmptyText(doc))}</div>`;
    return null;
  }
  currentPackagingCadPlanBox = packagingCadPlanRange(components.map(packagingCadPlanComponentBox));
  if (!currentPackagingCadPlanBox) {
    // Spec §C4：有分量、却一个坐标都没有 —— 说清原因，不留空白。
    host.innerHTML = `<div class="view-3d-placeholder">${esc(PACKAGING_CAD_PLAN_NO_COORDS)}</div>`;
    return null;
  }
  const drawn = packagingCadPlanComponentsSvg(components);
  const total = Number(evidence.component_total || components.length) || components.length;
  const hasBusinessParts = Boolean((doc && doc.business_parts) || []).length;
  host.innerHTML = `<div class="packaging-cad-plan-hint">${esc(PACKAGING_CAD_PLAN_LABEL)}`
    + ` · 图元 ${esc(String(components.length))}/${esc(String(total))}</div>`
    + `<svg class="packaging-cad-plan-svg" role="img" aria-label="${esc(PACKAGING_CAD_PLAN_LABEL)}"`
    + ` viewBox="${esc(packagingCadPlanViewBox(currentPackagingCadPlanBox))}"`
    + ` preserveAspectRatio="xMidYMid meet">${drawn}</svg>`
    + (hasBusinessParts ? "" : `<div class="packaging-cad-plan-note">${esc(gap.message || "")}</div>`);
  const svg = host.querySelector("svg");
  if (svg) {
    svg.addEventListener("click", event => {
      const node = (event.target && event.target.closest)
        ? event.target.closest("[data-component-id]") : null;
      if (!node) return;
      const target = packagingCadPlanClickTarget(node.getAttribute("data-business-part"));
      if (target.kind === "business") { openPackagingBusinessPart(target.code); return; }
      notePackagingPartPanel(PACKAGING_CAD_PLAN_UNBOUND);
    });
  }
  // 证据后到（首点竞态）：已经选中的业务部件要用新到的证据重画轮廓与依据（Spec §C4）。
  if (currentPackagingBusinessPartCode) openPackagingBusinessPart(currentPackagingBusinessPartCode);
  return currentPackagingCadPlan;
}

// 平面图里点到未绑定图元的提示（Spec §6.2）：不假装它属于某个业务部件。
function notePackagingPartPanel(message) {
  const host = $("packagingPartFacts");
  if (host) host.textContent = String(message || "");
  return String(message || "");
}

// 三态分家（Spec `packaging-cad-plan-read-failure.md` §C4）：
//   · 404（路由未上线 / 还没生成）→ 按「还没有几何证据」空态渲染，**不**写成读失败；
//   · 其它非 2xx 与 `fetch` 抛异常 → 走 `read_problem`（空文档形状：图元一律为空，
//     错误**只**放 `read_problem`，不许塞进 `geometry_evidence.components`）；
// 三种都照旧 `return null`（既有调用方契约不变）。
async function loadPackagingCadPlan() {
  if (!packagingCadPlanApplies()) return null;
  const host = packagingCadPlanViewer();
  if (host) host.innerHTML = `<div class="view-3d-placeholder">正在读取 CAD 平面图…</div>`;
  const problemDoc = (status) => ({
    geometry_evidence: {components: []}, business_parts: [], business_parts_gap: {},
    built: false,
    read_problem: {code: "cad_plan_unavailable", status: Number(status) || 0, message: ""},
  });
  let res = null;
  try {
    res = await fetch(packagingCadSceneEndpoint());
  } catch (error) {
    return renderPackagingCadPlan(problemDoc(0));
  }
  if (Number(res.status) === 404) {
    return renderPackagingCadPlan({geometry_evidence: {components: []}, business_parts: [],
                                   business_parts_gap: {}, built: false});
  }
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) return renderPackagingCadPlan(problemDoc(res.status));
  return renderPackagingCadPlan(payload);
}

// 选中即高亮（Spec `packaging-business-parts-and-cad-plan-view.md` §6.2）：业务部件用它绑定的
// 分量/图元，几何件用它自己的分量引用 —— 高亮只做视图，**不**在浏览器端重算尺寸、不拆件。
function highlightPackagingBusinessPartSelection(partCode, payload) {
  const binding = (payload && payload.geometry_binding) || {};
  const refs = (binding.component_ids || []).concat(binding.entity_ids || []);
  if (!refs.length && payload && payload.part) {
    refs.push(payload.part.component_id, payload.part.geometry_component_ref);
  }
  return highlightPackagingBusinessPart(partCode, refs.filter(Boolean));
}

function packagingPartSolidStaleText(part) {
  const reason = String((part && part.stale_reason) || "");
  if (!reason || !(part && part.stale)) {
    return reason === "parts_unknown" ? "3D：无法判断对应哪一版零件" : "";
  }
  return `3D 是上一版零件算的（${reason}），请重新生成`;
}

// 整份零件文档一次算完：POST .../requirement/packaging-parts/solids。
// 单件 unsupported 是结论不是错误（后端回 200），跑完把左栏与覆盖率一起刷新。
// 批量 3D 正文可用性（纯函数，Spec `packaging-solids-body-unusable.md` §2.1）：
// 正文不可用时**不许**按"0 件可挤出"渲染 —— `part_total` 是"这批一共几件"的唯一来源，
// 读不到就是读不到（分母为 0 的"0%（0/0）"看起来完全像一个合法结论）。
function packagingSolidsBatchFacts(payload) {
  const doc = (payload && typeof payload === "object" && !Array.isArray(payload)) ? payload : {};
  const stats = (doc.stats && typeof doc.stats === "object" && !Array.isArray(doc.stats))
    ? doc.stats : null;
  const raw = stats ? stats.part_total : undefined;
  const total = (typeof raw === "number" || (typeof raw === "string" && raw.trim() !== ""))
    ? Number(raw) : NaN;
  if (!stats || !isFinite(total) || total < 0) {
    return { available: false, ok_total: 0, part_total: 0, ratio: 0, unsupported_total: 0,
             headline: "",
             message: "这一次读不到批量挤出结果（正文里没有覆盖率），请稍后重试；"
               + "这不代表一件都挤不出来。" };
  }
  const ok = Number(stats.ok_total) || 0;
  const ratio = Number(stats.solid_ok_ratio) || 0;
  const unsupported = Number(stats.unsupported_total) || 0;
  return { available: true, ok_total: ok, part_total: total, ratio: ratio,
           unsupported_total: unsupported,
           headline: `3D 覆盖率 ${Math.round(ratio * 100)}%（${ok}/${total} 件可挤出）`,
           message: "" };
}

async function packagingPartsSolidBatch() {
  if (!currentProject) return { ok: false, error: { code: "no-project", message: "还没有打开项目。" } };
  status("正在批量计算 3D 挤出体…", true);
  let payload = {};
  try {
    const res = await fetch(`${API}/api/projects/${currentProject}/requirement/packaging-parts/solids`,
                            { method: "POST" });
    payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = (payload && payload.detail) || {};
      const message = typeof detail === "string" ? detail : String(detail.message || "");
      throw new Error(message || `批量挤出失败（HTTP ${res.status}）`);
    }
  } catch (error) {
    const message = String((error && error.message) || error);
    status(message);
    return { ok: false, error: { code: "solids-failed", message: message } };
  }
  const facts = packagingSolidsBatchFacts(payload);
  if (!facts.available) {
    status(facts.message);
    return { ok: false, error: { code: "solids_body_unexpected", message: facts.message } };
  }
  await refreshPackagingParts();
  // 可用正文才渲染「3D 覆盖率 …%」这句（逐字由 packagingSolidsBatchFacts().headline 给出）。
  status(facts.headline);
  return { ok: true, result: payload };
}

/* ---------------- 2.1 左栏空态的原因文案（Spec C4） ---------------- */
// 纯函数：有零件 → 空串；否则逐字给服务端的不可用原因 + 前置条件（带码与下一步动作）。
function packagingPartsEmptyText(partsDoc, preconditions, flowState) {
  const doc = (partsDoc && typeof partsDoc === "object") ? partsDoc : {};
  // 读失败优先于其它所有空态（Spec `packaging-parts-read-failure-empty-state.md` §2.2）：
  // "这一趟读不到零件文档"与"确实没有零件"/"还没算过"是三件事 —— 前者重跑一键解析不会有帮助
  // （服务端此刻正返回 500），所以必须自己一句话，且不许被下面的分支抢先。
  const problem = (doc.read_problem && typeof doc.read_problem === "object")
    ? doc.read_problem : null;
  if (problem) {
    const status = Number(problem.status) || 0;
    return status > 0
      ? `暂时读不到零件文档（HTTP ${status}），请稍后重试；这不代表这份图纸没有零件`
      : "暂时读不到零件文档（网络错误），请稍后重试；这不代表这份图纸没有零件";
  }
  const parts = Array.isArray(doc.parts) ? doc.parts : [];
  if (parts.length) return "";
  const segments = [];
  const unavailable = Array.isArray(doc.unavailable) ? doc.unavailable : [];
  unavailable.forEach(item => {
    const message = String((item && item.message) || "").trim();
    if (message) segments.push(message);
  });
  const gaps = Array.isArray(preconditions) ? preconditions : [];
  gaps.forEach(item => {
    const code = String((item && item.code) || "").trim();
    const message = String((item && item.message) || "").trim();
    const action = String((item && item.action) || "").trim();
    if (!code && !message) return;
    segments.push("[" + code + "] " + message + (action ? " → " + action : ""));
  });
  // 有文档、但一件可用零件都没有时（`total == 0`）必须明说（Spec
  // `packaging-parts-list-visibility-and-kinds.md` §2.5）：空表格不等于"没有零件"。
  const total = Number((doc && doc.total) !== undefined ? doc.total : NaN);
  if (!segments.length && doc.built === true && total === 0) {
    return "这份图纸没有可用的零件。";
  }
  // 这一刻读不到 ≠ 还没生成（Spec `packaging-parts-entry-readback.md` §4 B3）：链路里已经有
  // 这一版零件时，必须给证据并明说"不用重新解析图纸"——「请先跑一键解析图纸」是错的下一步。
  // 证据的取法（`parts_extract` 这一步 completed，且 `detail` 里有 `parts_id` / 正的
  // `parts_total`；`blocked` 不算"已经产出过"）就地写在函数里：这个纯函数被 `node` 单独抽出来
  // 执行，不许依赖同文件的其它函数（Spec `packaging-parts-entry-readback.md` §4 B3）。
  const flow = (flowState && typeof flowState === "object") ? flowState : {};
  const steps = Array.isArray(flow.steps) ? flow.steps : [];
  const step = steps.find(item => String((item || {}).step_id || "") === "parts_extract");
  const detail = (step && step.detail && typeof step.detail === "object") ? step.detail : {};
  const evidenceId = (step && String(step.status || "") === "completed")
    ? String(detail.parts_id || "").trim() : "";
  const evidenceTotal = (step && String(step.status || "") === "completed")
    ? Number(detail.parts_total) : NaN;
  const evidenceCount = (isFinite(evidenceTotal) && evidenceTotal > 0) ? evidenceTotal : 0;
  if (!segments.length && (evidenceId || evidenceCount)) {
    const parts = [];
    if (evidenceId) parts.push(`parts_id ${evidenceId}`);
    if (evidenceCount) parts.push(`共 ${evidenceCount} 件`);
    return `这一刻读不到零件清单；链路里已经有这一版零件（${parts.join("，")}），`
      + "请稍后重试或刷新页面 —— 不用重新解析图纸。";
  }
  if (!segments.length) return "零件文档还没生成，请先跑一键解析图纸。";
  return segments.join("；");
}

// "继续加载"这一页读不出来时的原因文案（Spec `packaging-parts-pagination-read-failure.md` §2.1）。
// 纯函数：有 `code` 才拼句子；有状态码说 HTTP，没有（含 `0`、网络异常）说"网络错误"。
// 与第一页的 `parts_unavailable` 分家：这一页读不到**不等于**整份零件文档没有，已列出的零件一件不动。
function packagingPartsPageReadProblemText(problem) {
  const row = (problem && typeof problem === "object") ? problem : null;
  const code = row ? String(row.code || "").trim() : "";
  if (!code) return "";
  const status = Number(row.status) || 0;
  return status > 0
    ? `这一页零件没读出来（HTTP ${status}），已列出的零件不受影响；点"继续加载"重试`
    : '这一页零件没读出来（网络错误），已列出的零件不受影响；点"继续加载"重试';
}

// 写后重读失败的那句话（纯函数，Spec `packaging-parts-reread-failure-after-write.md` §2.1）：
// 一笔补录**已经提交成功**，只是列表没能重新读回来 —— 不许说成"补录失败"，也不许假装列表已刷新。
// 只认零件文档那两条读失败码；表外码 / 空对象 / null 都不拼这句话。码表就地写进函数体
// （证据取法用 `node -e` 单独抽这一个函数执行，不许依赖同文件其它常量）。
function packagingPartsRereadProblemText(problem) {
  const row = (problem && typeof problem === "object") ? problem : null;
  const code = row ? String(row.code || "").trim() : "";
  if (code !== "parts_unavailable" && code !== "business_parts_unavailable") return "";
  const status = Number(row.status) || 0;
  return `刚才这一笔已经提交成功，但列表没能重新读回来（${status > 0 ? "HTTP " + status : "网络错误"}），`
    + "下面显示的是本地回显；刷新页面即可核对服务端的值";
}

// 单件详情「读不到」/「没这件」的文案（Spec
// `packaging-part-detail-read-failure.md` §C1）：判据顺序固定 —— **先认码、再认状态**。
// 只有后端的 `PACKAGING_PART_NOT_FOUND` 才能说"这一件没了"（重跑过解析），
// 其它情况一律说"稍后重试"，**不**把「读不到」说成「没这件」。
// 纯函数：体内无 DOM / 无 `fetch(` / 无 `localStorage`，可被 `node -e` 抽出来真跑。
function packagingPartDetailReadProblemText(problem) {
  const row = (problem && typeof problem === "object") ? problem : {};
  const code = String(row.code || "").trim();
  if (!code) return "";
  if (code === "PACKAGING_PART_NOT_FOUND") {
    return "这一件已经不在当前的零件文档里了（可能重跑过图纸解析）；请点左栏重新选一件";
  }
  const status = Number(row.status) || 0;
  return status > 0
    ? `暂时读不到这一件（HTTP ${status}），请稍后重试；这不代表这一件没有数据`
    : "暂时读不到这一件（网络错误），请稍后重试；这不代表这一件没有数据";
}

// 单件结论的「业务部件身份」文案（Spec
// `packaging-part-conclusion-business-identity-in-panel.md` §C1）：后端只给码
// （`business_parts_reimported` / `business_parts_unknown`），人话只在这一处出。
// 五态 —— `""` 什么都不说（本批之前落的结论没有业务身份） / `stale` 上一版清单算的 /
// `unknown` 判断不了哪一版 / `info` 哪一件哪一版 / `unbound` 没绑到业务件。
// 纯函数：无 DOM、无 `fetch(`、无 `localStorage`，可被 node 直接执行。
function packagingPartBusinessIdentityNote(payload) {
  const data = (payload && typeof payload === "object") ? payload : {};
  const version = String(data.business_parts_id == null ? "" : data.business_parts_id).trim();
  if (!version) return {text: "", level: ""};
  if (data.business_stale === true) {
    return {text: "这份结论是按上一版业务部件清单算的，请重跑后再用。", level: "stale"};
  }
  if (String(data.business_stale_reason || "") === "business_parts_unknown") {
    return {text: "判断不了这份结论对应哪一版业务部件清单。", level: "unknown"};
  }
  const code = String(data.business_part_code == null ? "" : data.business_part_code).trim();
  if (code) {
    const short = version.length > 12 ? version.slice(0, 12) + "…" : version;
    return {text: `业务部件 ${code}（清单 ${short}）`, level: "info"};
  }
  return {text: "这一件没有绑到业务部件，结论按几何零件算的。", level: "unbound"};
}

// 2.1 左栏零件文档（drawing_flow 链路）：GET .../requirement/packaging-parts。
// 端点未上线（零件提取那批才加）时拿到 404 → 保持 null，走空态文案，不谎报"解析失败"。
//
// 分页 + 种类折叠（Spec `packaging-parts-list-visibility-and-kinds.md` §2.4/§2.5）：读接口一次
// 只回 `limit` 件（缺省 64），`items` 是当前页、`total` / `kind_total` 是全量真值；"继续加载"
// 几何分量清单的取数（左栏几何诊断区用）：已经翻过页就用累加的行，否则用本页（`items`）；
// 文档没读到时不许用上一份的累加行。三笔账（已显示 / 共多少 / 多少种形状）用的是这里的真值。
// 单独成函数是为了让 `renderTree()` 保持"业务部件优先、几何只进折叠区"的判据本身可读。
function packagingGeometryListing(doc) {
  const rows = (currentPackagingParts && packagingPartsShown.length)
    ? packagingPartsShown : packagingPartsItems(doc);
  const total = Number(doc.total) || Number((doc.stats || {}).part_total) || rows.length;
  const kindTotal = Number(doc.kind_total) || Number((doc.stats || {}).kind_total) || 0;
  return { rows: rows, total: total, kindTotal: kindTotal };
}

// 按 `offset` 往后翻，翻回来的行**累加**在 `packagingPartsShown` 上（折叠与计数都在这上面）。
let currentPackagingParts = null;
// 业务部件文档（Spec `packaging-business-parts-and-cad-plan-view.md` §2）：**唯一**的业务
// 部件集合。有它就按它列左栏；没有才退回几何分量，并如实说"尚未形成业务部件清单"。
let currentPackagingBusinessParts = null;
let currentDrawingFlowState = null;
let packagingPartsPage = { offset: 0, limit: 64, kind: "", role: "",
                           outline_status: "", min_area_mm2: "" };
let packagingPartsShown = [];

// 当前页的行（`items`）：老后端只给 `parts` 时退回它，但绝不许把整份文档当页用。
// 业务部件行（Spec `packaging-business-parts-and-cad-plan-view.md` §2 第 1/4/6 条）：
// 有权威清单时左栏就是这 28 件，绝不是几百个几何分量；未绑定不等于删除 —— 照旧列出，
// 只把"尚未在 CAD 图中定位"写在行上。
// 没有权威清单时的左栏出口（Spec `packaging-business-parts-and-cad-plan-view.md` §2 第 5 条）：
// 必须说清"已识别几何区域 n 个，尚未形成业务部件清单"，并给一条补数据的路子 ——
// 不许把几百个几何分量冒充成业务零件，也不许只留一片空白。
// 业务部件清单"读不到"时的文案（纯函数，Spec `packaging-business-parts-read-failure-note.md` §2.2）：
// 与几何零件那一侧同构（`packagingPartsEmptyText()` 的 `read_problem` 分支）—— 读失败要自己的
// 一句话，不许被"已识别的几何区域还不是业务部件清单"抢先：那会把读失败赖到业务数据上，
// 还把人支去**重新导入**（会落新的一版业务部件文档，不是读失败该有的下一步）。
function packagingBusinessReadProblemText(problem) {
  const row = (problem && typeof problem === "object") ? problem : {};
  if (!String(row.code || "").trim()) return "";
  const status = Number(row.status) || 0;
  return status > 0
    ? `暂时读不到业务部件清单（HTTP ${status}），请稍后重试；这不代表这个项目还没导入权威清单`
    : "暂时读不到业务部件清单（网络错误），请稍后重试；这不代表这个项目还没导入权威清单";
}

function packagingBusinessImportNote(doc) {
  if (!doc || packagingBusinessPartRows(doc).length) return null;
  // 读失败优先（Spec §2.3）：**不给**导入按钮（重新导入是错的下一步），并用自己的 data- 钩子
  // 与"确实还没有权威清单"（`qqBusinessMissing`）分家。
  const problem = (doc.read_problem && typeof doc.read_problem === "object")
    ? doc.read_problem : null;
  if (problem) {
    const unavailable = document.createElement("div");
    unavailable.className = "packaging-business-unavailable";
    unavailable.dataset.qqBusinessUnavailable = "1";
    const problemText = document.createElement("div");
    problemText.className = "packaging-business-missing-text";
    problemText.textContent = packagingBusinessReadProblemText(problem);
    unavailable.appendChild(problemText);
    return unavailable;
  }
  const gap = (doc && doc.gap) || {};
  const wrap = document.createElement("div");
  wrap.className = "packaging-business-missing";
  wrap.dataset.qqBusinessMissing = "1";
  const text = document.createElement("div");
  text.className = "packaging-business-missing-text";
  text.textContent = gap.message
    || "已识别的几何区域还不是业务部件清单：下面列的是几何分量，不是业务零件。";
  const action = document.createElement("div");
  action.className = "packaging-business-missing-action";
  action.textContent = gap.action || "导入权威部件清单（Excel）后再跑 BOM / 工艺 / 成本";
  const button = document.createElement("button");
  button.id = "packagingBusinessImport";
  button.className = "btn btn-secondary";
  button.type = "button";
  button.textContent = "导入权威清单（业务部件）";
  button.addEventListener("click", () => { importPackagingBusinessParts(); });
  // 客户工作簿入口（Spec `packaging-authority-workbook-upload.md` §C3）：客户给的 xlsx
  // 不必先放上服务器 —— 选中文件即走同一条导入接口（只搬字节，前端不解析）。
  const picker = document.createElement("div");
  picker.className = "packaging-business-upload";
  picker.setAttribute("data-qq-business-upload", "1");
  picker.innerHTML = '<input type="file" accept=".xlsx,.xlsm" hidden>';
  const fileInput = picker.querySelector("input");
  const upload = document.createElement("button");
  upload.id = "packagingBusinessImportFile";
  upload.className = "btn btn-secondary";
  upload.type = "button";
  upload.textContent = "选择客户工作簿…";
  upload.addEventListener("click", () => { if (fileInput) fileInput.click(); });
  if (fileInput) {
    fileInput.addEventListener("change", () => {
      const picked = (fileInput.files || [])[0];
      if (picked) importPackagingBusinessPartsFromFile(picked);
      fileInput.value = "";
    });
  }
  wrap.appendChild(text);
  wrap.appendChild(action);
  wrap.appendChild(button);
  wrap.appendChild(upload);
  wrap.appendChild(picker);
  return wrap;
}

// 导入权威清单的三条路（Spec docs/specs/packaging-authority-workbook-upload.md §C3）：
//   ① 服务器路径（部署机上直接指一个路径，原样保留）；
//   ② 客户工作簿（选文件 → FileReader 读成 data URL → 只搬字节，前端**不**解析 xlsx）；
//   ③ 都没有 → null（不猜、不编空载荷）。
// 三个都是纯函数：体内无 DOM / fetch / storage，可被 `node -e` 抽出来真跑。
function packagingAuthorityFileName(name) {
  if (typeof name !== "string") return "";
  const tail = name.replace(/\\/g, "/").split("/").pop() || "";
  return tail.replace(/[\u0000-\u001f\u007f]/g, "").trim();
}
function packagingAuthorityBase64Of(dataUrl) {
  if (typeof dataUrl !== "string") return "";
  const match = /^data:[^,]*;base64,([\s\S]*)$/.exec(dataUrl);
  return match ? match[1].replace(/\s+/g, "") : "";
}
function packagingAuthorityImportBody(path, fileName, dataUrl) {
  const serverPath = typeof path === "string" ? path.trim() : "";
  if (serverPath) return { workbook_path: serverPath };
  const encoded = packagingAuthorityBase64Of(dataUrl);
  if (!encoded) return null;
  return { content_base64: encoded, file_name: packagingAuthorityFileName(fileName) };
}

// 导入接口只有一处字面量（Spec §C3/§C4：前端业务件路由引用计数保持 4）。
function packagingBusinessPartsImportPath() {
  return `${API}/api/projects/${currentProject}/requirement/packaging-business-parts/import`;
}

// 路径导入与文件导入共用这一个 POST（失败一律用后端给的 message，不猜原因）。
async function importPackagingBusinessPartsData(path, fileName, dataUrl) {
  if (!currentProject) return null;
  const body = packagingAuthorityImportBody(path, fileName, dataUrl);
  if (!body) return null;
  try {
    const res = await fetch(packagingBusinessPartsImportPath(), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = (payload && payload.detail) || {};
      const message = typeof detail === "string" ? detail : String(detail.message || "");
      throw new Error(message || `导入权威清单失败（HTTP ${res.status}）`);
    }
    currentPackagingBusinessParts = payload;
    renderTree(currentIR || {});
    await loadPackagingCadPlan().catch(() => null);
    return payload;
  } catch (error) {
    window.alert(String((error && error.message) || error));
    return null;
  }
}

// 导入权威清单 → 落一版业务部件文档（Spec §3/§5）：确定性解析，可重复跑（同资料同 id）。
async function importPackagingBusinessParts() {
  if (!currentProject) return null;
  const path = window.prompt("权威清单工作簿在服务器上的路径（.xlsx）", "");
  if (!path) return null;
  return importPackagingBusinessPartsData(path, "", "");
}

// 客户工作簿：读成 data URL 后交给同一个 POST（只搬字节、不解析）。
function importPackagingBusinessPartsFromFile(file) {
  if (!file) return null;
  return new Promise(resolve => {
    const reader = new FileReader();
    reader.onload = () => {
      resolve(importPackagingBusinessPartsData("", file.name, String(reader.result || "")));
    };
    reader.onerror = () => {
      window.alert("这个文件读不出来，请重新选择");
      resolve(null);
    };
    reader.readAsDataURL(file);
  });
}

// 依据区的共用行渲染（Spec `packaging-business-part-panel-evidence.md` §C3）：
// 几何零件面板与业务部件面板必须长一样、空态一样 —— 两种面板块只做一次。
// 位置有硬约束（不许前移到面板渲染函数之前）：
//   ① `function \w*[Pp]ackagingPart\w*` 的第一命中必须仍是带 viewBox 的
//      renderPackagingPartPanel()（tests/test_packaging_parts_panel_red.py E2）；
//   ② 不许落在 packagingPartProcess 的 +4000 字窗口里
//      （tests/test_packaging_parts_downstream_red.py F3 要在这个窗口里读到 CadInlineAnalysis）；
//   ③ function 声明会提升，调用点在前不影响执行。
function packagingPartEvidenceRowsHtml(rows) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return `<div class="view-3d-placeholder">这一件没有可回查的实体证据。</div>`;
  return list.map(row => `<div class="packaging-part-evidence-row">`
    + `<span class="ref">${esc(String(row.ref || ""))}</span>`
    + `<span class="kind">${esc(String(row.kind || ""))}</span>`
    + `<span class="layer">${esc(String(row.layer || ""))}</span>`
    + `<span class="note">${esc(String(row.note || ""))}</span></div>`).join("");
}

// 业务部件图的只读端点 URL（Spec `packaging-authority-thumbnail-media.md` §C6）：纯函数，
// 可被 node 直接执行；两段都 encode（件编码里可能出现 `/`）。
function packagingBusinessPartThumbnailUrl(projectId, code) {
  const pid = encodeURIComponent(String(projectId || ""));
  const part = encodeURIComponent(String(code || ""));
  return `${API}/api/projects/${pid}/requirement/packaging-business-parts/${part}/thumbnail`;
}

// 权威清单的两条披露（Spec `packaging-authority-disclosure-on-read.md` §C4）：部件图归属是
// "按顺序推定"还是"逐行核对"，以及哪些行被导入器跳过（含客户原文）—— 纯函数，可被 node 直接执行。
// 位置与 `packagingPartEvidenceRowsHtml()` 同一段：这里离 panels 远，不挤那两个源码窗口护栏。
function packagingAuthorityDisclosureLines(doc) {
  const payload = (doc && typeof doc === "object") ? doc : {};
  const authority = payload.authority;
  if (!authority || typeof authority !== "object" || Array.isArray(authority)) return [];
  const stats = (authority.stats && typeof authority.stats === "object") ? authority.stats : {};
  const thumb = (authority.thumbnail && typeof authority.thumbnail === "object")
    ? authority.thumbnail : {};
  const skipped = Array.isArray(authority.skipped) ? authority.skipped : [];
  const total = Number(stats.part_total) || 0;
  const bound = Number(thumb.bound_total) || 0;
  const boundBy = String(thumb.bound_by || "");
  const lines = [];
  if (total > 0) {
    if (!bound) {
      lines.push(`部件图：${total} 件都没配到部件图（这一版清单的图没有归属）。`);
    } else if (boundBy === "anchor_row") {
      lines.push(`部件图：${bound}/${total} 件配到了图，归属按锚点行。`);
    } else if (boundBy === "mixed") {
      lines.push(`部件图：${bound}/${total} 件配到了图，但归属来源不统一`
        + `（部分按锚点行、部分按顺序推定），需人工核对。`);
    } else {
      lines.push(`部件图：${bound}/${total} 件配到了图，但归属是按顺序推定`
        + `（图片是浮动对象，不是按锚点行逐行核对）。`);
    }
  }
  const skippedTotal = Number(stats.skipped_total) || 0;
  if (skippedTotal > 0) {
    const reasons = [];
    skipped.forEach(item => {
      const reason = String((item || {}).reason || "");
      if (reason && reasons.indexOf(reason) < 0) reasons.push(reason);
    });
    lines.push(`清单里有 ${skippedTotal} 行被跳过（${reasons.join("、") || "未给原因"}）：`
      + `这些行不是业务部件，但文字可能影响报价。`);
  }
  skipped.forEach(item => {
    const row = (item && typeof item === "object") ? item : {};
    const text = String(row.text || "").trim();
    if (!text) return;
    lines.push(`第 ${Number(row.row) || 0} 行被跳过（${String(row.reason || "")}）：${text}`
      + ` —— 请人工确认是否影响报价。`);
  });
  return lines;
}

// 业务部件面板的「依据」行（Spec `packaging-business-part-panel-evidence.md` §C2）：
// ① 权威清单出处（表 + 行 + 文件指纹；拼不出就如实说"未记录"，不猜文件名）；
// ② 每件绑定分量一行；③ 没绑定就补一行说明。纯函数，可被 node 直接执行。
function packagingBusinessPartEvidenceRows(row, components, source) {
  const part = (row && typeof row === "object") ? row : {};
  const authority = part.authority || {};
  const own = authority.source || {};
  const doc = (source && typeof source === "object") ? source : {};
  const sheet = String(own.sheet || doc.authority_sheet || "").trim();
  const line = own.row || doc.row || "";
  const digest = String(doc.authority_file_hash || own.file_hash || "").trim().slice(0, 12);
  const bits = [];
  if (sheet) bits.push("表 " + sheet);
  if (line) bits.push("第 " + line + " 行");
  if (digest) bits.push("文件指纹 " + digest);
  const rows = [{
    kind: "authority", ref: bits.join(" · "), layer: sheet,
    note: bits.length
      ? "业务尺寸 / 材料 / 排版来自这份权威清单"
      : "权威出处未记录（这份清单里没有留下表 / 行 / 文件指纹）",
  }];
  const binding = part.geometry_binding || {};
  const ids = (binding.component_ids || []).map(id => String(id));
  const byId = {};
  (Array.isArray(components) ? components : []).forEach(item => {
    const key = (item && item.component_id !== undefined && item.component_id !== null)
      ? String(item.component_id) : "";
    if (key) byId[key] = item;
  });
  ids.forEach(id => {
    const item = byId[id] || {};
    const layers = (Array.isArray(item.layers) ? item.layers : []).map(name => String(name)).filter(Boolean);
    const role = String(item.role || "").trim();
    const total = Array.isArray(item.entity_ids) ? item.entity_ids.length : 0;
    const notes = [];
    if (role) notes.push(role);
    if (layers.length) notes.push(layers.join("、"));
    if (total) notes.push("图元 " + total);
    rows.push({ kind: "component", ref: id, layer: layers.join("、"),
                note: notes.length ? notes.join(" · ") : "证据层没有这一件的细节" });
  });
  if (!ids.length) {
    rows.push({ kind: "binding", ref: "", layer: "",
                note: "尚未在 CAD 图中定位（几何未绑定；材料与采购项不受影响）" });
  }
  return rows;
}

function packagingBusinessPartRows(doc) {
  const rows = (doc && Array.isArray(doc.business_parts)) ? doc.business_parts : [];
  return rows.filter(row => row && typeof row === "object");
}

// 业务部件行的「单件工艺 / 成本」目标解析（Spec
// `packaging-business-part-downstream-entry.md` §C1）：**只读**地把业务部件映射到它绑定的几何件，
// 能算才给按钮 —— 绑不到 / 绑到的件没闭合一律说清"为什么不能算、下一步做什么"，绝不猜一个几何件。
// 纯函数：吃两个已在内存里的文档，不读库、不写状态；体内无 DOM / `fetch(` / `localStorage`。
function packagingBusinessPartDownstreamTarget(row, partsDoc) {
  const part = (row && typeof row === "object") ? row : {};
  const code = String(part.business_part_code || "").trim();
  if (!code) {
    return {ok: false, part_code: "", code: "business_part_missing",
            message: "这一件没有业务部件编码，不能发起下游。"};
  }
  const binding = (part.geometry_binding && typeof part.geometry_binding === "object")
    ? part.geometry_binding : {};
  const wanted = new Set();
  const add = value => {
    const text = String(value === undefined || value === null ? "" : value).trim();
    if (text) wanted.add(text);
  };
  (Array.isArray(binding.component_ids) ? binding.component_ids : []).forEach(add);
  (Array.isArray(part.geometry_component_ref) ? part.geometry_component_ref : []).forEach(add);
  const rows = (partsDoc && Array.isArray(partsDoc.parts)) ? partsDoc.parts : [];
  const hits = rows.filter(item => item && typeof item === "object"
    && (wanted.has(String(item.component_id || "").trim())
        || wanted.has(String(item.geometry_component_ref || "").trim())));
  if (!hits.length) {
    return {ok: false, part_code: "", code: "geometry_unbound",
            message: "这一件还没在 CAD 图中定位到几何件；先在平面图里确认几何映射，"
              + "或按权威尺寸补录后再算。"};
  }
  const closed = hits.filter(item => String(item.outline_status || "") === "closed")
    .sort((a, b) => String(a.part_code || "").localeCompare(String(b.part_code || "")));
  if (!closed.length) {
    return {ok: false, part_code: "", code: "outline_open",
            message: "这一件绑定的几何件还没有闭合轮廓（尺寸来自包围盒），先把轮廓补出来再算。"};
  }
  return {ok: true, part_code: String(closed[0].part_code || ""), code: "", message: ""};
}

// 业务部件「按权威尺寸算材料费」的入口判据（Spec
// `packaging-business-part-size-cost-entry.md` §C1）：没有几何的件也能算，但**只有**权威清单里
// 有长度/宽度时才给按钮 —— 否则又是一颗点了必然失败的按钮（那就是假入口）。
// 纯函数：无 DOM、无 `fetch(`、无 `localStorage`，可被 node 直接执行。
function packagingBusinessPartSizeCostTarget(row) {
  const part = (row && typeof row === "object") ? row : {};
  const code = String(part.business_part_code || "").trim();
  const authority = (part.authority && typeof part.authority === "object") ? part.authority : {};
  const positive = value => {
    const number = Number(String(value === undefined || value === null ? "" : value).trim());
    return Number.isFinite(number) && number > 0 ? number : 0;
  };
  if (!code) {
    return {ok: false, code: "business_part_missing", part_code: "",
            message: "这一件没有业务部件编码，不能算材料费。"};
  }
  if (!positive(authority.length_mm) || !positive(authority.width_mm)) {
    return {ok: false, code: "authority_size_missing", part_code: code,
            message: "这一件没有权威尺寸（长度/宽度），先在平面图里确认几何映射，"
              + "或补录权威尺寸后再算。"};
  }
  return {ok: true, code: "", message: "", part_code: code};
}

// 业务部件「按权威清单排工序」（Spec `packaging-business-part-process-entry.md` §C1）：
// 与上面那颗成本按钮同形，多一道材料门槛 —— 工序明细离不开材料原文，缺了就不给假入口。
function packagingBusinessPartProcessTarget(row) {
  const part = (row && typeof row === "object") ? row : {};
  const code = String(part.business_part_code || "").trim();
  const authority = (part.authority && typeof part.authority === "object") ? part.authority : {};
  const positive = value => {
    const number = Number(String(value === undefined || value === null ? "" : value).trim());
    return Number.isFinite(number) && number > 0 ? number : 0;
  };
  const text = value => String(value === undefined || value === null ? "" : value).trim();
  if (!code) {
    return {ok: false, code: "business_part_missing", part_code: "",
            message: "这一件没有业务部件编码，不能排工艺。"};
  }
  if (!positive(authority.length_mm) || !positive(authority.width_mm)) {
    return {ok: false, code: "authority_size_missing", part_code: code,
            message: "这一件没有权威尺寸（长度/宽度），先在平面图里确认几何映射，"
              + "或补录权威尺寸后再排工艺。"};
  }
  if (!text(authority.material_text) && !text(part.material)) {
    return {ok: false, code: "material_missing", part_code: code,
            message: "这一件在权威清单里没有材料原文，补上材料后再排工艺。"};
  }
  return {ok: true, code: "", message: "", part_code: code};
}

// 业务部件结论的「口径四键」（Spec `packaging-business-part-conclusion-basis-in-panel.md` §C1）：
// 后端读回体与任务结果都带 `size_source` / `size_source_ref` / `size_text` / `geometry`，
// 这一处把它们翻成一句人话 —— 几何零件那条路（`size_source` 为空）**一个字都不多说**。
function packagingBusinessPartBasisNote(data) {
  const payload = (data && typeof data === "object") ? data : {};
  const source = String(payload.size_source === undefined || payload.size_source === null
    ? "" : payload.size_source).trim();
  if (source !== "authority_dimensions") return {text: "", level: ""};
  const text = value => String(value === undefined || value === null ? "" : value).trim();
  const pieces = [];
  const sizeText = text(payload.size_text);
  const ref = text(payload.size_source_ref);
  if (sizeText) pieces.push(sizeText);
  if (ref) pieces.push(`来源：${ref}`);
  const inside = pieces.join("；");
  const geometry = text(payload.geometry);
  const bound = geometry.indexOf("bound:") === 0 ? geometry.slice(6).trim() : "";
  const tail = bound
    ? `；这一件另绑了几何件 ${bound}，本结论有意按权威尺寸算。`
    : "；这一件没有绑 CAD 几何。";
  return {text: `按权威尺寸算的${inside ? `（${inside}）` : ""}${tail}`,
          level: bound ? "authority_bound" : "authority_unbound"};
}

// 业务部件发起单件工艺 / 成本（Spec §C3）：解析出几何件编码 → 走既有取行与既有无分析入口，
// **不**新写第二套接口 / 渲染。
async function packagingBusinessPartAnalyze(mode, partCode) {
  const code = String(partCode || "").trim();
  if (!code) {
    return {ok: false, error: {code: "no-part-code", message: "这一件没有可用的几何件编码。"}};
  }
  await selectPackagingPart(code);
  return packagingPartAnalyze(mode);
}

// 业务部件「按权威尺寸算材料费」（Spec `packaging-business-part-size-cost-entry.md` §C3）：
// 端点走业务部件那条成本路由（`## 412`），复用既有内嵌面板；**不**走几何取行（业务编码不在零件
// 文档里），也**不**在前端算钱 —— 数字仍然全部来自后端 `compute_line()`。
function packagingBusinessPartSizeCost(partCode) {
  const code = String(partCode || "").trim();
  if (!code) {
    return {ok: false, error: {code: "no-part-code", message: "这一件没有业务部件编码。"}};
  }
  const host = $("analysisHost");
  if (!host || !window.CadInlineAnalysis) {
    return {ok: false,
            error: {code: "no-analysis-host", message: "当前看板没有可用的分析渲染区。"}};
  }
  const row = packagingBusinessPartRows(currentPackagingBusinessParts)
    .find(item => String(item.business_part_code || "") === code) || {};
  const base = `${API}/api/projects/${currentProject}/requirement/packaging-business-parts/`
    + encodeURIComponent(code);
  exitBoardViewHost();
  setRightPane("analysis", `${code} · 按权威尺寸算材料费`);
  window.CadInlineAnalysis.open("cost", {
    host,
    projectId: currentProject,
    part: {part_id: code, name: row.name || code},
    endpointBase: () => base,
    onClose: () => setRightPane("model"),
  });
  return {ok: true, result: {mode: "cost", partCode: code}};
}

// 业务部件「按权威清单排工序」（Spec `packaging-business-part-process-entry.md` §C3）：
// 端点走 `## 414` 那条工艺路由，复用既有内嵌面板；**不**走几何取行（业务编码不在零件文档里）、
// **不**在前端算工序 —— 工序明细全部来自后端 `outline_process()`。
function packagingBusinessPartProcessByAuthority(partCode) {
  const code = String(partCode || "").trim();
  if (!code) {
    return {ok: false, error: {code: "no-part-code", message: "这一件没有业务部件编码。"}};
  }
  const host = $("analysisHost");
  if (!host || !window.CadInlineAnalysis) {
    return {ok: false,
            error: {code: "no-analysis-host", message: "当前看板没有可用的分析渲染区。"}};
  }
  const row = packagingBusinessPartRows(currentPackagingBusinessParts)
    .find(item => String(item.business_part_code || "") === code) || {};
  const base = `${API}/api/projects/${currentProject}/requirement/packaging-business-parts/`
    + encodeURIComponent(code);
  exitBoardViewHost();
  setRightPane("analysis", `${code} · 工艺推荐（按权威清单）`);
  window.CadInlineAnalysis.open("process", {
    host,
    projectId: currentProject,
    part: {part_id: code, name: row.name || code},
    endpointBase: () => base,
    onClose: () => setRightPane("model"),
  });
  return {ok: true, result: {mode: "process", partCode: code}};
}

const PACKAGING_BINDING_COPY = {
  bound: "已在图纸中定位", partial: "部分定位", ambiguous: "定位待人工确认", unbound: "尚未在 CAD 图中定位",
};

function packagingBusinessPartSizeText(row) {
  const authority = (row && row.authority) || {};
  const text = String(authority.product_size_text || "").trim();
  if (text) return text;
  const length = authority.length_mm; const width = authority.width_mm;
  if (length && width) return `${length}×${width} mm`;
  return "";
}

// 业务部件清单的来源标签（Spec `packaging-parts-must-be-derived-from-the-drawing.md` §2.6 第 2 条）：
// 从图纸推导出来的清单要说成"从图纸推导（待人工确认）"，不许用"权威清单 / 已审核 BOM"描述它。
function packagingBusinessPartsSourceLabel(doc) {
  const record = doc || {};
  const derived = record.derived_from_drawing === true
    || (record.authority && record.authority.derived_from_drawing === true);
  return derived ? "从图纸推导（待人工确认）" : "来自权威清单";
}

/* ---------------- 业务部件的事实档三档（Spec packaging-business-truth-state-disclosure.md §2.6） ----------------
   三档**怎么判**只在解析器里（`packaging_business_part_resolver.TRUTH_STATES`）：页面只照 payload 说，
   不猜、也绝不把"没给档位"渲染成"图上识别"。两条都是纯函数（体内无 DOM / 全局 / 网络调用）。 */
function packagingBusinessPartTruthLabel(truthState) {
  const state = (truthState === null || truthState === undefined) ? "" : String(truthState).trim();
  if (state === "observed") return "图上识别";
  if (state === "inferred") return "规则纠名（推断）";
  if (state === "pending_confirmation") return "结构规则补件（待确认）";
  return "";
}

// 三档那一行（`识别 a · 推断 b · 待确认 c`）：三个分子都取 payload 的真值 —— 先看
// `summary.stats.truth_state_counts`，老载荷回落到扁平的三个 `*_total`。合计为 0 就回空串
// （工作簿来源的清单没有这三档，不许凭空显示一行"识别 0 / 推断 0 / 待确认 0"）。
function packagingBusinessTruthLine(doc) {
  const body = (doc && typeof doc === "object") ? doc : {};
  const summary = (body.summary && typeof body.summary === "object") ? body.summary : {};
  const stats = (summary.stats && typeof summary.stats === "object") ? summary.stats : {};
  const counts = (stats.truth_state_counts && typeof stats.truth_state_counts === "object")
    ? stats.truth_state_counts : {};
  const num = value => {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? Math.round(number) : 0;
  };
  const pick = (state, flat) => num(counts[state] === undefined ? stats[flat] : counts[state]);
  const observed = pick("observed", "observed_total");
  const inferred = pick("inferred", "inferred_total");
  const pending = pick("pending_confirmation", "pending_confirmation_total");
  if (!observed && !inferred && !pending) return "";
  return `识别 ${observed} · 推断 ${inferred} · 待确认 ${pending}`;
}

// 两笔账的同屏对账（Spec「两笔账同屏对账」§2.1）：只搬两个分子，
// 不自己判定几何 —— 几何账读不到说"待确认"，业务账还没落库说"还没有业务部件清单"，两件事不许混。
function packagingTwoLedgersLine(geometryDoc, businessDoc) {
  const geometry = (geometryDoc && typeof geometryDoc === "object") ? geometryDoc : null;
  const business = (businessDoc && typeof businessDoc === "object") ? businessDoc : null;
  const num = value => {
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? Math.round(number) : 0;
  };
  const problem = doc => (doc && doc.read_problem && typeof doc.read_problem === "object"
    && String(doc.read_problem.code || "").trim()) ? doc.read_problem : null;
  const geometryStats = (geometry && geometry.stats && typeof geometry.stats === "object")
    ? geometry.stats : {};
  const geometryTotal = num(geometry && geometry.total) || num(geometryStats.part_total);
  const geometryProblem = problem(geometry);
  const geometryKnown = geometryTotal > 0 && !geometryProblem;
  const summary = (business && business.summary && typeof business.summary === "object")
    ? business.summary : {};
  const summaryStats = (summary.stats && typeof summary.stats === "object") ? summary.stats : {};
  const flatStats = (business && business.stats && typeof business.stats === "object")
    ? business.stats : {};
  const pickBusiness = key => num(summaryStats[key]) || num(flatStats[key]);
  const businessTotal = pickBusiness("business_part_total");
  const boundTotal = pickBusiness("bound_total");
  const businessProblem = problem(business);
  const businessKnown = businessTotal > 0 && !businessProblem;
  const businessChannel = businessKnown
    ? `业务部件 ${businessTotal} 件（已定位 ${boundTotal} 件）` : "";
  if (geometryKnown && businessKnown) return `几何区域 ${geometryTotal} 个 → ${businessChannel}`;
  if (geometryKnown) {
    return businessProblem
      ? `几何区域 ${geometryTotal} 个 → 业务部件清单读不到`
      : `几何区域 ${geometryTotal} 个 → 还没有业务部件清单`;
  }
  if (businessKnown) return `几何区域待确认 → ${businessChannel}`;
  return "";
}

function renderPackagingBusinessTree(tree, rows) {
  const head = document.createElement("div");
  head.className = "packaging-business-head";
  head.dataset.qqBusinessParts = "1";
  head.textContent = `业务部件 ${rows.length} 件（${packagingBusinessPartsSourceLabel(currentPackagingBusinessParts)}）`;
  tree.appendChild(head);
  // 同一句对账（Spec「两笔账同屏对账」§2.2）：业务账有行的这一路也要把
  // 几何账的分子摆在旁边 —— 人在两条路上看到的口径必须一致；空串时不出现空节点。
  const ledgerLine = packagingTwoLedgersLine(currentPackagingParts, currentPackagingBusinessParts);
  if (ledgerLine) {
    const ledgerNote = document.createElement("div");
    ledgerNote.className = "packaging-ledger-reconciliation";
    ledgerNote.setAttribute("data-qq-ledger-reconciliation", "1");
    ledgerNote.textContent = ledgerLine;
    tree.appendChild(ledgerNote);
  }
  // 三档那一行（Spec `packaging-business-truth-state-disclosure.md` §2.6）：文案来自 payload，
  // 空串（工作簿来源 / 老载荷）时这一行整块不出现。
  const truthLine = packagingBusinessTruthLine(currentPackagingBusinessParts || {});
  if (truthLine) {
    const truth = document.createElement("div");
    truth.className = "packaging-truth-line";
    // 属性名写字面量（同 `data-qq-authority-skip` 的约定）：现场 grep 得到"这一行是哪来的"。
    truth.setAttribute("data-qq-truth-line", "1");
    truth.textContent = truthLine;
    tree.appendChild(truth);
  }
  // 权威清单的披露（Spec `packaging-authority-disclosure-on-read.md` §C5）：部件图归属与
  // 被跳过的行必须看得见 —— 跳过的行里可能有"影响报价"的客户原话。纯文本渲染，不拼 HTML。
  const disclosures = packagingAuthorityDisclosureLines(currentPackagingBusinessParts || {});
  if (disclosures.length) {
    const notice = document.createElement("div");
    notice.className = "packaging-authority-disclosure";
    // 属性名写字面量（Spec §C5 就点名了这个属性）：`dataset.qqAuthoritySkip` 在源码里
    // 看不出这个名字，现场 grep 不到"这个告警块是哪来的"。
    notice.setAttribute("data-qq-authority-skip", "1");
    disclosures.forEach(line => {
      const row = document.createElement("div");
      row.textContent = line;
      notice.appendChild(row);
    });
    tree.appendChild(notice);
  }
  rows.forEach(row => {
    const code = String(row.business_part_code || "");
    const binding = row.geometry_binding || {};
    const status = String(binding.status || "unbound");
    const line = document.createElement("div");
    line.className = "part part-item packaging-business-part";
    line.dataset.partId = code;
    line.dataset.businessPartCode = code;
    line.dataset.bindingStatus = status;
    line.addEventListener("click", () => openPackagingBusinessPart(code));
    const size = packagingBusinessPartSizeText(row);
    const material = String(((row.authority || {}).material_text) || "");
    // 事实档标签（Spec `packaging-business-truth-state-disclosure.md` §2.6）：逐值由 payload 的
    // `truth_state` 决定；空串 / 缺键的行**不**加属性、也不加这一行。
    const truthState = String(row.truth_state || "").trim();
    const truthLabel = packagingBusinessPartTruthLabel(truthState);
    if (truthLabel) line.setAttribute("data-qq-truth-state", truthState);
    line.innerHTML = `<div class="part-icon part-icon-box" aria-hidden="true"></div>`
      + `<div class="part-name">${esc(code)} ${esc(String(row.name || ""))}</div>`
      + `<div class="part-meta">${esc(size)}${material ? " · " + esc(material) : ""}</div>`
      + `<div class="part-note">${esc(PACKAGING_BINDING_COPY[status] || status)}</div>`
      + (truthLabel ? `<div class="part-note packaging-truth-label">${esc(truthLabel)}</div>` : "");
    tree.appendChild(line);
  });
}

// 点业务部件：右栏给权威资料 + 绑定状态，并在 CAD 平面图里高亮它绑定的图元
// （Spec §6.2）。这里**不**在浏览器端重算尺寸、也**不**重新拆件。
// 清单侧"这一件没有图"的原因文案（Spec `packaging-authority-thumbnail-bytes-read-failure.md` §2.1）：
// 闭合表逐字照抄服务端 `main.py PACKAGING_THUMBNAIL_REASON_COPY` 里件级会出现的三个码；
// **表外码照实暴露**（`部件图读不到（<码>）`），不许再断言"没入库 / 重新导入即可" ——
// 宽泛兜底会把"字节被清理"赖到"没导入"头上，而导入不是那件事的下一步。纯函数，不碰 DOM / fetch。
function packagingBusinessThumbnailReasonText(reason) {
  const code = (reason === null || reason === undefined) ? "" : String(reason).trim();
  if (!code) return "";
  if (code === "image_bytes_unreadable") return "工作簿里的部件图读不出来（导入时就没读到字节）";
  if (code === "thumbnail_missing") return "这份清单里这一件没有配到部件图";
  if (code === "thumbnail_not_saved") {
    return "这件有部件图引用，但字节还没入库：重新导入一次权威清单即可";
  }
  return `部件图读不到（${code}）`;
}

// 清单里"有部件图"的那一件、这一次字节却取不到（blob 被清理 / 接口暂时读不到）时的文案
// （Spec `packaging-authority-thumbnail-bytes-read-failure.md` §2.2）。与"这一件没有配图"、
// "字节还没入库"是三件事，所以这句子一个字都不提它们。纯函数，不碰 DOM / fetch。
function packagingBusinessThumbnailBytesFailureText() {
  return "这一件清单里有部件图，但这一次没取到字节（可能已被清理，也可能是接口暂时读不到）；"
    + "刷新或重新导入权威清单可重建";
}

function openPackagingBusinessPart(code) {
  const wanted = String(code || "");
  const rows = packagingBusinessPartRows(currentPackagingBusinessParts);
  const row = rows.find(item => String(item.business_part_code || "") === wanted);
  if (!row) return null;
  currentSelectedPanelPart = null;
  setRightPane("model");
  showPackagingPartPane();
  markSelection(wanted);
  const title = $("packagingPartTitle");
  if (title) title.textContent = `${wanted} ${String(row.name || "")}`.trim();
  const binding = row.geometry_binding || {};
  const authority = row.authority || {};
  const disclosures = packagingAuthorityDisclosureLines(currentPackagingBusinessParts || {});
  const facts = $("packagingPartFacts");
  if (facts) {
    facts.innerHTML = [
      pkgPartFactRow("权威尺寸", packagingBusinessPartSizeText(row)),
      pkgPartFactRow("部件图", authority.thumbnail_ref
        ? "已配到（" + (String(authority.thumbnail_source || "") === "order"
          ? "归属按顺序推定" : "归属按锚点行") + "）" : ""),
      pkgPartFactRow("清单告警", disclosures.join("；")),
      pkgPartFactRow("材料", authority.material_text),
      pkgPartFactRow("排版", authority.layout_text),
      pkgPartFactRow("工艺", authority.process_text),
      pkgPartFactRow("备注", authority.note),
      pkgPartFactRow("定位状态", PACKAGING_BINDING_COPY[String(binding.status || "")] || ""),
      pkgPartFactRow("绑定分量", (binding.component_ids || []).join(" / ")),
      pkgPartFactRow("同组提示", authority.merged_from
        ? "材料/排版/工艺与上一行同组（合并单元格）" : ""),
    ].join("");
  }
  const outlineHost = $("packagingPartOutline");
  if (outlineHost) {
    // 绑定了几何分量就把它们的形状画出来（Spec §C2）；画不出来才回到绑定状态文案。
    const outlineHtml = packagingBusinessPartOutlineHtml(binding, currentPackagingCadPlan);
    outlineHost.innerHTML = outlineHtml
      ? (outlineHtml + `<div class="packaging-part-note">${esc(PACKAGING_BOUND_OUTLINE_NOTE)}</div>`)
      : `<div class="view-3d-placeholder">`
        + `${esc(PACKAGING_BINDING_COPY[String(binding.status || "")] || "")}`
        + `</div>`;
  }
  const thumbnailHost = $("packagingPartThumbnail");
  if (thumbnailHost) {
    const thumb = (row.thumbnail && typeof row.thumbnail === "object") ? row.thumbnail : {};
    if (thumb.available) {
      const alt = `${wanted} ${String(row.name || "")}`.trim();
      // <img> 带不了请求头，所以走 mediaUrl()（与其它媒体一致）。
      thumbnailHost.innerHTML = `<img class="packaging-business-thumb"`
        + ` src="${esc(mediaUrl(packagingBusinessPartThumbnailUrl(currentProject, wanted)))}"`
        + ` alt="${esc(alt + " 的部件图")}">`;
      thumbnailHost.hidden = false;
      // 清单里有引用只说"这一件配了图"，**不是**"这一次取得到字节"（Spec
      // `packaging-authority-thumbnail-bytes-read-failure.md` §2.3）：字节被清理 / 接口 500 时
      // 浏览器只会画碎图，这里接住它，就这一块换成人话 —— 不改成"没有配图"、不动行上"已配到"。
      const img = thumbnailHost.querySelector("img.packaging-business-thumb");
      if (img) {
        img.onerror = () => {
          thumbnailHost.innerHTML = `<div class="packaging-part-note"`
            + ` data-qqThumbBytesUnavailable="1">`
            + `${esc(packagingBusinessThumbnailBytesFailureText())}</div>`;
        };
      }
    } else {
      const copy = packagingBusinessThumbnailReasonText(String(thumb.reason || ""));
      thumbnailHost.innerHTML = `<div class="packaging-part-note">${esc(copy)}</div>`;
      thumbnailHost.hidden = false;
    }
  }
  const evidenceHost = $("packagingPartEvidence");
  if (evidenceHost) {
    // 选中即重写（Spec §C2）：不许把上一件几何零件的实体证据留在这一件的面板里。
    const evidenceDoc = (currentPackagingBusinessParts || {}).geometry_evidence || {};
    evidenceHost.innerHTML = packagingPartEvidenceRowsHtml(packagingBusinessPartEvidenceRows(
      row, evidenceDoc.components, (currentPackagingBusinessParts || {}).source));
  }
  const actions = $("packagingPartActions");
  if (actions) {
    // 单件工艺 / 成本能不能发起（Spec `packaging-business-part-downstream-entry.md` §C2）：
    // 绑到闭合几何件 → 给两个按钮（点了复用几何件那套既有入口）；否则给一行原因（为什么不能算 + 下一步）。
    const target = packagingBusinessPartDownstreamTarget(row, currentPackagingParts || {});
    // 没有几何的件那条路（Spec `packaging-business-part-size-cost-entry.md` §C2）：有权威尺寸
    // 才给「按权威尺寸算材料费」那颗按钮，没有就还是只给原因。
    const sizeTarget = packagingBusinessPartSizeCostTarget(row);
    // 工艺那一半（Spec `packaging-business-part-process-entry.md` §C2）：同一条路、多一道材料门槛。
    const processTarget = packagingBusinessPartProcessTarget(row);
    const note = `<div class="packaging-part-note">单件工艺 / 成本按业务部件版本另跑；`
      + `几何没绑定只影响依赖几何的尺寸，不影响有权威尺寸的材料与采购项。</div>`;
    const downstream = target.ok
      ? `<div class="packaging-part-note" data-qqBusinessDownstream="1">`
        + '按绑定的几何件 <b>' + esc(target.part_code) + '</b> 发起单件结论。</div>'
        + `<button id="packagingBusinessPartProcess" class="part-row-action" type="button"`
        + ` data-qqBusinessDownstreamMode="process">工艺推荐</button>`
      : `<div class="packaging-part-note" data-qqBusinessDownstreamReason="1">`
        + `${esc(target.message)}</div>`
        + (sizeTarget.ok
           ? `<div class="packaging-part-note" data-qqBusinessSizeCost="1">`
             + `这一件没有几何：按权威清单的尺寸算材料费。</div>`
             + `<button id="packagingBusinessPartCostBySize" class="part-row-action"`
             + ` type="button">按权威尺寸算材料费</button>`
           : "")
        + (processTarget.ok
           ? `<div class="packaging-part-note" data-qqBusinessProcess="1">`
             + `这一件没有几何：按权威清单的原文与尺寸排工序。</div>`
             + `<button id="packagingBusinessPartProcessByAuthority" class="part-row-action"`
             + ` type="button">工艺推荐（按权威清单）</button>`
           : "");
    actions.innerHTML = downstream + note;
    const run = mode => {
      const button = $(mode === "cost" ? "packagingBusinessPartCost" : "packagingBusinessPartProcess");
      if (button) button.addEventListener("click", () => {
        packagingBusinessPartAnalyze(mode, target.part_code);
      });
    };
    if (target.ok) {
      // 只给「工艺推荐」绑事件：成本属于第 4 阶段（Spec §6.1），2.1 不再有成本入口。
      run("process");
    } else {
      const bySize = $("packagingBusinessPartCostBySize");
      if (bySize && sizeTarget.ok) {
        bySize.addEventListener("click", () => {
          packagingBusinessPartSizeCost(sizeTarget.part_code);
        });
      }
      const byAuthority = $("packagingBusinessPartProcessByAuthority");
      if (byAuthority && processTarget.ok) {
        byAuthority.addEventListener("click", () => {
          packagingBusinessPartProcessByAuthority(processTarget.part_code);
        });
      }
    }
  }
  highlightPackagingBusinessPart(wanted, (binding.component_ids || []).concat(binding.entity_ids || []));
  return row;
}

function packagingPartsItems(doc) {
  const source = Array.isArray(doc && doc.items) ? doc.items : ((doc && doc.parts) || []);
  return source.filter(row => row && typeof row === "object");
}

function packagingPartsQueryString(page, offset) {
  const query = new URLSearchParams();
  query.set("offset", String(offset || 0));
  query.set("limit", String((page && page.limit) || 64));
  ["kind", "role", "outline_status", "min_area_mm2"].forEach(key => {
    const value = page ? page[key] : "";
    if (value !== "" && value !== null && value !== undefined) query.set(key, String(value));
  });
  return query.toString();
}

async function fetchPackagingParts() {
  if (!currentProject) return null;
  // 读失败（非 404）时的空文档形状（Spec `packaging-parts-read-failure-empty-state.md` §2.1）：
  // 左栏空态据此说"读不到"而不是"还没生成，请先跑一键解析"。`status` 取 HTTP 状态码，
  // 网络异常（拿不到状态码）给 `0`。
  const readProblemDoc = (status) => ({
    parts: [], filtered: [], unavailable: [], stats: {}, source: {},
    reviewable: false, built: false,
    read_problem: {code: "parts_unavailable", status: Number(status) || 0, message: ""}});
  const url = `${API}/api/projects/${currentProject}/requirement/packaging-parts`
    + `?${packagingPartsQueryString(packagingPartsPage, 0)}`;
  let res = null;
  try {
    res = await fetch(url);
  } catch (error) {
    // 网络异常：没有状态码（给 0），与 404 / 5xx 都不同形。
    packagingPartsShown = [];
    return readProblemDoc(0);
  }
  // 404 = 端点未上线（那条既有路径逐字不变：仍返回 null，走空态文案，不谎报"解析失败"）。
  if (Number(res.status) === 404) return null;
  if (!res.ok) {
    // 其余非 2xx（含 5xx）= 这一趟读不到：说清状态码，别让用户以为"还没生成"。
    packagingPartsShown = [];
    return readProblemDoc(res.status);
  }
  const doc = await res.json().catch(() => null);
  packagingPartsShown = packagingPartsItems(doc);   // 换页一律从第一页重新累加
  return doc;
}

// "继续加载"：拿下一页并累加（按 part_code 去重），再重画左栏 —— 不许只能看前 64 件。
async function loadMorePackagingParts() {
  const doc = currentPackagingParts || {};
  if (!currentProject || !doc.has_more) return null;
  const offset = packagingPartsShown.length;
  // 这一页读不出来时交给渲染的位置（Spec `packaging-parts-pagination-read-failure.md` §2.2）：
  // 老路子三条失败都折成 `return null`，调用点又丢掉返回值 → 点一下什么都没发生。现在把
  // `page_problem` 写进文档并重画左栏；已列出的零件一件不动（`packagingPartsShown` 不碰），
  // 返回值契约不变（仍是 `null`）。文案一律由纯函数给，分页里不另写一套。
  const failPage = (status) => {
    const problem = {code: "parts_page_unavailable", status: Number(status) || 0, message: ""};
    problem.message = packagingPartsPageReadProblemText(problem);
    currentPackagingParts = Object.assign({}, doc, {page_problem: problem});
    renderTree(currentIR || {});
    return null;
  };
  try {
    const url = `${API}/api/projects/${currentProject}/requirement/packaging-parts`
      + `?${packagingPartsQueryString(packagingPartsPage, offset)}`;
    const res = await fetch(url);
    if (!res.ok) return failPage(res.status);
    const page = await res.json().catch(() => null);
    if (!page) return failPage(res.status);
    const seen = {};
    packagingPartsShown.forEach(row => { seen[String(row.part_code || "")] = true; });
    packagingPartsItems(page).forEach(row => {
      const code = String(row.part_code || "");
      if (!code || seen[code]) return;
      seen[code] = true;
      packagingPartsShown.push(row);
    });
    currentPackagingParts = Object.assign({}, doc, page,
                                           { parts: doc.parts || packagingPartsShown,
                                             page_problem: null });
    renderTree(currentIR || {});
    return page;
  } catch (error) { return failPage(0); }
}
async function fetchPackagingBusinessParts() {
  if (!currentProject) return null;
  // 读失败（非 404）时的空文档形状（Spec `packaging-business-parts-read-failure-note.md` §2.1）：
  // 面板提示据此说"读不到"而不是"已识别的几何区域还不是业务部件清单"。`status` 取 HTTP 状态码，
  // 网络异常（拿不到状态码）给 `0`。
  const readProblemDoc = (status) => ({
    business_parts: [], geometry_evidence: {}, source: {},
    read_problem: {code: "business_parts_unavailable", status: Number(status) || 0, message: ""}});
  try {
    const res = await fetch(`${API}/api/projects/${currentProject}/requirement/`
      + `packaging-business-parts`);
    // 404 = 端点未上线（那条既有路径逐字不变：仍返回 null，退回几何分量并说明原因）。
    if (Number(res.status) === 404) return null;
    if (!res.ok) return readProblemDoc(res.status);
    return await res.json().catch(() => null);
  } catch (error) {
    // 网络异常：没有状态码（给 0）。
    return readProblemDoc(0);
  }
}

async function refreshPackagingParts() {
  // 阶段钩子是纯展示：读回本身不许因为画不出阶段标记而失败（Spec C5）。
  const stage = (name, state) => {
    try { setLoadStage(name, state); } catch (error) { /* 纯展示 */ }
  };
  stage("geometry_parts", "loading");
  stage("business_parts", "loading");
  // 几何零件文档与业务部件清单**并发**开始读（Spec C5）：一段慢不许拖住另一段先出来；
  // 读不到只把自己那一段记成 failed（左栏退回几何分量并说明原因，不假装有权威清单）。
  const [parts, business] = await Promise.all([
    fetchPackagingParts().then(
      value => { stage("geometry_parts", "ready"); return value; },
      error => { stage("geometry_parts", "failed"); return null; }),
    fetchPackagingBusinessParts().then(
      value => { stage("business_parts", "ready"); return value; },
      error => { stage("business_parts", "failed"); return null; }),
  ]);
  currentPackagingParts = parts;
  currentPackagingBusinessParts = business;
  renderTree(currentIR || {});
  // BOM 业务角色的人工映射入口（Spec packaging-part-role-manual-mapping.md §4.5）：
  // 零件文档出来了就把「角色未映射 n 行」一并读出来 —— 不读，用户看不到还有几行没映射。
  await loadPackagingRoleMap().catch(() => null);
  // 空下拉框必须有解释（Spec `packaging-bom-role-unbound-template-disclosure.md` §2.3）：
  // BOM 体上的 `role_unbound_templates_unavailable` 与角色映射面板的 `templates_unavailable`
  // 说的是同一件事，两处都要说，不能一个是空白下拉、另一个才解释。
  await refreshPackagingBomRoleUnboundNote().catch(() => null);
  return currentPackagingParts;
}

// "这一次读不到 BOM"的原因文案（Spec `packaging-bom-role-unbound-note-read-failure.md` §2.1）。
// 纯函数：只认两个码（`bom_unavailable` / `bom_body_unexpected`），别的码 / 没有码给空串。
// 与"读到了、但候选角色读不到"（服务端那份 `role_unbound_templates_unavailable`）是两件事，
// 不许混进同一句话，也不许声称"这些行都有候选角色"。
function packagingRoleUnboundReadProblemText(problem) {
  const row = (problem && typeof problem === "object") ? problem : null;
  const code = row ? String(row.code || "").trim() : "";
  if (code === "bom_unavailable") {
    const where = (Number(row.status) || 0) > 0 ? `HTTP ${Number(row.status)}` : "网络错误";
    return `这一次读不到 BOM（${where}），候选角色的披露也读不到；`
      + "请稍后重试，这不代表这些行都有候选角色。";
  }
  if (code === "bom_body_unexpected") {
    return "这一次读到的 BOM 正文里没有盒型信息，候选角色的披露读不到；"
      + "请稍后重试，这不代表这些行都有候选角色。";
  }
  return "";
}

/* BOM 未映射清单旁边的候选角色披露（Spec `packaging-bom-role-unbound-template-disclosure.md` §2.3）。
   `GET …/requirement/packaging-bom` 的 `role_unbound_templates_unavailable` 非空 =
   "这一趟候选角色没读到"，与"这个盒型确实没有候选角色"分家；`{}` 时什么也不加。 */
async function refreshPackagingBomRoleUnboundNote() {
  if (!currentProject) return null;
  const host = $("packagingRoleMap");
  if (!host) return null;
  // 这一趟读不到 BOM 时，既有的"候选角色暂时读不到（…）"**一件不许回收**（Spec
  // `packaging-bom-role-unbound-note-read-failure.md` §2.2）：老路子从不看 `res.ok`、
  // 也把 `res.json()` 解不出 / `fetch` 抛异常一起并进 `flag = {}`，然后**无条件**清掉旧提示
  // —— 一次读失败就把已经披露的事实擦掉，"读不到"与"本来就没有"同形。读失败一律在清理旧提示
  // **之前**返回，改为 upsert 自己的读失败块（按钩子删自己那一块）。
  const showReadProblem = (problem) => {
    const stale = host.querySelector("[data-qqRoleUnboundReadProblem]");
    if (stale) stale.remove();
    const node = document.createElement("div");
    node.className = "role-map-warning";
    node.setAttribute("data-qqRoleUnboundReadProblem", "1");
    node.textContent = packagingRoleUnboundReadProblemText(problem);
    host.append(node);
    return {read_problem: problem};
  };
  let flag = {};
  try {
    const res = await fetch(API + `/api/projects/${currentProject}/requirement/packaging-bom`);
    // 非 2xx = 这一趟读不到 BOM（既不是"没有 flag"，也不是"这些行都有候选角色"）。
    if (!res.ok) {
      return showReadProblem({code: "bom_unavailable", status: Number(res.status) || 0,
                              message: ""});
    }
    const payload = await res.json().catch(() => ({}));
    // `res.ok` 但正文里没有盒型信息（`{"detail": …}` / 解不出 / 空）：第三种情形。
    const body = (payload && typeof payload.bom === "object" && payload.bom) || null;
    if (!body) {
      return showReadProblem({code: "bom_body_unexpected", status: Number(res.status) || 0,
                              message: ""});
    }
    flag = (body && body.role_unbound_templates_unavailable) || {};
  } catch (error) {
    // 网络异常：没有状态码（给 0），与 5xx 同码不同句。
    return showReadProblem({code: "bom_unavailable", status: 0, message: ""});
  }
  const old = host.querySelector("[data-role-unbound-templates-unavailable]");
  if (old) old.remove();
  if (!flag.code) return flag;
  const note = document.createElement("div");
  note.className = "role-map-warning";
  note.setAttribute("data-role-unbound-templates-unavailable", "1");
  note.textContent = `候选角色暂时读不到（${String(flag.code)}），请稍后重试；`
    + "这不代表该盒型没有候选角色。";
  host.append(note);
  return flag;
}

/* ---------------- 2.1 BOM 业务角色的人工映射（Spec packaging-part-role-manual-mapping.md §4.5） ----------------
   `role=unknown` 的图纸零件**不许**自动贴业务角色名（连续性 Spec §4.4），所以必须有人在页面上
   把"这一行是哪个部件"指定掉。这一栏只做两件事：把「角色未映射 n 行」显示出来，并给每一行一个
   候选角色下拉 + 提交。候选角色**只来自后端**（确认盒型的部件模板），前端不自己拼一份清单。 */
function packagingRoleMapUrl() {
  return `/api/projects/${currentProject}/requirement/packaging-bom/role-map`;
}

// 读不到**不许**显示成"每一行都有业务角色了"（Spec `packaging-silent-degradation-disclosure.md`
// §2.2/§4）：接口显式报错（503 role_map_unavailable）时把真因说出来。
function renderPackagingRoleMapUnavailable(message) {
  const host = $("packagingRoleMap");
  if (host) {
    host.innerHTML = `<div class="role-map-warning" data-role-map-unavailable="1">`
      + `人工角色映射读不到：${esc(String(message || "请稍后重试"))}</div>`;
  }
  return null;
}

// 200 但角色映射正文不可用时的文案（Spec `packaging-role-map-unreadable-body.md` §2.1）。
// 纯函数：只认 `role_map_body_unexpected`，别的码 / 没有码给空串。
// 这一句存在的意义是**不许**把"正文解不出"渲染成"每一行都有业务角色了。"（那是个会被当成结论的界面）。
function packagingRoleMapReadProblemText(problem) {
  const row = (problem && typeof problem === "object") ? problem : null;
  const code = row ? String(row.code || "").trim() : "";
  if (code !== "role_map_body_unexpected") return "";
  return "这一次读到的角色映射正文解不出，请稍后重试；这不代表每一行都有业务角色。";
}

async function loadPackagingRoleMap() {
  if (!currentProject) return null;
  try {
    const res = await fetch(API + packagingRoleMapUrl());
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = payload && payload.detail;
      return renderPackagingRoleMapUnavailable(
        (detail && (detail.message || detail)) || (payload && payload.message)
        || `HTTP ${res.status}`);
    }
    // `res.ok` 只说明 HTTP 成功，**不是**"正文可用"（Spec `packaging-role-map-unreadable-body.md` §2.2）：
    // 形状不对（`payload.role_map` 不是对象，或三个键一个都没有）此前会被 `|| {}` 吃掉，
    // 渲染成"每一行都有业务角色了。"—— 最危险的结论却由一次读不到触发。合法的 `items: []` 空态照旧渲染。
    const roleMap = (payload && payload.role_map) || null;
    const shaped = (roleMap && typeof roleMap === "object")
      && ("items" in roleMap || "unbound_total" in roleMap
          || "templates_unavailable" in roleMap);
    if (!shaped) {
      const problem = {code: "role_map_body_unexpected", status: Number(res.status) || 0,
                       message: ""};
      return renderPackagingRoleMapUnavailable(packagingRoleMapReadProblemText(problem));
    }
    return renderPackagingRoleMap(roleMap);
  } catch (error) {
    return renderPackagingRoleMapUnavailable("网络错误，请稍后重试");
  }
}

function renderPackagingRoleMap(roleMap) {
  const host = $("packagingRoleMap");
  if (!host) return roleMap || null;
  const rows = Array.isArray(roleMap && roleMap.items) ? roleMap.items : [];
  const total = Number((roleMap && roleMap.unbound_total) || rows.length || 0);
  const summary = `<div class="role-map-summary">角色未映射 ${total} 行</div>`;
  // 部件模板这一趟没读到 (Spec `packaging-silent-degradation-disclosure.md` §2.3/§4)：
  // **不许**显示成"该盒型没有部件模板"，也不许把空下拉当成事实。
  const unavailable = (roleMap && roleMap.templates_unavailable) || {};
  const unavailableNote = unavailable.code
    ? `<div class="role-map-warning" data-role-map-templates-unavailable="1">`
      + `模板暂时读不到（${esc(String(unavailable.code))}），请稍后重试；`
      + `这不代表该盒型没有部件模板。</div>`
    : "";
  if (!total) {
    host.innerHTML = summary + unavailableNote
      + '<div class="role-map-empty">每一行都有业务角色了。</div>';
    return roleMap;
  }
  const fallback = Array.isArray(roleMap.role_candidates) ? roleMap.role_candidates : [];
  const body = rows.map(row => {
    const options = (Array.isArray(row.role_candidates) && row.role_candidates.length
      ? row.role_candidates : fallback);
    const optionHtml = options
      .map(name => `<option value="${esc(String(name))}">${esc(String(name))}</option>`).join("");
    const picker = optionHtml
      ? `<select class="role-map-role" data-role-map-role="${esc(String(row.item_key))}">${optionHtml}</select>`
      : (unavailable.code
        ? '<span class="role-map-note">候选角色暂时读不到（部件模板查询失败），请稍后重试</span>'
        : '<span class="role-map-note">没有候选角色（确认盒型的部件模板为空）</span>');
    return '<div class="role-map-row" data-role-map-row="' + esc(String(row.item_key)) + '"'
      + ` data-part-code="${esc(String(row.part_code || ""))}">`
      + `<span class="role-map-key">${esc(String(row.item_key))}</span>`
      + `<span class="role-map-part">${esc(String(row.part_code || ""))}</span>`
      + picker
      + `<button class="btn-small" data-role-map-submit="${esc(String(row.item_key))}"`
      + ` data-part-code="${esc(String(row.part_code || ""))}"`
      + (optionHtml ? "" : " disabled") + ">提交</button></div>";
  }).join("");
  host.innerHTML = summary + unavailableNote + `<div class="role-map-rows">${body}</div>`;
  host.querySelectorAll("[data-role-map-submit]").forEach(button => {
    button.addEventListener("click", () => { submitPackagingRoleMap(button); });
  });
  return roleMap;
}

async function submitPackagingRoleMap(button) {
  const itemKey = String((button && button.dataset && button.dataset.roleMapSubmit) || "");
  const partCode = String((button && button.dataset && button.dataset.partCode) || "");
  const row = button && button.closest ? button.closest("[data-role-map-row]") : null;
  const picker = row ? row.querySelector("[data-role-map-role]") : null;
  const role = picker ? String(picker.value || "") : "";
  if (!itemKey || !role) return null;
  const res = await fetch(API + packagingRoleMapUrl(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_key: itemKey, part_code: partCode, role }),
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (payload && (payload.detail || payload.error)) || "";
    status(typeof detail === "string" && detail ? detail : `映射提交失败（HTTP ${res.status}）`);
    return null;
  }
  status(payload.changed === false
    ? `这一行的角色本来就是「${role}」，没有变化。`
    : `已把 ${itemKey} 的业务角色映射成「${role}」。`);
  renderPackagingRoleMap((payload && payload.role_map) || {});
  return payload;
}

// drawing-flow 走到终态后必须**重新拉一次**零件文档（Spec
// `e2e-packaging-dwg-quote-tech-continuity.md` §4.1）：零件是链路跑完才产出的，
// 不刷新的话左栏会一直停在空态占位文案上（线上那条现象：跑完了还是只有一个标题）。
async function refreshPackagingPartsAfterDrawingFlow(flowState) {
  if (flowState) currentDrawingFlowState = flowState;
  const doc = await refreshPackagingParts().catch(() => null);
  const rows = Array.isArray(doc && doc.parts) ? doc.parts.length : 0;
  status(rows
    ? `零件清单已刷新：${rows} 件（点一行可在右栏看这一件）`
    : "零件文档还没有内容，详见上方步骤表与前置条件。");
  return doc;
}

// 点一件零件：留在**当前看板**里看它（Spec §4.1「点击零件仍在看板内展开」）。
// 不跳页、不换视图：右栏切到图纸零件面板，左栏这一行保持选中。
function openPackagingPartInBoard(partCode) {
  const code = String(partCode || "");
  if (!code) return null;
  const tree = $("tree");
  if (tree) {
    tree.querySelectorAll(".part-item").forEach(row => {
      row.classList.toggle("active", String(row.dataset.partId || "") === code);
    });
  }
  return selectPackagingPart(code);
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
  currentDrawingFlowState = state || payload;
  renderDrawingFlowPanel(currentDrawingFlowState);
  const summary = cadIrSummaryOf(currentDrawingFlowState);
  status(summary.entities
    ? `图纸解析完成：CAD IR 实体 ${summary.entities} · 图层 ${summary.layers}`
    : "图纸解析链路已跑完，详见下方步骤表。");
  setWorkflow("review", "DWG / DXF 已由服务端图纸解析链路处理，请核对步骤与 CAD IR 摘要。");
  // 左栏零件文档：链路终态后重新拉一次（端点未上线时为空态，不影响链路结论）。
  await refreshPackagingPartsAfterDrawingFlow(currentDrawingFlowState).catch(() => null);
  return currentDrawingFlowState;
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
/* ---------------- 2.1 的装载态与分阶段加载（Spec tech-load-states-and-progressive-load.md） ----
   装载态只有一个来源（C1）：`openProject()` 进来置 `loading`、`finally` 收口 `ready` / `failed`，
   别处只读 —— 这样"还在加载"才不会被告知成"没有可解析的图纸"。
   四段各自的可见性（C5）：`core` / `flow` / `geometry_parts` / `business_parts`，每段闭集
   `loading | ready | failed`；阶段块只在装载期出现（装载结束后由正文自己说话）。 */
const PAGE_LOAD_STAGE_NAMES = ["core", "flow", "geometry_parts", "business_parts"];
const PAGE_LOAD_STAGE_LABELS = {
  core: "项目本体", flow: "图纸解析链路", geometry_parts: "几何分量", business_parts: "业务部件",
};
const PAGE_LOAD_STAGE_STATE_LABELS = { loading: "读取中", ready: "已就绪", failed: "读取失败" };
let pageLoadState = "idle";
let pageLoadStartedAt = 0;
let pageLoadStages = {
  core: "ready", flow: "ready", geometry_parts: "ready", business_parts: "ready",
};

// 已用时长（C4）：不足 1 秒说"不到 1s"、秒级说整数秒、超过一分钟说分和秒；量不出（含倒退）回空串。
function pageLoadElapsedText(startedAt, now) {
  const start = (startedAt === null || startedAt === undefined) ? NaN : Number(startedAt);
  const stamp = (now === null || now === undefined) ? NaN : Number(now);
  if (!Number.isFinite(start) || !Number.isFinite(stamp) || stamp < start) return "";
  const elapsed = stamp - start;
  if (elapsed < 1000) return "已用不到 1s";
  const seconds = Math.floor(elapsed / 1000);
  if (elapsed < 60000) return `已用 ${seconds}s`;
  return `已用 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

// 分阶段加载的那句话（C5）：先报失败、再报在途，全就绪才说 4/4；认不出的入参回空串。
function loadStagesLine(stages) {
  const rows = Array.isArray(stages)
    ? stages.map(item => (item && typeof item === "object")
        ? { name: String(item.name || item.stage || ""), state: String(item.state || "") }
        : { name: "", state: "" })
    : ((stages && typeof stages === "object")
        ? Object.keys(stages).map(name => ({ name: name, state: String(stages[name]) }))
        : []);
  const known = rows.filter(row => row.name
    && (row.state === "loading" || row.state === "ready" || row.state === "failed"));
  if (!known.length) return "";
  const total = known.length;
  const count = state => known.filter(row => row.state === state).length;
  const ready = count("ready");
  const loading = count("loading");
  const failed = count("failed");
  if (failed && loading) return `已就绪 ${ready}/${total} 段，${failed} 段读取失败`;
  if (loading) return `已就绪 ${ready}/${total} 段，${loading} 段读取中`;
  if (failed) return `已就绪 ${ready}/${total} 段，${failed} 段读取失败`;
  return `已就绪 ${ready}/${total} 段`;
}

// 阶段块：四段各自的钩子（C5）。装载期才画（节点在首屏占位里），装载结束后不残留。
function renderPageLoadStages() {
  if (typeof document === "undefined" || !document.querySelector) return null;
  const host = document.querySelector("[data-qq-page-stages]");
  if (!host) return null;
  host.innerHTML = PAGE_LOAD_STAGE_NAMES.map(name => {
    const state = pageLoadStages[name] || "loading";
    return `<div class="page-load-stage" data-qq-stage="${name}"`
      + ` data-qq-stage-state="${state}">`
      + `${PAGE_LOAD_STAGE_LABELS[name] || name}：${PAGE_LOAD_STAGE_STATE_LABELS[state] || state}</div>`;
  }).join("") + `<div class="page-load-stages-line">${loadStagesLine(pageLoadStages)}</div>`;
  return host;
}

// 每一段状态变化都只从这里进（唯一来源：`openProject` 与 `refreshPackagingParts`）。
function setLoadStage(name, state) {
  if (PAGE_LOAD_STAGE_NAMES.indexOf(name) < 0) return null;
  pageLoadStages[name] = (state === "ready" || state === "failed") ? state : "loading";
  return renderPageLoadStages();
}

// 看板动作的三态判定（C2）：纯函数，不读 DOM —— `run()` 里读按钮 / 装载态后调用它。
// "还不知道 / 还在读"一律回 loading，**不许**说成"没有可解析的图纸"。
function parseActionReadiness(input) {
  const row = (input && typeof input === "object") ? input : {};
  const state = (row.state === null || row.state === undefined) ? "" : String(row.state);
  const disabled = Boolean(row.disabled);
  const reason = (row.reason === null || row.reason === undefined) ? "" : String(row.reason).trim();
  if (state !== "ready" && state !== "failed") {
    const started = (row.startedAt === null || row.startedAt === undefined)
      ? NaN : Number(row.startedAt);
    if (!Number.isFinite(started)) {
      return { code: "loading", message: "图纸信息还在加载中（已用不到 1s），请稍候。" };
    }
    const stamp = (row.now === null || row.now === undefined) ? Date.now() : Number(row.now);
    const seconds = Number.isFinite(stamp) ? Math.floor((stamp - started) / 1000) : 0;
    return { code: "loading", message: `图纸信息还在加载中（已用 ${seconds}s），请稍候。` };
  }
  if (!disabled) return null;
  if (reason) return { code: "not-ready", message: reason };
  return { code: "not-ready", message: "当前没有可解析的图纸，请先上传 2D 工程图。" };
}

async function openProject(pid) {
  currentProject = pid;
  currentSelectedId = null;
  diffPick = []; $("diffView").innerHTML = "";
  // 装载态与首屏占位（Spec C1 / C4）：进函数第一件事就说"在读"，并在**发第一个请求之前**
  // 把占位画进 #tree —— 之前这段时间里 #tree 是空的、按钮灰着、状态栏还没字。
  pageLoadState = "loading";
  pageLoadStartedAt = Date.now();
  // 阶段推进是纯展示：任何一步画不出来都不许挡住加载本身（沙箱 / 老壳里没有这套钩子也一样）。
  const markStage = (name, state) => {
    try { setLoadStage(name, state); } catch (error) { /* 纯展示 */ }
  };
  let loadFailed = false;
  try {
    PAGE_LOAD_STAGE_NAMES.forEach(name => { pageLoadStages[name] = "loading"; });
    const treeHost = $("tree");
    if (treeHost) {
      treeHost.classList.remove("empty-state");
      treeHost.innerHTML = "";
      const skeleton = document.createElement("div");
      skeleton.className = "page-load-skeleton";
      skeleton.setAttribute("data-qq-page-skeleton", "1");
      skeleton.textContent = `正在读取项目…${pageLoadElapsedText(pageLoadStartedAt, Date.now())}`;
      const stageHost = document.createElement("div");
      stageHost.setAttribute("data-qq-page-stages", "1");
      skeleton.appendChild(stageHost);
      treeHost.appendChild(skeleton);
      renderPageLoadStages();
    }
  } catch (error) { /* 占位与阶段块都是纯展示：画不出来也不许挡住加载本身 */ }
  try {
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
    markStage("core", "ready");

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
    if (entry === "drawing_flow") {
      // 进入即读回（Spec `packaging-parts-entry-readback.md` §4 A1–A3）：零件文档是**持久化**的
      // （`packaging_parts.load_parts()` 不带 parts_id 就是最新一版），重新进入 2.1 必须把它读回来，
      // 否则左栏永远走空态、还会催用户重跑一遍本来就有的解析。
      // 两条路各自兜住：链路状态读不到**不许**牵连零件读回（§4 A2）。
      // 两段各自的阶段状态跟着走（Spec C5）：单段读失败只让自己那一段 failed。
      try { await loadDrawingFlowPanel(); markStage("flow", "ready"); }
      catch (error) { markStage("flow", "failed"); /* 链路状态那一路自己兜 */ }
      try { await refreshPackagingParts(); } catch (error) { /* 读不到由空态文案说清 */ }
    } else {
      // 非图纸项目没有这三段要读（视觉链路 / 3D 导入）：如实记成就绪，不许挂着"读取中"。
      ["flow", "geometry_parts", "business_parts"].forEach(name => { markStage(name, "ready"); });
    }

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
  } catch (error) {
    loadFailed = true;
    throw error;
  } finally {
    pageLoadState = loadFailed ? "failed" : "ready";
    markStage("core", pageLoadState);
    // 占位必须在两条路都被清掉（成功路径上正文会替换它，但"这个项目没有正文"时必须自己收）。
    try {
      const skeleton = document.querySelector("[data-qq-page-skeleton]");
      if (skeleton && typeof skeleton.remove === "function") skeleton.remove();
    } catch (error) { /* 占位清理是纯展示 */ }
    // 装载期的 renderTree() 只画了"正在读取零件文档…"（C4），装载收口后要按真结果重画一次；
    // 视觉链路本来就已经画过了，不重复（重复会丢掉选中态的高亮）。
    if (!loadFailed && currentDrawingEntry === "drawing_flow") {
      try { renderTree(currentIR || {}); } catch (error) { /* 重画失败不影响加载结果 */ }
    }
  }
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
      button.className = `part-subaction part-subaction-${mode}`;
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

// 被过滤分量的原因 → 中文（Spec `packaging-parts-component-chaining.md` §2.4 的第三句）。
// 只做展示映射：值来自后端 `filtered_reason_mix`，前端不参与判定。
const PACKAGING_FILTER_REASON_LABELS = {
  area_over_max: "面积超限",
  edge_over_max: "长边超限",
  area_under_min: "面积过小",
  no_curve_entity: "没有可制造曲线",
  unknown: "原因未知",
};

// 一件零件的尺寸文案（展开长×宽，单位未确认时明说"待确认"）。
function packagingPartSizeText(part) {
  const length = (part.unfolded_length_mm === null || part.unfolded_length_mm === undefined)
    ? "" : String(part.unfolded_length_mm);
  const width = (part.unfolded_width_mm === null || part.unfolded_width_mm === undefined)
    ? "" : String(part.unfolded_width_mm);
  return (length || width) ? `展开 ${length}×${width} mm` : "展开尺寸待确认";
}

// 零件清单树：只重建 #tree 内部（2.1 固定左栏），不碰右栏 3D 画布。
function renderTree(ir) {
  const tree = $("tree");
  if (!tree) return;
  tree.innerHTML = "";
  // 视觉链路不给图纸零件面板留位置：切回视觉 IR 时把面板收掉（右栏内容互斥）。
  if (currentDrawingEntry !== "drawing_flow") hidePackagingPartPanel();
  // DWG / DXF 链路的左栏是零件文档（不是视觉 IR）：行数 = stats.part_total，
  // 空态必须说清"为什么没有零件 + 下一步"（Spec C4）。
  if (currentDrawingEntry === "drawing_flow") {
    // 装载期间不许画终态文案（Spec C4）： "还没有零件 / 还没有权威清单"是**读完之后**的结论，
    // 读回来之前只能是"正在读"—— 否则一次慢读就被说成"这个项目没有零件"。
    if (pageLoadState === "loading") {
      const loadingNote = document.createElement("div");
      loadingNote.className = "parts-loading-note";
      loadingNote.setAttribute("data-qq-parts-loading", "1");
      loadingNote.textContent = `正在读取零件文档…`
        + `${pageLoadElapsedText(pageLoadStartedAt, Date.now())}`;
      tree.appendChild(loadingNote);
      return;
    }
    const business = packagingBusinessPartRows(currentPackagingBusinessParts);
    if (business.length) { renderPackagingBusinessTree(tree, business); return; }
    const doc = currentPackagingParts || {};
    // 行与三笔账的取数在 `packagingGeometryListing()`（分页累加优先；读不到时不许吃上一份的累加行）。
    const listing = packagingGeometryListing(doc);
    const rows = listing.rows;
    const total = listing.total;
    const kindTotal = listing.kindTotal;
    const preconditions = (currentDrawingFlowState && currentDrawingFlowState.preconditions) || [];
    // 两笔账的同屏对账（Spec「两笔账同屏对账」§2.2）：几何账与业务账
    // 摆在同一行说清关系 —— 263 是几何连通分量、28 才是业务部件；空串（两边都取不到数）不渲染。
    const ledgerLine = packagingTwoLedgersLine(doc, currentPackagingBusinessParts);
    if (ledgerLine) {
      const ledgerNote = document.createElement("div");
      ledgerNote.className = "packaging-ledger-reconciliation";
      ledgerNote.setAttribute("data-qq-ledger-reconciliation", "1");
      ledgerNote.textContent = ledgerLine;
      tree.appendChild(ledgerNote);
    }
    tree.classList.toggle("empty-state", !rows.length);
    // 写后重读失败（Spec `packaging-parts-reread-failure-after-write.md` §2.3）：这一笔已经提交
    // 成功，只是列表没能重新读回来 —— **有零件也要说**（上面/below 那些行一个都不许少）。
    const rereadProblemText = packagingPartsRereadProblemText(doc.reread_problem);
    if (rereadProblemText) {
      const rereadNote = document.createElement("div");
      rereadNote.className = "part-reread-problem-note";
      rereadNote.dataset.qqPartsRereadProblem = "1";
      rereadNote.textContent = rereadProblemText;
      tree.appendChild(rereadNote);
    }
    if (!rows.length) {
      const emptyNote = packagingBusinessImportNote(currentPackagingBusinessParts);
      if (emptyNote) tree.appendChild(emptyNote);
      const emptyText = document.createElement("div");
      emptyText.textContent = packagingPartsEmptyText(doc, preconditions, currentDrawingFlowState);
      tree.appendChild(emptyText);
      return;
    }
    // 有几何分量但**还没有**业务部件清单：先说清"下面这些是几何证据，不是业务零件"，
    // 并给一个导入权威清单的出口（Spec §2 第 5 条）。
    const missingNote = packagingBusinessImportNote(currentPackagingBusinessParts);
    if (missingNote) tree.appendChild(missingNote);
    // 覆盖率行 + 批量入口：三态文案由 packagingSolidCoverageText() 给（未算过 = "未生成"）。
    const coverage = document.createElement("div");
    coverage.className = "packaging-solid-coverage";
    coverage.dataset.qqSolidCoverage = "1";
    coverage.textContent = packagingSolidCoverageText(doc);
    if (doc.solids_stale_reason) {
      coverage.dataset.solidsStale = String(doc.solids_stale_reason);
    }
    // 批量挤出这个入口从包装 2.1 **撤掉**（Spec §6.1）：3D 属于历史/审计能力，
    // 包装页只看 CAD 平面图；`packagingPartsSolidBatch()` 本体保留（历史结论与诊断仍可回查）。
    tree.appendChild(coverage);
    // 角色出处（Spec `packaging-parts-role-lookup-disclosure.md` §2.4）：全 `unknown` 时先说清
    // 是"这一次没算出来"还是"这些图层名认不出"，再列零件行。
    const roleLookupNote = packagingRoleLookupNote(doc);
    if (roleLookupNote) tree.appendChild(roleLookupNote);
    // 几何区域**只**进"几何诊断 / 映射证据"折叠区，默认不展开（Spec
    // `packaging-28-part-auto-resolution-and-2d-board-cleanup.md` §4）：263 个分量是几何事实，
    // 不是客户说的 28 件业务部件 —— 两笔账不许混着都叫"零件"，左栏也不许回退成几何清单。
    const diagnostics = document.createElement("details");
    diagnostics.className = "geometry-diagnostics";
    diagnostics.dataset.qqGeometryDiagnostics = "1";
    const diagnosticsSummary = document.createElement("summary");
    diagnosticsSummary.textContent = `几何诊断 / 映射证据（几何区域 ${total} 个，默认收起）`;
    diagnostics.appendChild(diagnosticsSummary);
    const diagnosticsBody = document.createElement("div");
    diagnosticsBody.className = "geometry-diagnostics-body";
    diagnostics.appendChild(diagnosticsBody);
    tree.appendChild(diagnostics);
    // 一件几何分量的行：自带 `dataset.partId`，点击先在看板内定位（高亮这一行），再由
    // openPackagingPartInBoard() 交给图纸零件自己的选中路径 selectPackagingPart(part_code)
    // —— 留在当前看板里展开，既不跳页，也不会清空右栏（不走视觉链路的 selectPart）。
    // 名字故意叫"几何分量"：这些行是 CAD 连通分量，不是业务部件（两个词不许混用）。
    const geometryComponentRow = part => {
      const row = document.createElement("div");
      row.className = "part part-item";
      row.dataset.partId = part.part_code || "";
      row.addEventListener("click", () => openPackagingPartInBoard(part.part_code || ""));
      const layers = Array.isArray(part.layers) ? part.layers.join(" / ") : "";
      const size = packagingPartSizeText(part);
      // 这一件的 3D 是不是当前这一版零件算的（Spec `packaging-solids-parts-version-binding.md`
      // §2.3）：过期行必须把这句话放在行上，不许让页面照旧显示"有 3D"。
      const solidStale = packagingPartSolidStaleText(part);
      if (solidStale) row.dataset.solidStale = String(part.stale_reason || "");
      row.innerHTML = `<div class="part-icon part-icon-box" aria-hidden="true"></div>`
        + `<div class="part-info"><div class="part-name">${esc(part.part_code || "")} `
        + `${esc(part.name || "")}</div><div class="part-type">${esc(size)}`
        + `${layers ? " · " + esc(layers) : ""}</div>`
        + `${solidStale ? `<div class="part-solid-stale">${esc(solidStale)}</div>` : ""}</div>`;
      // 材料为空的行同样必须**看得见补录入口**（Spec
      // `packaging-parts-in-card-and-material-fill.md` §2.3 第 2 条）：与补料厚同一个渲染
      // 循环、同一种控件形状（`part-material-fix` / `part-thickness-fix`）。
      const materialSpec = part.material
        && (part.material.spec || part.material.name || part.material);
      if (!materialSpec) {
        const matFix = document.createElement("button");
        matFix.className = "part-row-action part-material-fix";
        matFix.type = "button";
        matFix.textContent = "补材料";
        matFix.addEventListener("click", (event) => {
          event.stopPropagation();
          packagingPartSetMaterial(part.part_code || "");
        });
        row.appendChild(matFix);
      }
      // 未闭合的件必须**看得见下一步**（Spec `packaging-open-outline-part-needs-a-way-out.md`
      // §2.3）：与补材料 / 补料厚同一个渲染循环、同一种控件形状（`part-outline-fix`）。
      // 「重算轮廓」走 `…/outline/recompute`（放额度只重算这一件），
      // 「按包围盒签字确认」走 `…/outline/confirm`（几何状态仍是 open，另立留痕）。
      const outlineConfirmed = !!(part.outline_confirmation && part.outline_confirmation.bound_by);
      if (String(part.outline_status || "") !== "closed" && !outlineConfirmed) {
        const exit = packagingOutlineExit(part);
        const outlineFix = document.createElement("button");
        outlineFix.className = "part-row-action part-outline-fix";
        outlineFix.type = "button";
        outlineFix.textContent = exit.label;
        outlineFix.addEventListener("click", (event) => {
          event.stopPropagation();
          packagingPartOutlineExitPost(part.part_code || "", exit.action);
        });
        row.appendChild(outlineFix);
      } else if (outlineConfirmed) {
        // 人签过字的件要能一眼看出"这是人放的，不是几何闭合的"（Spec §2.4）。
        const signoff = document.createElement("span");
        signoff.className = "part-outline-signoff";
        signoff.textContent = "人工签字·按包围盒";
        row.appendChild(signoff);
      }
      // 料厚为空的行必须**看得见补录入口**（Spec `packaging-parts-thickness-facts.md` §2.5）：
      // 真图 55 件不可挤出里有 51 件卡在"没有料厚"，页面上必须有地方能补。
      if (part.thickness_mm === null || part.thickness_mm === undefined) {
        const fix = document.createElement("button");
        fix.className = "part-row-action part-thickness-fix";
        fix.type = "button";
        fix.textContent = "补料厚";
        fix.addEventListener("click", (event) => {
          event.stopPropagation();
          packagingPartSetThickness(part.part_code || "");
        });
        row.appendChild(fix);
      }
      return row;
    };
    // —— 按种类折叠（Spec `packaging-parts-list-visibility-and-kinds.md` §2.5）——
    // 一种一行（`kind_index` / `kind_key` / 该种件数 / 代表尺寸），点开看这一种的件；
    // 种类**由后端的 `kind_key` 决定**，前端不自己按长宽聚类（同尺寸不同轮廓是两种）。
    const kindCounts = (doc && doc.kind_counts) || {};
    const groups = [];
    const byKind = {};
    rows.forEach(part => {
      const key = String((part && part.kind_key) || "")
        || ("kind-" + String((part && part.kind_index) || 0));
      if (!byKind[key]) {
        byKind[key] = { key: key, index: Number((part && part.kind_index) || 0), parts: [] };
        groups.push(byKind[key]);
      }
      byKind[key].parts.push(part);
    });
    groups.sort((a, b) => (a.index - b.index) || (a.key < b.key ? -1 : 1));
    groups.forEach(group => {
      const first = group.parts[0] || {};
      const count = Number(kindCounts[group.key]) || group.parts.length;
      const head = document.createElement("div");
      head.className = "part kind-row";
      head.dataset.kindIndex = String(group.index);
      head.dataset.kindKey = group.key;
      head.innerHTML = `<div class="part-icon part-icon-box" aria-hidden="true"></div>`
        + `<div class="part-info"><div class="part-name">第 ${group.index} 种 · 共 ${count} 件</div>`
        + `<div class="part-type">代表件 ${esc(first.part_code || "")} · `
        + `${esc(packagingPartSizeText(first))}</div></div>`;
      const list = document.createElement("div");
      list.className = "kind-parts";
      list.dataset.kindParts = group.key;
      list.hidden = true;
      group.parts.forEach(part => { list.appendChild(geometryComponentRow(part)); });
      // 一种一行：点这一行展开/收起这一种的件（默认收起，否则 263 件会淹掉种类）。
      head.addEventListener("click", () => { list.hidden = !list.hidden; });
      diagnosticsBody.appendChild(head);
      diagnosticsBody.appendChild(list);
    });
    // 三笔账分三句说（Spec §2.5 与 `packaging-parts-component-chaining.md` §2.4 同一口径）：
    // 已显示的件数、共多少件（全量真值）、被过滤掉的分量数。第一句永远在。
    const countNote = document.createElement("div");
    countNote.className = "part-count-note";
    countNote.dataset.qqPartsShown = "1";
    countNote.textContent = `已显示 ${rows.length} 个几何分量，共 ${total} 个（${kindTotal} 种形状）`;
    tree.appendChild(countNote);
    // 第二句 + 继续加载：翻页拿下一页，不许只能看前 64 件。
    const missing = Math.max(0, total - rows.length);
    if (missing > 0) {
      const note = document.createElement("div");
      note.className = "part-truncated-note";
      note.textContent = `还有 ${missing} 个几何分量未列出（只显示前 ${rows.length} 个）`;
      const more = document.createElement("button");
      more.id = "packagingPartsLoadMore";
      more.className = "btn btn-secondary";
      more.type = "button";
      more.textContent = `继续加载（还有 ${missing} 件）`;
      more.disabled = !doc.has_more;
      more.addEventListener("click", () => { loadMorePackagingParts(); });
      note.appendChild(more);
      // 这一页没读出来时当场说清（Spec `packaging-parts-pagination-read-failure.md` §2.3）：
      // 挂在"继续加载"按钮旁边，按钮**保持可点**（重试就是再点一次）；没有读问题就不出现。
      const pageProblemText = packagingPartsPageReadProblemText(doc.page_problem);
      if (pageProblemText) {
        const wrap = document.createElement("div");
        wrap.className = "part-page-problem-note";
        wrap.dataset.qqPartsPageProblem = "1";
        wrap.textContent = pageProblemText;
        note.appendChild(wrap);
      }
      tree.appendChild(note);
    }
    // 第三笔账（Spec `packaging-parts-component-chaining.md` §2.4）：因面积/长边超限、
    // 面积过小、没有可制造曲线而被**过滤掉**的分量 —— 与上面"未列出（截断）"是两件事，
    // 必须分句说，原因取前两位。原因明细来自读接口的 `filtered_reason_mix`，前端不自己猜。
    const filteredTotal = Number((doc.stats || {}).filtered_total) || 0;
    const filteredMix = (doc.stats || {}).filtered_reason_mix || {};
    if (filteredTotal > 0) {
      const top = Object.keys(filteredMix)
        .sort((a, b) => ((filteredMix[b] || 0) - (filteredMix[a] || 0)) || (a < b ? -1 : 1))
        .slice(0, 2)
        .map(key => `${PACKAGING_FILTER_REASON_LABELS[key] || key} ${filteredMix[key]}`)
        .join(" / ");
      const filteredNote = document.createElement("div");
      filteredNote.className = "part-filtered-note";
      filteredNote.dataset.qqFilteredNote = "1";
      filteredNote.textContent = top
        ? `另有 ${filteredTotal} 个图元分组未成为零件（${top}）`
        : `另有 ${filteredTotal} 个图元分组未成为零件`;
      tree.appendChild(filteredNote);
    }
    return;
  }
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
  // 包装（packaging / DWG 图纸）项目的右栏是 CAD 平面图，不是 3D：包装模式**不**创建
  // WebGL 渲染器，也不留一块空白画布（Spec
  // `packaging-28-part-auto-resolution-and-2d-board-cleanup.md` §5）。非包装项目一字不改。
  if (packagingCadPlanApplies()) { if (el) el.hidden = true; return; }
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
async function startAllPartProcesses(options) {
  if (allPartsProcessBusy) {
    return { ok: false, error: { code: "busy", message: "正在生成工艺推荐，请稍候。" } };
  }
  if (!currentProject) {
    return { ok: false, error: { code: "no-project", message: "还没有选择项目，无法生成工艺推荐。" } };
  }
  // 图纸项目：批量入口只遍历**业务部件**（Spec §7/§8.10）。几何分量（`currentPackagingParts`
  // 里那几百行 DWG-Pxx）只是证据、不是业务件，绝不对它们启动任务；业务清单还没形成时明确
  // 阻断并说清缺什么，不给出一个点了必然失败的按钮。
  if (currentDrawingEntry === "drawing_flow") {
    const businessRows = packagingBusinessPartRows(currentPackagingBusinessParts);
    if (!businessRows.length) {
      return { ok: false, error: { code: "no-business-parts",
        message: "还没有业务部件清单，无法批量生成工艺推荐；请先导入权威清单或人工建立。" } };
    }
    return startAllPackagingPartProcesses(businessRows);
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
  // 终态事件带 code / message / action / retryable：父壳据此区分"被阻断"与"真失败"，
  // 不用猜文案（Spec C3）。
  function parseDrawingSettle(event, signal) {
    const payload = { action: "parseDrawing" };
    if (signal && typeof signal === "object") {
      payload.code = String(signal.code || "");
      payload.message = String(signal.message || "");
      payload.action_text = String(signal.action || "");
      payload.retryable = signal.retryable !== false;
    } else if (signal) {
      payload.message = String(signal);
    }
    try { window.TechBoardRuntime.publish(event, "parseDrawing", payload); }
    catch (error) { /* 独立打开无运行时 */ }
  }
  // 后台链路放在注册表外：动作条目只负责启动它并秒级回执（deferred），
  // 真正的完成 / 被阻断 / 失败由这里按链路终态推给父壳（Spec C2）。
  async function parseDrawingInBackground() {
    try {
      const result = await parseDrawing();
      if (currentDrawingEntry === "drawing_flow") {
        // GET /drawing-flow 是 {flow, gates, stale, inheritance, preconditions}；
        // 终态判定认平铺的 flow（run_id + steps）。POST 失败时没有状态 → 由 error 兜底。
        const state = (result && typeof result === "object") ? result.drawing_flow : null;
        const flowState = (state && typeof state === "object" && state.flow) ? state.flow : state;
        const signal = drawingFlowTerminalSignal(flowState, parseDrawingError);
        parseDrawingSettle(signal.event, signal);
      } else if (result) {
        parseDrawingSettle("task-completed");
      } else {
        parseDrawingSettle("task-failed", { message: parseDrawingError || "图纸解析失败。",
                                            retryable: true });
      }
    } catch (error) {
      parseDrawingSettle("task-failed", { message: (error && error.message) || "图纸解析失败。",
                                          retryable: true });
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
        // 「还在加载 / 项目打不开 / 3D 导入项目」三种处境必须说得出各自的原因：
        // 判定只有 `parseActionReadiness()` 这一处（Spec C2），灰按钮的理由就是它的 `title`。
        const parseState = {
          state: pageLoadState,
          startedAt: pageLoadStartedAt,
          disabled: Boolean(button && button.disabled),
          reason: button ? button.title : "",
        };
        const readiness = parseActionReadiness(parseState);
        if (readiness) {
          return { ok: false, error: readiness };
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
          // 装载态原样报到看板（Spec C3）：按钮灰着是"还在读"还是"读完了但不能解析"，
          // 看板据此决定提示语；`enabled` 仍以 #btnParse.disabled 为准（不许换来源）。
          state: pageLoadState,
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
    // 成本属第 4 阶段（Spec `packaging-28-part-auto-resolution-and-2d-board-cleanup.md` §6.1）：
    // 财务经理在成本工作台处理，包装 2.1 不再把「成本测算」当零件动作。技术侧的历史视图链
    // 仍保留这个入口，文案据此写明阶段；包装（drawing_flow）项目里它不出现在左侧。
    "part-cost": {
      label: "成本（第 4 阶段）",
      getState: () => ({ visible: currentDrawingEntry !== "drawing_flow" }),
      run: (payload) => runBoardView("part-cost", payload),
    },
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
