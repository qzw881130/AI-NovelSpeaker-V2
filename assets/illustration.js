import {
  cancelPendingIllustrationImages,
  cancelPendingIllustrationTasks,
  cancelChapterIllustrationPromptBatch,
  enqueueChapterIllustration,
  enqueueAllIllustrationImages,
  enqueueIllustrationImage,
  fetchChapterIllustrationImages,
  fetchChapterIllustrationLlmParams,
  fetchChapterIllustrationPromptBatches,
  fetchChapterIllustrationPayload,
  fetchIllustrationImageWorkerStatus,
  fetchIllustrationLlmWorkerStatus,
  fetchIllustrationWorkerStatus,
  fetchIllustrationPromptItemOriginal,
  fetchNovelIllustrationChapters,
  getActiveNovelId,
  getData,
  getVideoExportFileUrl,
  optimizeIllustrationPromptItem,
  prepareIllustrationPromptItemOptimization,
  restartIllustrationImageWorker,
  restartIllustrationLlmWorker,
  retryChapterIllustrationPromptBatch,
  saveChapterIllustrationSceneOutput,
  saveChapterIllustrationPromptItem,
  saveChapterIllustrationPromptOutput,
  setActiveNovelId,
} from "./store.js";
import { renderNav, toast } from "./ui.js";

let allNovels = [];
let activeNovel = null;
let chapterItems = [];
let autoRefreshTimer = 0;
let elapsedRefreshTimer = 0;
let imagesRefreshTimer = 0;
let activeImagesChapterNum = 0;
let activePayloadChapterNum = 0;
let activePayloadStage = "";
let activePayloadKind = "";
let activePromptBatchesChapterNum = 0;
let activePromptBatches = [];
let activePromptBatchIndex = 0;
let activePromptBatchTab = "llm";
let activePromptBatchStage = "prompt";
let promptBatchesRefreshTimer = 0;
let activePromptOutputChapterNum = 0;
let currentImageItems = [];
let currentPreviewItems = [];
let currentPreviewIndex = -1;
let currentImagesTotal = 0;
let previewJsonDirty = false;
let previewJsonImageKey = "";
let previewJsonOriginalText = "";
let optimizeDetailTab = "llm";
let optimizeDetail = { requestPreview: null, inputText: "", outputText: "", jsonText: "", status: "idle", error: "" };
let activeImageDataText = "";
const selectedChapterNums = new Set();
let dragSelecting = false;
let dragSelectValue = true;
const AUTO_REFRESH_KEY = "ai_novel_illustration_refresh_interval";
const AUTO_REFRESH_VALUES = ["0", "5", "20", "60"];
const IMAGES_REFRESH_KEY = "ai_novel_illustration_images_refresh_seconds";
const REQUIRED_PROMPT_ITEM_KEYS = [
  "index",
  "origin",
  "country",
  "culture",
  "era",
  "visual_style",
  "scene_type",
  "scene_type_weight",
  "style_strength",
  "scene_title",
  "cn_summary",
  "human_count",
  "visual_character_card",
  "positive_core",
  "positive_character",
  "positive_scene",
  "positive_camera",
  "positive_style",
  "negative",
  "suggested_size",
];

const STAGE_LABELS = {
  scene: "Scene",
  shot: "Shot",
  prompt: "Prompt",
};

const SCENE_AUDIO_DIFF_WARNING_SECONDS = 8;

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text || "");
  return div.innerHTML;
}

function copyText(text) {
  const value = String(text || "");
  if (!value.trim() || value === "加载中...") {
    toast("暂无可复制内容");
    return Promise.resolve();
  }
  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    return navigator.clipboard.writeText(value).then(() => toast("内容已复制"));
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
  toast("内容已复制");
  return Promise.resolve();
}

function formatJsonIfPossible(text) {
  const value = String(text || "").trim();
  if (!value) return "";
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function parseJsonObjectText(text) {
  const parsed = JSON.parse(String(text || ""));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON必须是对象");
  }
  return parsed;
}

function safeDownloadFilename(value, fallback = "prompt-output") {
  return String(value || "")
    .trim()
    .replace(/[\\/:*?"<>|\x00-\x1f]/g, "_")
    .replace(/\s+/g, " ")
    .replace(/[. ]+$/g, "")
    || fallback;
}

function downloadTextFile(filename, content, type = "application/json;charset=utf-8") {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function getNovelByQueryOrActive() {
  const url = new URL(window.location.href);
  const queryId = String(url.searchParams.get("novelId") || "");
  if (queryId) return allNovels.find((n) => String(n.id) === queryId) || null;
  const activeId = getActiveNovelId();
  if (activeId) return allNovels.find((n) => String(n.id) === activeId) || null;
  return allNovels[0] || null;
}

function statusLabel(status) {
  const mapping = {
    idle: "未处理",
    pending: "待处理",
    running: "处理中",
    processing: "处理中",
    failed: "失败",
    timeout: "超时",
    completed: "完成",
    cancelled: "已取消",
  };
  return mapping[String(status || "idle")] || String(status || "-");
}

function statusClass(status) {
  const normalized = String(status || "idle");
  if (normalized === "completed") return "status-badge status-completed";
  if (["failed", "timeout"].includes(normalized)) return "status-badge status-failed";
  if (["running", "processing", "pending"].includes(normalized)) return "status-badge status-pending";
  return "status-badge";
}

function parseDateTime(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  const normalized = text.includes("T") ? text : text.replace(" ", "T");
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized);
  const date = new Date(hasTimezone ? normalized : `${normalized}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatElapsedSeconds(seconds) {
  const total = Math.max(0, Math.round(Number(seconds || 0)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function stageElapsedLabel(data) {
  const startedAt = parseDateTime(data?.startedAt);
  if (!startedAt) return "";
  const status = String(data?.status || "");
  const updatedAt = parseDateTime(data?.updatedAt);
  const endAt = ["pending", "running", "processing"].includes(status) || !updatedAt ? new Date() : updatedAt;
  return `本次执行耗时 {${formatElapsedSeconds((endAt.getTime() - startedAt.getTime()) / 1000)}}`;
}

function stageElapsedAttrs(data) {
  return [
    `data-status="${escapeHtml(data?.status || "")}"`,
    `data-started-at="${escapeHtml(data?.startedAt || "")}"`,
    `data-updated-at="${escapeHtml(data?.updatedAt || "")}"`,
  ].join(" ");
}

function updateLiveElapsedLabels() {
  document.querySelectorAll(".stage-elapsed").forEach((el) => {
    const status = String(el.dataset.status || "");
    if (!["pending", "running", "processing"].includes(status)) return;
    const startedAt = parseDateTime(el.dataset.startedAt || "");
    if (!startedAt) return;
    el.textContent = `本次执行耗时 {${formatElapsedSeconds((Date.now() - startedAt.getTime()) / 1000)}}`;
  });
  updatePromptBatchLiveElapsed();
}

function renderNovelSelect() {
  const select = document.getElementById("illustrationNovelSelect");
  select.innerHTML = allNovels.map((novel) => `<option value="${novel.id}">${escapeHtml(novel.name)}</option>`).join("");
  if (activeNovel) select.value = String(activeNovel.id);
}

function setHeader() {
  const titleEl = document.getElementById("illustrationPageTitle");
  const metaEl = document.getElementById("illustrationPageMeta");
  const summaryEl = document.getElementById("illustrationSummary");
  const imageSummaryEl = document.getElementById("illustrationImageSummary");
  const chaptersLink = document.getElementById("illustrationChaptersLink");
  if (!activeNovel) {
    titleEl.textContent = "生成插画";
    metaEl.textContent = "未找到小说";
    summaryEl.textContent = "-";
    if (imageSummaryEl) imageSummaryEl.textContent = "插画共0个，已生成0个，待生成0个";
    return;
  }
  const completed = chapterItems.reduce((sum, item) => {
    return sum + ["scene", "shot", "prompt"].filter((stage) => item.stages?.[stage]?.status === "completed").length;
  }, 0);
  const illustrationTotal = chapterItems.reduce((sum, item) => sum + Number(item.illustrationCount || 0), 0);
  const imageGenerated = chapterItems.reduce((sum, item) => sum + Number(item.images?.generated || 0), 0);
  const imageQueued = chapterItems.reduce((sum, item) => sum + Number(item.images?.queued || 0), 0);
  const imageNotQueued = chapterItems.reduce((sum, item) => sum + Number(item.images?.unqueued || 0), 0);
  titleEl.textContent = `${activeNovel.name} - 生成插画`;
  metaEl.textContent = `共 ${chapterItems.length} 回 · 已完成 ${completed}/${chapterItems.length * 3} 项`;
  summaryEl.textContent = `每回依次解析 scene、shot、prompt`;
  if (imageSummaryEl) {
    imageSummaryEl.innerHTML = [
      `插画共<span class="illustration-stat-number illustration-stat-total">${illustrationTotal}</span>个`,
      `已生成<span class="illustration-stat-number illustration-stat-generated">${imageGenerated}</span>个`,
      `待生成<span class="illustration-stat-number illustration-stat-queued">${imageQueued}</span>个`,
      `未入队列<span class="illustration-stat-number illustration-stat-not-queued">${imageNotQueued}</span>个`,
    ].join("，");
  }
  chaptersLink.href = `./chapters.html?novelId=${encodeURIComponent(activeNovel.id)}`;
}

function renderStageCell(item, stage) {
  const data = item.stages?.[stage] || { status: "idle", progress: 0, errorMessage: "" };
  const chapterNum = Number(item.chapterNum || 0);
  const progress = Number(data.progress || 0);
  const disabled = ["pending", "running", "processing"].includes(String(data.status || ""));
  const sceneWarning = stage === "scene" ? sceneTimingWarningFromChapter(item) : null;
  const shotWarning = stage === "shot" ? shotCountWarningFromChapter(item) : null;
  const promptJsonWarning = stage === "prompt" && item.promptJsonWarning?.hasWarning ? item.promptJsonWarning : null;
  const promptWarningTitle = promptJsonWarning
    ? `存在缺少key的插图JSON：${(promptJsonWarning.items || []).map((entry) => `#${entry.index}`).join(", ")}`
    : "";
  const actionButtons = stage === "prompt"
    ? `
        <button class="ghost-btn btn-sm illustration-run-btn" type="button" data-stage="${stage}" data-chapter-num="${chapterNum}" ${disabled ? "disabled" : ""}>解析插画${STAGE_LABELS[stage].toLowerCase()}</button>
        <button class="ghost-btn btn-sm illustration-prompt-detail-btn" type="button" data-stage="${stage}" data-chapter-num="${chapterNum}">详情</button>
        <button class="ghost-btn btn-sm illustration-prompt-output-btn" type="button" data-chapter-num="${chapterNum}">输出</button>
        ${item.videoExportTaskId ? `<button class="primary-btn btn-sm illustration-video-btn" type="button" data-chapter-num="${chapterNum}" data-task-id="${Number(item.videoExportTaskId)}"><span aria-hidden="true">▶</span> 播放视频</button>` : ""}
        ${data.status === "completed" ? renderImagesButton(item, chapterNum, promptWarningTitle) : ""}
      `
    : `
        <button class="ghost-btn btn-sm illustration-run-btn" type="button" data-stage="${stage}" data-chapter-num="${chapterNum}" ${disabled ? "disabled" : ""}>解析插画${STAGE_LABELS[stage].toLowerCase()}</button>
        ${stage === "scene" ? `<button class="ghost-btn btn-sm illustration-llm-params-btn" type="button" data-stage="${stage}" data-chapter-num="${chapterNum}">LLM参数</button>` : ""}
        ${stage === "shot" ? `<button class="ghost-btn btn-sm illustration-prompt-detail-btn" type="button" data-stage="${stage}" data-chapter-num="${chapterNum}">详情</button>` : ""}
        <button class="ghost-btn btn-sm illustration-view-btn" type="button" data-kind="input" data-stage="${stage}" data-chapter-num="${chapterNum}">输入</button>
        <button class="ghost-btn btn-sm illustration-view-btn ${sceneWarning || shotWarning ? "has-illustration-warning" : ""}" type="button" data-kind="output" data-stage="${stage}" data-chapter-num="${chapterNum}" ${sceneWarning || shotWarning ? `title="${escapeHtml(sceneWarning || shotWarning)}"` : ""}>输出${sceneWarning || shotWarning ? '<span class="illustration-alert-dot" aria-hidden="true">!</span>' : ""}</button>
      `;
  return `
    <div class="stage-cell">
      <div class="stage-status-row">
        <span class="${statusClass(data.status)}" title="${escapeHtml(data.errorMessage || "")}">${statusLabel(data.status)}${progress ? ` ${progress}%` : ""}</span>
        ${stageElapsedLabel(data) ? `<span class="stage-elapsed" ${stageElapsedAttrs(data)} title="${escapeHtml(data.startedAt || "")}">${escapeHtml(stageElapsedLabel(data))}</span>` : ""}
      </div>
      <div class="table-actions-inline">
        ${actionButtons}
      </div>
    </div>
  `;
}

function shotCountWarningFromChapter(item) {
  const warning = item?.shotCountWarning;
  if (!warning?.hasWarning) return "";
  return `Shot输出数量 ${Number(warning.shotCount || 0)} 与插图数 ${Number(warning.sceneCount || 0)} 不一致`;
}

function sceneTimingWarningFromChapter(item) {
  const warning = item?.sceneTimingWarning;
  if (!warning?.hasWarning) return "";
  return sceneTimingWarningTitle(warning.lastEndSeconds, warning.audioDurationSeconds, warning.diffSeconds);
}

function sceneTimingWarningTitle(lastEndSeconds, audioDurationSeconds, diffSeconds) {
  const lastEnd = Number(lastEndSeconds || 0);
  const duration = Number(audioDurationSeconds || 0);
  const diff = Number.isFinite(Number(diffSeconds)) ? Number(diffSeconds) : Math.abs(lastEnd - duration);
  return `Scene最后一项end与音频时长相差 ${formatTimeSeconds(diff)}（end ${formatTimeSeconds(lastEnd)}，音频 ${formatTimeSeconds(duration)}）`;
}

function sceneTimingWarningFromText(raw, audioDurationSeconds) {
  const duration = Number(audioDurationSeconds || 0);
  if (duration <= 0) return "";
  let parsed;
  try {
    parsed = JSON.parse(String(raw || ""));
  } catch {
    return "";
  }
  const grid = parsed && typeof parsed === "object" ? parsed.grid : null;
  if (!Array.isArray(grid) || !grid.length) return "";
  const last = grid[grid.length - 1];
  const lastEnd = parseSceneTimecode(last?.end);
  if (lastEnd == null) return "";
  const diff = Math.abs(lastEnd - duration);
  return diff >= SCENE_AUDIO_DIFF_WARNING_SECONDS ? sceneTimingWarningTitle(lastEnd, duration, diff) : "";
}

function setPayloadTitle(titleEl, text, warningTitle = "") {
  titleEl.textContent = text;
  if (!warningTitle) return;
  const badge = document.createElement("span");
  badge.className = "payload-warning-badge";
  badge.textContent = "!";
  badge.title = warningTitle;
  titleEl.appendChild(document.createTextNode(" "));
  titleEl.appendChild(badge);
}

function renderImagesButton(item, chapterNum, promptWarningTitle = "") {
  const missing = Number(item.images?.missing || 0);
  const unqueued = Number(item.images?.unqueued || 0);
  const expected = Number(item.images?.expected || 0);
  const generated = Number(item.images?.generated || 0);
  const title = unqueued > 0
    ? `未入队列 ${unqueued} 张；插图未生成 ${missing} 张（${generated}/${expected}）`
    : (missing > 0 ? `插图未生成 ${missing} 张（${generated}/${expected}）` : "插图");
  return `
    <button class="ghost-btn btn-sm illustration-images-btn ${missing > 0 ? "has-missing-images" : ""} ${unqueued > 0 ? "has-unqueued-images" : ""} ${promptWarningTitle ? "has-illustration-warning" : ""}" type="button" data-chapter-num="${chapterNum}" title="${escapeHtml(promptWarningTitle || title)}">
      插图
      ${missing > 0 || promptWarningTitle ? '<span class="illustration-alert-dot" aria-hidden="true">!</span>' : ""}
    </button>
  `;
}

function openImageDataModal(item) {
  if (!item) return;
  const title = document.getElementById("illustrationImageDataTitle");
  const content = document.getElementById("illustrationImageDataContent");
  const data = item.promptJson && typeof item.promptJson === "object" && Object.keys(item.promptJson).length ? item.promptJson : item;
  activeImageDataText = JSON.stringify(data, null, 2);
  title.textContent = `插图数据 · #${item.index || ""} ${item.sceneTitle || ""}`.trim();
  content.textContent = activeImageDataText;
  const dialog = document.getElementById("illustrationImageDataDialog");
  if (!dialog.open) dialog.showModal();
}

function openIllustrationVideo(chapterNum, taskId) {
  const dialog = document.getElementById("illustrationVideoDialog");
  const player = document.getElementById("illustrationVideoPlayer");
  const title = document.getElementById("illustrationVideoTitle");
  const meta = document.getElementById("illustrationVideoMeta");
  if (!dialog || !player || !taskId) return;
  const chapter = chapterItems.find((item) => Number(item.chapterNum || 0) === Number(chapterNum || 0));
  title.textContent = `播放视频 · 第${String(chapterNum).padStart(3, "0")}回`;
  meta.textContent = chapter?.title || "";
  player.src = getVideoExportFileUrl(taskId);
  dialog.showModal();
}

function closeIllustrationVideo() {
  const dialog = document.getElementById("illustrationVideoDialog");
  const player = document.getElementById("illustrationVideoPlayer");
  if (player) {
    player.pause();
    player.removeAttribute("src");
    player.load();
  }
  if (dialog?.open) dialog.close();
}

function isBusyStatus(status) {
  return ["running", "processing"].includes(String(status || ""));
}

function confirmSelectedChapters(chapters) {
  return window.confirm(`当前选择${chapters.length}个，是否确定？`);
}

function renderTable() {
  const tbody = document.getElementById("illustrationTableBody");
  if (!activeNovel) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-text">未找到小说</td></tr>';
    updateSelectionUi();
    return;
  }
  if (!chapterItems.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-text">暂无章回数据</td></tr>';
    updateSelectionUi();
    return;
  }
  const available = new Set(chapterItems.map((item) => Number(item.chapterNum || 0)));
  Array.from(selectedChapterNums).forEach((chapterNum) => {
    if (!available.has(chapterNum)) selectedChapterNums.delete(chapterNum);
  });
  tbody.innerHTML = chapterItems.map((item) => {
    const running = ["scene", "shot", "prompt"].some((stage) => isBusyStatus(item.stages?.[stage]?.status));
    return `
    <tr class="illustration-select-row ${selectedChapterNums.has(Number(item.chapterNum || 0)) ? "is-selected" : ""} ${running ? "is-running" : ""}" data-chapter-num="${Number(item.chapterNum || 0)}">
      <td class="select-col"><input class="illustration-row-select" type="checkbox" data-chapter-num="${Number(item.chapterNum || 0)}" ${selectedChapterNums.has(Number(item.chapterNum || 0)) ? "checked" : ""} /></td>
      <td>第 ${String(item.chapterNum || 0).padStart(3, "0")} 回</td>
      <td>${escapeHtml(item.title || "")}</td>
      <td>${formatAudioDurationClock(item.audioDurationSeconds || 0)}</td>
      <td>${Number(item.wordCount || 0).toLocaleString()}</td>
      <td>${Number(item.illustrationCount || 0) || "-"}</td>
      <td>${renderStageCell(item, "scene")}</td>
      <td>${renderStageCell(item, "shot")}</td>
      <td>${renderStageCell(item, "prompt")}</td>
    </tr>
  `;
  }).join("");
  updateSelectionUi();
}

function updateSelectionUi() {
  const count = selectedChapterNums.size;
  const selectAll = document.getElementById("illustrationSelectAll");
  const counter = document.getElementById("illustrationSelectionCount");
  const allCount = chapterItems.length;
  if (counter) counter.textContent = `已选 ${count} 回`;
  if (selectAll) {
    selectAll.checked = allCount > 0 && count === allCount;
    selectAll.indeterminate = count > 0 && count < allCount;
  }
  ["batchIllustrationSceneBtn", "batchIllustrationShotBtn", "batchIllustrationPromptBtn", "batchIllustrationAllStagesBtn", "batchIllustrationImagesBtn"].forEach((id) => {
    const btn = document.getElementById(id);
    if (btn) btn.disabled = count === 0;
  });
}

function setRowSelected(chapterNum, selected) {
  const num = Number(chapterNum || 0);
  if (!num) return;
  if (selected) selectedChapterNums.add(num);
  if (!selected) selectedChapterNums.delete(num);
  const row = document.querySelector(`.illustration-select-row[data-chapter-num="${num}"]`);
  const checkbox = document.querySelector(`.illustration-row-select[data-chapter-num="${num}"]`);
  if (row) row.classList.toggle("is-selected", selected);
  if (checkbox) checkbox.checked = selected;
  updateSelectionUi();
}

async function enqueueSelectedStage(stage) {
  const chapters = Array.from(selectedChapterNums).sort((a, b) => a - b);
  if (!chapters.length) {
    toast("请先选择章回");
    return;
  }
  if (!confirmSelectedChapters(chapters)) return;
  let queued = 0;
  let skipped = 0;
  for (const chapterNum of chapters) {
    try {
      await enqueueChapterIllustration(activeNovel.id, chapterNum, stage);
      queued += 1;
    } catch {
      skipped += 1;
    }
  }
  toast(`${STAGE_LABELS[stage]} 已入队 ${queued} 回${skipped ? `，跳过 ${skipped} 回` : ""}`);
  await refreshPage();
}

async function enqueueSelectedImages() {
  const chapters = Array.from(selectedChapterNums).sort((a, b) => a - b);
  if (!chapters.length) {
    toast("请先选择章回");
    return;
  }
  if (!confirmSelectedChapters(chapters)) return;
  let queued = 0;
  let skipped = 0;
  for (const chapterNum of chapters) {
    try {
      const data = await enqueueAllIllustrationImages(activeNovel.id, chapterNum);
      queued += Number(data.queued || 0);
      skipped += Number(data.skipped || 0);
    } catch {
      skipped += 1;
    }
  }
  toast(`插图已入队 ${queued} 张${skipped ? `，跳过 ${skipped} 项` : ""}`);
  await refreshPage();
}

async function enqueueSelectedAllStages() {
  const chapters = Array.from(selectedChapterNums).sort((a, b) => a - b);
  if (!chapters.length) {
    toast("请先选择章回");
    return;
  }
  if (!confirmSelectedChapters(chapters)) return;
  let queued = 0;
  let skipped = 0;
  for (const chapterNum of chapters) {
    for (const stage of ["scene", "shot", "prompt"]) {
      try {
        await enqueueChapterIllustration(activeNovel.id, chapterNum, stage, { allowWaiting: true });
        queued += 1;
      } catch {
        skipped += 1;
      }
    }
  }
  toast(`Scene+Shot+Prompt 已入队 ${queued} 项${skipped ? `，跳过 ${skipped} 项` : ""}`);
  await refreshPage();
}

function workerStatusText(label, status) {
  const state = String(status?.state || "stopped");
  const mapping = { running: "运行中", stale: "心跳超时", stopped: "未运行" };
  const age = status?.heartbeatAgeSeconds != null ? ` · 心跳${status.heartbeatAgeSeconds}s` : "";
  return `${label}: ${mapping[state] || state}${age}`;
}

function renderWorkerStatus(status) {
  const el = document.getElementById("illustrationWorkerStatus");
  const llmStatus = status?.llm || status;
  const imageStatus = status?.image || { state: "stopped" };
  el.textContent = `${workerStatusText("解析Worker", llmStatus)} · ${workerStatusText("生图Worker", imageStatus)}`;
}

async function refreshWorkerStatus() {
  try {
    const status = await fetchIllustrationWorkerStatus();
    if (status?.llm && status?.image) {
      renderWorkerStatus(status);
      return;
    }
    const [llm, image] = await Promise.all([
      fetchIllustrationLlmWorkerStatus().catch(() => status),
      fetchIllustrationImageWorkerStatus().catch(() => ({ state: "stopped" })),
    ]);
    renderWorkerStatus({ llm, image });
  } catch {
    renderWorkerStatus({ llm: { state: "stopped" }, image: { state: "stopped" } });
  }
}

async function refreshPage() {
  if (!activeNovel) {
    chapterItems = [];
    setHeader();
    renderTable();
    return;
  }
  chapterItems = await fetchNovelIllustrationChapters(activeNovel.id);
  setHeader();
  renderTable();
  await refreshWorkerStatus();
}

async function enqueueStage(chapterNum, stage) {
  await enqueueChapterIllustration(activeNovel.id, chapterNum, stage);
  toast(`第 ${chapterNum} 回 ${STAGE_LABELS[stage]} 已加入队列`);
  await refreshPage();
}

function formatMaybeJson(text) {
  const raw = String(text || "");
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function ratioLabel(width, height) {
  const w = Number(width || 0);
  const h = Number(height || 0);
  if (!w || !h) return "-";
  const gcd = (a, b) => (b ? gcd(b, a % b) : a);
  const d = gcd(w, h);
  return `${Math.round(w / d)}:${Math.round(h / d)}`;
}

function formatTimeSeconds(value) {
  const total = Math.max(0, Math.round(Number(value || 0)));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes ? `${minutes}分${String(seconds).padStart(2, "0")}秒` : `${seconds}秒`;
}

function formatAudioDurationClock(value) {
  const total = Math.max(0, Math.round(Number(value || 0)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function formatSelectedSeconds(value) {
  const total = Math.max(0, Math.round(Number(value || 0)));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `{${minutes}:${String(seconds).padStart(2, "0")}}`;
}

function parseSceneTimecode(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const text = String(value ?? "").trim();
  if (!text) return null;
  if (!text.includes(":")) {
    const seconds = Number(text);
    return Number.isFinite(seconds) ? seconds : null;
  }
  const parts = text.split(":");
  if (parts.length !== 3 || !/^\d+$/.test(parts[0]) || !/^\d+$/.test(parts[1]) || !/^\d+(?:[,.]\d+)?$/.test(parts[2])) return null;
  const hours = Number(parts[0]);
  const minutes = Number(parts[1]);
  const seconds = Number(parts[2].replace(",", "."));
  if (hours < 0 || minutes < 0 || minutes >= 60 || seconds < 0 || seconds >= 60) return null;
  return hours * 3600 + minutes * 60 + seconds;
}

function getSceneListFromParsed(parsed) {
  if (Array.isArray(parsed)) return parsed;
  if (!parsed || typeof parsed !== "object") return [];
  if (Array.isArray(parsed.grid)) return parsed.grid;
  if (Array.isArray(parsed.scenes)) return parsed.scenes;
  if (Array.isArray(parsed.scene)) return parsed.scene;
  return [];
}

function sceneField(item, keys) {
  if (!item || typeof item !== "object") return "";
  for (const key of keys) {
    const value = item[key];
    if (value !== undefined && value !== null && String(value).trim() !== "") return value;
  }
  return "";
}

function formatSceneStatTime(value) {
  const seconds = parseSceneTimecode(value);
  if (seconds == null) return String(value ?? "").trim() || "-";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const wholeSeconds = Math.floor(seconds % 60);
  const milliseconds = Math.round((seconds - Math.floor(seconds)) * 1000);
  const base = `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(wholeSeconds).padStart(2, "0")}`;
  return milliseconds > 0 ? `${base}.${String(milliseconds).padStart(3, "0")}` : base;
}

function formatSceneDuration(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  const rounded = Math.max(0, Math.round(Number(value) * 1000) / 1000);
  const whole = Math.floor(rounded);
  const minutes = Math.floor(whole / 60);
  const seconds = whole % 60;
  const milliseconds = Math.round((rounded - whole) * 1000);
  const suffix = milliseconds > 0 ? `.${String(milliseconds).padStart(3, "0").replace(/0+$/, "")}` : "";
  return `${minutes}分 ${seconds}${suffix}秒`;
}

function sceneStatsFromText(text) {
  const parsed = JSON.parse(String(text || ""));
  return getSceneListFromParsed(parsed).map((item, i) => {
    const index = sceneField(item, ["index", "scene_index", "sceneIndex", "id", "order"]) || i + 1;
    const title = sceneField(item, ["title", "name", "scene_title", "sceneTitle", "summary"]);
    const start = sceneField(item, ["start", "start_time", "startTime", "begin"]);
    const end = sceneField(item, ["end", "end_time", "endTime", "finish"]);
    const startSeconds = parseSceneTimecode(start);
    const endSeconds = parseSceneTimecode(end);
    return {
      index,
      title,
      start,
      end,
      duration: startSeconds == null || endSeconds == null ? null : endSeconds - startSeconds,
    };
  });
}

function renderSceneStatsPanel() {
  if (activePayloadStage !== "scene" || activePayloadKind !== "output") return;
  const editor = document.getElementById("illustrationPayloadContent");
  const summary = document.getElementById("sceneStatsSummary");
  const tbody = document.getElementById("sceneStatsTableBody");
  const text = String(editor?.value || "").trim();
  let stats = [];
  try {
    stats = sceneStatsFromText(text);
  } catch (err) {
    toast(`JSON 格式错误：${err.message}`);
    return;
  }
  const totalDuration = stats.reduce((sum, item) => sum + Math.max(0, Number(item.duration || 0)), 0);
  summary.textContent = `Scene ${stats.length} 项 · 合计时长 ${formatSceneDuration(totalDuration)}`;
  tbody.innerHTML = stats.length ? stats.map((item) => `
    <tr>
      <td>${escapeHtml(item.index)}</td>
      <td>${escapeHtml(item.title || "-")}</td>
      <td>${escapeHtml(formatSceneStatTime(item.start))}</td>
      <td>${escapeHtml(formatSceneStatTime(item.end))}</td>
      <td>${escapeHtml(formatSceneDuration(item.duration))}</td>
    </tr>
  `).join("") : '<tr><td colspan="5" class="empty-text">未找到 Scene 列表</td></tr>';
}

function toggleSceneStatsPanel() {
  const panel = document.getElementById("sceneStatsPanel");
  const btn = document.getElementById("sceneStatsBtn");
  if (!panel) return;
  if (panel.hidden) {
    renderSceneStatsPanel();
    panel.hidden = false;
    if (btn) btn.textContent = "收起Scene统计";
  } else {
    panel.hidden = true;
    if (btn) btn.textContent = "统计Scene";
  }
}

function hidePayloadTimeTooltip() {
  document.getElementById("payloadTimeTooltip")?.remove();
}

function showPayloadTimeTooltip() {
  if (activePayloadKind !== "output") {
    hidePayloadTimeTooltip();
    return;
  }
  const content = document.getElementById("illustrationPayloadContent");
  const dialog = document.getElementById("illustrationPayloadDialog");
  if (content?.tagName === "TEXTAREA") {
    const start = Number(content.selectionStart || 0);
    const end = Number(content.selectionEnd || 0);
    const selectedText = String(content.value || "").slice(start, end).trim().replace(/[，,。；;：:]+$/g, "");
    const selectedSeconds = parseSceneTimecode(selectedText);
    if (selectedSeconds == null || !dialog?.open) {
      hidePayloadTimeTooltip();
      return;
    }
    const rect = content.getBoundingClientRect();
    let tooltip = document.getElementById("payloadTimeTooltip");
    if (!tooltip) {
      tooltip = document.createElement("div");
      tooltip.id = "payloadTimeTooltip";
      tooltip.className = "payload-time-tooltip";
      dialog.appendChild(tooltip);
    }
    tooltip.textContent = formatSelectedSeconds(selectedSeconds);
    tooltip.style.left = `${Math.min(window.innerWidth - 80, Math.max(8, rect.left + rect.width / 2))}px`;
    tooltip.style.top = `${Math.max(8, rect.top - 34)}px`;
    return;
  }
  const selection = window.getSelection();
  if (!content || !dialog?.open || !selection || selection.rangeCount === 0) {
    hidePayloadTimeTooltip();
    return;
  }
  const selectedText = String(selection.toString() || "").trim().replace(/[，,。；;：:]+$/g, "");
  const selectedSeconds = parseSceneTimecode(selectedText);
  if (selectedSeconds == null) {
    hidePayloadTimeTooltip();
    return;
  }
  const range = selection.getRangeAt(0);
  if (!content.contains(range.commonAncestorContainer)) {
    hidePayloadTimeTooltip();
    return;
  }
  const rect = range.getBoundingClientRect();
  if (!rect.width && !rect.height) {
    hidePayloadTimeTooltip();
    return;
  }
  let tooltip = document.getElementById("payloadTimeTooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.id = "payloadTimeTooltip";
    tooltip.className = "payload-time-tooltip";
    dialog.appendChild(tooltip);
  }
  tooltip.textContent = formatSelectedSeconds(selectedSeconds);
  tooltip.style.left = `${Math.min(window.innerWidth - 80, Math.max(8, rect.left + rect.width / 2))}px`;
  tooltip.style.top = `${Math.max(8, rect.top - 34)}px`;
}

function imageTimeLabel(item) {
  const start = Number(item?.start);
  const end = Number(item?.end);
  const duration = Number(item?.duration);
  if (item?.start == null || item?.end == null || !Number.isFinite(start) || !Number.isFinite(end)) return "";
  const safeDuration = Number.isFinite(duration) ? duration : Math.max(0, end - start);
  return `时间：${formatTimeSeconds(start)} - ${formatTimeSeconds(end)} · 持续 ${formatTimeSeconds(safeDuration)}`;
}

async function openPayload(chapterNum, stage, kind) {
  activePayloadChapterNum = Number(chapterNum || 0);
  activePayloadStage = String(stage || "");
  activePayloadKind = kind;
  hidePayloadTimeTooltip();
  const dialog = document.getElementById("illustrationPayloadDialog");
  const title = document.getElementById("illustrationPayloadTitle");
  const content = document.getElementById("illustrationPayloadContent");
  const downloadBtn = document.getElementById("downloadIllustrationPayloadBtn");
  const saveBtn = document.getElementById("saveIllustrationPayloadBtn");
  const sceneStatsBtn = document.getElementById("sceneStatsBtn");
  const shortcutHint = document.getElementById("illustrationPayloadShortcutHint");
  const isEditableSceneOutput = stage === "scene" && kind === "output";
  if (downloadBtn) downloadBtn.hidden = kind !== "output";
  if (saveBtn) saveBtn.hidden = !isEditableSceneOutput;
  if (sceneStatsBtn) sceneStatsBtn.hidden = !isEditableSceneOutput;
  if (sceneStatsBtn) sceneStatsBtn.textContent = "统计Scene";
  const sceneStatsPanel = document.getElementById("sceneStatsPanel");
  if (sceneStatsPanel) sceneStatsPanel.hidden = true;
  if (shortcutHint) shortcutHint.hidden = !isEditableSceneOutput;
  if (content) content.readOnly = !isEditableSceneOutput;
  const chapter = chapterItems.find((item) => Number(item.chapterNum || 0) === Number(chapterNum || 0));
  const duration = Number(chapter?.audioDurationSeconds || 0);
  const durationText = duration > 0 ? ` · 音频时长 ${formatAudioDurationClock(duration)}` : "";
  const titlePrefix = `${STAGE_LABELS[stage]} ${kind === "input" ? "输入" : "输出"} · 第${String(chapterNum).padStart(3, "0")}回${durationText}`;
  const initialWarning = stage === "scene" && kind === "output" ? sceneTimingWarningFromChapter(chapter) : "";
  setPayloadTitle(title, titlePrefix, initialWarning);
  content.value = "加载中...";
  dialog.showModal();
  try {
    const payload = await fetchChapterIllustrationPayload(activeNovel.id, chapterNum, stage, kind);
    const raw = String(payload.text || "");
    const loadedWarning = stage === "scene" && kind === "output" ? sceneTimingWarningFromText(raw, duration) || initialWarning : "";
    setPayloadTitle(title, `${titlePrefix} · 字数 ${raw.length.toLocaleString()}`, loadedWarning);
    content.value = raw ? formatMaybeJson(raw) : "";
  } catch (err) {
    content.value = `加载失败：${err.message}`;
  }
}

async function switchSceneOutput(delta) {
  if (!activePayloadChapterNum || activePayloadStage !== "scene" || activePayloadKind !== "output") return;
  const chapters = chapterItems
    .filter((item) => item.stages?.scene?.status === "completed")
    .map((item) => Number(item.chapterNum || 0))
    .filter(Boolean)
    .sort((a, b) => a - b);
  const currentIndex = chapters.indexOf(Number(activePayloadChapterNum));
  if (currentIndex < 0) return;
  const nextIndex = currentIndex + Number(delta || 0);
  if (nextIndex < 0 || nextIndex >= chapters.length) {
    toast(delta < 0 ? "已经是第一回 Scene 输出" : "已经是最后一回 Scene 输出");
    return;
  }
  await openPayload(chapters[nextIndex], "scene", "output");
}

async function saveSceneOutput() {
  if (!activePayloadChapterNum || activePayloadStage !== "scene" || activePayloadKind !== "output") return;
  const editor = document.getElementById("illustrationPayloadContent");
  const text = String(editor?.value || "");
  try {
    JSON.parse(text);
  } catch (err) {
    toast(`JSON 格式错误：${err.message}`);
    return;
  }
  await saveChapterIllustrationSceneOutput(activeNovel.id, activePayloadChapterNum, text);
  toast("Scene 输出已保存");
  await refreshPage();
}

async function openPromptOutput(chapterNum) {
  activePromptOutputChapterNum = Number(chapterNum || 0);
  const dialog = document.getElementById("promptOutputDialog");
  const title = document.getElementById("promptOutputTitle");
  const editor = document.getElementById("promptOutputEditor");
  const findInput = document.getElementById("promptOutputFindInput");
  const replaceInput = document.getElementById("promptOutputReplaceInput");
  title.textContent = `Prompt 输出 · 第${String(chapterNum).padStart(3, "0")}回`;
  if (findInput) findInput.value = "";
  if (replaceInput) replaceInput.value = "";
  editor.value = "加载中...";
  updatePromptOutputCharCount();
  dialog.showModal();
  try {
    const payload = await fetchChapterIllustrationPayload(activeNovel.id, chapterNum, "prompt", "output");
    const raw = String(payload.resultJsonText || payload.text || "");
    editor.value = raw ? formatMaybeJson(raw) : "";
  } catch (err) {
    editor.value = `加载失败：${err.message}`;
  }
  updatePromptOutputCharCount();
}

function updatePromptOutputCharCount() {
  const editor = document.getElementById("promptOutputEditor");
  const count = document.getElementById("promptOutputCharCount");
  if (count) count.textContent = String(editor?.value.length || 0);
}

async function switchPromptOutput(delta) {
  if (!activePromptOutputChapterNum) return;
  const chapters = chapterItems
    .filter((item) => item.stages?.prompt?.status === "completed")
    .map((item) => Number(item.chapterNum || 0))
    .filter(Boolean)
    .sort((a, b) => a - b);
  const currentIndex = chapters.indexOf(Number(activePromptOutputChapterNum));
  if (currentIndex < 0) return;
  const nextIndex = currentIndex + Number(delta || 0);
  if (nextIndex < 0 || nextIndex >= chapters.length) {
    toast(delta < 0 ? "已经是第一回 Prompt 输出" : "已经是最后一回 Prompt 输出");
    return;
  }
  await openPromptOutput(chapters[nextIndex]);
}

async function savePromptOutput() {
  if (!activePromptOutputChapterNum) return;
  const editor = document.getElementById("promptOutputEditor");
  const text = String(editor.value || "");
  try {
    JSON.parse(text);
  } catch (err) {
    toast(`JSON 格式错误：${err.message}`);
    return;
  }
  await saveChapterIllustrationPromptOutput(activeNovel.id, activePromptOutputChapterNum, text);
  toast("Prompt 输出已保存，插图列表已同步");
  await refreshPage();
}

function downloadPromptOutput() {
  if (!activePromptOutputChapterNum) return;
  const editor = document.getElementById("promptOutputEditor");
  const text = String(editor?.value || "").trim();
  if (!text || text === "加载中...") {
    toast("暂无可下载内容");
    return;
  }
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    toast(`JSON 格式错误：${err.message}`);
    return;
  }
  const chapter = chapterItems.find((item) => Number(item.chapterNum || 0) === Number(activePromptOutputChapterNum));
  const title = String(chapter?.title || `第${String(activePromptOutputChapterNum).padStart(3, "0")}回`).trim();
  const filename = `${safeDownloadFilename(title)}-prompt-output.json`;
  downloadTextFile(filename, `${JSON.stringify(parsed, null, 2)}\n`);
  toast("Prompt 输出已下载");
}

function promptOutputItems(data) {
  if (Array.isArray(data)) return data;
  if (Array.isArray(data?.prompts)) return data.prompts;
  if (Array.isArray(data?.grid)) return data.grid;
  if (Array.isArray(data?.items)) return data.items;
  return [];
}

function clearPromptOutputNegative() {
  const editor = document.getElementById("promptOutputEditor");
  const text = String(editor?.value || "").trim();
  if (!editor || !text || text === "加载中...") {
    toast("暂无可清空内容");
    return;
  }
  let data;
  try {
    data = JSON.parse(text);
  } catch (err) {
    toast(`JSON 格式错误：${err.message}`);
    return;
  }
  const items = promptOutputItems(data);
  let count = 0;
  items.forEach((item) => {
    if (item && typeof item === "object" && Object.prototype.hasOwnProperty.call(item, "negative") && item.negative !== "") {
      item.negative = "";
      count += 1;
    }
  });
  editor.value = JSON.stringify(data, null, 2);
  updatePromptOutputCharCount();
  editor.focus();
  toast(`已清空 ${count} 项 negative`);
}

function downloadIllustrationPayloadOutput() {
  if (!activePayloadChapterNum || activePayloadKind !== "output") return;
  const content = document.getElementById("illustrationPayloadContent");
  const text = String(content?.value || "").trim();
  if (!text || text === "加载中...") {
    toast("暂无可下载内容");
    return;
  }
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    toast(`JSON 格式错误：${err.message}`);
    return;
  }
  const chapter = chapterItems.find((item) => Number(item.chapterNum || 0) === Number(activePayloadChapterNum));
  const title = String(chapter?.title || `第${String(activePayloadChapterNum).padStart(3, "0")}回`).trim();
  const stage = String(activePayloadStage || "payload").toLowerCase();
  const filename = `${safeDownloadFilename(title)}-${stage}-output.json`;
  downloadTextFile(filename, `${JSON.stringify(parsed, null, 2)}\n`);
  toast(`${STAGE_LABELS[activePayloadStage] || "内容"} 输出已下载`);
}

function findPromptOutputText() {
  const editor = document.getElementById("promptOutputEditor");
  const input = document.getElementById("promptOutputFindInput");
  const query = String(input?.value || "");
  if (!editor || !query) {
    toast("请输入查找内容");
    return false;
  }
  const text = String(editor.value || "");
  const start = Math.max(editor.selectionEnd || 0, 0);
  let index = text.indexOf(query, start);
  if (index < 0 && start > 0) index = text.indexOf(query, 0);
  if (index < 0) {
    toast("未找到匹配内容");
    return false;
  }
  editor.focus();
  editor.setSelectionRange(index, index + query.length);
  return true;
}

function replacePromptOutputText() {
  const editor = document.getElementById("promptOutputEditor");
  const findInput = document.getElementById("promptOutputFindInput");
  const replaceInput = document.getElementById("promptOutputReplaceInput");
  const query = String(findInput?.value || "");
  const replacement = String(replaceInput?.value || "");
  if (!editor || !query) {
    toast("请输入查找内容");
    return;
  }
  const selected = String(editor.value || "").slice(editor.selectionStart || 0, editor.selectionEnd || 0);
  if (selected !== query && !findPromptOutputText()) return;
  const start = editor.selectionStart || 0;
  const end = editor.selectionEnd || 0;
  editor.setRangeText(replacement, start, end, "end");
  updatePromptOutputCharCount();
  editor.focus();
}

function replaceAllPromptOutputText() {
  const editor = document.getElementById("promptOutputEditor");
  const findInput = document.getElementById("promptOutputFindInput");
  const replaceInput = document.getElementById("promptOutputReplaceInput");
  const query = String(findInput?.value || "");
  const replacement = String(replaceInput?.value || "");
  if (!editor || !query) {
    toast("请输入查找内容");
    return;
  }
  const text = String(editor.value || "");
  const parts = text.split(query);
  const count = parts.length - 1;
  if (!count) {
    toast("未找到匹配内容");
    return;
  }
  editor.value = parts.join(replacement);
  updatePromptOutputCharCount();
  editor.focus();
  toast(`已替换 ${count} 处`);
}

async function openLlmParams(chapterNum, stage) {
  const dialog = document.getElementById("illustrationLlmParamsDialog");
  const title = document.getElementById("illustrationLlmParamsTitle");
  const content = document.getElementById("illustrationLlmParamsContent");
  title.textContent = `${STAGE_LABELS[stage]} LLM参数 · 第${String(chapterNum).padStart(3, "0")}回`;
  content.textContent = "加载中...";
  dialog.showModal();
  try {
    const params = await fetchChapterIllustrationLlmParams(activeNovel.id, chapterNum, stage);
    content.textContent = JSON.stringify(params, null, 2);
  } catch (err) {
    content.textContent = `加载失败：${err.message}`;
  }
}

function promptBatchElapsed(batch) {
  const startedAt = parseDateTime(batch?.startedAt);
  if (!startedAt) return "";
  const status = String(batch?.status || "");
  const updatedAt = ["pending", "running", "processing"].includes(status) ? new Date() : parseDateTime(batch?.updatedAt) || new Date();
  return formatElapsedSeconds((updatedAt.getTime() - startedAt.getTime()) / 1000);
}

function promptBatchMetaText(batch) {
  if (!batch) return "-";
  return `状态 ${statusLabel(batch.status)}${batch.progress ? ` ${batch.progress}%` : ""} · 范围 #${batch.startIndex}-${batch.endIndex}${promptBatchElapsed(batch) ? ` · 耗时 ${promptBatchElapsed(batch)}` : ""}${batch.errorMessage ? ` · ${batch.errorMessage}` : ""}`;
}

function updatePromptBatchLiveElapsed() {
  if (!document.getElementById("promptBatchDialog")?.open) return;
  const batch = activePromptBatches[activePromptBatchIndex];
  if (!batch || !["pending", "running", "processing"].includes(String(batch.status || ""))) return;
  const meta = document.getElementById("promptBatchMeta");
  if (meta) meta.textContent = promptBatchMetaText(batch);
}

function promptBatchContent(batch) {
  if (!batch) return "暂无批次";
  if (activePromptBatchTab === "input") return batch.inputText || "暂无输入";
  if (activePromptBatchTab === "output") return batch.resultJsonText || batch.outputText || batch.errorMessage || "暂无输出";
  return batch.llmParamsText || "暂无 LLM 参数";
}

function renderPromptBatchModal() {
  const list = document.getElementById("promptBatchList");
  const content = document.getElementById("promptBatchContent");
  const meta = document.getElementById("promptBatchMeta");
  const retryBtn = document.getElementById("retryPromptBatchBtn");
  const cancelBtn = document.getElementById("cancelPromptBatchBtn");
  const stageLabel = STAGE_LABELS[activePromptBatchStage] || "Prompt";
  list.innerHTML = activePromptBatches.length ? activePromptBatches.map((batch, idx) => `
    <button class="prompt-batch-item ${idx === activePromptBatchIndex ? "active" : ""}" type="button" data-batch-index="${idx}">
      <strong>第 ${batch.batchIndex} 批</strong>
      <span>#${batch.startIndex}-${batch.endIndex} · ${statusLabel(batch.status)}${batch.progress ? ` ${batch.progress}%` : ""}</span>
    </button>
  `).join("") : `<p class="empty-text">暂无批次数据，开始解析 ${stageLabel} 后生成。</p>`;
  const batch = activePromptBatches[activePromptBatchIndex] || null;
  const batchBusy = Boolean(batch && ["pending", "running", "processing"].includes(String(batch.status || "")));
  meta.textContent = promptBatchMetaText(batch);
  retryBtn.hidden = batchBusy;
  retryBtn.disabled = !batch || batchBusy;
  if (cancelBtn) {
    cancelBtn.hidden = !batchBusy;
    cancelBtn.disabled = !batchBusy;
  }
  content.textContent = promptBatchContent(batch);
  document.querySelectorAll(".prompt-batch-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === activePromptBatchTab);
  });
}

async function refreshPromptBatchesModal() {
  if (!activePromptBatchesChapterNum) return;
  if (!document.getElementById("promptBatchDialog")?.open) return;
  const data = await fetchChapterIllustrationPromptBatches(activeNovel.id, activePromptBatchesChapterNum, activePromptBatchStage);
  activePromptBatches = data.batches || [];
  if (activePromptBatchIndex >= activePromptBatches.length) activePromptBatchIndex = Math.max(0, activePromptBatches.length - 1);
  renderPromptBatchModal();
}

function stopPromptBatchesAutoRefresh() {
  if (promptBatchesRefreshTimer) {
    window.clearInterval(promptBatchesRefreshTimer);
    promptBatchesRefreshTimer = 0;
  }
}

function startPromptBatchesAutoRefresh() {
  stopPromptBatchesAutoRefresh();
  promptBatchesRefreshTimer = window.setInterval(() => refreshPromptBatchesModal().catch(() => {}), 3000);
}

async function openPromptBatches(chapterNum, stage = "prompt") {
  activePromptBatchesChapterNum = Number(chapterNum || 0);
  activePromptBatchStage = String(stage || "prompt") === "shot" ? "shot" : "prompt";
  activePromptBatchIndex = 0;
  activePromptBatchTab = "llm";
  document.getElementById("promptBatchTitle").textContent = `${STAGE_LABELS[activePromptBatchStage]} 详情 · 第${String(chapterNum).padStart(3, "0")}回`;
  document.getElementById("promptBatchContent").textContent = "加载中...";
  document.getElementById("promptBatchDialog").showModal();
  await refreshPromptBatchesModal();
  startPromptBatchesAutoRefresh();
}

function renderImages(items) {
  currentImagesTotal = items.length;
  currentImageItems = items;
  currentPreviewItems = items.filter((item) => Boolean(item.imageUrl));
  const summary = document.getElementById("illustrationImagesQueueSummary");
  if (summary) {
    const generated = currentPreviewItems.length;
    const queued = items.filter((item) => String(item.status || "idle") !== "idle" && !item.imageUrl).length;
    const unqueued = items.filter((item) => String(item.status || "idle") === "idle").length;
    summary.textContent = `已生成: ${generated}　待生成：${queued}　未入队列：${unqueued}`;
  }
  const root = document.getElementById("illustrationImagesList");
  if (!items.length) {
    root.innerHTML = '<p class="empty-text">暂无 prompt.json 插图数据</p>';
    return;
  }
  root.innerHTML = items.map((item) => {
    const hasImage = Boolean(item.imageUrl);
    const busy = ["pending", "running", "processing"].includes(String(item.status || ""));
    const missingKeys = missingPromptItemKeys(item.promptJson);
    return `
      <article class="illustration-image-card ${missingKeys.length ? "has-json-key-warning" : ""}" data-image-id="${item.id}" data-image-index="${item.index}">
        <div class="illustration-image-slot ${hasImage ? "has-image" : ""}" data-image-url="${hasImage ? item.imageUrl : ""}">
          ${hasImage ? `<img src="${item.imageUrl}?v=${Date.now()}" alt="${escapeHtml(item.sceneTitle || "插图")}" data-image-id="${item.id}" />` : '<span>图片位置</span>'}
        </div>
        <div class="queue-head">
          <h4>#${item.index} ${escapeHtml(item.sceneTitle || "未命名场景")}</h4>
          <span class="${statusClass(item.status)}" title="${escapeHtml(item.errorMessage || "")}">${statusLabel(item.status)}${item.progress ? ` ${item.progress}%` : ""}</span>
        </div>
        <p class="meta illustration-size-meta" data-size-id="${item.id}">尺寸：${escapeHtml(item.suggestedSize || "待读取")} · 比例：-</p>
        ${imageTimeLabel(item) ? `<p class="meta illustration-time-meta">${escapeHtml(imageTimeLabel(item))}</p>` : ""}
        <p class="meta">人物：${escapeHtml(item.characterNames || "")}</p>
        ${missingKeys.length ? `<p class="meta illustration-json-card-warning">缺少key：${escapeHtml(missingKeys.join(", "))}</p>` : ""}
        <p class="meta illustration-summary-meta">${escapeHtml(item.cnSummary || "-")}</p>
        <div class="card-actions">
          <button class="ghost-btn btn-sm illustration-generate-image-btn" type="button" data-image-id="${item.id}" ${busy ? "disabled" : ""}>${hasImage ? "重新生成" : "生成"}</button>
          <button class="ghost-btn btn-sm illustration-image-data-btn" type="button" data-image-id="${item.id}">查看数据</button>
        </div>
      </article>
    `;
  }).join("");
  root.querySelectorAll("img[data-image-id]").forEach((img) => {
    img.addEventListener("load", () => {
      const meta = root.querySelector(`.illustration-size-meta[data-size-id="${img.dataset.imageId}"]`);
      if (meta) meta.textContent = `尺寸：${img.naturalWidth}x${img.naturalHeight} · 比例：${ratioLabel(img.naturalWidth, img.naturalHeight)}`;
    }, { once: true });
  });
  updateImagesJsonWarning();
}

function previewJsonKey(item) {
  return `${Number(item?.id || 0)}:${Number(item?.index || 0)}`;
}

function setPreviewJsonEditorValue(item) {
  const jsonEditor = document.getElementById("illustrationPreviewJsonEditor");
  if (!jsonEditor) return;
  const json = item?.promptJson && typeof item.promptJson === "object" && Object.keys(item.promptJson).length ? item.promptJson : null;
  const text = json ? JSON.stringify(json, null, 2) : "";
  jsonEditor.value = text;
  jsonEditor.placeholder = json ? "" : "未找到当前图片对应的原始 prompt.json 项";
  previewJsonDirty = false;
  previewJsonImageKey = previewJsonKey(item);
  previewJsonOriginalText = text;
  updatePreviewJsonCount();
  updatePreviewJsonWarning();
  updateImagesJsonWarning();
}

function markPreviewJsonDirty() {
  const editor = document.getElementById("illustrationPreviewJsonEditor");
  previewJsonDirty = String(editor?.value || "") !== previewJsonOriginalText;
  updatePreviewJsonCount();
  updatePreviewJsonWarning();
  updateImagesJsonWarning();
}

function openPreviewAt(index, options = {}) {
  if (!currentPreviewItems.length) return;
  const total = currentPreviewItems.length;
  currentPreviewIndex = ((Number(index) || 0) + total) % total;
  const item = currentPreviewItems[currentPreviewIndex];
  const img = document.getElementById("illustrationImagePreview");
  const dialog = document.getElementById("illustrationImagePreviewDialog");
  const prevBtn = document.getElementById("illustrationPreviewPrevBtn");
  const nextBtn = document.getElementById("illustrationPreviewNextBtn");
  const footerMeta = document.getElementById("illustrationPreviewFooterMeta");
  const regenerateBtn = document.getElementById("illustrationPreviewRegenerateBtn");
  const restoreBtn = document.getElementById("illustrationPreviewRestoreJsonBtn");
  const optimizeBtn = document.getElementById("illustrationPreviewOptimizeJsonBtn");
  const jsonEditor = document.getElementById("illustrationPreviewJsonEditor");
  const positionText = `${currentPreviewIndex + 1}/${total}`;
  const timeText = imageTimeLabel(item);
  const imageId = String(item.id || "");
  const imageUpdatedAt = String(item.updatedAt || "");
  const imageUrl = String(item.imageUrl || "");
  const busy = ["pending", "running", "processing"].includes(String(item.status || ""));
  prevBtn.disabled = total <= 1;
  nextBtn.disabled = total <= 1;
  regenerateBtn.dataset.imageId = imageId;
  regenerateBtn.disabled = busy || !item.id;
  regenerateBtn.textContent = busy ? `${statusLabel(item.status)}中` : "重新生成";
  if (optimizeBtn) {
    optimizeBtn.dataset.imageId = imageId;
    optimizeBtn.disabled = !item.id;
  }
  if (restoreBtn) {
    restoreBtn.dataset.imageId = imageId;
    restoreBtn.hidden = !item.hasOriginalPromptBackup;
    restoreBtn.disabled = !item.id;
  }
  document.getElementById("illustrationPreviewTitle").textContent = `#${item.index} ${item.sceneTitle || "预览插图"}`;
  document.getElementById("illustrationPreviewTotalCount").textContent = `插画共 ${currentImagesTotal} 个`;
  document.getElementById("illustrationPreviewSummary").textContent = item.cnSummary || "";
  document.getElementById("illustrationPreviewCharacters").textContent = item.characterNames ? `人物：${item.characterNames}` : "";
  document.getElementById("illustrationPreviewPrompt").textContent = item.promptText ? `提示词：${item.promptText}` : "";
  if (jsonEditor) {
    const samePreviewJson = previewJsonImageKey === previewJsonKey(item);
    if (!options.preserveDirtyJson || !previewJsonDirty || !samePreviewJson) {
      setPreviewJsonEditorValue(item);
    } else {
      updatePreviewJsonCount();
    }
  }
  footerMeta.textContent = [timeText, item.suggestedSize ? `建议尺寸：${item.suggestedSize}` : "", positionText].filter(Boolean).join(" · ");
  img.onload = () => {
    footerMeta.textContent = [timeText, `尺寸：${img.naturalWidth}x${img.naturalHeight}`, `比例：${ratioLabel(img.naturalWidth, img.naturalHeight)}`, positionText].filter(Boolean).join(" · ");
  };
  if (img.dataset.imageId !== imageId || img.dataset.imageUpdatedAt !== imageUpdatedAt || img.dataset.imageUrl !== imageUrl) {
    img.dataset.imageId = imageId;
    img.dataset.imageUpdatedAt = imageUpdatedAt;
    img.dataset.imageUrl = imageUrl;
    img.src = `${imageUrl}?v=${Date.now()}`;
  } else if (img.complete && img.naturalWidth) {
    footerMeta.textContent = [timeText, `尺寸：${img.naturalWidth}x${img.naturalHeight}`, `比例：${ratioLabel(img.naturalWidth, img.naturalHeight)}`, positionText].filter(Boolean).join(" · ");
  }
  if (!dialog.open) dialog.showModal();
}

function updatePreviewJsonCount() {
  const editor = document.getElementById("illustrationPreviewJsonEditor");
  const count = document.getElementById("illustrationPreviewJsonCount");
  if (count) count.textContent = String(editor?.value.length || 0);
}

function parsePreviewJsonEditorObject() {
  const editor = document.getElementById("illustrationPreviewJsonEditor");
  const text = String(editor?.value || "").trim();
  if (!text) return { value: null, error: "JSON内容为空" };
  try {
    const value = parseJsonObjectText(text);
    return { value, error: "" };
  } catch (err) {
    return { value: null, error: err.message || String(err) };
  }
}

function currentItemMissingPromptKeys(item) {
  if (!item) return REQUIRED_PROMPT_ITEM_KEYS;
  if (previewJsonDirty && previewJsonImageKey === previewJsonKey(item)) {
    const parsed = parsePreviewJsonEditorObject();
    return parsed.value ? missingPromptItemKeys(parsed.value) : REQUIRED_PROMPT_ITEM_KEYS;
  }
  return missingPromptItemKeys(item.promptJson);
}

function updatePreviewJsonWarning() {
  const warning = document.getElementById("illustrationPreviewJsonWarning");
  if (!warning) return;
  const parsed = parsePreviewJsonEditorObject();
  if (parsed.error) {
    warning.textContent = `JSON错误：${parsed.error}`;
    warning.hidden = false;
    return;
  }
  const missingKeys = missingPromptItemKeys(parsed.value);
  warning.textContent = missingKeys.length ? `缺少key：${missingKeys.join("、")}` : "";
  warning.hidden = !missingKeys.length;
}

function updateImagesJsonWarning() {
  const warning = document.getElementById("illustrationImagesJsonWarning");
  const root = document.getElementById("illustrationImagesList");
  if (!warning || !root) return;
  const invalidItems = currentImageItems.filter((item) => currentItemMissingPromptKeys(item).length);
  root.querySelectorAll(".illustration-image-card").forEach((card) => {
    const item = currentImageItems.find((entry) => String(entry.id) === String(card.dataset.imageId || ""));
    const missingKeys = currentItemMissingPromptKeys(item);
    card.classList.toggle("has-json-key-warning", Boolean(missingKeys.length));
    let warningEl = card.querySelector(".illustration-json-card-warning");
    if (missingKeys.length && !warningEl) {
      warningEl = document.createElement("p");
      warningEl.className = "meta illustration-json-card-warning";
      card.querySelector(".illustration-summary-meta")?.before(warningEl);
    }
    if (warningEl) {
      if (missingKeys.length) {
        warningEl.textContent = `缺少key：${missingKeys.join(", ")}`;
      } else {
        warningEl.remove();
      }
    }
  });
  if (!invalidItems.length) {
    warning.hidden = true;
    warning.innerHTML = "";
    return;
  }
  warning.hidden = false;
  warning.innerHTML = `异常：${invalidItems.map((item) => `<button class="illustration-json-warning-link" type="button" data-image-id="${item.id}">【${escapeHtml(item.index)}】</button>`).join("")}`;
}

function switchPreview(delta) {
  const dialog = document.getElementById("illustrationImagePreviewDialog");
  if (!dialog?.open || !currentPreviewItems.length) return;
  openPreviewAt(currentPreviewIndex + delta);
}

async function regeneratePreviewImage() {
  const btn = document.getElementById("illustrationPreviewRegenerateBtn");
  const imageId = Number(btn?.dataset.imageId || 0);
  if (!imageId) return;
  btn.disabled = true;
  try {
    await enqueueIllustrationImage(imageId);
    toast("插图已加入生成队列");
    await refreshImagesModal();
    const index = currentPreviewItems.findIndex((item) => Number(item.id) === imageId);
    if (index >= 0) openPreviewAt(index);
  } catch (err) {
    btn.disabled = false;
    toast(`加入队列失败：${err.message}`);
  }
}

function currentPreviewJson() {
  const item = currentPreviewItems[currentPreviewIndex];
  if (!item) return null;
  if (item.promptJson && typeof item.promptJson === "object" && Object.keys(item.promptJson).length) {
    return item.promptJson;
  }
  return null;
}

function copyPreviewJson() {
  const data = currentPreviewJson();
  if (!data) {
    toast("未找到原始完整JSON，请刷新后重试");
    return;
  }
  copyText(JSON.stringify(data, null, 2)).catch((err) => {
    toast(`复制失败：${err.message}`);
  });
}

function missingPromptItemKeys(item) {
  if (!item || typeof item !== "object" || Array.isArray(item)) return REQUIRED_PROMPT_ITEM_KEYS;
  return REQUIRED_PROMPT_ITEM_KEYS.filter((key) => !Object.prototype.hasOwnProperty.call(item, key));
}

function optimizeDetailContent() {
  if (optimizeDetailTab === "input") return optimizeDetail.inputText || "暂无输入";
  if (optimizeDetailTab === "output") return formatJsonIfPossible(optimizeDetail.outputText) || (optimizeDetail.error ? `优化失败：${optimizeDetail.error}` : "优化中...");
  return optimizeDetail.requestPreview ? JSON.stringify(optimizeDetail.requestPreview, null, 2) : "等待 LLM 参数...";
}

function renderOptimizeDetail() {
  const meta = document.getElementById("illustrationOptimizeMeta");
  const content = document.getElementById("illustrationOptimizeContent");
  const applyBtn = document.getElementById("illustrationOptimizeApplyBtn");
  if (meta) {
    const label = optimizeDetail.status === "running" ? "优化中" : optimizeDetail.status === "completed" ? "已完成" : optimizeDetail.status === "failed" ? "失败" : "等待优化";
    meta.textContent = optimizeDetail.error ? `${label} · ${optimizeDetail.error}` : label;
  }
  if (content) content.textContent = optimizeDetailContent();
  document.querySelectorAll(".illustration-optimize-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === optimizeDetailTab);
  });
  if (applyBtn) {
    applyBtn.disabled = !optimizeDetail.jsonText || optimizeDetail.status !== "completed";
  }
}

function openOptimizeDetail(title, inputText) {
  optimizeDetailTab = "output";
  optimizeDetail = { requestPreview: null, inputText, outputText: "", jsonText: "", status: "running", error: "" };
  document.getElementById("illustrationOptimizeTitle").textContent = title || "优化提示词";
  renderOptimizeDetail();
  const dialog = document.getElementById("illustrationOptimizeDialog");
  if (!dialog.open) dialog.showModal();
}

async function savePreviewJson(jsonText = null, options = {}) {
  if (jsonText && typeof jsonText === "object" && typeof jsonText.preventDefault === "function") jsonText = null;
  const item = currentPreviewItems[currentPreviewIndex];
  const editor = document.getElementById("illustrationPreviewJsonEditor");
  const btn = document.getElementById("illustrationPreviewSaveJsonBtn");
  if (!activeNovel || !activeImagesChapterNum || !item) return;
  const text = String(jsonText ?? (editor?.value || "")).trim();
  if (!text) {
    toast("JSON内容为空");
    return;
  }
  let parsed;
  try {
    parsed = parseJsonObjectText(text);
  } catch (err) {
    toast(`JSON 格式错误：${err.message}`);
    return;
  }
  const missingKeys = missingPromptItemKeys(parsed);
  if (missingKeys.length) {
    toast(`缺少必需字段：${missingKeys.join(", ")}`);
    return;
  }
  if (btn) {
    btn.disabled = true;
    btn.textContent = "保存中...";
  }
  try {
    const result = await saveChapterIllustrationPromptItem(activeNovel.id, activeImagesChapterNum, Number(item.index || 0), JSON.stringify(parsed, null, 2));
    if (!options.quietSuccess) toast("JSON已保存");
    await refreshImagesModal();
    const updatedId = Number(result.item?.id || item.id || 0);
    const nextIndex = currentPreviewItems.findIndex((entry) => Number(entry.id || 0) === updatedId) >= 0
      ? currentPreviewItems.findIndex((entry) => Number(entry.id || 0) === updatedId)
      : currentPreviewItems.findIndex((entry) => Number(entry.index || 0) === Number(item.index || 0));
    previewJsonDirty = false;
    if (nextIndex >= 0) openPreviewAt(nextIndex);
  } catch (err) {
    toast(`保存失败：${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "保存JSON";
    }
  }
}

async function optimizePreviewJson() {
  const item = currentPreviewItems[currentPreviewIndex];
  const editor = document.getElementById("illustrationPreviewJsonEditor");
  const btn = document.getElementById("illustrationPreviewOptimizeJsonBtn");
  const restoreBtn = document.getElementById("illustrationPreviewRestoreJsonBtn");
  if (!item?.id || !editor) return;
  const text = String(editor.value || "").trim();
  if (!text) {
    toast("JSON内容为空");
    return;
  }
  try {
    JSON.parse(text);
  } catch (err) {
    toast(`JSON 格式错误：${err.message}`);
    return;
  }
  if (btn) {
    btn.disabled = true;
    btn.textContent = "优化中...";
  }
  openOptimizeDetail(`优化提示词 · #${item.index || ""} ${item.sceneTitle || ""}`.trim(), text);
  try {
    const prepared = await prepareIllustrationPromptItemOptimization(item.id, text);
    optimizeDetail = {
      requestPreview: prepared.requestPreview || null,
      inputText: prepared.inputText || text,
      outputText: "",
      jsonText: "",
      status: "running",
      error: "",
    };
    renderOptimizeDetail();
    const result = await optimizeIllustrationPromptItem(item.id, text);
    optimizeDetail = {
      requestPreview: result.requestPreview || prepared.requestPreview || null,
      inputText: result.inputText || prepared.inputText || text,
      outputText: result.outputText || result.jsonText || "",
      jsonText: result.jsonText || "",
      status: "completed",
      error: "",
    };
    renderOptimizeDetail();
    editor.value = String(result.jsonText || "");
    const key = previewJsonKey(item);
    const target = currentPreviewItems.find((entry) => previewJsonKey(entry) === key);
    if (target) target.hasOriginalPromptBackup = true;
    if (restoreBtn) restoreBtn.hidden = false;
    previewJsonDirty = true;
    updatePreviewJsonCount();
    updatePreviewJsonWarning();
    updateImagesJsonWarning();
    editor.focus();
    toast("提示词已优化，请检查后保存JSON");
  } catch (err) {
    const data = err.data || {};
    optimizeDetail = {
      requestPreview: data.requestPreview || optimizeDetail.requestPreview || null,
      inputText: data.inputText || optimizeDetail.inputText || text,
      outputText: data.outputText || optimizeDetail.outputText || `优化失败：${err.message || String(err)}`,
      jsonText: data.jsonText || "",
      status: "failed",
      error: err.message || String(err),
    };
    if (data.outputText) optimizeDetailTab = "output";
    renderOptimizeDetail();
    toast(`优化失败：${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "优化提示词";
    }
  }
}

async function restorePreviewJson() {
  const item = currentPreviewItems[currentPreviewIndex];
  const editor = document.getElementById("illustrationPreviewJsonEditor");
  const btn = document.getElementById("illustrationPreviewRestoreJsonBtn");
  if (!item?.id || !editor) return;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "还原中...";
  }
  try {
    const result = await fetchIllustrationPromptItemOriginal(item.id);
    editor.value = String(result.jsonText || "");
    previewJsonDirty = true;
    updatePreviewJsonCount();
    updatePreviewJsonWarning();
    updateImagesJsonWarning();
    editor.focus();
    toast("已还原到原始提示词，请检查后保存JSON");
  } catch (err) {
    toast(`还原失败：${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "还原";
    }
  }
}

async function applyOptimizedPreviewJson() {
  const item = currentPreviewItems[currentPreviewIndex];
  const editor = document.getElementById("illustrationPreviewJsonEditor");
  const btn = document.getElementById("illustrationOptimizeApplyBtn");
  const text = String(optimizeDetail.jsonText || optimizeDetail.outputText || "").trim();
  if (!text) {
    toast("暂无可应用的优化输出");
    return;
  }
  let parsed;
  try {
    parsed = parseJsonObjectText(text);
  } catch (err) {
    toast(`优化输出不是有效 JSON：${err.message}`);
    return;
  }
  const missingKeys = missingPromptItemKeys(parsed);
  if (missingKeys.length) {
    toast(`优化输出缺少必需字段：${missingKeys.join(", ")}`);
    return;
  }
  const normalized = JSON.stringify(parsed, null, 2);
  if (editor) editor.value = normalized;
  previewJsonDirty = true;
  updatePreviewJsonCount();
  updatePreviewJsonWarning();
  updateImagesJsonWarning();
  if (btn) {
    btn.disabled = true;
    btn.textContent = "应用并重新生图中...";
  }
  try {
    await savePreviewJson(normalized, { quietSuccess: true });
    const imageId = Number(item?.id || 0);
    if (imageId) await enqueueIllustrationImage(imageId);
    const dialog = document.getElementById("illustrationOptimizeDialog");
    if (dialog?.open) dialog.close();
    toast("优化提示词已应用并重新生图");
    await refreshImagesModal().catch(() => {});
  } finally {
    if (btn) {
      btn.textContent = "应用&重新生图";
      renderOptimizeDetail();
    }
  }
}

async function refreshImagesModal() {
  if (!document.getElementById("illustrationImagesDialog")?.open) return;
  if (!activeImagesChapterNum) return;
  const previewDialog = document.getElementById("illustrationImagePreviewDialog");
  const previewImageId = previewDialog?.open ? Number(currentPreviewItems[currentPreviewIndex]?.id || 0) : 0;
  const previewItemIndex = previewDialog?.open ? Number(currentPreviewItems[currentPreviewIndex]?.index || 0) : 0;
  renderImages(await fetchChapterIllustrationImages(activeNovel.id, activeImagesChapterNum));
  if (previewImageId) {
    const index = currentPreviewItems.findIndex((item) => Number(item.id) === previewImageId);
    if (index >= 0) openPreviewAt(index, { preserveDirtyJson: true });
  } else if (previewItemIndex) {
    const index = currentPreviewItems.findIndex((item) => Number(item.index) === previewItemIndex);
    if (index >= 0) openPreviewAt(index, { preserveDirtyJson: true });
  }
  await refreshPage();
}

async function refreshPreviewImageData() {
  const btn = document.getElementById("illustrationPreviewRefreshBtn");
  if (!activeNovel || !activeImagesChapterNum) return;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "刷新中...";
  }
  try {
    await refreshImagesModal();
    toast("预览已刷新");
  } catch (err) {
    toast(`刷新失败：${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "刷新";
    }
  }
}

function stopImagesAutoRefresh() {
  if (imagesRefreshTimer) {
    window.clearInterval(imagesRefreshTimer);
    imagesRefreshTimer = 0;
  }
}

function applyImagesAutoRefresh() {
  stopImagesAutoRefresh();
  const select = document.getElementById("illustrationImagesRefreshInterval");
  const seconds = Number(select?.value || 0);
  localStorage.setItem(IMAGES_REFRESH_KEY, String(seconds));
  if (seconds > 0) {
    imagesRefreshTimer = window.setInterval(() => refreshImagesModal().catch(() => {}), seconds * 1000);
  }
}

function initImagesRefreshControl() {
  const select = document.getElementById("illustrationImagesRefreshInterval");
  if (!select) return;
  const saved = String(localStorage.getItem(IMAGES_REFRESH_KEY) || "5");
  select.value = ["0", "5", "10", "20", "30", "60"].includes(saved) ? saved : "5";
  select.addEventListener("change", applyImagesAutoRefresh);
  applyImagesAutoRefresh();
}

function stopAutoRefresh() {
  if (autoRefreshTimer) {
    window.clearInterval(autoRefreshTimer);
    autoRefreshTimer = 0;
  }
}

function stopElapsedRefresh() {
  if (elapsedRefreshTimer) {
    window.clearInterval(elapsedRefreshTimer);
    elapsedRefreshTimer = 0;
  }
}

function initElapsedRefresh() {
  stopElapsedRefresh();
  elapsedRefreshTimer = window.setInterval(updateLiveElapsedLabels, 1000);
}

function applyAutoRefresh() {
  stopAutoRefresh();
  const select = document.getElementById("illustrationAutoRefreshSelect");
  const seconds = Number(select?.value || 0);
  localStorage.setItem(AUTO_REFRESH_KEY, String(seconds));
  if (Number.isFinite(seconds) && seconds > 0) {
    autoRefreshTimer = window.setInterval(() => refreshPage().catch(() => {}), seconds * 1000);
  }
}

function initAutoRefreshControl() {
  const select = document.getElementById("illustrationAutoRefreshSelect");
  if (!select) return;
  const saved = String(localStorage.getItem(AUTO_REFRESH_KEY) || "5");
  select.value = AUTO_REFRESH_VALUES.includes(saved) ? saved : "5";
  select.addEventListener("change", applyAutoRefresh);
  applyAutoRefresh();
}

async function openImagesModal(chapterNum) {
  activeImagesChapterNum = Number(chapterNum || 0);
  const chapter = chapterItems.find((item) => Number(item.chapterNum || 0) === activeImagesChapterNum);
  const duration = Number(chapter?.audioDurationSeconds || 0);
  const durationText = duration > 0 ? ` · 音频时长 ${formatAudioDurationClock(duration)}` : "";
  const chapterTitle = String(chapter?.title || "").trim();
  const titleText = chapterTitle ? ` · ${chapterTitle}` : "";
  document.getElementById("illustrationImagesTitle").textContent = `插图生成 · 第${String(activeImagesChapterNum).padStart(3, "0")}回${titleText}${durationText}`;
  document.getElementById("illustrationImagesList").innerHTML = '<p class="empty-text">加载中...</p>';
  const dialog = document.getElementById("illustrationImagesDialog");
  if (!dialog.open) dialog.showModal();
  applyImagesAutoRefresh();
  await refreshImagesModal();
}

async function switchImagesChapter(delta) {
  if (!activeImagesChapterNum) return;
  const nums = chapterItems.map((item) => Number(item.chapterNum || 0)).filter(Boolean);
  const currentIndex = nums.indexOf(activeImagesChapterNum);
  const nextNum = nums[currentIndex + delta];
  if (!nextNum) {
    toast(delta < 0 ? "已经是第一回" : "已经是最后一回");
    return;
  }
  await openImagesModal(nextNum);
}

function bindEvents() {
  document.getElementById("illustrationNovelSelect").addEventListener("change", async (event) => {
    const id = String(event.target.value || "");
    setActiveNovelId(id);
    activeNovel = allNovels.find((novel) => String(novel.id) === id) || null;
    await refreshPage();
  });
  document.getElementById("refreshIllustrationBtn").addEventListener("click", async () => {
    await refreshPage();
    toast("插画解析列表已刷新");
  });
  document.getElementById("restartIllustrationLlmWorkerBtn").addEventListener("click", async () => {
    await restartIllustrationLlmWorker();
    toast("解析Worker已重启");
    await refreshPage();
  });
  document.getElementById("restartIllustrationImageWorkerBtn").addEventListener("click", async () => {
    await restartIllustrationImageWorker();
    toast("生图Worker已重启");
    await refreshPage();
  });
  document.getElementById("illustrationSelectAll").addEventListener("change", (event) => {
    selectedChapterNums.clear();
    if (event.target.checked) {
      chapterItems.forEach((item) => selectedChapterNums.add(Number(item.chapterNum || 0)));
    }
    renderTable();
  });
  document.getElementById("batchIllustrationSceneBtn").addEventListener("click", () => enqueueSelectedStage("scene"));
  document.getElementById("batchIllustrationShotBtn").addEventListener("click", () => enqueueSelectedStage("shot"));
  document.getElementById("batchIllustrationPromptBtn").addEventListener("click", () => enqueueSelectedStage("prompt"));
  document.getElementById("batchIllustrationAllStagesBtn").addEventListener("click", () => enqueueSelectedAllStages());
  document.getElementById("batchIllustrationImagesBtn").addEventListener("click", () => enqueueSelectedImages());
  document.getElementById("cancelPendingIllustrationTasksBtn").addEventListener("click", async () => {
    if (!activeNovel || !window.confirm("确定终止所有待处理的解析任务？正在执行中的任务不会被中断。")) return;
    const data = await cancelPendingIllustrationTasks(activeNovel.id);
    toast(`已取消 ${data.cancelled || 0} 个解析任务`);
    await refreshPage();
  });
  document.getElementById("cancelPendingIllustrationImagesBtn").addEventListener("click", async () => {
    if (!activeNovel || !window.confirm("确定取消所有待处理的生插图任务？正在生成中的任务不会被中断。")) return;
    const data = await cancelPendingIllustrationImages(activeNovel.id);
    toast(`已移出 ${data.cancelled || 0} 个生插图任务`);
    await refreshPage();
    await refreshImagesModal().catch(() => {});
  });
  document.getElementById("illustrationTableBody").addEventListener("click", async (event) => {
    const checkbox = event.target.closest(".illustration-row-select");
    if (checkbox) {
      setRowSelected(Number(checkbox.dataset.chapterNum || 0), checkbox.checked);
      return;
    }
    const runBtn = event.target.closest(".illustration-run-btn");
    if (runBtn) {
      await enqueueStage(Number(runBtn.dataset.chapterNum || 0), String(runBtn.dataset.stage || ""));
      return;
    }
    const llmParamsBtn = event.target.closest(".illustration-llm-params-btn");
    if (llmParamsBtn) {
      await openLlmParams(Number(llmParamsBtn.dataset.chapterNum || 0), String(llmParamsBtn.dataset.stage || ""));
      return;
    }
    const promptDetailBtn = event.target.closest(".illustration-prompt-detail-btn");
    if (promptDetailBtn) {
      await openPromptBatches(Number(promptDetailBtn.dataset.chapterNum || 0), String(promptDetailBtn.dataset.stage || "prompt"));
      return;
    }
    const promptOutputBtn = event.target.closest(".illustration-prompt-output-btn");
    if (promptOutputBtn) {
      await openPromptOutput(Number(promptOutputBtn.dataset.chapterNum || 0));
      return;
    }
    const videoBtn = event.target.closest(".illustration-video-btn");
    if (videoBtn) {
      openIllustrationVideo(Number(videoBtn.dataset.chapterNum || 0), Number(videoBtn.dataset.taskId || 0));
      return;
    }
    const viewBtn = event.target.closest(".illustration-view-btn");
    if (viewBtn) {
      await openPayload(Number(viewBtn.dataset.chapterNum || 0), String(viewBtn.dataset.stage || ""), String(viewBtn.dataset.kind || ""));
      return;
    }
    const imagesBtn = event.target.closest(".illustration-images-btn");
    if (imagesBtn) {
      await openImagesModal(Number(imagesBtn.dataset.chapterNum || 0));
    }
  });
  document.getElementById("illustrationTableBody").addEventListener("mousedown", (event) => {
    if (event.button !== 0 || event.target.closest("button,a,select,input")) return;
    const row = event.target.closest(".illustration-select-row");
    if (!row) return;
    event.preventDefault();
    const chapterNum = Number(row.dataset.chapterNum || 0);
    dragSelecting = true;
    dragSelectValue = !selectedChapterNums.has(chapterNum);
    setRowSelected(chapterNum, dragSelectValue);
    document.body.classList.add("is-illustration-drag-selecting");
  });
  document.getElementById("illustrationTableBody").addEventListener("mouseover", (event) => {
    if (!dragSelecting) return;
    const row = event.target.closest(".illustration-select-row");
    if (row) setRowSelected(Number(row.dataset.chapterNum || 0), dragSelectValue);
  });
  document.addEventListener("mouseup", () => {
    dragSelecting = false;
    document.body.classList.remove("is-illustration-drag-selecting");
  });
  document.getElementById("illustrationPayloadCopyBtn").addEventListener("click", () => {
    copyText(document.getElementById("illustrationPayloadContent")?.value || "").catch((err) => {
      toast(`复制失败：${err.message}`);
    });
  });
  document.getElementById("saveIllustrationPayloadBtn").addEventListener("click", async () => {
    try {
      await saveSceneOutput();
    } catch (err) {
      toast(`保存失败：${err.message}`);
    }
  });
  document.getElementById("sceneStatsBtn").addEventListener("click", toggleSceneStatsPanel);
  document.getElementById("illustrationPayloadDialog").addEventListener("keydown", async (event) => {
    if ((event.ctrlKey || event.metaKey) && String(event.key || "").toLowerCase() === "s") {
      event.preventDefault();
      try {
        await saveSceneOutput();
      } catch (err) {
        toast(`保存失败：${err.message}`);
      }
      return;
    }
    if (!activePayloadChapterNum || activePayloadStage !== "scene" || activePayloadKind !== "output") return;
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    if (!event.shiftKey || (!event.metaKey && !event.ctrlKey)) return;
    if (event.target.closest("input,select")) return;
    event.preventDefault();
    await switchSceneOutput(event.key === "ArrowLeft" ? -1 : 1);
  });
  document.getElementById("illustrationLlmParamsCopyBtn").addEventListener("click", () => {
    copyText(document.getElementById("illustrationLlmParamsContent")?.textContent || "").catch((err) => {
      toast(`复制失败：${err.message}`);
    });
  });
  document.getElementById("promptBatchCopyBtn").addEventListener("click", () => {
    copyText(document.getElementById("promptBatchContent")?.textContent || "").catch((err) => {
      toast(`复制失败：${err.message}`);
    });
  });
  document.getElementById("promptOutputCopyBtn").addEventListener("click", () => {
    copyText(document.getElementById("promptOutputEditor")?.value || "").catch((err) => {
      toast(`复制失败：${err.message}`);
    });
  });
  document.getElementById("promptOutputFindBtn").addEventListener("click", findPromptOutputText);
  document.getElementById("promptOutputReplaceBtn").addEventListener("click", replacePromptOutputText);
  document.getElementById("promptOutputReplaceAllBtn").addEventListener("click", replaceAllPromptOutputText);
  document.getElementById("promptOutputFindInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      findPromptOutputText();
    }
  });
  document.getElementById("promptOutputDialog").addEventListener("keydown", async (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    if (!event.shiftKey || (!event.metaKey && !event.ctrlKey)) return;
    if (event.target.closest("input,select")) return;
    event.preventDefault();
    await switchPromptOutput(event.key === "ArrowLeft" ? -1 : 1);
  });
  document.getElementById("savePromptOutputBtn").addEventListener("click", async () => {
    try {
      await savePromptOutput();
    } catch (err) {
      toast(`保存失败：${err.message}`);
    }
  });
  document.getElementById("downloadPromptOutputBtn").addEventListener("click", downloadPromptOutput);
  document.getElementById("clearPromptOutputNegativeBtn").addEventListener("click", clearPromptOutputNegative);
  document.getElementById("promptOutputEditor").addEventListener("input", updatePromptOutputCharCount);
  document.getElementById("promptOutputDialog").addEventListener("close", () => {
    activePromptOutputChapterNum = 0;
  });
  document.getElementById("illustrationVideoCloseBtn")?.addEventListener("click", closeIllustrationVideo);
  document.getElementById("illustrationVideoDialog")?.addEventListener("close", closeIllustrationVideo);
  document.getElementById("promptOutputDialog").addEventListener("keydown", async (event) => {
    if ((event.ctrlKey || event.metaKey) && String(event.key || "").toLowerCase() === "s") {
      event.preventDefault();
      try {
        await savePromptOutput();
      } catch (err) {
        toast(`保存失败：${err.message}`);
      }
    }
  });
  document.getElementById("promptBatchList").addEventListener("click", (event) => {
    const btn = event.target.closest(".prompt-batch-item");
    if (!btn) return;
    activePromptBatchIndex = Number(btn.dataset.batchIndex || 0);
    renderPromptBatchModal();
  });
  document.getElementById("promptBatchTabs").addEventListener("click", (event) => {
    const btn = event.target.closest(".prompt-batch-tab");
    if (!btn) return;
    activePromptBatchTab = String(btn.dataset.tab || "llm");
    renderPromptBatchModal();
  });
  document.getElementById("retryPromptBatchBtn").addEventListener("click", async () => {
    const batch = activePromptBatches[activePromptBatchIndex];
    if (!batch) return;
    const retryBtn = document.getElementById("retryPromptBatchBtn");
    const cancelBtn = document.getElementById("cancelPromptBatchBtn");
    retryBtn.hidden = true;
    if (cancelBtn) cancelBtn.hidden = false;
    const result = await retryChapterIllustrationPromptBatch(activeNovel.id, activePromptBatchesChapterNum, batch.batchIndex, activePromptBatchStage);
    const deletedImages = Number(result.deletedImages || 0);
    toast(`第 ${batch.batchIndex} 批已重新入队${deletedImages ? `，已清理 ${deletedImages} 张图片` : ""}`);
    await refreshPromptBatchesModal();
    await refreshPage();
    await refreshImagesModal().catch(() => {});
  });
  document.getElementById("cancelPromptBatchBtn")?.addEventListener("click", async () => {
    const batch = activePromptBatches[activePromptBatchIndex];
    if (!batch) return;
    await cancelChapterIllustrationPromptBatch(activeNovel.id, activePromptBatchesChapterNum, batch.batchIndex, activePromptBatchStage);
    toast(`第 ${batch.batchIndex} 批已终止`);
    await refreshPromptBatchesModal();
    await refreshPage();
  });
  document.getElementById("promptBatchDialog").addEventListener("close", () => {
    activePromptBatchesChapterNum = 0;
    activePromptBatchStage = "prompt";
    stopPromptBatchesAutoRefresh();
  });
  const payloadContent = document.getElementById("illustrationPayloadContent");
  payloadContent.addEventListener("mouseup", () => window.setTimeout(showPayloadTimeTooltip, 0));
  payloadContent.addEventListener("keyup", showPayloadTimeTooltip);
  payloadContent.addEventListener("scroll", hidePayloadTimeTooltip);
  document.getElementById("payloadScrollBottomBtn").addEventListener("click", () => {
    payloadContent.scrollTo({ top: payloadContent.scrollHeight, behavior: "smooth" });
  });
  document.getElementById("downloadIllustrationPayloadBtn")?.addEventListener("click", downloadIllustrationPayloadOutput);
  document.addEventListener("selectionchange", () => {
    if (document.getElementById("illustrationPayloadDialog")?.open) {
      window.setTimeout(showPayloadTimeTooltip, 0);
    }
  });
  document.getElementById("illustrationPayloadDialog").addEventListener("close", () => {
    activePayloadChapterNum = 0;
    activePayloadStage = "";
    activePayloadKind = "";
    hidePayloadTimeTooltip();
  });
  document.getElementById("generateAllIllustrationImagesBtn").addEventListener("click", async () => {
    if (!activeImagesChapterNum) return;
    const data = await enqueueAllIllustrationImages(activeNovel.id, activeImagesChapterNum);
    toast(`已入队 ${data.queued || 0} 张，跳过 ${data.skipped || 0} 张`);
    await refreshImagesModal();
  });
  document.getElementById("generateRemainingIllustrationImagesBtn")?.addEventListener("click", async () => {
    if (!activeImagesChapterNum) return;
    const remaining = currentImageItems.filter((item) => String(item.status || "idle") === "idle");
    let queued = 0;
    for (const item of remaining) {
      try {
        await enqueueIllustrationImage(item.id);
        queued += 1;
      } catch {
        // Skip items that changed status while this batch was being queued.
      }
    }
    toast(`剩余插图已入队 ${queued} 张`);
    await refreshImagesModal();
  });
  document.getElementById("illustrationImagesList").addEventListener("click", async (event) => {
    const dataBtn = event.target.closest(".illustration-image-data-btn");
    if (dataBtn) {
      const item = currentImageItems.find((entry) => String(entry.id) === String(dataBtn.dataset.imageId || ""));
      openImageDataModal(item);
      return;
    }
    const genBtn = event.target.closest(".illustration-generate-image-btn");
    if (genBtn) {
      await enqueueIllustrationImage(genBtn.dataset.imageId);
      toast("插图已加入生成队列");
      await refreshImagesModal();
      return;
    }
    const slot = event.target.closest(".illustration-image-slot.has-image");
    if (slot?.dataset.imageUrl) {
      const imageId = Number(slot.querySelector("img[data-image-id]")?.dataset.imageId || 0);
      const index = currentPreviewItems.findIndex((item) => Number(item.id) === imageId);
      openPreviewAt(index >= 0 ? index : 0);
    }
  });
  document.getElementById("illustrationImagesJsonWarning")?.addEventListener("click", (event) => {
    const btn = event.target.closest(".illustration-json-warning-link");
    if (!btn) return;
    const card = document.querySelector(`.illustration-image-card[data-image-id="${btn.dataset.imageId}"]`);
    if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  document.addEventListener("keydown", async (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    if (event.target.closest("input,select,textarea")) return;
    const previewDialog = document.getElementById("illustrationImagePreviewDialog");
    if (previewDialog?.open) {
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        switchPreview(-1);
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        switchPreview(1);
      }
      return;
    }
    const imagesDialog = document.getElementById("illustrationImagesDialog");
    if (!imagesDialog?.open) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      await switchImagesChapter(-1);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      await switchImagesChapter(1);
    }
  });
  document.getElementById("illustrationPreviewPrevBtn").addEventListener("click", () => switchPreview(-1));
  document.getElementById("illustrationPreviewNextBtn").addEventListener("click", () => switchPreview(1));
  document.getElementById("illustrationPreviewCopyJsonBtn").addEventListener("click", copyPreviewJson);
  document.getElementById("illustrationPreviewOptimizeJsonBtn")?.addEventListener("click", optimizePreviewJson);
  document.getElementById("illustrationPreviewRestoreJsonBtn")?.addEventListener("click", restorePreviewJson);
  document.getElementById("illustrationPreviewSaveJsonBtn").addEventListener("click", () => savePreviewJson());
  document.getElementById("illustrationOptimizeTabs")?.addEventListener("click", (event) => {
    const btn = event.target.closest(".illustration-optimize-tab");
    if (!btn) return;
    optimizeDetailTab = String(btn.dataset.tab || "llm");
    renderOptimizeDetail();
  });
  document.getElementById("illustrationOptimizeApplyBtn")?.addEventListener("click", applyOptimizedPreviewJson);
  document.getElementById("illustrationOptimizeCopyBtn")?.addEventListener("click", () => {
    copyText(document.getElementById("illustrationOptimizeContent")?.textContent || "").catch((err) => {
      toast(`复制失败：${err.message}`);
    });
  });
  document.getElementById("illustrationImageDataCopyBtn")?.addEventListener("click", () => {
    copyText(activeImageDataText).catch((err) => {
      toast(`复制失败：${err.message}`);
    });
  });
  document.getElementById("illustrationPreviewRefreshBtn").addEventListener("click", refreshPreviewImageData);
  document.getElementById("illustrationPreviewJsonEditor")?.addEventListener("input", markPreviewJsonDirty);
  document.getElementById("illustrationImagePreviewDialog")?.addEventListener("close", () => {
    previewJsonDirty = false;
    previewJsonImageKey = "";
    previewJsonOriginalText = "";
  });
  document.getElementById("illustrationPreviewRegenerateBtn").addEventListener("click", regeneratePreviewImage);
  document.getElementById("illustrationPreviewJsonEditor").addEventListener("input", updatePreviewJsonCount);
  document.getElementById("illustrationImagePreviewDialog").addEventListener("keydown", async (event) => {
    if (!(event.ctrlKey || event.metaKey) || String(event.key || "").toLowerCase() !== "s") return;
    event.preventDefault();
    await savePreviewJson();
  });
  document.getElementById("illustrationImagesDialog").addEventListener("close", stopImagesAutoRefresh);
  initImagesRefreshControl();
}

async function init() {
  renderNav();
  const data = await getData({ include: ["novels"] });
  allNovels = data.novels || [];
  activeNovel = getNovelByQueryOrActive();
  renderNovelSelect();
  bindEvents();
  await refreshPage();
  initAutoRefreshControl();
  initElapsedRefresh();
  window.addEventListener("beforeunload", () => {
    stopAutoRefresh();
    stopElapsedRefresh();
    stopPromptBatchesAutoRefresh();
    stopImagesAutoRefresh();
  });
}

init().catch((err) => {
  renderNav();
  toast(err.message || "加载失败");
});
