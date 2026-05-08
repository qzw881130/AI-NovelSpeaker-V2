import { getActiveNovelId, getCachedData, getData, setActiveNovelId } from "./store.js";
import { t, translateText } from "./i18n.js";

const UI_LANGUAGE_OPTIONS = new Set(["zh-CN", "zh-TW", "en-US", "ja-JP", "ko-KR"]);
const UI_TIMEZONE_OPTIONS = new Set([
  "Asia/Shanghai",
  "Asia/Hong_Kong",
  "Asia/Tokyo",
  "Asia/Seoul",
  "America/New_York",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Paris",
  "Australia/Sydney",
  "UTC",
]);
const UI_DEFAULTS = {
  language: "zh-CN",
  timezone: "Asia/Shanghai",
};

const NAV_ICONS = {
  novels: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 4.5h10a3 3 0 0 1 3 3v11a1 1 0 0 1-1.6.8 4.7 4.7 0 0 0-2.9-.8H7.8a2.3 2.3 0 0 0-2.3 2.3V6.5a2 2 0 0 1 2-2Z"/><path d="M8.5 8.5h7"/><path d="M8.5 12h7"/></svg>`,
  chapters: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4.5h10a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2Z"/><path d="M9 8h6"/><path d="M9 12h6"/><path d="M9 16h4"/></svg>`,
  jsonTasks: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 5c-1.7 1.4-2.5 3.4-2.5 7s.8 5.6 2.5 7"/><path d="M16 5c1.7 1.4 2.5 3.4 2.5 7s-.8 5.6-2.5 7"/><path d="M10 8.5h4"/><path d="M10 12h4"/><path d="M10 15.5h4"/></svg>`,
  lineAudioTasks: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9v6"/><path d="M10 6v12"/><path d="M14 8v8"/><path d="M18 10v4"/><path d="M4 19.5h16"/></svg>`,
  roles: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="3.2"/><path d="M5.5 18.5a6.5 6.5 0 0 1 13 0"/></svg>`,
  downloads: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4.5v10"/><path d="m8.5 11 3.5 3.5 3.5-3.5"/><path d="M5.5 18.5h13"/></svg>`,
  prompts: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 5.5h10a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H12l-4.5 3v-3H7a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2Z"/><path d="M9 9.5h6"/><path d="M9 12.5h4"/></svg>`,
  workflows: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="7" r="2.2"/><circle cx="17" cy="12" r="2.2"/><circle cx="7" cy="17" r="2.2"/><path d="M9.2 8.1l5.6 2.8"/><path d="M9.2 15.9l5.6-2.8"/></svg>`,
  settings: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.4 1a8 8 0 0 0-1.7-1l-.3-2.5h-4l-.3 2.5a8 8 0 0 0-1.7 1l-2.4-1-2 3.4 2 1.5a7 7 0 0 0 0 2l-2 1.5 2 3.4 2.4-1a8 8 0 0 0 1.7 1l.3 2.5h4l.3-2.5a8 8 0 0 0 1.7-1l2.4 1 2-3.4-2-1.5c.1-.3.1-.7.1-1Z"/></svg>`,
  capture: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 6.5 8 4.5H5.5a2 2 0 0 0-2 2V17.5a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-11a2 2 0 0 0-2-2H16l-1.5 2Z"/><circle cx="12" cy="12.5" r="3.2"/><path d="M19 8.5h.01"/></svg>`,
  workflowLogs: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4.5h8l3 3v12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2Z"/><path d="M15 4.5v3h3"/><path d="M8.5 11h7"/><path d="M8.5 14.5h7"/><path d="M8.5 18h4"/></svg>`,
  textFix: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 5.5h12"/><path d="M6 9.5h9"/><path d="M6 13.5h6"/><path d="M14.5 14.5 18 18"/><path d="m16.5 12 4 4"/><path d="M14 18h6"/></svg>`,
  batchRoleLines: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 7.5h15"/><path d="M4.5 12h15"/><path d="M4.5 16.5h9"/><path d="M18.5 16.5h.01"/><path d="M18.5 12h.01"/><path d="M18.5 7.5h.01"/></svg>`,
  liveReader: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 4.5h8a3 3 0 0 1 3 3v11a1 1 0 0 1-1.5.86A5.4 5.4 0 0 0 13 18.5H7.5A2.5 2.5 0 0 0 5 21V6.5a2 2 0 0 1 2-2Z"/><path d="M9 9.5h4"/><path d="M9 13h6"/><path d="M18.5 9.5 21 11l-2.5 1.5Z"/></svg>`,
  audioAsr: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 7.5h15"/><path d="M4.5 12h11"/><path d="M4.5 16.5h8"/><path d="M18 8.5v7"/><path d="m15.5 13 2.5 2.5 2.5-2.5"/></svg>`,
  nsfwReview: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5 20 6.5v5.7c0 4.2-2.7 7.9-8 9.8-5.3-1.9-8-5.6-8-9.8V6.5l8-3Z"/><path d="M9 12h6"/><path d="M12 9v6"/></svg>`,
};

const NAV_EXTERNALS = [
  {
    href: "https://github.com/qzw881130/AI-NovelSpeaker-V2",
    title: "GitHub",
    icon: `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 .5A12 12 0 0 0 8.2 23.9c.6.1.8-.2.8-.6v-2.1c-3.3.7-4-1.4-4-1.4-.5-1.3-1.3-1.7-1.3-1.7-1.1-.7.1-.7.1-.7 1.2.1 1.9 1.2 1.9 1.2 1.1 1.8 2.9 1.3 3.6 1 .1-.8.4-1.3.8-1.7-2.7-.3-5.5-1.4-5.5-6A4.7 4.7 0 0 1 6.6 8c-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.3 11.3 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.7 1.6.3 2.8.1 3.1a4.7 4.7 0 0 1 1.2 3.3c0 4.6-2.8 5.6-5.5 6 .4.4.8 1 .8 2.1v3.1c0 .3.2.7.8.6A12 12 0 0 0 12 .5Z"/></svg>`,
  },
  {
    href: "https://www.bilibili.com/video/BV136AKzcE2c",
    title: "Bilibili",
    icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="6.5" width="16" height="11" rx="3"/><path d="m9 3.5 2 3"/><path d="m15 3.5-2 3"/><path d="M9 11h.01"/><path d="M15 11h.01"/><path d="M8.5 14.5c1 .8 2.2 1.2 3.5 1.2s2.5-.4 3.5-1.2"/></svg>`,
  },
  {
    href: "https://www.youtube.com/watch?v=pVB0qMpFdqg",
    title: "YouTube",
    icon: `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M23.5 7.2a3 3 0 0 0-2.1-2.1C19.6 4.5 12 4.5 12 4.5s-7.6 0-9.4.6A3 3 0 0 0 .5 7.2 31.8 31.8 0 0 0 0 12a31.8 31.8 0 0 0 .5 4.8 3 3 0 0 0 2.1 2.1c1.8.6 9.4.6 9.4.6s7.6 0 9.4-.6a3 3 0 0 0 2.1-2.1A31.8 31.8 0 0 0 24 12a31.8 31.8 0 0 0-.5-4.8ZM9.6 15.5V8.5l6 3.5-6 3.5Z"/></svg>`,
  },
];

const NAV_ITEMS = [
  { href: "./index.html", labelKey: "nav.novels", icon: NAV_ICONS.novels },
  { href: "./chapters.html", labelKey: "nav.chapters", icon: NAV_ICONS.chapters },
  { href: "./json-tasks.html", labelKey: "nav.jsonTasks", icon: NAV_ICONS.jsonTasks },
  { href: "./line-audio-tasks.html", labelKey: "nav.lineAudioTasks", icon: NAV_ICONS.lineAudioTasks },
  { href: "./roles.html", labelKey: "nav.roles", icon: NAV_ICONS.roles },
  { href: "./novel-download.html", labelKey: "nav.downloads", icon: NAV_ICONS.downloads },
  { href: "./prompts.html", labelKey: "nav.prompts", icon: NAV_ICONS.prompts },
  { href: "./workflows.html", labelKey: "nav.workflows", icon: NAV_ICONS.workflows },
  { href: "./settings.html", labelKey: "nav.settings", icon: NAV_ICONS.settings },
  { href: "./novel-capture.html", labelKey: "nav.capture", icon: NAV_ICONS.capture },
  { href: "./workflow-logs.html", labelKey: "nav.workflowLogs", icon: NAV_ICONS.workflowLogs },
  { href: "./text-fix.html", labelKey: "nav.textFix", icon: NAV_ICONS.textFix },
  { href: "./batch-role-lines.html", labelKey: "nav.batchRoleLines", icon: NAV_ICONS.batchRoleLines },
  { href: "./live-reader.html", labelKey: "nav.liveReader", icon: NAV_ICONS.liveReader },
  { href: "./audio-asr.html", labelKey: "nav.audioAsr", icon: NAV_ICONS.audioAsr },
  { href: "./nsfw-review.html", labelKey: "nav.nsfwReview", icon: NAV_ICONS.nsfwReview },
];

const NAV_BADGE_KEYS = {
  json: "ai_novel_nav_badge_json",
  lineAudio: "ai_novel_nav_badge_line_audio",
};

function getNavBadgeCount(type) {
  const key = NAV_BADGE_KEYS[type];
  if (!key) return 0;
  const n = Number(localStorage.getItem(key) || 0);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

function setNavBadgeCount(type, value) {
  const key = NAV_BADGE_KEYS[type];
  if (!key) return;
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) {
    localStorage.removeItem(key);
    return;
  }
  localStorage.setItem(key, String(Math.floor(n)));
}

function incrementNavBadge(type, delta = 1) {
  const next = getNavBadgeCount(type) + Number(delta || 0);
  setNavBadgeCount(type, next);
}

function clearNavBadge(type) {
  setNavBadgeCount(type, 0);
}

function navBadgeForHref(href) {
  if (href === "./json-tasks.html") {
    return getNavBadgeCount("json");
  }
  if (href === "./line-audio-tasks.html") {
    return getNavBadgeCount("lineAudio");
  }
  return 0;
}

const NAV_COLLAPSED_KEY = "ai_novel_nav_collapsed";

function isNavCollapsed() {
  return localStorage.getItem(NAV_COLLAPSED_KEY) === "true";
}

function setNavCollapsed(collapsed) {
  localStorage.setItem(NAV_COLLAPSED_KEY, String(collapsed));
}

function updateNavLayout() {
  const appShell = document.querySelector(".app-shell");
  if (appShell) {
    appShell.classList.toggle("nav-collapsed", isNavCollapsed());
  }
}

function toggleNav() {
  const newState = !isNavCollapsed();
  setNavCollapsed(newState);
  updateNavLayout();
}

function renderNav() {
  const nav = document.getElementById("mainNav");
  if (!nav) return;
  const current = window.location.pathname.split("/").pop() || "index.html";
  const collapsed = isNavCollapsed();
  const links = NAV_ITEMS.map((item) => {
    const active = current === item.href.replace("./", "") ? "active" : "";
    const badge = navBadgeForHref(item.href);
    return `<a class="nav-link ${active}" href="${item.href}" title="${t(item.labelKey)}"><i class="nav-icon" aria-hidden="true">${item.icon}</i><span class="nav-label">${t(item.labelKey)}</span>${badge > 0 ? `<i class="nav-badge">+${badge}</i>` : ""}</a>`;
  }).join("");
  const externals = NAV_EXTERNALS.map(
    (item) =>
      `<a class="nav-external-link" href="${item.href}" target="_blank" rel="noopener noreferrer" title="${item.title}" aria-label="${item.title}"><i class="nav-external-icon" aria-hidden="true">${item.icon}</i></a>`
  ).join("");

  nav.innerHTML = `
    <div class="brand">
      <strong>AI NovelSpeaker V2</strong>
    </div>
    <button class="nav-toggle" id="navToggle" title="${collapsed ? "展开菜单" : "收起菜单"}">◀</button>
    ${links}
    <div class="nav-external-row">${externals}</div>
  `;

  // Bind toggle event
  const toggleBtn = document.getElementById("navToggle");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", toggleNav);
  }

  // Apply initial collapsed state
  updateNavLayout();
}

async function bindNovelSelector(selectId, onChanged) {
  const select = document.getElementById(selectId);
  if (!select) return;
  const data = await getData();
  const activeId = getActiveNovelId();
  select.innerHTML = data.novels
    .map((n) => `<option value="${n.id}">${n.name}</option>`)
    .join("");
  if (activeId) {
    select.value = activeId;
  }
  select.onchange = () => {
    setActiveNovelId(select.value);
    if (onChanged) onChanged(select.value);
  };
}

let toastTimer;
function toast(msg) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove("show");
  }, 1800);
}

function fmtNumber(num) {
  const { language } = getUiPrefs();
  return new Intl.NumberFormat(language).format(Number(num || 0));
}

function getUiPrefs() {
  const ui = getCachedData()?.settings?.ui || {};
  const language = UI_LANGUAGE_OPTIONS.has(String(ui.language || ""))
    ? String(ui.language)
    : UI_DEFAULTS.language;
  const timezone = UI_TIMEZONE_OPTIONS.has(String(ui.timezone || ""))
    ? String(ui.timezone)
    : UI_DEFAULTS.timezone;
  return { language, timezone };
}

function fmtDateTime(input, options = {}) {
  if (input == null || input === "") return "-";
  const dt = input instanceof Date ? input : new Date(input);
  if (Number.isNaN(dt.getTime())) return String(input);
  const { language, timezone } = getUiPrefs();
  return dt.toLocaleString(language, { hour12: false, timeZone: timezone, ...options });
}

function showPageError(error, fallbackText = "页面初始化失败") {
  const page = document.querySelector(".page");
  if (!page) return;
  const message = translateText(String(error?.message || fallbackText || "页面初始化失败"));
  let box = document.getElementById("pageErrorBanner");
  if (!box) {
    box = document.createElement("div");
    box.id = "pageErrorBanner";
    box.className = "page-error-banner";
    page.prepend(box);
  }
  box.innerHTML = `
    <strong>${t("error.loadFailed", { msg: "" }).replace(/:\s*$/, "")}</strong>
    <span>${message}</span>
    <span>scripts/init_storage.py / app_server.py</span>
  `;
  toast(t("error.loadFailed", { msg: message }));
}

export {
  bindNovelSelector,
  clearNavBadge,
  fmtDateTime,
  fmtNumber,
  getUiPrefs,
  incrementNavBadge,
  renderNav,
  showPageError,
  toast,
};
