import {
  bytesToText,
  cancelChapterAudioAsrSubtitleRepair,
  cancelChapterAudioAsr,
  enqueueBatchAudioAsr,
  enqueueChapterAudioAsr,
  fetchAudioAsrWorkerStatus,
  fetchNovelAudioAsrChapters,
  getData,
  getActiveNovelId,
  repairChapterAudioAsrSubtitle,
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

function diffLines(leftText, rightText) {
  const left = String(leftText || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const right = String(rightText || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const dp = Array.from({ length: left.length + 1 }, () => Array(right.length + 1).fill(0));
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      dp[i][j] = left[i] === right[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const rows = [];
  let i = 0;
  let j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      rows.push({ type: "same", left: left[i], right: right[j], leftNo: i + 1, rightNo: j + 1 });
      i += 1;
      j += 1;
    } else if (j >= right.length || (i < left.length && dp[i + 1][j] >= dp[i][j + 1])) {
      rows.push({ type: "delete", left: left[i], right: "", leftNo: i + 1, rightNo: "" });
      i += 1;
    } else {
      rows.push({ type: "insert", left: "", right: right[j], leftNo: "", rightNo: j + 1 });
      j += 1;
    }
  }
  return rows;
}

function diffChars(leftText, rightText) {
  const left = Array.from(String(leftText || ""));
  const right = Array.from(String(rightText || ""));
  const dp = Array.from({ length: left.length + 1 }, () => Array(right.length + 1).fill(0));
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      dp[i][j] = left[i] === right[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const leftParts = [];
  const rightParts = [];
  let deleteCount = 0;
  let insertCount = 0;
  let i = 0;
  let j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      const value = escapeHtml(left[i]);
      leftParts.push(value);
      rightParts.push(value);
      i += 1;
      j += 1;
    } else if (j >= right.length || (i < left.length && dp[i + 1][j] >= dp[i][j + 1])) {
      leftParts.push(`<span class="subtitle-diff-char-delete">${escapeHtml(left[i])}</span>`);
      deleteCount += 1;
      i += 1;
    } else {
      rightParts.push(`<span class="subtitle-diff-char-insert">${escapeHtml(right[j])}</span>`);
      insertCount += 1;
      j += 1;
    }
  }
  return { leftHtml: leftParts.join(""), rightHtml: rightParts.join(""), deleteCount, insertCount };
}

function renderSrtBlockHtml(block, textHtml = "") {
  const parts = [];
  if (String(block.index || "").trim()) parts.push(escapeHtml(block.index));
  if (String(block.time || "").trim()) parts.push(escapeHtml(block.time));
  parts.push(textHtml || escapeHtml(block.text || ""));
  return parts.filter((part) => String(part || "").trim()).join("\n");
}

function parseSrtBlocksForCompare(text) {
  const normalized = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
  if (!normalized) return [];
  return normalized.split(/\n\s*\n+/).map((block) => {
    const lines = block.split("\n").map((line) => line.trimEnd()).filter((line) => line.trim());
    const index = lines[0] || "";
    const time = lines[1] || "";
    return { index, time, text: lines.slice(2).join("\n") };
  }).filter((block) => block.index || block.time || block.text);
}

function diffSrtBlocks(leftText, rightText) {
  const leftBlocks = parseSrtBlocksForCompare(leftText);
  const rightBlocks = parseSrtBlocksForCompare(rightText);
  if (!leftBlocks.length || !rightBlocks.length) return diffLines(leftText, rightText);
  const total = Math.max(leftBlocks.length, rightBlocks.length);
  const rows = [];
  for (let i = 0; i < total; i += 1) {
    const left = leftBlocks[i] || { index: "", time: "", text: "" };
    const right = rightBlocks[i] || { index: "", time: "", text: "" };
    const leftValue = [left.index, left.time, left.text].filter((part) => String(part || "").trim()).join("\n");
    const rightValue = [right.index, right.time, right.text].filter((part) => String(part || "").trim()).join("\n");
    const type = leftValue === rightValue ? "same" : (!leftValue ? "insert" : !rightValue ? "delete" : "change");
    let leftHtml = escapeHtml(leftValue);
    let rightHtml = escapeHtml(rightValue);
    let deleteCount = type === "delete" ? Array.from(left.text || leftValue).length : 0;
    let insertCount = type === "insert" ? Array.from(right.text || rightValue).length : 0;
    if (type === "change" && left.index === right.index && left.time === right.time) {
      const textDiff = diffChars(left.text, right.text);
      leftHtml = renderSrtBlockHtml(left, textDiff.leftHtml);
      rightHtml = renderSrtBlockHtml(right, textDiff.rightHtml);
      deleteCount = textDiff.deleteCount;
      insertCount = textDiff.insertCount;
    }
    rows.push({ type, left: leftValue, right: rightValue, leftHtml, rightHtml, leftNo: left.index || "", rightNo: right.index || "", deleteCount, insertCount });
  }
  return rows;
}

function summarizeSubtitleDiff(rows) {
  return rows.reduce((summary, row) => {
    summary.deleted += Number(row.deleteCount || 0);
    summary.inserted += Number(row.insertCount || 0);
    if (row.type !== "same") summary.changedRows += 1;
    return summary;
  }, { deleted: 0, inserted: 0, changedRows: 0 });
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

async function openTextFileView(item, kind = "asr") {
  const isSrt = kind === "srt";
  const url = isSrt ? item?.correctedSrtDownloadUrl : item?.downloadUrl;
  if (!url) {
    toast(isSrt ? "暂无修复后的SRT文件" : "暂无ASR文件");
    return;
  }
  const dialog = document.getElementById("audioAsrViewDialog");
  const titleEl = document.getElementById("audioAsrViewTitle");
  const contentEl = document.getElementById("audioAsrViewContent");
  const copyBtn = document.getElementById("audioAsrCopyBtn");
  if (!dialog || !titleEl || !contentEl) return;
  titleEl.textContent = `${isSrt ? "查看修复字幕" : "查看ASR"} · 第${String(item.chapterNum).padStart(3, "0")}回 ${item.title || ""}`;
  contentEl.textContent = "加载中...";
  if (copyBtn) copyBtn.disabled = true;
  dialog.showModal();
  try {
    const res = await fetch(url, { cache: "no-store" });
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

async function openAsrView(item) {
  return openTextFileView(item, "asr");
}

async function openCorrectedSrtView(item) {
  return openTextFileView(item, "srt");
}

async function openSubtitleCompare(item) {
  if (!item?.downloadUrl || !item?.correctedSrtDownloadUrl) {
    toast("暂无可对比的字幕文件");
    return;
  }
  const dialog = document.getElementById("audioAsrCompareDialog");
  const titleEl = document.getElementById("audioAsrCompareTitle");
  const summaryEl = document.getElementById("audioAsrCompareSummary");
  const bodyEl = document.getElementById("audioAsrCompareBody");
  if (!dialog || !titleEl || !bodyEl) return;
  titleEl.textContent = `字幕对比 · 第${String(item.chapterNum).padStart(3, "0")}回 ${item.title || ""}`;
  if (summaryEl) summaryEl.textContent = "正在统计差异...";
  bodyEl.innerHTML = '<div class="empty-text">加载中...</div>';
  dialog.showModal();
  try {
    const [asrRes, srtRes] = await Promise.all([
      fetch(item.downloadUrl, { cache: "no-store" }),
      fetch(item.correctedSrtDownloadUrl, { cache: "no-store" }),
    ]);
    if (!asrRes.ok) throw new Error(`ASR HTTP ${asrRes.status}`);
    if (!srtRes.ok) throw new Error(`SRT HTTP ${srtRes.status}`);
    const rows = diffSrtBlocks(await asrRes.text(), await srtRes.text());
    const summary = summarizeSubtitleDiff(rows);
    if (summaryEl) {
      summaryEl.textContent = `删除 ${summary.deleted} 个字符 · 新增 ${summary.inserted} 个字符 · 变更 ${summary.changedRows} 段字幕`;
    }
    bodyEl.innerHTML = rows.map((row) => `
      <div class="subtitle-diff-row subtitle-diff-${row.type}">
        <div class="subtitle-diff-line-no">${escapeHtml(row.leftNo)}</div>
        <pre class="subtitle-diff-cell">${row.leftHtml || escapeHtml(row.left)}</pre>
        <div class="subtitle-diff-line-no">${escapeHtml(row.rightNo)}</div>
        <pre class="subtitle-diff-cell">${row.rightHtml || escapeHtml(row.right)}</pre>
      </div>
    `).join("");
  } catch (err) {
    if (summaryEl) summaryEl.textContent = "差异统计失败";
    bodyEl.innerHTML = `<div class="empty-text">加载失败：${escapeHtml(err.message)}</div>`;
  }
}

function renderSubtitleFixCell(item) {
  const status = String(item.subtitleFixStatus || "");
  const error = String(item.subtitleFixError || "");
  if (item.hasCorrectedSrt) {
    return `<div class="table-actions-inline"><a class="ghost-btn btn-sm" href="${item.correctedSrtDownloadUrl}">下载SRT</a><button class="ghost-btn btn-sm audio-asr-srt-view-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}">查看</button><button class="ghost-btn btn-sm audio-asr-compare-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}">对比</button><button class="ghost-btn btn-sm audio-asr-repair-subtitle-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}">重新修复</button></div>${error ? `<div class="meta">${escapeHtml(error)}</div>` : ""}`;
  }
  if (status === "pending") {
    const progress = item.subtitleFixTotalBatchCount ? ` ${Number(item.subtitleFixCurrentBatchIndex || 0)}/${Number(item.subtitleFixTotalBatchCount || 0)}` : "";
    return `<div class="table-actions-inline"><span class="status-badge status-pending">待修复${progress}</span><button class="ghost-btn btn-sm audio-asr-cancel-subtitle-repair-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}">终止</button></div>`;
  }
  if (status === "processing") {
    const progress = item.subtitleFixTotalBatchCount ? ` ${Number(item.subtitleFixCurrentBatchIndex || 0)}/${Number(item.subtitleFixTotalBatchCount || 0)}` : "";
    return `<div class="table-actions-inline"><span class="status-badge status-pending">修复中${progress}</span><button class="ghost-btn btn-sm audio-asr-cancel-subtitle-repair-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}">终止</button></div>`;
  }
  if (status === "cancelled") {
    return `<div class="table-actions-inline"><button class="ghost-btn btn-sm audio-asr-repair-subtitle-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}" ${item.hasAsr ? "" : "disabled"}>重试修复</button></div><div class="meta">${escapeHtml(error || "字幕修复已终止")}</div>`;
  }
  if (status === "failed") {
    return `<div class="table-actions-inline"><button class="ghost-btn btn-sm audio-asr-repair-subtitle-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}" ${item.hasAsr ? "" : "disabled"}>重试修复</button></div><div class="meta">${escapeHtml(error || "修复失败")}</div>`;
  }
  return item.hasAsr
    ? `<button class="ghost-btn btn-sm audio-asr-repair-subtitle-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}">修复字幕</button>`
    : '<span class="text-muted">暂无</span>';
}

function renderTable() {
  const tbody = document.getElementById("audioAsrTableBody");
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
      <td>${renderSubtitleFixCell(item)}</td>
      <td><button class="ghost-btn btn-sm audio-asr-single-btn" type="button" data-chapter-num="${Number(item.chapterNum || 0)}" data-status="${escapeHtml(item.status || "idle")}" ${item.hasAudio ? "" : "disabled"}>${getActionLabel(item)}</button></td>
    </tr>
  `).join("");
  updateSelectionControls();
  scheduleAutoRefreshIfNeeded();
}

function hasActiveTasks() {
  return chapterItems.some((item) => (
    ["pending", "running", "processing"].includes(String(item.status || ""))
    || ["pending", "processing"].includes(String(item.subtitleFixStatus || ""))
  ));
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

async function onRepairSubtitle(chapterNum) {
  if (!activeNovel) return;
  const item = chapterItems.find((entry) => Number(entry.chapterNum || 0) === Number(chapterNum || 0));
  if (!item?.hasAsr) {
    toast("请先提取ASR");
    return;
  }
  if (!window.confirm(`确认修复第 ${String(chapterNum).padStart(3, "0")} 回字幕错误？`)) return;
  const btn = document.querySelector(`.audio-asr-repair-subtitle-btn[data-chapter-num="${Number(chapterNum)}"]`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = "修复中...";
  }
  try {
    await repairChapterAudioAsrSubtitle(activeNovel.id, chapterNum);
    toast("字幕修复已加入队列");
    await refreshPage();
  } catch (err) {
    toast(`字幕修复失败：${err.message}`);
    await refreshPage().catch(() => {});
  }
}

async function onBatchRepairSubtitle(chapterNums) {
  if (!activeNovel) return;
  if (!chapterNums.length) {
    toast("请先选择要修复字幕的章回");
    return;
  }
  const selectedItems = chapterNums
    .map((chapterNum) => chapterItems.find((entry) => Number(entry.chapterNum || 0) === Number(chapterNum || 0)))
    .filter(Boolean);
  const repairableItems = selectedItems.filter((item) => (
    item.hasAsr
    && !["pending", "processing"].includes(String(item.subtitleFixStatus || ""))
  ));
  if (!repairableItems.length) {
    toast("所选章回没有可修复的ASR字幕");
    return;
  }
  const skipped = selectedItems.length - repairableItems.length;
  const confirmText = skipped > 0
    ? `确认批量修复 ${repairableItems.length} 回字幕？将跳过 ${skipped} 回无ASR或正在修复的章回。`
    : `确认批量修复 ${repairableItems.length} 回字幕？`;
  if (!window.confirm(confirmText)) return;

  const btn = document.getElementById("audioAsrBatchRepairSubtitleBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "入队中...";
  }
  let queued = 0;
  let failed = 0;
  for (const item of repairableItems) {
    try {
      await repairChapterAudioAsrSubtitle(activeNovel.id, Number(item.chapterNum || 0));
      queued += 1;
    } catch {
      failed += 1;
    }
  }
  if (btn) {
    btn.disabled = false;
    btn.textContent = "批量修复字幕";
  }
  toast(`字幕修复已入队 ${queued} 回${skipped ? `，跳过 ${skipped} 回` : ""}${failed ? `，失败 ${failed} 回` : ""}`);
  await refreshPage();
}

async function onCancelSubtitleRepair(chapterNum) {
  if (!activeNovel) return;
  if (!window.confirm(`确认终止第 ${String(chapterNum).padStart(3, "0")} 回字幕修复任务？`)) return;
  try {
    await cancelChapterAudioAsrSubtitleRepair(activeNovel.id, chapterNum);
    toast("字幕修复已终止");
    await refreshPage();
  } catch (err) {
    toast(`终止失败：${err.message}`);
    await refreshPage().catch(() => {});
  }
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
    const srtViewBtn = event.target.closest(".audio-asr-srt-view-btn");
    if (srtViewBtn) {
      const item = chapterItems.find((entry) => Number(entry.chapterNum || 0) === Number(srtViewBtn.dataset.chapterNum || 0));
      if (item) {
        await openCorrectedSrtView(item);
      }
      return;
    }
    const repairBtn = event.target.closest(".audio-asr-repair-subtitle-btn");
    if (repairBtn) {
      await onRepairSubtitle(Number(repairBtn.dataset.chapterNum || 0));
      return;
    }
    const cancelRepairBtn = event.target.closest(".audio-asr-cancel-subtitle-repair-btn");
    if (cancelRepairBtn) {
      await onCancelSubtitleRepair(Number(cancelRepairBtn.dataset.chapterNum || 0));
      return;
    }
    const compareBtn = event.target.closest(".audio-asr-compare-btn");
    if (compareBtn) {
      const item = chapterItems.find((entry) => Number(entry.chapterNum || 0) === Number(compareBtn.dataset.chapterNum || 0));
      if (item) {
        await openSubtitleCompare(item);
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
  document.getElementById("audioAsrBatchRepairSubtitleBtn").addEventListener("click", async () => {
    await onBatchRepairSubtitle(getSelectedChapterNums());
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
