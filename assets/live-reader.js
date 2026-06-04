import { fetchChapterAsrFile, fetchChapterDetail, fetchChapterIllustrationImages, fetchNovelChapters, getActiveNovelId, getData, setActiveNovelId } from "./store.js";
import { renderNav, showPageError, toast } from "./ui.js";
import { localizeDocumentText, translateText } from "./i18n.js";

const WIDTH_KEY = "ai_novel_live_reader_width";
const HEIGHT_KEY = "ai_novel_live_reader_height";
const FONT_SIZE_KEY = "ai_novel_live_reader_font_size";
const AUTO_NEXT_KEY = "ai_novel_live_reader_auto_next";
const AUTO_SCROLL_KEY = "ai_novel_live_reader_auto_scroll";
const HIGHLIGHT_KEY = "ai_novel_live_reader_highlight";
const ILLUSTRATIONS_KEY = "ai_novel_live_reader_illustrations";
const HIGHLIGHT_INTENSITY_KEY = "ai_novel_live_reader_highlight_intensity";
const FOLLOW_SENSITIVITY_KEY = "ai_novel_live_reader_follow_sensitivity";
const FOLLOW_SMOOTHNESS_KEY = "ai_novel_live_reader_follow_smoothness";
const CONTROLS_COLLAPSED_KEY = "ai_novel_live_reader_controls_collapsed";
const PLAYLIST_COLLAPSED_KEY = "ai_novel_live_reader_playlist_collapsed";
const TOP_SAFE_OFFSET_KEY = "ai_novel_live_reader_top_safe_offset";
const READER_THEME_KEY = "ai_novel_live_reader_theme";
const DEFAULT_READER_TOP_SAFE_OFFSET = 72;
const MAX_READER_TOP_SAFE_OFFSET = 360;
const DEFAULT_READER_THEME_ID = "parchment";
const READER_THEMES = [
  {
    id: "parchment",
    label: "羊皮纸暖白",
    background: "#fff7ea",
    text: "#4e3625",
    highlight: "230 192 162",
    highlightText: "#2f1f13",
    progressFill: "#bc6a34",
    progressTrack: "#eadfcd",
  },
  {
    id: "bamboo",
    label: "竹简浅青",
    background: "#eef4e6",
    text: "#324228",
    highlight: "205 223 179",
    highlightText: "#233018",
    progressFill: "#7fa04b",
    progressTrack: "#d9e6c9",
  },
  {
    id: "mist",
    label: "雾蓝静读",
    background: "#edf3f8",
    text: "#314455",
    highlight: "198 216 232",
    highlightText: "#1f3140",
    progressFill: "#5f89ac",
    progressTrack: "#d9e4ef",
  },
  {
    id: "ink",
    label: "水墨素灰",
    background: "#f3f4f6",
    text: "#22252b",
    highlight: "215 221 230",
    highlightText: "#14181e",
    progressFill: "#666f7c",
    progressTrack: "#d9dde3",
  },
  {
    id: "sepia",
    label: "古籍浅褐",
    background: "#f4eadf",
    text: "#4b3525",
    highlight: "221 193 168",
    highlightText: "#2e1f13",
    progressFill: "#a86e46",
    progressTrack: "#e4d3c0",
  },
  {
    id: "sage",
    label: "鼠尾草纸",
    background: "#f1f2e8",
    text: "#3d4534",
    highlight: "214 216 191",
    highlightText: "#262d1c",
    progressFill: "#7b8660",
    progressTrack: "#dfe1cf",
  },
  {
    id: "night",
    label: "夜读墨黑",
    background: "#1f2329",
    text: "#e9dfd3",
    highlight: "95 71 54",
    highlightText: "#fff4e6",
    progressFill: "#d6a57c",
    progressTrack: "#4a515a",
  },
  {
    id: "lotus",
    label: "荷塘月白",
    background: "#f7f4ea",
    text: "#34443f",
    highlight: "202 225 214",
    highlightText: "#20302c",
    progressFill: "#5e907d",
    progressTrack: "#d8e3db",
  },
  {
    id: "amber",
    label: "琥珀暖灯",
    background: "#fff1d8",
    text: "#5a321c",
    highlight: "244 204 136",
    highlightText: "#3b2012",
    progressFill: "#c16b2b",
    progressTrack: "#ecd2aa",
  },
  {
    id: "dawn",
    label: "晨曦桃粉",
    background: "#fff2f0",
    text: "#55313b",
    highlight: "241 199 205",
    highlightText: "#3a2028",
    progressFill: "#bd6f7b",
    progressTrack: "#ead2d6",
  },
  {
    id: "ocean",
    label: "海盐浅蓝",
    background: "#eef7f8",
    text: "#2d4650",
    highlight: "190 224 230",
    highlightText: "#1f343c",
    progressFill: "#4f9db0",
    progressTrack: "#cfe4e8",
  },
  {
    id: "plum",
    label: "梅子淡紫",
    background: "#f6f0f7",
    text: "#45324c",
    highlight: "219 199 226",
    highlightText: "#302038",
    progressFill: "#8d6aa0",
    progressTrack: "#dfd2e5",
  },
];
let deferredInstallPrompt = null;

let allNovels = [];
let activeNovel = null;
let chapterItems = [];
let audioChapterItems = [];
let activeChapterNum = null;
let activeChapterDetail = null;
let readingSegments = [];
let activeSegmentIndex = -1;
let currentAsrMode = false;
let activeParagraphElement = null;
let targetReaderScrollTop = null;
let readerScrollAnimationId = 0;
let readerScrollFallbackTimer = 0;
let segmentElementMap = new Map();
let pendingTimeUpdate = false;
let pendingTimeUpdateFallbackTimer = 0;
let liveReaderSyncTimer = 0;
let liveReaderSyncWorker = null;
let liveReaderSyncWorkerUrl = "";
let lastLiveReaderSyncAt = 0;
let lastMatchStatusText = "";
let lastTimeUpdateAt = 0;
let chapterLoadToken = 0;
let activeAudioLoadTrace = null;
let lastWarmupKey = "";
let liveIllustrationItems = [];
let activeIllustrationIndex = -1;
let pendingIllustrationIndex = -1;
let liveIllustrationLoadToken = 0;
const preloadedAudioMap = new Map();
const TIMEUPDATE_MIN_INTERVAL_MS = 120;
const LIVE_READER_SYNC_INTERVAL_MS = 500;
const CHAPTER_RENDER_CACHE_LIMIT = 8;
const chapterRenderCache = new Map();
const AUDIO_PRELOAD_CACHE_LIMIT = 6;

const AUDIO_TRACE_ENABLED = new URL(window.location.href).searchParams.has("debugAudio");
const AUDIO_LOAD_TRACE_EVENTS = ["loadstart", "loadedmetadata", "loadeddata", "canplay", "canplaythrough", "play", "playing", "waiting", "stalled", "suspend", "error"];

function nextAnimationFrame() {
  return new Promise((resolve) => window.requestAnimationFrame(() => resolve()));
}

function makeChapterCacheKey(novelId, chapterNum, content, asrText) {
  return `${Number(novelId || 0)}:${Number(chapterNum || 0)}:${String(content || "").length}:${String(asrText || "").length}`;
}

function getChapterRenderCache(key) {
  if (!chapterRenderCache.has(key)) return null;
  const value = chapterRenderCache.get(key);
  chapterRenderCache.delete(key);
  chapterRenderCache.set(key, value);
  return value;
}

function setChapterRenderCache(key, value) {
  if (chapterRenderCache.has(key)) {
    chapterRenderCache.delete(key);
  }
  chapterRenderCache.set(key, value);
  while (chapterRenderCache.size > CHAPTER_RENDER_CACHE_LIMIT) {
    const oldestKey = chapterRenderCache.keys().next().value;
    chapterRenderCache.delete(oldestKey);
  }
}

function splitParagraphs(text) {
  const content = String(text || "").replace(/\r/g, "").trim();
  if (!content) return [];
  return content
    .split(/\n+/)
    .map((item) => String(item || "").trim())
    .filter(Boolean);
}

function segmentRenderRanges(segment) {
  if (Array.isArray(segment?.ranges) && segment.ranges.length) {
    return segment.ranges;
  }
  if (
    Number.isInteger(segment?.paragraphIndex) &&
    Number.isInteger(segment?.startChar) &&
    Number.isInteger(segment?.endChar)
  ) {
    return [
      {
        paragraphIndex: Number(segment.paragraphIndex),
        startChar: Number(segment.startChar),
        endChar: Number(segment.endChar),
      },
    ];
  }
  return [];
}

function getNovelByQueryOrActive() {
  const url = new URL(window.location.href);
  const queryId = String(url.searchParams.get("novelId") || "");
  if (queryId) {
    return allNovels.find((item) => String(item.id) === queryId) || null;
  }
  const activeId = getActiveNovelId();
  if (activeId) {
    return allNovels.find((item) => String(item.id) === String(activeId)) || null;
  }
  return allNovels[0] || null;
}

function getAudioStreamUrl(chapterNum, audioVersion = "") {
  const base = `/api/novels/${Number(activeNovel?.id || 0)}/chapters/${Number(chapterNum)}/audio-stream`;
  const version = String(audioVersion || "").trim();
  return version ? `${base}?v=${encodeURIComponent(version)}` : base;
}

function getUpcomingWarmupChapters(chapterNum) {
  const index = audioChapterItems.findIndex((item) => Number(item.chapterNum) === Number(chapterNum));
  if (index < 0) return [];
  const candidates = [
    audioChapterItems[index + 1]?.chapterNum,
  ];
  return candidates
    .map((value) => Number(value || 0))
    .filter((value, idx, list) => Number.isInteger(value) && value > 0 && list.indexOf(value) === idx);
}

function scheduleUpcomingAudioWarmup(chapterNum) {
  if (!activeNovel) return;
  const chapters = getUpcomingWarmupChapters(chapterNum);
  if (!chapters.length) return;
  const warmupKey = `${Number(activeNovel.id)}:${chapters.join(",")}`;
  if (warmupKey === lastWarmupKey) return;
  lastWarmupKey = warmupKey;
  chapters.forEach((nextChapterNum) => {
    const item = audioChapterItems.find((chapter) => Number(chapter.chapterNum) === Number(nextChapterNum));
    preloadChapterAudio(nextChapterNum, item?.audioVersion || "");
  });
}

function preloadChapterAudio(chapterNum, audioVersion = "") {
  if (!activeNovel || !chapterNum) return;
  const src = getAudioStreamUrl(chapterNum, audioVersion);
  if (!src || preloadedAudioMap.has(src)) return;
  const audio = new Audio();
  audio.preload = "metadata";
  audio.src = src;
  audio.load();
  preloadedAudioMap.set(src, audio);
  while (preloadedAudioMap.size > AUDIO_PRELOAD_CACHE_LIMIT) {
    const [oldSrc, oldAudio] = preloadedAudioMap.entries().next().value || [];
    if (!oldSrc) break;
    oldAudio?.pause?.();
    oldAudio?.removeAttribute?.("src");
    oldAudio?.load?.();
    preloadedAudioMap.delete(oldSrc);
  }
}

function getSavedNumber(key, fallback, min, max) {
  const value = Number(localStorage.getItem(key) || fallback);
  if (!Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, Math.round(value)));
}

function getSavedBool(key, fallback) {
  const raw = localStorage.getItem(key);
  if (raw == null) return fallback;
  return raw === "1";
}

function saveBool(key, value) {
  localStorage.setItem(key, value ? "1" : "0");
}

function getSavedThemeId() {
  const saved = String(localStorage.getItem(READER_THEME_KEY) || "").trim();
  return READER_THEMES.some((item) => item.id === saved) ? saved : DEFAULT_READER_THEME_ID;
}

function getReaderTheme(themeId = getSavedThemeId()) {
  return READER_THEMES.find((item) => item.id === themeId) || READER_THEMES[0];
}

function renderThemeOptions() {
  const select = document.getElementById("liveReaderThemeSelect");
  if (!select || select.dataset.ready === "1") return;
  select.innerHTML = READER_THEMES.map((item) => `<option value="${item.id}">${item.label}</option>`).join("");
  select.dataset.ready = "1";
}

function isControlsCollapsed() {
  return getSavedBool(CONTROLS_COLLAPSED_KEY, false);
}

function setControlsCollapsed(collapsed) {
  saveBool(CONTROLS_COLLAPSED_KEY, collapsed);
}

function isPlaylistCollapsed() {
  return getSavedBool(PLAYLIST_COLLAPSED_KEY, false);
}

function setPlaylistCollapsed(collapsed) {
  saveBool(PLAYLIST_COLLAPSED_KEY, collapsed);
}

function setStatus(text) {
  const el = document.getElementById("liveReaderStatus");
  if (el) el.textContent = translateText(text);
}

function setMatchStatus(text) {
  const el = document.getElementById("liveReaderMatchStatus");
  const next = String(text || "");
  if (next === lastMatchStatusText) return;
  lastMatchStatusText = next;
  if (el) el.textContent = next;
}

function resetAudioLoadTrace(chapterNum, src) {
  if (!AUDIO_TRACE_ENABLED) return;
  activeAudioLoadTrace = {
    chapterNum: Number(chapterNum || 0),
    src: String(src || ""),
    startedAt: performance.now(),
    seen: new Set(),
  };
}

function logAudioLoadTrace(eventName) {
  if (!AUDIO_TRACE_ENABLED) return;
  const player = document.getElementById("liveReaderAudioPlayer");
  if (!player || !activeAudioLoadTrace) return;
  const elapsed = Math.round(performance.now() - activeAudioLoadTrace.startedAt);
  const duplicateTransient = (eventName === "waiting" || eventName === "stalled") && activeAudioLoadTrace.seen.has(eventName);
  if (duplicateTransient) return;
  activeAudioLoadTrace.seen.add(eventName);
  const payload = {
    chapterNum: activeAudioLoadTrace.chapterNum,
    event: eventName,
    elapsedMs: elapsed,
    readyState: player.readyState,
    networkState: player.networkState,
    currentTime: Number(player.currentTime || 0),
    duration: Number.isFinite(player.duration) ? Number(player.duration) : null,
  };
  console.info("[live-reader-audio]", payload);
}

function syncLiveEndingAudioState() {
  const items = Array.isArray((window.__liveReaderSettings?.liveEndingAudio || {}).items)
    ? (window.__liveReaderSettings.liveEndingAudio.items || [])
    : [];
  const select = document.getElementById("liveEndingAudioSelect");
  const btn = document.getElementById("liveEndingAudioPlayBtn");
  const refreshBtn = document.getElementById("refreshLiveEndingAudioBtn");
  const player = document.getElementById("liveEndingAudioPlayer");
  if (!btn || !player || !select || !refreshBtn) return;
  select.innerHTML = items
    .map((item, index) => `<option value="${index}">${(item.label || `结束语${index + 1}`).replaceAll('<', '&lt;')}</option>`)
    .join("");
  const hasAudio = items.length > 0;
  btn.classList.toggle("hidden", !hasAudio);
  select.classList.toggle("hidden", !hasAudio);
  refreshBtn.classList.toggle("hidden", !hasAudio);
  if (hasAudio) {
    const nextValue = items[Number(select.value)] ? String(select.value) : "0";
    applyLiveEndingAudioSelection(nextValue);
  } else {
    player.src = "";
    player.dataset.path = "";
  }
}

function applyLiveEndingAudioSelection(nextValue) {
  const items = Array.isArray((window.__liveReaderSettings?.liveEndingAudio || {}).items)
    ? (window.__liveReaderSettings.liveEndingAudio.items || [])
    : [];
  const select = document.getElementById("liveEndingAudioSelect");
  const player = document.getElementById("liveEndingAudioPlayer");
  if (!select || !player) return null;
  const index = Number(nextValue || 0);
  const item = items[index];
  if (!item) {
    select.value = "";
    player.src = "";
    player.dataset.path = "";
    return null;
  }
  const path = String(item.path || "").trim();
  select.value = String(index);
  player.dataset.path = path;
  player.src = path
    ? `/api/settings/live-ending-audio/file?path=${encodeURIComponent(path)}&v=${Date.now()}`
    : "";
  return item;
}

function applyControlsCollapsedState() {
  const collapsed = isControlsCollapsed();
  const panel = document.querySelector(".live-reader-controls-panel");
  const btn = document.getElementById("toggleLiveReaderControlsBtn");
  if (panel) {
    panel.classList.toggle("is-collapsed", collapsed);
  }
  if (btn) {
    btn.textContent = collapsed ? "展开" : "收起";
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }
}

function applyPlaylistCollapsedState() {
  const collapsed = isPlaylistCollapsed();
  const layout = document.querySelector(".live-reader-layout");
  const panel = document.querySelector(".live-reader-playlist-panel");
  const btn = document.getElementById("toggleLiveReaderPlaylistBtn");
  if (layout) {
    layout.classList.toggle("is-playlist-collapsed", collapsed);
  }
  if (panel) {
    panel.classList.toggle("is-collapsed", collapsed);
  }
  if (btn) {
    btn.textContent = collapsed ? "▶" : "◀";
    btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    btn.setAttribute("aria-label", collapsed ? "展开章回播放列表" : "收起章回播放列表");
    btn.title = collapsed ? "展开章回播放列表" : "收起章回播放列表";
  }
}

function formatMatchStrategy(strategy) {
  const value = String(strategy || "").trim();
  if (!value) return "未命中";
  if (value === "exact") return "精确匹配";
  if (value === "anchor") return "首尾锚点匹配";
  if (value === "prefix") return "前缀匹配";
  if (value === "suffix") return "后缀匹配";
  if (value === "middle") return "中段匹配";
  if (value === "fuzzy") return "模糊匹配";
  if (value.startsWith("combined:")) {
    const mode = value.slice("combined:".length);
    const mapping = {
      "current+next": "当前句+后句拼接",
      "prev+current": "前句+当前句拼接",
      "prev+current+next": "前后句拼接",
    };
    return mapping[mode] || `拼接匹配(${mode})`;
  }
  return value;
}

function getMaxReaderHeight() {
  const wrap = document.querySelector(".live-reader-reader-wrap");
  const panel = document.querySelector(".live-reader-main-panel");
  const page = document.querySelector(".live-reader-page");
  const minHeight = 320;
  const fallbackMax = 1200;
  if (!wrap) return fallbackMax;
  const rect = wrap.getBoundingClientRect();
  const panelStyle = panel ? window.getComputedStyle(panel) : null;
  const pageStyle = page ? window.getComputedStyle(page) : null;
  const panelBottomPadding = panelStyle ? parseFloat(panelStyle.paddingBottom || "0") || 0 : 0;
  const pageBottomPadding = pageStyle ? parseFloat(pageStyle.paddingBottom || "0") || 0 : 0;
  const bottomReserve = panelBottomPadding + pageBottomPadding + 8;
  return Math.max(minHeight, Math.floor(window.innerHeight - rect.top - bottomReserve));
}

function applyReaderSettings() {
  renderThemeOptions();
  const width = getSavedNumber(WIDTH_KEY, 520, 140, 900);
  const maxReaderHeight = getMaxReaderHeight();
  const height = getSavedNumber(HEIGHT_KEY, 820, 320, maxReaderHeight);
  const fontSize = getSavedNumber(FONT_SIZE_KEY, 28, 18, 42);
  const highlightIntensity = getSavedNumber(HIGHLIGHT_INTENSITY_KEY, 45, 0, 100) / 100;
  const followSensitivity = getSavedNumber(FOLLOW_SENSITIVITY_KEY, 60, 0, 240);
  const followSmoothness = getSavedNumber(FOLLOW_SMOOTHNESS_KEY, 45, 10, 100);
  const topSafeOffset = getSavedNumber(TOP_SAFE_OFFSET_KEY, DEFAULT_READER_TOP_SAFE_OFFSET, 0, MAX_READER_TOP_SAFE_OFFSET);
  const theme = getReaderTheme();
  const content = document.getElementById("liveReaderContent");
  const progressTrack = document.getElementById("liveReaderProgressTrack");
  const illustrationBox = document.getElementById("liveReaderIllustrationBox");
  const wrap = document.querySelector(".live-reader-reader-wrap");
  const themeSelect = document.getElementById("liveReaderThemeSelect");
  const themeValue = document.getElementById("liveReaderThemeValue");
  const themePreview = document.getElementById("liveReaderThemePreview");
  if (content) {
    content.style.width = `${width}px`;
    content.style.maxWidth = `${width}px`;
    content.style.fontSize = `${fontSize}px`;
  }
  if (progressTrack) {
    progressTrack.style.width = `${width}px`;
    progressTrack.style.maxWidth = `${width}px`;
  }
  if (illustrationBox) {
    illustrationBox.style.width = `${width + 36}px`;
    illustrationBox.style.maxWidth = "100%";
  }
  if (wrap) {
    wrap.style.height = `${height}px`;
    wrap.style.width = `${width + 36}px`;
    wrap.style.maxWidth = "100%";
  }
  document.getElementById("liveReaderWidthRange").value = String(width);
  document.getElementById("liveReaderWidthValue").textContent = `${width}px`;
  document.getElementById("liveReaderHeightRange").value = String(height);
  document.getElementById("liveReaderHeightRange").max = String(maxReaderHeight);
  document.getElementById("liveReaderHeightValue").textContent = `${height}px`;
  document.getElementById("liveReaderFontSizeRange").value = String(fontSize);
  document.getElementById("liveReaderFontSizeValue").textContent = `${fontSize}px`;
  document.getElementById("liveReaderHighlightIntensityRange").value = String(Math.round(highlightIntensity * 100));
  document.getElementById("liveReaderHighlightIntensityValue").textContent = `${Math.round(highlightIntensity * 100)}%`;
  document.getElementById("liveReaderFollowSensitivityRange").value = String(followSensitivity);
  document.getElementById("liveReaderFollowSensitivityValue").textContent = `${followSensitivity}px`;
  document.getElementById("liveReaderFollowSmoothnessRange").value = String(followSmoothness);
  document.getElementById("liveReaderFollowSmoothnessValue").textContent = `${followSmoothness}%`;
  document.getElementById("liveReaderTopSafeOffsetRange").value = String(topSafeOffset);
  document.getElementById("liveReaderTopSafeOffsetValue").textContent = `${topSafeOffset}px`;
  if (themeSelect) themeSelect.value = theme.id;
  if (themeValue) themeValue.textContent = theme.label;
  if (themePreview) {
    themePreview.innerHTML = `
      <span class="live-reader-theme-chip"><i style="background:${theme.background}"></i>正文背景</span>
      <span class="live-reader-theme-chip"><i style="background:rgb(${theme.highlight})"></i>高亮背景</span>
      <span class="live-reader-theme-chip"><i style="background:${theme.text}"></i>正文文字</span>
      <span class="live-reader-theme-chip"><i style="background:${theme.progressFill}"></i>进度条</span>
      <span class="live-reader-theme-chip"><i style="background:${theme.progressTrack}"></i>轨道底色</span>
    `;
  }
  document.getElementById("liveReaderAutoNext").checked = getSavedBool(AUTO_NEXT_KEY, true);
  document.getElementById("liveReaderAutoScroll").checked = getSavedBool(AUTO_SCROLL_KEY, true);
  document.getElementById("liveReaderHighlight").checked = getSavedBool(HIGHLIGHT_KEY, true);
  document.getElementById("liveReaderIllustrations").checked = getSavedBool(ILLUSTRATIONS_KEY, false);
  document.documentElement.style.setProperty("--live-highlight-alpha", String(highlightIntensity));
  document.documentElement.style.setProperty("--live-paragraph-alpha", String(Math.max(0, highlightIntensity * 0.45)));
  document.documentElement.style.setProperty("--live-reader-bg", theme.background);
  document.documentElement.style.setProperty("--live-reader-text-color", theme.text);
  document.documentElement.style.setProperty("--live-highlight-fill-rgb", theme.highlight);
  document.documentElement.style.setProperty("--live-highlight-text-color", theme.highlightText);
  document.documentElement.style.setProperty("--live-progress-fill", theme.progressFill);
  document.documentElement.style.setProperty("--live-progress-track", theme.progressTrack);
}

function isIllustrationsEnabled() {
  return Boolean(document.getElementById("liveReaderIllustrations")?.checked);
}

function formatIllustrationRemaining(item, currentTime) {
  const end = Number(item?.end);
  const duration = Number.isFinite(Number(item?.duration))
    ? Number(item.duration)
    : Number(item?.end || 0) - Number(item?.start || 0);
  const remaining = Number.isFinite(end) ? end - Number(currentTime || 0) : duration;
  return String(Math.max(0, Math.ceil(remaining)));
}

function clearLiveIllustration(message = "暂无匹配插画") {
  activeIllustrationIndex = -1;
  pendingIllustrationIndex = -1;
  liveIllustrationLoadToken += 1;
  const box = document.getElementById("liveReaderIllustrationBox");
  const meta = document.getElementById("liveReaderIllustrationMeta");
  const img = document.getElementById("liveReaderIllustrationImage");
  const timeEl = document.getElementById("liveReaderIllustrationTime");
  const titleEl = document.getElementById("liveReaderIllustrationTitle");
  if (!box || !meta || !img) return;
  box.classList.remove("is-loading", "is-switching");
  box.classList.add("is-empty");
  if (!isIllustrationsEnabled()) {
    box.classList.add("hidden");
    img.removeAttribute("src");
    if (timeEl) timeEl.textContent = "";
    if (titleEl) titleEl.textContent = "";
    meta.textContent = "插画未开启";
    return;
  }
  box.classList.remove("hidden");
  img.removeAttribute("src");
  if (timeEl) timeEl.textContent = "";
  if (titleEl) titleEl.textContent = "";
  meta.textContent = message;
}

function updateLiveIllustration(force = false) {
  if (!isIllustrationsEnabled()) {
    clearLiveIllustration();
    return;
  }
  const box = document.getElementById("liveReaderIllustrationBox");
  const meta = document.getElementById("liveReaderIllustrationMeta");
  const img = document.getElementById("liveReaderIllustrationImage");
  const timeEl = document.getElementById("liveReaderIllustrationTime");
  const titleEl = document.getElementById("liveReaderIllustrationTitle");
  const player = document.getElementById("liveReaderAudioPlayer");
  if (!box || !meta || !img || !player) return;
  box.classList.remove("hidden");
  if (!liveIllustrationItems.length) {
    clearLiveIllustration("当前章回暂无插画");
    return;
  }
  const currentTime = Number(player.currentTime || 0);
  let nextIndex = liveIllustrationItems.findIndex((item) => currentTime >= Number(item.start || 0) && currentTime <= Number(item.end || 0));
  if (nextIndex < 0) {
    nextIndex = liveIllustrationItems.findIndex((item) => Number(item.start || 0) > currentTime);
  }
  if (nextIndex < 0) {
    if (!img.getAttribute("src")) clearLiveIllustration("当前时间暂无匹配插画");
    return;
  }
  if (!force && (nextIndex === activeIllustrationIndex || nextIndex === pendingIllustrationIndex)) {
    if (timeEl) timeEl.textContent = formatIllustrationRemaining(liveIllustrationItems[nextIndex], currentTime);
    return;
  }
  pendingIllustrationIndex = nextIndex;
  const item = liveIllustrationItems[nextIndex];
  const url = `${item.imageUrl}?v=${Date.now()}`;
  const loadToken = ++liveIllustrationLoadToken;
  box.classList.add("is-loading");
  if (!img.getAttribute("src")) box.classList.add("is-empty");
  const preloader = new Image();
  preloader.onload = () => {
    if (loadToken !== liveIllustrationLoadToken) return;
    activeIllustrationIndex = nextIndex;
    pendingIllustrationIndex = -1;
    meta.textContent = "";
    if (timeEl) timeEl.textContent = formatIllustrationRemaining(item, Number(player.currentTime || currentTime));
    if (titleEl) titleEl.textContent = `${item.index}. ${item.sceneTitle || "插画"}`;
    box.classList.remove("is-empty", "is-loading", "is-switching");
    img.src = url;
    void img.offsetWidth;
    box.classList.add("is-switching");
  };
  preloader.onerror = () => {
    if (loadToken !== liveIllustrationLoadToken) return;
    pendingIllustrationIndex = -1;
    box.classList.remove("is-loading");
    if (!img.getAttribute("src")) clearLiveIllustration("插画图片加载失败");
  };
  preloader.src = url;
}

async function loadLiveIllustrations(chapterNum, loadToken) {
  liveIllustrationItems = [];
  activeIllustrationIndex = -1;
  if (!isIllustrationsEnabled() || !activeNovel || !chapterNum) {
    clearLiveIllustration();
    return;
  }
  clearLiveIllustration("插画加载中...");
  try {
    const items = await fetchChapterIllustrationImages(activeNovel.id, chapterNum);
    if (loadToken !== chapterLoadToken) return;
    liveIllustrationItems = items
      .filter((item) => item.imageUrl && Number.isFinite(Number(item.start)) && Number.isFinite(Number(item.end)))
      .sort((a, b) => Number(a.start || 0) - Number(b.start || 0));
    updateLiveIllustration(true);
  } catch (err) {
    if (loadToken !== chapterLoadToken) return;
    liveIllustrationItems = [];
    clearLiveIllustration(`插画加载失败：${err.message}`);
  }
}

function getReaderTopSafeOffset() {
  return getSavedNumber(TOP_SAFE_OFFSET_KEY, DEFAULT_READER_TOP_SAFE_OFFSET, 0, MAX_READER_TOP_SAFE_OFFSET);
}

function updateReaderProgressBar() {
  const player = document.getElementById("liveReaderAudioPlayer");
  const fill = document.getElementById("liveReaderProgressFill");
  if (!player || !fill) return;
  const duration = Number(player.duration || 0);
  const currentTime = Number(player.currentTime || 0);
  const ratio = duration > 0 ? Math.max(0, Math.min(currentTime / duration, 1)) : 0;
  fill.style.width = `${ratio * 100}%`;
}

function getFollowSensitivity() {
  return getSavedNumber(FOLLOW_SENSITIVITY_KEY, 60, 0, 240);
}

function getFollowSmoothnessFactor() {
  return getSavedNumber(FOLLOW_SMOOTHNESS_KEY, 45, 10, 100) / 100;
}

function clampNumber(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function getActiveSegmentScrollBounds(elements, fallbackEl) {
  const candidates = (Array.isArray(elements) ? elements : []).filter(Boolean);
  if (!candidates.length && fallbackEl) candidates.push(fallbackEl);
  if (!candidates.length) return null;

  let top = Number.POSITIVE_INFINITY;
  let bottom = 0;
  candidates.forEach((el) => {
    const elTop = Number(el.offsetTop || 0);
    top = Math.min(top, elTop);
    bottom = Math.max(bottom, elTop + Number(el.offsetHeight || 0));
  });
  if (!Number.isFinite(top)) return null;
  return {
    top,
    bottom,
    height: Math.max(1, bottom - top),
  };
}

function getSegmentLineScrollBounds(wrap, elements, fallbackEl, progress) {
  const candidates = (Array.isArray(elements) ? elements : []).filter(Boolean);
  if (!candidates.length && fallbackEl) candidates.push(fallbackEl);
  if (!wrap || !candidates.length) return null;

  const wrapRect = wrap.getBoundingClientRect();
  const rects = [];
  candidates.forEach((el) => {
    Array.from(el.getClientRects()).forEach((rect) => {
      if (rect.width <= 0 || rect.height <= 0) return;
      rects.push({
        top: rect.top - wrapRect.top + wrap.scrollTop,
        bottom: rect.bottom - wrapRect.top + wrap.scrollTop,
        height: rect.height,
      });
    });
  });
  if (!rects.length) return null;
  rects.sort((a, b) => a.top - b.top || a.bottom - b.bottom);
  const safeProgress = clampNumber(Number(progress || 0), 0, 1);
  return rects[Math.min(rects.length - 1, Math.floor(safeProgress * rects.length))] || null;
}

function getReaderScrollTarget(wrap, activeEls, fallbackEl, progress = 0) {
  const bounds = getActiveSegmentScrollBounds(activeEls, fallbackEl);
  if (!bounds) return null;

  const topSafeOffset = getReaderTopSafeOffset();
  const viewportHeight = Math.max(80, Number(wrap.clientHeight || 0));
  const bottomLookahead = clampNumber(Math.round(viewportHeight * 0.22), 56, 180);
  const readableHeight = Math.max(80, viewportHeight - topSafeOffset - bottomLookahead);
  const leadSpace = bounds.height >= readableHeight
    ? 0
    : clampNumber(Math.round(readableHeight * 0.18), 18, Math.max(18, readableHeight - bounds.height));
  const maxScrollTop = Math.max(0, Number(wrap.scrollHeight || 0) - viewportHeight);

  if (bounds.height > readableHeight * 0.8) {
    const lineBounds = getSegmentLineScrollBounds(wrap, activeEls, fallbackEl, progress);
    if (lineBounds) {
      const lineLeadSpace = clampNumber(Math.round(readableHeight * 0.24), 24, 140);
      return clampNumber(lineBounds.top - topSafeOffset - lineLeadSpace, 0, maxScrollTop);
    }
  }

  return clampNumber(bounds.top - topSafeOffset - leadSpace, 0, maxScrollTop);
}

function getHighlightIntensity() {
  return getSavedNumber(HIGHLIGHT_INTENSITY_KEY, 45, 0, 100) / 100;
}

function updateInstallButtonVisibility() {
  const btn = document.getElementById("liveReaderInstallBtn");
  if (!btn) return;
  const hidden = !deferredInstallPrompt && window.matchMedia("(display-mode: standalone)").matches === false;
  btn.classList.toggle("hidden", hidden);
  if (window.matchMedia("(display-mode: standalone)").matches) {
    btn.classList.add("hidden");
  }
}

async function installStandaloneApp() {
  if (!deferredInstallPrompt) {
    toast("当前环境暂不支持安装独立窗口。请使用 localhost 或 HTTPS，并在支持的浏览器中打开。", 5000);
    return;
  }
  deferredInstallPrompt.prompt();
  try {
    await deferredInstallPrompt.userChoice;
  } finally {
    deferredInstallPrompt = null;
    updateInstallButtonVisibility();
  }
}

function renderNovelSelect() {
  const select = document.getElementById("liveReaderNovelSelect");
  if (!select) return;
  select.innerHTML = allNovels.map((item) => `<option value="${item.id}">${item.name}</option>`).join("");
  if (activeNovel) select.value = String(activeNovel.id);
}

function renderPlaylist() {
  const root = document.getElementById("liveReaderPlaylist");
  const count = document.getElementById("liveReaderPlaylistCount");
  if (!root) return;
  if (count) count.textContent = `${audioChapterItems.length} 回`;
  if (!audioChapterItems.length) {
    root.innerHTML = '<p class="empty-text">暂无可播放音频章回</p>';
    return;
  }
  if (!root.dataset.boundClick) {
    root.dataset.boundClick = "1";
    root.addEventListener("click", async (event) => {
      const btn = event.target.closest("[data-chapter-num]");
      if (!btn) return;
      await loadChapter(Number(btn.dataset.chapterNum), { autoplay: false });
    });
  }
  root.innerHTML = audioChapterItems
    .map((item) => {
      const active = Number(item.chapterNum) === Number(activeChapterNum) ? " active" : "";
      return `<button class="live-reader-playlist-item${active}" data-chapter-num="${item.chapterNum}" type="button"><strong>${String(item.chapterNum).padStart(3, "0")}</strong><span>${item.title}</span></button>`;
    })
    .join("");
}

function updatePlaylistActiveState() {
  const root = document.getElementById("liveReaderPlaylist");
  if (!root) return;
  root.querySelectorAll("[data-chapter-num]").forEach((el) => {
    el.classList.toggle("active", Number(el.dataset.chapterNum || 0) === Number(activeChapterNum));
  });
}

function updateNavButtons() {
  const idx = audioChapterItems.findIndex((item) => Number(item.chapterNum) === Number(activeChapterNum));
  document.getElementById("liveReaderPrevBtn").disabled = idx <= 0;
  document.getElementById("liveReaderNextBtn").disabled = idx < 0 || idx >= audioChapterItems.length - 1;
}

function buildReadingSegments(text) {
  const content = String(text || "").replace(/\r/g, "").trim();
  const lines = splitParagraphs(content);
  const source = lines.length ? lines : (content ? [content] : []);
  const totalWeight = source.reduce((sum, item) => sum + Math.max(1, item.replace(/\s+/g, "").length), 0) || 1;
  let cumulative = 0;
  return source.map((item, index) => {
    const weight = Math.max(1, item.replace(/\s+/g, "").length);
    const startRatio = cumulative / totalWeight;
    cumulative += weight;
    const endRatio = cumulative / totalWeight;
    return {
      index,
      text: item,
      weight,
      startRatio,
      endRatio,
    };
  });
}

function normalizeSearchText(text) {
  const chars = Array.from(String(text || ""));
  const kept = [];
  const map = [];
  for (let index = 0; index < chars.length; index += 1) {
    const ch = chars[index];
    if (/^[\p{L}\p{N}]$/u.test(ch)) {
      kept.push(ch);
      map.push(index);
    }
  }
  return {
    normalized: kept.join(""),
    map,
  };
}

function collectMatchPositions(haystack, needle, fromIndex = 0) {
  const positions = [];
  if (!needle) return positions;
  let start = Math.max(0, fromIndex);
  while (start < haystack.length) {
    const found = haystack.indexOf(needle, start);
    if (found < 0) break;
    positions.push(found);
    start = found + 1;
  }
  return positions;
}

function chooseNearestForwardPosition(positions, cursor) {
  if (!positions.length) return -1;
  const forward = positions.find((pos) => pos >= cursor);
  if (forward != null) return forward;
  return positions.reduce((best, pos) => {
    if (best < 0) return pos;
    return Math.abs(pos - cursor) < Math.abs(best - cursor) ? pos : best;
  }, -1);
}

function longestCommonSubsequenceLength(a, b) {
  const aa = String(a || "");
  const bb = String(b || "");
  if (!aa || !bb) return 0;
  const dp = new Array(bb.length + 1).fill(0);
  for (let i = 1; i <= aa.length; i += 1) {
    let prev = 0;
    for (let j = 1; j <= bb.length; j += 1) {
      const temp = dp[j];
      if (aa[i - 1] === bb[j - 1]) {
        dp[j] = prev + 1;
      } else {
        dp[j] = Math.max(dp[j], dp[j - 1]);
      }
      prev = temp;
    }
  }
  return dp[bb.length];
}

function fuzzySimilarityScore(a, b) {
  const aa = String(a || "");
  const bb = String(b || "");
  if (!aa || !bb) return 0;
  const lcs = longestCommonSubsequenceLength(aa, bb);
  return (2 * lcs) / (aa.length + bb.length);
}

function findAnchorMatch(globalNormalized, normalizedSegment, cursor) {
  if (normalizedSegment.length < 8) return null;
  const anchorLength = Math.max(3, Math.min(8, Math.floor(normalizedSegment.length * 0.28)));
  const prefix = normalizedSegment.slice(0, anchorLength);
  const suffix = normalizedSegment.slice(-anchorLength);
  const prefixPositions = collectMatchPositions(globalNormalized, prefix, Math.max(0, cursor - 32));
  let best = null;
  for (const pos of prefixPositions) {
    const windowEnd = Math.min(globalNormalized.length, pos + normalizedSegment.length + 24);
    const windowText = globalNormalized.slice(pos, windowEnd);
    const suffixIndex = windowText.indexOf(suffix, Math.max(anchorLength, normalizedSegment.length - anchorLength - 12));
    if (suffixIndex < 0) continue;
    const matchedLength = suffixIndex + anchorLength;
    const candidateText = globalNormalized.slice(pos, pos + matchedLength);
    const similarity = fuzzySimilarityScore(normalizedSegment, candidateText);
    const distance = Math.abs(pos - cursor);
    const score = similarity * 100 - distance * 0.03;
    if (!best || score > best.score) {
      best = {
        start: pos,
        length: matchedLength,
        strategy: "anchor",
        score,
      };
    }
  }
  return best ? { start: best.start, length: best.length, strategy: best.strategy } : null;
}

function findFuzzyWindowMatch(globalNormalized, normalizedSegment, cursor) {
  if (normalizedSegment.length < 8) return null;
  const searchStart = Math.max(0, cursor - 36);
  const searchEnd = Math.min(globalNormalized.length, cursor + Math.max(normalizedSegment.length * 4, 180));
  const window = globalNormalized.slice(searchStart, searchEnd);
  if (!window) return null;
  const minLen = Math.max(6, Math.floor(normalizedSegment.length * 0.7));
  const maxLen = Math.min(normalizedSegment.length + 10, normalizedSegment.length * 2);
  let best = null;
  for (let start = 0; start < window.length; start += 1) {
    const globalStart = searchStart + start;
    for (let len = minLen; len <= maxLen && start + len <= window.length; len += 2) {
      const candidate = window.slice(start, start + len);
      const similarity = fuzzySimilarityScore(normalizedSegment, candidate);
      if (similarity < 0.72) continue;
      const distance = Math.abs(globalStart - cursor);
      const forwardBias = globalStart >= cursor ? 0 : 8;
      const score = similarity * 100 - distance * 0.025 - forwardBias;
      if (!best || score > best.score) {
        best = {
          start: globalStart,
          length: len,
          strategy: "fuzzy",
          score,
        };
      }
    }
  }
  return best ? { start: best.start, length: best.length, strategy: best.strategy } : null;
}

function findBestSegmentMatch(globalNormalized, normalizedSegment, cursor) {
  if (!normalizedSegment) return null;

  const directPositions = collectMatchPositions(
    globalNormalized,
    normalizedSegment,
    Math.max(0, cursor - 12)
  );
  const direct = chooseNearestForwardPosition(directPositions, cursor);
  if (direct >= 0) {
    return { start: direct, length: normalizedSegment.length, strategy: "exact" };
  }

  const anchor = findAnchorMatch(globalNormalized, normalizedSegment, cursor);
  if (anchor) {
    return anchor;
  }

  const candidateSpecs = [];
  if (normalizedSegment.length >= 10) {
    candidateSpecs.push({
      text: normalizedSegment.slice(0, Math.max(8, Math.floor(normalizedSegment.length * 0.72))),
      weight: 3,
      strategy: "prefix",
    });
    candidateSpecs.push({
      text: normalizedSegment.slice(-Math.max(8, Math.floor(normalizedSegment.length * 0.72))),
      weight: 3,
      strategy: "suffix",
    });
  }
  if (normalizedSegment.length >= 16) {
    const innerLength = Math.max(8, Math.floor(normalizedSegment.length * 0.55));
    const innerStart = Math.max(0, Math.floor((normalizedSegment.length - innerLength) / 2));
    candidateSpecs.push({
      text: normalizedSegment.slice(innerStart, innerStart + innerLength),
      weight: 2,
      strategy: "middle",
    });
  }

  let best = null;
  for (const spec of candidateSpecs) {
    if (!spec.text) continue;
    const positions = collectMatchPositions(
      globalNormalized,
      spec.text,
      Math.max(0, cursor - 24)
    );
    for (const pos of positions) {
      const distance = Math.abs(pos - cursor);
      const forwardBias = pos >= cursor ? 0 : 15;
      const score = distance + forwardBias - spec.text.length * spec.weight;
      if (!best || score < best.score) {
        best = {
          start: pos,
          length: spec.text.length,
          strategy: spec.strategy,
          score,
        };
      }
    }
  }

  if (best) {
    return { start: best.start, length: best.length, strategy: best.strategy };
  }

  const fuzzy = findFuzzyWindowMatch(globalNormalized, normalizedSegment, cursor);
  if (fuzzy) {
    return fuzzy;
  }

  return null;
}

function findCombinedSegmentMatch(globalNormalized, normalizedSegment, cursor, previousNormalized, nextNormalized) {
  const candidates = [];
  if (nextNormalized) {
    const text = `${normalizedSegment}${nextNormalized}`;
    if (text.length >= normalizedSegment.length + 4) {
      candidates.push({ mode: "current+next", text, offset: 0, length: normalizedSegment.length });
    }
  }
  if (previousNormalized) {
    const text = `${previousNormalized}${normalizedSegment}`;
    if (text.length >= normalizedSegment.length + 4) {
      candidates.push({ mode: "prev+current", text, offset: previousNormalized.length, length: normalizedSegment.length });
    }
  }
  if (previousNormalized && nextNormalized) {
    const text = `${previousNormalized}${normalizedSegment}${nextNormalized}`;
    if (text.length >= normalizedSegment.length + 8) {
      candidates.push({ mode: "prev+current+next", text, offset: previousNormalized.length, length: normalizedSegment.length });
    }
  }

  for (const candidate of candidates) {
    const found = findBestSegmentMatch(globalNormalized, candidate.text, Math.max(0, cursor - 24));
    if (!found) continue;
    return {
      start: found.start + candidate.offset,
      length: candidate.length,
      strategy: `combined:${candidate.mode}`,
    };
  }
  return null;
}

async function mapAsrSegmentsToOriginalText(originalText, asrSegments) {
  const paragraphs = splitParagraphs(originalText);
  const paragraphMeta = paragraphs.map((text, paragraphIndex) => {
    const normalized = normalizeSearchText(text);
    return {
      paragraphIndex,
      text,
      normalizedText: normalized.normalized,
      normalizedMap: normalized.map,
    };
  });
  const globalMap = [];
  let globalNormalized = "";
  for (const paragraph of paragraphMeta) {
    for (let i = 0; i < paragraph.normalizedText.length; i += 1) {
      globalNormalized += paragraph.normalizedText[i];
      globalMap.push({
        paragraphIndex: paragraph.paragraphIndex,
        charIndex: paragraph.normalizedMap[i],
      });
    }
  }
  let cursor = 0;
  for (let index = 0; index < asrSegments.length; index += 1) {
    const segment = asrSegments[index];
    const normalizedSegment = normalizeSearchText(segment.text).normalized;
    if (!normalizedSegment) continue;
    let match = findBestSegmentMatch(globalNormalized, normalizedSegment, cursor);
    if (!match) {
      const previousNormalized = index > 0 ? normalizeSearchText(asrSegments[index - 1]?.text || "").normalized : "";
      const nextNormalized = index < asrSegments.length - 1 ? normalizeSearchText(asrSegments[index + 1]?.text || "").normalized : "";
      match = findCombinedSegmentMatch(globalNormalized, normalizedSegment, cursor, previousNormalized, nextNormalized);
    }
    if (!match) continue;
    const foundAt = match.start;
    const matchedLength = match.length;
    const startMap = globalMap[foundAt];
    const endMap = globalMap[Math.min(globalMap.length - 1, foundAt + matchedLength - 1)];
    if (!startMap || !endMap) {
      cursor = foundAt + matchedLength;
      continue;
    }
    const ranges = [];
    for (let paragraphIndex = startMap.paragraphIndex; paragraphIndex <= endMap.paragraphIndex; paragraphIndex += 1) {
      const paragraph = paragraphMeta[paragraphIndex];
      if (!paragraph) continue;
      const isFirst = paragraphIndex === startMap.paragraphIndex;
      const isLast = paragraphIndex === endMap.paragraphIndex;
      const startChar = isFirst ? startMap.charIndex : 0;
      const endChar = isLast ? endMap.charIndex + 1 : paragraph.text.length;
      if (endChar <= startChar) continue;
      ranges.push({
        paragraphIndex,
        startChar,
        endChar,
      });
    }
    if (!ranges.length) {
      cursor = foundAt + matchedLength;
      continue;
    }
    segment.ranges = ranges;
    segment.paragraphIndex = ranges[0].paragraphIndex;
    segment.startChar = ranges[0].startChar;
    segment.endChar = ranges[ranges.length - 1].endChar;
    segment.matchStrategy = match.strategy;
    segment.matched = true;
    cursor = foundAt + matchedLength;
    if (index > 0 && index % 20 === 0) {
      await nextAnimationFrame();
    }
  }
  return {
    paragraphs,
  };
}

async function renderOriginalParagraphsWithHighlights(originalText, asrSegments) {
  const { paragraphs } = await mapAsrSegmentsToOriginalText(originalText, asrSegments);
  const html = paragraphs
    .map((paragraphText, paragraphIndex) => {
      const ranges = asrSegments
        .flatMap((segment) =>
          segmentRenderRanges(segment)
            .filter((range) => Number(range.paragraphIndex) === paragraphIndex)
            .map((range) => ({
              ...range,
              index: Number(segment.index || 0),
            }))
        )
        .sort((a, b) => a.startChar - b.startChar);
      if (!ranges.length) {
        return `<p class="live-reader-paragraph">${escapeHtml(paragraphText)}</p>`;
      }
      let cursor = 0;
      let html = "";
      for (const range of ranges) {
        const start = Math.max(0, Math.min(paragraphText.length, range.startChar));
        const end = Math.max(start, Math.min(paragraphText.length, range.endChar));
        if (start > cursor) {
          html += escapeHtml(paragraphText.slice(cursor, start));
        }
        html += `<span class="live-reader-segment" data-segment-index="${range.index}">${escapeHtml(paragraphText.slice(start, end))}</span>`;
        cursor = end;
      }
      if (cursor < paragraphText.length) {
        html += escapeHtml(paragraphText.slice(cursor));
      }
      return `<p class="live-reader-paragraph">${html}</p>`;
    })
    .join("");
  return html;
}

function parseAsrTimestamp(raw) {
  const value = String(raw || "").trim();
  const match = value.match(/^(\d{2}):(\d{2}):(\d{2}),(\d{3})$/);
  if (!match) return null;
  const [, hh, mm, ss, ms] = match;
  return Number(hh) * 3600 + Number(mm) * 60 + Number(ss) + Number(ms) / 1000;
}

function parseAsrContent(text) {
  const blocks = String(text || "")
    .replace(/\r/g, "")
    .split(/\n\s*\n/)
    .map((item) => item.trim())
    .filter(Boolean);
  const segments = [];
  for (const block of blocks) {
    const lines = block.split("\n").map((item) => item.trim()).filter(Boolean);
    if (lines.length < 3) continue;
    const timeLine = lines[1];
    const timeMatch = timeLine.match(/^(.*?)\s*-->\s*(.*?)$/);
    if (!timeMatch) continue;
    const start = parseAsrTimestamp(timeMatch[1]);
    const end = parseAsrTimestamp(timeMatch[2]);
    if (start == null || end == null) continue;
    const bodyText = lines.slice(2).join("\n").trim();
    if (!bodyText) continue;
    segments.push({
      index: segments.length,
      text: bodyText,
      startTime: start,
      endTime: end,
    });
  }
  return mergeAsrSegments(segments);
}

function mergeAsrSegments(segments) {
  const merged = [];
  let current = null;
  const flush = () => {
    if (!current) return;
    current.index = merged.length;
    merged.push(current);
    current = null;
  };

  for (const raw of segments) {
    const text = String(raw.text || "").trim();
    if (!text) continue;
    const start = Number(raw.startTime || 0);
    const end = Number(raw.endTime || start);
    const duration = Math.max(0, end - start);
    const gap = current ? Math.max(0, start - Number(current.endTime || start)) : 0;
    const currentLen = current ? String(current.text || "").replace(/\s+/g, "").length : 0;
    const currentDuration = current ? Math.max(0, Number(current.endTime || 0) - Number(current.startTime || 0)) : 0;
    const shouldMerge = Boolean(
      current &&
      gap <= 0.9 &&
      (duration <= 1.35 || text.length <= 8 || currentDuration <= 1.8 || currentLen <= 12)
    );

    if (!current || !shouldMerge) {
      flush();
      current = {
        index: 0,
        text,
        startTime: start,
        endTime: end,
      };
      continue;
    }

    current.text = `${String(current.text || "")}${text}`;
    current.endTime = end;
  }
  flush();
  return merged;
}

function cancelReaderScrollAnimation() {
  if (readerScrollAnimationId) {
    window.cancelAnimationFrame(readerScrollAnimationId);
    readerScrollAnimationId = 0;
  }
  if (readerScrollFallbackTimer) {
    window.clearTimeout(readerScrollFallbackTimer);
    readerScrollFallbackTimer = 0;
  }
}

function scheduleReaderScrollStep(step) {
  readerScrollAnimationId = window.requestAnimationFrame(() => {
    if (readerScrollFallbackTimer) {
      window.clearTimeout(readerScrollFallbackTimer);
      readerScrollFallbackTimer = 0;
    }
    step();
  });
  readerScrollFallbackTimer = window.setTimeout(() => {
    if (readerScrollAnimationId) {
      window.cancelAnimationFrame(readerScrollAnimationId);
      readerScrollAnimationId = 0;
    }
    readerScrollFallbackTimer = 0;
    step();
  }, 250);
}

function runReaderScrollAnimation() {
  cancelReaderScrollAnimation();
  const step = () => {
    const wrap = document.querySelector(".live-reader-reader-wrap");
    if (!wrap || targetReaderScrollTop == null) {
      readerScrollAnimationId = 0;
      return;
    }
    const distance = targetReaderScrollTop - wrap.scrollTop;
    if (Math.abs(distance) < 0.8) {
      wrap.scrollTop = targetReaderScrollTop;
      readerScrollAnimationId = 0;
      return;
    }
    const smoothness = getFollowSmoothnessFactor();
    const easing = 0.06 + smoothness * 0.18;
    wrap.scrollTop += distance * easing;
    scheduleReaderScrollStep(step);
  };
  scheduleReaderScrollStep(step);
}

function getActiveAsrSegmentIndex(currentTime) {
  if (!readingSegments.length) return -1;
  for (let index = 0; index < readingSegments.length; index += 1) {
    const segment = readingSegments[index];
    const start = Number(segment.startTime || 0);
    const nextStart = index < readingSegments.length - 1
      ? Number(readingSegments[index + 1].startTime || segment.endTime || start)
      : Number(segment.endTime || start);
    if (currentTime >= start && currentTime < Math.max(start, nextStart)) {
      return index;
    }
  }
  if (currentTime >= Number(readingSegments[readingSegments.length - 1].startTime || 0)) {
    return readingSegments.length - 1;
  }
  return -1;
}

function getSegmentPlaybackProgress(segment, currentTime, duration) {
  if (!segment) return 0;
  if (currentAsrMode) {
    const start = Number(segment.startTime || 0);
    const end = Math.max(start + 0.1, Number(segment.endTime || start));
    return clampNumber((Number(currentTime || 0) - start) / (end - start), 0, 1);
  }
  const startRatio = Number(segment.startRatio || 0);
  const endRatio = Math.max(startRatio + 0.001, Number(segment.endRatio || startRatio));
  const ratio = Number.isFinite(duration) && duration > 0 ? Number(currentTime || 0) / duration : startRatio;
  return clampNumber((ratio - startRatio) / (endRatio - startRatio), 0, 1);
}

function updateReaderAutoScroll(wrap, activeEls, activeEl, force, progress) {
  const autoScroll = document.getElementById("liveReaderAutoScroll")?.checked;
  if (!autoScroll || !wrap || !activeEl) return;
  const targetTop = getReaderScrollTarget(wrap, activeEls, activeEl, progress);
  if (targetTop == null) return;
  const sensitivity = getFollowSensitivity();
  const diff = Math.abs(wrap.scrollTop - targetTop);
  if (force || diff > sensitivity) {
    targetReaderScrollTop = targetTop;
    runReaderScrollAnimation();
  }
}

function renderReadingContentFromPayload(payload) {
  const contentEl = document.getElementById("liveReaderContent");
  if (!contentEl) return;
  currentAsrMode = Boolean(payload?.currentAsrMode);
  readingSegments = Array.isArray(payload?.readingSegments) ? payload.readingSegments : [];
  activeSegmentIndex = -1;
  segmentElementMap = new Map();
  if (!readingSegments.length) {
    contentEl.textContent = "暂无正文";
    return;
  }
  contentEl.innerHTML = String(payload?.html || "");
  contentEl.querySelectorAll("[data-segment-index]").forEach((el) => {
    const index = Number(el.dataset.segmentIndex || -1);
    const list = segmentElementMap.get(index) || [];
    list.push(el);
    segmentElementMap.set(index, list);
  });
}

function prependFrameTitleToHtml(html, title) {
  return html;
}

async function buildRenderedChapterPayload(text, asrText) {
  const rawText = String(text || "").trim();
  const rawAsrText = String(asrText || "");
  const chapterTitle = String(activeChapterDetail?.title || "").trim();
  const cacheKey = makeChapterCacheKey(activeNovel?.id, activeChapterNum, rawText, rawAsrText);
  const cached = getChapterRenderCache(cacheKey);
  if (cached) {
    return {
      currentAsrMode: cached.currentAsrMode,
      readingSegments: cached.readingSegments.map((segment) => ({ ...segment })),
      html: cached.html,
      cacheHit: true,
    };
  }

  const asrSegments = rawAsrText ? parseAsrContent(rawAsrText) : [];
  const currentAsrModeLocal = asrSegments.length > 0;
  const readingSegmentsLocal = currentAsrModeLocal ? asrSegments : buildReadingSegments(rawText);
  const html = currentAsrModeLocal
    ? prependFrameTitleToHtml(await renderOriginalParagraphsWithHighlights(rawText, asrSegments), chapterTitle)
    : readingSegmentsLocal
        .map(
          (segment) =>
            `<p class="live-reader-segment live-reader-paragraph" data-segment-index="${segment.index}">${escapeHtml(segment.text)}</p>`
        )
        .join("");

  const finalHtml = currentAsrModeLocal ? html : prependFrameTitleToHtml(html, chapterTitle);

  setChapterRenderCache(cacheKey, {
    currentAsrMode: currentAsrModeLocal,
    readingSegments: readingSegmentsLocal.map((segment) => ({ ...segment })),
    html: finalHtml,
  });

  return {
    currentAsrMode: currentAsrModeLocal,
    readingSegments: readingSegmentsLocal,
    html: finalHtml,
    cacheHit: false,
  };
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text || "");
  return div.innerHTML;
}

function updateSegmentHighlight(force = false) {
  const player = document.getElementById("liveReaderAudioPlayer");
  const wrap = document.querySelector(".live-reader-reader-wrap");
  const enableHighlight = document.getElementById("liveReaderHighlight")?.checked;
  const autoScroll = document.getElementById("liveReaderAutoScroll")?.checked;
  updateLiveIllustration(false);
  if (!player || !wrap || !readingSegments.length) return;
  if (!Number.isFinite(player.duration) || player.duration <= 0) return;
  let nextIndex = -1;
  if (currentAsrMode) {
    nextIndex = getActiveAsrSegmentIndex(player.currentTime);
  } else {
    const ratio = Math.min(1, Math.max(0, player.currentTime / player.duration));
    nextIndex = readingSegments.findIndex((segment) => ratio >= segment.startRatio && ratio < segment.endRatio);
    if (nextIndex < 0) nextIndex = readingSegments.length - 1;
  }
  if (!force && nextIndex === activeSegmentIndex) {
    const currentSegment = readingSegments[activeSegmentIndex];
    if (currentAsrMode && currentSegment) {
      setMatchStatus(currentSegment.matched ? `匹配: ${formatMatchStrategy(currentSegment.matchStrategy || "exact")}` : "匹配: 未命中，沿用上一句");
    }
    const activeEls = segmentElementMap.get(activeSegmentIndex) || Array.from(wrap.querySelectorAll(`[data-segment-index="${activeSegmentIndex}"]`));
    const activeEl = activeEls[0] || null;
    const progress = getSegmentPlaybackProgress(currentSegment, player.currentTime, player.duration);
    updateReaderAutoScroll(wrap, activeEls, activeEl, false, progress);
    return;
  }

  if (currentAsrMode && nextIndex >= 0) {
    let resolvedIndex = nextIndex;
    while (resolvedIndex >= 0 && !readingSegments[resolvedIndex]?.matched) {
      resolvedIndex -= 1;
    }
    if (resolvedIndex >= 0) {
      nextIndex = resolvedIndex;
    }
  }

  const prevEls = activeSegmentIndex >= 0
    ? (segmentElementMap.get(activeSegmentIndex) || Array.from(wrap.querySelectorAll(`[data-segment-index="${activeSegmentIndex}"]`)))
    : [];
  prevEls.forEach((el) => el.classList.remove("active"));
  if (activeParagraphElement) {
    activeParagraphElement.classList.remove("active");
  }
  activeSegmentIndex = nextIndex;
  const activeEls = segmentElementMap.get(activeSegmentIndex) || Array.from(wrap.querySelectorAll(`[data-segment-index="${activeSegmentIndex}"]`));
  const activeEl = activeEls[0] || null;
  if (!activeEl) {
    setMatchStatus(currentAsrMode ? "匹配: 未命中" : "匹配: 估算同步");
    return;
  }
  const currentSegment = readingSegments[activeSegmentIndex];
  if (currentAsrMode) {
    setMatchStatus(currentSegment?.matched ? `匹配: ${formatMatchStrategy(currentSegment.matchStrategy || "exact")}` : "匹配: 未命中，沿用上一句");
  } else {
    setMatchStatus("匹配: 估算同步");
  }
  const paragraphEl = activeEl.closest(".live-reader-paragraph");
  if (paragraphEl) {
    paragraphEl.classList.add("active");
    activeParagraphElement = paragraphEl;
  } else {
    activeParagraphElement = null;
  }
  if (enableHighlight) activeEls.forEach((el) => el.classList.add("active"));
  if (!enableHighlight) activeEls.forEach((el) => el.classList.remove("active"));
  if (autoScroll && paragraphEl) {
    const progress = getSegmentPlaybackProgress(currentSegment, player.currentTime, player.duration);
    updateReaderAutoScroll(wrap, activeEls, activeEl, force, progress);
  }
}

function clearSegmentHighlight() {
  const wrap = document.querySelector(".live-reader-reader-wrap");
  if (!wrap) return;
  wrap.querySelectorAll(".live-reader-segment.active").forEach((el) => el.classList.remove("active"));
  wrap.querySelectorAll(".live-reader-paragraph.active").forEach((el) => el.classList.remove("active"));
  activeSegmentIndex = -1;
  activeParagraphElement = null;
  lastMatchStatusText = "";
}

function flushTimeUpdate() {
  if (pendingTimeUpdateFallbackTimer) {
    window.clearTimeout(pendingTimeUpdateFallbackTimer);
    pendingTimeUpdateFallbackTimer = 0;
  }
  pendingTimeUpdate = false;
  updateSegmentHighlight(false);
}

function scheduleTimeUpdate() {
  if (pendingTimeUpdate) return;
  const now = performance.now();
  if (now - lastTimeUpdateAt < TIMEUPDATE_MIN_INTERVAL_MS) return;
  lastTimeUpdateAt = now;
  pendingTimeUpdate = true;
  window.requestAnimationFrame(flushTimeUpdate);
  pendingTimeUpdateFallbackTimer = window.setTimeout(flushTimeUpdate, 250);
}

function forceLiveReaderSync() {
  if (pendingTimeUpdateFallbackTimer) {
    window.clearTimeout(pendingTimeUpdateFallbackTimer);
    pendingTimeUpdateFallbackTimer = 0;
  }
  pendingTimeUpdate = false;
  updateSegmentHighlight(true);
  updateReaderProgressBar();
  updateLiveIllustration(true);
}

function runLiveReaderSyncTick(force = false) {
  const player = document.getElementById("liveReaderAudioPlayer");
  if (!player) return;
  if (!force && player.paused) return;
  const now = Date.now();
  if (!force && now - lastLiveReaderSyncAt < Math.max(120, LIVE_READER_SYNC_INTERVAL_MS / 2)) return;
  lastLiveReaderSyncAt = now;
  pendingTimeUpdate = false;
  updateSegmentHighlight(force);
  updateReaderProgressBar();
  updateLiveIllustration(force);
}

function stopLiveReaderSyncLoop() {
  if (liveReaderSyncTimer) {
    window.clearInterval(liveReaderSyncTimer);
    liveReaderSyncTimer = 0;
  }
  if (liveReaderSyncWorker) {
    liveReaderSyncWorker.terminate();
    liveReaderSyncWorker = null;
  }
  if (liveReaderSyncWorkerUrl) {
    URL.revokeObjectURL(liveReaderSyncWorkerUrl);
    liveReaderSyncWorkerUrl = "";
  }
}

function startLiveReaderSyncLoop() {
  stopLiveReaderSyncLoop();
  liveReaderSyncTimer = window.setInterval(() => runLiveReaderSyncTick(false), LIVE_READER_SYNC_INTERVAL_MS);
  try {
    const blob = new Blob([
      `let timer=0;self.onmessage=(event)=>{if(event.data==='start'){clearInterval(timer);timer=setInterval(()=>self.postMessage('tick'),${LIVE_READER_SYNC_INTERVAL_MS});}else if(event.data==='stop'){clearInterval(timer);timer=0;}};`,
    ], { type: "application/javascript" });
    liveReaderSyncWorkerUrl = URL.createObjectURL(blob);
    liveReaderSyncWorker = new Worker(liveReaderSyncWorkerUrl);
    liveReaderSyncWorker.onmessage = () => runLiveReaderSyncTick(false);
    liveReaderSyncWorker.postMessage("start");
  } catch {
    liveReaderSyncWorker = null;
  }
}

function resetReaderScroll() {
  const wrap = document.querySelector(".live-reader-reader-wrap");
  if (wrap) wrap.scrollTop = 0;
  targetReaderScrollTop = 0;
  cancelReaderScrollAnimation();
  clearSegmentHighlight();
}

async function loadChapter(chapterNum, options = {}) {
  if (!activeNovel) return;
  const loadToken = ++chapterLoadToken;
  activeChapterNum = chapterNum;
  const detail = await fetchChapterDetail(activeNovel.id, chapterNum);
  if (loadToken !== chapterLoadToken) return;
  activeChapterDetail = detail;
  liveIllustrationItems = [];
  activeIllustrationIndex = -1;
  clearLiveIllustration();
  document.getElementById("liveReaderChapterTitle").textContent = detail.title;
  document.getElementById("liveReaderChapterMeta").textContent = `${detail.novelName} · 章节 ${detail.chapterNum} · 字数 ${detail.wordCount || 0}`;
  document.getElementById("liveReaderMatchStatus").textContent = "匹配: 初始化中";
  const frameTitleEl = document.getElementById("liveReaderFrameTitle");
  if (frameTitleEl) frameTitleEl.textContent = detail.title;
  const player = document.getElementById("liveReaderAudioPlayer");
  if (detail.hasAudio) {
    player.pause();
    player.removeAttribute("src");
    player.load();
    const nextSrc = getAudioStreamUrl(chapterNum, detail.audioVersion);
    resetAudioLoadTrace(chapterNum, nextSrc);
    player.src = nextSrc;
    player.load();
    if (options.autoplay) {
      try {
        await player.play();
      } catch {
        toast("浏览器阻止了自动播放，请手动点击播放");
      }
    }
  } else {
    player.pause();
    player.removeAttribute("src");
    player.load();
  }
  updateReaderProgressBar();
  resetReaderScroll();
  updatePlaylistActiveState();
  updateNavButtons();
  scheduleUpcomingAudioWarmup(chapterNum);
  loadLiveIllustrations(chapterNum, loadToken);

  const basePayload = await buildRenderedChapterPayload(String(detail.content || "").trim(), "");
  if (loadToken !== chapterLoadToken) return;
  renderReadingContentFromPayload(basePayload);
  updateSegmentHighlight(true);
  setStatus("就绪");
  setMatchStatus("匹配: 估算同步");

  let asrText = "";
  try {
    asrText = await fetchChapterAsrFile(activeNovel.id, chapterNum);
  } catch {
    asrText = "";
  }
  if (loadToken !== chapterLoadToken || !asrText) return;
  const renderPayload = await buildRenderedChapterPayload(String(detail.content || "").trim(), asrText);
  if (loadToken !== chapterLoadToken) return;
  renderReadingContentFromPayload(renderPayload);
  resetReaderScroll();
  updateSegmentHighlight(true);
  setStatus(renderPayload.currentAsrMode ? "已加载精准时间轴" : "就绪");
  setMatchStatus(renderPayload.currentAsrMode ? "匹配: 初始化中" : "匹配: 估算同步");
}

async function loadNovelChapters() {
  if (!activeNovel) return;
  chapterItems = await fetchNovelChapters(activeNovel.id);
  audioChapterItems = chapterItems.filter((item) => Boolean(item.hasAudio));
  renderPlaylist();
  const target = audioChapterItems.find((item) => Number(item.chapterNum) === Number(activeChapterNum)) || audioChapterItems[0] || null;
  if (target) {
    await loadChapter(target.chapterNum, { autoplay: false });
  } else {
    document.getElementById("liveReaderChapterTitle").textContent = "暂无可播放章回";
    document.getElementById("liveReaderChapterMeta").textContent = "";
    const frameTitleEl = document.getElementById("liveReaderFrameTitle");
    if (frameTitleEl) frameTitleEl.textContent = "-";
    document.getElementById("liveReaderContent").textContent = "当前小说还没有可用音频章回。";
    readingSegments = [];
    setMatchStatus("匹配: -");
    updateNavButtons();
  }
}

async function switchNovel(novelId) {
  activeNovel = allNovels.find((item) => String(item.id) === String(novelId)) || allNovels[0] || null;
  if (!activeNovel) return;
  setActiveNovelId(activeNovel.id);
  document.getElementById("liveReaderPageTitle").textContent = `${activeNovel.name} - 直播阅读器`;
  renderNovelSelect();
  activeChapterNum = null;
  await loadNovelChapters();
}

async function playAdjacentChapter(step, options = {}) {
  const idx = audioChapterItems.findIndex((item) => Number(item.chapterNum) === Number(activeChapterNum));
  if (idx < 0) return;
  const next = audioChapterItems[idx + step];
  const shouldWrap = Boolean(options.wrap);
  const wrapped = step > 0 ? audioChapterItems[0] : audioChapterItems[audioChapterItems.length - 1];
  const target = next || (shouldWrap ? wrapped : null);
  if (!target) return;
  await loadChapter(target.chapterNum, { autoplay: true });
}

function bindEvents() {
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredInstallPrompt = event;
    updateInstallButtonVisibility();
  });
  window.matchMedia("(display-mode: standalone)").addEventListener?.("change", updateInstallButtonVisibility);
  window.addEventListener("resize", () => {
    applyReaderSettings();
    updateSegmentHighlight(true);
  });
  document.getElementById("liveReaderNovelSelect")?.addEventListener("change", async (event) => {
    await switchNovel(event.target.value);
  });
  document.getElementById("refreshLiveReaderBtn")?.addEventListener("click", async () => {
    await loadNovelChapters();
    toast("已刷新");
  });
  document.getElementById("toggleLiveReaderControlsBtn")?.addEventListener("click", () => {
    setControlsCollapsed(!isControlsCollapsed());
    applyControlsCollapsedState();
  });
  document.getElementById("toggleLiveReaderPlaylistBtn")?.addEventListener("click", () => {
    setPlaylistCollapsed(!isPlaylistCollapsed());
    applyPlaylistCollapsedState();
  });
  document.getElementById("liveEndingAudioPlayBtn")?.addEventListener("click", async () => {
    const select = document.getElementById("liveEndingAudioSelect");
    applyLiveEndingAudioSelection(select?.value || "0");
    const player = document.getElementById("liveEndingAudioPlayer");
    if (!player?.src) {
      toast("未配置直播结束语音频");
      return;
    }
    player.currentTime = 0;
    try {
      await player.play();
    } catch {
      toast("播放直播结束语失败，请重试");
    }
  });
  document.getElementById("refreshLiveEndingAudioBtn")?.addEventListener("click", async () => {
    const data = await getData({ include: ["settings"] });
    window.__liveReaderSettings = data.settings || {};
    syncLiveEndingAudioState();
    toast("直播结束语列表已刷新");
  });
  document.getElementById("liveEndingAudioSelect")?.addEventListener("change", (event) => {
    applyLiveEndingAudioSelection(event.target.value || "0");
  });
  document.getElementById("liveReaderPrevBtn")?.addEventListener("click", async () => {
    await playAdjacentChapter(-1);
  });
  document.getElementById("liveReaderNextBtn")?.addEventListener("click", async () => {
    await playAdjacentChapter(1);
  });
  document.getElementById("liveReaderWidthRange")?.addEventListener("input", (event) => {
    localStorage.setItem(WIDTH_KEY, String(event.target.value || 520));
    applyReaderSettings();
  });
  document.getElementById("liveReaderHeightRange")?.addEventListener("input", (event) => {
    localStorage.setItem(HEIGHT_KEY, String(event.target.value || 820));
    applyReaderSettings();
  });
  document.getElementById("liveReaderFontSizeRange")?.addEventListener("input", (event) => {
    localStorage.setItem(FONT_SIZE_KEY, String(event.target.value || 28));
    applyReaderSettings();
  });
  document.getElementById("liveReaderHighlightIntensityRange")?.addEventListener("input", (event) => {
    localStorage.setItem(HIGHLIGHT_INTENSITY_KEY, String(event.target.value || 45));
    applyReaderSettings();
  });
  document.getElementById("liveReaderFollowSensitivityRange")?.addEventListener("input", (event) => {
    localStorage.setItem(FOLLOW_SENSITIVITY_KEY, String(event.target.value || 60));
    applyReaderSettings();
  });
  document.getElementById("liveReaderFollowSmoothnessRange")?.addEventListener("input", (event) => {
    localStorage.setItem(FOLLOW_SMOOTHNESS_KEY, String(event.target.value || 45));
    applyReaderSettings();
  });
  document.getElementById("liveReaderTopSafeOffsetRange")?.addEventListener("input", (event) => {
    localStorage.setItem(TOP_SAFE_OFFSET_KEY, String(event.target.value || DEFAULT_READER_TOP_SAFE_OFFSET));
    applyReaderSettings();
    updateSegmentHighlight(true);
  });
  document.getElementById("liveReaderThemeSelect")?.addEventListener("change", (event) => {
    localStorage.setItem(READER_THEME_KEY, String(event.target.value || DEFAULT_READER_THEME_ID));
    applyReaderSettings();
  });
  document.getElementById("liveReaderResetThemeBtn")?.addEventListener("click", () => {
    localStorage.setItem(READER_THEME_KEY, DEFAULT_READER_THEME_ID);
    applyReaderSettings();
  });
  document.getElementById("liveReaderAutoNext")?.addEventListener("change", (event) => {
    saveBool(AUTO_NEXT_KEY, Boolean(event.target.checked));
  });
  document.getElementById("liveReaderAutoScroll")?.addEventListener("change", (event) => {
    saveBool(AUTO_SCROLL_KEY, Boolean(event.target.checked));
  });
  document.getElementById("liveReaderHighlight")?.addEventListener("change", (event) => {
    saveBool(HIGHLIGHT_KEY, Boolean(event.target.checked));
    updateSegmentHighlight(true);
    if (!event.target.checked) {
      clearSegmentHighlight();
    }
  });
  document.getElementById("liveReaderIllustrations")?.addEventListener("change", async (event) => {
    saveBool(ILLUSTRATIONS_KEY, Boolean(event.target.checked));
    if (event.target.checked && activeChapterNum) {
      await loadLiveIllustrations(activeChapterNum, chapterLoadToken);
    } else {
      clearLiveIllustration();
    }
  });
  document.getElementById("liveReaderInstallBtn")?.addEventListener("click", async () => {
    await installStandaloneApp();
  });
  const player = document.getElementById("liveReaderAudioPlayer");
  if (AUDIO_TRACE_ENABLED) {
    AUDIO_LOAD_TRACE_EVENTS.forEach((eventName) => {
      player?.addEventListener(eventName, () => logAudioLoadTrace(eventName));
    });
  }
  player?.addEventListener("timeupdate", scheduleTimeUpdate);
  player?.addEventListener("timeupdate", () => updateLiveIllustration(false));
  player?.addEventListener("timeupdate", updateReaderProgressBar);
  player?.addEventListener("loadedmetadata", updateReaderProgressBar);
  player?.addEventListener("play", () => {
    startLiveReaderSyncLoop();
    setStatus("播放中");
    updateReaderProgressBar();
    updateSegmentHighlight(true);
  });
  player?.addEventListener("pause", () => {
    stopLiveReaderSyncLoop();
    setStatus("已暂停");
    updateReaderProgressBar();
  });
  player?.addEventListener("ended", async () => {
    stopLiveReaderSyncLoop();
    updateReaderProgressBar();
    updateSegmentHighlight(true);
    setStatus("播放结束");
    if (document.getElementById("liveReaderAutoNext")?.checked) {
      await playAdjacentChapter(1, { wrap: true });
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") forceLiveReaderSync();
  });
  window.addEventListener("focus", forceLiveReaderSync);
  window.addEventListener("pageshow", forceLiveReaderSync);
}

async function init() {
  renderNav();
  if ("serviceWorker" in navigator && (window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost" || window.location.protocol === "https:")) {
    navigator.serviceWorker.register("./service-worker.js").catch(() => {
      // ignore
    });
  }
  applyReaderSettings();
  applyControlsCollapsedState();
  applyPlaylistCollapsedState();
  updateInstallButtonVisibility();
  const data = await getData({ include: ["novels", "settings"] });
  window.__liveReaderSettings = data.settings || {};
  allNovels = data.novels || [];
  activeNovel = getNovelByQueryOrActive();
  if (!activeNovel) {
    throw new Error("未找到小说");
  }
  syncLiveEndingAudioState();
  renderNovelSelect();
  bindEvents();
  await switchNovel(activeNovel.id);
  localizeDocumentText(document);
}

init().catch((err) => {
  showPageError(err, "页面初始化失败");
});
