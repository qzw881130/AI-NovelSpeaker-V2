import {
  bytesToText,
  cancelVideoExportTask,
  createVideoCoverBundle,
  fetchVideoExportTasks,
  fetchVideoExportWorkerStatus,
  fetchVideoCoverBundleStatus,
  fetchChapterIllustrationImages,
  fetchYoutubePlaylists,
  fetchYoutubeSettings,
  getActiveNovelId,
  getData,
  getVideoCoverBundleFileUrl,
  getVideoExportCoverUrl,
  getVideoExportFileUrl,
  restartVideoExportWorker,
  retryYoutubeUploadTask,
  retryVideoExportTask,
  enqueueYoutubeUpload,
  setVideoExportCoverImage,
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
let taskSortBy = "id";
let taskIdSortDirection = "desc";
let coverBundlePollTimer = null;
let activeYoutubeUploadTaskId = null;
let youtubeSettings = null;
const VIDEO_EXPORT_REFRESH_INTERVAL_KEY = "ai_novel_video_export_refresh_interval";
const VIDEO_EXPORT_SORT_BY_KEY = "ai_novel_video_export_sort_by";
const VIDEO_EXPORT_SORT_DIRECTION_KEY = "ai_novel_video_export_sort_direction";

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

function uploadStatusText(upload) {
  const status = String(upload?.status || "");
  if (!status) return "未上传";
  if (status === "pending") return "等待上传";
  if (status === "running") return `上传中 ${Number(upload.progress || 0)}%`;
  if (status === "completed") return "上传完成";
  if (status === "failed") return "上传失败";
  return status;
}

function renderYoutubeUploadProgress(upload) {
  if (!upload) return "";
  const status = String(upload.status || "pending");
  const progress = status === "completed" ? 100 : Math.max(0, Math.min(100, Number(upload.progress || 0)));
  const link = upload.youtubeUrl
    ? `<a href="${escapeHtml(upload.youtubeUrl)}" target="_blank" rel="noopener noreferrer">查看视频</a>`
    : "";
  return `
    <div class="youtube-upload-progress ${status === "running" ? "is-running" : ""} status-${escapeHtml(status)}">
      <div class="youtube-upload-ring" style="--progress:${progress}">
        <span>${progress}%</span>
      </div>
      <div>
        <strong>油管上传：${escapeHtml(uploadStatusText(upload))}</strong>
        ${link ? `<p>${link}</p>` : ""}
      </div>
    </div>`;
}

function youtubeUploadErrorSummary(raw, status = "") {
  const text = String(raw || "").trim();
  const isFailed = String(status || "") === "failed";
  const isQuota = /quotaExceeded|youtube\.quota|exceeded your quota|配额|quota/i.test(text);
  const steps = [];
  if (text.includes("封面上传失败")) steps.push("封面");
  if (text.includes("字幕上传失败")) steps.push("字幕");
  if (text.includes("加入播放列表失败")) steps.push("播放列表");
  const uniqueSteps = Array.from(new Set(steps));
  if (isQuota) {
    return {
      title: "YouTube API 配额已用尽",
      summary: isFailed
        ? "YouTube API 配额不足，上传任务未完成。"
        : uniqueSteps.length
        ? `主视频已上传成功，但配额不足，${uniqueSteps.join("、")}未完成。`
        : "YouTube API 配额不足，部分上传步骤未完成。",
      steps: uniqueSteps,
    };
  }
  return {
    title: "油管上传提醒",
    summary: isFailed
      ? "上传任务失败，请展开技术详情查看原因。"
      : uniqueSteps.length
      ? `主视频已上传成功，但${uniqueSteps.join("、")}未完成。`
      : "上传过程中出现附加步骤错误。",
    steps: uniqueSteps,
  };
}

function renderYoutubeUploadError(raw, status = "") {
  const text = String(raw || "").trim();
  if (!text) return "";
  const info = youtubeUploadErrorSummary(text, status);
  const isFailed = String(status || "") === "failed";
  const stepChips = info.steps.length
    ? `<div class="youtube-upload-warning-steps">${info.steps.map((step) => `<span>${escapeHtml(step)}</span>`).join("")}</div>`
    : "";
  return `
    <div class="youtube-upload-warning">
      <div class="youtube-upload-warning-head">
        <strong>${escapeHtml(info.title)}</strong>
        <span>${isFailed ? "失败" : "非致命"}</span>
      </div>
      <p>${escapeHtml(info.summary)}</p>
      ${stepChips}
      <details>
        <summary>查看技术详情</summary>
        <button class="ghost-btn btn-sm youtube-upload-copy-error-btn" data-action="copy-youtube-upload-error" type="button">复制详情</button>
        <pre>${escapeHtml(text)}</pre>
      </details>
    </div>`;
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

function sortedTasks(items) {
  const direction = taskIdSortDirection === "asc" ? 1 : -1;
  return items.slice().sort((a, b) => {
    if (taskSortBy === "chapterNum") {
      const chapterCompare = Number(a.chapterNum || 0) - Number(b.chapterNum || 0);
      if (chapterCompare !== 0) return chapterCompare * direction;
    }
    if (taskSortBy === "chapterTitle") {
      const titleCompare = String(a.chapterTitle || "").localeCompare(String(b.chapterTitle || ""), "zh-Hans", { numeric: true });
      if (titleCompare !== 0) return titleCompare * direction;
    }
    return (Number(a.id || 0) - Number(b.id || 0)) * direction;
  });
}

function restoreTaskSortOptions() {
  const savedSortBy = localStorage.getItem(VIDEO_EXPORT_SORT_BY_KEY);
  const savedDirection = localStorage.getItem(VIDEO_EXPORT_SORT_DIRECTION_KEY);
  taskSortBy = ["id", "chapterNum", "chapterTitle"].includes(savedSortBy) ? savedSortBy : "id";
  taskIdSortDirection = savedDirection === "asc" ? "asc" : "desc";
  const select = document.getElementById("videoExportSortBySelect");
  if (select) select.value = taskSortBy;
}

function renderSortOrderButton() {
  const btn = document.getElementById("videoExportSortOrderBtn");
  if (!btn) return;
  const isAsc = taskIdSortDirection === "asc";
  btn.innerHTML = isAsc
    ? `<svg class="sort-order-icon" viewBox="0 0 24 24" aria-hidden="true"><path class="sort-order-line" d="M4 18h8M4 13h11M4 8h14"/><path class="sort-order-arrow" d="M19 18V6m0 0-4 4m4-4 4 4"/></svg>`
    : `<svg class="sort-order-icon" viewBox="0 0 24 24" aria-hidden="true"><path class="sort-order-line" d="M4 6h8M4 11h11M4 16h14"/><path class="sort-order-arrow" d="M19 6v12m0 0-4-4m4 4 4-4"/></svg>`;
  const sortName = taskSortBy === "chapterTitle" ? "章回标题" : taskSortBy === "chapterNum" ? "编号" : "ID";
  const label = isAsc ? `按 ${sortName} 升序排列` : `按 ${sortName} 降序排列`;
  btn.setAttribute("aria-label", label);
  btn.title = label;
}

function renderTasks() {
  const list = document.getElementById("videoExportTaskList");
  const summary = document.getElementById("videoExportSummary");
  const visible = sortedTasks(filteredTasks());
  renderSortOrderButton();
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
      const upload = task.youtubeUpload || null;
      const uploadYoutube = task.status === "completed" && task.downloadUrl
        ? `<button class="ghost-btn btn-sm" data-action="upload-youtube" data-id="${task.id}" type="button">上传油管</button>`
        : "";
      const youtubeUploadProgress = renderYoutubeUploadProgress(upload);
      const retryYoutubeUpload = upload && ["failed", "running"].includes(String(upload.status || ""))
        ? `<button class="ghost-btn btn-sm" data-action="retry-youtube-upload" data-upload-id="${upload.id}" type="button">重试油管</button>`
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
          ${youtubeUploadProgress}
          ${task.errorMessage ? `<p class="error-text">${escapeHtml(task.errorMessage)}</p>` : ""}
          ${renderYoutubeUploadError(upload?.errorMessage, upload?.status)}
          <div class="actions-row">${play}${download}${downloadSrt}${downloadCover}${previewCover}${uploadYoutube}${retryYoutubeUpload}${retry}${cancel}</div>
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
  const setBtn = document.getElementById("videoExportCoverSetBtn");
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
  const task = tasks.find((item) => String(item.id) === String(activeCoverTaskId));
  if (setBtn) {
    const saved = Number(task?.coverImageIndex || 0);
    setBtn.disabled = !imageIndex || saved === imageIndex;
    setBtn.textContent = saved === imageIndex ? "当前封面" : "设为封面";
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
      const savedIndex = Number(task.coverImageIndex || 0);
      const defaultIndex = savedIndex || Number(coverImageOptions[0]?.index || 0);
      if (defaultIndex) select.value = String(defaultIndex);
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

async function setActiveCoverImage() {
  if (!activeCoverTaskId) return;
  const select = document.getElementById("videoExportCoverImageSelect");
  const imageIndex = Number(select?.value || 0);
  if (!imageIndex) return;
  const updated = await setVideoExportCoverImage(activeCoverTaskId, imageIndex);
  tasks = tasks.map((task) => String(task.id) === String(activeCoverTaskId) ? { ...task, ...(updated || {}), coverImageIndex: imageIndex } : task);
  toast("已设置视频封面");
  renderTasks();
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

function clearCoverBundlePoll() {
  if (coverBundlePollTimer) {
    clearInterval(coverBundlePollTimer);
    coverBundlePollTimer = null;
  }
}

function renderCoverBundleStatus(task) {
  const progressWrap = document.getElementById("videoExportCoverBundleProgressWrap");
  const progressBar = document.getElementById("videoExportCoverBundleProgressBar");
  const status = document.getElementById("videoExportCoverBundleStatus");
  const link = document.getElementById("videoExportCoverBundleDownloadLink");
  const startBtn = document.getElementById("videoExportCoverBundleStartBtn");
  const current = Number(task?.current || 0);
  const total = Number(task?.total || 0);
  const percent = total > 0 ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : 0;
  progressWrap?.classList.remove("hidden");
  if (progressBar) progressBar.style.width = `${percent}%`;
  if (link) {
    const fileName = String(task?.bundle?.fileName || "");
    link.classList.toggle("hidden", !fileName || task?.status !== "completed");
    if (fileName && activeNovel) {
      link.href = getVideoCoverBundleFileUrl(activeNovel.id, fileName);
      link.textContent = `下载 ZIP (${bytesToText(task.bundle.sizeBytes || 0)})`;
    }
  }
  if (startBtn) startBtn.disabled = task?.status === "queued" || task?.status === "running";
  if (!status) return;
  if (task?.status === "completed") {
    status.textContent = `打包完成：${current} / ${total}`;
    clearCoverBundlePoll();
  } else if (task?.status === "failed") {
    status.textContent = `打包失败：${task.error || "未知错误"}`;
    clearCoverBundlePoll();
  } else if (task?.status === "queued") {
    status.textContent = "已加入打包队列...";
  } else if (task?.status === "running") {
    status.textContent = `正在打包：${current} / ${total}`;
  } else {
    status.textContent = "等待打包";
  }
}

async function pollCoverBundleStatus() {
  if (!activeNovel) return;
  const task = await fetchVideoCoverBundleStatus(activeNovel.id);
  renderCoverBundleStatus(task || { status: "idle", current: 0, total: 0 });
}

function openCoverBundleDialog() {
  const dialog = document.getElementById("videoExportCoverBundleDialog");
  const meta = document.getElementById("videoExportCoverBundleMeta");
  const link = document.getElementById("videoExportCoverBundleDownloadLink");
  if (!dialog) return;
  if (meta) meta.textContent = `将《${activeNovel?.name || "当前小说"}》已完成的视频封面打包为 ZIP。`;
  link?.classList.add("hidden");
  renderCoverBundleStatus({ status: "idle", current: 0, total: 0 });
  dialog.showModal();
}

function closeCoverBundleDialog() {
  clearCoverBundlePoll();
  const dialog = document.getElementById("videoExportCoverBundleDialog");
  if (dialog?.open) dialog.close();
}

function defaultYoutubeTitle(task) {
  const channelName = String(youtubeSettings?.channelName || "旺仔有声小说").trim() || "旺仔有声小说";
  return `${task.novelName || ""}|${task.chapterTitle || ""} | ${channelName}`;
}

function defaultYoutubePlaylist(task) {
  return `有声《${task.novelName || ""}》`;
}

async function ensureYoutubeSettings() {
  youtubeSettings = await fetchYoutubeSettings();
  return youtubeSettings;
}

async function openYoutubeUploadDialog(taskId) {
  const task = tasks.find((item) => String(item.id) === String(taskId));
  if (!task || task.status !== "completed") {
    toast("视频尚未导出完成");
    return;
  }
  await ensureYoutubeSettings();
  activeYoutubeUploadTaskId = task.id;
  const upload = task.youtubeUpload || null;
  const uploadStatus = String(upload?.status || "");
  const isUploading = ["pending", "running"].includes(uploadStatus);
  const dialog = document.getElementById("youtubeUploadDialog");
  const coverPreview = document.getElementById("youtubeUploadCoverPreview");
  const defaultPlaylist = upload?.playlistTitle || defaultYoutubePlaylist(task);
  const submitBtn = document.getElementById("youtubeUploadSubmitBtn");
  document.getElementById("youtubeUploadMeta").textContent = `#${task.id} · ${task.novelName || ""} · 第${String(task.chapterNum).padStart(3, "0")}回`;
  document.getElementById("youtubeUploadTitle").value = upload?.title || defaultYoutubeTitle(task);
  document.getElementById("youtubeUploadTags").value = upload?.tags || youtubeSettings?.defaultTags || "四大名著,三国演义,有声小说,旺仔有声小说";
  document.getElementById("youtubeUploadPrivacy").value = upload?.privacyStatus || "private";
  if (submitBtn) {
    submitBtn.disabled = isUploading;
    submitBtn.textContent = isUploading ? "上传中" : "上传";
    submitBtn.title = isUploading ? "当前任务正在上传，不能重复提交" : "";
  }
  document.getElementById("youtubeUploadRecordDate").textContent = new Date().toISOString().slice(0, 10);
  document.getElementById("youtubeUploadSubtitleText").textContent = task.srtDownloadUrl ? "将上传对应 SRT 字幕" : "未找到对应 SRT 字幕";
  document.getElementById("youtubeUploadCoverText").textContent = task.coverImageIndex ? `使用 #${task.coverImageIndex} 插图封面` : "未设置，使用第一个插图作为封面";
  if (coverPreview) {
    coverPreview.src = getVideoExportCoverUrl(task.id);
    coverPreview.classList.remove("hidden");
  }
  const playlistSelect = document.getElementById("youtubeUploadPlaylistSelect");
  if (playlistSelect) playlistSelect.innerHTML = `<option value="${escapeHtml(defaultPlaylist)}">${escapeHtml(defaultPlaylist)}</option>`;
  loadYoutubePlaylistOptions(defaultPlaylist).catch((err) => toast(err.message));
  dialog.showModal();
}

function closeYoutubeUploadDialog() {
  const dialog = document.getElementById("youtubeUploadDialog");
  const coverPreview = document.getElementById("youtubeUploadCoverPreview");
  activeYoutubeUploadTaskId = null;
  if (coverPreview) {
    coverPreview.removeAttribute("src");
    coverPreview.classList.add("hidden");
  }
  if (dialog?.open) dialog.close();
  const submitBtn = document.getElementById("youtubeUploadSubmitBtn");
  if (submitBtn) {
    submitBtn.disabled = false;
    submitBtn.textContent = "上传";
    submitBtn.title = "";
  }
}

function renderYoutubePlaylistOptions(items, selectedTitle) {
  const select = document.getElementById("youtubeUploadPlaylistSelect");
  if (!select) return;
  const selected = String(selectedTitle || select.value || "").trim();
  const titles = [];
  if (selected) titles.push(selected);
  for (const item of items || []) {
    const title = String(item.title || "").trim();
    if (title && !titles.includes(title)) titles.push(title);
  }
  select.innerHTML = titles.length
    ? titles.map((title) => `<option value="${escapeHtml(title)}">${escapeHtml(title)}</option>`).join("")
    : `<option value="">未读取到播放列表</option>`;
  if (selected) select.value = selected;
}

async function loadYoutubePlaylistOptions(selectedTitle = "", options = {}) {
  const select = document.getElementById("youtubeUploadPlaylistSelect");
  if (!select) return;
  const previous = String(selectedTitle || select.value || "").trim();
  select.innerHTML = `<option value="">拉取中...</option>`;
  renderYoutubePlaylistOptions(await fetchYoutubePlaylists(options), previous);
}

async function submitYoutubeUpload() {
  if (!activeYoutubeUploadTaskId) return;
  const payload = {
    title: document.getElementById("youtubeUploadTitle")?.value || "",
    playlistTitle: document.getElementById("youtubeUploadPlaylistSelect")?.value || "",
    tags: document.getElementById("youtubeUploadTags")?.value || "",
    privacyStatus: document.getElementById("youtubeUploadPrivacy")?.value || "private",
  };
  await enqueueYoutubeUpload(activeYoutubeUploadTaskId, payload);
  toast("已加入油管上传队列");
  closeYoutubeUploadDialog();
  await refreshTasks();
}

async function startCoverBundle() {
  if (!activeNovel) return;
  clearCoverBundlePoll();
  const task = await createVideoCoverBundle(activeNovel.id);
  renderCoverBundleStatus(task || { status: "queued", current: 0, total: 0 });
  coverBundlePollTimer = setInterval(() => {
    pollCoverBundleStatus().catch((err) => toast(err.message));
  }, 1000);
  await pollCoverBundleStatus();
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
  document.getElementById("videoExportSortBySelect")?.addEventListener("change", (event) => {
    taskSortBy = ["id", "chapterNum", "chapterTitle"].includes(event.target.value) ? event.target.value : "id";
    localStorage.setItem(VIDEO_EXPORT_SORT_BY_KEY, taskSortBy);
    renderTasks();
  });
  document.getElementById("videoExportSortOrderBtn")?.addEventListener("click", () => {
    taskIdSortDirection = taskIdSortDirection === "asc" ? "desc" : "asc";
    localStorage.setItem(VIDEO_EXPORT_SORT_DIRECTION_KEY, taskIdSortDirection);
    renderTasks();
  });
  document.getElementById("videoExportCoverBundleBtn")?.addEventListener("click", openCoverBundleDialog);
  document.getElementById("videoExportCoverBundleCloseBtn")?.addEventListener("click", closeCoverBundleDialog);
  document.getElementById("videoExportCoverBundleDialog")?.addEventListener("close", closeCoverBundleDialog);
  document.getElementById("videoExportCoverBundleStartBtn")?.addEventListener("click", () => {
    startCoverBundle().catch((err) => toast(err.message));
  });
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
      if (btn.dataset.action === "copy-youtube-upload-error") {
        const text = btn.closest("details")?.querySelector("pre")?.textContent || "";
        if (text) {
          await navigator.clipboard.writeText(text);
          toast("已复制油管上传错误详情");
        }
        return;
      }
      if (btn.dataset.action === "preview-cover") {
        await openCoverPreview(btn.dataset.id);
        return;
      }
      if (btn.dataset.action === "upload-youtube") {
        await openYoutubeUploadDialog(btn.dataset.id);
        return;
      }
      if (btn.dataset.action === "retry-youtube-upload") {
        await retryYoutubeUploadTask(btn.dataset.uploadId);
        toast("已重新加入油管上传队列");
        await refreshTasks();
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
  document.getElementById("videoExportCoverSetBtn")?.addEventListener("click", () => {
    setActiveCoverImage().catch((err) => toast(err.message));
  });
  document.getElementById("videoExportCoverPrevBtn")?.addEventListener("click", () => {
    navigateCoverPreview(-1).catch((err) => toast(err.message));
  });
  document.getElementById("videoExportCoverNextBtn")?.addEventListener("click", () => {
    navigateCoverPreview(1).catch((err) => toast(err.message));
  });
  document.getElementById("youtubeUploadCloseBtn")?.addEventListener("click", closeYoutubeUploadDialog);
  document.getElementById("youtubeUploadDialog")?.addEventListener("close", closeYoutubeUploadDialog);
  document.getElementById("youtubeUploadLoadPlaylistsBtn")?.addEventListener("click", () => {
    loadYoutubePlaylistOptions("", { refresh: true }).catch((err) => toast(err.message));
  });
  document.getElementById("youtubeUploadSubmitBtn")?.addEventListener("click", () => {
    submitYoutubeUpload().catch((err) => toast(err.message));
  });
  document.addEventListener("keydown", handleCoverPreviewKeydown);
}

async function init() {
  renderNav();
  restoreTaskSortOptions();
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
  clearCoverBundlePoll();
});

init().catch((err) => toast(err.message));
