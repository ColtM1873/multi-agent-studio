"use strict";

(function () {
  const isEnglish = () => {
    try {
      return lang === "en";
    } catch (_e) {
      return localStorage.getItem("lang") === "en";
    }
  };

  const originalT = window.t;
  if (typeof originalT === "function") {
    window.t = function (text) {
      if (text === "加载Embedding模型中") {
        return isEnglish() ? "Preparing session…" : "正在准备会话…";
      }
      return originalT(text);
    };
  }

  const hideField = (id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const field = el.closest(".field");
    if (field) field.style.display = "none";
    el.style.display = "none";
  };

  const originalBuildForm = window.buildForm;
  if (typeof originalBuildForm === "function") {
    window.buildForm = function (cfg, canEditSubs) {
      const result = originalBuildForm.apply(this, arguments);
      hideField("f_sdb");
      hideField("f_ns");
      hideField("f_memory_tools");
      hideField("f_emb_use_global");
      hideField("f_store_db_hint");
      ["embManualBlock", "embGlobalBlock"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.style.display = "none";
      });
      document.querySelectorAll("#editorForm details.db-help li").forEach((li) => {
        if (li.textContent.includes("pgvector")) li.remove();
      });
      return result;
    };
  }

  const originalBuildPayload = window.buildPayload;
  if (typeof originalBuildPayload === "function") {
    window.buildPayload = function (cfg) {
      const payload = originalBuildPayload.apply(this, arguments);
      if (payload && payload.main_agent) {
        payload.main_agent.memory_tools_enabled = false;
      }
      return payload;
    };
  }

  const originalOpenSettings = window.openSettings;
  if (typeof originalOpenSettings === "function") {
    window.openSettings = async function () {
      const result = await originalOpenSettings.apply(this, arguments);
      const memoryToggle = document.getElementById("set_mem");
      if (memoryToggle) {
        const group = memoryToggle.closest(".settings-group");
        if (group) group.style.display = "none";
      }
      return result;
    };
  }

  const originalOpenAdvancedSettings = window.openAdvancedSettings;
  if (typeof originalOpenAdvancedSettings === "function") {
    window.openAdvancedSettings = async function () {
      const result = await originalOpenAdvancedSettings.apply(this, arguments);
      const search = document.getElementById("adv_search_threshold");
      const attach = document.getElementById("adv_attach_threshold");
      [search, attach].forEach((el) => {
        if (!el) return;
        const row = el.closest(".switch-row");
        if (row) row.style.display = "none";
      });
      const first = search || attach;
      if (first) {
        const row = first.closest(".switch-row");
        const header = row && row.previousElementSibling;
        if (header && header.classList.contains("muted")) {
          header.style.display = "none";
        }
      }
      return result;
    };
  }

  const originalOpenGlobalConfig = window.openGlobalConfigSettings;
  if (typeof originalOpenGlobalConfig === "function") {
    window.openGlobalConfigSettings = async function () {
      const result = await originalOpenGlobalConfig.apply(this, arguments);
      const embeddingBtn = document.getElementById("gcEmb");
      if (embeddingBtn) embeddingBtn.style.display = "none";
      return result;
    };
  }
})();
