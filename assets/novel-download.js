import { bytesToText, fetchNovelDownloadChapters, getData, getActiveNovelId, setActiveNovelId } from "./store.js";
import { renderNav, toast } from "./ui.js";

let allNovels = [];
let activeNovel = null;
let chapterItems = [];

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
  const rolesLink = document.getElementById("novelDownloadRolesLink");
  const chaptersLink = document.getElementById("novelDownloadChaptersLink");
  if (!activeNovel) {
    titleEl.textContent = "小说下载";
    metaEl.textContent = "未找到小说";
    summaryEl.textContent = "-";
    return;
  }
  titleEl.textContent = `${activeNovel.name} - 小说下载`;
  const available = chapterItems.filter((item) => item.hasAudio).length;
  metaEl.textContent = `${activeNovel.author || "未知作者"} · 共 ${chapterItems.length} 回 · 可下载 ${available} 回`;
  summaryEl.textContent = `总计 ${chapterItems.length} 回`;
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

function renderTable() {
  const tbody = document.getElementById("novelDownloadTableBody");
  if (!activeNovel) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-text">未找到小说</td></tr>';
    return;
  }
  if (!chapterItems.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-text">暂无章回数据</td></tr>';
    return;
  }
  tbody.innerHTML = chapterItems.map((item) => `
    <tr>
      <td>${String(item.chapterNum).padStart(3, "0")}</td>
      <td>${escapeHtml(item.title || "-")}</td>
      <td>${Number(item.wordCount || 0).toLocaleString("zh-CN")}</td>
      <td>${item.hasAudio ? formatDuration(item.audioDurationSeconds || 0) : "-"}</td>
      <td>${item.hasAudio ? bytesToText(item.audioSizeBytes || 0) : "-"}</td>
      <td>${item.hasAudio ? `<a class="ghost-btn btn-sm" href="${item.downloadUrl}">下载音频</a>` : '<span class="text-muted">暂无音频</span>'}</td>
    </tr>
  `).join("");
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
    await refreshPage();
  });
  document.getElementById("refreshNovelDownloadBtn").addEventListener("click", async () => {
    await refreshPage();
    toast("小说下载列表已刷新");
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
