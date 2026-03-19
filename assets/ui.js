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

const NAV_ITEMS = [
  { href: "./index.html", labelKey: "nav.novels" },
  { href: "./chapters.html", labelKey: "nav.chapters" },
  { href: "./json-tasks.html", labelKey: "nav.jsonTasks" },
  { href: "./audio-queue.html", labelKey: "nav.audioQueue" },
  { href: "./prompts.html", labelKey: "nav.prompts" },
  { href: "./workflows.html", labelKey: "nav.workflows" },
  { href: "./settings.html", labelKey: "nav.settings" },
  { href: "./novel-capture.html", labelKey: "nav.capture" },
];

const NAV_BADGE_KEYS = {
  json: "ai_novel_nav_badge_json",
  audio: "ai_novel_nav_badge_audio",
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
  if (href === "./audio-queue.html") {
    return getNavBadgeCount("audio");
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
    return `<a class="nav-link ${active}" href="${item.href}"><span>${t(item.labelKey)}</span>${badge > 0 ? `<i class="nav-badge">+${badge}</i>` : ""}</a>`;
  }).join("");

  nav.innerHTML = `
    <div class="brand">
      <strong>AI NovelSpeaker V1</strong>
    </div>
    <button class="nav-toggle" id="navToggle" title="${collapsed ? "展开菜单" : "收起菜单"}">◀</button>
    ${links}
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
