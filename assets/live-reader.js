import { fetchChapterAsrFile, fetchChapterDetail, fetchNovelChapters, getActiveNovelId, getData, setActiveNovelId } from "./store.js";
import { renderNav, showPageError, toast } from "./ui.js";
import { localizeDocumentText, translateText } from "./i18n.js";

const WIDTH_KEY = "ai_novel_live_reader_width";
const HEIGHT_KEY = "ai_novel_live_reader_height";
const FONT_SIZE_KEY = "ai_novel_live_reader_font_size";
const AUTO_NEXT_KEY = "ai_novel_live_reader_auto_next";
const AUTO_SCROLL_KEY = "ai_novel_live_reader_auto_scroll";
const HIGHLIGHT_KEY = "ai_novel_live_reader_highlight";
const HIGHLIGHT_INTENSITY_KEY = "ai_novel_live_reader_highlight_intensity";
const FOLLOW_SENSITIVITY_KEY = "ai_novel_live_reader_follow_sensitivity";
const FOLLOW_SMOOTHNESS_KEY = "ai_novel_live_reader_follow_smoothness";
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

function splitParagraphs(text) {
  const content = String(text || "").replace(/\r/g, "").trim();
  if (!content) return [];
  return content
    .split(/\n+/)
    .map((item) => String(item || "").trim())
    .filter(Boolean);
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

function getAudioStreamUrl(chapterNum) {
  return `/api/novels/${Number(activeNovel?.id || 0)}/chapters/${Number(chapterNum)}/audio-stream?rand=${Date.now()}`;
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

function setStatus(text) {
  const el = document.getElementById("liveReaderStatus");
  if (el) el.textContent = translateText(text);
}

function setMatchStatus(text) {
  const el = document.getElementById("liveReaderMatchStatus");
  if (el) el.textContent = text;
}

function syncLiveEndingAudioState() {
  const path = String((window.__liveReaderSettings?.liveEndingAudio || {}).path || "").trim();
  const btn = document.getElementById("liveEndingAudioPlayBtn");
  const player = document.getElementById("liveEndingAudioPlayer");
  if (!btn || !player) return;
  const hasAudio = Boolean(path);
  btn.classList.toggle("hidden", !hasAudio);
  player.src = hasAudio ? `/api/settings/live-ending-audio/file?v=${Date.now()}` : "";
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

function applyReaderSettings() {
  const width = getSavedNumber(WIDTH_KEY, 520, 140, 900);
  const height = getSavedNumber(HEIGHT_KEY, 820, 320, 1200);
  const fontSize = getSavedNumber(FONT_SIZE_KEY, 28, 18, 42);
  const highlightIntensity = getSavedNumber(HIGHLIGHT_INTENSITY_KEY, 45, 0, 100) / 100;
  const followSensitivity = getSavedNumber(FOLLOW_SENSITIVITY_KEY, 60, 0, 240);
  const followSmoothness = getSavedNumber(FOLLOW_SMOOTHNESS_KEY, 45, 10, 100);
  const content = document.getElementById("liveReaderContent");
  const wrap = document.querySelector(".live-reader-reader-wrap");
  if (content) {
    content.style.width = `${width}px`;
    content.style.maxWidth = `${width}px`;
    content.style.fontSize = `${fontSize}px`;
  }
  if (wrap) {
    wrap.style.height = `${height}px`;
    wrap.style.width = `${width + 36}px`;
    wrap.style.maxWidth = "100%";
  }
  document.getElementById("liveReaderWidthRange").value = String(width);
  document.getElementById("liveReaderWidthValue").textContent = `${width}px`;
  document.getElementById("liveReaderHeightRange").value = String(height);
  document.getElementById("liveReaderHeightValue").textContent = `${height}px`;
  document.getElementById("liveReaderFontSizeRange").value = String(fontSize);
  document.getElementById("liveReaderFontSizeValue").textContent = `${fontSize}px`;
  document.getElementById("liveReaderHighlightIntensityRange").value = String(Math.round(highlightIntensity * 100));
  document.getElementById("liveReaderHighlightIntensityValue").textContent = `${Math.round(highlightIntensity * 100)}%`;
  document.getElementById("liveReaderFollowSensitivityRange").value = String(followSensitivity);
  document.getElementById("liveReaderFollowSensitivityValue").textContent = `${followSensitivity}px`;
  document.getElementById("liveReaderFollowSmoothnessRange").value = String(followSmoothness);
  document.getElementById("liveReaderFollowSmoothnessValue").textContent = `${followSmoothness}%`;
  document.getElementById("liveReaderAutoNext").checked = getSavedBool(AUTO_NEXT_KEY, true);
  document.getElementById("liveReaderAutoScroll").checked = getSavedBool(AUTO_SCROLL_KEY, true);
  document.getElementById("liveReaderHighlight").checked = getSavedBool(HIGHLIGHT_KEY, true);
  document.documentElement.style.setProperty("--live-highlight-alpha", String(highlightIntensity));
  document.documentElement.style.setProperty("--live-paragraph-alpha", String(Math.max(0, highlightIntensity * 0.45)));
}

function getFollowSensitivity() {
  return getSavedNumber(FOLLOW_SENSITIVITY_KEY, 60, 0, 240);
}

function getFollowSmoothnessFactor() {
  return getSavedNumber(FOLLOW_SMOOTHNESS_KEY, 45, 10, 100) / 100;
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
  root.innerHTML = audioChapterItems
    .map((item) => {
      const active = Number(item.chapterNum) === Number(activeChapterNum) ? " active" : "";
      return `<button class="live-reader-playlist-item${active}" data-chapter-num="${item.chapterNum}" type="button"><strong>${String(item.chapterNum).padStart(3, "0")}</strong><span>${item.title}</span></button>`;
    })
    .join("");
  root.querySelectorAll("[data-chapter-num]").forEach((el) => {
    el.addEventListener("click", async () => {
      await loadChapter(Number(el.dataset.chapterNum), { autoplay: false });
    });
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

function mapAsrSegmentsToOriginalText(originalText, asrSegments) {
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
    if (!startMap || !endMap || startMap.paragraphIndex !== endMap.paragraphIndex) {
      cursor = foundAt + matchedLength;
      continue;
    }
    segment.paragraphIndex = startMap.paragraphIndex;
    segment.startChar = startMap.charIndex;
    segment.endChar = endMap.charIndex + 1;
    segment.matchStrategy = match.strategy;
    segment.matched = true;
    cursor = foundAt + matchedLength;
  }
  return {
    paragraphs,
  };
}

function renderOriginalParagraphsWithHighlights(originalText, asrSegments) {
  const { paragraphs } = mapAsrSegmentsToOriginalText(originalText, asrSegments);
  return paragraphs
    .map((paragraphText, paragraphIndex) => {
      const ranges = asrSegments
        .filter((segment) => segment.paragraphIndex === paragraphIndex && Number.isInteger(segment.startChar) && Number.isInteger(segment.endChar))
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
    readerScrollAnimationId = window.requestAnimationFrame(step);
  };
  readerScrollAnimationId = window.requestAnimationFrame(step);
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

function renderReadingContent(text, asrSegments = []) {
  const contentEl = document.getElementById("liveReaderContent");
  if (!contentEl) return;
  currentAsrMode = Array.isArray(asrSegments) && asrSegments.length > 0;
  readingSegments = currentAsrMode ? asrSegments : buildReadingSegments(text);
  activeSegmentIndex = -1;
  if (!readingSegments.length) {
    contentEl.textContent = "暂无正文";
    return;
  }
  if (currentAsrMode) {
    contentEl.innerHTML = renderOriginalParagraphsWithHighlights(text, asrSegments);
    return;
  }
  contentEl.innerHTML = readingSegments
    .map(
      (segment) =>
        `<p class="live-reader-segment live-reader-paragraph" data-segment-index="${segment.index}">${escapeHtml(segment.text)}</p>`
    )
    .join("");
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

  const prevEl = activeSegmentIndex >= 0 ? wrap.querySelector(`[data-segment-index="${activeSegmentIndex}"]`) : null;
  if (prevEl) prevEl.classList.remove("active");
  if (activeParagraphElement) {
    activeParagraphElement.classList.remove("active");
  }
  activeSegmentIndex = nextIndex;
  const activeEl = wrap.querySelector(`[data-segment-index="${activeSegmentIndex}"]`);
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
  if (enableHighlight) activeEl.classList.add("active");
  if (!enableHighlight) activeEl.classList.remove("active");
  if (autoScroll && paragraphEl) {
    const sensitivity = getFollowSensitivity();
    const targetTop = Math.max(
      0,
      activeEl.offsetTop - (wrap.clientHeight - activeEl.offsetHeight) / 2
    );
    const diff = Math.abs(wrap.scrollTop - targetTop);
    if (diff > sensitivity) {
      targetReaderScrollTop = targetTop;
      runReaderScrollAnimation();
    }
  }
}

function clearSegmentHighlight() {
  const wrap = document.querySelector(".live-reader-reader-wrap");
  if (!wrap) return;
  wrap.querySelectorAll(".live-reader-segment.active").forEach((el) => el.classList.remove("active"));
  wrap.querySelectorAll(".live-reader-paragraph.active").forEach((el) => el.classList.remove("active"));
  activeSegmentIndex = -1;
  activeParagraphElement = null;
}

function scrollReaderByProgress() {
  updateSegmentHighlight(false);
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
  activeChapterNum = chapterNum;
  const detail = await fetchChapterDetail(activeNovel.id, chapterNum);
  activeChapterDetail = detail;
  document.getElementById("liveReaderChapterTitle").textContent = detail.title;
  document.getElementById("liveReaderChapterMeta").textContent = `${detail.novelName} · 章节 ${detail.chapterNum} · 字数 ${detail.wordCount || 0}`;
  let asrSegments = [];
  try {
    const asrText = await fetchChapterAsrFile(activeNovel.id, chapterNum);
    asrSegments = parseAsrContent(asrText);
  } catch {
    asrSegments = [];
  }
  renderReadingContent(String(detail.content || "").trim(), asrSegments);
  const player = document.getElementById("liveReaderAudioPlayer");
  if (detail.hasAudio) {
    player.src = getAudioStreamUrl(chapterNum);
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
  resetReaderScroll();
  updateSegmentHighlight(true);
  setStatus(asrSegments.length ? "已加载精准时间轴" : "就绪");
  setMatchStatus(asrSegments.length ? "匹配: 初始化中" : "匹配: 估算同步");
  renderPlaylist();
  updateNavButtons();
  localizeDocumentText(document);
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

async function playAdjacentChapter(step) {
  const idx = audioChapterItems.findIndex((item) => Number(item.chapterNum) === Number(activeChapterNum));
  if (idx < 0) return;
  const next = audioChapterItems[idx + step];
  if (!next) return;
  await loadChapter(next.chapterNum, { autoplay: true });
}

function bindEvents() {
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredInstallPrompt = event;
    updateInstallButtonVisibility();
  });
  window.matchMedia("(display-mode: standalone)").addEventListener?.("change", updateInstallButtonVisibility);
  document.getElementById("liveReaderNovelSelect")?.addEventListener("change", async (event) => {
    await switchNovel(event.target.value);
  });
  document.getElementById("refreshLiveReaderBtn")?.addEventListener("click", async () => {
    await loadNovelChapters();
    toast("已刷新");
  });
  document.getElementById("liveEndingAudioPlayBtn")?.addEventListener("click", async () => {
    const player = document.getElementById("liveEndingAudioPlayer");
    if (!player?.src) {
      toast("未配置直播结束语音频");
      return;
    }
    try {
      await player.play();
    } catch {
      toast("播放直播结束语失败，请重试");
    }
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
  document.getElementById("liveReaderInstallBtn")?.addEventListener("click", async () => {
    await installStandaloneApp();
  });
  const player = document.getElementById("liveReaderAudioPlayer");
  player?.addEventListener("timeupdate", scrollReaderByProgress);
  player?.addEventListener("play", () => {
    setStatus("播放中");
    updateSegmentHighlight(true);
  });
  player?.addEventListener("pause", () => setStatus("已暂停"));
  player?.addEventListener("ended", async () => {
    updateSegmentHighlight(true);
    setStatus("播放结束");
    if (document.getElementById("liveReaderAutoNext")?.checked) {
      await playAdjacentChapter(1);
    }
  });
}

async function init() {
  renderNav();
  if ("serviceWorker" in navigator && (window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost" || window.location.protocol === "https:")) {
    navigator.serviceWorker.register("./service-worker.js").catch(() => {
      // ignore
    });
  }
  applyReaderSettings();
  updateInstallButtonVisibility();
  const data = await getData();
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
