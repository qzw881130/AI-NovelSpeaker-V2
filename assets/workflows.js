import { deleteWorkflow, duplicateWorkflow, getData, saveWorkflow } from "./store.js";
import { renderNav, showPageError, toast } from "./ui.js";
import { localizeDocumentText, t, translateText } from "./i18n.js";

let editingId = "";
let modalMode = "create";
let currentData = { workflows: [] };
let activeWorkflowTab = "voice_sample";

const WORKFLOW_IO_FIELDS = {
  voice_sample: {
    inputs: [
      { key: "voiceDescription", label: "音色描述" },
      { key: "lineText", label: "台词" },
    ],
    outputs: [{ key: "audioFile", label: "生成的声音文件" }],
  },
  line_audio: {
    inputs: [
      { key: "referenceAudio", label: "参考音频文件" },
      { key: "lineText", label: "台词" },
      { key: "referenceText", label: "参考音频的文本" },
    ],
    outputs: [{ key: "audioFile", label: "生成的声音文件" }],
  },
  voice_transcribe: {
    inputs: [{ key: "audioFile", label: "音频文件" }],
    outputs: [{ key: "textOutput", label: "提取的文本" }],
  },
  audio_asr: {
    inputs: [{ key: "audioFile", label: "音频文件" }],
    outputs: [
      { key: "textOutput", label: "提取的文本" },
      { key: "languageOutput", label: "识别语言" },
      { key: "timestampsOutput", label: "时间轴文本" },
      { key: "textListOutput", label: "分句文本" },
      { key: "startTimesOutput", label: "开始时间" },
      { key: "endTimesOutput", label: "结束时间" },
    ],
  },
};

function orderedWorkflows() {
  return [...(currentData.workflows || [])]
    .filter((item) => String(item.workflowType || "") === activeWorkflowTab)
    .sort((a, b) => {
    const at = a.type === "system" ? 0 : 1;
    const bt = b.type === "system" ? 0 : 1;
    if (at !== bt) return at - bt;
    return Number(b.id) - Number(a.id);
    });
}

const WORKFLOW_TYPE_LABELS = {
  voice_transcribe: "提取声音文本",
  audio_asr: "提取音频ASR",
  line_audio: "生成台词音频",
  voice_sample: "生成示例音频",
};

function getWorkflowTypeLabel(type) {
  return translateText(WORKFLOW_TYPE_LABELS[type] || type || "-");
}

function parseWorkflowJsonNodes(jsonText) {
  try {
    const parsed = JSON.parse(String(jsonText || "{}").trim() || "{}");
    if (!parsed || typeof parsed !== "object") return [];
    const toOption = (nodeId, node) => {
      const title = String(node?._meta?.title || node?.title || "").trim();
      const classType = String(node?.class_type || node?.type || "").trim();
      const label = title
        ? (title.startsWith(`#${nodeId}`) ? title : `#${nodeId} ${title}`)
        : `#${nodeId} ${classType || "节点"}`;
      return { nodeId: String(nodeId), label };
    };

    if (Array.isArray(parsed.nodes)) {
      return parsed.nodes
        .filter((node) => node && typeof node === "object" && node.id != null)
        .map((node) => toOption(node.id, node))
        .sort((a, b) => Number(a.nodeId) - Number(b.nodeId));
    }

    return Object.entries(parsed)
      .filter(([key, node]) => /^\d+$/.test(String(key)) && node && typeof node === "object")
      .map(([nodeId, node]) => toOption(nodeId, node))
      .sort((a, b) => Number(a.nodeId) - Number(b.nodeId));
  } catch {
    return [];
  }
}

function normalizeWorkflowIoConfig(config) {
  const safe = config && typeof config === "object" ? config : {};
  return {
    inputs: safe.inputs && typeof safe.inputs === "object" ? safe.inputs : {},
    outputs: safe.outputs && typeof safe.outputs === "object" ? safe.outputs : {},
  };
}

function collectWorkflowIoConfig(form) {
  const type = String(form.workflowType.value || "").trim();
  const defs = WORKFLOW_IO_FIELDS[type] || { inputs: [], outputs: [] };
  const config = { inputs: {}, outputs: {} };
  defs.inputs.forEach((field) => {
    const select = form.querySelector(`[data-io-kind="input"][data-io-key="${field.key}"]`);
    config.inputs[field.key] = { nodeId: String(select?.value || "").trim() };
  });
  defs.outputs.forEach((field) => {
    const select = form.querySelector(`[data-io-kind="output"][data-io-key="${field.key}"]`);
    config.outputs[field.key] = { nodeId: String(select?.value || "").trim() };
  });
  return config;
}

function renderWorkflowIoConfig(form, workflowType, ioConfig, readonly) {
  const root = document.getElementById("workflowIoConfig");
  if (!root) return;
  const defs = WORKFLOW_IO_FIELDS[workflowType];
  if (!defs) {
    root.innerHTML = '<p class="meta">请先选择工作流类型</p>';
    return;
  }
  const normalized = normalizeWorkflowIoConfig(ioConfig);
  const nodeOptions = parseWorkflowJsonNodes(form.jsonText.value);
  const optionHtml = ['<option value="">请选择结点</option>']
    .concat(nodeOptions.map((node) => `<option value="${node.nodeId}">${node.label}</option>`))
    .join("");
  const renderField = (kind, field) => {
    const selected = String(normalized[`${kind}s`]?.[field.key]?.nodeId || "");
    return `
      <label>
        <span>${field.label}</span>
        <select data-io-kind="${kind}" data-io-key="${field.key}" ${readonly ? "disabled" : ""}>
          ${optionHtml}
        </select>
      </label>
    `;
  };
  root.innerHTML = `
    <div class="workflow-io-section">
      <h5>输入</h5>
      <div class="workflow-io-fields">
        ${defs.inputs.map((field) => renderField("input", field)).join("")}
      </div>
    </div>
    <div class="workflow-io-section">
      <h5>输出</h5>
      <div class="workflow-io-fields">
        ${defs.outputs.map((field) => renderField("output", field)).join("")}
      </div>
    </div>
  `;
  defs.inputs.forEach((field) => {
    const select = root.querySelector(`[data-io-kind="input"][data-io-key="${field.key}"]`);
    if (select) select.value = String(normalized.inputs?.[field.key]?.nodeId || "");
  });
  defs.outputs.forEach((field) => {
    const select = root.querySelector(`[data-io-kind="output"][data-io-key="${field.key}"]`);
    if (select) select.value = String(normalized.outputs?.[field.key]?.nodeId || "");
  });
}

function render() {
  document.querySelectorAll("[data-workflow-tab]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.workflowTab === activeWorkflowTab);
  });
  document.getElementById("workflowList").innerHTML = orderedWorkflows()
    .map(
      (w) => `
      <article class="asset-card">
        <div class="queue-head">
          <h3>${translateText(w.name)}</h3>
          <span class="chip ${w.type === "system" ? "pending" : "completed"}">${w.type === "system" ? "系统" : "用户"}</span>
        </div>
        <p class="meta"><strong>类型:</strong> ${getWorkflowTypeLabel(w.workflowType)} | ${translateText(w.description || "-")}</p>
        <p class="meta">输入输出配置：${w.workflowType ? "已配置" : "-"}</p>
        <div class="workflow-log-toggle-row">
          <span class="meta">日志状态</span>
          <button class="workflow-log-toggle ${w.workflowLogEnabled !== false ? "is-on" : "is-off"}" data-action="toggle-log" data-id="${w.id}" type="button" aria-label="切换日志状态">
            <span class="workflow-log-toggle-thumb"></span>
          </button>
          <span class="meta ${w.workflowLogEnabled !== false ? "text-success" : "text-muted"}">${w.workflowLogEnabled !== false ? "已开启" : "已关闭"}</span>
        </div>
        <div class="card-actions">
          <button class="ghost-btn" data-action="copy" data-id="${w.id}">复制为用户工作流</button>
          <button class="ghost-btn" data-action="${w.type === "system" ? "view" : "edit"}" data-id="${w.id}">${w.type === "system" ? "查看" : "编辑"}</button>
          ${w.type === "user" ? `<button class="ghost-btn" data-action="delete" data-id="${w.id}">删除</button>` : ""}
        </div>
      </article>
    `
    )
    .join("");

  if (!orderedWorkflows().length) {
    document.getElementById("workflowList").innerHTML = '<p class="empty-text">当前类型下暂无工作流。</p>';
  }

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
  form.querySelectorAll("[data-io-kind]").forEach((el) => {
    el.disabled = readonly;
  });
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
  renderWorkflowIoConfig(form, form.workflowType.value, item?.workflowIoConfig || {}, mode === "view");
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
  if (action === "toggle-log") {
    saveWorkflow(
      {
        name: item.name,
        workflowType: item.workflowType,
        description: item.description,
        jsonText: item.jsonText,
        workflowIoConfig: item.workflowIoConfig || {},
        workflowLogEnabled: item.workflowLogEnabled === false,
      },
      id
    )
      .then(async () => {
        currentData = await getData();
        render();
        toast("工作流日志状态已更新");
      })
      .catch((err) => toast(t("error.operationFailed", { msg: err.message })));
  }
}

function bindEvents() {
  document.getElementById("createWorkflowBtn").addEventListener("click", () => openModal(null, "create"));
  document.querySelectorAll("[data-workflow-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeWorkflowTab = String(btn.dataset.workflowTab || "voice_sample");
      render();
    });
  });
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
        workflowIoConfig: collectWorkflowIoConfig(form),
      },
      editingId
    );
    document.getElementById("workflowModal").close();
    toast(editingId ? t("toast.updated") : t("toast.created"));
    currentData = await getData();
    render();
  });
  document.getElementById("workflowForm").workflowType.addEventListener("change", (event) => {
    const form = document.getElementById("workflowForm");
    renderWorkflowIoConfig(form, String(event.target.value || ""), {}, modalMode === "view");
  });
  document.getElementById("workflowForm").jsonText.addEventListener("input", () => {
    const form = document.getElementById("workflowForm");
    renderWorkflowIoConfig(
      form,
      String(form.workflowType.value || ""),
      collectWorkflowIoConfig(form),
      modalMode === "view"
    );
  });
}

async function init() {
  renderNav();
  bindEvents();
  currentData = await getData({ include: ["workflows"] });
  render();
  localizeDocumentText(document);
}

init().catch((err) => {
  renderNav();
  showPageError(err, t("error.pageLoad"));
});
