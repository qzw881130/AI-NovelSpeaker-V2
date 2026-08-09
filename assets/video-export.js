import {
  bytesToText,
  cancelVideoExportTask,
  fetchVideoExportTasks,
  fetchVideoExportWorkerStatus,
  fetchChapterIllustrationImages,
  getActiveNovelId,
  getData,
  getVideoExportCoverUrl,
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
let activeCoverTaskId = null;
let coverImageOptions = [];
let coverPreviewLoading = false;
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
  const subtitleText = String(task.subtitleMode || "srt") === "none" ? "无字幕" : "有字幕";
  return `${task.width}x${task.height} · ${task.fps}fps · ${subtitleText} · 时长 ${formatDuration(task.durationSeconds)} · 存储 ${sizeText}`;
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
  const subtitle = document.getElementById("videoExportSubtitleFilter")?.value || "";
  return tasks.filter((task) => {
    if (status && task.status !== status) return false;
    if (size && `${task.width}x${task.height}` !== size) return false;
    if (subtitle && String(task.subtitleMode || "srt") !== subtitle) return false;
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
      const downloadCover = task.status === "completed" && task.downloadUrl
        ? `<a class="ghost-btn btn-sm" href="${getVideoExportCoverUrl(task.id)}">下载封面</a>`
        : "";
      const previewCover = task.status === "completed" && task.downloadUrl
        ? `<button class="ghost-btn btn-sm" data-action="preview-cover" data-id="${task.id}" type="button">预览封面</button>`
        : "";
      const downloadSrt = task.status === "completed" && task.srtDownloadUrl
        ? `<a class="ghost-btn btn-sm" href="${task.srtDownloadUrl}">下载SRT字幕</a>`
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
          <div class="actions-row">${play}${download}${downloadSrt}${downloadCover}${previewCover}${retry}${cancel}</div>
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

function coverUrl(taskId, imageIndex) {
  const url = getVideoExportCoverUrl(taskId, imageIndex ? { imageIndex } : {});
  return `${url}${url.includes("?") ? "&" : "?"}v=${Date.now()}`;
}

function coverNavigationTasks() {
  return filteredTasks()
    .filter((task) => task.status === "completed" && task.downloadUrl)
    .slice()
    .sort((a, b) => Number(a.chapterNum || 0) - Number(b.chapterNum || 0));
}

function updateCoverNavigationButtons() {
  const prevBtn = document.getElementById("videoExportCoverPrevBtn");
  const nextBtn = document.getElementById("videoExportCoverNextBtn");
  const items = coverNavigationTasks();
  const index = items.findIndex((item) => String(item.id) === String(activeCoverTaskId));
  if (prevBtn) prevBtn.disabled = index <= 0;
  if (nextBtn) nextBtn.disabled = index < 0 || index >= items.length - 1;
}

async function navigateCoverPreview(direction) {
  if (!activeCoverTaskId || coverPreviewLoading) return;
  const items = coverNavigationTasks();
  const index = items.findIndex((item) => String(item.id) === String(activeCoverTaskId));
  if (index < 0) return;
  const next = items[index + Number(direction || 0)];
  if (!next) return;
  await openCoverPreview(next.id);
}

function updateCoverPreviewImage() {
  const img = document.getElementById("videoExportCoverPreview");
  const download = document.getElementById("videoExportCoverDownloadBtn");
  const select = document.getElementById("videoExportCoverImageSelect");
  const meta = document.getElementById("videoExportCoverImageMeta");
  if (!img || !activeCoverTaskId) return;
  const imageIndex = Number(select?.value || 0);
  const selected = coverImageOptions.find((item) => Number(item.index) === imageIndex) || null;
  const url = coverUrl(activeCoverTaskId, imageIndex);
  img.src = url;
  if (download) download.href = getVideoExportCoverUrl(activeCoverTaskId, imageIndex ? { imageIndex } : {});
  if (meta) {
    meta.textContent = selected
      ? `#${selected.index} ${selected.sceneTitle || selected.cnSummary || "插图"}`
      : "使用默认底图";
  }
}

async function openCoverPreview(taskId) {
  if (coverPreviewLoading) return;
  const task = tasks.find((item) => String(item.id) === String(taskId));
  if (!task || task.status !== "completed") {
    toast("视频尚未导出完成");
    return;
  }
  const dialog = document.getElementById("videoExportCoverDialog");
  const img = document.getElementById("videoExportCoverPreview");
  const title = document.getElementById("videoExportCoverTitle");
  const meta = document.getElementById("videoExportCoverMeta");
  const select = document.getElementById("videoExportCoverImageSelect");
  const imageMeta = document.getElementById("videoExportCoverImageMeta");
  if (!dialog || !img) return;
  coverPreviewLoading = true;
  activeCoverTaskId = task.id;
  coverImageOptions = [];
  updateCoverNavigationButtons();
  title.textContent = `第${String(task.chapterNum).padStart(3, "0")}回 ${task.chapterTitle || ""}`;
  meta.textContent = `${task.novelName || ""} · ${task.width}x${task.height}`;
  if (select) {
    select.innerHTML = `<option value="">加载插图中...</option>`;
    select.disabled = true;
  }
  if (imageMeta) imageMeta.textContent = "";
  img.removeAttribute("src");
  dialog.showModal();
  try {
    const images = await fetchChapterIllustrationImages(task.novelId, task.chapterNum);
    coverImageOptions = images.filter((item) => item.status === "completed" && item.imageUrl);
    if (select) {
      select.disabled = !coverImageOptions.length;
      select.innerHTML = coverImageOptions.length
        ? coverImageOptions.map((item) => `<option value="${Number(item.index)}">#${Number(item.index)} ${escapeHtml(item.sceneTitle || item.cnSummary || "插图")}</option>`).join("")
        : `<option value="">无可用插图，使用视频帧</option>`;
      if (coverImageOptions[0]) select.value = String(coverImageOptions[0].index);
    }
  } catch (err) {
    if (select) select.innerHTML = `<option value="">插图加载失败</option>`;
    toast(err.message);
  } finally {
    coverPreviewLoading = false;
    updateCoverNavigationButtons();
  }
  updateCoverPreviewImage();
}

function closeCoverPreview() {
  const dialog = document.getElementById("videoExportCoverDialog");
  const img = document.getElementById("videoExportCoverPreview");
  if (img) img.removeAttribute("src");
  activeCoverTaskId = null;
  coverImageOptions = [];
  coverPreviewLoading = false;
  updateCoverNavigationButtons();
  if (dialog?.open) dialog.close();
}

function handleCoverPreviewKeydown(event) {
  const dialog = document.getElementById("videoExportCoverDialog");
  if (!dialog?.open) return;
  const target = event.target;
  if (target?.matches?.("input, textarea, select")) return;
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    navigateCoverPreview(-1).catch((err) => toast(err.message));
  }
  if (event.key === "ArrowRight") {
    event.preventDefault();
    navigateCoverPreview(1).catch((err) => toast(err.message));
  }
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
  document.getElementById("videoExportSubtitleFilter")?.addEventListener("change", renderTasks);
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
      if (btn.dataset.action === "preview-cover") {
        await openCoverPreview(btn.dataset.id);
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
  document.getElementById("videoExportCoverCloseBtn")?.addEventListener("click", closeCoverPreview);
  document.getElementById("videoExportCoverDialog")?.addEventListener("close", closeCoverPreview);
  document.getElementById("videoExportCoverImageSelect")?.addEventListener("change", updateCoverPreviewImage);
  document.getElementById("videoExportCoverPrevBtn")?.addEventListener("click", () => {
    navigateCoverPreview(-1).catch((err) => toast(err.message));
  });
  document.getElementById("videoExportCoverNextBtn")?.addEventListener("click", () => {
    navigateCoverPreview(1).catch((err) => toast(err.message));
  });
  document.addEventListener("keydown", handleCoverPreviewKeydown);
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
