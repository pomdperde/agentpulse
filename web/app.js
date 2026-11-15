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
    const resp = await fetch(`/api/topics?${params.toString()}`);
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
    const resp = await fetch(`/api/sources?field=${encodeURIComponent(field)}`);
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
    const resp = await fetch(`/api/subfields?field=${encodeURIComponent(field)}`);
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
      const resp = await fetch(`/api/subfields?${params.toString()}`);
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
loadSubfields(false);

$("#clear-log").addEventListener("click", () => {
  logEl.textContent = "";
});

$("#close-report").addEventListener("click", () => {
  resultPanel.classList.add("hidden");
});

function finishRun(runId) {
  fetch(`/api/runs/${runId}`)
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

function connectEvents(runId) {
  if (stream) stream.close();
  currentRunId = runId;
  let ended = false;
  stream = new EventSource(`/api/runs/${runId}/events`);

  stream.onmessage = (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }
    const name = data.event || "";

    if (name === "stage") {
      const status = data.status === "running" ? "running" : data.status === "done" ? "done" : "failed";
      setAgent(data.agent, status, data.message || "");
      appendLog(`${data.agent} · ${data.message || data.status}`);
      return;
    }
    if (name === "run.start") {
      appendLog(`任务开始：${data.topic}（窗口 ${data.days} 天）`);
      return;
    }
    if (name === "run.done") {
      appendLog(`流水线完成：精选 ${data.kept} 篇，模型调用 ${data.llm_calls} 次`, "ok");
      return;
    }
    if (name === "run.error" || name === "run.failed") {
      appendLog(`运行失败：${data.error}`, "er");
      document.querySelectorAll('.agent[data-status="running"]').forEach((card) => {
        card.dataset.status = "failed";
      });
      return;
    }
    if (name === "run.complete") {
      ended = true;
      finishRun(runId);
      runBtn.disabled = false;
      return;
    }
    if (name === "stream.end") {
      ended = true;
      stream.close();
      runBtn.disabled = false;
    }
  };

  stream.onerror = () => {
    if (ended) return;
    appendLog("事件流中断，尝试重连…", "er");
  };
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
    const res = await fetch("/api/runs", {
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
    const res = await fetch(`/api/runs/${currentRunId}/ask`, {
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

checkHealth();
