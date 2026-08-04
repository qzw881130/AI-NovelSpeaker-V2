import {
  createChapter,
  deleteChapter,
  fetchChapterDetail,
  fetchChapterJsonOutput,
  fetchNovelChapters,
  getData,
  getActiveNovelId,
  requestConvertJson,
  saveChapterJsonOutput,
  setActiveNovelId,
  updateChapter,
  fetchChapterLineAudios,
  fetchChapterLineAudioOverview,
  enqueueLineAudio,
  enqueueAllLineAudios,
  editLineAudioTaskAudio,
  detectLineAudioTaskSilences,
  detectLineAudioTaskNoise,
  recordLineAudioNoiseFalsePositive,
  analyzeLineAudioTaskLoudness,
  previewLineAudioReplacementTargets,
  replaceMatchingLineAudios,
  mergeChapterLineAudio,
} from "./store.js";
import { fmtDateTime, fmtNumber, incrementNavBadge, renderNav, showPageError, toast } from "./ui.js";
import { localizeDocumentText, t, translateText } from "./i18n.js";

let allNovels = [];
let activeNovel = null;
let currentSettings = null;
let chapterState = [];
let activeChapterNum = null;
let activeChapterDetail = null;
let chapterModalMode = "create";
let chapterEditSourceNum = null;
let modalInitialWordCount = 0;
let jsonViewMode = "raw";
let jsonViewRawText = "";
let jsonViewParsed = null;
let jsonViewEditing = false;
let jsonAceEditor = null;
let lastJsonFindQuery = "";
let lastJsonFindIndex = -1;
const CHAPTER_FONT_SIZE_KEY = "ai_novel_reader_font_size";
const JSON_VIEW_FONT_SIZE_KEY = "ai_novel_json_view_font_size";
const LINE_AUDIO_REFRESH_INTERVAL_KEY = "ai_novel_line_audio_refresh_interval";
const CHAPTER_QUICK_JUMP_EXPANDED_KEY = "ai_novel_chapter_quick_jump_expanded";
const LAST_CHAPTER_KEY_PREFIX = "ai_novel_last_chapter";
const LAST_LINE_AUDIO_ROW_KEY_PREFIX = "ai_novel_last_line_audio_row";
const LINE_AUDIO_REFRESH_INTERVALS = new Set([0, 5, 20, 60]);
const LINE_AUDIO_EDITOR_MAX_ZOOM = 1200;
const MERGE_ADJACENT_LINE_MAX_CHARS = 180;
let jsonAutosaveTimerId = null;
let jsonAutosaveSaving = false;
let chapterQuickJumpExpanded = localStorage.getItem(CHAPTER_QUICK_JUMP_EXPANDED_KEY) === "1";

// 台词音频状态
let lineAudioEntries = [];
let linePreviewRows = [];
let activeLineRole = "__all";
let activeLineAudioFilter = "__all";
let filterMissingRoleOnly = false;
let filterDunhaoMultiRoleOnly = false;
let lineSearchIndex = -1;
let lineEditEnabled = false;
let editingLineIndex = -1;
let editingLineOriginalText = "";
let lineAudioRefreshTimerId = null;
let lineAudioRefreshIntervalSeconds = getSavedLineAudioRefreshInterval();
let lineAudioEditorWaveSurfer = null;
let lineAudioEditorRegions = null;
let lineAudioEditorRegion = null;
let lineAudioEditorDeleteRegions = [];
let lineAudioEditorSelectionPlaying = false;
let lineAudioEditorTaskId = 0;
let lineAudioEditorLineIndex = -1;
let lineAudioEditorNoiseSegments = [];
let lineAudioEditorReady = false;
let lineAudioEditorDetectToken = 0;
let lineAudioEditorLoudnessToken = 0;
let lineAudioEditorSpaceKeyHandler = null;
let lineAudioEditorZoom = 0;
let lineAudioEditorVolumeDb = 0;
let lineAudioEditorSpeedFactor = 1;
let lineAudioEditorSuggestedGainDb = null;
let activeLineAudioRowIndex = -1;
let lineAudioSilenceMarks = new Map();
let lineAudioNoiseMarks = new Map();
let lineAudioQualityMarks = new Map();
let lineAudioBatchProcessingIndex = -1;
let lineAudioBatchBusy = false;
let lineAudioAnomalyCount = 0;
let waveSurferModulesPromise = null;

// 角色列表状态
let chapterRoles = [];
let isRolesEditing = false;
let globalRoleDefaults = [];
let filterRolesMissingOnly = false;

function resetChapterJsonCache() {
  jsonViewRawText = "";
  jsonViewParsed = null;
  jsonViewEditing = false;
  clearJsonAutosaveTimer();
}

async function loadChapterJsonCache() {
  if (!activeNovel || !activeChapterNum) {
    resetChapterJsonCache();
    return null;
  }
  const output = await fetchChapterJsonOutput(activeNovel.id, activeChapterNum);
  const text = String(output?.jsonText || "").trim();
  jsonViewRawText = text;
  if (!text) {
    jsonViewParsed = null;
    return null;
  }
  try {
    jsonViewParsed = JSON.parse(text);
  } catch {
    jsonViewParsed = null;
  }
  return jsonViewParsed;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const base64 = reader.result?.toString()?.split(",")?.[1];
      if (base64) resolve(base64);
      else reject(new Error("Failed to convert file to base64"));
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function fmtDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "-";
  }
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) {
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function calcWordCount(text) {
  return String(text || "").replace(/\s+/g, "").length;
}

function getSavedChapterFontSize() {
  const raw = Number(localStorage.getItem(CHAPTER_FONT_SIZE_KEY) || 18);
  if (!Number.isFinite(raw)) return 18;
  return Math.min(30, Math.max(14, Math.round(raw)));
}

function applyChapterFontSize(px) {
  const size = Math.min(30, Math.max(14, Math.round(Number(px) || 18)));
  const content = document.getElementById("chapterContent");
  const range = document.getElementById("chapterFontSizeRange");
  const value = document.getElementById("chapterFontSizeValue");
  if (content) {
    content.style.fontSize = `${size}px`;
  }
  if (range) {
    range.value = String(size);
  }
  if (value) {
    value.textContent = `${size}px`;
  }
}

function saveChapterFontSize(px) {
  const size = Math.min(30, Math.max(14, Math.round(Number(px) || 18)));
  localStorage.setItem(CHAPTER_FONT_SIZE_KEY, String(size));
  applyChapterFontSize(size);
}

function getSavedLineAudioRefreshInterval() {
  const value = Number(localStorage.getItem(LINE_AUDIO_REFRESH_INTERVAL_KEY) || 5);
  return LINE_AUDIO_REFRESH_INTERVALS.has(value) ? value : 5;
}

function saveLineAudioRefreshInterval(value) {
  const next = Number(value || 0);
  lineAudioRefreshIntervalSeconds = LINE_AUDIO_REFRESH_INTERVALS.has(next) ? next : 5;
  localStorage.setItem(LINE_AUDIO_REFRESH_INTERVAL_KEY, String(lineAudioRefreshIntervalSeconds));
}

function getLastChapterKey(novelId = activeNovel?.id) {
  return novelId ? `${LAST_CHAPTER_KEY_PREFIX}_${Number(novelId)}` : "";
}

function getSavedLastChapterNum(novelId = activeNovel?.id) {
  const key = getLastChapterKey(novelId);
  if (!key) return null;
  const value = Number(localStorage.getItem(key) || 0);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function saveLastChapterNum(chapterNum) {
  const key = getLastChapterKey();
  const value = Number(chapterNum || 0);
  if (key && value > 0) localStorage.setItem(key, String(value));
}

function getLastLineAudioRowKey() {
  if (!activeNovel?.id || !activeChapterNum) return "";
  return `${LAST_LINE_AUDIO_ROW_KEY_PREFIX}_${Number(activeNovel.id)}_${Number(activeChapterNum)}`;
}

function getSavedLastLineAudioRowIndex() {
  const key = getLastLineAudioRowKey();
  if (!key) return -1;
  const value = Number(localStorage.getItem(key) || -1);
  return Number.isFinite(value) && value >= 0 ? value : -1;
}

function saveLastLineAudioRowIndex(lineIndex) {
  const key = getLastLineAudioRowKey();
  const value = Number(lineIndex);
  if (key && value >= 0) localStorage.setItem(key, String(value));
}

function syncLineAudioRefreshSelect() {
  const select = document.getElementById("lineAudioRefreshSelect");
  if (select) select.value = String(lineAudioRefreshIntervalSeconds);
}

function getLineAudioQueueSchedule() {
  const queue = currentSettings?.lineAudioQueue || {};
  const mode = String(queue.mode || "immediate").trim();
  const scheduledAt = String(queue.scheduledAt || "").trim();
  if (mode !== "scheduled") {
    return { mode: "immediate", scheduledAt: "", label: translateText("立即执行") };
  }
  if (!scheduledAt) {
    return { mode: "immediate", scheduledAt: "", label: translateText("立即执行") };
  }
  return {
    mode: "scheduled",
    scheduledAt,
    label: `${translateText("指定时间执行")} ${fmtDateTime(scheduledAt) || scheduledAt}`,
  };
}

function syncModalWordCount(form) {
  const content = String(form.content.value || "");
  const wc = content.trim() ? calcWordCount(content) : modalInitialWordCount;
  form.wordCount.value = String(wc);
}

function getNovelByQueryOrActive() {
  const url = new URL(window.location.href);
  const queryId = String(url.searchParams.get("novelId") || "");
  if (queryId) {
    return allNovels.find((n) => String(n.id) === queryId) || null;
  }
  const activeId = String(getActiveNovelId() || "");
  if (activeId) {
    return allNovels.find((n) => String(n.id) === activeId) || allNovels[0] || null;
  }
  return allNovels[0] || null;
}

function getChapterNumFromQuery() {
  const url = new URL(window.location.href);
  const value = Number(url.searchParams.get("chapterNum") || 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function setHeader(novel) {
  document.getElementById("chapterPageTitle").textContent = `${novel.name} ${t("nav.chapters")}`;
}

function renderNovelSelect() {
  const select = document.getElementById("chapterNovelSelect");
  select.innerHTML = allNovels.map((n) => `<option value="${n.id}">${n.name}</option>`).join("");
  if (activeNovel) select.value = String(activeNovel.id);
}

function renderQuickJump(list) {
  const wrap = document.getElementById("chapterQuickJump");
  const count = document.getElementById("chapterQuickJumpCount");
  const toggle = document.getElementById("toggleChapterQuickJumpBtn");
  document.querySelector(".chapter-side")?.classList.toggle("quick-jump-expanded", chapterQuickJumpExpanded);
  if (count) count.textContent = `${list.length} 回编号`;
  if (toggle) {
    toggle.textContent = chapterQuickJumpExpanded ? "收起编号" : "展开编号";
    toggle.setAttribute("aria-expanded", chapterQuickJumpExpanded ? "true" : "false");
  }
  wrap.innerHTML = list
    .map((c) => {
      const progress = c.hasAudio ? 100 : c.hasJson ? 55 : 0;
      const activeClass = c.chapterNum === activeChapterNum ? "active" : "";
      return `<button class="quick-chip ${activeClass}" style="--progress:${progress}%" data-chapter-num="${c.chapterNum}">${String(c.chapterNum).padStart(3, "0")}</button>`;
    })
    .join("");
  wrap.querySelectorAll("[data-chapter-num]").forEach((el) => {
    el.addEventListener("click", () => loadChapter(Number(el.dataset.chapterNum)));
  });
  if (!chapterQuickJumpExpanded) {
    window.requestAnimationFrame(() => {
      wrap.querySelector(".quick-chip.active")?.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
  }
}

function renderChapterList() {
  const keyword = document.getElementById("chapterSearch").value.trim();
  const list = keyword
    ? chapterState.filter((c) => c.title.includes(keyword) || String(c.chapterNum).includes(keyword))
    : chapterState;

  document.getElementById("chapterList").innerHTML = list
    .map((c) => {
      const activeClass = c.chapterNum === activeChapterNum ? "active" : "";
      return `<li class="chapter-item ${activeClass}" data-chapter-num="${c.chapterNum}">
        <strong>${c.title}</strong>
        <div class="meta chapter-item-meta">
          <span>字数 ${fmtNumber(c.wordCount)}</span>
          <span class="chapter-state-icons">
            <span class="state-icon state-json ${c.hasJson ? "done" : "todo"}" title="JSON"></span>
            <span class="state-icon state-audio ${c.hasAudio ? "done" : "todo"}" title="音频"></span>
          </span>
        </div>
      </li>`;
    })
    .join("");

  document.querySelectorAll(".chapter-item").forEach((el) => {
    el.addEventListener("click", () => loadChapter(Number(el.dataset.chapterNum)));
  });
  renderQuickJump(chapterState);
  localizeDocumentText(document);
}

function setStatus(text) {
  document.getElementById("chapterStatus").textContent = translateText(text);
}

function getCurrentChapterState() {
  return chapterState.find((c) => c.chapterNum === activeChapterNum) || null;
}

function updateChapterNavButtons() {
  const prevBtn = document.getElementById("prevChapterBtn");
  const nextBtn = document.getElementById("nextChapterBtn");
  if (!prevBtn || !nextBtn) return;
  if (!chapterState.length || activeChapterNum == null) {
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    return;
  }
  const idx = chapterState.findIndex((item) => item.chapterNum === activeChapterNum);
  prevBtn.disabled = idx <= 0;
  nextBtn.disabled = idx < 0 || idx >= chapterState.length - 1;
}

function resetChapterAudioPlayer() {
  const box = document.getElementById("chapterAudioBox");
  const verWrap = document.getElementById("chapterVerAudioWrap");
  const player = document.getElementById("chapterAudioPlayer");
  const nonVerWrap = document.getElementById("chapterNonVerAudioWrap");
  const nonVerPlayer = document.getElementById("chapterNonVerAudioPlayer");
  const duration = document.getElementById("chapterAudioDuration");
  const nonVerDuration = document.getElementById("chapterNonVerAudioDuration");
  box.classList.add("hidden");
  if (verWrap) verWrap.classList.add("hidden");
  duration.textContent = "时长：-";
  player.pause();
  player.removeAttribute("src");
  player.load();
  if (nonVerWrap) nonVerWrap.classList.add("hidden");
  if (nonVerDuration) nonVerDuration.textContent = "时长：-";
  if (nonVerPlayer) {
    nonVerPlayer.pause();
    nonVerPlayer.removeAttribute("src");
    nonVerPlayer.load();
  }
}

function getChapterAudioStreamUrl(chapterNum, audioVersion = "") {
  if (!activeNovel) return "";
  const base = `/api/novels/${Number(activeNovel.id)}/chapters/${Number(chapterNum)}/audio-stream`;
  const version = String(audioVersion || "").trim();
  return version ? `${base}?v=${encodeURIComponent(version)}` : base;
}

function getChapterNonVerAudioUrl(chapterNum, audioVersion = "") {
  if (!activeNovel) return "";
  const base = `/api/novels/${Number(activeNovel.id)}/chapters/${Number(chapterNum)}/merged-audio?variant=nonver`;
  const version = String(audioVersion || "").trim();
  return version ? `${base}&v=${encodeURIComponent(version)}` : base;
}

function refreshChapterAudioState(detail) {
  if (!activeNovel || (!detail?.hasAudio && !detail?.hasNonVerAudio)) {
    resetChapterAudioPlayer();
    return;
  }
  const box = document.getElementById("chapterAudioBox");
  const verWrap = document.getElementById("chapterVerAudioWrap");
  const player = document.getElementById("chapterAudioPlayer");
  const nonVerWrap = document.getElementById("chapterNonVerAudioWrap");
  const nonVerPlayer = document.getElementById("chapterNonVerAudioPlayer");
  const duration = document.getElementById("chapterAudioDuration");
  const nonVerDuration = document.getElementById("chapterNonVerAudioDuration");
  if (detail?.hasAudio) {
    if (verWrap) verWrap.classList.remove("hidden");
    player.src = getChapterAudioStreamUrl(detail.chapterNum, detail.audioVersion);
    duration.textContent = `时长：${fmtDuration(detail.audioDurationSeconds || 0)}`;
  } else {
    if (verWrap) verWrap.classList.add("hidden");
    player.pause();
    player.removeAttribute("src");
    player.load();
    duration.textContent = "时长：-";
  }
  if (detail?.hasNonVerAudio) {
    if (nonVerWrap) nonVerWrap.classList.remove("hidden");
    if (nonVerPlayer) nonVerPlayer.src = getChapterNonVerAudioUrl(detail.chapterNum, detail.nonVerAudioVersion);
    if (nonVerDuration) nonVerDuration.textContent = `时长：${fmtDuration(detail.nonVerAudioDurationSeconds || 0)}`;
  } else {
    if (nonVerWrap) nonVerWrap.classList.add("hidden");
    if (nonVerPlayer) {
      nonVerPlayer.pause();
      nonVerPlayer.removeAttribute("src");
      nonVerPlayer.load();
    }
    if (nonVerDuration) nonVerDuration.textContent = "时长：-";
  }
  box.classList.remove("hidden");
}

async function loadChapter(chapterNum) {
  if (!activeNovel) return;
  activeChapterNum = chapterNum;
  saveLastChapterNum(chapterNum);
  resetChapterJsonCache();
  try {
    const detail = await fetchChapterDetail(activeNovel.id, chapterNum);
    activeChapterDetail = detail;
    document.getElementById("chapterTitle").textContent = detail.title;
    document.getElementById("chapterMeta").textContent = `${detail.novelName} · 章节 ${detail.chapterNum} · 字数 ${fmtNumber(detail.wordCount)}`;
    document.getElementById("chapterContent").textContent = detail.content;
    refreshChapterAudioState(detail);
    if (detail?.hasJson) {
      await loadChapterJsonCache();
    }
    await updateChapterActionWarnings();
    setStatus("就绪");
    renderChapterList();
    updateChapterNavButtons();
    localizeDocumentText(document);
  } catch (err) {
    resetChapterAudioPlayer();
    setStatus(t("error.loadFailed", { msg: err.message }));
  }
}

function copyText(text, successText) {
  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    return navigator.clipboard.writeText(text).then(() => toast(successText));
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  document.execCommand("copy");
  document.body.removeChild(ta);
  toast(successText);
  return Promise.resolve();
}

function downloadText(filename, content) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function renderJsonViewMode(options = {}) {
  const { preserveEditorView = false } = options;
  const preview = document.getElementById("chapterJsonPreview");
  const readOnly = document.getElementById("chapterJsonReadOnly");
  const editor = document.getElementById("chapterJsonEditor");
  const editorWrap = document.getElementById("chapterJsonEditorWrap");
  const aceHost = document.getElementById("chapterJsonAceEditor");
  const illegalColonBtn = document.getElementById("checkIllegalColonBtn");
  const findWrap = document.getElementById("jsonFindReplaceWrap");
  const repairBtn = document.getElementById("jsonRepairBtn");
  const replaceBtn = document.getElementById("jsonReplaceBtn");
  const replaceAllBtn = document.getElementById("jsonReplaceAllBtn");
  const editBtn = document.getElementById("editJsonViewBtn");
  const saveBtn = document.getElementById("saveJsonViewBtn");
  const autosaveHint = document.getElementById("jsonAutosaveHint");
  const shortcutHint = document.getElementById("jsonShortcutHint");
  const rawBtn = document.getElementById("viewJsonRawBtn");
  const jubenBtn = document.getElementById("viewJsonJubenBtn");
  const rolesBtn = document.getElementById("viewJsonRolesBtn");
  const jsonFontSize = Number(localStorage.getItem(JSON_VIEW_FONT_SIZE_KEY) || 18);
  const normalizedFontSize = Number.isFinite(jsonFontSize) ? Math.max(14, Math.min(30, jsonFontSize)) : 18;
  preview.style.fontSize = `${normalizedFontSize}px`;
  readOnly.style.fontSize = `${normalizedFontSize}px`;
  editor.style.fontSize = `${normalizedFontSize}px`;
  setJsonEditorFontSize(normalizedFontSize);
  const fontRange = document.getElementById("jsonViewFontSizeRange");
  const fontValue = document.getElementById("jsonViewFontSizeValue");
  if (fontRange) fontRange.value = String(normalizedFontSize);
  if (fontValue) fontValue.textContent = `${normalizedFontSize}px`;
  rawBtn.classList.toggle("active", jsonViewMode === "raw");
  jubenBtn.classList.toggle("active", jsonViewMode === "juben");
  rolesBtn.classList.toggle("active", jsonViewMode === "roles");

  const canEdit = jsonViewMode === "raw" || jsonViewMode === "juben" || jsonViewMode === "roles";
  const useAceEditor = jsonViewEditing && jsonViewMode === "juben" && Boolean(ensureJsonAceEditor());
  editBtn.classList.toggle("hidden", !canEdit);
  saveBtn.classList.toggle("hidden", !jsonViewEditing);
  editBtn.textContent = jsonViewEditing ? t("common.cancelEdit") : "编辑JSON";
  const rawReadOnlyVisible = jsonViewMode === "raw" && !jsonViewEditing;
  preview.classList.toggle("hidden", jsonViewEditing || rawReadOnlyVisible);
  readOnly.classList.toggle("hidden", !rawReadOnlyVisible);
  editorWrap.classList.toggle("hidden", !jsonViewEditing);
  aceHost.classList.toggle("hidden", !useAceEditor);
  editor.classList.toggle("hidden", !jsonViewEditing || useAceEditor);
  illegalColonBtn.classList.toggle("hidden", !(jsonViewEditing && jsonViewMode === "juben"));
  findWrap.classList.toggle("hidden", jsonViewMode !== "raw");
  repairBtn.classList.toggle("hidden", !(jsonViewMode === "raw" && jsonViewEditing));
  replaceBtn.classList.toggle("hidden", !(jsonViewMode === "raw" && jsonViewEditing));
  replaceAllBtn.classList.toggle("hidden", !(jsonViewMode === "raw" && jsonViewEditing));
  if (autosaveHint) {
    autosaveHint.classList.toggle("hidden", !(jsonViewEditing && jsonViewMode === "juben"));
  }
  if (shortcutHint) {
    shortcutHint.classList.toggle("hidden", !(jsonViewEditing && jsonViewMode === "juben"));
  }

  if (jsonViewEditing) {
    if (jsonViewMode === "raw") {
      if (jsonViewParsed && typeof jsonViewParsed === "object") {
        setJsonEditorValue(JSON.stringify(jsonViewParsed, null, 2), { preserveView: preserveEditorView });
      } else {
        try {
          setJsonEditorValue(JSON.stringify(JSON.parse(jsonViewRawText || "{}"), null, 2), { preserveView: preserveEditorView });
        } catch {
          setJsonEditorValue(jsonViewRawText || JSON.stringify({ role_list: [], juben: "" }, null, 2), { preserveView: preserveEditorView });
        }
      }
    } else if (jsonViewMode === "juben") {
      setJsonEditorValue(String(jsonViewParsed?.juben || "").trim(), { preserveView: preserveEditorView });
    } else if (jsonViewMode === "roles") {
      const roles = Array.isArray(jsonViewParsed?.role_list) ? jsonViewParsed.role_list : [];
      setJsonEditorValue(JSON.stringify(roles, null, 2), { preserveView: preserveEditorView });
    } else {
      setJsonEditorValue(jsonViewRawText || "", { preserveView: preserveEditorView });
    }
    updateJsonAutosaveState();
    return;
  }

  updateJsonAutosaveState();

  if (jsonViewMode === "raw") {
    if (jsonViewParsed && typeof jsonViewParsed === "object") {
      readOnly.value = JSON.stringify(jsonViewParsed, null, 2);
    } else {
      readOnly.value = jsonViewRawText || JSON.stringify({ role_list: [], juben: "" }, null, 2);
    }
    return;
  }

  if (!jsonViewParsed || typeof jsonViewParsed !== "object") {
    preview.textContent = "JSON 解析失败，无法显示此视图。";
    return;
  }

  if (jsonViewMode === "juben") {
    const juben = String(jsonViewParsed.juben || "").trim();
    preview.textContent = juben || "该 JSON 没有 juben 字段。";
    return;
  }

  const list = Array.isArray(jsonViewParsed.role_list) ? jsonViewParsed.role_list : [];
  if (!list.length) {
    preview.textContent = "该 JSON 没有 role_list 数据。";
    return;
  }
  const lines = list.map((x, i) => {
    const name = String(x?.name || "").trim() || `角色${i + 1}`;
    const instruct = String(x?.instruct || "").trim() || "-";
    const sample = String(x?.text || "").trim() || "-";
    return `【${name}】\n人设: ${instruct}\n示例: ${sample}`;
  });
  preview.textContent = lines.join("\n\n");
  localizeDocumentText(document);
}

function clearJsonAutosaveTimer() {
  if (jsonAutosaveTimerId) {
    window.clearInterval(jsonAutosaveTimerId);
    jsonAutosaveTimerId = null;
  }
}

function updateJsonAutosaveState() {
  const shouldAutosave = jsonViewEditing && jsonViewMode === "juben";
  if (!shouldAutosave) {
    clearJsonAutosaveTimer();
    return;
  }
  if (jsonAutosaveTimerId) return;
  jsonAutosaveTimerId = window.setInterval(async () => {
    if (!jsonViewEditing || jsonViewMode !== "juben" || jsonAutosaveSaving) return;
    const currentText = String(getJsonEditorValue() || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
    const savedText = String(jsonViewParsed?.juben || "").trim();
    if (currentText === savedText) return;
    jsonAutosaveSaving = true;
    try {
      await saveJsonViewEdit({ keepEditing: true, silent: true, autosave: true });
    } finally {
      jsonAutosaveSaving = false;
    }
  }, 15000);
}

async function saveJsonViewEdit(options = {}) {
  const { keepEditing = false, silent = false, autosave = false } = options;
  if (!activeNovel || !activeChapterNum) {
    toast("当前章节 JSON 不可编辑");
    return;
  }
  const editorValue = getJsonEditorValue();
  let draft = jsonViewParsed && typeof jsonViewParsed === "object"
    ? JSON.parse(JSON.stringify(jsonViewParsed))
    : null;

  if (jsonViewMode === "raw") {
    try {
      draft = JSON.parse(String(editorValue || "{}"));
    } catch {
      toast("JSON 内容不是合法格式");
      return;
    }
  } else if (!draft || typeof draft !== "object") {
    toast("当前章节 JSON 不可编辑");
    return;
  } else if (jsonViewMode === "juben") {
    draft.juben = String(editorValue || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
  } else if (jsonViewMode === "roles") {
    let roles;
    try {
      roles = JSON.parse(String(editorValue || "[]"));
    } catch {
      toast("角色编辑内容必须是合法 JSON 数组");
      return;
    }
    if (!Array.isArray(roles)) {
      toast("角色编辑内容必须是 JSON 数组");
      return;
    }
    draft.role_list = roles.map((x) => ({
      name: String(x?.name || "").trim(),
      instruct: String(x?.instruct || "").trim(),
      text: String(x?.text || "").trim(),
    }));
  } else {
    toast("当前模式不支持编辑");
    return;
  }

  const merged = JSON.stringify(draft, null, 2);
  await saveChapterJsonOutput(activeNovel.id, activeChapterNum, merged);
  jsonViewParsed = draft;
  jsonViewRawText = merged;
  jsonViewEditing = keepEditing;
  renderJsonViewMode({ preserveEditorView: keepEditing && jsonViewMode === "juben" });
  await updateChapterActionWarnings();
  setStatus(autosave ? "台词已自动保存" : "JSON 已保存");
  if (!silent) {
    toast(t("toast.saved"));
  }
}

function getActiveJsonTextEl() {
  if (jsonViewEditing) {
    return document.getElementById("chapterJsonEditor");
  }
  if (jsonViewMode === "raw") {
    return document.getElementById("chapterJsonReadOnly");
  }
  return null;
}

function ensureJsonAceEditor() {
  if (jsonAceEditor) return jsonAceEditor;
  const ace = window.ace;
  const host = document.getElementById("chapterJsonAceEditor");
  if (!ace || !host) return null;
  jsonAceEditor = ace.edit(host);
  jsonAceEditor.setTheme("ace/theme/textmate");
  jsonAceEditor.session.setMode("ace/mode/text");
  jsonAceEditor.session.setUseWrapMode(true);
  jsonAceEditor.session.setUseWorker(false);
  jsonAceEditor.setShowPrintMargin(false);
  jsonAceEditor.setHighlightActiveLine(false);
  jsonAceEditor.setHighlightGutterLine(false);
  jsonAceEditor.setOption("scrollPastEnd", 0);
  jsonAceEditor.setOption("fontFamily", 'Menlo, Monaco, Consolas, "Courier New", monospace');
  jsonAceEditor.commands.addCommand({
    name: "saveJubenEditor",
    bindKey: { win: "Ctrl-S", mac: "Command-S" },
    exec: () => {
      if (!jsonViewEditing || jsonViewMode !== "juben") return;
      saveJsonViewEdit({ keepEditing: true });
    },
  });
  return jsonAceEditor;
}

function setJsonEditorFontSize(size) {
  const normalized = Math.max(14, Math.min(30, Number(size) || 18));
  const aceEditor = ensureJsonAceEditor();
  if (aceEditor) {
    aceEditor.setFontSize(normalized);
    aceEditor.resize();
  }
}

function setJsonEditorValue(value, options = {}) {
  const { preserveView = false } = options;
  const text = String(value || "");
  const editor = document.getElementById("chapterJsonEditor");
  if (editor) {
    editor.value = text;
  }
  if (jsonViewMode === "juben") {
    const aceEditor = ensureJsonAceEditor();
    if (aceEditor) {
      const currentValue = aceEditor.getValue();
      const changed = currentValue !== text;
      const cursor = aceEditor.getCursorPosition();
      const scrollTop = aceEditor.session.getScrollTop();
      const scrollLeft = aceEditor.session.getScrollLeft();
      if (changed) {
        aceEditor.setValue(text, -1);
      }
      if (preserveView) {
        aceEditor.moveCursorToPosition(cursor);
        aceEditor.clearSelection();
        aceEditor.session.setScrollTop(scrollTop);
        aceEditor.session.setScrollLeft(scrollLeft);
      } else {
        aceEditor.clearSelection();
        aceEditor.session.setScrollTop(0);
        aceEditor.session.setScrollLeft(0);
      }
      aceEditor.resize();
    }
  }
}

function getJsonEditorValue() {
  if (jsonViewEditing && jsonViewMode === "juben") {
    const aceEditor = ensureJsonAceEditor();
    if (aceEditor) {
      return aceEditor.getValue();
    }
  }
  return String(document.getElementById("chapterJsonEditor")?.value || "");
}

function getFirstRoleSeparatorIndex(line) {
  const text = String(line || "");
  const positions = [text.indexOf(":"), text.indexOf("：")].filter((pos) => pos >= 0);
  return positions.length ? Math.min(...positions) : -1;
}

function findIllegalColonInJuben(text) {
  const normalized = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = normalized.split("\n");
  let offset = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const separatorIndex = getFirstRoleSeparatorIndex(line);
    if (separatorIndex >= 0) {
      const dialogueRaw = line.slice(separatorIndex + 1);
      const trimmedDialogue = dialogueRaw.replace(/\s+$/g, "");
      const scanText = /[:：]$/.test(trimmedDialogue)
        ? trimmedDialogue.slice(0, -1)
        : trimmedDialogue;
      const matchIndex = scanText.search(/[:：]/);
      if (matchIndex >= 0) {
        return {
          lineNumber: index + 1,
          lineStart: offset,
          lineEnd: offset + line.length,
          colonIndex: offset + separatorIndex + 1 + matchIndex,
          column: separatorIndex + 1 + matchIndex,
        };
      }
    }
    offset += line.length + 1;
  }
  return null;
}

function focusIllegalColonInJubenEditor(location) {
  if (!location) return;
  const aceEditor = ensureJsonAceEditor();
  if (jsonViewEditing && jsonViewMode === "juben" && aceEditor) {
    const row = Math.max(0, location.lineNumber - 1);
    const column = Math.max(0, location.column || 0);
    aceEditor.focus();
    aceEditor.gotoLine(location.lineNumber, column, true);
    aceEditor.selection.setSelectionRange({
      start: { row, column },
      end: { row, column: column + 1 },
    });
    aceEditor.centerSelection();
    return;
  }
  const editor = document.getElementById("chapterJsonEditor");
  if (!editor) return;
  editor.focus();
  editor.setSelectionRange(location.colonIndex, location.colonIndex + 1);
}

function checkIllegalColonInJubenEditor() {
  if (!(jsonViewEditing && jsonViewMode === "juben")) return;
  const found = findIllegalColonInJuben(getJsonEditorValue());
  if (!found) {
    toast("未发现非法冒号");
    return;
  }
  focusIllegalColonInJubenEditor(found);
  toast(`发现非法冒号：第 ${found.lineNumber} 行`);
}

function selectJsonMatch(start, length) {
  const input = getActiveJsonTextEl();
  if (!input) return false;
  input.focus();
  input.setSelectionRange(start, start + length);
  input.scrollTop = input.scrollHeight * (start / Math.max(input.value.length, 1));
  lastJsonFindIndex = start;
  return true;
}

function findNextInJsonEditor() {
  const query = String(document.getElementById("jsonFindInput")?.value || "");
  if (!query) {
    toast("请输入要查找的内容");
    return false;
  }
  const input = getActiveJsonTextEl();
  if (!input) return false;
  const text = input.value;
  let startIndex = input.selectionEnd;
  if (lastJsonFindQuery !== query) {
    startIndex = 0;
  }
  let matchIndex = text.indexOf(query, startIndex);
  if (matchIndex < 0 && startIndex > 0) {
    matchIndex = text.indexOf(query, 0);
  }
  if (matchIndex < 0) {
    lastJsonFindQuery = query;
    lastJsonFindIndex = -1;
    toast(`未找到：${query}`);
    return false;
  }
  lastJsonFindQuery = query;
  return selectJsonMatch(matchIndex, query.length);
}

function replaceCurrentInJsonEditor() {
  if (!(jsonViewMode === "raw" && jsonViewEditing)) {
    toast("请先进入 JSON 编辑再执行替换");
    return;
  }
  const query = String(document.getElementById("jsonFindInput")?.value || "");
  const replacement = String(document.getElementById("jsonReplaceInput")?.value || "");
  if (!query) {
    toast("请输入要查找的内容");
    return;
  }
  const input = getActiveJsonTextEl();
  if (!input) return;
  const selected = input.value.slice(input.selectionStart, input.selectionEnd);
  if (selected !== query) {
    if (!findNextInJsonEditor()) return;
  }
  const start = input.selectionStart;
  const end = input.selectionEnd;
  input.setRangeText(replacement, start, end, "select");
  lastJsonFindQuery = query;
  lastJsonFindIndex = start;
  input.focus();
  toast("已替换当前匹配");
}

function replaceAllInJsonEditor() {
  if (!(jsonViewMode === "raw" && jsonViewEditing)) {
    toast("请先进入 JSON 编辑再执行替换");
    return;
  }
  const query = String(document.getElementById("jsonFindInput")?.value || "");
  const replacement = String(document.getElementById("jsonReplaceInput")?.value || "");
  if (!query) {
    toast("请输入要查找的内容");
    return;
  }
  const input = getActiveJsonTextEl();
  if (!input) return;
  const text = input.value;
  const count = text.split(query).length - 1;
  if (count <= 0) {
    toast(`未找到：${query}`);
    return;
  }
  input.value = text.split(query).join(replacement);
  lastJsonFindQuery = query;
  lastJsonFindIndex = -1;
  input.focus();
  toast(`已全部替换 ${count} 处`);
}

function repairJsonInEditor() {
  if (!(jsonViewMode === "raw" && jsonViewEditing)) {
    toast("请先进入 JSON 编辑再执行修复");
    return;
  }
  const input = getActiveJsonTextEl();
  if (!input) return;
  const original = String(input.value || "");
  const repaired = original.replace(/[\r\n]+/g, "");
  if (repaired === original) {
    toast("未发现可移除的换行符");
    return;
  }
  const removedCount = (original.match(/[\r\n]/g) || []).length;
  input.value = repaired;
  input.focus();
  input.setSelectionRange(0, 0);
  lastJsonFindQuery = "";
  lastJsonFindIndex = -1;
  toast(`已移除 ${removedCount} 个换行符`);
}

async function refreshChapters() {
  if (!activeNovel) return;
  chapterState = await fetchNovelChapters(activeNovel.id);
  if (chapterState.length === 0) {
    activeChapterNum = null;
    activeChapterDetail = null;
    resetChapterAudioPlayer();
    document.getElementById("chapterTitle").textContent = "暂无章节";
    document.getElementById("chapterMeta").textContent = "当前小说尚未创建章节";
    document.getElementById("chapterContent").textContent = '请先点击"创建章回"录入章节信息。';
    renderChapterList();
    localizeDocumentText(document);
    return;
  }
  if (!chapterState.some((x) => x.chapterNum === activeChapterNum)) {
    const queryChapterNum = getChapterNumFromQuery();
    const savedChapterNum = getSavedLastChapterNum();
    activeChapterNum = chapterState.some((x) => x.chapterNum === queryChapterNum)
      ? queryChapterNum
      : chapterState.some((x) => x.chapterNum === savedChapterNum)
      ? savedChapterNum
      : chapterState[0].chapterNum;
  }
  renderChapterList();
  updateChapterNavButtons();
  await loadChapter(activeChapterNum);
}

function bindActions() {
  document.getElementById("refreshChapterBtn").addEventListener("click", async () => {
    if (!activeNovel) return;
    try {
      await refreshChapters();
      setStatus(translateText("章节数据已刷新"));
      toast(translateText("章节数据已刷新"));
    } catch (err) {
      setStatus(t("error.loadFailed", { msg: err.message }));
      toast(t("error.loadFailed", { msg: err.message }));
    }
  });

  document.getElementById("copyChapterBtn").addEventListener("click", () => {
    if (!activeChapterDetail) return;
    copyText(`${activeChapterDetail.title}\n\n${activeChapterDetail.content}`, t("toast.copied"));
  });

  document.getElementById("downloadChapterBtn").addEventListener("click", () => {
    if (!activeChapterDetail || !activeNovel) return;
    downloadText(`${activeNovel.name}-${activeChapterDetail.title}.txt`, activeChapterDetail.content || "");
    setStatus(translateText("开始下载文本"));
  });

  document.getElementById("convertJsonBtn").addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    if (activeChapterDetail?.hasJson) {
      const confirmed = window.confirm("当前章节已存在解析后的 JSON，继续 AI转JSON 将覆盖现有结果。确定继续吗？");
      if (!confirmed) return;
    }
    try {
      await requestConvertJson(activeNovel.id, activeChapterNum);
      incrementNavBadge("json", 1);
      renderNav();
      setStatus("已加入 JSON 转换队列");
      toast(t("toast.created"));
      await refreshChapters();
    } catch (err) {
      setStatus(t("error.operationFailed", { msg: err.message }));
      toast(t("error.operationFailed", { msg: err.message }));
    }
  });

  document.getElementById("viewJsonBtn").addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    await loadChapterJsonCache();
    const text = jsonViewRawText || JSON.stringify({ role_list: [], juben: "" }, null, 2);
    jsonViewMode = "juben";
    jsonViewEditing = false;
    lastJsonFindQuery = "";
    lastJsonFindIndex = -1;
    document.getElementById("jsonFindInput").value = "";
    document.getElementById("jsonReplaceInput").value = "";
    renderJsonViewMode();
    localizeDocumentText(document);
    document.getElementById("jsonDialog").showModal();
  });

  document.getElementById("compareChapterBtn").addEventListener("click", () => {
    if (!activeNovel || !activeChapterNum) return;
    window.open(`./chapters-compare.html?novelId=${Number(activeNovel.id)}&chapterNum=${Number(activeChapterNum)}`, "_blank");
  });

  document.getElementById("viewJsonRawBtn").addEventListener("click", () => {
    jsonViewMode = "raw";
    jsonViewEditing = false;
    lastJsonFindQuery = "";
    lastJsonFindIndex = -1;
    renderJsonViewMode();
    localizeDocumentText(document);
  });
  document.getElementById("viewJsonJubenBtn").addEventListener("click", () => {
    jsonViewMode = "juben";
    jsonViewEditing = false;
    lastJsonFindQuery = "";
    lastJsonFindIndex = -1;
    renderJsonViewMode();
    localizeDocumentText(document);
  });
  document.getElementById("viewJsonRolesBtn").addEventListener("click", () => {
    jsonViewMode = "roles";
    jsonViewEditing = false;
    lastJsonFindQuery = "";
    lastJsonFindIndex = -1;
    renderJsonViewMode();
    localizeDocumentText(document);
  });

  document.getElementById("editJsonViewBtn").addEventListener("click", () => {
    if (jsonViewMode !== "raw" && jsonViewMode !== "juben" && jsonViewMode !== "roles") return;
    jsonViewEditing = !jsonViewEditing;
    renderJsonViewMode();
    localizeDocumentText(document);
  });
  document.getElementById("saveJsonViewBtn").addEventListener("click", async () => {
    await saveJsonViewEdit();
  });

  document.getElementById("copyJsonBtn").addEventListener("click", () => {
    const text = jsonViewEditing
      ? getJsonEditorValue() || ""
      : jsonViewMode === "raw"
        ? document.getElementById("chapterJsonReadOnly").value || ""
        : document.getElementById("chapterJsonPreview").textContent || "";
    copyText(text, t("toast.copied"));
  });

  document.getElementById("jsonFindNextBtn").addEventListener("click", () => {
    findNextInJsonEditor();
  });
  document.getElementById("jsonFindInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      findNextInJsonEditor();
    }
  });
  document.getElementById("jsonReplaceBtn").addEventListener("click", () => {
    replaceCurrentInJsonEditor();
  });
  document.getElementById("jsonReplaceAllBtn").addEventListener("click", () => {
    replaceAllInJsonEditor();
  });
  document.getElementById("jsonRepairBtn").addEventListener("click", () => {
    repairJsonInEditor();
  });
  document.getElementById("checkIllegalColonBtn").addEventListener("click", () => {
    checkIllegalColonInJubenEditor();
  });

  document.getElementById("chapterJsonEditor").addEventListener("keydown", async (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      if (!jsonViewEditing) return;
      await saveJsonViewEdit({ keepEditing: true });
    }
  });

  document.getElementById("jsonViewFontSizeRange")?.addEventListener("input", (event) => {
    const value = Number(event.target.value || 18);
    localStorage.setItem(JSON_VIEW_FONT_SIZE_KEY, String(value));
    renderJsonViewMode();
  });

  document.getElementById("createChapterBtn").addEventListener("click", () => {
    openChapterModal("create");
  });

  document.getElementById("editChapterBtn").addEventListener("click", () => {
    openChapterModal("edit");
  });

  document.getElementById("deleteChapterBtn").addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    const chapter = getCurrentChapterState();
    if (!chapter) return;
    if (!window.confirm(t("confirm.deleteChapter", { title: chapter.title }))) return;
    try {
      await deleteChapter(activeNovel.id, activeChapterNum);
      toast(t("toast.deleted"));
      await refreshChapters();
    } catch (err) {
      toast(t("error.deleteFailed", { msg: err.message }));
    }
  });

  document.getElementById("chapterCancelBtn").addEventListener("click", () => {
    document.getElementById("chapterModal").close();
  });

  document.getElementById("chapterForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!activeNovel) return;
    const form = event.currentTarget;
    const input = {
      chapterNum: Number(form.chapterNum.value),
      title: form.title.value.trim(),
      content: form.content.value,
    };
    if (!input.chapterNum || !input.title) {
      toast(t("error.operationFailed", { msg: "invalid input" }));
      return;
    }
    try {
      if (chapterModalMode === "create") {
        await createChapter(activeNovel.id, input);
        toast(t("toast.created"));
      } else {
        await updateChapter(activeNovel.id, chapterEditSourceNum, input);
        toast(t("toast.updated"));
      }
      document.getElementById("chapterModal").close();
      activeChapterNum = input.chapterNum;
      await refreshChapters();
    } catch (err) {
      toast(t("error.saveFailed", { msg: err.message }));
    }
  });

  const chapterForm = document.getElementById("chapterForm");
  const contentInput = chapterForm.elements.namedItem("content");
  if (contentInput) {
    contentInput.addEventListener("input", () => {
      syncModalWordCount(chapterForm);
    });
  }

  document.getElementById("chapterSearch").addEventListener("input", renderChapterList);
  document.getElementById("toggleChapterQuickJumpBtn")?.addEventListener("click", () => {
    chapterQuickJumpExpanded = !chapterQuickJumpExpanded;
    localStorage.setItem(CHAPTER_QUICK_JUMP_EXPANDED_KEY, chapterQuickJumpExpanded ? "1" : "0");
    renderQuickJump(chapterState);
  });
  document.getElementById("prevChapterBtn").addEventListener("click", () => {
    const idx = chapterState.findIndex((item) => item.chapterNum === activeChapterNum);
    if (idx > 0) {
      loadChapter(chapterState[idx - 1].chapterNum);
    }
  });
  document.getElementById("nextChapterBtn").addEventListener("click", () => {
    const idx = chapterState.findIndex((item) => item.chapterNum === activeChapterNum);
    if (idx >= 0 && idx < chapterState.length - 1) {
      loadChapter(chapterState[idx + 1].chapterNum);
    }
  });
  document.getElementById("chapterFontSizeRange").addEventListener("input", (event) => {
    saveChapterFontSize(Number(event.target.value));
  });
  document.getElementById("chapterNovelSelect").addEventListener("change", async (event) => {
    const id = Number(event.target.value);
    setActiveNovelId(id);
    activeNovel = allNovels.find((n) => Number(n.id) === id) || null;
    if (!activeNovel) return;
    activeChapterNum = null;
    setHeader(activeNovel);
    await refreshChapters();
    toast(`${t("common.view")}: ${activeNovel.name}`);
  });

  // 台词预览按钮
  document.getElementById("viewLineAudioBtn")?.addEventListener("click", openLineAudioDialog);

  // 台词音频弹窗内按钮
  document.getElementById("enqueueAllLineAudioBtn")?.addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    const missingRoleRows = getMissingRoleLinePreviewRows();
    if (missingRoleRows.length) {
      toast(formatMissingRoleLineWarning(missingRoleRows));
      return;
    }
    if (!window.confirm("确定生成所有台词音频并加入队列吗？")) return;
    try {
      const schedule = getLineAudioQueueSchedule();
      const result = await enqueueAllLineAudios(activeNovel.id, activeChapterNum, {
        scheduledAt: schedule.scheduledAt,
      });
      incrementNavBadge("lineAudio", Number(result.queued || 0));
      renderNav();
      if (Number(result.queued || 0) > 0) {
        lineAudioNoiseMarks = new Map();
        lineAudioQualityMarks = new Map();
        updateLineAudioNoiseBadge();
      }
      toast(`已加入队列: ${result.queued || 0} 个, 跳过: ${result.skipped?.length || 0} 个`);
      setStatus(schedule.label);
      await loadLineAudios();
      startLineAudioRefreshLoop();
    } catch (err) {
      toast(err.message);
    }
  });

  document.getElementById("enqueueRemainingLineAudioBtn")?.addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    const remainingIndexes = getRemainingLineIndexesForQueue();
    if (!remainingIndexes.length) {
      toast(translateText("当前没有可加入队列的剩余台词"));
      return;
    }
    if (!window.confirm(`确定生成剩余 ${remainingIndexes.length} 条台词音频并加入队列吗？`)) return;
    try {
      const schedule = getLineAudioQueueSchedule();
      let queuedCount = 0;
      for (const lineIndex of remainingIndexes) {
        await enqueueLineAudio(activeNovel.id, activeChapterNum, lineIndex, {
          scheduledAt: schedule.scheduledAt,
        });
        clearLineAudioNoiseMark(lineIndex);
        queuedCount += 1;
      }
      if (queuedCount > 0) {
        incrementNavBadge("lineAudio", queuedCount);
        renderNav();
      }
      setStatus(schedule.label);
      toast(`${translateText("剩余台词已加入队列")}: ${queuedCount}`);
      await loadLineAudios();
      startLineAudioRefreshLoop();
    } catch (err) {
      toast(err.message);
    }
  });

  document.getElementById("enqueueFilteredLineAudioBtn")?.addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    const filteredIndexes = getFilteredLineIndexesForQueue();
    if (!filteredIndexes.length) {
      toast(translateText("当前没有可加入队列的台词"));
      return;
    }
    if (!window.confirm(`确定生成当前所列 ${filteredIndexes.length} 条台词音频并加入队列吗？`)) return;
    try {
      const schedule = getLineAudioQueueSchedule();
      let queuedCount = 0;
      for (const lineIndex of filteredIndexes) {
        await enqueueLineAudio(activeNovel.id, activeChapterNum, lineIndex, {
          scheduledAt: schedule.scheduledAt,
        });
        clearLineAudioNoiseMark(lineIndex);
        queuedCount += 1;
      }
      if (queuedCount > 0) {
        incrementNavBadge("lineAudio", queuedCount);
        renderNav();
      }
      setStatus(schedule.label);
      toast(`${translateText("当前所列台词已加入队列")}: ${queuedCount}`);
      await loadLineAudios();
      startLineAudioRefreshLoop();
    } catch (err) {
      toast(err.message);
    }
  });

  document.getElementById("enqueueNonNarrationLineAudioBtn")?.addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    const indexes = getNonNarrationLineIndexesForQueue();
    if (!indexes.length) {
      toast(translateText("当前没有可加入队列的非旁白台词"));
      return;
    }
    if (!window.confirm(`确定生成所有非旁白 ${indexes.length} 条台词音频并加入队列吗？`)) return;
    try {
      const schedule = getLineAudioQueueSchedule();
      let queuedCount = 0;
      for (const lineIndex of indexes) {
        await enqueueLineAudio(activeNovel.id, activeChapterNum, lineIndex, {
          scheduledAt: schedule.scheduledAt,
        });
        clearLineAudioNoiseMark(lineIndex);
        queuedCount += 1;
      }
      if (queuedCount > 0) {
        incrementNavBadge("lineAudio", queuedCount);
        renderNav();
      }
      setStatus(schedule.label);
      toast(`${translateText("非旁白台词已加入队列")}: ${queuedCount}`);
      await loadLineAudios();
      startLineAudioRefreshLoop();
    } catch (err) {
      toast(err.message);
    }
  });

  document.getElementById("mergeLineAudioBtn")?.addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    const url = `./novel-download.html?novelId=${encodeURIComponent(activeNovel.id)}`;
    window.open(url, "_blank");
  });

  document.getElementById("detectAllLineAudioSilencesBtn")?.addEventListener("click", async () => {
    await detectAllLineAudioSilences();
  });

  document.getElementById("detectLineAudioNoiseBtn")?.addEventListener("click", async () => {
    await detectAllLineAudioNoise();
  });

  document.getElementById("removeAllLineAudioMarkedSegmentsBtn")?.addEventListener("click", async () => {
    await removeAllLineAudioMarkedSegments();
  });

  document.getElementById("lineRoleFilter")?.addEventListener("change", (event) => {
    activeLineRole = String(event.target.value || "__all");
    lineSearchIndex = -1;
    renderLineAudioTable();
  });

  document.getElementById("lineHasAudioFilter")?.addEventListener("change", (event) => {
    activeLineAudioFilter = String(event.target.value || "__all");
    lineSearchIndex = -1;
    renderLineAudioTable();
  });

  document.getElementById("lineMissingRoleFilter")?.addEventListener("change", (event) => {
    filterMissingRoleOnly = Boolean(event.target.checked);
    lineSearchIndex = -1;
    renderLineAudioTable();
  });

  document.getElementById("lineDunhaoMultiRoleFilter")?.addEventListener("change", (event) => {
    filterDunhaoMultiRoleOnly = Boolean(event.target.checked);
    lineSearchIndex = -1;
    renderLineAudioTable();
  });

  document.getElementById("lineSearchInput")?.addEventListener("input", () => {
    lineSearchIndex = -1;
    renderLineAudioTable();
  });

  document.getElementById("lineSearchInput")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      focusNextLineSearchMatch();
    }
  });

  document.getElementById("lineSearchNextBtn")?.addEventListener("click", () => {
    focusNextLineSearchMatch();
  });

  document.getElementById("lineAudioRefreshSelect")?.addEventListener("change", (event) => {
    saveLineAudioRefreshInterval(event.target.value);
    startLineAudioRefreshLoop();
  });

  document.getElementById("lineAudioManualRefreshBtn")?.addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    if (btn) btn.disabled = true;
    try {
      await loadLineAudios({ preserveEditing: true });
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  document.getElementById("mergeAdjacentSameRoleLinesBtn")?.addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    if (btn) btn.disabled = true;
    try {
      await mergeAdjacentSameRoleLines();
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  document.getElementById("toggleLineEditBtn")?.addEventListener("click", () => {
    lineEditEnabled = !lineEditEnabled;
    editingLineIndex = -1;
    editingLineOriginalText = "";
    updateLineAudioToolbarState();
    renderLineAudioTable();
    startLineAudioRefreshLoop();
  });

  document.getElementById("lineAudioDialog")?.addEventListener("close", () => {
    stopLineAudioRefreshLoop();
    editingLineIndex = -1;
    editingLineOriginalText = "";
  });

  document.getElementById("lineAudioEditorDialog")?.addEventListener("close", () => {
    destroyLineAudioEditor();
  });

  document.getElementById("lineAudioEditorDialog")?.addEventListener("keydown", async (event) => {
    await handleLineAudioEditorKeydown(event);
  });

  document.getElementById("lineAudioEditorPlayBtn")?.addEventListener("click", () => {
    playLineAudioEditorSelection();
  });

  document.getElementById("lineAudioEditorSaveBtn")?.addEventListener("click", async () => {
    await saveLineAudioEditorSelection();
  });

  document.getElementById("lineAudioEditorMarkDeleteBtn")?.addEventListener("click", () => {
    markLineAudioEditorDeleteRegion();
  });

  document.getElementById("lineAudioEditorClearDeleteBtn")?.addEventListener("click", () => {
    clearLineAudioEditorDeleteRegions();
  });

  document.getElementById("lineAudioEditorRemoveSaveBtn")?.addEventListener("click", async () => {
    await saveLineAudioEditorDeleteRegions();
  });

  document.getElementById("lineAudioEditorRemoveNoTrainSaveBtn")?.addEventListener("click", async () => {
    await saveLineAudioEditorDeleteRegions({ collectTrainingSamples: false });
  });

  document.getElementById("lineAudioEditorVolumeSaveBtn")?.addEventListener("click", async () => {
    await saveLineAudioEditorVolume();
  });

  document.getElementById("lineAudioEditorSilenceSaveBtn")?.addEventListener("click", async () => {
    await saveLineAudioEditorSilenceSelection();
  });

  document.getElementById("lineAudioEditorSpeedSaveBtn")?.addEventListener("click", async () => {
    await saveLineAudioEditorSpeed();
  });

  document.getElementById("lineAudioEditorMatchLoudnessBtn")?.addEventListener("click", () => {
    matchLineAudioEditorLoudness();
  });

  document.getElementById("lineAudioEditorNoiseDetectBtn")?.addEventListener("click", async () => {
    await detectLineAudioEditorNoise();
  });

  document.getElementById("lineAudioEditorNoiseFalsePositiveBtn")?.addEventListener("click", async () => {
    await recordLineAudioEditorNoiseFalsePositive();
  });

  document.getElementById("lineAudioEditorReplaceMatchingBtn")?.addEventListener("click", async () => {
    await replaceMatchingLineAudioFromEditor();
  });

  document.getElementById("lineAudioEditorStart")?.addEventListener("change", updateLineAudioEditorRegionFromInputs);
  document.getElementById("lineAudioEditorEnd")?.addEventListener("change", updateLineAudioEditorRegionFromInputs);
  document.getElementById("lineAudioEditorDbRange")?.addEventListener("input", (event) => {
    setLineAudioEditorVolumeDb(Number(event.target.value || 0));
  });
  document.getElementById("lineAudioEditorDbInput")?.addEventListener("change", (event) => {
    setLineAudioEditorVolumeDb(Number(event.target.value || 0));
  });
  document.getElementById("lineAudioEditorSpeedRange")?.addEventListener("input", (event) => {
    setLineAudioEditorSpeed(Number(event.target.value || 1));
  });
  document.getElementById("lineAudioEditorSpeedInput")?.addEventListener("change", (event) => {
    setLineAudioEditorSpeed(Number(event.target.value || 1));
  });
  document.getElementById("lineAudioEditorZoomRange")?.addEventListener("input", (event) => {
    setLineAudioEditorZoom(Number(event.target.value || 0));
  });
  document.getElementById("lineAudioEditorZoomOutBtn")?.addEventListener("click", () => {
    setLineAudioEditorZoom(lineAudioEditorZoom - 20);
  });
  document.getElementById("lineAudioEditorZoomInBtn")?.addEventListener("click", () => {
    setLineAudioEditorZoom(lineAudioEditorZoom + 20);
  });
  document.getElementById("lineAudioEditorZoomResetBtn")?.addEventListener("click", () => {
    setLineAudioEditorZoom(0);
  });

  const player = document.getElementById("chapterAudioPlayer");
  const duration = document.getElementById("chapterAudioDuration");
  player.addEventListener("loadedmetadata", () => {
    duration.textContent = `时长：${fmtDuration(player.duration)}`;
  });
  player.addEventListener("durationchange", () => {
    duration.textContent = `时长：${fmtDuration(player.duration)}`;
  });
  player.addEventListener("error", () => {
    duration.textContent = translateText("时长：读取失败");
  });

}

// ============ 台词音频功能 ============

function extractRoleName(line) {
  const text = String(line || "").trim();
  if (!text) return "";
  const positions = [text.indexOf(":"), text.indexOf("：")].filter((pos) => pos >= 0);
  if (!positions.length) return "";
  const splitAt = Math.min(...positions);
  return String(text.slice(0, splitAt) || "").trim().slice(0, 20);
}

function parseJubenLineForMerge(line) {
  const raw = String(line || "").trim();
  if (!raw) return { raw: String(line || ""), roleName: "", separator: "", text: "" };
  const positions = [raw.indexOf(":"), raw.indexOf("：")].filter((pos) => pos >= 0);
  if (!positions.length) return { raw, roleName: "", separator: "", text: raw };
  const splitAt = Math.min(...positions);
  return {
    raw,
    roleName: String(raw.slice(0, splitAt) || "").trim(),
    separator: raw.slice(splitAt, splitAt + 1) || ":",
    text: String(raw.slice(splitAt + 1) || "").trim(),
  };
}

function composeJubenLine(roleName, separator, text) {
  return `${roleName}${separator || ":"}${text}`;
}

function partitionRunByCount(items, groupCount) {
  const groups = [];
  for (let index = 0; index < groupCount; index += 1) {
    const start = Math.floor((index * items.length) / groupCount);
    const end = Math.floor(((index + 1) * items.length) / groupCount);
    const group = items.slice(start, end);
    if (group.length) groups.push(group);
  }
  return groups;
}

function mergeSameRoleRun(run, maxChars = MERGE_ADJACENT_LINE_MAX_CHARS) {
  for (let groupCount = 1; groupCount <= run.length; groupCount += 1) {
    const groups = partitionRunByCount(run, groupCount);
    const valid = groups.every((group) => getLineCharCount(group.map((item) => item.text).join("")) <= maxChars);
    if (!valid) continue;
    const rows = groups.map((group) => {
      if (group.length === 1) return group[0].raw;
      return composeJubenLine(group[0].roleName, group[0].separator, group.map((item) => item.text).join(""));
    });
    return {
      rows,
      mergedCount: groups.reduce((count, group) => count + Math.max(0, group.length - 1), 0),
      mergedGroups: groups.filter((group) => group.length > 1).length,
      overlongCount: 0,
    };
  }
  return {
    rows: run.map((item) => item.raw),
    mergedCount: 0,
    mergedGroups: 0,
    overlongCount: run.filter((item) => getLineCharCount(item.text) > maxChars).length,
  };
}

function mergeAdjacentSameRoleJubenRows(rows, maxChars = MERGE_ADJACENT_LINE_MAX_CHARS) {
  const parsedRows = rows.map((line) => parseJubenLineForMerge(line));
  const nextRows = [];
  let mergedCount = 0;
  let mergedGroups = 0;
  let overlongCount = 0;

  for (let index = 0; index < parsedRows.length;) {
    const current = parsedRows[index];
    if (!current.raw.trim() || !current.roleName) {
      nextRows.push(String(rows[index] || ""));
      index += 1;
      continue;
    }

    const run = [current];
    let nextIndex = index + 1;
    while (nextIndex < parsedRows.length && parsedRows[nextIndex].roleName === current.roleName) {
      run.push(parsedRows[nextIndex]);
      nextIndex += 1;
    }

    if (run.length === 1) {
      nextRows.push(current.raw);
    } else {
      const result = mergeSameRoleRun(run, maxChars);
      nextRows.push(...result.rows);
      mergedCount += result.mergedCount;
      mergedGroups += result.mergedGroups;
      overlongCount += result.overlongCount;
    }
    index = nextIndex;
  }

  return { rows: nextRows, mergedCount, mergedGroups, overlongCount };
}

function getJubenLinesFromParsed(parsed) {
  const juben = String(parsed?.juben || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (!juben.trim()) {
    return [];
  }
  return juben
    .split("\n")
    .map((line, index) => ({
      raw: String(line || ""),
      index,
    }))
    .map((item) => ({
      ...item,
      line: String(item.raw || "").trim(),
    }))
    .filter((item) => item.line)
    .map((item) => ({
      index: item.index,
      line: item.line,
      roleName: extractRoleName(item.line),
    }));
}

function getLinePreviewRow(lineIndex) {
  return linePreviewRows.find((item) => Number(item.index) === Number(lineIndex)) || null;
}

function syncLineRoleFilterOptions() {
  const select = document.getElementById("lineRoleFilter");
  if (!select) return;
  const roleNames = Array.from(new Set(linePreviewRows.map((item) => item.roleName).filter(Boolean)));
  select.innerHTML = "";
  const allOpt = document.createElement("option");
  allOpt.value = "__all";
  allOpt.textContent = "全部";
  select.appendChild(allOpt);
  for (const name of roleNames) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  }
  if (!roleNames.includes(activeLineRole)) {
    activeLineRole = "__all";
  }
  select.value = activeLineRole;
}

function getLineAudioEntry(lineIndex) {
  return lineAudioEntries.find((item) => Number(item.lineIndex) === Number(lineIndex)) || null;
}

function formatLineAudioSeconds(seconds) {
  const value = Number(seconds || 0);
  if (!Number.isFinite(value) || value <= 0) return "-";
  return `${value.toFixed(1)}秒`;
}

function getLineCharCount(line) {
  return String(line || "").replace(/\s+/g, "").length;
}

function getLineCharCountClass(count) {
  if (count >= 180) return "is-long";
  if (count >= 90) return "is-medium";
  return "is-short";
}

function getLineAudioAnomaly(row, entry) {
  const duration = Number(entry?.durationSeconds || 0);
  if (!entry?.hasAudio || duration <= 0) return { abnormal: false, reason: "" };
  const charCount = getLineCharCount(entry?.lineText || row?.line || "");
  if (charCount <= 0) return { abnormal: false, reason: "" };
  const efficiency = charCount / duration;
  const theoretical = charCount * 0.35;
  const maxAllowed = Math.max(8, theoretical * 3);
  if (efficiency < 0.5) {
    return { abnormal: true, reason: `字符效率 ${efficiency.toFixed(2)} 字/秒，低于 0.5` };
  }
  if (charCount <= 5 && duration > 4) {
    return { abnormal: true, reason: `超短台词 ${charCount} 字，音频 ${duration.toFixed(1)} 秒` };
  }
  if (charCount <= 15 && duration > 15) {
    return { abnormal: true, reason: `短台词 ${charCount} 字，音频 ${duration.toFixed(1)} 秒` };
  }
  if (duration > maxAllowed) {
    return { abnormal: true, reason: `音频 ${duration.toFixed(1)} 秒，超过允许 ${maxAllowed.toFixed(1)} 秒` };
  }
  return { abnormal: false, reason: "" };
}

function getIncompleteLongLineAudioMark(row, entry) {
  const duration = Number(entry?.durationSeconds || 0);
  if (!entry?.hasAudio || duration <= 0) return null;
  const charCount = getLineCharCount(entry?.lineText || row?.line || "");
  if (charCount < 100) return null;
  const efficiency = charCount / duration;
  const minSecondsPerChar = charCount >= 180 ? 0.2 : 0.16;
  const minExpectedDuration = charCount * minSecondsPerChar;
  const isClearlyTooShort =
    charCount >= 180
      ? duration < minExpectedDuration && efficiency >= 5.6
      : duration < minExpectedDuration && efficiency >= 7.5;
  if (!isClearlyTooShort) return null;
  return {
    status: "abnormal",
    score: 85,
    segments: [{ start: 0, end: duration, type: "incomplete_long_line_audio" }],
    reasons: [
      `长台词 ${charCount} 字，但音频仅 ${duration.toFixed(1)} 秒`,
      `字符效率 ${efficiency.toFixed(1)} 字/秒，疑似只读了一部分台词`,
      "建议重新生成该台词音频",
    ],
  };
}

function getLineAudioViewState(entry) {
  let statusText = "未生成";
  let statusClass = "text-muted";
  let disabled = false;
  let hasAudio = false;
  let src = "";

  if (entry) {
    if (entry.hasAudio && entry.streamUrl) {
      statusText = "已生成";
      statusClass = "status-completed";
      hasAudio = true;
      const version = String(entry.task?.updatedAt || entry.lineHash || "0");
      src = `${entry.streamUrl}?v=${encodeURIComponent(version)}`;
    } else if (entry.task?.status === "pending") {
      statusText = "待生成";
      statusClass = "status-pending";
      disabled = true;
    } else if (entry.task?.status === "processing") {
      statusText = "生成中";
      statusClass = "status-processing";
      disabled = true;
    } else if (entry.task?.status === "failed") {
      statusText = "生成失败";
      statusClass = "status-failed";
    } else if (!entry.roleInLibrary) {
      statusText = "角色未加入角色库";
      statusClass = "status-failed";
      disabled = true;
    } else if (!entry.roleHasSampleAudio) {
      statusText = "缺少角色示例音频";
      statusClass = "status-failed";
      disabled = true;
    }
  }

  return { statusText, statusClass, disabled, hasAudio, src };
}

function getLineAudioSilenceMark(lineIndex) {
  return lineAudioSilenceMarks.get(Number(lineIndex)) || null;
}

function getLineAudioSilenceMarkCount(lineIndex) {
  const mark = getLineAudioSilenceMark(lineIndex);
  return Array.isArray(mark?.segments) ? mark.segments.length : 0;
}

function getLineAudioNoiseMark(lineIndex) {
  const index = Number(lineIndex);
  return lineAudioNoiseMarks.get(index) || lineAudioQualityMarks.get(index) || null;
}

function getLineAudioNoiseMarkCount() {
  return Array.from(lineAudioNoiseMarks.values()).filter(
    (mark) => Array.isArray(mark?.segments) && mark.segments.length,
  ).length;
}

function isLineAudioRegenerateRecommendedMark(mark) {
  const segments = Array.isArray(mark?.segments) ? mark.segments : [];
  return segments.some((segment) => ["incomplete_long_line_audio", "short_line_audio_too_long"].includes(String(segment?.type || "")));
}

function isLineAudioNonDeleteSegment(segment) {
  return ["incomplete_long_line_audio", "short_line_audio_too_long"].includes(String(segment?.type || ""));
}

function updateLineAudioEditorWarning(mark = null) {
  const warning = document.getElementById("lineAudioEditorWarning");
  if (!warning) return;
  if (!mark) {
    warning.classList.add("hidden");
    warning.textContent = "";
    return;
  }
  const reasons = Array.isArray(mark.reasons) ? mark.reasons.filter(Boolean) : [];
  const shouldRegenerate = isLineAudioRegenerateRecommendedMark(mark);
  warning.textContent = shouldRegenerate
    ? `建议重新生成该台词音频：${reasons.join("；") || "检测到音频疑似未完整读出台词。"}`
    : `音频异常：${reasons.join("；") || "建议试听确认。"}`;
  warning.classList.remove("hidden");
}

function updateLineAudioEditorFalsePositiveButton() {
  const btn = document.getElementById("lineAudioEditorNoiseFalsePositiveBtn");
  if (!btn) return;
  btn.classList.toggle("hidden", !lineAudioEditorNoiseSegments.length);
}

function updateLineAudioNoiseBadge() {
  const badge = document.getElementById("detectLineAudioNoiseBadge");
  if (!badge) return;
  const count = getLineAudioNoiseMarkCount();
  badge.textContent = String(count);
  badge.classList.toggle("hidden", count <= 0);
}

function clearLineAudioNoiseMark(lineIndex) {
  const index = Number(lineIndex);
  if (!lineAudioNoiseMarks.has(index) && !lineAudioQualityMarks.has(index)) return;
  lineAudioNoiseMarks.delete(index);
  lineAudioQualityMarks.delete(index);
  updateLineAudioNoiseBadge();
  updateLineAudioRow(index);
}

function updateLineAudioMarkedSegmentsBadge() {
  const badge = document.getElementById("removeAllLineAudioMarkedSegmentsBadge");
  if (!badge) return;
  const count = Array.from(lineAudioSilenceMarks.values()).filter(
    (mark) => Array.isArray(mark?.segments) && mark.segments.length,
  ).length;
  badge.textContent = String(count);
  badge.classList.toggle("hidden", count <= 0);
}

function renderLineAudioEditButton(lineIndex) {
  const count = getLineAudioSilenceMarkCount(lineIndex);
  const noiseMark = getLineAudioNoiseMark(lineIndex);
  const badge = count ? `<span class="line-audio-edit-badge">${count}</span>` : "";
  const noiseBadge = noiseMark ? `<span class="line-audio-noise-badge" title="音频异常：${escapeHtml(noiseMark.reasons?.join("；") || "请检查音频")}">!</span>` : "";
  return `<button class="ghost-btn btn-sm edit-line-audio-btn" data-line-index="${lineIndex}" type="button">${noiseBadge}编辑音频${badge}</button>`;
}

function getFilteredLinePreviewRows() {
  return linePreviewRows.filter((row) => {
    if (activeLineRole !== "__all" && row.roleName !== activeLineRole) {
      return false;
    }
    if (filterMissingRoleOnly) {
      const entry = getLineAudioEntry(row.index);
      if (entry?.roleInLibrary !== false) {
        return false;
      }
    }
    if (filterDunhaoMultiRoleOnly && !String(row.roleName || "").includes("、")) {
      return false;
    }
    if (activeLineAudioFilter !== "__all") {
      const entry = getLineAudioEntry(row.index);
      const hasAudio = Boolean(entry && entry.hasAudio && entry.streamUrl);
      if (activeLineAudioFilter === "with" && !hasAudio) {
        return false;
      }
      if (activeLineAudioFilter === "without" && hasAudio) {
        return false;
      }
    }
    return true;
  });
}

function getRemainingLineIndexesForQueue() {
  return linePreviewRows
    .filter((row) => {
      const entry = getLineAudioEntry(row.index);
      if (!entry || !entry.canGenerate) return false;
      if (entry.hasAudio && entry.streamUrl) return false;
      const taskStatus = String(entry.task?.status || "").trim();
      return !["pending", "processing", "running", "completed"].includes(taskStatus);
    })
    .map((row) => row.index);
}

function getFilteredLineIndexesForQueue() {
  return getFilteredLinePreviewRows()
    .filter((row) => {
      const entry = getLineAudioEntry(row.index);
      if (!entry || !entry.canGenerate) return false;
      const taskStatus = String(entry.task?.status || "").trim();
      return !["pending", "processing", "running"].includes(taskStatus);
    })
    .map((row) => row.index);
}

function getNonNarrationLineIndexesForQueue() {
  return linePreviewRows
    .filter((row) => String(row.roleName || "").trim() !== "旁白")
    .filter((row) => {
      const entry = getLineAudioEntry(row.index);
      if (!entry || !entry.canGenerate) return false;
      const taskStatus = String(entry.task?.status || "").trim();
      return !["pending", "processing", "running"].includes(taskStatus);
    })
    .map((row) => row.index);
}

function getMissingRoleLinePreviewRows(rows = linePreviewRows) {
  return rows.filter((row) => {
    const entry = getLineAudioEntry(row.index);
    return entry?.roleInLibrary === false;
  });
}

function formatMissingRoleLineWarning(rows) {
  if (!rows.length) return "";
  const roleNames = Array.from(new Set(rows.map((row) => String(row.roleName || "").trim()).filter(Boolean)));
  const lineNos = rows.map((row) => String(Number(row.index) + 1).padStart(3, "0")).slice(0, 8);
  const roleText = roleNames.slice(0, 6).join("、");
  const lineText = lineNos.join("、");
  const moreRoles = roleNames.length > 6 ? ` 等 ${roleNames.length} 个角色` : "";
  const moreLines = rows.length > 8 ? ` 等 ${rows.length} 行` : "";
  return `检测到角色未加入角色库：${roleText}${moreRoles}；涉及行号：${lineText}${moreLines}。请先加入角色库后再批量生成所有。`;
}

function getLineSearchMatches(rows) {
  const query = String(document.getElementById("lineSearchInput")?.value || "").trim();
  if (!query) {
    return [];
  }
  return rows.filter((row) => row.line.includes(query));
}

function updateLineAudioToolbarState() {
  const toggleBtn = document.getElementById("toggleLineEditBtn");
  if (toggleBtn) {
    toggleBtn.textContent = lineEditEnabled ? "结束编辑" : "编辑台词";
  }
  const roleFilter = document.getElementById("lineRoleFilter");
  const audioFilter = document.getElementById("lineHasAudioFilter");
  const missingRoleFilter = document.getElementById("lineMissingRoleFilter");
  const dunhaoMultiRoleFilter = document.getElementById("lineDunhaoMultiRoleFilter");
  if (roleFilter) roleFilter.disabled = Boolean(lineEditEnabled);
  if (audioFilter) audioFilter.disabled = Boolean(lineEditEnabled);
  if (missingRoleFilter) missingRoleFilter.disabled = Boolean(lineEditEnabled);
  if (dunhaoMultiRoleFilter) dunhaoMultiRoleFilter.disabled = Boolean(lineEditEnabled);
}

function focusLineAudioAnomalyRow(row) {
  if (!row) return;
  activeLineRole = "__all";
  activeLineAudioFilter = "with";
  filterMissingRoleOnly = false;
  filterDunhaoMultiRoleOnly = false;
  lineSearchIndex = -1;
  const roleFilter = document.getElementById("lineRoleFilter");
  const audioFilter = document.getElementById("lineHasAudioFilter");
  const missingRoleFilter = document.getElementById("lineMissingRoleFilter");
  const dunhaoMultiRoleFilter = document.getElementById("lineDunhaoMultiRoleFilter");
  const searchInput = document.getElementById("lineSearchInput");
  if (roleFilter) roleFilter.value = activeLineRole;
  if (audioFilter) audioFilter.value = activeLineAudioFilter;
  if (missingRoleFilter) missingRoleFilter.checked = false;
  if (dunhaoMultiRoleFilter) dunhaoMultiRoleFilter.checked = false;
  if (searchInput) searchInput.value = "";
  renderLineAudioTable();
  window.requestAnimationFrame(() => {
    const target = document.querySelector(`.juben-line[data-line-index="${row.index}"]`);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.add("juben-line-anomaly-focus");
    window.setTimeout(() => target.classList.remove("juben-line-anomaly-focus"), 1600);
  });
}

function stopLineAudioRefreshLoop() {
  if (lineAudioRefreshTimerId) {
    window.clearInterval(lineAudioRefreshTimerId);
    lineAudioRefreshTimerId = null;
  }
}

function refreshVisibleLineAudioRows() {
  getFilteredLinePreviewRows().forEach((row) => {
    updateLineAudioRow(row.index);
  });
}

function hasPlayingLineAudio() {
  const dialog = document.getElementById("lineAudioDialog");
  if (!dialog) return false;
  return Array.from(dialog.querySelectorAll("audio")).some(
    (player) => !player.paused && !player.ended
  );
}

function startLineAudioRefreshLoop() {
  stopLineAudioRefreshLoop();
  const dialog = document.getElementById("lineAudioDialog");
  if (!dialog?.open || !activeNovel || !activeChapterNum || lineAudioRefreshIntervalSeconds <= 0) return;
  lineAudioRefreshTimerId = window.setInterval(async () => {
    if (!dialog.open) {
      stopLineAudioRefreshLoop();
      return;
    }
    if (hasPlayingLineAudio()) {
      return;
    }
    await loadLineAudios({ silent: true, preserveEditing: true });
  }, lineAudioRefreshIntervalSeconds * 1000);
}

function updateLineAudioRow(lineIndex) {
  const rowEl = document.querySelector(`.juben-line[data-line-index="${lineIndex}"]`);
  if (!rowEl) return;
  const audioCell = rowEl.querySelector(".juben-line-audio");
  if (!audioCell) return;

  const entry = getLineAudioEntry(lineIndex);
  const view = getLineAudioViewState(entry);
  const row = getLinePreviewRow(lineIndex);
  const anomaly = getLineAudioAnomaly(row, entry);
  rowEl.classList.toggle("juben-line-audio-anomaly", anomaly.abnormal);
  rowEl.title = anomaly.abnormal ? `检测到音频时长异常，可能存在生成失败或尾部静音。${anomaly.reason}。建议重新生成。` : "";
  if (view.hasAudio) {
    const durationText = formatLineAudioSeconds(entry?.durationSeconds);
    audioCell.innerHTML = `
      <audio controls preload="metadata" src="${escapeHtml(view.src)}"></audio>
      <span class="juben-line-duration${anomaly.abnormal ? " is-anomaly" : ""}">${anomaly.abnormal ? "⚠ " : ""}${escapeHtml(durationText)}</span>
      <span class="${view.statusClass}">${escapeHtml(view.statusText)}</span>
      ${renderLineAudioEditButton(lineIndex)}
      <button class="ghost-btn btn-sm enqueue-line-btn" data-line-index="${lineIndex}" type="button">生成音频</button>
    `;
  } else {
    audioCell.innerHTML = `
      <span class="${view.statusClass}">${escapeHtml(view.statusText)}</span>
      <button class="ghost-btn btn-sm enqueue-line-btn" data-line-index="${lineIndex}" type="button"${view.disabled ? " disabled" : ""}>生成音频</button>
    `;
  }
  bindLineAudioButtons(audioCell);
}

async function openLineAudioDialog() {
  if (!activeNovel || !activeChapterNum) {
    toast("请先选择章节");
    return;
  }
  const parsed = await loadChapterJsonCache();
  if (!jsonViewRawText) {
    toast("请先完成JSON转换");
    return;
  }
  if (!parsed) {
    toast("JSON解析失败，无法查看台词");
    return;
  }
  linePreviewRows = getJubenLinesFromParsed(parsed);
  if (!linePreviewRows.length) {
    toast("该 JSON 没有 juben 数据");
    return;
  }
  lineEditEnabled = false;
  editingLineIndex = -1;
  editingLineOriginalText = "";
  lineSearchIndex = -1;
  lineAudioSilenceMarks = new Map();
  lineAudioNoiseMarks = new Map();
  lineAudioQualityMarks = new Map();
  updateLineAudioMarkedSegmentsBadge();
  updateLineAudioNoiseBadge();
  clearLineAudioBatchProcessingLine();
  activeLineAudioRowIndex = getSavedLastLineAudioRowIndex();
  syncLineAudioRefreshSelect();
  syncLineRoleFilterOptions();
  updateLineAudioToolbarState();
  await loadLineAudios();
  document.getElementById("lineAudioDialog")?.showModal();
  restoreActiveLineAudioRow();
  startLineAudioRefreshLoop();
}

async function loadLineAudios(options = {}) {
  if (!activeNovel || !activeChapterNum) return;
  try {
    lineAudioEntries = await fetchChapterLineAudios(activeNovel.id, activeChapterNum);
    if (options.preserveEditing && lineEditEnabled && editingLineIndex >= 0) {
      refreshVisibleLineAudioRows();
      return;
    }
    if (options.partialLineIndex != null && hasPlayingLineAudio()) {
      updateLineAudioRow(Number(options.partialLineIndex));
      return;
    }
    renderLineAudioTable();
  } catch (err) {
    if (!options.silent) {
      toast(t("error.loadFailed", { msg: err.message }));
    }
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function renderLineAudioTable() {
  const root = document.getElementById("lineAudioTableBody");
  const countEl = document.getElementById("lineAudioFilteredCount");
  if (!root) return;

  syncLineRoleFilterOptions();
  updateLineAudioToolbarState();

  const rows = getFilteredLinePreviewRows();
  const matches = getLineSearchMatches(rows);
  const allAudioRows = linePreviewRows.filter((row) => {
    const entry = getLineAudioEntry(row.index);
    return Boolean(entry?.hasAudio && entry?.streamUrl);
  });
  const anomalyRows = allAudioRows.filter((row) => {
    const entry = getLineAudioEntry(row.index);
    return getLineAudioAnomaly(row, entry).abnormal;
  });
  const anomalyCount = anomalyRows.length;
  lineAudioAnomalyCount = anomalyCount;
  if (countEl) {
    countEl.innerHTML = `${translateText("筛选")} ${rows.length} ${translateText("条")} <span class="line-audio-anomaly-summary">异常音频：${anomalyCount}</span>${anomalyCount ? ' <button id="jumpFirstLineAudioAnomalyBtn" class="ghost-btn btn-sm line-audio-anomaly-jump-btn" type="button">跳转</button>' : ""}`;
    countEl.querySelector("#jumpFirstLineAudioAnomalyBtn")?.addEventListener("click", () => {
      const first = anomalyRows[0];
      focusLineAudioAnomalyRow(first);
    });
  }
  updateLineAudioSilenceActionButtons();
  root.innerHTML = "";

  if (!rows.length) {
    root.innerHTML = '<p class="empty-text">当前筛选条件下暂无台词。</p>';
    localizeDocumentText(document);
    return;
  }

  const head = document.createElement("div");
  head.className = "juben-line-head";
  head.innerHTML = "<span>行号</span><span>台词</span><span>台词音频</span>";
  root.appendChild(head);

  const list = document.createElement("div");
  list.className = "juben-lines";

  for (const row of rows) {
    const entry = getLineAudioEntry(row.index);
    const view = getLineAudioViewState(entry);
    const item = document.createElement("div");
    const isEditing = editingLineIndex === row.index;
    const isMatched = matches.some((match) => match.index === row.index);
    const isActiveLine = activeLineAudioRowIndex === row.index;
    const isBatchProcessing = lineAudioBatchProcessingIndex === row.index;
    const anomaly = getLineAudioAnomaly(row, entry);
    const noiseMark = getLineAudioNoiseMark(row.index);
    item.className = `juben-line${isEditing ? " juben-line-single-editing" : ""}${isMatched ? " juben-line-search-hit" : ""}${isActiveLine ? " juben-line-active" : ""}${isBatchProcessing ? " juben-line-batch-processing" : ""}${anomaly.abnormal ? " juben-line-audio-anomaly" : ""}${noiseMark ? " juben-line-audio-noise" : ""}`;
    item.title = noiseMark
      ? `音频异常，评分 ${Number(noiseMark.score || 0)}。${noiseMark.reasons?.join("；") || "建议试听确认。"}`
      : (anomaly.abnormal ? `检测到音频时长异常，可能存在生成失败或尾部静音。${anomaly.reason}。建议重新生成。` : "");
    item.dataset.lineIndex = String(row.index);

    const meta = document.createElement("div");
    meta.className = "juben-line-meta";

    const no = document.createElement("span");
    no.className = "juben-line-no";
    no.textContent = String(row.index + 1).padStart(3, "0");

    const charCount = getLineCharCount(row.line);
    const count = document.createElement("span");
    count.className = `juben-line-count ${getLineCharCountClass(charCount)}`;
    count.textContent = String(charCount);

    meta.appendChild(no);
    meta.appendChild(count);
    item.appendChild(meta);

    const textCell = document.createElement("div");
    textCell.className = "juben-line-text-cell";

    if (isEditing) {
      const input = document.createElement("textarea");
      input.className = "juben-line-input juben-line-single-input";
      input.rows = 2;
      input.value = row.line;
      input.dataset.lineIndex = String(row.index);

      const saveBtn = document.createElement("button");
      saveBtn.className = "juben-line-save-btn";
      saveBtn.type = "button";
      saveBtn.textContent = "保存";
      saveBtn.dataset.lineIndex = String(row.index);

      textCell.appendChild(input);
      textCell.appendChild(saveBtn);
    } else {
      const text = document.createElement("span");
      text.className = "juben-line-text";
      text.textContent = row.line;
      textCell.appendChild(text);

      if (lineEditEnabled) {
        const editIcon = document.createElement("span");
        editIcon.className = "juben-line-edit-icon";
        editIcon.textContent = "✎";
        editIcon.title = "编辑台词";
        editIcon.dataset.lineIndex = String(row.index);
        textCell.appendChild(editIcon);
      }
    }

    item.appendChild(textCell);

    const audioCell = document.createElement("div");
    audioCell.className = "juben-line-audio";
    if (view.hasAudio) {
      const durationText = formatLineAudioSeconds(entry?.durationSeconds);
      audioCell.innerHTML = `
        <audio controls preload="metadata" src="${escapeHtml(view.src)}"></audio>
        <span class="juben-line-duration${anomaly.abnormal ? " is-anomaly" : ""}">${anomaly.abnormal ? "⚠ " : ""}${escapeHtml(durationText)}</span>
        <span class="${view.statusClass}">${escapeHtml(view.statusText)}</span>
        ${renderLineAudioEditButton(row.index)}
        <button class="ghost-btn btn-sm enqueue-line-btn" data-line-index="${row.index}" type="button">生成音频</button>
      `;
    } else {
      audioCell.innerHTML = `
        <span class="${view.statusClass}">${escapeHtml(view.statusText)}</span>
        <button class="ghost-btn btn-sm enqueue-line-btn" data-line-index="${row.index}" type="button"${view.disabled ? " disabled" : ""}>生成音频</button>
      `;
    }
    item.appendChild(audioCell);
    list.appendChild(item);
  }

  root.appendChild(list);
  bindLineAudioRowActivation(root);
  bindLineAudioButtons(root);
  bindLineEditingEvents(root);
  localizeDocumentText(document);
}

function setActiveLineAudioRow(lineIndex, options = {}) {
  const index = Number(lineIndex);
  if (!Number.isFinite(index) || index < 0) return;
  activeLineAudioRowIndex = index;
  saveLastLineAudioRowIndex(index);
  document.querySelectorAll(".juben-line-active").forEach((el) => {
    el.classList.remove("juben-line-active");
  });
  const target = document.querySelector(`.juben-line[data-line-index="${index}"]`);
  if (!target) return;
  target.classList.add("juben-line-active");
  if (options.scroll) {
    target.scrollIntoView({ behavior: options.smooth ? "smooth" : "auto", block: "center" });
  }
}

function restoreActiveLineAudioRow() {
  const savedIndex = getSavedLastLineAudioRowIndex();
  if (savedIndex < 0 || !getLinePreviewRow(savedIndex)) return;
  activeLineAudioRowIndex = savedIndex;
  window.setTimeout(() => setActiveLineAudioRow(savedIndex, { scroll: true }), 0);
}

function bindLineAudioRowActivation(root) {
  root.querySelectorAll(".juben-line[data-line-index]").forEach((row) => {
    if (row.dataset.activeBound === "1") return;
    row.dataset.activeBound = "1";
    row.addEventListener("click", (event) => {
      if (event.target.closest("button, audio, input, textarea, select, a")) return;
      setActiveLineAudioRow(Number(row.dataset.lineIndex || -1));
    });
  });
}

function getLineAudioEditableRows() {
  return linePreviewRows
    .map((row) => ({ row, entry: getLineAudioEntry(row.index) }))
    .filter(({ entry }) => Boolean(entry?.hasAudio && entry?.streamUrl && entry?.task?.id));
}

function setLineAudioBatchProcessingLine(lineIndex, options = {}) {
  const previous = lineAudioBatchProcessingIndex;
  lineAudioBatchProcessingIndex = Number(lineIndex);
  if (previous >= 0) {
    document.querySelector(`.juben-line[data-line-index="${previous}"]`)?.classList.remove("juben-line-batch-processing");
  }
  const target = document.querySelector(`.juben-line[data-line-index="${lineAudioBatchProcessingIndex}"]`);
  if (!target) return;
  target.classList.add("juben-line-batch-processing");
  if (options.scroll) {
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function clearLineAudioBatchProcessingLine() {
  if (lineAudioBatchProcessingIndex >= 0) {
    document.querySelector(`.juben-line[data-line-index="${lineAudioBatchProcessingIndex}"]`)?.classList.remove("juben-line-batch-processing");
  }
  lineAudioBatchProcessingIndex = -1;
}

function setLineAudioBatchButtonsDisabled(disabled) {
  lineAudioBatchBusy = Boolean(disabled);
  updateLineAudioSilenceActionButtons();
}

function updateLineAudioSilenceActionButtons() {
  const disabled = lineAudioBatchBusy || lineAudioAnomalyCount > 0;
  document.getElementById("detectLineAudioNoiseBtn")?.toggleAttribute("disabled", lineAudioBatchBusy);
  document.getElementById("detectAllLineAudioSilencesBtn")?.toggleAttribute("disabled", disabled);
  document.getElementById("removeAllLineAudioMarkedSegmentsBtn")?.toggleAttribute("disabled", disabled);
}

async function detectAllLineAudioSilences() {
  if (!activeNovel || !activeChapterNum || lineAudioBatchBusy) return;
  if (lineAudioAnomalyCount > 0) return;
  const items = getLineAudioEditableRows();
  if (!items.length) {
    toast("当前章节没有可检测的台词音频");
    return;
  }
  lineAudioSilenceMarks = new Map();
  updateLineAudioMarkedSegmentsBadge();
  setLineAudioBatchButtonsDisabled(true);
  let markedCount = 0;
  let incompleteCount = 0;
  try {
    for (const { row, entry } of items) {
      setLineAudioBatchProcessingLine(row.index, { scroll: true });
      try {
        const data = await detectLineAudioTaskSilences(entry.task.id, { noiseDb: "-45dB", minDuration: 1.2 });
        const duration = Number(entry.durationSeconds || data.durationSeconds || 0);
        const segments = normalizeLineAudioEditorSegments(
          reserveLineAudioEditorMiddleSilence(data.segments || [], duration),
          duration,
        );
        if (segments.length) {
          lineAudioSilenceMarks.set(Number(row.index), {
            taskId: Number(entry.task.id),
            segments,
          });
          markedCount += 1;
          updateLineAudioMarkedSegmentsBadge();
        }
        const incompleteMark = getIncompleteLongLineAudioMark(row, entry);
        if (incompleteMark) {
          lineAudioQualityMarks.set(Number(row.index), {
            taskId: Number(entry.task.id),
            ...incompleteMark,
          });
          incompleteCount += 1;
        }
        updateLineAudioRow(row.index);
      } catch (err) {
        console.warn("detect line audio silence failed", row.index, err);
      }
    }
    toast(`已检测 ${items.length} 条台词音频，${markedCount} 条存在长空片段，${incompleteCount} 条疑似长台词未读完`);
  } finally {
    clearLineAudioBatchProcessingLine();
    setLineAudioBatchButtonsDisabled(false);
  }
}

async function detectAllLineAudioNoise() {
  if (!activeNovel || !activeChapterNum || lineAudioBatchBusy) return;
  const items = getLineAudioEditableRows();
  if (!items.length) {
    toast("当前章节没有可检测的台词音频");
    return;
  }
  lineAudioNoiseMarks = new Map();
  updateLineAudioNoiseBadge();
  setLineAudioBatchButtonsDisabled(true);
  let markedCount = 0;
  try {
    for (const { row, entry } of items) {
      setLineAudioBatchProcessingLine(row.index, { scroll: true });
      try {
        const data = await detectLineAudioTaskNoise(entry.task.id, { sensitivity: "strict" });
        const segments = Array.isArray(data.segments) ? data.segments : [];
        if (segments.length && ["suspicious", "abnormal"].includes(String(data.status || ""))) {
          const reasons = segments.flatMap((segment) => Array.isArray(segment.reasons) ? segment.reasons : []);
          lineAudioNoiseMarks.set(Number(row.index), {
            taskId: Number(entry.task.id),
            status: String(data.status || "suspicious"),
            score: Number(data.score || 0),
            segments,
            reasons,
          });
          markedCount += 1;
          updateLineAudioNoiseBadge();
        }
        updateLineAudioRow(row.index);
      } catch (err) {
        console.warn("detect line audio noise failed", row.index, err);
      }
    }
    toast(`已检测 ${items.length} 条台词音频，${markedCount} 条疑似含有噪音`);
  } finally {
    clearLineAudioBatchProcessingLine();
    setLineAudioBatchButtonsDisabled(false);
  }
}

async function removeAllLineAudioMarkedSegments() {
  if (!activeNovel || !activeChapterNum || lineAudioBatchBusy) return;
  if (lineAudioAnomalyCount > 0) return;
  const items = Array.from(lineAudioSilenceMarks.entries())
    .map(([lineIndex, mark]) => ({ lineIndex: Number(lineIndex), mark }))
    .filter(({ mark }) => mark?.taskId && Array.isArray(mark.segments) && mark.segments.length);
  if (!items.length) {
    toast("当前没有已标记的音频片段");
    return;
  }
  const groupedItems = Array.from(items.reduce((map, item) => {
    const taskId = Number(item.mark.taskId || 0);
    if (!taskId) return map;
    const group = map.get(taskId) || { taskId, segments: [], lineIndexes: [] };
    group.segments.push(...item.mark.segments);
    group.lineIndexes.push(item.lineIndex);
    map.set(taskId, group);
    return map;
  }, new Map()).values());
  if (!window.confirm(`确定删除 ${groupedItems.length} 个台词音频任务中的已标记片段吗？`)) return;
  setLineAudioBatchButtonsDisabled(true);
  let editedCount = 0;
  try {
    for (const item of groupedItems) {
      const primaryLineIndex = item.lineIndexes[0];
      setLineAudioBatchProcessingLine(primaryLineIndex, { scroll: true });
      try {
        await editLineAudioTaskAudio(item.taskId, {
          mode: "remove",
          segments: item.segments,
        });
        item.lineIndexes.forEach((lineIndex) => lineAudioSilenceMarks.delete(lineIndex));
        item.lineIndexes.forEach((lineIndex) => clearLineAudioNoiseMark(lineIndex));
        updateLineAudioMarkedSegmentsBadge();
        editedCount += 1;
        await loadLineAudios({ silent: true, partialLineIndex: primaryLineIndex });
        setLineAudioBatchProcessingLine(primaryLineIndex, { scroll: false });
      } catch (err) {
        toast(`第 ${primaryLineIndex + 1} 行处理失败: ${err.message || "未知错误"}`);
      }
      item.lineIndexes.forEach((lineIndex) => updateLineAudioRow(lineIndex));
    }
    await updateChapterActionWarnings();
    toast(`已删除 ${editedCount} 个台词音频任务的标记片段`);
  } finally {
    clearLineAudioBatchProcessingLine();
    setLineAudioBatchButtonsDisabled(false);
  }
}

async function loadWaveSurferModules() {
  if (!waveSurferModulesPromise) {
    waveSurferModulesPromise = Promise.all([
      import("https://unpkg.com/wavesurfer.js@7/dist/wavesurfer.esm.js"),
      import("https://unpkg.com/wavesurfer.js@7/dist/plugins/regions.esm.js"),
      import("https://unpkg.com/wavesurfer.js@7/dist/plugins/timeline.esm.js"),
      import("https://unpkg.com/wavesurfer.js@7/dist/plugins/hover.esm.js"),
    ]).then(([waveSurferModule, regionsModule, timelineModule, hoverModule]) => ({
      WaveSurfer: waveSurferModule.default,
      RegionsPlugin: regionsModule.default,
      TimelinePlugin: timelineModule.default,
      HoverPlugin: hoverModule.default,
    }));
  }
  return waveSurferModulesPromise;
}

function destroyLineAudioEditor() {
  unbindLineAudioEditorSpaceKey();
  const waveEl = document.getElementById("lineAudioWaveform");
  if (lineAudioEditorWaveSurfer) {
    lineAudioEditorWaveSurfer.destroy();
  }
  lineAudioEditorWaveSurfer = null;
  lineAudioEditorRegions = null;
  lineAudioEditorRegion = null;
  lineAudioEditorDeleteRegions = [];
  lineAudioEditorNoiseSegments = [];
  lineAudioEditorSelectionPlaying = false;
  lineAudioEditorTaskId = 0;
  lineAudioEditorLineIndex = -1;
  lineAudioEditorReady = false;
  lineAudioEditorSuggestedGainDb = null;
  setLineAudioEditorVolumeDb(0);
  setLineAudioEditorSpeed(1);
  lineAudioEditorDetectToken += 1;
  lineAudioEditorLoudnessToken += 1;
  setLineAudioEditorZoom(0);
  const playBtn = document.getElementById("lineAudioEditorPlayBtn");
  if (playBtn) playBtn.textContent = "播放选中";
  updateLineAudioEditorLoudnessStats(null);
  updateLineAudioEditorWarning(null);
  updateLineAudioEditorFalsePositiveButton();
}

function shouldIgnoreLineAudioEditorShortcut(event) {
  const tagName = String(event.target?.tagName || "").toLowerCase();
  return ["input", "textarea", "select", "button"].includes(tagName) || Boolean(event.target?.isContentEditable);
}

function bindLineAudioEditorSpaceKey() {
  unbindLineAudioEditorSpaceKey();
  lineAudioEditorSpaceKeyHandler = (event) => {
    const dialog = document.getElementById("lineAudioEditorDialog");
    if (!dialog?.open || event.code !== "Space" || shouldIgnoreLineAudioEditorShortcut(event)) return;
    event.preventDefault();
    playLineAudioEditorSelection();
  };
  document.addEventListener("keydown", lineAudioEditorSpaceKeyHandler);
}

function unbindLineAudioEditorSpaceKey() {
  if (!lineAudioEditorSpaceKeyHandler) return;
  document.removeEventListener("keydown", lineAudioEditorSpaceKeyHandler);
  lineAudioEditorSpaceKeyHandler = null;
}

function getLineAudioEditorSelection() {
  const start = Number(document.getElementById("lineAudioEditorStart")?.value || 0);
  const end = Number(document.getElementById("lineAudioEditorEnd")?.value || 0);
  return { start, end };
}

function syncLineAudioEditorInputs(start, end) {
  const startInput = document.getElementById("lineAudioEditorStart");
  const endInput = document.getElementById("lineAudioEditorEnd");
  if (startInput) startInput.value = Number(start || 0).toFixed(1);
  if (endInput) endInput.value = Number(end || 0).toFixed(1);
}

function setLineAudioEditorZoom(value) {
  const zoom = Math.max(0, Math.min(LINE_AUDIO_EDITOR_MAX_ZOOM, Math.round(Number(value || 0))));
  lineAudioEditorZoom = zoom;
  const range = document.getElementById("lineAudioEditorZoomRange");
  if (range) range.value = String(zoom);
  if (lineAudioEditorWaveSurfer?.zoom) {
    lineAudioEditorWaveSurfer.zoom(zoom);
  }
}

function setLineAudioEditorVolumeDb(value) {
  const rawDb = Number(value || 0);
  const db = Math.max(-20, Math.min(12, Number.isFinite(rawDb) ? rawDb : 0));
  lineAudioEditorVolumeDb = db;
  const range = document.getElementById("lineAudioEditorDbRange");
  const input = document.getElementById("lineAudioEditorDbInput");
  const label = document.getElementById("lineAudioEditorDbLabel");
  const text = `${db >= 0 ? "+" : ""}${db.toFixed(1)}dB`;
  if (range) range.value = db.toFixed(1);
  if (input) input.value = db.toFixed(1);
  if (label) label.textContent = text;
}

function getLineAudioEditorVolumeFactor() {
  return Math.pow(10, lineAudioEditorVolumeDb / 20);
}

function setLineAudioEditorSpeed(value) {
  const rawSpeed = Number(value || 1);
  const speed = Math.max(0.8, Math.min(1.2, Number.isFinite(rawSpeed) ? rawSpeed : 1));
  lineAudioEditorSpeedFactor = speed;
  const range = document.getElementById("lineAudioEditorSpeedRange");
  const input = document.getElementById("lineAudioEditorSpeedInput");
  const label = document.getElementById("lineAudioEditorSpeedLabel");
  if (range) range.value = speed.toFixed(2);
  if (input) input.value = speed.toFixed(2);
  if (label) label.textContent = `${speed.toFixed(2)}x`;
}

function formatLineAudioDb(value, suffix = "dB") {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  return `${num >= 0 ? "+" : ""}${num.toFixed(1)}${suffix}`;
}

function updateLineAudioEditorPlayheadLabel(seconds = null) {
  const waveEl = document.getElementById("lineAudioWaveform");
  const label = document.getElementById("lineAudioEditorPlayheadTime");
  if (!waveEl || !label || !lineAudioEditorWaveSurfer || !lineAudioEditorReady) return;
  const duration = Number(lineAudioEditorWaveSurfer.getDuration?.() || 0);
  if (!Number.isFinite(duration) || duration <= 0) {
    label.classList.add("hidden");
    return;
  }
  const currentTime = Math.max(0, Math.min(Number(seconds ?? lineAudioEditorWaveSurfer.getCurrentTime?.() ?? 0), duration));
  const scrollWidth = Math.max(waveEl.scrollWidth || 0, waveEl.clientWidth || 0, 1);
  const x = (currentTime / duration) * scrollWidth - (waveEl.scrollLeft || 0);
  if (x < 0 || x > waveEl.clientWidth) {
    label.classList.add("hidden");
    return;
  }
  label.textContent = `${currentTime.toFixed(1)}秒`;
  label.style.left = `${Math.max(18, Math.min(waveEl.clientWidth - 18, x))}px`;
  label.classList.remove("hidden");
}

function attachLineAudioEditorPlayheadLabel() {
  const waveEl = document.getElementById("lineAudioWaveform");
  if (!waveEl) return;
  document.getElementById("lineAudioEditorPlayheadTime")?.remove();
  const label = document.createElement("div");
  label.id = "lineAudioEditorPlayheadTime";
  label.className = "line-audio-playhead-time hidden";
  waveEl.appendChild(label);
  waveEl.addEventListener("scroll", () => updateLineAudioEditorPlayheadLabel(), { passive: true });
}

function updateLineAudioEditorLoudnessStats(data) {
  const statsEl = document.getElementById("lineAudioEditorLoudnessStats");
  const matchBtn = document.getElementById("lineAudioEditorMatchLoudnessBtn");
  if (!data) {
    lineAudioEditorSuggestedGainDb = null;
    if (statsEl) statsEl.innerHTML = "Peak: -<br />LUFS: -";
    if (matchBtn) {
      matchBtn.disabled = true;
      matchBtn.textContent = "匹配 -20 LUFS";
    }
    return;
  }
  lineAudioEditorSuggestedGainDb = Number(data.suggestedGainDb);
  if (statsEl) {
    const inputLufs = Number(data.inputLufs);
    statsEl.innerHTML = `Peak: ${formatLineAudioDb(data.peakDbfs, "dBFS")}<br />LUFS: ${Number.isFinite(inputLufs) ? inputLufs.toFixed(1) : "-"}`;
  }
  if (matchBtn) {
    matchBtn.disabled = !Number.isFinite(lineAudioEditorSuggestedGainDb);
    const targetLufs = Number(data.targetLufs);
    matchBtn.textContent = `匹配 ${Number.isFinite(targetLufs) ? targetLufs.toFixed(0) : "-20"} LUFS`;
    matchBtn.title = Number.isFinite(lineAudioEditorSuggestedGainDb)
      ? `建议增益 ${formatLineAudioDb(lineAudioEditorSuggestedGainDb)}`
      : "无法计算建议增益";
  }
}

async function analyzeLineAudioEditorLoudness(taskId, token) {
  updateLineAudioEditorLoudnessStats(null);
  const statsEl = document.getElementById("lineAudioEditorLoudnessStats");
  if (statsEl) statsEl.innerHTML = "Peak: 分析中<br />LUFS: 分析中";
  try {
    const data = await analyzeLineAudioTaskLoudness(taskId, { targetLufs: -20 });
    if (token !== lineAudioEditorLoudnessToken || taskId !== lineAudioEditorTaskId) return;
    updateLineAudioEditorLoudnessStats(data);
  } catch (err) {
    if (token !== lineAudioEditorLoudnessToken || taskId !== lineAudioEditorTaskId) return;
    updateLineAudioEditorLoudnessStats(null);
    if (statsEl) statsEl.innerHTML = "Peak: 失败<br />LUFS: 失败";
    console.warn("analyze line audio loudness failed", err);
  }
}

function matchLineAudioEditorLoudness() {
  if (!Number.isFinite(lineAudioEditorSuggestedGainDb)) {
    toast("暂无可用的响度建议");
    return;
  }
  setLineAudioEditorVolumeDb(lineAudioEditorSuggestedGainDb);
  toast(`已设置建议增益 ${formatLineAudioDb(lineAudioEditorSuggestedGainDb)}，点击“调整音量并保存”生效`);
}

function getLineAudioEditorPlaybackRange() {
  const duration = lineAudioEditorWaveSurfer?.getDuration?.() || 0;
  const selection = getLineAudioEditorSelection();
  const end = Math.max(0, Math.min(Number(selection.end || 0), duration));
  const start = Math.max(0, Math.min(Number(selection.start || 0), Math.max(0, end - 0.05)));
  return { start, end };
}

function playLineAudioEditorSelectionFromCurrentHover() {
  if (!lineAudioEditorWaveSurfer || !lineAudioEditorReady) return;
  const { start, end } = getLineAudioEditorPlaybackRange();
  if (end <= start || end - start < 0.05) return;
  lineAudioEditorWaveSurfer.play(start, end);
}

function updateLineAudioEditorRegionFromInputs() {
  if (!lineAudioEditorReady || !lineAudioEditorRegions || !lineAudioEditorWaveSurfer) return;
  const duration = lineAudioEditorWaveSurfer.getDuration() || 0;
  let { start, end } = getLineAudioEditorSelection();
  start = Math.max(0, Math.min(start, Math.max(0, duration - 0.05)));
  end = Math.max(start + 0.05, Math.min(end, duration));
  syncLineAudioEditorInputs(start, end);
  if (lineAudioEditorRegion?.setOptions) {
    lineAudioEditorRegion.setOptions({ start, end });
    return;
  }
  lineAudioEditorRegions.clearRegions?.();
  lineAudioEditorRegion = lineAudioEditorRegions.addRegion({
    start,
    end,
    color: "rgba(168, 82, 36, 0.24)",
    drag: true,
    resize: true,
  });
}

function normalizeLineAudioEditorSegments(segments, duration = 0) {
  const maxDuration = Math.max(0, Number(duration || 0));
  return segments
    .map((segment) => ({
      start: Math.max(0, Math.min(Number(segment.start || 0), maxDuration)),
      end: Math.max(0, Math.min(Number(segment.end || 0), maxDuration)),
      type: segment.type ? String(segment.type) : "",
      reason: segment.reason ? String(segment.reason) : "",
    }))
    .filter((segment) => segment.end - segment.start >= 0.05)
    .sort((a, b) => a.start - b.start)
    .reduce((items, segment) => {
      const last = items[items.length - 1];
      if (last && segment.start <= last.end + 0.02) {
        last.end = Math.max(last.end, segment.end);
      } else {
        items.push({ ...segment });
      }
      return items;
    }, []);
}

function reserveLineAudioEditorMiddleSilence(segments, duration = 0) {
  const maxDuration = Math.max(0, Number(duration || 0));
  const reserveSeconds = 0.4;
  return segments.map((segment) => {
    const start = Number(segment.start || 0);
    const end = Number(segment.end || 0);
    if (segment.type === "repeated_short_line_audio") {
      return segment;
    }
    if (start <= 0.05 || end >= maxDuration - 0.05 || end - start <= reserveSeconds + 0.05) {
      return segment;
    }
    return { ...segment, end: end - reserveSeconds };
  });
}

function getLineAudioEditorDeleteSegments() {
  const duration = lineAudioEditorWaveSurfer?.getDuration?.() || 0;
  return normalizeLineAudioEditorSegments(
    lineAudioEditorDeleteRegions.map((region) => ({
      start: region.start,
      end: region.end,
      type: region.lineAudioSegmentType || "",
      reason: region.lineAudioSegmentReason || "",
    })),
    duration,
  );
}

function updateLineAudioEditorDeleteSummary() {
  const summary = document.getElementById("lineAudioEditorDeleteSummary");
  if (!summary) return;
  const segments = getLineAudioEditorDeleteSegments();
  if (!segments.length) {
    summary.textContent = "拖动波形区域选择片段。可保留选中，也可标记多个删除片段后统一保存。";
    return;
  }
  const text = segments
    .map((segment, index) => `${index + 1}. ${segment.start.toFixed(1)}-${segment.end.toFixed(1)}秒`)
    .join("；");
  summary.textContent = `已标记删除 ${segments.length} 段：${text}`;
}

function addLineAudioEditorDeleteRegion(start, end, segment = {}) {
  if (!lineAudioEditorReady || !lineAudioEditorRegions) return false;
  if (end <= start || end - start < 0.05) {
    return false;
  }
  const region = lineAudioEditorRegions.addRegion({
    start,
    end,
    color: "rgba(168, 54, 47, 0.28)",
    drag: true,
    resize: true,
  });
  region.lineAudioSegmentType = segment?.type ? String(segment.type) : "";
  region.lineAudioSegmentReason = segment?.reason ? String(segment.reason) : "";
  lineAudioEditorDeleteRegions.push(region);
  region.on?.("remove", () => {
    lineAudioEditorDeleteRegions = lineAudioEditorDeleteRegions.filter((item) => item !== region);
    updateLineAudioEditorDeleteSummary();
  });
  updateLineAudioEditorDeleteSummary();
  return true;
}

function markLineAudioEditorDeleteRegion() {
  if (!lineAudioEditorReady || !lineAudioEditorRegion) return;
  const { start, end } = getLineAudioEditorSelection();
  if (!addLineAudioEditorDeleteRegion(start, end)) {
    toast("请选择有效的音频片段");
  }
}

function clearLineAudioEditorDeleteRegions() {
  for (const region of lineAudioEditorDeleteRegions) {
    region.remove?.();
  }
  lineAudioEditorDeleteRegions = [];
  updateLineAudioEditorDeleteSummary();
}

function getNavigableLineAudioIndexes() {
  return getFilteredLinePreviewRows()
    .filter((row) => {
      const entry = getLineAudioEntry(row.index);
      return Boolean(entry?.hasAudio && entry?.streamUrl && entry?.task?.id);
    })
    .map((row) => Number(row.index));
}

async function navigateLineAudioEditor(direction) {
  if (!lineAudioEditorTaskId || lineAudioEditorLineIndex < 0) return;
  const indexes = getNavigableLineAudioIndexes();
  if (!indexes.length) return;
  const current = Number(lineAudioEditorLineIndex);
  const currentPosition = indexes.indexOf(current);
  let targetIndex = null;
  if (direction < 0) {
    targetIndex = currentPosition >= 0 ? indexes[currentPosition - 1] : indexes.filter((index) => index < current).pop();
  } else {
    targetIndex = currentPosition >= 0 ? indexes[currentPosition + 1] : indexes.find((index) => index > current);
  }
  if (targetIndex == null) {
    toast(direction < 0 ? "已经是上一条台词音频" : "已经是下一条台词音频");
    return;
  }
  await openLineAudioEditor(targetIndex);
}

async function handleLineAudioEditorKeydown(event) {
  if (!event || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  if (shouldIgnoreLineAudioEditorShortcut(event)) return;
  event.preventDefault();
  await navigateLineAudioEditor(event.key === "ArrowLeft" ? -1 : 1);
}

async function autoMarkLineAudioEditorSilences(taskId, token) {
  const summary = document.getElementById("lineAudioEditorDeleteSummary");
  if (summary) summary.textContent = "正在用 ffmpeg 检测空白音频...";
  try {
    const data = await detectLineAudioTaskSilences(taskId, { noiseDb: "-45dB", minDuration: 1.2 });
    if (token !== lineAudioEditorDetectToken || taskId !== lineAudioEditorTaskId) return;
    const duration = lineAudioEditorWaveSurfer?.getDuration?.() || Number(data.durationSeconds || 0);
    const segments = normalizeLineAudioEditorSegments(
      reserveLineAudioEditorMiddleSilence(data.segments || [], duration),
      duration,
    );
    if (!segments.length) {
      updateLineAudioEditorDeleteSummary();
      return;
    }
    clearLineAudioEditorDeleteRegions();
    for (const segment of segments) {
      addLineAudioEditorDeleteRegion(segment.start, segment.end, segment);
    }
    updateLineAudioEditorDeleteSummary();
    toast(`已自动标记 ${segments.length} 段空白音频`);
  } catch (err) {
    if (token !== lineAudioEditorDetectToken || taskId !== lineAudioEditorTaskId) return;
    updateLineAudioEditorDeleteSummary();
    toast(err.message || "自动检测空白音频失败");
  }
}

async function detectLineAudioEditorNoise() {
  if (!lineAudioEditorTaskId) return;
  const btn = document.getElementById("lineAudioEditorNoiseDetectBtn");
  const summary = document.getElementById("lineAudioEditorDeleteSummary");
  if (btn) btn.disabled = true;
  if (summary) summary.textContent = "正在检测疑似噪音...";
  try {
    const data = await detectLineAudioTaskNoise(lineAudioEditorTaskId, { sensitivity: "strict" });
    const segments = Array.isArray(data.segments) ? data.segments : [];
    lineAudioEditorNoiseSegments = segments;
    updateLineAudioEditorFalsePositiveButton();
    if (!segments.length) {
      lineAudioEditorNoiseSegments = [];
      updateLineAudioEditorFalsePositiveButton();
      updateLineAudioEditorWarning(null);
      updateLineAudioEditorDeleteSummary();
      toast("未检测到疑似噪音");
      return;
    }
    clearLineAudioEditorDeleteRegions();
    for (const segment of segments) {
      if (isLineAudioNonDeleteSegment(segment)) continue;
      addLineAudioEditorDeleteRegion(Number(segment.start || 0), Number(segment.end || 0), segment);
    }
    const reasons = segments.flatMap((segment) => Array.isArray(segment.reasons) ? segment.reasons : []);
    updateLineAudioEditorWarning({ segments, reasons, score: Number(data.score || 0), status: String(data.status || "abnormal") });
    if (summary) {
      summary.textContent = `检测到疑似噪音，评分 ${Number(data.score || segments[0]?.score || 0)}。${reasons.join("；") || "已标记疑似噪音片段。"}`;
    }
    toast(`已标记 ${segments.length} 段疑似噪音`);
  } catch (err) {
    lineAudioEditorNoiseSegments = [];
    updateLineAudioEditorFalsePositiveButton();
    updateLineAudioEditorDeleteSummary();
    toast(err.message || "检测噪音失败");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function recordLineAudioEditorNoiseFalsePositive() {
  if (!lineAudioEditorTaskId || !lineAudioEditorNoiseSegments.length) {
    toast("当前没有可记录的误报片段");
    return;
  }
  const btn = document.getElementById("lineAudioEditorNoiseFalsePositiveBtn");
  if (btn) btn.disabled = true;
  try {
    const result = await recordLineAudioNoiseFalsePositive(lineAudioEditorTaskId, lineAudioEditorNoiseSegments);
    clearLineAudioNoiseMark(lineAudioEditorLineIndex);
    clearLineAudioEditorDeleteRegions();
    lineAudioEditorNoiseSegments = [];
    updateLineAudioEditorFalsePositiveButton();
    updateLineAudioEditorWarning(null);
    updateLineAudioEditorDeleteSummary();
    toast(`已保存 ${Number(result.count || 0)} 段误报正常样本`);
  } catch (err) {
    toast(err.message || "保存误报样本失败");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function openLineAudioEditor(lineIndex) {
  const entry = getLineAudioEntry(lineIndex);
  if (!entry?.hasAudio || !entry?.streamUrl || !entry?.task?.id) {
    toast("当前行没有可编辑的音频");
    return;
  }
  const dialog = document.getElementById("lineAudioEditorDialog");
  const textEl = document.getElementById("lineAudioEditorTextContent");
  const charCountEl = document.getElementById("lineAudioEditorCharCount");
  const durationEl = document.getElementById("lineAudioEditorDuration");
  const waveEl = document.getElementById("lineAudioWaveform");
  const currentNoiseMark = getLineAudioNoiseMark(lineIndex);
  if (!dialog || !waveEl) return;

  destroyLineAudioEditor();
  lineAudioEditorTaskId = Number(entry.task.id);
  lineAudioEditorLineIndex = Number(lineIndex);
  lineAudioEditorNoiseSegments = Array.isArray(currentNoiseMark?.segments) ? currentNoiseMark.segments : [];
  updateLineAudioEditorFalsePositiveButton();
  setLineAudioEditorZoom(0);
  setLineAudioEditorVolumeDb(0);
  setLineAudioEditorSpeed(1);
  const lineText = entry.rawLine || entry.lineText || "";
  const charCount = getLineCharCount(lineText);
  if (textEl) textEl.textContent = `${String(lineIndex + 1).padStart(3, "0")} ${lineText}`;
  if (charCountEl) {
    charCountEl.className = `juben-line-count line-audio-editor-char-count ${getLineCharCountClass(charCount)}`;
    charCountEl.textContent = `${charCount}字`;
    charCountEl.title = `台词字数：${charCount}`;
  }
  updateLineAudioEditorWarning(currentNoiseMark);
  if (durationEl) durationEl.textContent = `时长：${formatLineAudioSeconds(entry.durationSeconds)}`;
  syncLineAudioEditorInputs(0, Number(entry.durationSeconds || 0));
  waveEl.textContent = "正在加载波形...";
  if (!dialog.open) dialog.showModal();
  bindLineAudioEditorSpaceKey();

  try {
    const { WaveSurfer, RegionsPlugin, TimelinePlugin, HoverPlugin } = await loadWaveSurferModules();
    waveEl.textContent = "";
    lineAudioEditorRegions = RegionsPlugin.create();
    lineAudioEditorWaveSurfer = WaveSurfer.create({
      container: waveEl,
      waveColor: "#d8b995",
      progressColor: "#a85224",
      cursorColor: "#2b2118",
      height: 140,
      normalize: true,
      plugins: [
        TimelinePlugin.create({
          height: 22,
          insertPosition: "afterend",
          style: {
            fontSize: "11px",
            color: "#7d6752",
          },
        }),
        HoverPlugin.create({
          lineColor: "#a85224",
          lineWidth: 2,
          labelBackground: "#2b2118",
          labelColor: "#fffaf2",
          labelSize: 13,
          formatTimeCallback: (seconds) => formatLineAudioSeconds(seconds),
        }),
        lineAudioEditorRegions,
      ],
    });
    attachLineAudioEditorPlayheadLabel();
    lineAudioEditorRegions.on("region-updated", (region) => {
      if (region === lineAudioEditorRegion) {
        syncLineAudioEditorInputs(region.start, region.end);
      } else if (lineAudioEditorDeleteRegions.includes(region)) {
        updateLineAudioEditorDeleteSummary();
      }
    });
    lineAudioEditorWaveSurfer.on("pause", () => {
      lineAudioEditorSelectionPlaying = false;
      const playBtn = document.getElementById("lineAudioEditorPlayBtn");
      if (playBtn) playBtn.textContent = "播放选中";
      updateLineAudioEditorPlayheadLabel();
    });
    lineAudioEditorWaveSurfer.on("finish", () => {
      lineAudioEditorSelectionPlaying = false;
      const playBtn = document.getElementById("lineAudioEditorPlayBtn");
      if (playBtn) playBtn.textContent = "播放选中";
      updateLineAudioEditorPlayheadLabel();
    });
    lineAudioEditorWaveSurfer.on("timeupdate", (seconds) => {
      updateLineAudioEditorPlayheadLabel(seconds);
    });
    lineAudioEditorWaveSurfer.on("audioprocess", (seconds) => {
      updateLineAudioEditorPlayheadLabel(seconds);
    });
    lineAudioEditorWaveSurfer.on("seeking", (seconds) => {
      updateLineAudioEditorPlayheadLabel(seconds);
    });
    lineAudioEditorWaveSurfer.on("ready", () => {
      const duration = lineAudioEditorWaveSurfer.getDuration() || Number(entry.durationSeconds || 0);
      const detectToken = lineAudioEditorDetectToken;
      const loudnessToken = lineAudioEditorLoudnessToken;
      lineAudioEditorReady = true;
      if (durationEl) durationEl.textContent = `时长：${formatLineAudioSeconds(duration)}`;
      syncLineAudioEditorInputs(0, duration);
      lineAudioEditorRegion = lineAudioEditorRegions.addRegion({
        start: 0,
        end: duration,
        color: "rgba(168, 82, 36, 0.24)",
        drag: true,
        resize: true,
      });
      updateLineAudioEditorDeleteSummary();
      updateLineAudioEditorPlayheadLabel(0);
      autoMarkLineAudioEditorSilences(lineAudioEditorTaskId, detectToken);
      analyzeLineAudioEditorLoudness(lineAudioEditorTaskId, loudnessToken);
    });
    lineAudioEditorWaveSurfer.on("error", (error) => {
      toast(error?.message || "音频波形加载失败");
    });
    const version = String(entry.task?.updatedAt || entry.lineHash || Date.now());
    lineAudioEditorWaveSurfer.load(`${entry.streamUrl}?v=${encodeURIComponent(version)}`);
  } catch (err) {
    toast(err.message || "音频编辑器加载失败");
  }
}

function playLineAudioEditorSelection() {
  if (!lineAudioEditorWaveSurfer || !lineAudioEditorReady) return;
  const playBtn = document.getElementById("lineAudioEditorPlayBtn");
  if (lineAudioEditorSelectionPlaying) {
    lineAudioEditorWaveSurfer.pause();
    lineAudioEditorSelectionPlaying = false;
    if (playBtn) playBtn.textContent = "播放选中";
    return;
  }
  lineAudioEditorSelectionPlaying = true;
  if (playBtn) playBtn.textContent = "暂停播放";
  playLineAudioEditorSelectionFromCurrentHover();
}

async function saveLineAudioEditorSelection() {
  if (!lineAudioEditorTaskId) return;
  const saveBtn = document.getElementById("lineAudioEditorSaveBtn");
  const lineIndex = lineAudioEditorLineIndex;
  const { start, end } = getLineAudioEditorSelection();
  if (end <= start || end - start < 0.05) {
    toast("请选择有效的音频片段");
    return;
  }
  if (!window.confirm(`确定只保留 ${start.toFixed(1)} 秒到 ${end.toFixed(1)} 秒的音频片段并替换原音频吗？`)) {
    return;
  }
  if (saveBtn) saveBtn.disabled = true;
  try {
    const result = await editLineAudioTaskAudio(lineAudioEditorTaskId, {
      mode: "keep",
      startSeconds: start,
      endSeconds: end,
    });
    clearLineAudioNoiseMark(lineIndex);
    await loadLineAudios({ preserveEditing: true });
    await updateChapterActionWarnings();
    await openLineAudioEditor(lineIndex);
    toast(`音频已保存，时长 ${formatLineAudioSeconds(result.durationSeconds)}`);
  } catch (err) {
    toast(err.message || "保存音频失败");
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function saveLineAudioEditorVolume() {
  if (!lineAudioEditorTaskId) return;
  const saveBtn = document.getElementById("lineAudioEditorVolumeSaveBtn");
  const lineIndex = lineAudioEditorLineIndex;
  const db = Math.max(-20, Math.min(12, Number(lineAudioEditorVolumeDb || 0)));
  const dbText = `${db >= 0 ? "+" : ""}${db.toFixed(1)}dB`;
  if (Math.abs(db) < 0.001) {
    toast("音量增益为 0.0dB，无需调整");
    return;
  }
  if (!window.confirm(`确定将音频音量调整 ${dbText} 并替换原音频吗？`)) {
    return;
  }
  if (saveBtn) saveBtn.disabled = true;
  try {
    const result = await editLineAudioTaskAudio(lineAudioEditorTaskId, {
      mode: "volume",
      volumeFactor: getLineAudioEditorVolumeFactor(),
    });
    clearLineAudioNoiseMark(lineIndex);
    await loadLineAudios({ preserveEditing: true });
    await updateChapterActionWarnings();
    await openLineAudioEditor(lineIndex);
    toast(`音量已调整 ${dbText}，时长 ${formatLineAudioSeconds(result.durationSeconds)}`);
  } catch (err) {
    toast(err.message || "调整音量失败");
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function saveLineAudioEditorSilenceSelection() {
  if (!lineAudioEditorTaskId) return;
  const saveBtn = document.getElementById("lineAudioEditorSilenceSaveBtn");
  const lineIndex = lineAudioEditorLineIndex;
  const { start, end } = getLineAudioEditorSelection();
  if (end <= start || end - start < 0.05) {
    toast("请选择有效的音频片段");
    return;
  }
  if (!window.confirm(`确定将 ${start.toFixed(1)} 秒到 ${end.toFixed(1)} 秒替换为空音频并保留原时长吗？`)) {
    return;
  }
  if (saveBtn) saveBtn.disabled = true;
  try {
    const result = await editLineAudioTaskAudio(lineAudioEditorTaskId, {
      mode: "silence",
      startSeconds: start,
      endSeconds: end,
    });
    clearLineAudioNoiseMark(lineIndex);
    await loadLineAudios({ preserveEditing: true });
    await updateChapterActionWarnings();
    await openLineAudioEditor(lineIndex);
    toast(`已静音选中片段，时长保持 ${formatLineAudioSeconds(result.durationSeconds)}`);
  } catch (err) {
    toast(err.message || "静音选中片段失败");
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function saveLineAudioEditorSpeed() {
  if (!lineAudioEditorTaskId) return;
  const saveBtn = document.getElementById("lineAudioEditorSpeedSaveBtn");
  const lineIndex = lineAudioEditorLineIndex;
  const speed = Math.max(0.8, Math.min(1.2, Number(lineAudioEditorSpeedFactor || 1)));
  if (Math.abs(speed - 1) < 0.001) {
    toast("语速倍率为 1.00x，无需调整");
    return;
  }
  if (!window.confirm(`确定将音频语速调整为 ${speed.toFixed(2)}x 并替换原音频吗？`)) {
    return;
  }
  if (saveBtn) saveBtn.disabled = true;
  try {
    const result = await editLineAudioTaskAudio(lineAudioEditorTaskId, {
      mode: "speed",
      speedFactor: speed,
    });
    clearLineAudioNoiseMark(lineIndex);
    await loadLineAudios({ preserveEditing: true });
    await updateChapterActionWarnings();
    await openLineAudioEditor(lineIndex);
    toast(`语速已调整为 ${speed.toFixed(2)}x，时长 ${formatLineAudioSeconds(result.durationSeconds)}`);
  } catch (err) {
    toast(err.message || "调整语速失败");
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function saveLineAudioEditorDeleteRegions(options = {}) {
  if (!lineAudioEditorTaskId || !lineAudioEditorWaveSurfer) return;
  const collectTrainingSamples = options.collectTrainingSamples !== false;
  const saveBtn = document.getElementById(
    collectTrainingSamples ? "lineAudioEditorRemoveSaveBtn" : "lineAudioEditorRemoveNoTrainSaveBtn",
  );
  const lineIndex = lineAudioEditorLineIndex;
  const duration = lineAudioEditorWaveSurfer.getDuration() || 0;
  const segments = getLineAudioEditorDeleteSegments();
  if (!segments.length) {
    toast("请先标记要删除的音频片段");
    return;
  }
  const totalDelete = segments.reduce((sum, segment) => sum + (segment.end - segment.start), 0);
  if (duration > 0 && duration - totalDelete < 0.05) {
    toast("不能删除整段音频");
    return;
  }
  if (saveBtn) saveBtn.disabled = true;
  try {
    const result = await editLineAudioTaskAudio(lineAudioEditorTaskId, {
      mode: "remove",
      segments,
      collectTrainingSamples,
    });
    clearLineAudioNoiseMark(lineIndex);
    await loadLineAudios({ preserveEditing: true });
    await updateChapterActionWarnings();
    await openLineAudioEditor(lineIndex);
    toast(`音频已保存${collectTrainingSamples ? "" : "（未加入训练样本）"}，时长 ${formatLineAudioSeconds(result.durationSeconds)}`);
  } catch (err) {
    toast(err.message || "保存音频失败");
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function replaceMatchingLineAudioFromEditor() {
  if (!lineAudioEditorTaskId) return;
  const btn = document.getElementById("lineAudioEditorReplaceMatchingBtn");
  if (btn) btn.disabled = true;
  try {
    const preview = await previewLineAudioReplacementTargets(lineAudioEditorTaskId);
    const totalCount = Number(preview.totalCount || 0);
    if (!totalCount) {
      toast("没有找到其他相同角色+台词");
      return;
    }
    const confirmed = await confirmLineAudioReplacement(preview);
    if (!confirmed) return;
    const result = await replaceMatchingLineAudios(lineAudioEditorTaskId);
    await loadLineAudios({ preserveEditing: true });
    await updateChapterActionWarnings();
    toast(`已替换 ${Number(result.replacedCount || 0)} 个台词音频任务，共匹配 ${Number(result.totalCount || 0)} 处`);
  } catch (err) {
    toast(err.message || "替换相同台词音频失败");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function confirmLineAudioReplacement(preview) {
  const dialog = document.getElementById("lineAudioReplaceConfirmDialog");
  if (!dialog) return Promise.resolve(false);
  const source = preview?.source || {};
  const chapters = Array.isArray(preview?.chapters) ? preview.chapters : [];
  const totalCount = Number(preview?.totalCount || 0);
  const summary = document.getElementById("lineAudioReplaceConfirmSummary");
  const roleEl = document.getElementById("lineAudioReplaceConfirmRole");
  const textEl = document.getElementById("lineAudioReplaceConfirmText");
  const chaptersEl = document.getElementById("lineAudioReplaceConfirmChapters");
  if (summary) summary.textContent = `找到 ${totalCount} 处其他相同角色+台词。确定用当前音频替换以下所有台词音频吗？`;
  if (roleEl) roleEl.textContent = source.roleName || "-";
  if (textEl) textEl.textContent = source.lineText || "-";
  if (chaptersEl) {
    chaptersEl.innerHTML = chapters
      .map((item) => `<div class="line-audio-replace-confirm-item"><strong>第 ${String(item.chapterNum).padStart(3, "0")} 回</strong><span>${escapeHtml(item.chapterTitle || "未命名")}</span><span class="line-audio-replace-confirm-count">${Number(item.count || 0)} 处</span></div>`)
      .join("");
  }
  return new Promise((resolve) => {
    const onClose = () => {
      dialog.removeEventListener("close", onClose);
      resolve(dialog.returnValue === "confirm");
    };
    dialog.addEventListener("close", onClose);
    dialog.returnValue = "cancel";
    dialog.showModal();
  });
}

function bindLineAudioButtons(root) {
  root.querySelectorAll(".edit-line-audio-btn").forEach((btn) => {
    if (btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async (event) => {
      const lineIndex = Number(event.currentTarget.dataset.lineIndex || -1);
      setActiveLineAudioRow(lineIndex);
      await openLineAudioEditor(lineIndex);
    });
  });

  root.querySelectorAll(".enqueue-line-btn").forEach((btn) => {
    if (btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async (event) => {
      const lineIndex = Number(event.currentTarget.dataset.lineIndex || -1);
      if (!activeNovel || !activeChapterNum || lineIndex < 0) return;
      try {
        const schedule = getLineAudioQueueSchedule();
        await enqueueLineAudio(activeNovel.id, activeChapterNum, lineIndex, {
          scheduledAt: schedule.scheduledAt,
        });
        incrementNavBadge("lineAudio", 1);
        renderNav();
        clearLineAudioNoiseMark(lineIndex);
        setStatus(schedule.label);
        toast(schedule.label);
        await loadLineAudios({ partialLineIndex: lineIndex });
        startLineAudioRefreshLoop();
      } catch (err) {
        toast(err.message);
      }
    });
  });
}

function bindLineEditingEvents(root) {
  root.querySelectorAll(".juben-line-edit-icon").forEach((icon) => {
    icon.addEventListener("click", (event) => {
      event.stopPropagation();
      editingLineIndex = Number(icon.dataset.lineIndex || -1);
      editingLineOriginalText = getLinePreviewRow(editingLineIndex)?.line || "";
      renderLineAudioTable();
      const input = document.querySelector(`.juben-line-single-input[data-line-index="${editingLineIndex}"]`);
      input?.focus();
    });
  });

  root.querySelectorAll(".juben-line-text-cell").forEach((cell) => {
    cell.addEventListener("click", (event) => {
      if (!lineEditEnabled || event.target.closest(".juben-line-save-btn")) return;
      const row = cell.closest(".juben-line[data-line-index]");
      const lineIndex = Number(row?.dataset.lineIndex || -1);
      if (lineIndex < 0 || editingLineIndex === lineIndex) return;
      if (editingLineIndex >= 0) {
        saveLineText(editingLineIndex);
        return;
      }
      editingLineIndex = lineIndex;
      editingLineOriginalText = getLinePreviewRow(lineIndex)?.line || "";
      renderLineAudioTable();
      const input = document.querySelector(`.juben-line-single-input[data-line-index="${lineIndex}"]`);
      input?.focus();
    });
  });

  root.querySelectorAll(".juben-line-save-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const lineIndex = Number(btn.dataset.lineIndex || -1);
      await saveLineText(lineIndex);
    });
  });

  root.querySelectorAll(".juben-line-single-input").forEach((input) => {
    input.addEventListener("keydown", async (event) => {
      const lineIndex = Number(input.dataset.lineIndex || -1);
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        await saveLineText(lineIndex);
      }
      if (event.key === "Escape") {
        event.preventDefault();
        editingLineIndex = -1;
        editingLineOriginalText = "";
        renderLineAudioTable();
      }
    });
  });
}

function focusNextLineSearchMatch() {
  const rows = getFilteredLinePreviewRows();
  const matches = getLineSearchMatches(rows);
  if (!matches.length) {
    toast("未找到匹配台词");
    return;
  }
  lineSearchIndex = (lineSearchIndex + 1) % matches.length;
  const match = matches[lineSearchIndex];
  const target = document.querySelector(`.juben-line[data-line-index="${match.index}"]`);
  target?.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function saveLineText(lineIndex) {
  if (!activeNovel || !activeChapterNum || lineIndex < 0) return;
  const input = document.querySelector(`.juben-line-single-input[data-line-index="${lineIndex}"]`);
  if (!input) return;

  const newText = String(input.value || "").trim();
  if (!newText) {
    toast("台词内容不能为空");
    return;
  }
  const parsed = parseChapterJson();
  if (!parsed) {
    toast("JSON解析失败，无法保存台词");
    return;
  }

  const roleList = Array.isArray(parsed.role_list) ? parsed.role_list : [];
  const allowedRoles = new Set(roleList.map((item) => String(item?.name || "").trim()).filter(Boolean));
  const nextRole = extractRoleName(newText);
  if (nextRole && !allowedRoles.has(nextRole)) {
    toast(`台词中的角色不存在于角色列表: ${nextRole}`);
    return;
  }

  const nextRows = String(parsed?.juben || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  if (lineIndex >= nextRows.length) {
    toast("行号超出范围");
    return;
  }
  nextRows[lineIndex] = newText;
  parsed.juben = nextRows.join("\n");

  try {
    const nextJsonText = JSON.stringify(parsed, null, 2);
    await saveChapterJsonOutput(activeNovel.id, activeChapterNum, nextJsonText);
    jsonViewRawText = nextJsonText;
    jsonViewParsed = parsed;
    linePreviewRows = getJubenLinesFromParsed(parsed);
    editingLineIndex = -1;
    editingLineOriginalText = "";
    await loadLineAudios();
    await updateChapterActionWarnings();
    toast(`第 ${lineIndex + 1} 行台词已保存`);
    setStatus(`第 ${lineIndex + 1} 行台词已保存`);
  } catch (err) {
    toast(err.message || "保存台词失败");
  }
}

async function mergeAdjacentSameRoleLines() {
  if (!activeNovel || !activeChapterNum) return;
  if (editingLineIndex >= 0 && !window.confirm("当前有正在编辑的台词，继续合并会放弃未保存编辑。确定继续吗？")) {
    return;
  }
  const parsed = parseChapterJson();
  if (!parsed) {
    toast("JSON解析失败，无法合并台词");
    return;
  }
  const rows = String(parsed?.juben || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  if (!rows.some((line) => String(line || "").trim())) {
    toast("当前章节没有可合并的台词");
    return;
  }

  const result = mergeAdjacentSameRoleJubenRows(rows, MERGE_ADJACENT_LINE_MAX_CHARS);
  if (result.mergedCount <= 0) {
    const suffix = result.overlongCount > 0 ? `，${result.overlongCount} 条单行已超过 ${MERGE_ADJACENT_LINE_MAX_CHARS} 字` : "";
    toast(`没有可合并的相邻同角色台词${suffix}`);
    return;
  }

  if (!window.confirm(`确定合并同角色相邻台词吗？\n台词 ${rows.length} 条 -> ${result.rows.length} 条，合并 ${result.mergedCount} 条。`)) {
    return;
  }

  parsed.juben = result.rows.join("\n");
  try {
    const nextJsonText = JSON.stringify(parsed, null, 2);
    await saveChapterJsonOutput(activeNovel.id, activeChapterNum, nextJsonText);
    jsonViewRawText = nextJsonText;
    jsonViewParsed = parsed;
    linePreviewRows = getJubenLinesFromParsed(parsed);
    editingLineIndex = -1;
    editingLineOriginalText = "";
    lineSearchIndex = -1;
    await loadLineAudios();
    await updateChapterActionWarnings();
    const suffix = result.overlongCount > 0 ? `，${result.overlongCount} 条单行超过 ${MERGE_ADJACENT_LINE_MAX_CHARS} 字未合并` : "";
    toast(`已合并 ${result.mergedGroups} 组，台词 ${rows.length} 条 -> ${result.rows.length} 条${suffix}`);
    setStatus(`已合并同角色相邻台词：${rows.length} -> ${result.rows.length}`);
  } catch (err) {
    toast(err.message || "合并台词失败");
  }
}

// ============ 角色列表功能 ============

function parseChapterJson() {
  const jsonText = jsonViewRawText;
  if (!jsonText) return null;
  try {
    return JSON.parse(jsonText);
  } catch {
    return null;
  }
}

function getRoleListFromJson(parsed) {
  if (!parsed) return [];
  const roleList = parsed.role_list || [];
  return roleList.map((role) => ({
    name: String(role.name || "").trim(),
    instruct: String(role.instruct || "").trim(),
    text: String(role.text || "").trim(),
  }));
}

async function loadGlobalRoleDefaults() {
  if (!activeNovel) return;
  try {
    const res = await fetch(`/api/novels/${activeNovel.id}/roles`);
    const data = await res.json();
    globalRoleDefaults = data.roles || [];
  } catch {
    globalRoleDefaults = [];
  }
}

function setRolesModalError(msg) {
  const el = document.getElementById("rolesModalError");
  if (el) {
    el.textContent = msg || "";
    el.classList.toggle("hidden", !msg);
  }
}

function updateRolesToolbarState() {
  document.getElementById("editRolesBtn").classList.toggle("hidden", isRolesEditing);
  document.getElementById("saveRolesBtn").classList.toggle("hidden", !isRolesEditing);
  document.getElementById("cancelRolesEditBtn").classList.toggle("hidden", !isRolesEditing);
  document.getElementById("addRemainingRolesBtn")?.classList.toggle("hidden", isRolesEditing);
}

function isRoleInLibrary(role) {
  const roleName = String(role?.name || "").trim();
  if (!roleName) return true;
  return globalRoleDefaults.some((item) => String(item.name || "").trim() === roleName);
}

function getMissingChapterRoles() {
  return chapterRoles.filter((role) => String(role?.name || "").trim() && !isRoleInLibrary(role));
}

async function saveRoleToLibrary(role) {
  const res = await fetch(`/api/novels/${activeNovel.id}/roles`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: role.name,
      instruct: role.instruct,
      sampleText: role.text,
    }),
  });
  if (!res.ok) {
    throw new Error(translateText("保存失败"));
  }
}

function updateAddRemainingRolesButton() {
  const btn = document.getElementById("addRemainingRolesBtn");
  if (!btn) return;
  const missingCount = getMissingChapterRoles().length;
  btn.disabled = isRolesEditing || missingCount <= 0;
  btn.textContent = missingCount > 0
    ? `${translateText("将剩余加入角色")} (${missingCount})`
    : translateText("已全部加入角色库");
}

function renderRolesTable() {
  const tbody = document.getElementById("rolesTableBody");
  if (!tbody) return;

  const visibleRoles = chapterRoles
    .map((role, index) => ({ role, index }))
    .filter(({ role }) => {
      if (!filterRolesMissingOnly || isRolesEditing) return true;
      const roleName = String(role.name || "").trim();
      return !globalRoleDefaults.some((item) => String(item.name || "").trim() === roleName);
    });

  tbody.innerHTML = visibleRoles.map(({ role, index }) => {
    if (isRolesEditing) {
      return `
        <tr data-role-index="${index}">
          <td>${index + 1}</td>
          <td><input class="role-input" data-field="name" value="${escapeHtml(role.name)}" /></td>
          <td><textarea class="role-textarea" data-field="instruct" rows="2">${escapeHtml(role.instruct)}</textarea></td>
          <td><textarea class="role-textarea" data-field="text" rows="2">${escapeHtml(role.text)}</textarea></td>
          <td><button class="ghost-btn btn-sm delete-role-btn" type="button">${translateText("删除")}</button></td>
        </tr>
      `;
    } else {
      const roleName = String(role.name || "").trim();
      const roleInstruct = String(role.instruct || "").trim();
      const defaultRole = globalRoleDefaults.find(
        (item) => String(item.name || "").trim() === roleName
      );
      
      let actionHtml = '<span class="text-muted">-</span>';
      if (!defaultRole) {
        actionHtml = `<button class="ghost-btn btn-sm add-to-library-btn role-library-action role-library-action-add" type="button">${translateText("加入角色库")}</button>`;
      } else if (String(defaultRole.instruct || "").trim() !== roleInstruct) {
        actionHtml = `<button class="ghost-btn btn-sm replace-library-btn role-library-action role-library-action-replace" type="button">${translateText("替换角色库")}</button>`;
      } else {
        actionHtml = `<span class="success-text">${translateText("已设为默认")}</span>`;
      }
      
      return `
        <tr data-role-index="${index}">
          <td>${index + 1}</td>
          <td>${escapeHtml(role.name || "-")}</td>
          <td>${escapeHtml(role.instruct || "-")}</td>
          <td>${escapeHtml(role.text || "-")}</td>
          <td>${actionHtml}</td>
        </tr>
      `;
    }
  }).join("");

  // 绑定编辑模式事件
  if (isRolesEditing) {
    tbody.querySelectorAll(".delete-role-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const tr = e.target.closest("tr");
        const idx = Number(tr?.dataset.roleIndex ?? -1);
        if (idx >= 0) {
          chapterRoles.splice(idx, 1);
          renderRolesTable();
        }
      });
    });
  } else {
    // 绑定加入角色库/替换角色库事件
    tbody.querySelectorAll(".add-to-library-btn, .replace-library-btn").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        const tr = e.target.closest("tr");
        const idx = Number(tr?.dataset.roleIndex ?? -1);
        const role = idx >= 0 ? chapterRoles[idx] : null;
        if (!role || !role.name) return;

        btn.disabled = true;
        try {
          await saveRoleToLibrary(role);
          toast(translateText("已保存到角色库"));
          await loadGlobalRoleDefaults();
          renderRolesTable();
          await updateChapterActionWarnings();
        } catch {
          toast(translateText("保存失败"));
        } finally {
          btn.disabled = false;
        }
      });
    });
  }
  updateAddRemainingRolesButton();
  localizeDocumentText(document);
}

async function addRemainingRolesToLibrary() {
  if (!activeNovel || isRolesEditing) return;
  const btn = document.getElementById("addRemainingRolesBtn");
  const missingRoles = getMissingChapterRoles();
  if (!missingRoles.length) {
    toast(translateText("已全部加入角色库"));
    return;
  }
  if (!window.confirm(`确认将剩余 ${missingRoles.length} 个角色加入角色库吗？`)) return;
  const previousText = btn?.textContent || "";
  if (btn) {
    btn.disabled = true;
    btn.textContent = translateText("保存中...");
  }
  let saved = 0;
  let failed = 0;
  try {
    for (const role of missingRoles) {
      try {
        await saveRoleToLibrary(role);
        saved += 1;
      } catch {
        failed += 1;
      }
    }
    await loadGlobalRoleDefaults();
    renderRolesTable();
    await updateChapterActionWarnings();
    toast(failed ? `已加入 ${saved} 个，失败 ${failed} 个` : `已加入 ${saved} 个角色`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = previousText;
    }
    updateAddRemainingRolesButton();
  }
}

async function openRolesDialog() {
  if (!activeNovel || !activeChapterNum) {
    toast("请先选择章节");
    return;
  }

  const parsed = await loadChapterJsonCache();
  if (!jsonViewRawText) {
    toast("请先完成JSON转换");
    return;
  }
  if (!parsed) {
    toast("JSON解析失败，无法查看角色");
    return;
  }

  isRolesEditing = false;
  filterRolesMissingOnly = false;
  const missingOnlyFilter = document.getElementById("rolesMissingOnlyFilter");
  if (missingOnlyFilter) missingOnlyFilter.checked = false;
  
  // Load roles from JSON's role_list (like old jpm project)
  const list = Array.isArray(parsed?.role_list) ? parsed.role_list : [];
  chapterRoles = [];
  for (const item of list) {
    const name = String((item && item.name) || "").trim();
    const instruct = String((item && item.instruct) || "").trim();
    const text = String((item && item.text) || "").trim();
    if (name) {
      chapterRoles.push({ name, instruct, text });
    }
  }
  
  // Load global role defaults (role library) for comparison
  await loadGlobalRoleDefaults();
  
  setRolesModalError("");
  updateRolesToolbarState();
  renderRolesTable();
  
  const dialog = document.getElementById("rolesDialog");
  dialog.showModal();
}

async function saveRolesEdit() {
  if (!activeNovel || !activeChapterNum) return;

  const rows = [];
  const names = new Set();
  const trs = Array.from(document.querySelectorAll("#rolesTableBody tr[data-role-index]"));
  
  for (const tr of trs) {
    const name = tr.querySelector('[data-field="name"]')?.value?.trim() || "";
    const instruct = tr.querySelector('[data-field="instruct"]')?.value?.trim() || "";
    const text = tr.querySelector('[data-field="text"]')?.value?.trim() || "";

    if (!name && !instruct && !text) continue;
    if (!name) {
      setRolesModalError("角色名不能为空");
      return;
    }
    if (names.has(name)) {
      setRolesModalError(`角色名重复: ${name}`);
      return;
    }
    names.add(name);
    rows.push({ name, instruct, text });
  }

  const saveBtn = document.getElementById("saveRolesBtn");
  saveBtn.disabled = true;
  try {
    // Update JSON with new role_list
    const parsed = parseChapterJson();
    if (parsed) {
      parsed.role_list = rows;
      const jsonText = JSON.stringify(parsed, null, 2);
      
      // Save to server
      await saveChapterJsonOutput(activeNovel.id, activeChapterNum, jsonText);
      
      chapterRoles = rows;
      isRolesEditing = false;
      setRolesModalError("");
      updateRolesToolbarState();
      renderRolesTable();
      toast("角色已保存");
      await updateChapterActionWarnings();
    }
  } catch (err) {
    setRolesModalError("保存失败: " + err.message);
  } finally {
    saveBtn.disabled = false;
  }
}

// 检查角色库状态并更新警告图标
async function updateRolesWarningBadge() {
  const warningBtn = document.getElementById("viewRolesBtn");
  if (!warningBtn) return;

  const parsed = parseChapterJson();
  if (!parsed || !activeNovel) {
    warningBtn.classList.remove("has-role-library-warning");
    warningBtn.title = "";
    return;
  }

  const chapterRoleNames = (parsed.role_list || []).map(r => String(r.name || "").trim()).filter(Boolean);
  if (chapterRoleNames.length === 0) {
    warningBtn.classList.remove("has-role-library-warning");
    warningBtn.title = "";
    return;
  }

  // 获取角色库中的角色
  try {
    const res = await fetch(`/api/novels/${activeNovel.id}/roles`);
    const data = await res.json();
    const libraryRoleNames = new Set((data.roles || []).map(r => String(r.name || "").trim()));

    // 检查是否有角色不在角色库中
    const missingNames = Array.from(new Set(chapterRoleNames.filter(name => !libraryRoleNames.has(name))));
    const hasMissingRoles = missingNames.length > 0;
    warningBtn.classList.toggle("has-role-library-warning", hasMissingRoles);
    warningBtn.title = hasMissingRoles ? `有角色未加入角色库：${missingNames.join("、")}` : "";
  } catch {
    warningBtn.classList.remove("has-role-library-warning");
    warningBtn.title = "";
  }
}

async function updateLineAudioWarningBadge() {
  const warningBtn = document.getElementById("viewLineAudioBtn");
  if (!warningBtn) return;

  const parsed = parseChapterJson();
  if (!parsed || !activeNovel || !activeChapterNum) {
    warningBtn.classList.remove("has-line-audio-warning");
    warningBtn.title = "";
    return;
  }

  const rows = getJubenLinesFromParsed(parsed);
  if (!rows.length) {
    warningBtn.classList.remove("has-line-audio-warning");
    warningBtn.title = "";
    return;
  }

  try {
    const entries = await fetchChapterLineAudios(activeNovel.id, activeChapterNum);
    const incomplete = rows.filter((row) => {
      const entry = entries.find((item) => Number(item.lineIndex) === Number(row.index));
      return !(entry && entry.hasAudio && entry.streamUrl);
    });
    const abnormal = rows.filter((row) => {
      const entry = entries.find((item) => Number(item.lineIndex) === Number(row.index));
      return getLineAudioAnomaly(row, entry).abnormal;
    });
    const hasWarning = incomplete.length > 0 || abnormal.length > 0;
    warningBtn.classList.toggle("has-line-audio-warning", hasWarning);
    const titleParts = [];
    if (incomplete.length > 0) titleParts.push(`还有 ${incomplete.length} 条台词未生成音频`);
    if (abnormal.length > 0) titleParts.push(`有 ${abnormal.length} 条异常台词音频`);
    warningBtn.title = titleParts.join("；");
  } catch {
    warningBtn.classList.remove("has-line-audio-warning");
    warningBtn.title = "";
  }
}

async function updateMergedAudioWarningBadge() {
  const warningBtn = document.getElementById("mergeLineAudioBtn");
  if (!warningBtn || !activeNovel || !activeChapterNum) return;
  try {
    const overview = await fetchChapterLineAudioOverview(activeNovel.id, activeChapterNum);
    const hasWarning = Boolean(overview.mergedAudioOutdated);
    warningBtn.classList.toggle("has-line-audio-warning", hasWarning);
    warningBtn.title = hasWarning ? "存在更新的台词音频，建议重新合并" : "";
  } catch {
    warningBtn.classList.remove("has-line-audio-warning");
    warningBtn.title = "";
  }
}

async function updateChapterActionWarnings() {
  await updateRolesWarningBadge();
  await updateLineAudioWarningBadge();
  await updateMergedAudioWarningBadge();
}

function bindRolesEvents() {
  document.getElementById("viewRolesBtn")?.addEventListener("click", openRolesDialog);

  document.getElementById("rolesMissingOnlyFilter")?.addEventListener("change", (event) => {
    filterRolesMissingOnly = Boolean(event.target.checked);
    renderRolesTable();
  });

  document.getElementById("addRemainingRolesBtn")?.addEventListener("click", addRemainingRolesToLibrary);

  document.getElementById("editRolesBtn")?.addEventListener("click", () => {
    isRolesEditing = true;
    updateRolesToolbarState();
    renderRolesTable();
  });
  
  document.getElementById("cancelRolesEditBtn")?.addEventListener("click", () => {
    isRolesEditing = false;
    chapterRoles = getRoleListFromJson(parseChapterJson());
    setRolesModalError("");
    updateRolesToolbarState();
    renderRolesTable();
  });
  
  document.getElementById("saveRolesBtn")?.addEventListener("click", saveRolesEdit);
}

function openChapterModal(mode) {
  if (!activeNovel) return;
  const form = document.getElementById("chapterForm");
  const modal = document.getElementById("chapterModal");
  chapterModalMode = mode;
  chapterEditSourceNum = null;

  if (mode === "create") {
    const maxNum = chapterState.reduce((m, c) => Math.max(m, c.chapterNum), 0);
    document.getElementById("chapterModalTitle").textContent = "创建章回";
    form.chapterNum.value = String(maxNum + 1);
    form.title.value = "";
    modalInitialWordCount = 0;
    form.content.value = "";
    syncModalWordCount(form);
    localizeDocumentText(document);
    modal.showModal();
    return;
  }

  if (!activeChapterDetail) {
    toast(t("api.chapterNotFound"));
    return;
  }
  chapterEditSourceNum = activeChapterDetail.chapterNum;
  document.getElementById("chapterModalTitle").textContent = "编辑章回";
  form.chapterNum.value = String(activeChapterDetail.chapterNum);
  form.title.value = activeChapterDetail.title || "";
  modalInitialWordCount = Number(activeChapterDetail.wordCount) || 0;
  form.content.value = activeChapterDetail.content || "";
  syncModalWordCount(form);
  localizeDocumentText(document);
  modal.showModal();
}

async function init() {
  renderNav();
  applyChapterFontSize(getSavedChapterFontSize());
  updateChapterNavButtons();
  resetChapterAudioPlayer();
  const data = await getData({ include: ["novels", "settings"] });
  allNovels = data.novels || [];
  currentSettings = data.settings || null;
  activeNovel = getNovelByQueryOrActive();
  if (!activeNovel) {
    document.getElementById("chapterPageTitle").textContent = "暂无小说可管理";
    return;
  }
  setActiveNovelId(activeNovel.id);
  setHeader(activeNovel);
  renderNovelSelect();
  bindActions();
  bindRolesEvents();
  await refreshChapters();
  localizeDocumentText(document);
}

init().catch((err) => {
  renderNav();
  showPageError(err, t("error.pageLoad"));
});
