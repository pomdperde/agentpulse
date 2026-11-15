(function () {
  "use strict";

  var drawer = document.getElementById("settings-drawer");
  var mask = document.getElementById("settings-mask");
  var toast = document.getElementById("settings-toast");
  var saveBtn = document.getElementById("settings-save");
  var testBtn = document.getElementById("settings-test");

  var ncbiKeyInput = document.getElementById("settings-key");
  var ncbiEye = document.getElementById("settings-eye");
  var ncbiSource = document.getElementById("settings-key-source");
  var ncbiHint = document.getElementById("settings-key-hint");
  var lockBanner = document.getElementById("settings-lock-banner");

  var chatKeyInput = document.getElementById("settings-chat-key");
  var chatEye = document.getElementById("settings-chat-eye");
  var chatSource = document.getElementById("settings-chat-source");
  var chatHint = document.getElementById("settings-chat-hint");
  var chatBaseInput = document.getElementById("settings-chat-base");
  var chatModelInput = document.getElementById("settings-chat-model");
  var chatTestBtn = document.getElementById("settings-chat-test");

  var emailInput = document.getElementById("settings-email");

  var state = null;
  var dirty = {
    ncbi_api_key: false,
    chat_api_key: false,
    chat_base_url: false,
    chat_model: false,
    contact_email: false,
  };

  function showToast(text, kind) {
    toast.textContent = text || "";
    toast.className = "hint" + (kind ? " " + kind : "");
  }

  async function api(method, url, body) {
    var options = { method: method };
    if (body) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    var resp = await fetch(url, options);
    var data = null;
    try {
      data = await resp.json();
    } catch (err) {
      data = null;
    }
    if (!resp.ok) {
      throw new Error((data && data.detail) || "HTTP " + resp.status);
    }
    return data;
  }

  function field(name) {
    return (state && state.fields && state.fields[name]) || {};
  }

  function updateSave() {
    var editable = Object.keys(dirty).some(function (name) {
      return dirty[name] && !field(name).locked;
    });
    saveBtn.disabled = !editable;
  }

  function renderKey(name, input, eye, sourceTag, hintEl, emptyHint) {
    var item = field(name);
    input.disabled = !!item.locked;
    eye.disabled = !!item.locked;
    input.value = "";
    input.type = "password";
    eye.textContent = "显示";
    input.placeholder = item.locked
      ? "已由环境变量提供（不可见）"
      : item.configured
        ? "已配置 " + item.hint + "，留空表示不修改"
        : "粘贴 " + emptyHint;
    sourceTag.textContent = item.locked
      ? "环境变量"
      : item.configured
        ? "已配置 " + item.hint
        : "未配置";
  }

  function render() {
    var ncbi = field("ncbi_api_key");
    var chat = field("chat_api_key");

    renderKey("ncbi_api_key", ncbiKeyInput, ncbiEye, ncbiSource, ncbiHint, "NCBI API Key");
    ncbiHint.textContent = ncbi.locked
      ? "来源：环境变量 · 本页只读，修改需重启服务"
      : "未配置 3 请求/秒 · 配置后 10 请求/秒";
    lockBanner.classList.toggle("hidden", !ncbi.locked);

    renderKey("chat_api_key", chatKeyInput, chatEye, chatSource, chatHint, "模型 API Key");
    chatHint.textContent = chat.locked
      ? "来源：环境变量 · 本页只读，修改需重启服务"
      : state.llm_ready
        ? "模型已就绪 · 当前 " + (field("chat_model").value || "")
        : "未配置时自动走规则模式";

    var base = field("chat_base_url");
    chatBaseInput.value = base.value || "";
    chatBaseInput.disabled = !!base.locked;
    chatBaseInput.placeholder = base.locked ? "已由环境变量提供" : "https://api.deepseek.com/v1";

    var model = field("chat_model");
    chatModelInput.value = model.value || "";
    chatModelInput.disabled = !!model.locked;
    chatModelInput.placeholder = model.locked ? "已由环境变量提供" : "deepseek-v4-flash";

    emailInput.value = field("contact_email").value || "";
    emailInput.disabled = !!field("contact_email").locked;

    Object.keys(dirty).forEach(function (name) {
      dirty[name] = false;
    });
    updateSave();
  }

  async function open() {
    drawer.classList.remove("hidden");
    mask.classList.remove("hidden");
    drawer.setAttribute("aria-hidden", "false");
    showToast("加载配置…");
    try {
      state = await api("GET", "/api/settings");
      render();
      showToast("");
    } catch (err) {
      showToast("读取配置失败：" + err.message, "bad");
    }
  }

  function close() {
    drawer.classList.add("hidden");
    mask.classList.add("hidden");
    drawer.setAttribute("aria-hidden", "true");
  }

  document.getElementById("settings-btn").addEventListener("click", open);
  document.getElementById("settings-close").addEventListener("click", close);
  mask.addEventListener("click", close);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !drawer.classList.contains("hidden")) {
      close();
    }
  });

  function bindEye(input, eye, locked) {
    eye.addEventListener("click", function () {
      if (locked()) return;
      var hidden = input.type === "password";
      input.type = hidden ? "text" : "password";
      eye.textContent = hidden ? "隐藏" : "显示";
      eye.setAttribute("aria-pressed", hidden ? "true" : "false");
    });
  }

  bindEye(ncbiKeyInput, ncbiEye, function () {
    return !!field("ncbi_api_key").locked;
  });
  bindEye(chatKeyInput, chatEye, function () {
    return !!field("chat_api_key").locked;
  });

  [
    [ncbiKeyInput, "ncbi_api_key"],
    [chatKeyInput, "chat_api_key"],
    [chatBaseInput, "chat_base_url"],
    [chatModelInput, "chat_model"],
    [emailInput, "contact_email"],
  ].forEach(function (pair) {
    pair[0].addEventListener("input", function () {
      dirty[pair[1]] = true;
      updateSave();
    });
  });

  chatTestBtn.addEventListener("click", async function () {
    chatTestBtn.disabled = true;
    showToast("正在验证模型连接…");
    try {
      var res = await api("POST", "/api/settings/test-model");
      if (res.ok) {
        showToast("模型可用 · " + (res.model || ""), "ok");
      } else {
        showToast(res.message || "模型不可用", "bad");
      }
    } catch (err) {
      showToast("测试失败：" + err.message, "bad");
    } finally {
      chatTestBtn.disabled = false;
    }
  });

  testBtn.addEventListener("click", async function () {
    testBtn.disabled = true;
    showToast("正在验证密钥…");
    try {
      var res = await api("POST", "/api/settings/test");
      if (res.ok) {
        showToast("连接正常 · " + res.total + " 条命中 · " + res.rate_limit, "ok");
      } else {
        showToast(res.message || "测试失败", "bad");
      }
    } catch (err) {
      showToast("测试失败：" + err.message, "bad");
    } finally {
      testBtn.disabled = false;
    }
  });

  saveBtn.addEventListener("click", async function () {
    var payload = {};
    var labels = [];
    var mapping = [
      ["chat_api_key", chatKeyInput, "模型密钥"],
      ["chat_base_url", chatBaseInput, "接口地址"],
      ["chat_model", chatModelInput, "模型名"],
      ["ncbi_api_key", ncbiKeyInput, "NCBI 密钥"],
      ["contact_email", emailInput, "联系邮箱"],
    ];
    mapping.forEach(function (item) {
      var name = item[0];
      if (!dirty[name] || field(name).locked) return;
      payload[name] = item[1].value.trim();
      labels.push(item[2]);
    });
    if (!labels.length) return;

    saveBtn.disabled = true;
    try {
      state = await api("PUT", "/api/settings", payload);
      render();
      showToast(labels.join("、") + " 已保存，下一轮任务生效", "ok");
    } catch (err) {
      showToast("保存失败：" + err.message, "bad");
      updateSave();
    }
  });
})();
