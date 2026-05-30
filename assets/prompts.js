import { deletePrompt, duplicatePrompt, getData, savePrompt, savePromptSettings } from "./store.js";
import { renderNav, showPageError, toast } from "./ui.js";
import { localizeDocumentText, t, translateText } from "./i18n.js";

let editingId = "";
let modalMode = "create";
let currentData = { prompts: [] };
let activePromptCategory = "json_parse";
let promptSaving = false;
let settingsPromptCategory = "json_parse";

const ILLUSTRATION_PROMPT_CATEGORIES = new Set(["illustration_scene", "illustration_shot", "illustration_prompt"]);
const BATCH_CHAR_OPTIONS = [
  { value: "0", label: "不拆分" },
  { value: "3500", label: "3500" },
  { value: "4000", label: "4000" },
  { value: "5000", label: "5000" },
  { value: "6000", label: "6000" },
  { value: "7000", label: "7000" },
  { value: "8000", label: "8000" },
  { value: "9000", label: "9000" },
  { value: "10000", label: "10000" },
];

const providerDefaults = {
  grok: { baseUrl: "https://api.x.ai/v1", model: "grok-2-latest", temperature: 0.3, topP: 0.85, maxTokens: 8192 },
  deepseek: { baseUrl: "https://api.deepseek.com", model: "deepseek-v4-flash", temperature: 0.3, topP: 0.85, maxTokens: 384000 },
  qwen: { baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus", temperature: 0.3, topP: 0.85, maxTokens: 8192 },
  gemini: { baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai", model: "gemini-2.0-flash", temperature: 0.3, topP: 0.85, maxTokens: 8192 },
  openai: { baseUrl: "https://api.openai.com/v1", model: "gpt-4.1-mini", temperature: 0.3, topP: 0.85, maxTokens: 8192 },
  ollama: { baseUrl: "http://127.0.0.1:11434/v1", model: "qwen2.5:7b", temperature: 0.3, topP: 0.85, maxTokens: 8192 },
  local_llama: { baseUrl: "http://192.168.50.1:12080/v1", model: "gemma-31b", temperature: 0.1, topP: 0.85, maxTokens: 84000, numCtx: 84000 },
  custom: { baseUrl: "", model: "", temperature: 0.3, topP: 0.85, maxTokens: 8192 },
};

function promptCategoryLabel(category) {
  const labels = {
    json_parse: "JSON 解析提示词",
    nsfw_review: "NSFW 审查提示词",
    illustration_scene: "插画-scene提示词",
    illustration_shot: "插画-shot提示词",
    illustration_prompt: "插画-prompt提示词",
  };
  return labels[String(category || "json_parse")] || "JSON 解析提示词";
}

function promptCharCount(content) {
  return Array.from(String(content || "")).length;
}

function orderedPrompts() {
  return [...(currentData.prompts || [])]
    .filter((item) => String(item.category || "json_parse") === activePromptCategory)
    .sort((a, b) => {
    const at = a.type === "system" ? 0 : 1;
    const bt = b.type === "system" ? 0 : 1;
    if (at !== bt) return at - bt;
    return Number(b.id) - Number(a.id);
  });
}

function renderTabs() {
  document.querySelectorAll("[data-prompt-tab]").forEach((el) => {
    el.classList.toggle("active", el.dataset.promptTab === activePromptCategory);
  });
}

function render() {
  renderTabs();
  document.getElementById("promptList").innerHTML = orderedPrompts()
    .map(
      (p) => `
      <article class="asset-card">
        <div class="queue-head">
          <h3>${translateText(p.name)}</h3>
          <div class="table-actions-inline">
            <span class="chip ${p.type === "system" ? "pending" : "completed"}">${p.type === "system" ? "系统" : "用户"}</span>
            <span class="chip">${promptCategoryLabel(p.category)}</span>
          </div>
        </div>
        <p class="meta">${translateText(p.description || "-")}</p>
        <p class="meta">${translateText("提示词字数")}: ${promptCharCount(p.content)}</p>
        <div class="card-actions">
          <button class="ghost-btn" data-action="copy" data-id="${p.id}">复制为用户提示词</button>
          <button class="ghost-btn" data-action="${p.type === "system" ? "view" : "edit"}" data-id="${p.id}">${p.type === "system" ? "查看" : "编辑"}</button>
          <button class="ghost-btn" data-action="settings" data-id="${p.id}">设置</button>
          ${p.type === "user" ? `<button class="ghost-btn" data-action="delete" data-id="${p.id}">删除</button>` : ""}
        </div>
      </article>
    `
    )
    .join("");

  document.querySelectorAll("[data-action]").forEach((el) => {
    el.addEventListener("click", () => onAction(el.dataset.action, el.dataset.id));
  });
  localizeDocumentText(document);
}

function defaultPromptLlmSettings() {
  const llm = providerDefaults.local_llama;
  return {
    enabled: false,
    llm: {
      provider: "local_llama",
      baseUrl: llm.baseUrl,
      model: llm.model,
      apiKey: "",
      temperature: llm.temperature,
      topP: llm.topP,
      maxTokens: llm.maxTokens,
      numCtx: llm.numCtx,
      keepAlive: "30m",
      unloadAfterCall: false,
      batchTimeoutMinutes: 15,
      think: true,
      batchMaxChars: 3500,
    },
  };
}

function syncPromptSettingsState() {
  const enabled = document.getElementById("promptLlmEnabled")?.checked;
  const fieldset = document.getElementById("promptLlmFields");
  if (fieldset) fieldset.disabled = !enabled;
}

function syncPromptProviderFields() {
  const provider = String(document.getElementById("promptLlmProvider")?.value || "");
  const isOllama = provider === "ollama";
  const isLocalLlama = provider === "local_llama";
  document.getElementById("promptLlmNumCtxWrap")?.classList.toggle("hidden", !(isOllama || isLocalLlama));
  document.getElementById("promptLlmKeepAliveWrap")?.classList.toggle("hidden", !isOllama);
  document.getElementById("promptLlmUnloadAfterCallWrap")?.classList.toggle("hidden", !(isOllama || isLocalLlama));
  document.getElementById("promptLlmThinkWrap")?.classList.toggle("hidden", !(isOllama || isLocalLlama));
}

function syncBatchCharsOptions(category) {
  const select = document.getElementById("promptLlmBatchChars");
  if (!select) return;
  const options = ILLUSTRATION_PROMPT_CATEGORIES.has(String(category || ""))
    ? BATCH_CHAR_OPTIONS.slice(0, 1)
    : BATCH_CHAR_OPTIONS;
  select.innerHTML = options.map((item) => `<option value="${item.value}">${item.label}</option>`).join("");
}

function openSettingsModal(promptItem) {
  editingId = promptItem?.id || "";
  settingsPromptCategory = promptItem?.category || "json_parse";
  const settings = { ...defaultPromptLlmSettings(), ...(promptItem?.llmSettings || {}) };
  settings.llm = { ...defaultPromptLlmSettings().llm, ...(promptItem?.llmSettings?.llm || {}) };
  syncBatchCharsOptions(settingsPromptCategory);
  document.getElementById("promptSettingsTitle").textContent = `${translateText(promptItem?.name || "提示词")} · 设置`;
  document.getElementById("promptLlmEnabled").checked = Boolean(settings.enabled);
  document.getElementById("promptLlmProvider").value = settings.llm.provider || "local_llama";
  document.getElementById("promptLlmBase").value = settings.llm.baseUrl || "";
  document.getElementById("promptLlmModel").value = settings.llm.model || "";
  document.getElementById("promptLlmKey").value = settings.llm.apiKey || "";
  document.getElementById("promptLlmTemperature").value = settings.llm.temperature ?? 0.3;
  document.getElementById("promptLlmTopP").value = settings.llm.topP ?? 0.85;
  document.getElementById("promptLlmTokens").value = settings.llm.maxTokens ?? 8192;
  document.getElementById("promptLlmNumCtx").value = settings.llm.numCtx ?? 65536;
  document.getElementById("promptLlmKeepAlive").value = settings.llm.keepAlive || "30m";
  document.getElementById("promptLlmUnloadAfterCall").checked = Boolean(settings.llm.unloadAfterCall);
  document.getElementById("promptLlmThink").checked = settings.llm.think !== false;
  document.getElementById("promptLlmBatchTimeout").value = settings.llm.batchTimeoutMinutes ?? 15;
  document.getElementById("promptLlmBatchChars").value = ILLUSTRATION_PROMPT_CATEGORIES.has(String(settingsPromptCategory)) ? "0" : String(settings.llm.batchMaxChars ?? 3500);
  syncPromptSettingsState();
  syncPromptProviderFields();
  document.getElementById("promptSettingsModal").showModal();
}

function collectPromptLlmSettings() {
  const temperature = Number(document.getElementById("promptLlmTemperature").value || 0.3);
  return {
    enabled: document.getElementById("promptLlmEnabled").checked,
    llm: {
      provider: document.getElementById("promptLlmProvider").value,
      baseUrl: document.getElementById("promptLlmBase").value.trim(),
      model: document.getElementById("promptLlmModel").value.trim(),
      apiKey: document.getElementById("promptLlmKey").value.trim(),
      temperature: Math.max(0, Math.min(1, temperature)),
      topP: Number(document.getElementById("promptLlmTopP").value || 0.85),
      maxTokens: Number(document.getElementById("promptLlmTokens").value || 8192),
      numCtx: Number(document.getElementById("promptLlmNumCtx").value || 65536),
      keepAlive: document.getElementById("promptLlmKeepAlive").value || "30m",
      unloadAfterCall: document.getElementById("promptLlmUnloadAfterCall").checked,
      think: document.getElementById("promptLlmThink").checked,
      batchTimeoutMinutes: Number(document.getElementById("promptLlmBatchTimeout").value || 15),
      batchMaxChars: ILLUSTRATION_PROMPT_CATEGORIES.has(String(settingsPromptCategory)) ? 0 : Number(document.getElementById("promptLlmBatchChars").value || 3500),
    },
  };
}

function setPromptLlmTestResult(className, text) {
  const el = document.getElementById("promptLlmTestResult");
  if (!el) return;
  el.className = `caption ${className || ""}`.trim();
  el.textContent = text;
}

async function testPromptLlm() {
  setPromptLlmTestResult("", "测试中...");
  try {
    const res = await fetch("/api/settings/test-llm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ llm: collectPromptLlmSettings().llm, proxyUrl: "" }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    setPromptLlmTestResult("text-success", `可用 · ${data.message || "调用成功"}`);
    toast("LLM测试通过");
  } catch (err) {
    setPromptLlmTestResult("text-danger", `失败 · ${err.message}`);
    toast(t("error.operationFailed", { msg: err.message }));
  }
}

function setFormReadonly(readonly) {
  const form = document.getElementById("promptForm");
  form.name.readOnly = readonly;
  form.category.disabled = readonly;
  form.description.readOnly = readonly;
  form.content.readOnly = readonly;
  const saveBtn = document.getElementById("promptSaveBtn");
  saveBtn.hidden = readonly;
  document.getElementById("promptCancelBtn").textContent = readonly ? "关闭" : "取消";
}

function openModal(promptItem, mode = "create") {
  modalMode = mode;
  editingId = promptItem?.id || "";
  document.getElementById("promptModalTitle").textContent =
    mode === "view" ? "查看系统提示词" : editingId ? "编辑提示词" : "新建提示词";
  const form = document.getElementById("promptForm");
  form.name.value = mode === "view" ? translateText(promptItem?.name || "") : promptItem?.name || "";
  form.category.value = promptItem?.category || activePromptCategory || "json_parse";
  form.description.value = mode === "view" ? translateText(promptItem?.description || "") : promptItem?.description || "";
  form.content.value = promptItem?.content || "";
  document.getElementById("promptCharCount").textContent = `${translateText("提示词字数")}: ${promptCharCount(promptItem?.content || "")}`;
  setFormReadonly(mode === "view");
  localizeDocumentText(document);
  document.getElementById("promptModal").showModal();
}

function onAction(action, id) {
  const item = currentData.prompts.find((p) => String(p.id) === String(id));
  if (!item) return;
  if (action === "copy") {
    duplicatePrompt(id)
      .then(async () => {
        toast(t("toast.copied"));
        currentData = await getData();
        render();
      })
      .catch((err) => toast(t("error.copyFailed", { msg: err.message })));
  }
  if (action === "edit") {
    openModal(item, "edit");
  }
  if (action === "view") {
    openModal(item, "view");
  }
  if (action === "settings") {
    openSettingsModal(item);
  }
  if (action === "delete") {
    if (!window.confirm(t("confirm.deletePrompt", { name: item.name }))) return;
    deletePrompt(id)
      .then(async () => {
        toast(t("toast.deleted"));
        currentData = await getData();
        render();
      })
      .catch((err) => toast(t("error.deleteFailed", { msg: err.message })));
  }
}

function bindEvents() {
  document.getElementById("createPromptBtn").addEventListener("click", () => openModal(null, "create"));
  document.querySelectorAll("[data-prompt-tab]").forEach((el) => {
    el.addEventListener("click", () => {
      activePromptCategory = String(el.dataset.promptTab || "json_parse");
      render();
    });
  });
  document.getElementById("promptCancelBtn").addEventListener("click", () => {
    document.getElementById("promptModal").close();
  });
  document.getElementById("promptForm").content.addEventListener("input", (event) => {
    document.getElementById("promptCharCount").textContent = `${translateText("提示词字数")}: ${promptCharCount(event.target.value)}`;
  });
  document.getElementById("promptForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (promptSaving) return;
    if (modalMode === "view") {
      document.getElementById("promptModal").close();
      return;
    }
    const form = event.currentTarget;
    const submitBtn = form.querySelector("button[type='submit']");
    promptSaving = true;
    if (submitBtn) submitBtn.disabled = true;
    try {
      await savePrompt(
        {
          name: form.name.value.trim(),
          category: form.category.value,
          description: form.description.value.trim(),
          content: form.content.value.trim(),
        },
        editingId
      );
      document.getElementById("promptModal").close();
      toast(editingId ? t("toast.updated") : t("toast.created"));
      currentData = await getData({ include: ["prompts"] });
      render();
    } finally {
      promptSaving = false;
      if (submitBtn) submitBtn.disabled = false;
    }
  });
  document.getElementById("promptSettingsCancelBtn").addEventListener("click", () => {
    document.getElementById("promptSettingsModal").close();
  });
  document.getElementById("promptLlmEnabled").addEventListener("change", syncPromptSettingsState);
  document.getElementById("promptLlmProvider").addEventListener("change", (event) => {
    const next = providerDefaults[event.target.value] || providerDefaults.custom;
    document.getElementById("promptLlmBase").value = next.baseUrl || "";
    document.getElementById("promptLlmModel").value = next.model || "";
    document.getElementById("promptLlmTemperature").value = next.temperature ?? 0.3;
    document.getElementById("promptLlmTopP").value = next.topP ?? 0.85;
    document.getElementById("promptLlmTokens").value = next.maxTokens ?? 8192;
    document.getElementById("promptLlmNumCtx").value = next.numCtx ?? 65536;
    syncPromptProviderFields();
  });
  document.getElementById("promptLlmTestBtn").addEventListener("click", testPromptLlm);
  document.getElementById("promptSettingsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!editingId) return;
    await savePromptSettings(editingId, collectPromptLlmSettings());
    document.getElementById("promptSettingsModal").close();
    toast(t("toast.updated"));
    currentData = await getData({ include: ["prompts"] });
    render();
  });
}

async function init() {
  renderNav();
  bindEvents();
  currentData = await getData({ include: ["prompts"] });
  render();
  localizeDocumentText(document);
}

init().catch((err) => {
  renderNav();
  showPageError(err, t("error.pageLoad"));
});
