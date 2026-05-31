import {
  enqueueChapterIllustration,
  enqueueAllIllustrationImages,
  enqueueIllustrationImage,
  fetchChapterIllustrationImages,
  fetchChapterIllustrationPayload,
  fetchIllustrationWorkerStatus,
  fetchNovelIllustrationChapters,
  getActiveNovelId,
  getData,
  restartIllustrationWorker,
  setActiveNovelId,
} from "./store.js";
import { renderNav, toast } from "./ui.js";

let allNovels = [];
let activeNovel = null;
let chapterItems = [];
let autoRefreshTimer = 0;
let imagesRefreshTimer = 0;
let activeImagesChapterNum = 0;
let currentPreviewItems = [];
let currentPreviewIndex = -1;
const selectedChapterNums = new Set();
let dragSelecting = false;
let dragSelectValue = true;
const IMAGES_REFRESH_KEY = "ai_novel_illustration_images_refresh_seconds";

const STAGE_LABELS = {
  scene: "Scene",
  shot: "Shot",
  prompt: "Prompt",
};

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

function renderNovelSelect() {
  const select = document.getElementById("illustrationNovelSelect");
  select.innerHTML = allNovels.map((novel) => `<option value="${novel.id}">${escapeHtml(novel.name)}</option>`).join("");
  if (activeNovel) select.value = String(activeNovel.id);
}

function setHeader() {
  const titleEl = document.getElementById("illustrationPageTitle");
  const metaEl = document.getElementById("illustrationPageMeta");
  const summaryEl = document.getElementById("illustrationSummary");
  const chaptersLink = document.getElementById("illustrationChaptersLink");
  if (!activeNovel) {
    titleEl.textContent = "生成插画";
    metaEl.textContent = "未找到小说";
    summaryEl.textContent = "-";
    return;
  }
  const completed = chapterItems.reduce((sum, item) => {
    return sum + ["scene", "shot", "prompt"].filter((stage) => item.stages?.[stage]?.status === "completed").length;
  }, 0);
  titleEl.textContent = `${activeNovel.name} - 生成插画`;
  metaEl.textContent = `共 ${chapterItems.length} 回 · 已完成 ${completed}/${chapterItems.length * 3} 项`;
  summaryEl.textContent = `每回依次解析 scene.json、shot.json、prompt.json`;
  chaptersLink.href = `./chapters.html?novelId=${encodeURIComponent(activeNovel.id)}`;
}

function renderStageCell(item, stage) {
  const data = item.stages?.[stage] || { status: "idle", progress: 0, errorMessage: "" };
  const chapterNum = Number(item.chapterNum || 0);
  const progress = Number(data.progress || 0);
  const disabled = ["pending", "running", "processing"].includes(String(data.status || ""));
  return `
    <div class="stage-cell">
      <span class="${statusClass(data.status)}" title="${escapeHtml(data.errorMessage || "")}">${statusLabel(data.status)}${progress ? ` ${progress}%` : ""}</span>
      <div class="table-actions-inline">
        <button class="ghost-btn btn-sm illustration-run-btn" type="button" data-stage="${stage}" data-chapter-num="${chapterNum}" ${disabled ? "disabled" : ""}>解析插画${STAGE_LABELS[stage].toLowerCase()}</button>
        <button class="ghost-btn btn-sm illustration-view-btn" type="button" data-kind="input" data-stage="${stage}" data-chapter-num="${chapterNum}">输入</button>
        <button class="ghost-btn btn-sm illustration-view-btn" type="button" data-kind="output" data-stage="${stage}" data-chapter-num="${chapterNum}">输出</button>
        ${stage === "prompt" && data.status === "completed" ? renderImagesButton(item, chapterNum) : ""}
      </div>
    </div>
  `;
}

function renderImagesButton(item, chapterNum) {
  const missing = Number(item.images?.missing || 0);
  const expected = Number(item.images?.expected || 0);
  const generated = Number(item.images?.generated || 0);
  const title = missing > 0 ? `插图未生成 ${missing} 张（${generated}/${expected}）` : "插图";
  return `
    <button class="ghost-btn btn-sm illustration-images-btn ${missing > 0 ? "has-missing-images" : ""}" type="button" data-chapter-num="${chapterNum}" title="${escapeHtml(title)}">
      插图
      ${missing > 0 ? '<span class="illustration-alert-dot" aria-hidden="true">!</span>' : ""}
    </button>
  `;
}

function renderTable() {
  const tbody = document.getElementById("illustrationTableBody");
  if (!activeNovel) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-text">未找到小说</td></tr>';
    updateSelectionUi();
    return;
  }
  if (!chapterItems.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-text">暂无章回数据</td></tr>';
    updateSelectionUi();
    return;
  }
  const available = new Set(chapterItems.map((item) => Number(item.chapterNum || 0)));
  Array.from(selectedChapterNums).forEach((chapterNum) => {
    if (!available.has(chapterNum)) selectedChapterNums.delete(chapterNum);
  });
  tbody.innerHTML = chapterItems.map((item) => `
    <tr class="illustration-select-row ${selectedChapterNums.has(Number(item.chapterNum || 0)) ? "is-selected" : ""}" data-chapter-num="${Number(item.chapterNum || 0)}">
      <td class="select-col"><input class="illustration-row-select" type="checkbox" data-chapter-num="${Number(item.chapterNum || 0)}" ${selectedChapterNums.has(Number(item.chapterNum || 0)) ? "checked" : ""} /></td>
      <td>第 ${String(item.chapterNum || 0).padStart(3, "0")} 回</td>
      <td>${escapeHtml(item.title || "")}</td>
      <td>${formatTimeSeconds(item.audioDurationSeconds || 0)}</td>
      <td>${Number(item.wordCount || 0).toLocaleString()}</td>
      <td>${renderStageCell(item, "scene")}</td>
      <td>${renderStageCell(item, "shot")}</td>
      <td>${renderStageCell(item, "prompt")}</td>
    </tr>
  `).join("");
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

function renderWorkerStatus(status) {
  const el = document.getElementById("illustrationWorkerStatus");
  const state = String(status?.state || "stopped");
  const mapping = { running: "运行中", stale: "心跳超时", stopped: "未运行" };
  const age = status?.heartbeatAgeSeconds != null ? ` · 心跳${status.heartbeatAgeSeconds}s` : "";
  el.textContent = `Worker: ${mapping[state] || state}${age}`;
}

async function refreshWorkerStatus() {
  try {
    renderWorkerStatus(await fetchIllustrationWorkerStatus());
  } catch {
    renderWorkerStatus({ state: "stopped" });
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

function imageTimeLabel(item) {
  const start = Number(item?.start);
  const end = Number(item?.end);
  const duration = Number(item?.duration);
  if (item?.start == null || item?.end == null || !Number.isFinite(start) || !Number.isFinite(end)) return "";
  const safeDuration = Number.isFinite(duration) ? duration : Math.max(0, end - start);
  return `时间：${formatTimeSeconds(start)} - ${formatTimeSeconds(end)} · 持续 ${formatTimeSeconds(safeDuration)}`;
}

async function openPayload(chapterNum, stage, kind) {
  const dialog = document.getElementById("illustrationPayloadDialog");
  const title = document.getElementById("illustrationPayloadTitle");
  const content = document.getElementById("illustrationPayloadContent");
  title.textContent = `${STAGE_LABELS[stage]} ${kind === "input" ? "输入" : "输出"} · 第${String(chapterNum).padStart(3, "0")}回`;
  content.textContent = "加载中...";
  dialog.showModal();
  try {
    const payload = await fetchChapterIllustrationPayload(activeNovel.id, chapterNum, stage, kind);
    const raw = String(payload.text || "");
    title.textContent = `${STAGE_LABELS[stage]} ${kind === "input" ? "输入" : "输出"} · 第${String(chapterNum).padStart(3, "0")}回 · 字数 ${raw.length.toLocaleString()}`;
    content.textContent = raw ? formatMaybeJson(raw) : "暂无内容";
  } catch (err) {
    content.textContent = `加载失败：${err.message}`;
  }
}

function renderImages(items) {
  currentPreviewItems = items.filter((item) => Boolean(item.imageUrl));
  const root = document.getElementById("illustrationImagesList");
  if (!items.length) {
    root.innerHTML = '<p class="empty-text">暂无 prompt.json 插图数据</p>';
    return;
  }
  root.innerHTML = items.map((item) => {
    const hasImage = Boolean(item.imageUrl);
    const busy = ["pending", "running", "processing"].includes(String(item.status || ""));
    return `
      <article class="illustration-image-card">
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
        <p class="meta illustration-summary-meta">${escapeHtml(item.cnSummary || "-")}</p>
        <div class="card-actions">
          <button class="ghost-btn btn-sm illustration-generate-image-btn" type="button" data-image-id="${item.id}" ${busy ? "disabled" : ""}>${hasImage ? "重新生成" : "生成"}</button>
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
}

function openPreviewAt(index) {
  if (!currentPreviewItems.length) return;
  const total = currentPreviewItems.length;
  currentPreviewIndex = ((Number(index) || 0) + total) % total;
  const item = currentPreviewItems[currentPreviewIndex];
  const img = document.getElementById("illustrationImagePreview");
  const dialog = document.getElementById("illustrationImagePreviewDialog");
  const prevBtn = document.getElementById("illustrationPreviewPrevBtn");
  const nextBtn = document.getElementById("illustrationPreviewNextBtn");
  const footerMeta = document.getElementById("illustrationPreviewFooterMeta");
  const positionText = `${currentPreviewIndex + 1}/${total}`;
  const timeText = imageTimeLabel(item);
  prevBtn.disabled = total <= 1;
  nextBtn.disabled = total <= 1;
  document.getElementById("illustrationPreviewTitle").textContent = `#${item.index} ${item.sceneTitle || "预览插图"}`;
  document.getElementById("illustrationPreviewSummary").textContent = item.cnSummary || "";
  document.getElementById("illustrationPreviewCharacters").textContent = item.characterNames ? `人物：${item.characterNames}` : "";
  document.getElementById("illustrationPreviewPrompt").textContent = item.promptText ? `提示词：${item.promptText}` : "";
  footerMeta.textContent = [timeText, item.suggestedSize ? `建议尺寸：${item.suggestedSize}` : "", positionText].filter(Boolean).join(" · ");
  img.onload = () => {
    footerMeta.textContent = [timeText, `尺寸：${img.naturalWidth}x${img.naturalHeight}`, `比例：${ratioLabel(img.naturalWidth, img.naturalHeight)}`, positionText].filter(Boolean).join(" · ");
  };
  img.src = `${item.imageUrl}?v=${Date.now()}`;
  if (!dialog.open) dialog.showModal();
}

function switchPreview(delta) {
  const dialog = document.getElementById("illustrationImagePreviewDialog");
  if (!dialog?.open || !currentPreviewItems.length) return;
  openPreviewAt(currentPreviewIndex + delta);
}

async function refreshImagesModal() {
  if (!document.getElementById("illustrationImagesDialog")?.open) return;
  if (!activeImagesChapterNum) return;
  renderImages(await fetchChapterIllustrationImages(activeNovel.id, activeImagesChapterNum));
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

async function openImagesModal(chapterNum) {
  activeImagesChapterNum = Number(chapterNum || 0);
  const chapter = chapterItems.find((item) => Number(item.chapterNum || 0) === activeImagesChapterNum);
  const duration = Number(chapter?.audioDurationSeconds || 0);
  const durationText = duration > 0 ? ` · 音频时长 ${formatTimeSeconds(duration)}` : "";
  document.getElementById("illustrationImagesTitle").textContent = `插图生成 · 第${String(activeImagesChapterNum).padStart(3, "0")}回${durationText}`;
  document.getElementById("illustrationImagesList").innerHTML = '<p class="empty-text">加载中...</p>';
  document.getElementById("illustrationImagesDialog").showModal();
  applyImagesAutoRefresh();
  await refreshImagesModal();
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
  document.getElementById("restartIllustrationWorkerBtn").addEventListener("click", async () => {
    await restartIllustrationWorker();
    toast("插画Worker已重启");
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
    copyText(document.getElementById("illustrationPayloadContent")?.textContent || "").catch((err) => {
      toast(`复制失败：${err.message}`);
    });
  });
  document.getElementById("generateAllIllustrationImagesBtn").addEventListener("click", async () => {
    if (!activeImagesChapterNum) return;
    const data = await enqueueAllIllustrationImages(activeNovel.id, activeImagesChapterNum);
    toast(`已入队 ${data.queued || 0} 张，跳过 ${data.skipped || 0} 张`);
    await refreshImagesModal();
  });
  document.getElementById("illustrationImagesList").addEventListener("click", async (event) => {
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
  document.addEventListener("keydown", (event) => {
    const dialog = document.getElementById("illustrationImagePreviewDialog");
    if (!dialog?.open || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      switchPreview(-1);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      switchPreview(1);
    }
  });
  document.getElementById("illustrationPreviewPrevBtn").addEventListener("click", () => switchPreview(-1));
  document.getElementById("illustrationPreviewNextBtn").addEventListener("click", () => switchPreview(1));
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
  autoRefreshTimer = window.setInterval(refreshPage, 5000);
  window.addEventListener("beforeunload", () => {
    window.clearInterval(autoRefreshTimer);
    stopImagesAutoRefresh();
  });
}

init().catch((err) => {
  renderNav();
  toast(err.message || "加载失败");
});
