import {
  bytesToText,
  deleteNovel,
  downloadNovelBundle,
  getActiveNovelId,
  getData,
  saveNovel,
  setActiveNovelId,
} from "./store.js";
import { fmtNumber, renderNav, showPageError, toast } from "./ui.js";
import { localizeDocumentText, t } from "./i18n.js";

let editingId = "";
let refreshTimer = null;
let currentData = { novels: [], prompts: [], workflows: [] };
const REFRESH_INTERVAL_KEY = "ai_novel_index_refresh_interval";

function isValidEnglishDir(value) {
  return /^[A-Za-z0-9_]{1,25}$/.test(String(value || ""));
}

function renderMetrics(data) {
  const totalWords = data.novels.reduce((s, n) => s + n.totalWords, 0);
  const totalChapters = data.novels.reduce((s, n) => s + n.chapterCount, 0);
  const totalTxt = data.novels.reduce((s, n) => s + (n.storage?.txtBytes || 0), 0);
  const totalAudio = data.novels.reduce((s, n) => s + (n.storage?.audioBytes || 0), 0);
  const totalTemp = data.novels.reduce((s, n) => s + (n.storage?.tempBytes || 0), 0);
  document.getElementById("projectMetrics").innerHTML = `
    <div class="metric"><span>小说数</span><strong>${fmtNumber(data.novels.length)}</strong></div>
    <div class="metric"><span>章节总数</span><strong>${fmtNumber(totalChapters)}</strong></div>
    <div class="metric"><span>总字数</span><strong>${fmtNumber(totalWords)}</strong></div>
    <div class="metric"><span>本地存储</span><strong>${bytesToText(totalTxt + totalAudio)}</strong></div>
    <div class="metric"><span>Temp存储</span><strong>${bytesToText(totalTemp)}</strong></div>
  `;
}

function progressBar(value) {
  return `<div class="progress"><i style="width:${value}%"></i></div>`;
}

function renderNovelCards() {
  const data = currentData;
  const keyword = document.getElementById("novelKeyword").value.trim().toLowerCase();
  const sort = document.getElementById("novelSort").value;
  const promptMap = Object.fromEntries(data.prompts.map((p) => [String(p.id), p.name]));
  const workflowMap = Object.fromEntries(data.workflows.map((w) => [String(w.id), w.name]));

  let list = data.novels.filter((n) => `${n.name}${n.author}`.toLowerCase().includes(keyword));
  if (sort === "chapters") list = list.sort((a, b) => b.chapterCount - a.chapterCount);
  if (sort === "words") list = list.sort((a, b) => b.totalWords - a.totalWords);
  if (sort === "updated") list = list.sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));

  document.getElementById("novelGrid").innerHTML = list
    .map(
      (n) => `
      <article class="novel-card">
        <button class="ghost-btn novel-delete-btn" data-action="delete" data-id="${n.id}" title="删除小说" aria-label="删除小说" type="button">✕</button>
        <div class="novel-card-head"><div class="novel-title-row"><h3>${n.name}</h3><button class="ghost-btn novel-edit-btn" data-action="edit" data-id="${n.id}" title="编辑小说" aria-label="编辑小说" type="button">✎</button></div><p class="meta">${n.author}</p></div>
        <p class="novel-intro" title="${n.intro || ""}">${n.intro || ""}</p>
        <div class="chips">
          <span class="chip">章节 ${fmtNumber(n.chapterCount)}</span>
          <span class="chip">字数 ${fmtNumber(n.totalWords)}</span>
          <span class="chip">英文目录: ${n.englishDir || "-"}</span>
          <span class="chip">提示词: ${promptMap[String(n.promptId)] || "-"}</span>
          <span class="chip">示例音频工作流: ${workflowMap[String(n.voiceSampleWorkflowId)] || "-"}</span>
          <span class="chip">台词音频工作流: ${workflowMap[String(n.lineAudioWorkflowId)] || "-"}</span>
          <span class="chip">提取文本工作流: ${workflowMap[String(n.voiceTranscribeWorkflowId)] || "-"}</span>
        </div>
        <div><p class="meta">JSON处理 ${n.jsonProgress}%</p>${progressBar(n.jsonProgress)}</div>
        <div><p class="meta">音频生成 ${n.audioProgress}%</p>${progressBar(n.audioProgress)}</div>
        <div class="card-actions">
          <button class="ghost-btn" data-action="chapters" data-id="${n.id}">章节管理</button>
          <button class="ghost-btn" data-action="download" data-id="${n.id}">打包下载</button>
        </div>
      </article>
    `
    )
    .join("");

  document.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => onNovelAction(btn.dataset.action, btn.dataset.id));
  });
  localizeDocumentText(document);
}

function renderStorageTable(data) {
  const rows = data.novels
    .map(
      (n) => `
      <div class="storage-row">
        <span>${n.name}</span>
        <span>${bytesToText(n.storage?.txtBytes || 0)}</span>
        <span>${bytesToText(n.storage?.audioBytes || 0)}</span>
        <span>${bytesToText(n.storage?.tempBytes || 0)}</span>
      </div>
    `
    )
    .join("");
  document.getElementById("storageTable").innerHTML = `<div class="storage-row head"><span>小说</span><span>txt</span><span>音频</span><span>Temp存储</span></div>${rows}`;
  localizeDocumentText(document);
}

function openNovelModal(novel) {
  const modal = document.getElementById("novelModal");
  const form = document.getElementById("novelForm");
  editingId = novel?.id || "";
  document.getElementById("novelModalTitle").textContent = editingId ? "编辑小说" : "创建小说";

  document.getElementById("novelPromptSelect").innerHTML = currentData.prompts
    .map((p) => `<option value="${p.id}">${p.name}</option>`)
    .join("");
  
  // 仅按 workflowType 精确筛选，避免不同类型工作流串到错误下拉里
  const voiceSampleWorkflows = currentData.workflows.filter(
    (w) => String(w.workflowType || "").trim() === "voice_sample"
  );
  const lineAudioWorkflows = currentData.workflows.filter(
    (w) => String(w.workflowType || "").trim() === "line_audio"
  );
  const voiceTranscribeWorkflows = currentData.workflows.filter(
    (w) => String(w.workflowType || "").trim() === "voice_transcribe"
  );
  
  document.getElementById("voiceSampleWorkflowSelect").innerHTML = voiceSampleWorkflows
    .map((w) => `<option value="${w.id}">${w.name}</option>`)
    .join("");
  document.getElementById("lineAudioWorkflowSelect").innerHTML = lineAudioWorkflows
    .map((w) => `<option value="${w.id}">${w.name}</option>`)
    .join("");
  document.getElementById("voiceTranscribeWorkflowSelect").innerHTML = voiceTranscribeWorkflows
    .map((w) => `<option value="${w.id}">${w.name}</option>`)
    .join("");

  form.name.value = novel?.name || "";
  form.author.value = novel?.author || "";
  form.englishDir.value = novel?.englishDir || "";
  form.intro.value = novel?.intro || "";
  form.promptId.value = novel?.promptId || currentData.prompts[0]?.id || "";
  form.voiceSampleWorkflowId.value = novel?.voiceSampleWorkflowId || voiceSampleWorkflows[0]?.id || "";
  form.lineAudioWorkflowId.value = novel?.lineAudioWorkflowId || lineAudioWorkflows[0]?.id || "";
  form.voiceTranscribeWorkflowId.value = novel?.voiceTranscribeWorkflowId || voiceTranscribeWorkflows[0]?.id || "";
  localizeDocumentText(document);
  modal.showModal();
}

function closeNovelModal() {
  document.getElementById("novelModal").close();
}

async function onNovelAction(action, id) {
  const novel = currentData.novels.find((n) => String(n.id) === String(id));
  if (!novel) return;
  try {
    if (action === "edit") openNovelModal(novel);
    if (action === "download") {
      await downloadNovelBundle(id);
      toast(t("toast.created"));
    }
    if (action === "chapters") {
      setActiveNovelId(id);
      window.location.href = `./chapters.html?novelId=${encodeURIComponent(id)}`;
    }
    if (action === "delete") {
      if (!window.confirm(t("confirm.deleteNovel", { name: novel.name }))) return;
      await deleteNovel(id);
      toast(t("toast.deleted"));
      await refresh();
    }
  } catch (err) {
    toast(t("error.operationFailed", { msg: err.message }));
  }
}

async function refresh() {
  currentData = await getData();
  const activeId = getActiveNovelId();
  if (currentData.novels.length && !currentData.novels.some((n) => String(n.id) === String(activeId || ""))) {
    setActiveNovelId(currentData.novels[0].id);
  }
  renderMetrics(currentData);
  renderNovelCards();
  renderStorageTable(currentData);
}

function applyAutoRefresh() {
  const select = document.getElementById("autoRefreshSelect");
  if (!select) return;
  if (refreshTimer) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
  const seconds = Number(select.value || 0);
  localStorage.setItem(REFRESH_INTERVAL_KEY, String(seconds));
  if (!Number.isFinite(seconds) || seconds <= 0) return;
  refreshTimer = window.setInterval(() => {
    refresh().catch(() => {
      // ignore
    });
  }, seconds * 1000);
}

function initAutoRefresh() {
  const select = document.getElementById("autoRefreshSelect");
  if (!select) return;
  const saved = localStorage.getItem(REFRESH_INTERVAL_KEY);
  if (saved != null && ["0", "5", "20", "60"].includes(saved)) select.value = saved;
  applyAutoRefresh();
}

function bindEvents() {
  document.getElementById("createNovelBtn").addEventListener("click", () => openNovelModal());
  document.getElementById("novelKeyword").addEventListener("input", renderNovelCards);
  document.getElementById("novelSort").addEventListener("change", renderNovelCards);
  document.getElementById("autoRefreshSelect").addEventListener("change", applyAutoRefresh);
  document.getElementById("novelCancelBtn").addEventListener("click", closeNovelModal);

  document.getElementById("novelForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const englishDir = String(form.englishDir.value || "").trim();
    if (!isValidEnglishDir(englishDir)) {
      toast(t("error.invalidEnglishDir"));
      form.englishDir.focus();
      return;
    }
    try {
      await saveNovel(
        {
          name: form.name.value,
          author: form.author.value,
          englishDir,
          intro: form.intro.value,
          promptId: form.promptId.value,
          voiceSampleWorkflowId: form.voiceSampleWorkflowId.value,
          lineAudioWorkflowId: form.lineAudioWorkflowId.value,
          voiceTranscribeWorkflowId: form.voiceTranscribeWorkflowId.value,
        },
        editingId
      );
      closeNovelModal();
      toast(editingId ? t("toast.updated") : t("toast.created"));
      await refresh();
    } catch (err) {
      toast(t("error.saveFailed", { msg: err.message }));
    }
  });
}

async function init() {
  renderNav();
  bindEvents();
  await refresh();
  localizeDocumentText(document);
  initAutoRefresh();
}

init().catch((err) => {
  renderNav();
  showPageError(err, t("error.pageLoad"));
});
