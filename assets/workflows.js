import { deleteWorkflow, duplicateWorkflow, getData, saveWorkflow } from "./store.js";
import { renderNav, showPageError, toast } from "./ui.js";
import { localizeDocumentText, t, translateText } from "./i18n.js";

let editingId = "";
let modalMode = "create";
let currentData = { workflows: [] };

function orderedWorkflows() {
  return [...(currentData.workflows || [])].sort((a, b) => {
    const at = a.type === "system" ? 0 : 1;
    const bt = b.type === "system" ? 0 : 1;
    if (at !== bt) return at - bt;
    return Number(b.id) - Number(a.id);
  });
}

const WORKFLOW_TYPE_LABELS = {
  voice_transcribe: "提取声音文本",
  line_audio: "生成台词音频",
  voice_sample: "生成示例音频",
};

function getWorkflowTypeLabel(type) {
  return WORKFLOW_TYPE_LABELS[type] || type || "-";
}

function render() {
  document.getElementById("workflowList").innerHTML = orderedWorkflows()
    .map(
      (w) => `
      <article class="asset-card">
        <div class="queue-head">
          <h3>${translateText(w.name)}</h3>
          <span class="chip ${w.type === "system" ? "pending" : "completed"}">${w.type === "system" ? "系统" : "用户"}</span>
        </div>
        <p class="meta"><strong>类型:</strong> ${getWorkflowTypeLabel(w.workflowType)} | ${translateText(w.description || "-")}</p>
        <div class="card-actions">
          <button class="ghost-btn" data-action="copy" data-id="${w.id}">复制为用户工作流</button>
          <button class="ghost-btn" data-action="${w.type === "system" ? "view" : "edit"}" data-id="${w.id}">${w.type === "system" ? "查看" : "编辑"}</button>
          ${w.type === "user" ? `<button class="ghost-btn" data-action="delete" data-id="${w.id}">删除</button>` : ""}
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
  const form = document.getElementById("workflowForm");
  form.name.readOnly = readonly;
  form.workflowType.disabled = readonly;
  form.description.readOnly = readonly;
  form.jsonText.readOnly = readonly;
  const saveBtn = document.getElementById("workflowSaveBtn");
  saveBtn.hidden = readonly;
  document.getElementById("workflowCancelBtn").textContent = readonly ? "关闭" : "取消";
}

function formatWorkflowJsonText(jsonText) {
  const text = String(jsonText || "").trim();
  if (!text) return '{"workflow":""}';
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

function openModal(item, mode = "create") {
  modalMode = mode;
  editingId = item?.id || "";
  document.getElementById("workflowModalTitle").textContent =
    mode === "view" ? "查看系统工作流" : editingId ? "编辑工作流" : "创建工作流";
  const form = document.getElementById("workflowForm");
  form.name.value = mode === "view" ? translateText(item?.name || "") : item?.name || "";
  form.workflowType.value = item?.workflowType || "";
  form.description.value = mode === "view" ? translateText(item?.description || "") : item?.description || "";
  form.jsonText.value = formatWorkflowJsonText(item?.jsonText);
  setFormReadonly(mode === "view");
  localizeDocumentText(document);
  document.getElementById("workflowModal").showModal();
}

function onAction(action, id) {
  const item = currentData.workflows.find((w) => String(w.id) === String(id));
  if (!item) return;
  if (action === "copy") {
    duplicateWorkflow(id)
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
    if (!window.confirm(t("confirm.deleteWorkflow", { name: item.name }))) return;
    deleteWorkflow(id)
      .then(async () => {
        toast(t("toast.deleted"));
        currentData = await getData();
        render();
      })
      .catch((err) => toast(t("error.deleteFailed", { msg: err.message })));
  }
}

function bindEvents() {
  document.getElementById("createWorkflowBtn").addEventListener("click", () => openModal(null, "create"));
  document.getElementById("workflowCancelBtn").addEventListener("click", () => {
    document.getElementById("workflowModal").close();
  });
  document.getElementById("workflowForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (modalMode === "view") {
      document.getElementById("workflowModal").close();
      return;
    }
    const form = event.currentTarget;
    await saveWorkflow(
      {
        name: form.name.value.trim(),
        workflowType: form.workflowType.value,
        description: form.description.value.trim(),
        jsonText: form.jsonText.value.trim(),
      },
      editingId
    );
    document.getElementById("workflowModal").close();
    toast(editingId ? t("toast.updated") : t("toast.created"));
    currentData = await getData();
    render();
  });
}

async function init() {
  renderNav();
  bindEvents();
  currentData = await getData();
  render();
  localizeDocumentText(document);
}

init().catch((err) => {
  renderNav();
  showPageError(err, t("error.pageLoad"));
});
