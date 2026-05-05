import {
  bytesToText,
  enqueueBatchAudioAsr,
  enqueueChapterAudioAsr,
  fetchNovelAudioAsrChapters,
  getData,
  getActiveNovelId,
  setActiveNovelId,
} from "./store.js";
import { renderNav, toast } from "./ui.js";

let allNovels = [];
let activeNovel = null;
let chapterItems = [];
const selectedChapterNums = new Set();
let autoRefreshTimer = 0;

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
  if (!dialog || !titleEl || !contentEl) return;
  titleEl.textContent = `查看ASR · 第${String(item.chapterNum).padStart(3, "0")}回 ${item.title || ""}`;
  contentEl.textContent = "加载中...";
  dialog.showModal();
  try {
    const res = await fetch(item.downloadUrl, { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    contentEl.textContent = await res.text();
  } catch (err) {
    contentEl.textContent = `加载失败：${err.message}`;
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
    <tr>
      <td>
        <label class="novel-download-checkbox-cell" aria-label="选择第 ${String(item.chapterNum).padStart(3, "0")} 回">
          <input class="audio-asr-item-check" type="checkbox" data-chapter-num="${Number(item.chapterNum || 0)}" ${item.hasAudio ? "" : "disabled"} ${selectedChapterNums.has(Number(item.chapterNum || 0)) ? "checked" : ""} />
        </label>
      </td>
      <td>${String(item.chapterNum).padStart(3, "0")}</td>
      <td>${escapeHtml(item.title || "-")}</td>
      <td>${item.hasAudio ? formatDuration(item.audioDurationSeconds || 0) : "-"}</td>
      <td><span class="${statusClass(item.status)}">${statusLabel(item.status)}</span>${item.errorMessage ? `<div class="meta">${escapeHtml(item.errorMessage)}</div>` : ""}</td>
      <td>${item.hasAsr ? `<div class="table-actions-inline"><a class="ghost-btn btn-sm" href="${item.downloadUrl}">下载ASR</a><button class="ghost-btn btn-sm audio-asr-view-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}">查看</button></div>` : '<span class="text-muted">暂无</span>'}</td>
      <td><button class="ghost-btn btn-sm audio-asr-single-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}" ${item.hasAudio ? "" : "disabled"}>${item.status === "completed" ? "重新提取" : "提取ASR"}</button></td>
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
}

async function enqueueSingle(chapterNum) {
  await enqueueChapterAudioAsr(activeNovel.id, chapterNum);
  toast(`第 ${chapterNum} 回已加入 ASR 队列`);
  await refreshPage();
}

async function enqueueBatch(chapterNums) {
  if (!chapterNums.length) {
    toast("请先选择要提取的章回");
    return;
  }
  const result = await enqueueBatchAudioAsr(activeNovel.id, chapterNums);
  toast(`已入队 ${Number(result.queued || 0)} 回，跳过 ${Number(result.skipped || 0)} 回`);
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
    await enqueueSingle(Number(btn.dataset.chapterNum || 0));
  });
  document.getElementById("audioAsrBatchBtn").addEventListener("click", async () => {
    await enqueueBatch(getSelectedChapterNums());
  });
  document.getElementById("audioAsrBatchAllBtn").addEventListener("click", async () => {
    const all = chapterItems.filter((item) => item.hasAudio).map((item) => Number(item.chapterNum || 0));
    await enqueueBatch(all);
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
