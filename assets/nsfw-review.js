import {
  enqueueBatchNsfwReview,
  enqueueChapterNsfwReview,
  fetchNsfwReviewWorkerStatus,
  fetchNovelNsfwReviewChapters,
  getData,
  getActiveNovelId,
  restartNsfwReviewWorker,
  setActiveNovelId,
} from "./store.js";
import { renderNav, toast } from "./ui.js";

let allNovels = [];
let activeNovel = null;
let chapterItems = [];
const selectedChapterNums = new Set();
let autoRefreshTimer = 0;

function renderTaskWorkerStatus(status) {
  const el = document.getElementById("nsfwReviewWorkerStatus");
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
    const status = await fetchNsfwReviewWorkerStatus();
    renderTaskWorkerStatus(status);
  } catch {
    renderTaskWorkerStatus({ state: "stopped" });
  }
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
    timeout: "超时",
    completed: "完成",
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

function setHeader() {
  const titleEl = document.getElementById("nsfwReviewPageTitle");
  const metaEl = document.getElementById("nsfwReviewPageMeta");
  const summaryEl = document.getElementById("nsfwReviewSummary");
  const selectionMetaEl = document.getElementById("nsfwReviewSelectionMeta");
  const chaptersLink = document.getElementById("nsfwReviewChaptersLink");
  if (!activeNovel) {
    titleEl.textContent = "NSFW审查";
    metaEl.textContent = "未找到小说";
    summaryEl.textContent = "-";
    selectionMetaEl.textContent = "已选择 0 回";
    return;
  }
  titleEl.textContent = `${activeNovel.name} - NSFW审查`;
  const completed = chapterItems.filter((item) => item.status === "completed").length;
  const flagged = chapterItems.filter((item) => item.hasNsfw).length;
  metaEl.textContent = `共 ${chapterItems.length} 回 · 已完成 ${completed} 回 · 命中 ${flagged} 回`;
  summaryEl.textContent = `总计 ${chapterItems.length} 回`;
  selectionMetaEl.textContent = `已选择 ${selectedChapterNums.size} 回`;
  chaptersLink.href = `./chapters.html?novelId=${encodeURIComponent(activeNovel.id)}`;
}

function renderNovelSelect() {
  const select = document.getElementById("nsfwReviewNovelSelect");
  select.innerHTML = allNovels.map((novel) => `<option value="${novel.id}">${novel.name}</option>`).join("");
  if (activeNovel) select.value = String(activeNovel.id);
}

function updateSelectionControls() {
  const selectAll = document.getElementById("nsfwReviewSelectAll");
  const selectionMetaEl = document.getElementById("nsfwReviewSelectionMeta");
  const selectedCount = selectedChapterNums.size;
  const items = chapterItems;
  if (selectionMetaEl) selectionMetaEl.textContent = `已选择 ${selectedCount} 回`;
  if (!selectAll) return;
  selectAll.checked = items.length > 0 && selectedCount === items.length;
  selectAll.indeterminate = selectedCount > 0 && selectedCount < items.length;
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

async function openResultView(item) {
  const dialog = document.getElementById("nsfwReviewDialog");
  const titleEl = document.getElementById("nsfwReviewDialogTitle");
  const contentEl = document.getElementById("nsfwReviewDialogContent");
  if (!dialog || !titleEl || !contentEl) return;
  titleEl.textContent = `查看审查结果 · 第${String(item.chapterNum).padStart(3, "0")}回 ${item.title || ""}`;
  contentEl.textContent = formatJsonPretty(item.resultJsonText || "") || "暂无审查结果";
  dialog.showModal();
}

function renderTable() {
  const tbody = document.getElementById("nsfwReviewTableBody");
  if (!activeNovel) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-text">未找到小说</td></tr>';
    clearSelection();
    return;
  }
  if (!chapterItems.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-text">暂无章回数据</td></tr>';
    clearSelection();
    return;
  }
  tbody.innerHTML = chapterItems.map((item) => `
    <tr>
      <td>
        <label class="novel-download-checkbox-cell" aria-label="选择第 ${String(item.chapterNum).padStart(3, "0")} 回">
          <input class="nsfw-review-item-check" type="checkbox" data-chapter-num="${Number(item.chapterNum || 0)}" ${selectedChapterNums.has(Number(item.chapterNum || 0)) ? "checked" : ""} />
        </label>
      </td>
      <td>${String(item.chapterNum).padStart(3, "0")}</td>
      <td>${escapeHtml(item.title || "-")}</td>
      <td>${Number(item.wordCount || 0)}</td>
      <td><span class="${statusClass(item.status)}">${statusLabel(item.status)}</span>${item.errorMessage ? `<div class="meta">${escapeHtml(item.errorMessage)}</div>` : ""}</td>
      <td>${item.status === "completed" ? `<span class="nsfw-flag-dot ${item.hasNsfw ? "is-danger" : "is-safe"}" title="${item.hasNsfw ? "命中NSFW" : "未命中NSFW"}"></span>` : '<span class="text-muted">-</span>'}</td>
      <td>${item.resultJsonText ? `<div class="table-actions-inline"><button class="ghost-btn btn-sm nsfw-review-view-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}">查看</button><span class="meta ${item.hasNsfw ? "status-failed" : "status-completed"}">${escapeHtml(item.summary || "")}</span></div>` : '<span class="text-muted">暂无</span>'}</td>
      <td><button class="ghost-btn btn-sm nsfw-review-single-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}">${item.status === "completed" ? "重新审查" : "审查"}</button></td>
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
  chapterItems = await fetchNovelNsfwReviewChapters(activeNovel.id);
  setHeader();
  renderTable();
  await refreshTaskWorkerStatus();
}

async function enqueueSingle(chapterNum) {
  await enqueueChapterNsfwReview(activeNovel.id, chapterNum);
  toast(`第 ${chapterNum} 回已加入 NSFW 审查队列`);
  await refreshPage();
}

async function enqueueBatch(chapterNums) {
  if (!chapterNums.length) {
    toast("请先选择要审查的章回");
    return;
  }
  const result = await enqueueBatchNsfwReview(activeNovel.id, chapterNums);
  toast(`已入队 ${Number(result.queued || 0)} 回，跳过 ${Number(result.skipped || 0)} 回`);
  await refreshPage();
}

function bindEvents() {
  document.getElementById("nsfwReviewNovelSelect").addEventListener("change", async (event) => {
    const id = String(event.target.value || "");
    setActiveNovelId(id);
    activeNovel = allNovels.find((novel) => String(novel.id) === id) || null;
    clearSelection();
    await refreshPage();
  });
  document.getElementById("refreshNsfwReviewBtn").addEventListener("click", async () => {
    await refreshPage();
    toast("NSFW审查列表已刷新");
  });
  document.getElementById("restartNsfwTaskWorkerBtn").addEventListener("click", async () => {
    await restartNsfwReviewWorker();
    toast("任务Worker已重启");
    await refreshPage();
  });
  document.getElementById("nsfwReviewSelectAll").addEventListener("change", (event) => {
    const checked = Boolean(event.target.checked);
    selectedChapterNums.clear();
    if (checked) {
      chapterItems.forEach((item) => selectedChapterNums.add(Number(item.chapterNum || 0)));
    }
    renderTable();
  });
  document.getElementById("nsfwReviewTableBody").addEventListener("change", (event) => {
    const checkbox = event.target.closest(".nsfw-review-item-check");
    if (!checkbox) return;
    toggleChapterSelection(checkbox.dataset.chapterNum, checkbox.checked);
  });
  document.getElementById("nsfwReviewTableBody").addEventListener("click", async (event) => {
    const viewBtn = event.target.closest(".nsfw-review-view-btn");
    if (viewBtn) {
      const item = chapterItems.find((entry) => Number(entry.chapterNum || 0) === Number(viewBtn.dataset.chapterNum || 0));
      if (item) await openResultView(item);
      return;
    }
    const btn = event.target.closest(".nsfw-review-single-btn");
    if (!btn) return;
    await enqueueSingle(Number(btn.dataset.chapterNum || 0));
  });
  document.getElementById("nsfwReviewBatchBtn").addEventListener("click", async () => {
    await enqueueBatch(getSelectedChapterNums());
  });
  document.getElementById("nsfwReviewBatchAllBtn").addEventListener("click", async () => {
    const all = chapterItems.map((item) => Number(item.chapterNum || 0));
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
