import { bytesToText, fetchNovelDownloadChapters, getData, getActiveNovelId, mergeChapterLineAudio, setActiveNovelId } from "./store.js";
import { renderNav, toast } from "./ui.js";

let allNovels = [];
let activeNovel = null;
let chapterItems = [];
const selectedChapterNums = new Set();
let dragSelectState = null;
const sortState = {
  field: "",
  direction: "none",
};

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text || "");
  return div.innerHTML;
}

function formatDuration(totalSeconds) {
  const safe = Math.max(0, Math.round(Number(totalSeconds) || 0));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = safe % 60;
  if (hours > 0) {
    return `${hours}小时${minutes}分钟${seconds}秒`;
  }
  if (minutes > 0) {
    return `${minutes}分钟${seconds}秒`;
  }
  return `${seconds}秒`;
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

function setHeader() {
  const titleEl = document.getElementById("novelDownloadPageTitle");
  const metaEl = document.getElementById("novelDownloadPageMeta");
  const summaryEl = document.getElementById("novelDownloadSummary");
  const selectionMetaEl = document.getElementById("novelDownloadSelectionMeta");
  const rolesLink = document.getElementById("novelDownloadRolesLink");
  const chaptersLink = document.getElementById("novelDownloadChaptersLink");
  if (!activeNovel) {
    titleEl.textContent = "小说下载";
    metaEl.textContent = "未找到小说";
    summaryEl.textContent = "-";
    if (selectionMetaEl) selectionMetaEl.textContent = "已选择 0 回";
    return;
  }
  titleEl.textContent = `${activeNovel.name} - 小说下载`;
  const available = chapterItems.filter((item) => item.hasAudio).length;
  metaEl.textContent = `${activeNovel.author || "未知作者"} · 共 ${chapterItems.length} 回 · 可下载 ${available} 回`;
  summaryEl.textContent = `总计 ${chapterItems.length} 回`;
  if (selectionMetaEl) selectionMetaEl.textContent = `已选择 ${selectedChapterNums.size} 回`;
  rolesLink.href = `./roles.html?novelId=${encodeURIComponent(activeNovel.id)}`;
  chaptersLink.href = `./chapters.html?novelId=${encodeURIComponent(activeNovel.id)}`;
}

function renderNovelSelect() {
  const select = document.getElementById("novelDownloadNovelSelect");
  select.innerHTML = allNovels.map((novel) => `<option value="${novel.id}">${novel.name}</option>`).join("");
  if (activeNovel) {
    select.value = String(activeNovel.id);
  }
}

function nextSortDirection(currentField, nextField) {
  if (currentField !== nextField) return "desc";
  if (sortState.direction === "desc") return "asc";
  if (sortState.direction === "asc") return "none";
  return "desc";
}

function getSortedChapterItems() {
  const items = [...chapterItems];
  if (!sortState.field || sortState.direction === "none") {
    return items.sort((a, b) => Number(a.chapterNum || 0) - Number(b.chapterNum || 0));
  }
  const factor = sortState.direction === "asc" ? 1 : -1;
  return items.sort((a, b) => {
    let av = 0;
    let bv = 0;
    if (sortState.field === "wordCount") {
      av = Number(a.wordCount || 0);
      bv = Number(b.wordCount || 0);
    } else if (sortState.field === "audioDurationSeconds") {
      av = Number(a.audioDurationSeconds || 0);
      bv = Number(b.audioDurationSeconds || 0);
    } else if (sortState.field === "audioSizeBytes") {
      av = Number(a.audioSizeBytes || 0);
      bv = Number(b.audioSizeBytes || 0);
    } else if (sortState.field === "nonVerAudioDurationSeconds") {
      av = Number(a.nonVerAudioDurationSeconds || 0);
      bv = Number(b.nonVerAudioDurationSeconds || 0);
    } else if (sortState.field === "nonVerAudioSizeBytes") {
      av = Number(a.nonVerAudioSizeBytes || 0);
      bv = Number(b.nonVerAudioSizeBytes || 0);
    }
    if (av === bv) {
      return Number(a.chapterNum || 0) - Number(b.chapterNum || 0);
    }
    return (av - bv) * factor;
  });
}

function updateSortIcons() {
  const mapping = [
    ["wordCount", "sortWordCountIcon"],
    ["audioDurationSeconds", "sortDurationIcon"],
    ["audioSizeBytes", "sortSizeIcon"],
  ];
  mapping.forEach(([field, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (sortState.field !== field || sortState.direction === "none") {
      el.textContent = "↕";
      return;
    }
    el.textContent = sortState.direction === "desc" ? "↓" : "↑";
  });
}

function updateSelectionControls() {
  const selectAll = document.getElementById("novelDownloadSelectAll");
  const selectionMetaEl = document.getElementById("novelDownloadSelectionMeta");
  const selectedCount = selectedChapterNums.size;
  const totalCount = chapterItems.length;
  if (selectionMetaEl) {
    selectionMetaEl.textContent = `已选择 ${selectedCount} 回`;
  }
  if (!selectAll) return;
  selectAll.checked = totalCount > 0 && selectedCount === totalCount;
  selectAll.indeterminate = selectedCount > 0 && selectedCount < totalCount;
}

function setBatchMergeProgress(current, total) {
  const progressEl = document.getElementById("batchMergeProgress");
  if (!progressEl) return;
  if (!total || current < 0) {
    progressEl.textContent = "0/0";
    progressEl.classList.add("hidden");
    return;
  }
  progressEl.textContent = `${current}/${total}`;
  progressEl.classList.remove("hidden");
}

function toggleChapterSelection(chapterNum, checked) {
  const safeChapterNum = Number(chapterNum || 0);
  if (!safeChapterNum) return;
  if (checked) {
    selectedChapterNums.add(safeChapterNum);
  } else {
    selectedChapterNums.delete(safeChapterNum);
  }
  updateSelectionControls();
}

function isInteractiveTarget(target) {
  return Boolean(target?.closest?.("a,button,input,select,textarea,label,audio,video"));
}

function setChapterSelected(chapterNum, selected) {
  const safeChapterNum = Number(chapterNum || 0);
  if (!safeChapterNum) return;
  if (selected) selectedChapterNums.add(safeChapterNum);
  else selectedChapterNums.delete(safeChapterNum);
  const row = Array.from(document.querySelectorAll(".novel-download-row"))
    .find((entry) => Number(entry.dataset.chapterNum || 0) === safeChapterNum);
  if (row) {
    row.classList.toggle("is-selected", selectedChapterNums.has(safeChapterNum));
    const checkbox = row.querySelector(".novel-download-item-check");
    if (checkbox) checkbox.checked = selectedChapterNums.has(safeChapterNum);
  }
}

function applyDragSelection(row) {
  if (!dragSelectState || !row) return;
  setChapterSelected(row.dataset.chapterNum, dragSelectState.selecting);
  updateSelectionControls();
}

function stopDragSelection() {
  if (!dragSelectState) return;
  dragSelectState = null;
  document.body.classList.remove("is-novel-download-drag-selecting");
}

function clearSelection() {
  selectedChapterNums.clear();
  updateSelectionControls();
}

function getSelectedChapterItems() {
  return chapterItems
    .filter((item) => selectedChapterNums.has(Number(item.chapterNum || 0)))
    .sort((a, b) => Number(a.chapterNum || 0) - Number(b.chapterNum || 0));
}

function renderTable() {
  const tbody = document.getElementById("novelDownloadTableBody");
  if (!activeNovel) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-text">未找到小说</td></tr>';
    clearSelection();
    return;
  }
  if (!chapterItems.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-text">暂无章回数据</td></tr>';
    clearSelection();
    return;
  }
  const rows = getSortedChapterItems();
  const validChapterNums = new Set(rows.map((item) => Number(item.chapterNum || 0)).filter(Boolean));
  Array.from(selectedChapterNums).forEach((chapterNum) => {
    if (!validChapterNums.has(chapterNum)) {
      selectedChapterNums.delete(chapterNum);
    }
  });
  tbody.innerHTML = rows.map((item) => {
    const chapterNum = Number(item.chapterNum || 0);
    const selected = selectedChapterNums.has(chapterNum);
    const abnormalCount = Number(item.abnormalLineAudioCount || 0);
    const abnormalBadge = abnormalCount > 0
      ? `<span class="novel-download-abnormal-badge" title="异常台词音频数：${abnormalCount}">! ${abnormalCount}</span>`
      : "";
    return `
    <tr class="novel-download-row${selected ? " is-selected" : ""}${abnormalCount > 0 ? " has-abnormal-line-audio" : ""}" data-chapter-num="${chapterNum}">
      <td>
        <label class="novel-download-checkbox-cell" aria-label="选择第 ${String(item.chapterNum).padStart(3, "0")} 回">
          <input class="novel-download-item-check" type="checkbox" data-chapter-num="${chapterNum}" ${selected ? "checked" : ""} />
        </label>
      </td>
      <td>${String(item.chapterNum).padStart(3, "0")}</td>
      <td>${escapeHtml(item.title || "-")} ${abnormalBadge}</td>
      <td>${Number(item.wordCount || 0).toLocaleString("zh-CN")}</td>
      <td>${item.hasAudio ? formatDuration(item.audioDurationSeconds || 0) : "-"}</td>
      <td>${item.hasAudio ? bytesToText(item.audioSizeBytes || 0) : "-"}</td>
      <td>${item.hasNonVerAudio ? formatDuration(item.nonVerAudioDurationSeconds || 0) : "-"}</td>
      <td>${item.hasNonVerAudio ? bytesToText(item.nonVerAudioSizeBytes || 0) : "-"}</td>
      <td>${[
        item.hasAudio ? `<a class="ghost-btn btn-sm" href="${item.downloadUrl}">下载音频</a>` : '<span class="text-muted">暂无音频</span>',
        item.hasNonVerAudio ? `<a class="ghost-btn btn-sm" href="${item.nonVerDownloadUrl}">下载无版权</a>` : ''
      ].filter(Boolean).join(' ')}</td>
    </tr>
  `;
  }).join("");
  updateSortIcons();
  updateSelectionControls();
}

async function refreshPage() {
  if (!activeNovel) return;
  chapterItems = await fetchNovelDownloadChapters(activeNovel.id);
  setHeader();
  renderTable();
}

function bindEvents() {
  document.getElementById("novelDownloadNovelSelect").addEventListener("change", async (event) => {
    const id = String(event.target.value || "");
    setActiveNovelId(id);
    activeNovel = allNovels.find((novel) => String(novel.id) === id) || null;
    clearSelection();
    await refreshPage();
  });
  document.getElementById("refreshNovelDownloadBtn").addEventListener("click", async () => {
    await refreshPage();
    toast("小说下载列表已刷新");
  });
  document.getElementById("sortWordCountBtn").addEventListener("click", () => {
    sortState.direction = nextSortDirection(sortState.field, "wordCount");
    sortState.field = sortState.direction === "none" ? "" : "wordCount";
    renderTable();
  });
  document.getElementById("sortDurationBtn").addEventListener("click", () => {
    sortState.direction = nextSortDirection(sortState.field, "audioDurationSeconds");
    sortState.field = sortState.direction === "none" ? "" : "audioDurationSeconds";
    renderTable();
  });
  document.getElementById("sortSizeBtn").addEventListener("click", () => {
    sortState.direction = nextSortDirection(sortState.field, "audioSizeBytes");
    sortState.field = sortState.direction === "none" ? "" : "audioSizeBytes";
    renderTable();
  });
  document.getElementById("novelDownloadSelectAll").addEventListener("change", (event) => {
    const checked = Boolean(event.target.checked);
    selectedChapterNums.clear();
    if (checked) {
      chapterItems.forEach((item) => {
        const chapterNum = Number(item.chapterNum || 0);
        if (chapterNum) selectedChapterNums.add(chapterNum);
      });
    }
    renderTable();
  });
  document.getElementById("novelDownloadTableBody").addEventListener("change", (event) => {
    const checkbox = event.target.closest(".novel-download-item-check");
    if (!checkbox) return;
    toggleChapterSelection(checkbox.dataset.chapterNum, checkbox.checked);
    checkbox.closest(".novel-download-row")?.classList.toggle("is-selected", checkbox.checked);
  });
  document.getElementById("novelDownloadTableBody").addEventListener("mousedown", (event) => {
    if (event.button !== 0 || isInteractiveTarget(event.target)) return;
    const row = event.target.closest(".novel-download-row");
    if (!row) return;
    const chapterNum = Number(row.dataset.chapterNum || 0);
    if (!chapterNum) return;
    event.preventDefault();
    dragSelectState = { selecting: !selectedChapterNums.has(chapterNum) };
    document.body.classList.add("is-novel-download-drag-selecting");
    applyDragSelection(row);
  });
  document.getElementById("novelDownloadTableBody").addEventListener("mouseover", (event) => {
    if (!dragSelectState) return;
    const row = event.target.closest(".novel-download-row");
    if (!row) return;
    applyDragSelection(row);
  });
  document.addEventListener("mouseup", stopDragSelection);
  window.addEventListener("blur", stopDragSelection);
  document.getElementById("batchMergeAudioBtn").addEventListener("click", async () => {
    if (!activeNovel) return;
    const selectedItems = getSelectedChapterItems();
    if (!selectedItems.length) {
      toast("请先选择要合并的章回");
      return;
    }
    const mergeBtn = document.getElementById("batchMergeAudioBtn");
    mergeBtn.disabled = true;
    mergeBtn.textContent = "合并中...";
    setBatchMergeProgress(0, selectedItems.length);
    let successCount = 0;
    let failedCount = 0;
    try {
      for (const [index, item] of selectedItems.entries()) {
        setBatchMergeProgress(index + 1, selectedItems.length);
        try {
          await mergeChapterLineAudio(activeNovel.id, item.chapterNum);
          successCount += 1;
          await refreshPage();
        } catch {
          failedCount += 1;
        }
      }
      if (failedCount > 0) {
        toast(`批量合并完成：成功 ${successCount} 回，失败 ${failedCount} 回`);
      } else {
        toast(`批量合并完成：共 ${successCount} 回`);
      }
    } catch (err) {
      toast(err.message || "批量合并失败");
    } finally {
      mergeBtn.disabled = false;
      mergeBtn.textContent = "批量合并音频";
      setBatchMergeProgress(-1, 0);
    }
  });
  document.getElementById("batchMergeNonVerAudioBtn").addEventListener("click", async () => {
    if (!activeNovel) return;
    const selectedItems = getSelectedChapterItems();
    if (!selectedItems.length) {
      toast("请先选择要合并的章回");
      return;
    }
    const mergeBtn = document.getElementById("batchMergeNonVerAudioBtn");
    mergeBtn.disabled = true;
    mergeBtn.textContent = "合并中...";
    setBatchMergeProgress(0, selectedItems.length);
    let successCount = 0;
    let failedCount = 0;
    try {
      for (const [index, item] of selectedItems.entries()) {
        setBatchMergeProgress(index + 1, selectedItems.length);
        try {
          await mergeChapterLineAudio(activeNovel.id, item.chapterNum, { variant: "nonver" });
          successCount += 1;
          await refreshPage();
        } catch {
          failedCount += 1;
        }
      }
      if (failedCount > 0) {
        toast(`批量合并无版权音频完成：成功 ${successCount} 回，失败 ${failedCount} 回`);
      } else {
        toast(`批量合并无版权音频完成：共 ${successCount} 回`);
      }
    } catch (err) {
      toast(err.message || "批量合并无版权音频失败");
    } finally {
      mergeBtn.disabled = false;
      mergeBtn.textContent = "批量合并无版权音频";
      setBatchMergeProgress(-1, 0);
    }
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
