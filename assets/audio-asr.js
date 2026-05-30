import {
  bytesToText,
  cancelChapterAudioAsr,
  enqueueBatchAudioAsr,
  enqueueChapterAudioAsr,
  fetchAudioAsrWorkerStatus,
  fetchNovelAudioAsrChapters,
  getData,
  getActiveNovelId,
  restartAudioAsrWorker,
  setActiveNovelId,
} from "./store.js";
import { renderNav, toast } from "./ui.js";

let allNovels = [];
let activeNovel = null;
let chapterItems = [];
const selectedChapterNums = new Set();
let autoRefreshTimer = 0;
let isDragSelecting = false;

function copyText(text) {
  const value = String(text || "");
  if (!value.trim() || value === "加载中...") {
    toast("暂无可复制内容");
    return Promise.resolve();
  }
  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    return navigator.clipboard.writeText(value).then(() => toast("ASR内容已复制"));
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
  toast("ASR内容已复制");
  return Promise.resolve();
}

function isForceExtractEnabled() {
  return Boolean(document.getElementById("audioAsrForceExtract")?.checked);
}

function renderTaskWorkerStatus(status) {
  const el = document.getElementById("audioAsrWorkerStatus");
  if (!el) return;
  const state = String(status?.state || "stopped");
  const mapping = {
    running: "运行中",
    stale: "心跳超时",
    stopped: "未运行",
  };
  const age = status?.heartbeatAgeSeconds != null ? ` · 心跳${status.heartbeatAgeSeconds}s` : "";
  el.textContent = `Worker: ${mapping[state] || state}${age}`;
}

async function refreshTaskWorkerStatus() {
  try {
    const status = await fetchAudioAsrWorkerStatus();
    renderTaskWorkerStatus(status);
  } catch {
    renderTaskWorkerStatus({ state: "stopped" });
  }
}

function formatDuration(totalSeconds) {
  const safe = Math.max(0, Math.round(Number(totalSeconds) || 0));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = safe % 60;
  if (hours > 0) return `${hours}小时${minutes}分钟${seconds}秒`;
  if (minutes > 0) return `${minutes}分钟${seconds}秒`;
  return `${seconds}秒`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text || "");
  return div.innerHTML;
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
    cancelled: "已终止",
    failed: "失败",
    completed: "完成",
  };
  return mapping[String(status || "idle")] || String(status || "-");
}

function statusClass(status) {
  const normalized = String(status || "idle");
  if (normalized === "completed") return "status-badge status-completed";
  if (normalized === "failed") return "status-badge status-failed";
  if (["running", "processing", "pending"].includes(normalized)) return "status-badge status-pending";
  return "status-badge";
}

function getActionLabel(item) {
  const status = String(item.status || "idle");
  if (["pending", "running", "processing"].includes(status)) return "终止";
  return status === "completed" ? "重新提取" : "提取ASR";
}

function getChunkProgressLabel(item) {
  const current = Number(item.currentChunkIndex || 0);
  const total = Number(item.totalChunkCount || 0);
  if (total <= 0) return "";
  const safeCurrent = Math.max(0, Math.min(current || 0, total));
  return `${safeCurrent}/${total}`;
}

function setHeader() {
  const titleEl = document.getElementById("audioAsrPageTitle");
  const metaEl = document.getElementById("audioAsrPageMeta");
  const summaryEl = document.getElementById("audioAsrSummary");
  const selectionMetaEl = document.getElementById("audioAsrSelectionMeta");
  const chaptersLink = document.getElementById("audioAsrChaptersLink");
  if (!activeNovel) {
    titleEl.textContent = "提取音频ASR";
    metaEl.textContent = "未找到小说";
    summaryEl.textContent = "-";
    selectionMetaEl.textContent = "已选择 0 回";
    return;
  }
  titleEl.textContent = `${activeNovel.name} - 提取音频ASR`;
  const available = chapterItems.filter((item) => item.hasAudio).length;
  const completed = chapterItems.filter((item) => item.status === "completed").length;
  metaEl.textContent = `共 ${chapterItems.length} 回 · 可提取 ${available} 回 · 已完成 ${completed} 回`;
  summaryEl.textContent = `总计 ${chapterItems.length} 回`;
  selectionMetaEl.textContent = `已选择 ${selectedChapterNums.size} 回`;
  chaptersLink.href = `./chapters.html?novelId=${encodeURIComponent(activeNovel.id)}`;
}

function renderNovelSelect() {
  const select = document.getElementById("audioAsrNovelSelect");
  select.innerHTML = allNovels.map((novel) => `<option value="${novel.id}">${novel.name}</option>`).join("");
  if (activeNovel) select.value = String(activeNovel.id);
}

function updateSelectionControls() {
  const selectAll = document.getElementById("audioAsrSelectAll");
  const selectionMetaEl = document.getElementById("audioAsrSelectionMeta");
  const selectedCount = selectedChapterNums.size;
  const availableItems = chapterItems.filter((item) => item.hasAudio);
  if (selectionMetaEl) selectionMetaEl.textContent = `已选择 ${selectedCount} 回`;
  if (!selectAll) return;
  selectAll.checked = availableItems.length > 0 && selectedCount === availableItems.length;
  selectAll.indeterminate = selectedCount > 0 && selectedCount < availableItems.length;
}

function toggleChapterSelection(chapterNum, checked) {
  const safeChapterNum = Number(chapterNum || 0);
  if (!safeChapterNum) return;
  if (checked) selectedChapterNums.add(safeChapterNum);
  else selectedChapterNums.delete(safeChapterNum);
  updateSelectionControls();
}

function applyDragSelection(chapterNum) {
  const safeChapterNum = Number(chapterNum || 0);
  if (!safeChapterNum) return;
  const item = chapterItems.find((entry) => Number(entry.chapterNum || 0) === safeChapterNum);
  if (!item?.hasAudio) return;
  if (selectedChapterNums.has(safeChapterNum)) return;
  selectedChapterNums.add(safeChapterNum);
  const checkbox = document.querySelector(`.audio-asr-item-check[data-chapter-num="${safeChapterNum}"]`);
  if (checkbox) checkbox.checked = true;
  updateSelectionControls();
}

function clearSelection() {
  selectedChapterNums.clear();
  updateSelectionControls();
}

function getSelectedChapterNums() {
  return chapterItems
    .filter((item) => selectedChapterNums.has(Number(item.chapterNum || 0)))
    .map((item) => Number(item.chapterNum || 0));
}

async function openAsrView(item) {
  if (!item?.downloadUrl) {
    toast("暂无ASR文件");
    return;
  }
  const dialog = document.getElementById("audioAsrViewDialog");
  const titleEl = document.getElementById("audioAsrViewTitle");
  const contentEl = document.getElementById("audioAsrViewContent");
  const copyBtn = document.getElementById("audioAsrCopyBtn");
  if (!dialog || !titleEl || !contentEl) return;
  titleEl.textContent = `查看ASR · 第${String(item.chapterNum).padStart(3, "0")}回 ${item.title || ""}`;
  contentEl.textContent = "加载中...";
  if (copyBtn) copyBtn.disabled = true;
  dialog.showModal();
  try {
    const res = await fetch(item.downloadUrl, { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    contentEl.textContent = await res.text();
    if (copyBtn) copyBtn.disabled = false;
  } catch (err) {
    contentEl.textContent = `加载失败：${err.message}`;
    if (copyBtn) copyBtn.disabled = true;
  }
}

function renderTable() {
  const tbody = document.getElementById("audioAsrTableBody");
  if (!activeNovel) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-text">未找到小说</td></tr>';
    clearSelection();
    return;
  }
  if (!chapterItems.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-text">暂无章回数据</td></tr>';
    clearSelection();
    return;
  }
  tbody.innerHTML = chapterItems.map((item) => `
    <tr class="audio-asr-row" data-chapter-num="${Number(item.chapterNum || 0)}">
      <td>
        <label class="novel-download-checkbox-cell" aria-label="选择第 ${String(item.chapterNum).padStart(3, "0")} 回">
          <input class="audio-asr-item-check" type="checkbox" data-chapter-num="${Number(item.chapterNum || 0)}" ${item.hasAudio ? "" : "disabled"} ${selectedChapterNums.has(Number(item.chapterNum || 0)) ? "checked" : ""} />
        </label>
      </td>
      <td>${String(item.chapterNum).padStart(3, "0")}</td>
      <td>${escapeHtml(item.title || "-")}</td>
      <td>${item.hasAudio ? formatDuration(item.audioDurationSeconds || 0) : "-"}</td>
      <td><span class="${statusClass(item.status)}">${statusLabel(item.status)}</span>${getChunkProgressLabel(item) ? `<div class="meta">${escapeHtml(getChunkProgressLabel(item))}</div>` : ""}${item.errorMessage ? `<div class="meta">${escapeHtml(item.errorMessage)}</div>` : ""}</td>
      <td>${item.hasAsr ? `<div class="table-actions-inline"><a class="ghost-btn btn-sm" href="${item.downloadUrl}">下载ASR</a><button class="ghost-btn btn-sm audio-asr-view-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}">查看</button></div>` : '<span class="text-muted">暂无</span>'}</td>
      <td><button class="ghost-btn btn-sm audio-asr-single-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}" data-status="${escapeHtml(item.status || "idle")}" ${item.hasAudio ? "" : "disabled"}>${getActionLabel(item)}</button></td>
    </tr>
  `).join("");
  updateSelectionControls();
  scheduleAutoRefreshIfNeeded();
}

function hasActiveTasks() {
  return chapterItems.some((item) => ["pending", "running", "processing"].includes(String(item.status || "")));
}

function scheduleAutoRefreshIfNeeded() {
  if (autoRefreshTimer) {
    window.clearTimeout(autoRefreshTimer);
    autoRefreshTimer = 0;
  }
  if (!hasActiveTasks()) return;
  autoRefreshTimer = window.setTimeout(async () => {
    await refreshPage();
  }, 3000);
}

async function refreshPage() {
  if (!activeNovel) return;
  chapterItems = await fetchNovelAudioAsrChapters(activeNovel.id);
  setHeader();
  renderTable();
  await refreshTaskWorkerStatus();
}

async function enqueueSingle(chapterNum) {
  const result = await enqueueChapterAudioAsr(activeNovel.id, chapterNum, {
    forceExtract: isForceExtractEnabled(),
  });
  if (String(result.status || "") === "skipped") {
    toast(`第 ${chapterNum} 回已跳过，音频无变化`);
  } else {
    toast(`第 ${chapterNum} 回已加入 ASR 队列`);
  }
  await refreshPage();
}

async function cancelSingle(chapterNum) {
  await cancelChapterAudioAsr(activeNovel.id, chapterNum);
  toast(`第 ${chapterNum} 回已终止`);
  await refreshPage();
}

async function enqueueBatch(chapterNums) {
  if (!chapterNums.length) {
    toast("请先选择要提取的章回");
    return;
  }
  const result = await enqueueBatchAudioAsr(activeNovel.id, chapterNums, {
    forceExtract: isForceExtractEnabled(),
  });
  const queued = Number(result.queued || 0);
  const skipped = Number(result.skipped || 0);
  const skippedUnchanged = Number(result.skippedUnchanged || 0);
  if (queued <= 0 && skippedUnchanged > 0 && skipped === skippedUnchanged) {
    toast(`已跳过 ${skippedUnchanged} 回，音频无变化`);
  } else {
    toast(`已入队 ${queued} 回，跳过 ${skipped} 回`);
  }
  await refreshPage();
}

function bindEvents() {
  document.getElementById("audioAsrNovelSelect").addEventListener("change", async (event) => {
    const id = String(event.target.value || "");
    setActiveNovelId(id);
    activeNovel = allNovels.find((novel) => String(novel.id) === id) || null;
    clearSelection();
    await refreshPage();
  });
  document.getElementById("refreshAudioAsrBtn").addEventListener("click", async () => {
    await refreshPage();
    toast("音频ASR列表已刷新");
  });
  document.getElementById("restartAudioAsrTaskWorkerBtn").addEventListener("click", async () => {
    await restartAudioAsrWorker();
    toast("任务Worker已重启");
    await refreshPage();
  });
  document.getElementById("audioAsrSelectAll").addEventListener("change", (event) => {
    const checked = Boolean(event.target.checked);
    selectedChapterNums.clear();
    if (checked) {
      chapterItems.forEach((item) => {
        if (item.hasAudio) selectedChapterNums.add(Number(item.chapterNum || 0));
      });
    }
    renderTable();
  });
  document.getElementById("audioAsrTableBody").addEventListener("change", (event) => {
    const checkbox = event.target.closest(".audio-asr-item-check");
    if (!checkbox) return;
    toggleChapterSelection(checkbox.dataset.chapterNum, checkbox.checked);
  });
  document.getElementById("audioAsrTableBody").addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    if (event.target.closest("button, a, input, label")) return;
    const row = event.target.closest(".audio-asr-row");
    if (!row) return;
    isDragSelecting = true;
    document.body.classList.add("is-drag-selecting");
    applyDragSelection(row.dataset.chapterNum);
    event.preventDefault();
  });
  document.getElementById("audioAsrTableBody").addEventListener("pointerover", (event) => {
    if (!isDragSelecting) return;
    const row = event.target.closest(".audio-asr-row");
    if (!row) return;
    applyDragSelection(row.dataset.chapterNum);
  });
  document.getElementById("audioAsrTableBody").addEventListener("click", async (event) => {
    const viewBtn = event.target.closest(".audio-asr-view-btn");
    if (viewBtn) {
      const item = chapterItems.find((entry) => Number(entry.chapterNum || 0) === Number(viewBtn.dataset.chapterNum || 0));
      if (item) {
        await openAsrView(item);
      }
      return;
    }
    const btn = event.target.closest(".audio-asr-single-btn");
    if (!btn) return;
    const chapterNum = Number(btn.dataset.chapterNum || 0);
    const status = String(btn.dataset.status || "idle");
    if (["pending", "running", "processing"].includes(status)) {
      await cancelSingle(chapterNum);
      return;
    }
    await enqueueSingle(chapterNum);
  });
  document.getElementById("audioAsrBatchBtn").addEventListener("click", async () => {
    await enqueueBatch(getSelectedChapterNums());
  });
  document.getElementById("audioAsrBatchAllBtn").addEventListener("click", async () => {
    const all = chapterItems.filter((item) => item.hasAudio).map((item) => Number(item.chapterNum || 0));
    await enqueueBatch(all);
  });
  document.getElementById("audioAsrCopyBtn")?.addEventListener("click", () => {
    copyText(document.getElementById("audioAsrViewContent")?.textContent || "").catch((err) => {
      toast(`复制失败：${err.message}`);
    });
  });
  document.addEventListener("pointerup", () => {
    isDragSelecting = false;
    document.body.classList.remove("is-drag-selecting");
  });
}

async function init() {
  renderNav();
  const data = await getData();
  allNovels = data.novels || [];
  activeNovel = getNovelByQueryOrActive();
  renderNovelSelect();
  bindEvents();
  await refreshPage();
}

init().catch((err) => {
  renderNav();
  toast(err.message || "加载失败");
});
