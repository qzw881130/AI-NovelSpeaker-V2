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
let jsonAutosaveTimerId = null;
let jsonAutosaveSaving = false;

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
  wrap.innerHTML = list
    .slice(0, 200)
    .map((c) => {
      const progress = c.hasAudio ? 100 : c.hasJson ? 55 : 0;
      const activeClass = c.chapterNum === activeChapterNum ? "active" : "";
      return `<button class="quick-chip ${activeClass}" style="--progress:${progress}%" data-chapter-num="${c.chapterNum}">${String(c.chapterNum).padStart(3, "0")}</button>`;
    })
    .join("");
  wrap.querySelectorAll("[data-chapter-num]").forEach((el) => {
    el.addEventListener("click", () => loadChapter(Number(el.dataset.chapterNum)));
  });
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
    activeChapterNum = chapterState[0].chapterNum;
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
    try {
      const schedule = getLineAudioQueueSchedule();
      const result = await enqueueAllLineAudios(activeNovel.id, activeChapterNum, {
        scheduledAt: schedule.scheduledAt,
      });
      incrementNavBadge("lineAudio", Number(result.queued || 0));
      renderNav();
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
    try {
      const schedule = getLineAudioQueueSchedule();
      let queuedCount = 0;
      for (const lineIndex of remainingIndexes) {
        await enqueueLineAudio(activeNovel.id, activeChapterNum, lineIndex, {
          scheduledAt: schedule.scheduledAt,
        });
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
    try {
      const schedule = getLineAudioQueueSchedule();
      let queuedCount = 0;
      for (const lineIndex of filteredIndexes) {
        await enqueueLineAudio(activeNovel.id, activeChapterNum, lineIndex, {
          scheduledAt: schedule.scheduledAt,
        });
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

  document.getElementById("mergeLineAudioBtn")?.addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    const url = `./novel-download.html?novelId=${encodeURIComponent(activeNovel.id)}`;
    window.open(url, "_blank");
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
      if (entry.hasAudio && entry.streamUrl) return false;
      const taskStatus = String(entry.task?.status || "").trim();
      return !["pending", "processing", "running", "completed"].includes(taskStatus);
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
  if (!dialog?.open || !activeNovel || !activeChapterNum) return;
  lineAudioRefreshTimerId = window.setInterval(async () => {
    if (!dialog.open) {
      stopLineAudioRefreshLoop();
      return;
    }
    if (hasPlayingLineAudio()) {
      return;
    }
    await loadLineAudios({ silent: true, preserveEditing: true });
  }, 3000);
}

function updateLineAudioRow(lineIndex) {
  const rowEl = document.querySelector(`.juben-line[data-line-index="${lineIndex}"]`);
  if (!rowEl) return;
  const audioCell = rowEl.querySelector(".juben-line-audio");
  if (!audioCell) return;

  const entry = getLineAudioEntry(lineIndex);
  const view = getLineAudioViewState(entry);
  if (view.hasAudio) {
    audioCell.innerHTML = `
      <audio controls preload="metadata" src="${escapeHtml(view.src)}"></audio>
      <span class="${view.statusClass}">${escapeHtml(view.statusText)}</span>
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
  syncLineRoleFilterOptions();
  updateLineAudioToolbarState();
  await loadLineAudios();
  document.getElementById("lineAudioDialog")?.showModal();
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
  if (countEl) {
    countEl.textContent = `${translateText("筛选")} ${rows.length} ${translateText("条")}`;
  }
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
    item.className = `juben-line${isEditing ? " juben-line-single-editing" : ""}${isMatched ? " juben-line-search-hit" : ""}`;
    item.dataset.lineIndex = String(row.index);

    const no = document.createElement("span");
    no.className = "juben-line-no";
    no.textContent = String(row.index + 1).padStart(3, "0");
    item.appendChild(no);

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
      audioCell.innerHTML = `
        <audio controls preload="metadata" src="${escapeHtml(view.src)}"></audio>
        <span class="${view.statusClass}">${escapeHtml(view.statusText)}</span>
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
  bindLineAudioButtons(root);
  bindLineEditingEvents(root);
  localizeDocumentText(document);
}

function bindLineAudioButtons(root) {
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
    const hasWarning = incomplete.length > 0;
    warningBtn.classList.toggle("has-line-audio-warning", hasWarning);
    warningBtn.title = hasWarning
      ? `还有 ${incomplete.length} 条台词未生成音频`
      : "";
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
