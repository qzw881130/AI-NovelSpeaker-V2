import {
  bytesToText,
  createNovelBundle,
  deleteNovel,
  deleteNovelBundleFile,
  downloadNovelBundleFile,
  fetchNovelBundleStatus,
  getActiveNovelId,
  getData,
  listNovelBundles,
  refreshNovelAudioDuration,
  saveNovel,
  setActiveNovelId,
} from "./store.js";
import { fmtDateTime, fmtNumber, renderNav, showPageError, toast } from "./ui.js";
import { localizeDocumentText, t, translateText } from "./i18n.js";

let editingId = "";
let refreshTimer = null;
let currentData = { novels: [], prompts: [], workflows: [] };
const REFRESH_INTERVAL_KEY = "ai_novel_index_refresh_interval";
const NOVEL_VISIBILITY_KEY = "ai_novel_index_visibility";
const STORAGE_TABLE_COLLAPSED_KEY = "ai_novel_index_storage_table_collapsed";
let activeBundleNovelId = "";
let bundleTaskTimer = 0;

function getNovelVisibilityMap() {
  try {
    const raw = JSON.parse(localStorage.getItem(NOVEL_VISIBILITY_KEY) || "{}");
    return raw && typeof raw === "object" ? raw : {};
  } catch {
    return {};
  }
}

function saveNovelVisibilityMap(map) {
  localStorage.setItem(NOVEL_VISIBILITY_KEY, JSON.stringify(map || {}));
}

function isNovelMasked(novelId) {
  return Boolean(getNovelVisibilityMap()[String(novelId)]);
}

function setNovelMasked(novelId, masked) {
  const map = getNovelVisibilityMap();
  map[String(novelId)] = Boolean(masked);
  saveNovelVisibilityMap(map);
}

function maskNovelText(value) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .replace(/[^\s]/g, "*");
}

function isStorageTableCollapsed() {
  return localStorage.getItem(STORAGE_TABLE_COLLAPSED_KEY) === "1";
}

function applyStorageTableCollapsedState() {
  const collapsed = isStorageTableCollapsed();
  const table = document.getElementById("storageTable");
  const btn = document.getElementById("toggleStorageTableBtn");
  if (table) table.classList.toggle("hidden", collapsed);
  if (btn) {
    btn.textContent = collapsed ? "展开" : "折叠";
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }
}

function setBundleControlsBusy(busy) {
  const btn = document.getElementById("bundleCreateBtn");
  const presetSelect = document.getElementById("bundleAudioPresetSelect");
  const variantSelect = document.getElementById("bundleAudioVariantSelect");
  if (btn) {
    btn.disabled = Boolean(busy);
    btn.textContent = busy ? translateText("打包中...") : translateText("打包");
  }
  if (presetSelect) presetSelect.disabled = Boolean(busy);
  if (variantSelect) variantSelect.disabled = Boolean(busy);
}

const BUNDLE_AUDIO_PRESET_LABELS = {
  lossless: "无损格式",
  "mp3-128k": "MP3 128k【高音质】",
  "mp3-96k": "MP3 96k【推荐】",
  "mp3-64k": "MP3 64k【有声书最佳平衡】",
  "mp3-48k-mono": "MP3 48k Mono【老人机神器】",
};

const BUNDLE_AUDIO_PRESET_BITRATES = {
  "mp3-128k": 128000,
  "mp3-96k": 96000,
  "mp3-64k": 64000,
  "mp3-48k-mono": 48000,
};

const BUNDLE_AUDIO_VARIANT_LABELS = {
  ver: "有版权",
  nonver: "无版权",
};

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

function formatDuration(totalSeconds) {
  const safe = Math.max(0, Math.round(Number(totalSeconds) || 0));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = safe % 60;
  const parts = [];
  if (hours > 0) parts.push(`${hours}小时`);
  if (hours > 0 || minutes > 0) parts.push(`${minutes}分钟`);
  parts.push(`${seconds}秒`);
  return parts.join("");
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
      (n) => {
        const masked = isNovelMasked(n.id);
        const displayName = masked ? maskNovelText(n.name) : n.name;
        const displayAuthor = masked ? maskNovelText(n.author) : n.author;
        const displayIntro = masked ? maskNovelText(n.intro || "") : (n.intro || "");
        const visibilityTitle = masked ? "显示小说信息" : "隐藏小说信息";
        const visibilityIcon = masked ? "👁" : "🙈";
        return `
      <article class="novel-card">
        <button class="ghost-btn novel-delete-btn" data-action="delete" data-id="${n.id}" title="删除小说" aria-label="删除小说" type="button">✕</button>
        <div class="novel-card-head"><div class="novel-title-row"><h3>${displayName}</h3><button class="ghost-btn novel-edit-btn" data-action="edit" data-id="${n.id}" title="编辑小说" aria-label="编辑小说" type="button">✎</button><button class="ghost-btn novel-visibility-btn" data-action="toggle-visibility" data-id="${n.id}" title="${visibilityTitle}" aria-label="${visibilityTitle}" type="button">${visibilityIcon}</button></div><p class="meta">${displayAuthor}</p></div>
        <p class="novel-intro" title="${displayIntro}">${displayIntro}</p>
        <div class="chips">
          <span class="chip">章节 ${fmtNumber(n.chapterCount)}</span>
          <span class="chip">字数 ${fmtNumber(n.totalWords)}</span>
          <span class="chip">英文目录: ${n.englishDir || "-"}</span>
          <span class="chip">提示词: ${promptMap[String(n.promptId)] || "-"}</span>
          <span class="chip">插画Scene提示词: ${promptMap[String(n.illustrationScenePromptId)] || "-"}</span>
          <span class="chip">插画Shot提示词: ${promptMap[String(n.illustrationShotPromptId)] || "-"}</span>
          <span class="chip">插画Prompt提示词: ${promptMap[String(n.illustrationPromptPromptId)] || "-"}</span>
          <span class="chip">示例音频工作流: ${workflowMap[String(n.voiceSampleWorkflowId)] || "-"}</span>
          <span class="chip">台词音频工作流: ${workflowMap[String(n.lineAudioWorkflowId)] || "-"}</span>
          <span class="chip">提取文本工作流: ${workflowMap[String(n.voiceTranscribeWorkflowId)] || "-"}</span>
        </div>
        <div><p class="meta">JSON处理 ${n.jsonProgress}%</p>${progressBar(n.jsonProgress)}</div>
        <div><p class="meta">音频生成 ${n.audioProgress}%</p>${progressBar(n.audioProgress)}</div>
        <div class="novel-duration-row">
          <p class="meta">总时长：${formatDuration(n.totalAudioDurationSeconds || 0)}</p>
          <button class="ghost-btn icon-btn" data-action="refresh-audio-duration" data-id="${n.id}" title="刷新总时长" aria-label="刷新总时长" type="button">↻</button>
        </div>
        <div class="card-actions">
          <button class="ghost-btn" data-action="chapters" data-id="${n.id}">章节管理</button>
          <button class="ghost-btn" data-action="download" data-id="${n.id}">打包下载</button>
        </div>
      </article>
    `
      }
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
        <span>${formatDuration(n.totalAudioDurationSeconds || 0)}</span>
      </div>
    `
    )
    .join("");
  document.getElementById("storageTable").innerHTML = `<div class="storage-row head"><span>小说</span><span>txt</span><span>音频</span><span>Temp存储</span><span>总时长</span></div>${rows}`;
  localizeDocumentText(document);
}

function openNovelModal(novel) {
  const modal = document.getElementById("novelModal");
  const form = document.getElementById("novelForm");
  editingId = novel?.id || "";
  document.getElementById("novelModalTitle").textContent = editingId ? "编辑小说" : "创建小说";

  const jsonParsePrompts = currentData.prompts.filter(
    (p) => String(p.category || "json_parse").trim() === "json_parse"
  );
  const nsfwPrompts = currentData.prompts.filter(
    (p) => String(p.category || "json_parse").trim() === "nsfw_review"
  );
  const illustrationScenePrompts = currentData.prompts.filter(
    (p) => String(p.category || "json_parse").trim() === "illustration_scene"
  );
  const illustrationShotPrompts = currentData.prompts.filter(
    (p) => String(p.category || "json_parse").trim() === "illustration_shot"
  );
  const illustrationPromptPrompts = currentData.prompts.filter(
    (p) => String(p.category || "json_parse").trim() === "illustration_prompt"
  );

  document.getElementById("novelPromptSelect").innerHTML = jsonParsePrompts
    .map((p) => `<option value="${p.id}">${p.name}</option>`)
    .join("");
  document.getElementById("novelNsfwPromptSelect").innerHTML = nsfwPrompts
    .map((p) => `<option value="${p.id}">${p.name}</option>`)
    .join("");
  document.getElementById("novelIllustrationScenePromptSelect").innerHTML = illustrationScenePrompts
    .map((p) => `<option value="${p.id}">${p.name}</option>`)
    .join("");
  document.getElementById("novelIllustrationShotPromptSelect").innerHTML = illustrationShotPrompts
    .map((p) => `<option value="${p.id}">${p.name}</option>`)
    .join("");
  document.getElementById("novelIllustrationPromptPromptSelect").innerHTML = illustrationPromptPrompts
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
  const audioAsrWorkflows = currentData.workflows.filter(
    (w) => String(w.workflowType || "").trim() === "audio_asr"
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
  document.getElementById("audioAsrWorkflowSelect").innerHTML = audioAsrWorkflows
    .map((w) => `<option value="${w.id}">${w.name}</option>`)
    .join("");

  form.name.value = novel?.name || "";
  form.author.value = novel?.author || "";
  form.englishDir.value = novel?.englishDir || "";
  form.intro.value = novel?.intro || "";
  form.promptId.value = novel?.promptId || jsonParsePrompts[0]?.id || "";
  form.nsfwPromptId.value = novel?.nsfwPromptId || nsfwPrompts.find((p) => p.name === "NSFW审查提示词")?.id || nsfwPrompts[0]?.id || "";
  form.illustrationScenePromptId.value = novel?.illustrationScenePromptId || illustrationScenePrompts.find((p) => p.name === "插画-scene提示词")?.id || illustrationScenePrompts[0]?.id || "";
  form.illustrationShotPromptId.value = novel?.illustrationShotPromptId || illustrationShotPrompts.find((p) => p.name === "插画-shot提示词")?.id || illustrationShotPrompts[0]?.id || "";
  form.illustrationPromptPromptId.value = novel?.illustrationPromptPromptId || illustrationPromptPrompts.find((p) => p.name === "插画-prompt提示词")?.id || illustrationPromptPrompts[0]?.id || "";
  form.voiceSampleWorkflowId.value = novel?.voiceSampleWorkflowId || voiceSampleWorkflows[0]?.id || "";
  form.lineAudioWorkflowId.value = novel?.lineAudioWorkflowId || lineAudioWorkflows[0]?.id || "";
  form.voiceTranscribeWorkflowId.value = novel?.voiceTranscribeWorkflowId || voiceTranscribeWorkflows[0]?.id || "";
  form.audioAsrWorkflowId.value = novel?.audioAsrWorkflowId || audioAsrWorkflows[0]?.id || "";
  localizeDocumentText(document);
  modal.showModal();
}

function closeNovelModal() {
  document.getElementById("novelModal").close();
}

async function openBundleModal(novel) {
  activeBundleNovelId = String(novel.id);
  document.getElementById("bundleModalTitle").textContent = `${novel.name} - ${translateText("打包下载")}`;
  const presetSelect = document.getElementById("bundleAudioPresetSelect");
  const variantSelect = document.getElementById("bundleAudioVariantSelect");
  if (presetSelect && !presetSelect.value) presetSelect.value = "lossless";
  if (variantSelect && !variantSelect.value) variantSelect.value = "ver";
  updateBundleEstimate();
  document.getElementById("bundleList").innerHTML = `<p class="empty-text">${translateText("加载中...")}</p>`;
  document.getElementById("bundleModal").showModal();
  const task = await syncBundleTaskStatus({ silent: true });
  const status = String(task?.status || "idle");
  if (status !== "queued" && status !== "running") {
    await refreshBundleList();
  } else {
    startBundleTaskPolling();
  }
}

function closeBundleModal() {
  if (bundleTaskTimer) {
    window.clearInterval(bundleTaskTimer);
    bundleTaskTimer = 0;
  }
  document.getElementById("bundleModal").close();
}

function setBundleListLoading(task = null) {
  const listEl = document.getElementById("bundleList");
  if (!listEl) return;
  const current = Number(task?.current || 0);
  const total = Number(task?.total || 0);
  const progressText = total > 0 ? ` ${current}/${total}` : "";
  listEl.innerHTML = `<p class="empty-text">${translateText("打包中...")}${progressText}</p>`;
}

function stopBundleTaskPolling() {
  if (!bundleTaskTimer) return;
  window.clearInterval(bundleTaskTimer);
  bundleTaskTimer = 0;
}

async function syncBundleTaskStatus(options = {}) {
  const task = await fetchNovelBundleStatus(activeBundleNovelId);
  if (!task) return null;
  const status = String(task.status || "idle");
  if (status === "queued" || status === "running") {
    setBundleControlsBusy(true);
    setBundleListLoading(task);
    return task;
  }
  stopBundleTaskPolling();
  setBundleControlsBusy(false);
  if (status === "completed") {
    if (!options.silent) toast(t("toast.created"));
    await refreshBundleList();
    return task;
  }
  if (status === "failed") {
    document.getElementById("bundleList").innerHTML = `<p class="empty-text">打包失败：${task.error || "未知错误"}</p>`;
    return task;
  }
  await refreshBundleList();
  return task;
}

function startBundleTaskPolling() {
  stopBundleTaskPolling();
  bundleTaskTimer = window.setInterval(() => {
    if (!activeBundleNovelId) return;
    syncBundleTaskStatus({ silent: true }).catch(() => {
      // ignore
    });
  }, 1000);
}

async function refreshBundleList() {
  const listEl = document.getElementById("bundleList");
  if (!activeBundleNovelId || !listEl) return;
  try {
    const bundles = await listNovelBundles(activeBundleNovelId);
    if (!bundles.length) {
      listEl.innerHTML = `<p class="empty-text">${translateText("暂无打包记录")}</p>`;
      localizeDocumentText(document);
      return;
    }
    listEl.innerHTML = bundles
      .map(
        (bundle) => `
        <div class="bundle-item">
          <div>
            <strong>${bundle.fileName}</strong>
            <p class="meta">${escapeBundleVariantLabel(bundle.audioVariant)} · ${escapeBundlePresetLabel(bundle.audioPreset)} · ${translateText("创建时间")} ${fmtDateTime(bundle.createdAt)} · ${bytesToText(bundle.sizeBytes)}</p>
          </div>
          <div class="bundle-item-actions">
            <button class="ghost-btn bundle-download-btn" data-file="${bundle.fileName}" type="button">${translateText("下载")}</button>
            <button class="ghost-btn danger bundle-delete-btn" data-file="${bundle.fileName}" type="button">${translateText("删除")}</button>
          </div>
        </div>
      `
      )
      .join("");
    listEl.querySelectorAll(".bundle-download-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await downloadNovelBundleFile(activeBundleNovelId, btn.dataset.file || "");
        } catch (err) {
          toast(t("error.operationFailed", { msg: err.message }));
        }
      });
    });
    listEl.querySelectorAll(".bundle-delete-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const fileName = String(btn.dataset.file || "");
        if (!window.confirm(t("confirm.deleteFile", { name: fileName }) || `确认删除 ${fileName} ?`)) return;
        try {
          await deleteNovelBundleFile(activeBundleNovelId, fileName);
          toast(t("toast.deleted"));
          await refreshBundleList();
        } catch (err) {
          toast(t("error.operationFailed", { msg: err.message }));
        }
      });
    });
    localizeDocumentText(document);
  } catch (err) {
    listEl.innerHTML = `<p class="empty-text">${err.message}</p>`;
  }
}

function escapeBundlePresetLabel(audioPreset) {
  const text = BUNDLE_AUDIO_PRESET_LABELS[String(audioPreset || "lossless")] || String(audioPreset || "无损格式");
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function escapeBundleVariantLabel(audioVariant) {
  const text = BUNDLE_AUDIO_VARIANT_LABELS[String(audioVariant || "ver")] || String(audioVariant || "有版权");
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function getActiveBundleNovel() {
  return currentData.novels.find((item) => String(item.id) === String(activeBundleNovelId)) || null;
}

function estimateBundleSizeBytes(novel, audioPreset, audioVariant = "ver") {
  if (!novel) return 0;
  const txtBytes = Number(novel.storage?.txtBytes || 0);
  if (audioPreset === "lossless") {
    return txtBytes + Number(audioVariant === "nonver" ? (novel.storage?.audioNonVerBytes || 0) : (novel.storage?.audioBytes || 0));
  }
  const bitrate = Number(BUNDLE_AUDIO_PRESET_BITRATES[audioPreset] || 0);
  const totalSeconds = Number(audioVariant === "nonver" ? (novel.totalAudioNonVerDurationSeconds || 0) : (novel.totalAudioDurationSeconds || 0));
  const audioBytes = bitrate > 0 ? Math.ceil((totalSeconds * bitrate) / 8) : 0;
  return txtBytes + audioBytes;
}

function updateBundleEstimate() {
  const el = document.getElementById("bundleEstimateMeta");
  const presetSelect = document.getElementById("bundleAudioPresetSelect");
  const variantSelect = document.getElementById("bundleAudioVariantSelect");
  const novel = getActiveBundleNovel();
  if (!el || !presetSelect || !variantSelect || !novel) return;
  const audioPreset = String(presetSelect.value || "lossless");
  const audioVariant = String(variantSelect.value || "ver");
  const rows = Object.keys(BUNDLE_AUDIO_PRESET_LABELS)
    .map((key) => {
      const active = key === audioPreset ? " active" : "";
      const label = BUNDLE_AUDIO_PRESET_LABELS[key] || key;
      const estimatedBytes = estimateBundleSizeBytes(novel, key, audioVariant);
      return `
        <div class="bundle-estimate-row${active}">
          <span>${label}</span>
          <strong>${bytesToText(estimatedBytes)}</strong>
        </div>
      `;
    })
    .join("");
  el.innerHTML = `
    <p class="meta">预估体积对比 · ${escapeBundleVariantLabel(audioVariant)}</p>
    <div class="bundle-estimate-table">${rows}</div>
  `;
}

async function onNovelAction(action, id) {
  const novel = currentData.novels.find((n) => String(n.id) === String(id));
  if (!novel) return;
  try {
    if (action === "edit") openNovelModal(novel);
    if (action === "toggle-visibility") {
      setNovelMasked(id, !isNovelMasked(id));
      renderNovelCards();
    }
    if (action === "download") {
      await openBundleModal(novel);
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
    if (action === "refresh-audio-duration") {
      const btn = document.querySelector(`[data-action="refresh-audio-duration"][data-id="${String(id)}"]`);
      const previousText = btn?.textContent || "↻";
      if (btn) {
        btn.disabled = true;
        btn.textContent = "...";
      }
      try {
        await refreshNovelAudioDuration(id);
        renderNovelCards();
        toast("总时长已更新");
      } finally {
        const nextBtn = document.querySelector(`[data-action="refresh-audio-duration"][data-id="${String(id)}"]`);
        if (nextBtn) {
          nextBtn.disabled = false;
          nextBtn.textContent = previousText;
        }
      }
    }
  } catch (err) {
    toast(t("error.operationFailed", { msg: err.message }));
  }
}

async function refresh() {
  currentData = await getData({ include: ["novelsFull", "prompts", "workflows", "settings"] });
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
  document.getElementById("bundleAudioPresetSelect").addEventListener("change", updateBundleEstimate);
  document.getElementById("bundleAudioVariantSelect").addEventListener("change", updateBundleEstimate);
  document.getElementById("novelCancelBtn").addEventListener("click", closeNovelModal);
  document.getElementById("bundleCloseBtn").addEventListener("click", closeBundleModal);
  document.getElementById("toggleStorageTableBtn")?.addEventListener("click", () => {
    localStorage.setItem(STORAGE_TABLE_COLLAPSED_KEY, isStorageTableCollapsed() ? "0" : "1");
    applyStorageTableCollapsedState();
  });
  document.getElementById("bundleCreateBtn").addEventListener("click", async () => {
    if (!activeBundleNovelId) return;
    const presetSelect = document.getElementById("bundleAudioPresetSelect");
    const variantSelect = document.getElementById("bundleAudioVariantSelect");
    const audioPreset = String(presetSelect?.value || "lossless");
    const audioVariant = String(variantSelect?.value || "ver");
    setBundleControlsBusy(true);
    setBundleListLoading();
    try {
      const task = await createNovelBundle(activeBundleNovelId, { audioPreset, audioVariant });
      setBundleListLoading(task);
      startBundleTaskPolling();
      await syncBundleTaskStatus({ silent: true });
    } catch (err) {
      setBundleControlsBusy(false);
      toast(t("error.operationFailed", { msg: err.message }));
      await refreshBundleList();
    }
  });

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
          nsfwPromptId: form.nsfwPromptId.value,
          voiceSampleWorkflowId: form.voiceSampleWorkflowId.value,
          lineAudioWorkflowId: form.lineAudioWorkflowId.value,
          voiceTranscribeWorkflowId: form.voiceTranscribeWorkflowId.value,
          audioAsrWorkflowId: form.audioAsrWorkflowId.value,
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
  applyStorageTableCollapsedState();
  localizeDocumentText(document);
  initAutoRefresh();
}

init().catch((err) => {
  renderNav();
  showPageError(err, t("error.pageLoad"));
});
