const $ = (sel) => document.querySelector(sel);

const logEl = $("#log");
const runBtn = $("#run-btn");
const resultPanel = $("#result-panel");
const reportFrame = $("#report-frame");
const openReport = $("#open-report");

const qaLog = $("#qa-log");
const qaForm = $("#qa-form");
const qaInput = $("#qa-input");
const qaSubmit = $("#qa-submit");
const qaMode = $("#qa-mode");

let stream = null;
let currentRunId = null;

// ---------- 鉴权 ----------
const TOKEN_KEY = "agentpulse_token";
const USER_KEY = "agentpulse_user";
function getToken() { return localStorage.getItem(TOKEN_KEY) || ""; }
function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
function clearAuth() { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); }
function getCurrentUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || "null"); } catch { return null; }
}
function setCurrentUser(u) { localStorage.setItem(USER_KEY, JSON.stringify(u)); }
function isAdmin() { return getCurrentUser()?.role === "admin"; }

function apiHeaders(extra = {}) {
  const h = { ...extra };
  const t = getToken();
  if (t) h["Authorization"] = `Bearer ${t}`;
  return h;
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: apiHeaders(options.headers || {}),
  });
  if (res.status === 401) {
    clearAuth();
    showLogin();
    throw new Error("登录已过期，请重新登录");
  }
  return res;
}

function showLogin() {
  $("#app").classList.add("hidden");
  $("#login-overlay").classList.remove("hidden");
}
function hideLogin() {
  $("#login-overlay").classList.add("hidden");
  $("#app").classList.remove("hidden");
}

async function doLogin(username, password) {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  setToken(data.token);
  setCurrentUser(data.user);
  return data.user;
}

function doLogout() {
  clearAuth();
  showLogin();
}

async function bootstrapAuth() {
  const token = getToken();
  if (!token) { showLogin(); return; }
  try {
    const res = await apiFetch("/api/auth/me");
    if (!res.ok) throw new Error("invalid");
    const user = await res.json();
    setCurrentUser(user);
    renderUserBar();
    hideLogin();
  } catch {
    clearAuth();
    showLogin();
  }
}

function renderUserBar() {
  const u = getCurrentUser();
  const bar = $("#user-bar");
  if (!u || !bar) return;
  bar.innerHTML = `<span class="user-name">${u.username}</span><span class="user-role ${u.role}">${u.role === "admin" ? "管理员" : "用户"}</span><button id="logout-btn" class="link-btn">退出</button>`;
  $("#logout-btn").addEventListener("click", doLogout);
  const adminOnly = u.role === "admin";
  $("#user-mgmt-btn")?.classList.toggle("hidden", !adminOnly);
  $("#settings-btn")?.classList.toggle("hidden", !adminOnly);
  $("#cleanup-btn")?.classList.toggle("hidden", !adminOnly);
}

function now() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function appendLog(text, kind = "") {
  const line = document.createElement("span");
  line.className = kind;
  line.textContent = `[${now()}] ${text}\n`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function resetAgents() {
  document.querySelectorAll(".agent").forEach((card) => {
    card.removeAttribute("data-status");
    card.querySelector("em").textContent = "待命";
  });
}

function setAgent(agent, status, message) {
  const card = document.querySelector(`.agent[data-agent="${agent}"]`);
  if (!card) return;
  card.dataset.status = status;
  if (message) card.querySelector("em").textContent = message;
}

async function checkHealth() {
  const el = $("#health");
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    el.textContent = data.llm ? "服务正常 · 已接入模型" : "服务正常 · 规则模式";
    el.className = "health ok";
  } catch {
    el.textContent = "服务不可用";
    el.className = "health bad";
  }
}

function topicInputs() {
  return [1, 2, 3, 4, 5].map((index) => $(`#topic${index}`)).filter(Boolean);
}

function parseTopics() {
  return [...new Set(
    topicInputs().map((input) => input.value.trim()).filter(Boolean)
  )];
}

function markActiveChip() {
  const current = $("#topic1").value.trim();
  document.querySelectorAll("#field-topics button").forEach((btn) => {
    btn.classList.toggle("active", btn.textContent === current);
  });
}

async function loadFieldTopics(autoSwitch) {
  const box = $("#field-topics");
  const field = $("#field").value;
  const subfield = $("#subfield").value;
  const subfield3 = $("#subfield3").value;
  box.innerHTML = "";
  const scope = [field || "全部门类", subfield, subfield3].filter(Boolean).join(" · ");
  $("#topic-scope").textContent = scope;
  try {
    const params = new URLSearchParams({ field });
    if (subfield) params.set("subfield", subfield);
    if (subfield3) params.set("subfield3", subfield3);
    const resp = await apiFetch(`/api/topics?${params.toString()}`);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    const topics = data.topics || [];
    topics.forEach((topic) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = topic;
      btn.addEventListener("click", () => {
        $("#topic1").value = topic;
        markActiveChip();
      });
      box.appendChild(btn);
    });
    if (autoSwitch && topics.length) {
      $("#topic1").value = topics[0];
      appendLog(`范围切换为「${scope}」，主题自动切换为「${topics[0]}」`);
    }
    markActiveChip();
  } catch (err) {
    appendLog(`学科主题加载失败：${err.message}`, "warn");
  }
}

async function loadSources(field) {
  if (!field) return;
  try {
    const resp = await apiFetch(`/api/sources?field=${encodeURIComponent(field)}`);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    const recommended = data.sources || [];
    document.querySelectorAll(".checks input").forEach((box) => {
      box.checked = recommended.includes(box.value);
    });
    appendLog(`已按「${field}」切换推荐数据源：${recommended.join("、")}`);
  } catch (err) {
    appendLog(`数据源推荐加载失败：${err.message}`, "warn");
  }
}

async function loadSubfields(autoLoadTopics) {
  const field = $("#field").value;
  const sel = $("#subfield");
  const sel3 = $("#subfield3");
  sel.innerHTML = '<option value="">全部二级学科</option>';
  sel3.innerHTML = '<option value="">全部三级学科</option>';
  sel3.disabled = true;
  sel.disabled = !field;
  if (!field) {
    await loadFieldTopics(autoLoadTopics);
    return;
  }
  await loadSources(field);
  try {
    const resp = await apiFetch(`/api/subfields?field=${encodeURIComponent(field)}`);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    (data.subfields || []).forEach((node) => {
      const opt = document.createElement("option");
      opt.value = node.name;
      opt.textContent = node.name;
      sel.appendChild(opt);
    });
  } catch (err) {
    appendLog(`二级学科加载失败：${err.message}`, "warn");
  }
  await loadFieldTopics(autoLoadTopics);
}

async function loadChildren(autoLoadTopics) {
  const field = $("#field").value;
  const parent = $("#subfield").value;
  const sel3 = $("#subfield3");
  sel3.innerHTML = '<option value="">全部三级学科</option>';
  sel3.disabled = !parent;
  if (field && parent) {
    try {
      const params = new URLSearchParams({ field, parent });
      const resp = await apiFetch(`/api/subfields?${params.toString()}`);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      (data.subfields || []).forEach((node) => {
        const opt = document.createElement("option");
        opt.value = node.name;
        opt.textContent = node.name;
        sel3.appendChild(opt);
      });
    } catch (err) {
      appendLog(`三级学科加载失败：${err.message}`, "warn");
    }
  }
  await loadFieldTopics(autoLoadTopics);
}

$("#field").addEventListener("change", () => loadSubfields(true));
$("#subfield").addEventListener("change", () => loadChildren(true));
$("#subfield3").addEventListener("change", () => loadFieldTopics(true));
$("#topic1").addEventListener("input", markActiveChip);

$("#clear-log").addEventListener("click", () => {
  logEl.textContent = "";
});

$("#close-report").addEventListener("click", () => {
  resultPanel.classList.add("hidden");
});

function finishRun(runId) {
  apiFetch(`/api/runs/${runId}`)
    .then((res) => res.json())
    .then((data) => {
      if (data.status !== "done" || !data.report_url) return;
      reportFrame.src = data.report_url;
      openReport.href = data.report_url;
      resultPanel.classList.remove("hidden");
      appendLog("报告已生成，可在下方查看。", "ok");
    })
    .catch((err) => appendLog(`读取结果失败：${err}`, "er"));
}

async function connectEvents(runId) {
  if (stream) stream.abort();
  currentRunId = runId;
  let ended = false;
  stream = new AbortController();
  try {
    const res = await apiFetch(`/api/runs/${runId}/events`, { signal: stream.signal });
    if (!res.ok || !res.body) throw new Error("HTTP " + res.status);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        let data;
        try { data = JSON.parse(line.slice(5).trim()); } catch { continue; }
        const name = data.event || "";
        if (name === "stage") {
          const st = data.status === "running" ? "running" : data.status === "done" ? "done" : "failed";
          setAgent(data.agent, st, data.message || "");
          appendLog(`${data.agent} · ${data.message || data.status}`);
        } else if (name === "run.start") {
          appendLog(`任务开始：${data.topic}（窗口 ${data.days} 天）`);
        } else if (name === "run.done") {
          appendLog(`流水线完成：精选 ${data.kept} 篇，模型调用 ${data.llm_calls} 次`, "ok");
        } else if (name === "run.error" || name === "run.failed") {
          appendLog(`运行失败：${data.error}`, "er");
          document.querySelectorAll('.agent[data-status="running"]').forEach((card) => { card.dataset.status = "failed"; });
        } else if (name === "run.complete") {
          ended = true;
          finishRun(runId);
          runBtn.disabled = false;
        } else if (name === "stream.end") {
          ended = true;
          runBtn.disabled = false;
        }
      }
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    if (!ended) appendLog(`事件流中断：${err.message}`, "er");
  }
}

async function startRun(event) {
  event.preventDefault();
  const topics = parseTopics();
  if (topics.length === 0) {
    appendLog("请至少填写第 1 个研究主题。", "er");
    return;
  }
  const topic = topics.join(" / ");
  const sources = [...document.querySelectorAll(".checks input:checked")].map((i) => i.value);
  if (sources.length === 0) {
    appendLog("至少选择一个数据源。", "er");
    return;
  }

  runBtn.disabled = true;
  resetAgents();
  resultPanel.classList.add("hidden");
  reportFrame.src = "about:blank";
  qaLog.textContent = "";
  qaMode.textContent = "基于本次检索的论文、精读结论与主题归纳作答";
  logEl.textContent = "";
  appendLog(`提交任务：${topic}`);

  try {
    const res = await apiFetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        topics,
        days: Number($("#days").value),
        max_papers: Number($("#max-papers").value),
        sources,
        field: $("#field").value || null,
        subfield: $("#subfield").value || null,
        subfield3: $("#subfield3").value || null,
        venue: $("#venue").value.trim() || null,
        ieee_only: $("#ieee").checked,
        no_llm: $("#no-llm").checked,
      }),
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(`HTTP ${res.status} ${detail}`);
    }
    const data = await res.json();
    appendLog(`任务已创建：${data.id}`);
    connectEvents(data.id);
  } catch (err) {
    appendLog(`提交失败：${err.message}`, "er");
    runBtn.disabled = false;
  }
}

$("#run-btn").addEventListener("click", startRun);
topicInputs().forEach((input) => {
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") startRun(e);
  });
});

function qaMessage(kind, text) {
  const item = document.createElement("div");
  item.className = `qa-item ${kind}`;
  item.textContent = text;
  qaLog.appendChild(item);
  qaLog.scrollTop = qaLog.scrollHeight;
  return item;
}

function qaCitations(items) {
  const box = document.createElement("div");
  box.className = "qa-cites";
  items.forEach((paper, index) => {
    const link = document.createElement("a");
    link.href = paper.url || "#";
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = `[${index + 1}] ${paper.title}${paper.venue ? ` · ${paper.venue}` : ""}`;
    box.appendChild(link);
  });
  return box;
}

async function askQuestion(event) {
  event.preventDefault();
  const question = qaInput.value.trim();
  if (!question) return;
  if (!currentRunId) {
    appendLog("请先完成一次检索，再就结果提问。", "warn");
    return;
  }

  qaInput.value = "";
  qaMessage("me", question);
  const pending = qaMessage("bot pending", "检索相关片段并组织回答…");
  qaSubmit.disabled = true;

  try {
    const res = await apiFetch(`/api/runs/${currentRunId}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    pending.remove();
    const node = qaMessage("bot", data.answer || "（空回答）");
    if (data.citations && data.citations.length) node.appendChild(qaCitations(data.citations));
    qaMode.textContent = data.mode === "llm" ? "模型作答 · 引用可点击" : "规则模式 · 摘录原文";
  } catch (err) {
    pending.remove();
    qaMessage("bot", `提问失败：${err.message}`);
  } finally {
    qaSubmit.disabled = false;
    qaInput.focus();
  }
}

qaForm.addEventListener("submit", askQuestion);

const cleanupBtn = $("#cleanup-btn");
cleanupBtn.addEventListener("click", async () => {
  if (!confirm("清理本地查询记录？\n将保留最近 20 轮报告，删除更早的历史报告。")) return;
  const label = cleanupBtn.textContent;
  cleanupBtn.disabled = true;
  cleanupBtn.textContent = "清理中…";
  try {
    const resp = await apiFetch("/api/cleanup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keep: 20 }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    if (!data.removed) {
      appendLog(`本地记录已是最近 ${data.kept} 轮，无需清理`, "ok");
    } else {
      appendLog(
        `已清理 ${data.removed} 个历史报告，释放 ${data.freed_mb} MB，保留最近 ${data.kept} 轮`,
        "ok"
      );
    }
  } catch (err) {
    appendLog(`清理失败：${err.message}`, "er");
  } finally {
    cleanupBtn.disabled = false;
    cleanupBtn.textContent = label;
  }
});

// ---------- 用户管理 ----------
const umDrawer = $("#usermgmt-drawer");
const umMask = $("#usermgmt-mask");
function umToast(text, kind) {
  const t = $("#um-toast");
  t.textContent = text || "";
  t.className = "hint" + (kind ? " " + kind : "");
}
async function loadUsers() {
  try {
    const res = await apiFetch("/api/users");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    const list = $("#um-list");
    const me = getCurrentUser();
    list.innerHTML = "";
    (data.items || []).forEach((u) => {
      const row = document.createElement("div");
      row.className = "user-row";
      const isSelf = u.id === me.id;
      row.innerHTML = `
        <div class="user-info"><b>${u.username}</b><span class="user-role ${u.role}">${u.role === "admin" ? "管理员" : "用户"}</span>${isSelf ? "<span class='tag'>当前</span>" : ""}</div>
        <div class="user-actions">
          ${!isSelf ? `<select class="um-role-sel" data-id="${u.id}"><option value="user"${u.role==="user"?" selected":""}>用户</option><option value="admin"${u.role==="admin"?" selected":""}>管理员</option></select>
          <button class="ghost um-reset" data-id="${u.id}">改密</button>
          <button class="ghost um-del" data-id="${u.id}">删除</button>` : ""}
        </div>`;
      list.appendChild(row);
    });
    list.querySelectorAll(".um-role-sel").forEach((sel) => {
      sel.addEventListener("change", async () => {
        const id = sel.dataset.id;
        try { await apiFetch(`/api/users/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role: sel.value }) }); umToast("角色已更新", "ok"); loadUsers(); }
        catch (e) { umToast(e.message, "er"); }
      });
    });
    list.querySelectorAll(".um-reset").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.id;
        const np = prompt("输入新密码（至少 6 位）：");
        if (!np) return;
        try { await apiFetch(`/api/users/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: np }) }); umToast("密码已重置", "ok"); }
        catch (e) { umToast(e.message, "er"); }
      });
    });
    list.querySelectorAll(".um-del").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("确定删除该用户？其历史任务仍保留。")) return;
        const id = btn.dataset.id;
        try { await apiFetch(`/api/users/${id}`, { method: "DELETE" }); umToast("已删除", "ok"); loadUsers(); }
        catch (e) { umToast(e.message, "er"); }
      });
    });
  } catch (e) { umToast(e.message, "er"); }
}
function openUserMgmt() { umDrawer.classList.remove("hidden"); umMask.classList.remove("hidden"); loadUsers(); }
function closeUserMgmt() { umDrawer.classList.add("hidden"); umMask.classList.add("hidden"); }
$("#user-mgmt-btn").addEventListener("click", openUserMgmt);
$("#usermgmt-close").addEventListener("click", closeUserMgmt);
umMask.addEventListener("click", closeUserMgmt);
$("#um-create").addEventListener("click", async () => {
  const username = $("#um-username").value.trim();
  const password = $("#um-password").value;
  const role = $("#um-role").value;
  if (!username || !password) { umToast("请填写用户名和密码", "er"); return; }
  try {
    const res = await apiFetch("/api/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password, role }) });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || "HTTP " + res.status); }
    umToast(`已创建用户 ${username}`, "ok");
    $("#um-username").value = ""; $("#um-password").value = "";
    loadUsers();
  } catch (e) { umToast(e.message, "er"); }
});

// ---------- 登录表单 ----------
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#login-submit");
  const msg = $("#login-msg");
  btn.disabled = true;
  msg.textContent = "登录中…";
  msg.className = "";
  try {
    const user = await doLogin($("#login-username").value, $("#login-password").value);
    renderUserBar();
    hideLogin();
    checkHealth();
    loadSubfields(false);
  } catch (err) {
    msg.textContent = err.message;
    msg.className = "err";
  } finally {
    btn.disabled = false;
  }
});

// ---------- 启动 ----------
bootstrapAuth();
