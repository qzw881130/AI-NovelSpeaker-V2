import { deletePrompt, duplicatePrompt, getData, savePrompt } from "./store.js";
import { renderNav, showPageError, toast } from "./ui.js";
import { localizeDocumentText, t, translateText } from "./i18n.js";

let editingId = "";
let modalMode = "create";
let currentData = { prompts: [] };
let activePromptCategory = "json_parse";
let promptSaving = false;

function promptCategoryLabel(category) {
  return String(category || "json_parse") === "nsfw_review" ? "NSFW 审查提示词" : "JSON 解析提示词";
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
