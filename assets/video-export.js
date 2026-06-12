import {
  bytesToText,
  cancelVideoExportTask,
  fetchVideoExportTasks,
  fetchVideoExportWorkerStatus,
  getActiveNovelId,
  getData,
  getVideoExportFileUrl,
  restartVideoExportWorker,
  retryVideoExportTask,
  setActiveNovelId,
} from "./store.js";
import { fmtDateTime, renderNav, toast } from "./ui.js";
import { localizeDocumentText, t } from "./i18n.js";

let novels = [];
let activeNovel = null;
let tasks = [];
let refreshTimer = null;
const VIDEO_EXPORT_REFRESH_INTERVAL_KEY = "ai_novel_video_export_refresh_interval";

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function statusText(status) {
  const value = String(status || "pending");
  if (value === "running") return "运行中";
  if (value === "completed") return "完成";
  if (value === "failed") return "失败";
  if (value === "cancelled") return "已取消";
  return "等待";
}

function statusClass(status) {
  if (status === "running") return "status-running";
  if (status === "completed") return "status-completed";
  if (status === "failed") return "status-failed";
  return "status-pending";
}

function formatDuration(seconds) {
  const sec = Math.max(0, Math.floor(Number(seconds || 0)));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function parseDbTime(text) {
  const raw = String(text || "").trim();
  if (!raw) return null;
  const iso = raw.includes("T") ? raw : raw.replace(" ", "T");
  const dt = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(dt.getTime()) ? null : dt;
}

function estimateRemainingText(task) {
  if (task.status !== "running") return "";
  const currentFrame = Number(task.currentFrame || 0);
  const totalFrames = Number(task.totalFrames || 0);
  const startedAt = parseDbTime(task.startedAt);
  if (!startedAt || currentFrame <= 0 || totalFrames <= currentFrame) return "预计剩余 -";
  const elapsedSeconds = Math.max(1, (Date.now() - startedAt.getTime()) / 1000);
  const framesPerSecond = currentFrame / elapsedSeconds;
  if (!Number.isFinite(framesPerSecond) || framesPerSecond <= 0) return "预计剩余 -";
  const remainingSeconds = (totalFrames - currentFrame) / framesPerSecond;
  return `预计剩余 ${formatDuration(remainingSeconds)}`;
}

function videoMetaText(task) {
  const sizeText = task.sizeBytes ? bytesToText(task.sizeBytes) : "-";
  return `${task.width}x${task.height} · ${task.fps}fps · 时长 ${formatDuration(task.durationSeconds)} · 存储 ${sizeText}`;
}

function renderNovelSelect() {
  const select = document.getElementById("videoExportNovelSelect");
  select.innerHTML = novels.map((novel) => `<option value="${novel.id}">${escapeHtml(novel.name)}</option>`).join("");
  if (activeNovel) select.value = String(activeNovel.id);
}

function renderWorkerStatus(status) {
  const el = document.getElementById("videoExportWorkerStatus");
  const state = String(status?.state || "stopped");
  const label = { running: "运行中", stale: "心跳超时", stopped: "未运行" }[state] || state;
  const age = status?.heartbeatAgeSeconds != null ? ` · 心跳${status.heartbeatAgeSeconds}s` : "";
  el.textContent = `Worker: ${label}${age}`;
}

function filteredTasks() {
  const status = document.getElementById("videoExportStatusFilter")?.value || "";
  const size = document.getElementById("videoExportSizeFilter")?.value || "";
  return tasks.filter((task) => {
    if (status && task.status !== status) return false;
    if (size && `${task.width}x${task.height}` !== size) return false;
    return true;
  });
}

function renderTasks() {
  const list = document.getElementById("videoExportTaskList");
  const summary = document.getElementById("videoExportSummary");
  const visible = filteredTasks();
  summary.textContent = `${visible.length} / ${tasks.length} 个任务`;
  if (!visible.length) {
    list.innerHTML = `<p class="empty-text">暂无视频导出任务。</p>`;
    return;
  }
  list.innerHTML = visible
    .map((task) => {
      const progress = Math.max(0, Math.min(100, Number(task.progress || 0)));
      const frameText = task.totalFrames ? `${task.currentFrame || 0} / ${task.totalFrames}` : "-";
      const remainingText = estimateRemainingText(task);
      const play = task.status === "completed" && task.downloadUrl
        ? `<button class="ghost-btn btn-sm" data-action="play" data-id="${task.id}" type="button">播放</button>`
        : "";
      const download = task.status === "completed" && task.downloadUrl
        ? `<a class="primary-btn btn-sm" href="${getVideoExportFileUrl(task.id)}">下载MP4</a>`
        : "";
      const retry = task.status === "failed" || task.status === "cancelled"
        ? `<button class="ghost-btn btn-sm" data-action="retry" data-id="${task.id}" type="button">重试</button>`
        : "";
      const cancel = task.status === "pending" || task.status === "running"
        ? `<button class="ghost-btn btn-sm" data-action="cancel" data-id="${task.id}" type="button">终止</button>`
        : "";
      return `
        <article class="task-detail-block">
          <div class="task-detail-head">
            <div>
              <h3>#${task.id} | 第${String(task.chapterNum).padStart(3, "0")}回 ${escapeHtml(task.chapterTitle)}</h3>
              <p class="meta">${escapeHtml(task.novelName)} · ${videoMetaText(task)}</p>
            </div>
            <span class="status-badge ${statusClass(task.status)}">${statusText(task.status)}</span>
          </div>
          <div class="progress-row"><span style="width:${progress}%"></span></div>
          <div class="task-list-meta">
            <span>进度 ${progress}%</span>
            <span>帧 ${frameText}</span>
            ${remainingText ? `<span>${remainingText}</span>` : ""}
            <span>文件 ${task.sizeBytes ? bytesToText(task.sizeBytes) : "-"}</span>
            <span>更新 ${escapeHtml(fmtDateTime(task.updatedAt))}</span>
          </div>
          ${task.errorMessage ? `<p class="error-text">${escapeHtml(task.errorMessage)}</p>` : ""}
          <div class="actions-row">${play}${download}${retry}${cancel}</div>
        </article>`;
    })
    .join("");
}

function getRefreshIntervalSeconds() {
  const value = Number(localStorage.getItem(VIDEO_EXPORT_REFRESH_INTERVAL_KEY) || 5);
  return [0, 3, 5, 10, 30].includes(value) ? value : 5;
}

function applyRefreshInterval() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  const seconds = getRefreshIntervalSeconds();
  const select = document.getElementById("videoExportRefreshIntervalSelect");
  if (select) select.value = String(seconds);
  if (seconds > 0) {
    refreshTimer = setInterval(refreshTasks, seconds * 1000);
  }
}

function openVideoPlayer(taskId) {
  const task = tasks.find((item) => String(item.id) === String(taskId));
  if (!task || task.status !== "completed") {
    toast("视频尚未导出完成");
    return;
  }
  const dialog = document.getElementById("videoExportPlayerDialog");
  const player = document.getElementById("videoExportPlayer");
  const title = document.getElementById("videoExportPlayerTitle");
  const meta = document.getElementById("videoExportPlayerMeta");
  if (!dialog || !player) return;
  title.textContent = `第${String(task.chapterNum).padStart(3, "0")}回 ${task.chapterTitle || ""}`;
  meta.textContent = videoMetaText(task);
  player.src = getVideoExportFileUrl(task.id);
  dialog.showModal();
}

function closeVideoPlayer() {
  const dialog = document.getElementById("videoExportPlayerDialog");
  const player = document.getElementById("videoExportPlayer");
  if (player) {
    player.pause();
    player.removeAttribute("src");
    player.load();
  }
  if (dialog?.open) dialog.close();
}

async function refreshWorkerStatus() {
  try {
    renderWorkerStatus(await fetchVideoExportWorkerStatus());
  } catch {
    renderWorkerStatus({ state: "stopped" });
  }
}

async function refreshTasks() {
  if (!activeNovel) return;
  try {
    tasks = await fetchVideoExportTasks(activeNovel.id);
    renderTasks();
    await refreshWorkerStatus();
  } catch (err) {
    toast(t("error.loadFailed", { msg: err.message }));
  }
}

function bindEvents() {
  document.getElementById("videoExportNovelSelect")?.addEventListener("change", (event) => {
    setActiveNovelId(event.target.value);
    activeNovel = novels.find((novel) => String(novel.id) === String(event.target.value)) || novels[0] || null;
    refreshTasks();
  });
  document.getElementById("videoExportStatusFilter")?.addEventListener("change", renderTasks);
  document.getElementById("videoExportSizeFilter")?.addEventListener("change", renderTasks);
  document.getElementById("videoExportRefreshIntervalSelect")?.addEventListener("change", (event) => {
    localStorage.setItem(VIDEO_EXPORT_REFRESH_INTERVAL_KEY, String(event.target.value || 0));
    applyRefreshInterval();
  });
  document.getElementById("refreshVideoExportTasksBtn")?.addEventListener("click", refreshTasks);
  document.getElementById("restartVideoExportWorkerBtn")?.addEventListener("click", async () => {
    try {
      await restartVideoExportWorker();
      toast("已重启视频导出 Worker");
      await refreshWorkerStatus();
    } catch (err) {
      toast(err.message);
    }
  });
  document.getElementById("videoExportTaskList")?.addEventListener("click", async (event) => {
    const btn = event.target.closest("button[data-action]");
    if (!btn) return;
    try {
      if (btn.dataset.action === "play") {
        openVideoPlayer(btn.dataset.id);
        return;
      }
      if (btn.dataset.action === "retry") await retryVideoExportTask(btn.dataset.id);
      if (btn.dataset.action === "cancel") await cancelVideoExportTask(btn.dataset.id);
      await refreshTasks();
    } catch (err) {
      toast(err.message);
    }
  });
  document.getElementById("videoExportPlayerCloseBtn")?.addEventListener("click", closeVideoPlayer);
  document.getElementById("videoExportPlayerDialog")?.addEventListener("close", closeVideoPlayer);
}

async function init() {
  renderNav();
  const data = await getData({ include: ["novels"] });
  novels = data.novels || [];
  const activeId = getActiveNovelId();
  activeNovel = novels.find((novel) => String(novel.id) === String(activeId)) || novels[0] || null;
  renderNovelSelect();
  bindEvents();
  await refreshTasks();
  applyRefreshInterval();
  localizeDocumentText(document);
}

window.addEventListener("beforeunload", () => {
  if (refreshTimer) clearInterval(refreshTimer);
});

init().catch((err) => toast(err.message));
