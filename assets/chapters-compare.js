import {
  fetchChapterCompareData,
  fetchNovelChapters,
  getActiveNovelId,
  getData,
} from "./store.js";
import { renderNav, toast } from "./ui.js";
import { localizeDocumentText, t, translateText } from "./i18n.js";

let allNovels = [];
let activeNovel = null;
let compareSelectionText = "";
let chapterItems = [];
const COMPARE_FONT_SIZE_KEY = "compareFontSizePx";

function getSavedCompareFontSize() {
  const raw = Number(localStorage.getItem(COMPARE_FONT_SIZE_KEY) || 17);
  if (!Number.isFinite(raw)) return 17;
  return Math.min(30, Math.max(14, Math.round(raw)));
}

function applyCompareFontSize(px) {
  const size = Math.min(30, Math.max(14, Math.round(Number(px) || 17)));
  const range = document.getElementById("compareFontSizeRange");
  const value = document.getElementById("compareFontSizeValue");
  document.documentElement.style.setProperty("--compare-font-size", `${size}px`);
  if (range) {
    range.value = String(size);
  }
  if (value) {
    value.textContent = `${size}px`;
  }
}

function saveCompareFontSize(px) {
  const size = Math.min(30, Math.max(14, Math.round(Number(px) || 17)));
  localStorage.setItem(COMPARE_FONT_SIZE_KEY, String(size));
  applyCompareFontSize(size);
}

function bindCompareFontSizeControl() {
  const range = document.getElementById("compareFontSizeRange");
  applyCompareFontSize(getSavedCompareFontSize());
  if (!range) return;
  range.addEventListener("input", (event) => {
    saveCompareFontSize(event.target.value);
  });
}

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

function tokenizeCompareText(text) {
  return Array.from(String(text || "")).map((char) => ({
    char,
    comparable: /[“”‘’"'，。！？、；：,.!?;:（）()《》〈〉【】『』「」\[\]…—\-]/.test(char) || /\s/.test(char) ? null : char,
  }));
}

function diffChars(aText, bText) {
  const aTokens = tokenizeCompareText(aText);
  const bTokens = tokenizeCompareText(bText);
  const aComparable = aTokens
    .map((token, index) => ({ index, value: token.comparable }))
    .filter((token) => token.value !== null);
  const bComparable = bTokens
    .map((token, index) => ({ index, value: token.comparable }))
    .filter((token) => token.value !== null);

  const a = aComparable.map((token) => token.value);
  const b = bComparable.map((token) => token.value);
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

  const aTypes = aTokens.map((token) => (token.comparable === null ? "ignore" : "same"));
  const bTypes = bTokens.map((token) => (token.comparable === null ? "ignore" : "same"));
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      aTypes[aComparable[i].index] = "del";
      i += 1;
    } else {
      bTypes[bComparable[j].index] = "add";
      j += 1;
    }
  }
  while (i < m) {
    aTypes[aComparable[i].index] = "del";
    i += 1;
  }
  while (j < n) {
    bTypes[bComparable[j].index] = "add";
    j += 1;
  }

  return {
    aTokens: aTokens.map((token, index) => ({ type: aTypes[index], text: token.char })),
    bTokens: bTokens.map((token, index) => ({ type: bTypes[index], text: token.char })),
    exactMatch: a.join("") === b.join(""),
    comparableCount: b.length,
  };
}

function tokensToHtml(tokens) {
  return tokens
    .map((token) => {
      const cls = token.type === "same" || token.type === "ignore"
        ? "compare-char-same"
        : token.type === "add"
          ? "compare-char-add"
          : "compare-char-del";
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
  const { aTokens, bTokens, exactMatch, comparableCount } = diffChars(parsedJoined, originalJoined);
  const missingCount = bTokens.filter((token) => token.type === "add").length;
  const parsedCharCount = parsedLines.join("").replace(/\s+/g, "").length;

  parsedEl.innerHTML = `<div class="compare-block compare-rich-block">${tokensToHtml(aTokens)}</div>`;
  originalEl.innerHTML = `<div class="compare-block compare-rich-block">${tokensToHtml(bTokens)}</div>`;

  summaryEl.textContent = exactMatch
    ? translateText("解析结果与原文完全一致")
    : `${translateText("解析后台词行数")}: ${parsedLines.length} · ${translateText("解析后台词字符数")}: ${parsedCharCount} · ${translateText("去标点后字符数")}: ${comparableCount} · ${translateText("丢失")} ${missingCount} ${translateText("个")}`;
}

function hideSelectionBubble() {
  const bubble = document.getElementById("compareSelectionBubble");
  if (!bubble) return;
  bubble.classList.add("hidden");
  bubble.textContent = "";
  compareSelectionText = "";
}

function normalizeSelectedText(text) {
  return String(text || "")
    .replace(/[“”‘’"'『』「」]/g, "")
    .replace(/\s+/g, "")
    .trim();
}

function showSelectionBubble(selection, rect) {
  const bubble = document.getElementById("compareSelectionBubble");
  if (!bubble) return;
  const cleaned = normalizeSelectedText(selection);
  if (!cleaned) {
    hideSelectionBubble();
    return;
  }
  compareSelectionText = `旁白:${cleaned}`;
  bubble.textContent = compareSelectionText;
  bubble.classList.remove("hidden");

  const bubbleRect = bubble.getBoundingClientRect();
  const top = Math.max(12, rect.top - bubbleRect.height - 14);
  const left = Math.min(
    Math.max(12, rect.left + rect.width / 2 - bubbleRect.width / 2),
    window.innerWidth - bubbleRect.width - 12
  );
  bubble.style.top = `${top}px`;
  bubble.style.left = `${left}px`;
}

function bindSelectionBubble() {
  const bubble = document.getElementById("compareSelectionBubble");
  const columns = document.getElementById("compareColumns");
  if (!bubble || !columns) return;

  const updateFromSelection = () => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) {
      hideSelectionBubble();
      return;
    }
    const text = sel.toString();
    const range = sel.getRangeAt(0);
    const commonAncestor = range.commonAncestorContainer;
    const withinCompare = columns.contains(commonAncestor.nodeType === 1 ? commonAncestor : commonAncestor.parentElement);
    if (!withinCompare) {
      hideSelectionBubble();
      return;
    }
    const rect = range.getBoundingClientRect();
    if (!rect || (!rect.width && !rect.height)) {
      hideSelectionBubble();
      return;
    }
    showSelectionBubble(text, rect);
  };

  document.addEventListener("selectionchange", updateFromSelection);
  document.addEventListener("scroll", () => {
    if (compareSelectionText) {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) {
        hideSelectionBubble();
        return;
      }
      const rect = sel.getRangeAt(0).getBoundingClientRect();
      showSelectionBubble(sel.toString(), rect);
    }
  }, true);
  document.addEventListener("mousedown", (event) => {
    if (bubble.contains(event.target)) return;
    if (!columns.contains(event.target)) {
      hideSelectionBubble();
    }
  });

  bubble.addEventListener("click", async () => {
    if (!compareSelectionText) return;
    await navigator.clipboard.writeText(compareSelectionText);
    toast(translateText("已复制"));
  });

  bubble.addEventListener("keydown", async (event) => {
    if ((event.key === "Enter" || event.key === " ") && compareSelectionText) {
      event.preventDefault();
      await navigator.clipboard.writeText(compareSelectionText);
      toast(translateText("已复制"));
    }
  });
}

function updateChapterNav(chapterNum) {
  const prevBtn = document.getElementById("prevCompareChapterBtn");
  const nextBtn = document.getElementById("nextCompareChapterBtn");
  const idx = chapterItems.findIndex((item) => Number(item.chapterNum) === Number(chapterNum));
  prevBtn.disabled = idx <= 0;
  nextBtn.disabled = idx < 0 || idx >= chapterItems.length - 1;

  prevBtn.onclick = () => {
    if (idx <= 0) return;
    const prev = chapterItems[idx - 1];
    window.location.href = `./chapters-compare.html?novelId=${Number(activeNovel.id)}&chapterNum=${Number(prev.chapterNum)}`;
  };
  nextBtn.onclick = () => {
    if (idx < 0 || idx >= chapterItems.length - 1) return;
    const next = chapterItems[idx + 1];
    window.location.href = `./chapters-compare.html?novelId=${Number(activeNovel.id)}&chapterNum=${Number(next.chapterNum)}`;
  };
}

async function init() {
  renderNav();
  bindSelectionBubble();
  bindCompareFontSizeControl();
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
    chapterItems = await fetchNovelChapters(activeNovel.id);
    const { detail, jsonOutput } = await fetchChapterCompareData(activeNovel.id, chapterNum);
    const parsedLines = parseJubenLines(jsonOutput?.jsonText || "");
    document.getElementById("comparePageTitle").textContent = `${activeNovel.name} - ${detail.title}`;
    document.getElementById("comparePageMeta").textContent = `${translateText("章节")}: ${detail.chapterNum}`;
    updateChapterNav(detail.chapterNum);

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
