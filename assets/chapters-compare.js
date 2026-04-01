import {
  fetchChapterCompareData,
  getActiveNovelId,
  getData,
} from "./store.js";
import { renderNav, toast } from "./ui.js";
import { localizeDocumentText, t, translateText } from "./i18n.js";

let allNovels = [];
let activeNovel = null;

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
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

function parseJubenLines(jsonText) {
  const raw = String(jsonText || "").trim();
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    const juben = String(parsed.juben || "").trim();
    return juben ? juben.split(/\r?\n/).map((line) => line.trim()).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function normalizeChars(text) {
  return Array.from(String(text || "").replace(/\s+/g, "").trim());
}

function diffChars(aText, bText) {
  const a = normalizeChars(aText);
  const b = normalizeChars(bText);
  const m = a.length;
  const n = b.length;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));

  for (let i = m - 1; i >= 0; i -= 1) {
    for (let j = n - 1; j >= 0; j -= 1) {
      dp[i][j] = a[i] === b[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const aTokens = [];
  const bTokens = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      aTokens.push({ type: "same", text: a[i] });
      bTokens.push({ type: "same", text: b[j] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      aTokens.push({ type: "del", text: a[i] });
      i += 1;
    } else {
      bTokens.push({ type: "add", text: b[j] });
      j += 1;
    }
  }
  while (i < m) {
    aTokens.push({ type: "del", text: a[i] });
    i += 1;
  }
  while (j < n) {
    bTokens.push({ type: "add", text: b[j] });
    j += 1;
  }

  return { aTokens, bTokens, exactMatch: a.join("") === b.join("") };
}

function tokensToHtml(tokens) {
  return tokens
    .map((token) => {
      const cls = token.type === "same" ? "compare-char-same" : token.type === "add" ? "compare-char-add" : "compare-char-del";
      return `<span class="${cls}">${escapeHtml(token.text)}</span>`;
    })
    .join("");
}

function renderDiffBlocks(parsedLines, originalText) {
  const parsedEl = document.getElementById("compareParsed");
  const originalEl = document.getElementById("compareOriginal");
  const summaryEl = document.getElementById("compareSummary");

  const parsedJoined = parsedLines.join("\n");
  const originalJoined = String(originalText || "").trim();
  const { aTokens, bTokens, exactMatch } = diffChars(parsedJoined, originalJoined);

  parsedEl.innerHTML = `<div class="compare-block compare-rich-block">${tokensToHtml(aTokens)}</div>`;
  originalEl.innerHTML = `<div class="compare-block compare-rich-block">${tokensToHtml(bTokens)}</div>`;

  summaryEl.textContent = exactMatch
    ? translateText("解析结果与原文完全一致")
    : `${translateText("解析后台词行数")}: ${parsedLines.length} · ${translateText("原文字符数")}: ${normalizeChars(originalJoined).length}`;
}

async function init() {
  renderNav();
  const data = await getData();
  allNovels = data.novels || [];
  activeNovel = getNovelByQueryOrActive();
  const url = new URL(window.location.href);
  const chapterNum = Number(url.searchParams.get("chapterNum") || 0);

  if (!activeNovel || !chapterNum) {
    document.getElementById("compareEmpty").textContent = translateText("缺少小说或章节参数");
    localizeDocumentText(document);
    return;
  }

  document.getElementById("backToChaptersBtn").href = `./chapters.html?novelId=${activeNovel.id}`;

  try {
    const { detail, jsonOutput } = await fetchChapterCompareData(activeNovel.id, chapterNum);
    const parsedLines = parseJubenLines(jsonOutput?.jsonText || "");
    document.getElementById("comparePageTitle").textContent = `${activeNovel.name} - ${detail.title}`;
    document.getElementById("comparePageMeta").textContent = `${translateText("章节")}: ${detail.chapterNum}`;

    if (!parsedLines.length) {
      document.getElementById("compareEmpty").textContent = translateText("当前章节暂无可对比的台词数据");
      localizeDocumentText(document);
      return;
    }

    renderDiffBlocks(parsedLines, detail.content || "");
    document.getElementById("compareColumns").classList.remove("hidden");
    document.getElementById("compareEmpty").classList.add("hidden");
    localizeDocumentText(document);
  } catch (err) {
    document.getElementById("compareEmpty").textContent = err.message;
    toast(t("error.pageLoad", { msg: err.message }));
  }
}

init().catch((err) => {
  renderNav();
  toast(t("error.pageLoad", { msg: err.message }));
});
