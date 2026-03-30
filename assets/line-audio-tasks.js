import {
  getData,
  getActiveNovelId,
  setActiveNovelId,
  fetchLineAudioTasks,
  fetchLineAudioTaskDetail,
  deleteLineAudioTask,
  retryLineAudioTask,
} from "./store.js";
import { clearNavBadge, renderNav, toast, fmtDateTime } from "./ui.js";
import { localizeDocumentText, t, translateText } from "./i18n.js";

let allNovels = [];
let activeNovel = null;
let lineAudioTasks = [];
let activeLineAudioTaskId = null;
let lineAudioRefreshTimerId = null;
let activeLineAudioTaskSignature = "";
const LINE_AUDIO_REFRESH_INTERVAL_KEY = "ai_novel_line_audio_refresh_interval";
const LINE_AUDIO_TASK_PAGE_SIZE = 100;
let lineAudioNextOffset = 0;
let lineAudioHasMore = false;
let lineAudioTotalCount = 0;
let lineAudioPendingCount = 0;
let lineAudioLoadingMore = false;

function updateLineAudioDocumentTitle() {
  const baseTitle = activeNovel
    ? `${activeNovel.name} - ${translateText("台词音频任务队列")}`
    : translateText("台词音频任务队列");
  document.title = `【${translateText("待执行")}：${lineAudioPendingCount}】${baseTitle}`;
}

function isTaskDetailAudioPlaying() {
  const player = document.getElementById("lineAudioTaskPlayer");
  return Boolean(player && !player.paused && !player.ended);
}

function getNovelByQueryOrActive() {
  const url = new URL(window.location.href);
  const queryId = String(url.searchParams.get("novelId") || "");
  if (queryId) {
    return allNovels.find((n) => String(n.id) === queryId) || null;
  }
  const activeId = getActiveNovelId();
  if (activeId) {
    return allNovels.find((n) => String(n.id) === activeId) || null;
  }
  return allNovels[0] || null;
}

function setHeader(novel) {
  document.getElementById("lineAudioPageTitle").textContent = `${novel.name} - ${translateText("台词音频任务队列")}`;
  updateLineAudioDocumentTitle();
}

function statusText(status) {
  const normalized = String(status || "pending").toLowerCase();
  if (normalized === "running" || normalized === "processing") return t("common.status.running");
  if (normalized === "completed") return t("common.status.completed");
  if (normalized === "failed") return t("common.status.failed");
  if (normalized === "cancelled") return t("common.status.cancelled");
  return t("common.status.pending");
}

function renderNovelSelect() {
  const select = document.getElementById("lineAudioNovelSelect");
  select.innerHTML = allNovels.map((n) => `<option value="${n.id}">${n.name}</option>`).join("");
  if (activeNovel) select.value = String(activeNovel.id);
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function parseDbTime(text) {
  const raw = String(text || "").trim();
  if (!raw) return null;
  const hasZone = /[zZ]|[+-]\d\d:\d\d$/.test(raw);
  const iso = raw.includes("T") ? raw : raw.replace(" ", "T");
  const dt = new Date(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(dt.getTime()) ? null : dt;
}

function formatDbDateTime(text) {
  const dt = parseDbTime(text);
  return dt ? fmtDateTime(dt) : "-";
}

function formatHms(totalSeconds) {
  const sec = Math.max(0, Math.floor(totalSeconds || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function calcTaskRuntime(task) {
  const start = parseDbTime(task.comfyStartedAt);
  if (!start) return "-";
  const finished = parseDbTime(task.comfyFinishedAt);
  const end = finished || (task.comfyStatus === "running" ? new Date() : null);
  if (!end) return "-";
  return formatHms((end.getTime() - start.getTime()) / 1000);
}

function getScheduleLabel(task) {
  const scheduledAt = String(task.scheduledAt || "").trim();
  return scheduledAt
    ? `${translateText("指定时间执行")} ${formatDbDateTime(scheduledAt) || scheduledAt}`
    : translateText("立即执行");
}

function statusClass(status) {
  if (status === "processing" || status === "running") return "status-running";
  if (status === "completed") return "status-completed";
  if (status === "failed") return "status-failed";
  return "status-pending";
}

function renderLineAudioTaskList() {
  const listEl = document.getElementById("lineAudioTaskList");
  const pendingCountEl = document.getElementById("lineAudioPendingCount");
  const listMetaEl = document.getElementById("lineAudioListMeta");
  const loadMoreBtn = document.getElementById("loadMoreLineAudioTasksBtn");
  if (pendingCountEl) {
    pendingCountEl.textContent = `${t("common.status.pending")} ${lineAudioPendingCount}`;
  }
  updateLineAudioDocumentTitle();
  if (listMetaEl) {
    listMetaEl.textContent = `${lineAudioTasks.length} / ${lineAudioTotalCount}`;
  }
  if (loadMoreBtn) {
    loadMoreBtn.classList.toggle("hidden", !lineAudioHasMore);
    loadMoreBtn.disabled = lineAudioLoadingMore;
  }
  listEl.innerHTML = "";

  if (!lineAudioTasks || lineAudioTasks.length === 0) {
    listEl.innerHTML = `<li class="task-list-empty">${translateText("暂无台词音频任务")}</li>`;
    return;
  }

  for (const task of lineAudioTasks) {
    const li = document.createElement("li");
    li.className = "task-list-item" + (task.id === activeLineAudioTaskId ? " active" : "");
    li.dataset.taskId = task.id;

    const status = task.status || "pending";
    const runtime = calcTaskRuntime(task);

    li.innerHTML = `
      <div class="task-list-title">#${task.id} | ${task.chapterTitle || `#${task.chapterNum}`}</div>
      <div class="task-list-subtitle">${escapeHtml(task.roleName || "-")}：${escapeHtml(task.lineText?.substring(0, 20) || "")}...</div>
      <div class="task-list-meta">
        <span class="status-badge ${statusClass(status)}">${statusText(status)}</span>
        <span>${translateText("行号")} : ${task.lineIndex + 1}</span>
        <span>${translateText("用时")} : ${runtime}</span>
        <span>${escapeHtml(getScheduleLabel(task))}</span>
      </div>
    `;

    li.addEventListener("click", () => loadLineAudioTaskDetail(task.id));
    listEl.appendChild(li);
  }
}

async function loadMoreLineAudioTasks() {
  if (!activeNovel || !lineAudioHasMore || lineAudioLoadingMore) return;
  lineAudioLoadingMore = true;
  renderLineAudioTaskList();
  try {
    const data = await fetchLineAudioTasks(activeNovel.id, {
      limit: LINE_AUDIO_TASK_PAGE_SIZE,
      offset: lineAudioNextOffset,
    });
    lineAudioTasks = lineAudioTasks.concat(data.lineAudioTasks || []);
    lineAudioNextOffset = Number(data.nextOffset || lineAudioTasks.length);
    lineAudioHasMore = Boolean(data.hasMore);
    lineAudioTotalCount = Number(data.totalCount || lineAudioTasks.length);
    lineAudioPendingCount = Number(data.pendingCount || 0);
    renderLineAudioTaskList();
    localizeDocumentText(document);
  } catch (err) {
    toast(t("error.loadFailed", { msg: err.message }));
  } finally {
    lineAudioLoadingMore = false;
    renderLineAudioTaskList();
  }
}

function getLineAudioTaskSignature(task) {
  return JSON.stringify({
    id: task.id,
    status: task.status || "",
    comfyStatus: task.comfyStatus || "",
    comfyPromptId: task.comfyPromptId || "",
    outputFilename: task.outputFilename || "",
    downloadedFilePath: task.downloadedFilePath || "",
    scheduledAt: task.scheduledAt || "",
    errorMessage: task.errorMessage || "",
    updatedAt: task.updatedAt || "",
  });
}

function renderLineAudioTaskDetail(task) {
  const detailEl = document.getElementById("lineAudioTaskDetail");
  activeLineAudioTaskSignature = getLineAudioTaskSignature(task);
  const lineCharCount = Array.from(String(task.lineText || "")).length;

  let html = '<div class="task-detail-grid">';

  html += `<div class="detail-row"><span class="detail-label">${translateText("任务ID:")}</span><span class="detail-value">${task.id}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("章节:")}</span><span class="detail-value">${escapeHtml(task.chapterTitle || `#${task.chapterNum}`)}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("台词序号:")}</span><span class="detail-value">${task.lineIndex + 1}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("角色:")}</span><span class="detail-value">${escapeHtml(task.roleName || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("台词:")}</span><span class="detail-value">${escapeHtml(task.lineText || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("台词字数:")}</span><span class="detail-value">${lineCharCount}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("参考文本:")}</span><span class="detail-value">${escapeHtml(task.referenceText || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("参考音频:")}</span><span class="detail-value">${escapeHtml(task.referenceAudioPath || "-")}</span></div>`;

  const status = task.status || "pending";
  html += `<div class="detail-row"><span class="detail-label">${translateText("状态:")}</span><span class="detail-value"><span class="status-badge ${statusClass(status)}">${statusText(status)}</span></span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("Comfy状态:")}</span><span class="detail-value">${escapeHtml(task.comfyStatus || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("Comfy Prompt ID:")}</span><span class="detail-value">${escapeHtml(task.comfyPromptId || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("输出文件:")}</span><span class="detail-value">${escapeHtml(task.outputFilename || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("本地下载路径:")}</span><span class="detail-value">${escapeHtml(task.downloadedFilePath || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("执行设置:")}</span><span class="detail-value">${escapeHtml(getScheduleLabel(task))}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("任务用时:")}</span><span class="detail-value">${calcTaskRuntime(task)}</span></div>`;

  if (task.errorMessage) {
    html += `<div class="detail-row"><span class="detail-label">${translateText("错误信息:")}</span><span class="detail-value error-text">${escapeHtml(task.errorMessage)}</span></div>`;
  }

  html += `<div class="detail-row"><span class="detail-label">${translateText("创建时间:")}</span><span class="detail-value">${formatDbDateTime(task.createdAt)}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">${translateText("更新时间:")}</span><span class="detail-value">${formatDbDateTime(task.updatedAt)}</span></div>`;

  html += '</div>';

  // 音频播放器
  if (status === "completed" && task.id) {
    html += '<div class="task-audio-section">';
    html += `<h4>${translateText("音频试听")}</h4>`;
    html += `<audio id="lineAudioTaskPlayer" controls preload="metadata" src="/api/line-audio-tasks/${Number(task.id)}/file"></audio>`;
    html += '</div>';
  }

  // 操作按钮
  html += '<div class="task-actions">';
  if (status === "failed" || status === "cancelled") {
    html += `<button class="ghost-btn retry-task-btn" data-task-id="${task.id}" type="button">${translateText("重试")}</button>`;
  }
  html += `<button class="ghost-btn danger delete-task-btn" data-task-id="${task.id}" type="button">${translateText("删除任务")}</button>`;
  html += '</div>';

  detailEl.innerHTML = html;

  const retryBtn = detailEl.querySelector(".retry-task-btn");
  if (retryBtn) {
    retryBtn.addEventListener("click", async () => {
      try {
        await retryLineAudioTask(task.id);
        toast(translateText("任务已重新加入队列"));
        await refreshLineAudioTasks();
        await loadLineAudioTaskDetail(task.id);
      } catch (err) {
        toast(err.message);
      }
    });
  }

  // 绑定删除按钮
  const deleteBtn = detailEl.querySelector(".delete-task-btn");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", async () => {
      if (!window.confirm(translateText("确定要删除这个任务吗？"))) return;
      try {
        await deleteLineAudioTask(task.id);
        toast(translateText("任务已删除"));
        lineAudioTasks = lineAudioTasks.filter((t) => t.id !== task.id);
        if (activeLineAudioTaskId === task.id) {
          activeLineAudioTaskId = null;
          detailEl.innerHTML = `<p class="empty-text">${translateText("请选择左侧任务查看详情。")}</p>`;
        }
        renderLineAudioTaskList();
        localizeDocumentText(document);
      } catch (err) {
        toast(err.message);
      }
    });
  }
}

async function loadLineAudioTaskList() {
  if (!activeNovel) return;
  try {
    const data = await fetchLineAudioTasks(activeNovel.id, {
      limit: LINE_AUDIO_TASK_PAGE_SIZE,
      offset: 0,
    });
    lineAudioTasks = data.lineAudioTasks || [];
    lineAudioNextOffset = Number(data.nextOffset || lineAudioTasks.length);
    lineAudioHasMore = Boolean(data.hasMore);
    lineAudioTotalCount = Number(data.totalCount || lineAudioTasks.length);
    lineAudioPendingCount = Number(data.pendingCount || 0);
    renderLineAudioTaskList();
    localizeDocumentText(document);
    if (activeLineAudioTaskId == null && lineAudioTasks.length > 0) {
      await loadLineAudioTaskDetail(lineAudioTasks[0].id);
    } else if (activeLineAudioTaskId != null) {
      await loadLineAudioTaskDetail(activeLineAudioTaskId);
    }
  } catch (err) {
    toast(t("error.loadFailed", { msg: err.message }));
  }
}

async function loadLineAudioTaskDetail(taskId) {
  if (!activeNovel) return;
  let task = lineAudioTasks.find((t) => t.id === taskId);
  if (!task) {
    try {
      task = await fetchLineAudioTaskDetail(taskId);
    } catch {
      return;
    }
  }

  activeLineAudioTaskId = taskId;
  renderLineAudioTaskList();

  const nextSignature = getLineAudioTaskSignature(task);
  if (activeLineAudioTaskSignature === nextSignature) {
    return;
  }
  renderLineAudioTaskDetail(task);
  localizeDocumentText(document);
}

async function refreshLineAudioTasks() {
  if (isTaskDetailAudioPlaying()) {
    return;
  }
  await loadLineAudioTaskList();
}

function applyLineAudioRefreshInterval() {
  if (lineAudioRefreshTimerId) {
    window.clearInterval(lineAudioRefreshTimerId);
    lineAudioRefreshTimerId = null;
  }
  const seconds = Number(document.getElementById("refreshLineAudioIntervalSelect").value || 0);
  if (!Number.isFinite(seconds) || seconds <= 0) return;
  lineAudioRefreshTimerId = window.setInterval(() => {
    refreshLineAudioTasks();
  }, seconds * 1000);
}

function restoreLineAudioRefreshInterval() {
  const select = document.getElementById("refreshLineAudioIntervalSelect");
  if (!select) return;
  const saved = localStorage.getItem(LINE_AUDIO_REFRESH_INTERVAL_KEY);
  if (saved != null && Array.from(select.options).some((option) => option.value === saved)) {
    select.value = saved;
  }
}

function bindActions() {
  document.getElementById("refreshLineAudioTasksBtn").addEventListener("click", () => {
    refreshLineAudioTasks();
  });

  document.getElementById("loadMoreLineAudioTasksBtn").addEventListener("click", () => {
    loadMoreLineAudioTasks();
  });

  document.getElementById("lineAudioTaskList").addEventListener("scroll", (event) => {
    const el = event.currentTarget;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 80) {
      loadMoreLineAudioTasks();
    }
  });

  document.getElementById("refreshLineAudioIntervalSelect").addEventListener("change", () => {
    const value = String(document.getElementById("refreshLineAudioIntervalSelect").value || "0");
    localStorage.setItem(LINE_AUDIO_REFRESH_INTERVAL_KEY, value);
    applyLineAudioRefreshInterval();
    const seconds = Number(value || 0);
    if (seconds > 0) {
      toast(`${translateText("已设置自动刷新")}: ${seconds}${translateText("秒")}`);
    }
  });

  document.getElementById("lineAudioNovelSelect").addEventListener("change", async (event) => {
    const id = Number(event.target.value);
    setActiveNovelId(id);
    activeNovel = allNovels.find((n) => Number(n.id) === id) || null;
    if (!activeNovel) return;
    setHeader(activeNovel);
    activeLineAudioTaskId = null;
    document.getElementById("lineAudioTaskDetail").innerHTML = `<p class="empty-text">${translateText("请选择左侧任务查看详情。")}</p>`;
    await refreshLineAudioTasks();
    toast(`${t("common.view")}: ${activeNovel.name}`);
  });
}

async function init() {
  clearNavBadge("lineAudio");
  renderNav();
  const data = await getData();
  allNovels = data.novels || [];
  activeNovel = getNovelByQueryOrActive();

  if (!activeNovel) {
    document.getElementById("lineAudioPageTitle").textContent = translateText("暂无小说");
    document.getElementById("lineAudioNovelSelect").innerHTML = `<option value="">${translateText("暂无小说")}</option>`;
    localizeDocumentText(document);
    return;
  }

  setActiveNovelId(activeNovel.id);
  setHeader(activeNovel);
  renderNovelSelect();
  restoreLineAudioRefreshInterval();
  bindActions();
  await refreshLineAudioTasks();
  applyLineAudioRefreshInterval();
  localizeDocumentText(document);
}

init().catch((err) => {
  renderNav();
  toast(t("error.pageLoad", { msg: err.message }));
});
