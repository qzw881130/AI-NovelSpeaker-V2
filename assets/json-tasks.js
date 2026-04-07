import { deleteJsonTask, fetchJsonTaskDetail, getData, retryJsonTask, retryJsonTaskBatch } from "./store.js";
import { clearNavBadge, fmtDateTime, fmtNumber, renderNav, showPageError, toast } from "./ui.js";
import { localizeDocumentText, t } from "./i18n.js";

function statusLabel(status) {
  return t(`common.status.${status}`) || status;
}

let currentData = { novels: [], prompts: [], jsonTasks: [] };
let refreshTimer = null;
let clockTimer = null;
const taskDetails = new Map();
const loadingDetails = new Set();
const batchOpenStates = new Map();
const REFRESH_INTERVAL_KEY = "ai_novel_json_tasks_refresh_interval";
const REFRESH_VALUES = ["0", "5", "20", "60"];

function renderNovelSelector() {
  const select = document.getElementById("taskNovelSelect");
  const current = String(select.value || "");
  select.innerHTML = `<option value="">全部小说</option>${currentData.novels
    .map((n) => `<option value="${n.id}">${n.name}</option>`)
    .join("")}`;
  if (current && select.querySelector(`option[value="${current}"]`)) {
    select.value = current;
  }
}

function promptName(map, id) {
  return map.get(String(id)) || "-";
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatJsonPretty(text) {
  const raw = String(text || "").trim();
  if (!raw) return "";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function parseServerTime(value) {
  const text = String(value || "").trim();
  if (!text) return Date.now();
  const hasZone = /[zZ]|[+-]\d\d:\d\d$/.test(text);
  const normalized = text.includes("T") ? text : text.replace(" ", "T");
  const ts = Date.parse(hasZone ? normalized : `${normalized}Z`);
  return Number.isFinite(ts) ? ts : Date.now();
}

function formatServerTime(value) {
  return fmtDateTime(new Date(parseServerTime(value)));
}

function formatElapsedFrom(value) {
  const ms = Math.max(0, Date.now() - parseServerTime(value));
  return formatElapsedMs(ms);
}

function formatElapsedBetween(startValue, endValue) {
  const ms = Math.max(0, parseServerTime(endValue) - parseServerTime(startValue));
  return formatElapsedMs(ms);
}

function formatElapsedMs(ms) {
  const total = Math.floor(ms / 1000);
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `{${mm}:${ss}}`;
}

function updateElapsedLabels() {
  document.querySelectorAll("[data-elapsed-from]").forEach((el) => {
    const from = el.getAttribute("data-elapsed-from") || "";
    el.textContent = formatElapsedFrom(from);
  });
}

function progressWidth(task) {
  const base = Number(task.progress || 0);
  if (task.status === "running") return Math.max(base, 14);
  return Math.max(0, Math.min(100, base));
}

function displayBatchProgress(task) {
  const total = Math.max(0, Number(task.batchTotal || 0));
  if (total <= 0) return "0/0";
  const done = Math.max(0, Number(task.batchDone || 0));
  const failed = Math.max(0, Number(task.batchFailed || 0));
  let current = done;
  if (task.status === "running") {
    current = Math.min(total, done + 1);
  } else if (task.status === "failed") {
    current = Math.min(total, done + failed);
  } else if (task.status === "completed") {
    current = total;
  }
  return `${fmtNumber(current)}/${fmtNumber(total)}`;
}

function batchKey(batch) {
  const primary = batch.id ?? batch.batchId ?? batch.batchIndex;
  if (primary != null && primary !== "") return String(primary);
  return `${batch.updatedAt || ""}:${batch.inputWordCount || 0}`;
}

function renderBatchDetails(taskId) {
  const data = taskDetails.get(String(taskId));
  if (!data) return "";
  const batches = Array.isArray(data.batches) ? data.batches : [];
  const openSet = batchOpenStates.get(String(taskId));
  const updatedLabel = t("common.updatedAt");
  const wordsLabel = t("字数");
  const inputLabel = t("输入文本");
  const llmLabel = t("LLM返回");
  const parsedJsonLabel = t("解析JSON");
  if (!batches.length) {
    return `<div class="batch-panel"><p class="meta">当前任务没有分批记录。</p></div>`;
  }
  return `<div class="batch-panel">${batches
    .map((b) => {
      const key = batchKey(b);
      const shouldOpen = openSet ? openSet.has(key) : b.status === "failed";
      const err = b.errorMessage ? ` · 失败: ${escapeHtml(b.errorMessage)}` : "";
      const canRetry = ["completed", "failed"].includes(String(data.status || ""));
      return `<details data-batch-detail="1" data-task-id="${taskId}" data-batch-key="${escapeHtml(key)}" ${shouldOpen ? "open" : ""}><summary>批次 ${b.batchIndex} · ${b.status} · ${wordsLabel} ${fmtNumber(b.inputWordCount || 0)}${err}</summary><p class="meta">${updatedLabel} ${formatServerTime(
        b.updatedAt
      )} · 重试 ${fmtNumber(b.retryCount || 0)}/3</p>${canRetry ? `<div class="card-actions"><button class="ghost-btn" data-batch-action="retry" data-task-id="${taskId}" data-batch-index="${b.batchIndex}">${t("common.retry")}</button></div>` : ""}<div class="batch-block"><strong>${inputLabel}</strong><pre>${escapeHtml(b.inputText || "")}</pre></div><div class="batch-block"><strong>${llmLabel}</strong><pre>${escapeHtml(
        b.llmResponseText || ""
      )}</pre></div><div class="batch-block"><strong>${parsedJsonLabel}</strong><pre>${escapeHtml(formatJsonPretty(b.parsedJsonText || ""))}</pre></div></details>`;
    })
    .join("")}</div>`;
}

function render() {
  const promptMap = new Map(currentData.prompts.map((p) => [String(p.id), p.name]));
  const activeNovel = document.getElementById("taskNovelSelect").value;
  const status = document.getElementById("taskStatusSelect").value;
  const createdAtLabel = t("common.createdAt");
  const updatedAtLabel = t("common.updatedAt");
  const elapsedLabel = t("common.elapsed");
  const chapterLabel = t("章节");
  const wordsLabel = t("字数");
  const promptLabel = t("提示词");
  const batchesLabel = t("分批");

  const list = currentData.jsonTasks.filter((t) => {
    const hitNovel = activeNovel ? String(t.novelId) === String(activeNovel) : true;
    const hitStatus = status === "all" ? true : t.status === status;
    return hitNovel && hitStatus;
  });

  if (!list.length) {
    document.getElementById("jsonTaskList").innerHTML =
      '<article class="queue-card"><p class="meta">当前筛选条件下暂无任务，试试切换到“全部小说/全部状态”。</p></article>';
    localizeDocumentText(document);
    return;
  }

  document.getElementById("jsonTaskList").innerHTML = list
    .map(
      (task) => `
      <article class="queue-card">
        <div class="queue-head">
          <h3><span class="audio-task-id">#${fmtNumber(task.id)}</span> ${escapeHtml(task.title || "-")}</h3>
          <strong class="status ${task.status}">${statusLabel(task.status)}</strong>
        </div>
        <div class="meta json-meta-row">
          <span class="json-meta-pill"><i class="json-meta-dot"></i>${escapeHtml(task.novelName || "-")}</span>
          <span class="json-meta-pill"><i class="json-meta-dot"></i>${chapterLabel} ${fmtNumber(task.chapter || 0)}</span>
          <span class="json-meta-pill"><i class="json-meta-dot"></i>${wordsLabel} ${fmtNumber(task.wordCount || 0)}</span>
          <span class="json-meta-pill"><i class="json-meta-dot"></i>${promptLabel} ${escapeHtml(promptName(promptMap, task.promptId))}</span>
          <span class="json-meta-pill"><i class="json-meta-dot"></i>${batchesLabel} ${displayBatchProgress(task)}</span>
        </div>
        <p class="meta json-meta-time">${createdAtLabel} ${formatServerTime(task.createdAt || task.updatedAt)} · ${updatedAtLabel} ${formatServerTime(task.updatedAt)}${
          task.status === "running"
            ? ` · ${elapsedLabel} <span data-elapsed-from="${task.startedAt || task.createdAt || task.updatedAt}">${formatElapsedFrom(task.startedAt || task.createdAt || task.updatedAt)}</span>`
            : ` · ${elapsedLabel} ${formatElapsedBetween(task.startedAt || task.createdAt || task.updatedAt, task.updatedAt)}`
        }</p>
        ${task.status === "failed" && task.errorMessage ? `<p class="task-error">${escapeHtml(task.errorMessage)}</p>` : ""}
        ${
          task.status === "failed"
            ? `<div class="card-actions"><button class="ghost-btn" data-task-action="retry" data-task-id="${task.id}">${t("common.retry")}</button><button class="ghost-btn" data-task-action="delete" data-task-id="${task.id}">${t("common.delete")}</button><button class="ghost-btn" data-task-action="batches" data-task-id="${task.id}">${taskDetails.has(
                String(task.id)
              ) ? "收起批次" : "批次详情"}</button></div>`
            : task.status !== "running"
              ? `<div class="card-actions"><button class="ghost-btn" data-task-action="delete" data-task-id="${task.id}">${t("common.delete")}</button><button class="ghost-btn" data-task-action="batches" data-task-id="${task.id}">${taskDetails.has(
                  String(task.id)
                ) ? "收起批次" : "批次详情"}</button></div>`
              : `<div class="card-actions"><button class="ghost-btn" data-task-action="batches" data-task-id="${task.id}">${taskDetails.has(
                  String(task.id)
                ) ? "收起批次" : "批次详情"}</button></div>`
        }
        ${loadingDetails.has(String(task.id)) ? `<p class="meta">正在加载批次详情...</p>` : ""}
        ${taskDetails.has(String(task.id)) ? renderBatchDetails(task.id) : ""}
        <div class="progress ${task.status === "running" ? "is-running" : ""} ${task.status === "failed" ? "is-failed" : ""}"><i style="width:${progressWidth(task)}%"></i></div>
      </article>
    `
    )
    .join("");

  document.querySelectorAll("[data-task-action]").forEach((el) => {
    el.addEventListener("click", () => onTaskAction(el.dataset.taskAction, el.dataset.taskId));
  });
  document.querySelectorAll("details[data-batch-detail]").forEach((el) => {
    el.addEventListener("toggle", () => {
      const taskId = String(el.getAttribute("data-task-id") || "");
      const key = String(el.getAttribute("data-batch-key") || "");
      if (!taskId || !key) return;
      let openSet = batchOpenStates.get(taskId);
      if (!openSet) {
        openSet = new Set();
        batchOpenStates.set(taskId, openSet);
      }
      if (el.open) openSet.add(key);
      else openSet.delete(key);
      if (!openSet.size) batchOpenStates.delete(taskId);
    });
  });
  document.querySelectorAll("[data-batch-action]").forEach((el) => {
    el.addEventListener("click", () => onBatchAction(el.dataset.batchAction, el.dataset.taskId, el.dataset.batchIndex));
  });
  updateElapsedLabels();
  localizeDocumentText(document);
}

async function onBatchAction(action, taskId, batchIndex) {
  try {
    if (action !== "retry") return;
    await retryJsonTaskBatch(taskId, batchIndex);
    const detail = await fetchJsonTaskDetail(taskId);
    taskDetails.set(String(taskId), detail);
    currentData = await getData();
    render();
    toast("批次已重试");
  } catch (err) {
    toast(t("error.operationFailed", { msg: err.message }));
  }
}

async function onTaskAction(action, id) {
  const task = currentData.jsonTasks.find((x) => String(x.id) === String(id));
  if (!task) return;
  try {
    if (action === "retry") {
      await retryJsonTask(task.id);
      toast(t("common.retry"));
      await reload();
      return;
    }
    if (action === "delete") {
      if (task.status === "running") {
        toast(t("error.runningTaskNotDeletable"));
        return;
      }
      if (!window.confirm(t("confirm.deleteTask", { title: task.title }))) return;
      await deleteJsonTask(task.id);
      taskDetails.delete(String(task.id));
      batchOpenStates.delete(String(task.id));
      toast(t("toast.deleted"));
      await reload();
      return;
    }
    if (action === "batches") {
      const key = String(task.id);
      if (taskDetails.has(key)) {
        taskDetails.delete(key);
        render();
        return;
      }
      if (loadingDetails.has(key)) return;
      loadingDetails.add(key);
      render();
      try {
        const detail = await fetchJsonTaskDetail(task.id);
        taskDetails.set(key, detail);
      } finally {
        loadingDetails.delete(key);
      }
      render();
    }
  } catch (err) {
    toast(t("error.operationFailed", { msg: err.message }));
  }
}

async function reload() {
  const novelValue = document.getElementById("taskNovelSelect")?.value || "";
  const statusValue = document.getElementById("taskStatusSelect")?.value || "all";
  currentData = await getData();
  const alive = new Set((currentData.jsonTasks || []).map((x) => String(x.id)));
  for (const key of Array.from(taskDetails.keys())) {
    if (!alive.has(key)) taskDetails.delete(key);
  }
  for (const key of Array.from(batchOpenStates.keys())) {
    if (!alive.has(key)) batchOpenStates.delete(key);
  }
  const expandedTaskIds = Array.from(taskDetails.keys()).filter((key) => alive.has(key));
  if (expandedTaskIds.length) {
    await Promise.all(
      expandedTaskIds.map(async (key) => {
        try {
          const detail = await fetchJsonTaskDetail(Number(key));
          taskDetails.set(key, detail);
        } catch {
          // keep existing cached details when refresh fails
        }
      })
    );
  }
  renderNovelSelector();
  document.getElementById("taskNovelSelect").value = novelValue;
  document.getElementById("taskStatusSelect").value = statusValue;
  render();
}

function bindEvents() {
  document.getElementById("taskNovelSelect").addEventListener("change", render);
  document.getElementById("taskStatusSelect").addEventListener("change", render);
  document.getElementById("refreshJsonTasksBtn").addEventListener("click", async () => {
    await reload();
    toast(t("common.refresh"));
  });

  document.getElementById("jsonAutoRefreshSelect").addEventListener("change", applyAutoRefresh);
}

function applyAutoRefresh() {
  const select = document.getElementById("jsonAutoRefreshSelect");
  if (!select) return;
  if (refreshTimer) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
  const seconds = Number(select.value || 0);
  localStorage.setItem(REFRESH_INTERVAL_KEY, String(seconds));
  if (!Number.isFinite(seconds) || seconds <= 0) return;
  refreshTimer = window.setInterval(() => {
    reload().catch(() => {
      // ignore
    });
  }, seconds * 1000);
}

function initAutoRefresh() {
  const select = document.getElementById("jsonAutoRefreshSelect");
  if (!select) return;
  const saved = localStorage.getItem(REFRESH_INTERVAL_KEY);
  if (saved != null && REFRESH_VALUES.includes(saved)) {
    select.value = saved;
  }
  applyAutoRefresh();
}

function initClockTicker() {
  if (clockTimer) {
    window.clearInterval(clockTimer);
    clockTimer = null;
  }
  clockTimer = window.setInterval(() => {
    if (!currentData.jsonTasks.some((x) => x.status === "running")) return;
    updateElapsedLabels();
  }, 1000);
}

async function init() {
  clearNavBadge("json");
  renderNav();
  bindEvents();
  initAutoRefresh();
  initClockTicker();
  await reload();
  localizeDocumentText(document);
}

init().catch((err) => {
  renderNav();
  showPageError(err, t("error.pageLoad"));
});
