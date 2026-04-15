import { getActiveNovelId, getData, searchNovelText, replaceNovelText, setActiveNovelId } from "./store.js";
import { renderNav, toast } from "./ui.js";

let allNovels = [];
let activeNovel = null;
let currentSearchText = "";
let currentResults = [];
let currentTotalCount = 0;

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text || "");
  return div.innerHTML;
}

function getNovelByQueryOrActive() {
  const url = new URL(window.location.href);
  const queryId = String(url.searchParams.get("novelId") || "");
  if (queryId) {
    return allNovels.find((n) => String(n.id) === queryId) || null;
  }
  const activeId = String(getActiveNovelId() || "");
  if (activeId) {
    return allNovels.find((n) => String(n.id) === activeId) || null;
  }
  return allNovels[0] || null;
}

function renderNovelSelect() {
  const select = document.getElementById("textFixNovelSelect");
  select.innerHTML = allNovels.map((novel) => `<option value="${novel.id}">${escapeHtml(novel.name)}</option>`).join("");
  if (activeNovel) {
    select.value = String(activeNovel.id);
  }
}

function renderHeader() {
  document.getElementById("textFixMeta").textContent = activeNovel
    ? `${activeNovel.name} · 搜索范围：所有章回 txt 及 JSON`
    : "未找到小说";
  document.getElementById("textFixSummary").textContent = currentSearchText
    ? `搜索：${currentSearchText}`
    : "尚未搜索";
  document.getElementById("textFixCount").textContent = `共 ${currentTotalCount} 处，涉及 ${currentResults.length} 个章回`;
}

function renderResults() {
  const wrap = document.getElementById("textFixResultList");
  if (!currentSearchText) {
    wrap.innerHTML = '<p class="empty-text">请输入搜索文本后点击搜索。</p>';
    return;
  }
  if (!currentResults.length) {
    wrap.innerHTML = '<p class="empty-text">没有找到匹配内容。</p>';
    return;
  }
  wrap.innerHTML = currentResults
    .map(
      (item) => `
        <article class="asset-card text-fix-result-card">
          <div class="queue-head">
            <h3>${String(item.chapterNum).padStart(3, "0")} · ${escapeHtml(item.title || "-")}</h3>
          </div>
          <p class="meta">txt 命中 ${item.txtCount || 0} 次 · json 命中 ${item.jsonCount || 0} 次</p>
        </article>
      `
    )
    .join("");
}

async function runSearch() {
  const searchText = String(document.getElementById("textFixSearchInput").value || "").trim();
  currentSearchText = searchText;
  if (!activeNovel) {
    toast("未找到小说");
    return;
  }
  if (!searchText) {
    currentResults = [];
    currentTotalCount = 0;
    renderHeader();
    renderResults();
    toast("请输入搜索文本");
    return;
  }
  const data = await searchNovelText(activeNovel.id, searchText);
  currentResults = data.matches || [];
  currentTotalCount = Number(data.totalCount || 0);
  renderHeader();
  renderResults();
}

async function runReplace() {
  if (!activeNovel) {
    toast("未找到小说");
    return;
  }
  const searchText = String(document.getElementById("textFixSearchInput").value || "").trim();
  const replaceText = String(document.getElementById("textFixReplaceInput").value || "");
  if (!searchText) {
    toast("请输入搜索文本");
    return;
  }
  if (!window.confirm(`确定要将“${searchText}”替换为“${replaceText}”吗？`)) return;
  const result = await replaceNovelText(activeNovel.id, searchText, replaceText);
  toast(`替换完成：txt ${result.txtReplaced || 0} 处，json ${result.jsonReplaced || 0} 处`);
  await runSearch();
}

function bindEvents() {
  document.getElementById("textFixNovelSelect").addEventListener("change", async (event) => {
    const id = String(event.target.value || "");
    setActiveNovelId(id);
    activeNovel = allNovels.find((n) => String(n.id) === id) || null;
    currentResults = [];
    currentTotalCount = 0;
    currentSearchText = "";
    renderHeader();
    renderResults();
  });
  document.getElementById("textFixSearchBtn").addEventListener("click", runSearch);
  document.getElementById("textFixReplaceBtn").addEventListener("click", runReplace);
  document.getElementById("textFixRefreshBtn").addEventListener("click", async () => {
    const data = await getData();
    allNovels = data.novels || [];
    activeNovel = getNovelByQueryOrActive();
    renderNovelSelect();
    renderHeader();
    renderResults();
    toast("数据已刷新");
  });
}

async function init() {
  renderNav();
  const data = await getData();
  allNovels = data.novels || [];
  activeNovel = getNovelByQueryOrActive();
  renderNovelSelect();
  bindEvents();
  renderHeader();
  renderResults();
}

init().catch((err) => {
  renderNav();
  toast(err.message || "加载失败");
});
