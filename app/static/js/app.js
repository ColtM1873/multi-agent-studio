"use strict";

/* ================= 常量 ================= */
const DEFAULT_HTML_PROMPT = "不错，请把你的回答输出为一份html文件，使用你所拥有的FileTools，自拟文件名。";
const EMB_OPTIONS = ["BAAI/bge-m3", "BAAI/bge-large-zh-v1.5", "BAAI/bge-small-zh-v1.5"];
const SSL_OPTIONS = [
  { v: "", label: "无字符串（视数据库设置启用/关闭 SSL）" },
  { v: "?sslmode=prefer", label: "prefer（有 SSL 就启用，无则明文）" },
  { v: "?sslmode=require", label: "require（必须 SSL，不校验证书）" },
  { v: "?sslmode=verify-ca", label: "verify-ca（校验 CA 证书）" },
  { v: "?sslmode=verify-full", label: "verify-full（校验 CA + 主机名）" },
];

/* ================= 工具 ================= */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.json();
}

function toast(msg, isError = false) {
  let wrap = $(".toast-wrap");
  if (!wrap) { wrap = document.createElement("div"); wrap.className = "toast-wrap"; document.body.appendChild(wrap); }
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

/* token 单位换算 */
function tokToUnit(t) {
  t = +t || 0;
  if (t && t % 10000 === 0) return { v: t / 10000, u: "万" };
  if (t && t % 1000 === 0) return { v: t / 1000, u: "千" };
  return { v: t, u: "千" };
}
function unitToTok(v, u) { return Math.round((+v || 0) * (u === "万" ? 10000 : 1000)); }

/* 连接前缀 host 解析 */
function hostOfPrefix(prefix) {
  const m = String(prefix || "").match(/@([^:/\s]+)/);
  return m ? m[1] : "";
}
function isLocalPrefix(prefix) {
  const h = hostOfPrefix(prefix).replace(/^\[|\]$/g, "");
  return h === "localhost" || h === "127.0.0.1" || h === "::1";
}

/* 确认弹窗 */
function askConfirm(prompt) {
  return new Promise(resolve => {
    const mask = document.createElement("div");
    mask.className = "modal-mask";
    mask.innerHTML = `
      <div class="modal">
        <h3>确认</h3>
        <div class="modal-body">${esc(prompt)}</div>
        <div class="modal-actions">
          <button class="btn" data-v="no">否</button>
          <button class="btn primary" data-v="yes">是</button>
        </div>
      </div>`;
    document.body.appendChild(mask);
    mask.querySelectorAll("[data-v]").forEach(b => b.onclick = () => { resolve(b.dataset.v); mask.remove(); });
  });
}

async function checkMcp(transport, url, command) {
  try {
    const r = await api("/api/mcp-check", { method: "POST", body: JSON.stringify({ transport, url: url || null, command: command || null }) });
    return r.ok;
  } catch (e) { return false; }
}

/* 系统设置 */
async function getSettings() {
  if (!settingsCache) settingsCache = await api("/api/settings");
  return settingsCache;
}
async function saveSettings(s) {
  settingsCache = await api("/api/settings", { method: "PUT", body: JSON.stringify(s) });
  return settingsCache;
}

/* 提示音：Web Audio 合成，无需音频文件 */
function playSound(type) {
  if (!type || type === "none") return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const t = ctx.currentTime;
    const tone = (freq, start, dur, vol, endFreq) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, t + start);
      if (endFreq) osc.frequency.exponentialRampToValueAtTime(endFreq, t + start + dur);
      gain.gain.setValueAtTime(vol, t + start);
      gain.gain.exponentialRampToValueAtTime(0.001, t + start + dur);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t + start);
      osc.stop(t + start + dur + 0.02);
    };
    if (type === "ber") tone(1200, 0, 0.28, 0.25, 520);
    else if (type === "ding") tone(880, 0, 0.25, 0.25);
    else if (type === "chime") { tone(660, 0, 0.2, 0.2); tone(880, 0.12, 0.25, 0.2); }
  } catch (e) {}
}

/* 缩放 */
let zoomPct = parseInt(localStorage.getItem("md-zoom") || "100", 10);
function applyZoom() {
  document.documentElement.style.setProperty("--md-font-size", (15 * zoomPct / 100).toFixed(1) + "px");
}
function changeZoom(delta) {
  zoomPct = Math.min(200, Math.max(60, zoomPct + delta));
  localStorage.setItem("md-zoom", String(zoomPct));
  applyZoom();
}

/* 未保存提醒 */
function bindDirty(container) {
  editorDirty = false;
  container.addEventListener("input", () => { editorDirty = true; });
  container.addEventListener("change", () => { editorDirty = true; });
}
async function confirmLeave() {
  if (!editorDirty) return true;
  try { const s = await getSettings(); if (!s.warn_unsaved_changes) return true; } catch (e) {}
  return confirm("有未保存的修改，确定离开？");
}
function maskedPrefix(p) {
  return String(p || "")
    .replace(/\/\/([^:@/]+):[^@/]+@/, "//$1:passwd@")
    .replace(/(@[^:/]+):\d+\//, "$1:port/");
}

/* ================= Markdown 渲染器 ================= */
const md = window.markdownit({
  html: true,
  linkify: true,
  breaks: true,
  highlight(code, lang) {
    if (lang && window.hljs && window.hljs.getLanguage(lang)) {
      try { return '<pre class="hljs"><code>' + window.hljs.highlight(code, { language: lang }).value + "</code></pre>"; } catch (e) {}
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(code) + "</code></pre>";
  },
});
function renderMd(text) { return md.render(text || ""); }

/* ================= 状态 ================= */
const S = { view: "agents", agentId: null, agentName: null, threadId: null, editingDefault: false };
const app = $("#app");
let ws = null;
let isRunning = false;
let currentReplyEl = null;
let editorDirty = false;
let settingsCache = null;
let pinned = localStorage.getItem("pin-follow") === "1";

/* ================= 顶栏 ================= */
function topbar(backLabel, backAction) {
  const back = backAction ? `<button class="btn small" id="backBtn">← 返回</button>` : "";
  return `<div class="topbar">
    <div class="logo">Multi-Agent Studio</div>
    <div class="crumb">${S.agentName || ""}</div>
    <div class="spacer"></div>${back}
  </div>`;
}
function bindBack(cb) { const b = $("#backBtn"); if (b) b.onclick = cb; }

/* ================= 视图调度 ================= */
function render() {
  app.innerHTML = "";
  isRunning = false; ws = null; currentReplyEl = null; editorDirty = false;
  if (S.view === "agents") renderAgents();
  else if (S.view === "editor") renderEditorView();
  else if (S.view === "threads") renderThreadsView();
  else if (S.view === "chat") renderChatView();
}
function goAgents() { S.view = "agents"; S.agentId = null; S.agentName = null; S.threadId = null; render(); }
function goThreads(agentId, agentName) { S.view = "threads"; S.agentId = agentId; S.agentName = agentName; S.threadId = null; render(); }
function goChat(agentId, agentName, threadId) { S.view = "chat"; S.agentId = agentId; S.agentName = agentName; S.threadId = threadId; render(); }

/* ================= 视图1：Agent 列表 ================= */
async function renderAgents() {
  app.innerHTML = topbar();
  const view = document.createElement("div");
  view.className = "view";
  app.appendChild(view);
  view.innerHTML = `
    <div style="display:flex;align-items:center;margin-bottom:20px;gap:12px;">
      <h2 class="section-title" style="margin:0;">Multi-Agent 配置</h2>
      <div class="spacer" style="flex:1;"></div>
      <button class="gear-btn" id="gearBtn" title="系统设置">⚙️</button>
      <button class="btn small" id="editDefaultBtn">编辑默认配置</button>
      <button class="btn primary" id="newBtn">+ 新建 multi-agent</button>
    </div>
    <div id="agentGrid" class="card-grid"><div class="muted">加载中…</div></div>`;

  $("#newBtn").onclick = () => { S.editingDefault = false; S.agentId = null; S.view = "editor"; render(); };
  $("#editDefaultBtn").onclick = () => { S.editingDefault = true; S.agentId = null; S.view = "editor"; render(); };
  $("#gearBtn").onclick = () => openSettings();

  let agents;
  try { agents = await api("/api/agents"); }
  catch (e) { $("#agentGrid").innerHTML = `<div class="empty"><div class="big">⚠️</div>${esc(e.message)}</div>`; return; }

  if (!agents.length) {
    $("#agentGrid").innerHTML = `<div class="empty"><div class="big">📦</div>还没有任何 multi-agent 配置<br/><br/><button class="btn primary" id="newBtn2">+ 新建 multi-agent</button></div>`;
    const n = $("#newBtn2"); if (n) n.onclick = () => { S.editingDefault = false; S.agentId = null; S.view = "editor"; render(); };
    return;
  }

  $("#agentGrid").innerHTML = agents.map(a => `
    <div class="agent-card" data-id="${esc(a.agent_id)}">
      <h3>${esc(a.name)}</h3>
      <div class="meta">
        checkpoint 库：<code>${esc(a.postgres.checkpoint_database)}</code><br/>
        子 agent：${a.sub_agents.length} 个（${a.sub_agents.map(s => esc(s.name)).join("、") || "无"}）<br/>
        主模型：<code>${esc(a.main_agent.llm_provider_name)}</code>
      </div>
      <div class="actions">
        <button class="btn primary small" data-act="open">打开</button>
        <button class="btn small" data-act="edit">编辑</button>
        <button class="btn small" data-act="default">设为默认</button>
        <button class="btn danger small" data-act="del">删除</button>
      </div>
    </div>`).join("");

  $$(".agent-card").forEach(card => {
    const id = card.dataset.id;
    const name = agents.find(a => a.agent_id === id).name;
    card.addEventListener("click", e => { if (e.target.closest("button")) return; goThreads(id, name); });
    $$("button", card).forEach(btn => {
      btn.onclick = (e) => {
        e.stopPropagation();
        const act = btn.dataset.act;
        if (act === "open") goThreads(id, name);
        else if (act === "edit") { S.editingDefault = false; S.agentId = id; S.view = "editor"; render(); }
        else if (act === "default") setDefault(id);
        else if (act === "del") deleteAgent(id, name);
      };
    });
  });
}

async function openSettings() {
  let s;
  try { s = await getSettings(); } catch (e) { toast(e.message, true); return; }
  const mask = document.createElement("div");
  mask.className = "modal-mask";
  mask.innerHTML = `
    <div class="modal" style="width:480px;">
      <h3>⚙️ 系统设置</h3>
      <div class="switch-row">
        <span class="sw-label">修改未保存时提醒 <i class="info-icon">!<span class="tip">编辑 multi-agent 配置时，若做了改动但未保存就离开，弹窗确认。</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_warn" ${s.warn_unsaved_changes ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">显示输入示例（灰色占位字） <i class="info-icon">!<span class="tip">新建 / 编辑 multi-agent 时，空输入框显示灰色示例文字，帮助快速上手。</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_ph" ${s.show_placeholders ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">记忆吸附 <i class="info-icon">!<span class="tip">开启后，主 agent 收到用户消息时会先从记忆库语义检索 N 条相关记忆，附在用户消息里一起传入。此设置影响图编译，进入某个 multi-agent 后不可改动，需退回主界面。</span></i></span>
        <label class="toggle"><input type="checkbox" id="set_mem" ${s.memory_attach ? "checked" : ""}><span class="track"></span></label>
      </div>
      <div class="switch-row">
        <span class="sw-label">吸附记忆条数</span>
        <input type="number" id="set_memnum" value="${s.num_memories_attached}" min="1" max="20" ${s.memory_attach ? "" : "disabled"}>
      </div>
      <div class="switch-row">
        <span class="sw-label">回复完成提示音 <i class="info-icon">!<span class="tip">agent 回复完成时播放的提示音，切换即试听。</span></i></span>
        <select id="set_sound">
          <option value="none" ${s.notification_sound === "none" ? "selected" : ""}>无</option>
          <option value="ber" ${s.notification_sound === "ber" ? "selected" : ""}>ber（下滑音）</option>
          <option value="ding" ${s.notification_sound === "ding" ? "selected" : ""}>ding（清脆单音）</option>
          <option value="chime" ${s.notification_sound === "chime" ? "selected" : ""}>chime（双音上行）</option>
        </select>
      </div>
      <div class="switch-row">
        <span class="sw-label">发送键 <i class="info-icon">!<span class="tip">聊天输入框里，按该键发送消息。</span></i></span>
        <select id="set_send">
          <option value="enter" ${s.send_key === "enter" ? "selected" : ""}>Enter</option>
          <option value="shift_enter" ${s.send_key === "shift_enter" ? "selected" : ""}>Shift + Enter</option>
          <option value="ctrl_enter" ${s.send_key === "ctrl_enter" ? "selected" : ""}>Ctrl + Enter</option>
          <option value="mouse_only" ${s.send_key === "mouse_only" ? "selected" : ""}>仅鼠标（Enter 只换行）</option>
        </select>
      </div>
      <div class="switch-row">
        <span class="sw-label">换行键 <i class="info-icon">!<span class="tip">除发送键外，其余 Enter 组合均换行。</span></i></span>
        <select id="set_newline">
          <option value="enter" ${s.newline_key === "enter" ? "selected" : ""}>Enter</option>
          <option value="shift_enter" ${s.newline_key === "shift_enter" ? "selected" : ""}>Shift + Enter</option>
          <option value="ctrl_enter" ${s.newline_key === "ctrl_enter" ? "selected" : ""}>Ctrl + Enter</option>
        </select>
      </div>
      <div class="modal-actions">
        <button class="btn" id="setCancel">取消</button>
        <button class="btn primary" id="setSave">保存</button>
      </div>
    </div>`;
  document.body.appendChild(mask);
  mask.querySelector("#set_mem").addEventListener("change", e => { mask.querySelector("#set_memnum").disabled = !e.target.checked; });
  mask.querySelector("#set_sound").addEventListener("change", e => playSound(e.target.value));

  const sendSel = mask.querySelector("#set_send");
  const newlineSel = mask.querySelector("#set_newline");
  const updateNewline = () => {
    const sk = sendSel.value;
    newlineSel.querySelectorAll("option").forEach(o => { o.disabled = (o.value === sk && sk !== "mouse_only"); });
    if (newlineSel.selectedOptions[0] && newlineSel.selectedOptions[0].disabled) {
      newlineSel.value = newlineSel.querySelector("option:not([disabled])").value;
    }
  };
  sendSel.addEventListener("change", updateNewline);
  updateNewline();

  mask.querySelector("#setCancel").onclick = () => mask.remove();
  mask.querySelector("#setSave").onclick = async () => {
    try {
      await saveSettings({
        warn_unsaved_changes: mask.querySelector("#set_warn").checked,
        memory_attach: mask.querySelector("#set_mem").checked,
        num_memories_attached: +mask.querySelector("#set_memnum").value || 3,
        notification_sound: mask.querySelector("#set_sound").value,
        send_key: sendSel.value,
        newline_key: newlineSel.value,
        show_placeholders: mask.querySelector("#set_ph").checked,
      });
      mask.remove();
      toast("设置已保存");
    } catch (e) { toast(e.message, true); }
  };
}

async function setDefault(id) {
  try { const cfg = await api("/api/agents/" + id); await api("/api/default", { method: "PUT", body: JSON.stringify(cfg) }); toast("已设为默认配置"); }
  catch (e) { toast(e.message, true); }
}
async function deleteAgent(id, name) {
  if (!confirm(`确定删除配置「${name}」？\n（不会删除数据库里的会话历史）`)) return;
  try { await api("/api/agents/" + id, { method: "DELETE" }); toast("已删除"); renderAgents(); }
  catch (e) { toast(e.message, true); }
}

/* ================= 视图2：配置编辑器 ================= */
async function renderEditorView() {
  const leave = async () => { if (await confirmLeave()) goAgents(); };
  app.innerHTML = topbar("返回", () => leave());
  const view = document.createElement("div");
  view.className = "view";
  app.appendChild(view);
  bindBack(() => leave());

  const isNew = !S.agentId && !S.editingDefault;
  const isDefault = S.editingDefault;
  let cfg;
  if (S.editingDefault) cfg = await api("/api/default");
  else if (S.agentId) cfg = await api("/api/agents/" + S.agentId);
  else cfg = await api("/api/default");

  const canEditSubs = isNew || isDefault;
  const title = isDefault ? "编辑默认配置" : (isNew ? "新建 multi-agent" : `编辑「${cfg.name}」`);

  view.innerHTML = `
    <h2 class="section-title">${esc(title)}</h2>
    ${isNew ? `<div class="muted" style="margin-bottom:16px;">表单已按默认配置预填，请修改差异项。创建后子 agent 不可增删改名。</div>` : ""}
    <div id="editorForm"></div>
    <div style="margin-top:20px;display:flex;gap:12px;justify-content:flex-end;">
      <button class="btn" id="cancelBtn">取消</button>
      <button class="btn primary" id="saveBtn">保存</button>
    </div>`;
  $("#cancelBtn").onclick = () => leave();
  $("#saveBtn").onclick = () => saveConfig(cfg, isNew, isDefault);
  await getSettings().catch(() => {});
  buildForm(cfg, canEditSubs);
  bindDirty(view);
}

const info = (tip) => `<i class="info-icon">!<span class="tip">${tip}</span></i>`;

const PH = {
  agent_id: "例如 my-agent",
  name: "例如 研究助理",
  checkpoint_database: "例如 my_agent_checkpoints",
  prefix: "例如 postgresql://user:passwd@localhost:5432/",
  main_system_prompt: "例如：你是一名研究助理，负责理解用户问题，将复杂任务拆解并委派给合适的子 agent，汇总后给出结构化、准确的回答。请使用中文。",
  main_api_key: "例如 sk-...",
  root_dir: "例如 C:\\Agent_WorkPlace",
  emb_cache: "留空则使用默认缓存目录",
  sub_name: "例如 web_search_agent",
  sub_description: "例如：负责联网搜索，将需要查询的内容与注意事项告知它，它会返回检索到的原文片段。",
  sub_system_prompt: "例如：你是一名联网搜索子 agent，负责为主 agent 检索网页信息，最终输出检索到的原文或相关片段。",
  sub_api_key: "例如 sk-...",
};
const ph = (key) => (settingsCache && settingsCache.show_placeholders !== false) ? ` placeholder="${esc(PH[key])}"` : "";
const slugify = (s) => (s || "").trim().replace(/[^0-9a-zA-Z\u4e00-\u9fa5_-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "agent";

// tooltip 自动感知边界：右侧放不下翻到左侧（事件委托，覆盖动态创建的 info-icon）
document.addEventListener("mouseover", (e) => {
  const icon = e.target.closest && e.target.closest(".info-icon");
  if (!icon) return;
  const tip = icon.querySelector(".tip");
  if (!tip) return;
  tip.classList.remove("tip-left");
  tip.style.visibility = "hidden";
  tip.style.display = "block";
  const rect = icon.getBoundingClientRect();
  const tw = tip.offsetWidth;
  if (rect.right + 16 + tw > window.innerWidth) tip.classList.add("tip-left");
  tip.style.display = "";
  tip.style.visibility = "";
  icon.classList.add("show");
});
document.addEventListener("mouseout", (e) => {
  const icon = e.target.closest && e.target.closest(".info-icon");
  if (icon) icon.classList.remove("show");
});

function unitField(id, val, unit, extraLabel, tip) {
  return `<div class="field"><label>${esc(extraLabel || "")}${tip ? info(tip) : ""}</label>
    <div class="unit-row">
      <input id="${id}" value="${esc(val)}" type="number">
      <select id="${id}_unit"><option value="万" ${unit === "万" ? "selected" : ""}>万</option><option value="千" ${unit === "千" ? "selected" : ""}>千</option></select>
    </div></div>`;
}

function buildForm(cfg, canEditSubs) {
  const form = $("#editorForm");
  const pg = cfg.postgres || {};
  const main = cfg.main_agent || {};
  const emb = main.embedding || {};
  const sum = main.summary || {};
  const ft = main.file_tools || {};
  const ns = (pg.store_namespace || []).join(",");
  const isLocal = isLocalPrefix(pg.prefix);
  const gap = tokToUnit(sum.summarize_gap_tokenwise);
  const flush = tokToUnit(sum.flush_history_tokenwise);

  const suffixCtrl = isLocal
    ? `<input id="f_suffix" value="?sslmode=disable" disabled><span class="hint">本地连接，自动使用 sslmode=disable</span>`
    : `<select id="f_suffix">${SSL_OPTIONS.map(o => `<option value="${esc(o.v)}" ${(pg.suffix || "") === o.v ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select><span class="hint">云端/企业库请选择 SSL 模式</span>`;

  const embInOptions = EMB_OPTIONS.includes(emb.model_name);
  const embSelect = `
    <div class="unit-row">
      <select id="f_emb_model_sel">
        ${EMB_OPTIONS.map(m => `<option value="${esc(m)}" ${m === emb.model_name ? "selected" : ""}>${esc(m)}</option>`).join("")}
        <option value="__custom__" ${!embInOptions ? "selected" : ""}>自定义…</option>
      </select>
    </div>
    <input id="f_emb_model_custom" placeholder="自定义模型名" value="${embInOptions ? "" : esc(emb.model_name)}" style="${embInOptions ? "display:none;" : ""} margin-top:6px;">`;

  const htmlReport = main.html_report;
  const htmlPrompt = main.html_report_prompt || DEFAULT_HTML_PROMPT;

  form.innerHTML = `
    <div class="form-card"><h4>基本信息</h4><div class="form-grid">
      <div class="field"><label>agent_id ${info("唯一标识，用作配置文件名 configs/&lt;id&gt;.json，创建后不可改。")}</label><input id="f_agent_id" value="${esc(cfg.agent_id)}" ${canEditSubs ? "" : "disabled"}${ph("agent_id")}></div>
      <div class="field"><label>名称 name ${info("显示名称，创建后不可改（与历史会话绑定）。")}</label><input id="f_name" value="${esc(cfg.name)}" ${canEditSubs ? "" : "disabled"}${ph("name")}><span class="lock-hint">${canEditSubs ? "" : "创建后不可改"}</span></div>
      <div class="field"><label>checkpoint 数据库（会话历史绑定） ${info("会话历史归属的库名，需先在 pgAdmin 建库，创建后不可改。")}</label><input id="f_cpdb" value="${esc(pg.checkpoint_database)}" ${canEditSubs ? "" : "disabled"}${ph("checkpoint_database")}><span class="lock-hint">${canEditSubs ? "需先在 pgAdmin 建库" : "创建后不可改"}</span></div>
      <div class="field"><label>store 数据库 ${info("长期记忆存储的库名，可与 checkpoint 库相同或不同。")}</label><input id="f_sdb" value="${esc(pg.store_database)}"></div>
      <div class="field"><label>store_namespace（逗号分隔） ${info("记忆存储命名空间，逗号分隔多个层级。")}</label><input id="f_ns" value="${esc(ns)}"></div>
      <div class="field full"><label>连接前缀 prefix <i class="info-icon">!<span class="tip">格式：postgresql://用户名:密码@主机:端口/<br/>例如 postgresql://user:passwd@localhost:5432/<br/><br/>下面会实时显示完整连接串。程序会根据主机自动判断是否本地回环。</span></i></label><input id="f_prefix" value="${esc(pg.prefix)}"${ph("prefix")}></div>
      <div class="field"><label>连接后缀 suffix <i class="info-icon">!<span class="tip">本地 postgres（localhost/127.0.0.1）自动用 sslmode=disable，省一次 SSL 握手。<br/><br/>云端或企业级 postgres 请选择对应 SSL 模式；「无字符串」表示交由数据库设置决定。</span></i></label>${suffixCtrl}</div>
      <div class="field full"><label>完整连接串示例</label><div class="conn-example" id="connExample"></div></div>
    </div></div>

    <div class="form-card"><h4>主 agent</h4><div class="form-grid">
      <div class="field"><label>模型 provider ${info("格式「供应商:模型名」，如 deepseek:deepseek-v4-pro。")}</label><input id="f_llm" value="${esc(main.llm_provider_name)}"></div>
      <div class="field"><label>API Key ${info("该模型供应商的 API 密钥（明文存本地配置）。")}</label><input id="f_apikey" value="${esc(main.api_key)}" type="password"${ph("main_api_key")}></div>
      <div class="field"><label>文件工具根目录 ${info("主 agent 文件工具读写文件的根目录。")}</label><input id="f_rootdir" value="${esc(ft.root_dir)}"${ph("root_dir")}></div>
      <div class="field"><label>embedding 模型 <i class="info-icon">!<span class="tip">无需提前下载，首次配置会自动下载（需连接 Hugging Face Hub，国内网络可能连不上）。若已离线缓存过，可在下方缓存目录直接使用。</span></i></label>${embSelect}</div>
      <div class="field"><label>embedding 缓存目录 ${info("本地模型缓存路径，留空用 Hugging Face 默认缓存。")}</label><input id="f_emb_cache" value="${esc(emb.cache_folder)}"${ph("emb_cache")}></div>
      <div class="field"><label>embedding 维度 ${info("向量维度；bge-m3 为 1024，换模型需对应调整。")}</label><input id="f_emb_dims" value="${esc(emb.dims)}" type="number"></div>
      ${unitField("f_sum_gap", gap.v, gap.u, "阶段性总结阈值", "累计 token 达到该值时触发一次阶段性总结。")}
      ${unitField("f_sum_flush", flush.v, flush.u, "清空历史阈值", "累计 token 达到该值时清空历史（只保留最近几轮）。")}
      <div class="field"><label>清空时保留轮数 ${info("清空历史时保留最近几轮对话。")}</label><input id="f_sum_reserve" value="${esc(sum.reserve_message_round)}" type="number"></div>
      <div class="field full"><label>System Prompt ${info("主 agent 的系统提示词，定义其角色与行为。")}</label><textarea id="f_prompt" rows="8"${ph("main_system_prompt")}>${esc(main.system_prompt)}</textarea></div>
      <div class="field full" style="border-top:1px solid var(--border);padding-top:14px;">
        <label style="flex-direction:row;align-items:center;gap:8px;"><input type="checkbox" id="f_html_report" ${htmlReport ? "checked" : ""}> 启用 HTML 报告 ${info("主 agent 输出完后，询问是否将结果生成 HTML 报告。")}</label>
      </div>
      <div class="field full"><label>HTML 报告生成 prompt ${info("确认生成后注入给主 agent 的提示词（可恢复默认）。")} <button class="btn small" id="resetHtmlBtn" type="button">恢复默认</button></label><textarea id="f_html_prompt" rows="3">${esc(htmlPrompt)}</textarea></div>
    </div></div>

    <div class="form-card"><h4>子 agent（${canEditSubs ? "可增删" : "创建后不可增删改名"}）</h4>
      <div id="subs"></div>
      ${canEditSubs ? `<button class="btn small" id="addSubBtn" type="button">+ 添加子 agent</button>` : ""}
    </div>`;

  // 连接示例实时更新（脱敏 + 释义）
  const refreshExample = () => {
    const p = maskedPrefix($("#f_prefix").value);
    const db = $("#f_cpdb").value || "<数据库名>";
    const suffix = $("#f_suffix").value || "";
    $("#connExample").innerHTML = esc(p + db + suffix) +
      '<div class="muted" style="margin-top:6px;font-size:11px;">组成：<code>postgresql://</code> 协议 · <code>用户名:passwd</code> 登录凭据 · <code>@主机:port</code> 数据库地址 · <code>/数据库名</code> 库名 · <code>?sslmode</code> SSL 模式</div>';
  };
  ["f_prefix", "f_cpdb", "f_suffix", "f_sdb"].forEach(id => { const el = $("#" + id); if (el) el.addEventListener("input", refreshExample); });
  refreshExample();

  // embedding 下拉 ↔ 自定义
  const embSel = $("#f_emb_model_sel"), embCustom = $("#f_emb_model_custom");
  embSel.onchange = () => { embCustom.style.display = embSel.value === "__custom__" ? "" : "none"; };

  $("#resetHtmlBtn").onclick = () => { $("#f_html_prompt").value = DEFAULT_HTML_PROMPT; };

  const subsBox = $("#subs");
  (cfg.sub_agents || []).forEach((s, i) => subsBox.appendChild(subAgentBox(s, i, canEditSubs)));
  const addBtn = $("#addSubBtn");
  if (addBtn) addBtn.onclick = () => subsBox.appendChild(subAgentBox(null, Date.now(), true));
}

function subAgentBox(s, key, canEdit) {
  s = s || { name: "", description: "", system_prompt: "", api_key: "", llm_provider_name: "deepseek:deepseek-v4-pro", mcp_servers: [], summary: { flush_history_tokenwise: 200000, reserve_message_round: 4 } };
  const flush = tokToUnit((s.summary || {}).flush_history_tokenwise);
  const div = document.createElement("div");
  div.className = "subagent-box";
  div.dataset.key = key;
  div.innerHTML = `
    <div class="sub-head"><span class="name">🧩 子 agent</span>${canEdit ? `<button class="btn danger small" data-act="remove" type="button">移除</button>` : ""}</div>
    <div class="form-grid">
      <div class="field"><label>名称 name ${info("子 agent 标识，会作为工具名呈现给主 agent，创建后不可改。")}</label><input data-f="name" value="${esc(s.name)}" ${canEdit ? "" : "disabled"}${ph("sub_name")}></div>
      <div class="field"><label>模型 provider ${info("格式「供应商:模型名」，如 deepseek:deepseek-v4-pro。")}</label><input data-f="llm_provider_name" value="${esc(s.llm_provider_name)}"></div>
      <div class="field"><label>API Key ${info("该子 agent 所用模型的 API 密钥（明文存本地配置）。")}</label><input data-f="api_key" value="${esc(s.api_key)}" type="password"${ph("sub_api_key")}></div>
      <div class="field"><label>清空历史阈值 ${info("该子 agent 累计 token 达到该值时清空历史（只保留最近几轮）。")}</label><div class="unit-row"><input data-f="flush" value="${esc(flush.v)}" type="number"><select data-f="flush_unit"><option value="万" ${flush.u === "万" ? "selected" : ""}>万</option><option value="千" ${flush.u === "千" ? "selected" : ""}>千</option></select></div></div>
      <div class="field"><label>保留轮数 ${info("清空历史时保留最近几轮对话。")}</label><input data-f="reserve" value="${esc((s.summary || {}).reserve_message_round)}" type="number"></div>
      <div class="field full"><label>Description ${info("描述该子 agent 能力，作为工具描述呈现给主 agent。")}</label><textarea data-f="description" rows="3"${ph("sub_description")}>${esc(s.description)}</textarea></div>
      <div class="field full"><label>System Prompt ${info("该子 agent 的系统提示词。")}</label><textarea data-f="system_prompt" rows="6"${ph("sub_system_prompt")}>${esc(s.system_prompt)}</textarea></div>
      <div class="field full"><label>MCP 服务器 ${info("该子 agent 可用的工具来源；程序不负责启动，需外部自行启动。")}</label><div data-f="mcp"></div></div>
    </div>`;

  div.querySelector('[data-act="remove"]')?.addEventListener("click", () => div.remove());

  const mcpBox = div.querySelector('[data-f="mcp"]');
  (s.mcp_servers || []).forEach((m, j) => mcpBox.appendChild(mcpRow(m, j)));
  const addM = document.createElement("button");
  addM.className = "btn small add-mcp"; addM.type = "button"; addM.textContent = "+ 添加 MCP";
  addM.onclick = () => mcpBox.insertBefore(mcpRow(null, Date.now()), addM);
  mcpBox.appendChild(addM);
  return div;
}

function mcpRow(m, key) {
  m = m || { name: "", transport: "http", url: "", command: "", args: [] };
  const div = document.createElement("div");
  div.className = "mcp-row";
  div.innerHTML = `
    <span class="dot gray" data-dot title="点击检测健康状态"></span>
    <input data-m="name" placeholder="名称" value="${esc(m.name)}" style="flex:0.8">
    <select data-m="transport" style="flex:0.6">
      <option value="http" ${m.transport === "http" ? "selected" : ""}>http</option>
      <option value="stdio" ${m.transport === "stdio" ? "selected" : ""}>stdio</option>
    </select>
    <input data-m="url" placeholder="URL（http）" value="${esc(m.url || "")}">
    <input data-m="command" placeholder="命令（stdio）" value="${esc(m.command || "")}">
    <input data-m="args" placeholder="参数，逗号分隔（stdio）" value="${esc((m.args || []).join(","))}">
    <button class="btn danger small" data-act="rm" type="button">×</button>`;
  div.querySelector('[data-act="rm"]').onclick = () => div.remove();

  const dot = div.querySelector("[data-dot]");
  const refresh = async () => {
    dot.className = "dot gray";
    const t = div.querySelector('[data-m="transport"]').value;
    const ok = await checkMcp(t, div.querySelector('[data-m="url"]').value, div.querySelector('[data-m="command"]').value);
    dot.className = "dot " + (ok ? "green" : "red");
  };
  dot.onclick = refresh;
  refresh();
  return div;
}

function collectSubAgent(box) {
  const get = f => box.querySelector(`[data-f="${f}"]`).value;
  const mcp = $$(".mcp-row", box).map(r => ({
    name: r.querySelector('[data-m="name"]').value,
    transport: r.querySelector('[data-m="transport"]').value,
    url: r.querySelector('[data-m="url"]').value || null,
    command: r.querySelector('[data-m="command"]').value || null,
    args: r.querySelector('[data-m="args"]').value ? r.querySelector('[data-m="args"]').value.split(",").map(x => x.trim()).filter(Boolean) : [],
  }));
  return {
    name: get("name"),
    description: get("description"),
    system_prompt: get("system_prompt"),
    api_key: get("api_key"),
    llm_provider_name: get("llm_provider_name"),
    mcp_servers: mcp,
    summary: {
      flush_history_tokenwise: unitToTok(box.querySelector('[data-f="flush"]').value, box.querySelector('[data-f="flush_unit"]').value),
      reserve_message_round: +get("reserve"),
    },
  };
}

function embModelValue() {
  const sel = $("#f_emb_model_sel").value;
  return sel === "__custom__" ? $("#f_emb_model_custom").value : sel;
}

function buildPayload(cfg) {
  const val = id => $(`#${id}`).value;
  return {
    agent_id: val("f_agent_id") || slugify(val("f_name")),
    name: val("f_name"),
    postgres: {
      prefix: val("f_prefix"),
      suffix: val("f_suffix"),
      store_database: val("f_sdb"),
      checkpoint_database: val("f_cpdb"),
      store_namespace: val("f_ns").split(",").map(x => x.trim()).filter(Boolean),
    },
    main_agent: {
      system_prompt: val("f_prompt"),
      api_key: val("f_apikey"),
      llm_provider_name: val("f_llm"),
      file_tools: { root_dir: val("f_rootdir") },
      embedding: {
        model_name: embModelValue(),
        cache_folder: val("f_emb_cache"),
        dims: +val("f_emb_dims"),
        device: (cfg.main_agent.embedding || {}).device || "cpu",
        local_files_only: (cfg.main_agent.embedding || {}).local_files_only ?? true,
      },
      summary: {
        summarize_gap_tokenwise: unitToTok(val("f_sum_gap"), val("f_sum_gap_unit")),
        flush_history_tokenwise: unitToTok(val("f_sum_flush"), val("f_sum_flush_unit")),
        reserve_message_round: +val("f_sum_reserve"),
      },
      html_report: $("#f_html_report").checked,
      html_report_prompt: val("f_html_prompt"),
    },
    sub_agents: $$(".subagent-box").map(collectSubAgent),
    output: cfg.output || { stream_output_dir: "" },
  };
}

async function saveConfig(cfg, isNew, isDefault) {
  try {
    const payload = buildPayload(cfg);
    if (!payload.name) return toast("请填写名称", true);
    if (!payload.postgres.checkpoint_database) return toast("请填写 checkpoint 数据库名", true);

    if (isDefault) { await api("/api/default", { method: "PUT", body: JSON.stringify(payload) }); toast("默认配置已保存"); goAgents(); }
    else if (isNew) { await api("/api/agents", { method: "POST", body: JSON.stringify(payload) }); toast("已创建"); goAgents(); }
    else { payload.agent_id = S.agentId; await api("/api/agents/" + S.agentId, { method: "PUT", body: JSON.stringify(payload) }); toast("已保存"); goAgents(); }
  } catch (e) { toast(e.message, true); }
}

/* ================= 视图3：Thread 列表 ================= */
async function renderThreadsView() {
  app.innerHTML = topbar("返回", () => goAgents());
  const view = document.createElement("div");
  view.className = "view";
  app.appendChild(view);
  bindBack(() => goAgents());
  view.innerHTML = `
    <div style="display:flex;align-items:center;margin-bottom:20px;gap:12px;">
      <h2 class="section-title" style="margin:0;">会话线程 — ${esc(S.agentName)}</h2>
      <div class="spacer" style="flex:1;"></div>
      <button class="btn primary" id="newThreadBtn">+ 新建线程</button>
    </div>
    <div id="threadsBox"><div class="muted">加载中…</div></div>`;
  $("#newThreadBtn").onclick = () => { const n = prompt("新线程名称："); if (n && n.trim()) goChat(S.agentId, S.agentName, n.trim()); };

  let threads;
  try { threads = await api(`/api/agents/${S.agentId}/threads`); }
  catch (e) { $("#threadsBox").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }

  if (!threads.length) {
    $("#threadsBox").innerHTML = `<div class="empty"><div class="big">🧵</div>暂无会话线程<br/><br/><button class="btn primary" id="nt2">+ 新建线程</button></div>`;
    const n = $("#nt2"); if (n) n.onclick = () => { const nm = prompt("新线程名称："); if (nm && nm.trim()) goChat(S.agentId, S.agentName, nm.trim()); };
    return;
  }

  $("#threadsBox").innerHTML = `
    <div style="background:var(--surface);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow);">
      ${threads.map(t => `
        <div class="thread-row" data-tid="${esc(t.thread_id)}">
          <span class="tid">${esc(t.thread_id)}</span>
          <span class="meta"><small>${t.checkpoints} checkpoints</small><small>${esc(t.last_updated || "")}</small></span>
          <button class="btn danger small" data-del="${esc(t.thread_id)}">删除</button>
        </div>`).join("")}
    </div>`;

  $$("#threadsBox .thread-row").forEach(row => {
    const tid = row.dataset.tid;
    row.onclick = () => goChat(S.agentId, S.agentName, tid);
    row.querySelector("[data-del]").onclick = (e) => { e.stopPropagation(); deleteThread(tid); };
  });
}
async function deleteThread(tid) {
  if (!confirm(`确定删除线程「${tid}」？此操作不可撤销。`)) return;
  try { await api(`/api/agents/${S.agentId}/threads/${encodeURIComponent(tid)}`, { method: "DELETE" }); toast("已删除"); renderThreadsView(); }
  catch (e) { toast(e.message, true); }
}

/* ================= 视图4：聊天 ================= */
function setStatusIndicator(mode) {
  const ind = $("#statusInd");
  if (!ind) return;
  if (mode === "thinking") { ind.className = "status-indicator thinking"; ind.innerHTML = '<span class="status-dot thinking"></span>Agent 思考中…'; }
  else if (mode === "answering") { ind.className = "status-indicator answering"; ind.innerHTML = '<span class="status-dot answering"></span>Agent 回答中…'; }
  else { ind.className = "status-indicator"; ind.innerHTML = ""; }
}

function toggleMsgDrawer(e) {
  const existing = $(".msg-drawer-panel");
  if (existing) { existing.remove(); return; }
  const btn = e.currentTarget;
  const panel = document.createElement("div");
  panel.className = "msg-drawer-panel";
  const blocks = $$("#historyMd .user-msg-block");
  if (!blocks.length) {
    panel.innerHTML = '<div class="muted" style="padding:8px;">暂无用户消息</div>';
  } else {
    panel.innerHTML = blocks.map((b, i) => {
      const summary = b.dataset.summary || "";
      const raw = summary || (b.querySelector(".user-msg-quote")?.innerText || "");
      const first = raw.split("\n").map(s => s.trim()).filter(Boolean)[0] || "";
      const label = first.length > 24 ? first.slice(0, 24) + "…" : first;
      return `<div class="msg-drawer-item" data-i="${i}">${esc(label || "（空消息）")}</div>`;
    }).join("");
    panel.querySelectorAll(".msg-drawer-item").forEach(item => {
      item.onclick = () => {
        const el = $$("#historyMd .user-msg-block")[+item.dataset.i];
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        panel.remove();
      };
    });
  }
  btn.appendChild(panel);
}

async function renderChatView() {
  app.innerHTML = topbar("返回线程", () => goThreads(S.agentId, S.agentName));
  const view = document.createElement("div");
  view.className = "chat-view";
  app.appendChild(view);
  bindBack(() => goThreads(S.agentId, S.agentName));

  view.innerHTML = `
    <div class="chat-head">
      <span class="thread-name">🧵 ${esc(S.threadId)}</span>
      <span class="muted">（${esc(S.agentName)}）</span>
      <div class="spacer" style="flex:1;"></div>
      <span class="status-indicator" id="statusInd"></span>
      <div class="zoom-controls">
        <span class="zoom-btn" id="zoomOut">−</span>
        <span class="zoom-btn" id="zoomIn">+</span>
        <span class="zoom-pct" id="zoomPct"></span>
      </div>
      <button class="btn small" id="msgDirBtn" style="position:relative;">☰ 消息目录</button>
      <select id="subgraphSel" class="btn small" style="max-width:220px;"><option value="">主会话</option></select>
    </div>
    <div class="chat-body" id="chatBody">
      <div class="history-pane" id="historyPane"><div class="md-body markdown-body" id="historyMd"><div class="muted">加载历史中…</div></div></div>
      <button id="pinBtn" class="pin-btn" title="跟随最新输出 · 按住可拖动">📌</button>
      <div class="input-pane" id="inputPane">
        <div class="input-toolbar"><span class="muted" id="inputHint">输入消息（Enter 发送，Shift+Enter 换行）</span><div class="spacer" style="flex:1;"></div></div>
        <textarea id="msgInput" placeholder="输入消息…"></textarea>
        <div class="input-actions"><button class="btn" id="stopBtn" style="display:none;">停止</button><button class="btn primary" id="sendBtn">发送</button></div>
      </div>
      <div id="doneBubble" class="done-bubble done-bubble-float" style="display:none;"></div>
    </div>`;

  const chatBody = $("#chatBody");
  const pinBtn = $("#pinBtn");

  const defaultPinPos = () => {
    const inputPane = $("#inputPane");
    if (!chatBody || !pinBtn) return { left: 0, top: 0 };
    const bw = pinBtn.offsetWidth || 34, bh = pinBtn.offsetHeight || 34;
    const inp = inputPane ? inputPane.offsetHeight : 0;
    return { left: chatBody.clientWidth - bw - 28, top: chatBody.clientHeight - inp - bh - 24 };
  };
  const applyPinPos = (pos) => {
    pinBtn.style.left = pos.left + "px";
    pinBtn.style.top = pos.top + "px";
    pinBtn.style.right = "auto";
    pinBtn.style.bottom = "auto";
  };

  let customPos = null;
  try { customPos = JSON.parse(localStorage.getItem("pin-pos") || "null"); } catch (e) {}
  if (customPos && typeof customPos.left === "number") applyPinPos(customPos);
  else applyPinPos(defaultPinPos());

  if (window.Split) {
    Split(["#historyPane", "#inputPane"], {
      direction: "vertical", sizes: [72, 28], minSize: [100, 80], gutterSize: 8, cursor: "row-resize",
      onDragEnd: () => { if (!customPos) applyPinPos(defaultPinPos()); },
    });
  }

  applyZoom();
  getSettings().then(s => {
    if (!s) return;
    const keyLabel = k => ({ enter: "Enter", shift_enter: "Shift+Enter", ctrl_enter: "Ctrl+Enter" })[k] || k;
    const hint = $("#inputHint");
    if (hint) {
      hint.textContent = s.send_key === "mouse_only"
        ? "输入消息（点击发送，Enter 换行）"
        : `输入消息（${keyLabel(s.send_key)} 发送，${keyLabel(s.newline_key)} 换行）`;
    }
  }).catch(() => {});
  const zoomPctEl = $("#zoomPct");
  const updateZoomLabel = () => { if (zoomPctEl) zoomPctEl.textContent = zoomPct + "%"; };
  updateZoomLabel();
  $("#zoomOut").onclick = () => { changeZoom(-10); updateZoomLabel(); };
  $("#zoomIn").onclick = () => { changeZoom(10); updateZoomLabel(); };
  $("#msgDirBtn").onclick = (e) => toggleMsgDrawer(e);

  const updatePinBtn = () => { pinBtn.classList.toggle("active", pinned); };
  updatePinBtn();

  let dragState = null, dragged = false;

  pinBtn.onclick = () => {
    if (dragged) { dragged = false; return; }
    pinned = !pinned;
    localStorage.setItem("pin-follow", pinned ? "1" : "0");
    updatePinBtn();
    if (pinned) { const pane = $("#historyPane"); if (pane) pane.scrollTop = pane.scrollHeight; }
  };

  // 按住拖动移动按钮
  pinBtn.addEventListener("mousedown", (e) => {
    dragState = { x: e.clientX, y: e.clientY, left: pinBtn.offsetLeft, top: pinBtn.offsetTop };
    dragged = false;
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragState) return;
    const dx = e.clientX - dragState.x, dy = e.clientY - dragState.y;
    if (!dragged && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) dragged = true;
    if (!dragged) return;
    const bw = pinBtn.offsetWidth, bh = pinBtn.offsetHeight;
    pinBtn.style.left = Math.min(Math.max(0, dragState.left + dx), chatBody.clientWidth - bw) + "px";
    pinBtn.style.top = Math.min(Math.max(0, dragState.top + dy), chatBody.clientHeight - bh) + "px";
  });
  window.addEventListener("mouseup", () => {
    if (dragState) {
      if (dragged) {
        customPos = { left: pinBtn.offsetLeft, top: pinBtn.offsetTop };
        localStorage.setItem("pin-pos", JSON.stringify(customPos));
      }
      dragState = null;
    }
  });

  const historyEl = $("#historyMd");
  const scrollToLastUserMsg = () => {
    const blocks = historyEl.querySelectorAll(".user-msg-block");
    if (blocks.length) {
      blocks[blocks.length - 1].scrollIntoView({ block: "start" });
      return;
    }
    const pane = $("#historyPane");
    const stats = historyEl.querySelector("#stats-anchor");
    if (!stats || !pane) return;
    const pr = pane.getBoundingClientRect();
    const sr = stats.getBoundingClientRect();
    pane.scrollTop = Math.max(0, pane.scrollTop + (sr.top - pr.top) - pane.clientHeight);
  };
  try {
    const h = await api(`/api/agents/${S.agentId}/threads/${encodeURIComponent(S.threadId)}/history`);
    historyEl.innerHTML = renderMd(h.markdown);
    scrollToLastUserMsg();
  } catch (e) { historyEl.innerHTML = `<div class="muted">（无历史）</div>`; }

  const sel = $("#subgraphSel");
  try {
    const subs = await api(`/api/agents/${S.agentId}/threads/${encodeURIComponent(S.threadId)}/subgraphs`);
    subs.forEach(s => { const o = document.createElement("option"); o.value = s.node_name; o.textContent = s.node_name; sel.appendChild(o); });
  } catch (e) { /* 无子图 */ }

  const input = $("#msgInput"), sendBtn = $("#sendBtn"), stopBtn = $("#stopBtn");

  function updateSendState() {
    sendBtn.disabled = isRunning || !!sel.value;
  }

  sel.onchange = async () => {
    updateSendState();
    if (!sel.value) { renderChatView(); return; }
    try {
      const h = await api(`/api/agents/${S.agentId}/threads/${encodeURIComponent(S.threadId)}/subgraphs/${encodeURIComponent(sel.value)}/history`);
      $("#historyMd").innerHTML = renderMd(h.markdown);
    } catch (e) { toast(e.message, true); }
  };

  function setRunning(r) {
    isRunning = r;
    sel.disabled = r;
    updateSendState();
    if (r) setStatusIndicator("thinking"); else setStatusIndicator("idle");
    if (r) stopBtn.style.display = ""; else stopBtn.style.display = "none";
  }

  function showDoneBubble() {
    const b = $("#doneBubble");
    if (!b) return;
    const pane = $("#inputPane");
    b.textContent = "✅ 回复已完成";
    b.style.bottom = (pane ? pane.offsetHeight + 10 : 160) + "px";
    b.style.display = "inline-flex";
    clearTimeout(b._t);
    b._t = setTimeout(() => { b.style.display = "none"; }, 4000);
    getSettings().then(s => playSound(s.notification_sound)).catch(() => playSound("ber"));
  }

  async function send() {
    const content = input.value.trim();
    if (!content || isRunning) return;
    setRunning(true);
    appendReplyHeader();
    const ok = await openChatWs(content);
    setRunning(false);
    if (ok) {
      if (input.value.trim() === content) input.value = "";
      showDoneBubble();
    } else {
      toast("发送失败，消息已保留在输入框", true);
    }
  }

  sendBtn.onclick = send;
  input.onkeydown = e => {
    if (e.key !== "Enter") return;
    const combo = e.ctrlKey ? "ctrl_enter" : (e.shiftKey ? "shift_enter" : "enter");
    const sendKey = (settingsCache && settingsCache.send_key) || "enter";
    if (sendKey !== "mouse_only" && combo === sendKey) {
      e.preventDefault();
      send();
      return;
    }
    // 非发送键一律换行（手动插入，兼容 Ctrl+Enter）
    e.preventDefault();
    const start = input.selectionStart, end = input.selectionEnd;
    input.value = input.value.slice(0, start) + "\n" + input.value.slice(end);
    input.selectionStart = input.selectionEnd = start + 1;
  };
  stopBtn.onclick = () => { if (ws) ws.send(JSON.stringify({ type: "stop" })); };
  updateSendState();
}

function appendReplyHeader() {
  const historyEl = $("#historyMd");
  if (!currentReplyEl || currentReplyEl.parentElement !== historyEl) {
    currentReplyEl = document.createElement("div");
    currentReplyEl.className = "current-reply md-body markdown-body";
    historyEl.appendChild(currentReplyEl);
  }
}

function openChatWs(content) {
  return new Promise((resolve) => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/agents/${S.agentId}/threads/${encodeURIComponent(S.threadId)}/chat`);

    const buffers = { main: "", main_user: "", sub: {} };
    let rafPending = false;
    let answered = false;
    const flush = () => { rafPending = false; renderStream(); };
    const schedule = () => { if (!rafPending) { rafPending = true; requestAnimationFrame(flush); } };

    function renderStream() {
      if (!currentReplyEl) return;
      const pane = $("#historyPane");
      const atBottom = pane && (pane.scrollHeight - pane.scrollTop - pane.clientHeight < 48);
      let html = "";
      if (buffers.main_user) html += `<div class="user-msg-block"><div class="user-msg-head">🧑 <strong>用户</strong></div><blockquote class="user-msg-quote">${esc(buffers.main_user).replace(/\n/g, "<br>")}</blockquote></div>`;
      if (buffers.main) html += `<div>${renderMd(buffers.main)}</div>`;
      for (const [name, txt] of Object.entries(buffers.sub)) {
        if (txt) html += `<div style="margin-top:12px;"><span class="sub-tag">🧩 子 agent · ${esc(name)}</span><div>${renderMd(txt)}</div></div>`;
      }
      currentReplyEl.innerHTML = html;
      if (pane && (pinned || atBottom)) pane.scrollTop = pane.scrollHeight;
    }

    ws.onopen = () => ws.send(JSON.stringify({ type: "send", content }));

    ws.onmessage = async (ev) => {
      const msg = JSON.parse(ev.data);
      switch (msg.type) {
        case "text":
          if (msg.source === "main" && !answered) { answered = true; setStatusIndicator("answering"); }
          if (msg.source === "main_user") buffers.main_user += msg.text;
          else if (msg.source === "main") buffers.main += msg.text;
          else { const n = msg.source.replace(/^sub:/, ""); buffers.sub[n] = (buffers.sub[n] || "") + msg.text; }
          schedule();
          break;
        case "tool_call":
          buffers.main += `\n\n🔧 **工具调用**: \`${esc(msg.name)}\`\n\n`;
          if (msg.args && Object.keys(msg.args).length) buffers.main += "```json\n" + JSON.stringify(msg.args, null, 2) + "\n```\n\n";
          schedule();
          break;
        case "tool_result":
          buffers.main += `\n✅ **工具结果** (\`${esc(msg.name)}\`):\n\n${esc(msg.content)}\n\n`;
          schedule();
          break;
        case "subgraph_start":
          buffers.sub[msg.name] = buffers.sub[msg.name] || "";
          break;
        case "interrupt": {
          const ans = await askConfirm(msg.prompt || "请确认");
          ws.send(JSON.stringify({ type: "resume", value: ans }));
          break;
        }
        case "done":
          resolve(true);
          break;
        case "error":
          buffers.main += `\n\n> ⚠️ ${esc(msg.message)}\n\n`;
          schedule();
          resolve(false);
          break;
      }
    };
    ws.onerror = () => { toast("WebSocket 连接失败", true); resolve(false); };
    ws.onclose = () => resolve(false);
  });
}

/* ================= 启动 ================= */
render();
