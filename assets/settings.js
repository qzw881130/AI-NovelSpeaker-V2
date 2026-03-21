import { getData, saveSettings } from "./store.js";
import { renderNav, showPageError, toast } from "./ui.js";
import { getLanguage, localizeDocumentText, t, translateText } from "./i18n.js";

const providerDefaults = {
  grok: { baseUrl: "https://api.x.ai/v1", model: "grok-2-latest" },
  deepseek: { baseUrl: "https://api.deepseek.com", model: "deepseek-chat" },
  qwen: { baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  gemini: { baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai", model: "gemini-2.0-flash" },
  openai: { baseUrl: "https://api.openai.com/v1", model: "gpt-4.1-mini" },
  ollama: { baseUrl: "http://127.0.0.1:11434/v1", model: "qwen2.5:7b" },
  custom: { baseUrl: "", model: "" },
};

const PROVIDER_MAX_TOKENS = {
  deepseek: 8192,
};

const BATCH_CHAR_OPTIONS = new Set([0, 3500, 4000, 5000, 6000, 7000]);
const NUM_CTX_OPTIONS = new Set([32768, 65536, 98304, 131072]);
const KEEP_ALIVE_OPTIONS = new Set(["5m", "15m", "30m", "1h", "6h", "24h"]);
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

const TZ_LABELS = {
  "Asia/Shanghai": {
    "zh-CN": "北京时间 (Asia/Shanghai)",
    "zh-TW": "北京時間 (Asia/Shanghai)",
    "en-US": "Beijing Time (Asia/Shanghai)",
    "ja-JP": "北京時間 (Asia/Shanghai)",
    "ko-KR": "베이징 시간 (Asia/Shanghai)",
  },
  "Asia/Hong_Kong": {
    "zh-CN": "香港时间 (Asia/Hong_Kong)",
    "zh-TW": "香港時間 (Asia/Hong_Kong)",
    "en-US": "Hong Kong Time (Asia/Hong_Kong)",
    "ja-JP": "香港時間 (Asia/Hong_Kong)",
    "ko-KR": "홍콩 시간 (Asia/Hong_Kong)",
  },
  "Asia/Tokyo": {
    "zh-CN": "东京时间 (Asia/Tokyo)",
    "zh-TW": "東京時間 (Asia/Tokyo)",
    "en-US": "Tokyo Time (Asia/Tokyo)",
    "ja-JP": "東京時間 (Asia/Tokyo)",
    "ko-KR": "도쿄 시간 (Asia/Tokyo)",
  },
  "Asia/Seoul": {
    "zh-CN": "首尔时间 (Asia/Seoul)",
    "zh-TW": "首爾時間 (Asia/Seoul)",
    "en-US": "Seoul Time (Asia/Seoul)",
    "ja-JP": "ソウル時間 (Asia/Seoul)",
    "ko-KR": "서울 시간 (Asia/Seoul)",
  },
  "America/New_York": {
    "zh-CN": "纽约时间 (America/New_York)",
    "zh-TW": "紐約時間 (America/New_York)",
    "en-US": "New York Time (America/New_York)",
    "ja-JP": "ニューヨーク時間 (America/New_York)",
    "ko-KR": "뉴욕 시간 (America/New_York)",
  },
  "America/Los_Angeles": {
    "zh-CN": "洛杉矶时间 (America/Los_Angeles)",
    "zh-TW": "洛杉磯時間 (America/Los_Angeles)",
    "en-US": "Los Angeles Time (America/Los_Angeles)",
    "ja-JP": "ロサンゼルス時間 (America/Los_Angeles)",
    "ko-KR": "로스앤젤레스 시간 (America/Los_Angeles)",
  },
  "Europe/London": {
    "zh-CN": "伦敦时间 (Europe/London)",
    "zh-TW": "倫敦時間 (Europe/London)",
    "en-US": "London Time (Europe/London)",
    "ja-JP": "ロンドン時間 (Europe/London)",
    "ko-KR": "런던 시간 (Europe/London)",
  },
  "Europe/Paris": {
    "zh-CN": "巴黎时间 (Europe/Paris)",
    "zh-TW": "巴黎時間 (Europe/Paris)",
    "en-US": "Paris Time (Europe/Paris)",
    "ja-JP": "パリ時間 (Europe/Paris)",
    "ko-KR": "파리 시간 (Europe/Paris)",
  },
  "Australia/Sydney": {
    "zh-CN": "悉尼时间 (Australia/Sydney)",
    "zh-TW": "雪梨時間 (Australia/Sydney)",
    "en-US": "Sydney Time (Australia/Sydney)",
    "ja-JP": "シドニー時間 (Australia/Sydney)",
    "ko-KR": "시드니 시간 (Australia/Sydney)",
  },
  UTC: {
    "zh-CN": "UTC (协调世界时)",
    "zh-TW": "UTC (協調世界時)",
    "en-US": "UTC (Coordinated Universal Time)",
    "ja-JP": "UTC (協定世界時)",
    "ko-KR": "UTC (협정 세계시)",
  },
};

function applyTimezoneLabels() {
  const lang = getLanguage();
  const select = document.getElementById("uiTimezone");
  if (!select) return;
  Array.from(select.options).forEach((opt) => {
    const table = TZ_LABELS[String(opt.value)];
    if (!table) return;
    opt.textContent = table[lang] || table["zh-CN"] || opt.textContent;
  });
}

function friendlyErrorText(raw, type) {
  const msg = String(raw || "");
  const lower = msg.toLowerCase();

  if (!msg) return translateText("连接失败，请检查配置后重试");
  if (lower.includes("connection refused") || lower.includes("errno 61")) {
    return translateText(type === "comfy"
      ? "连接被拒绝，请确认 ComfyUI 已启动且地址正确"
      : "连接被拒绝，请确认模型服务地址可访问");
  }
  if (lower.includes("timed out") || lower.includes("timeout")) {
    return translateText("请求超时，请检查网络或代理设置");
  }
  if (lower.includes("name or service not known") || lower.includes("nodename nor servname")) {
    return translateText("域名或地址无法解析，请检查 API Base URL");
  }
  if (lower.includes("api key") && lower.includes("不能为空")) {
    return translateText("请先填写 API Key 再测试");
  }
  if (lower.includes("认证失败") || lower.includes("http 401") || lower.includes("http 403")) {
    return translateText("认证失败，请检查 API Key 是否正确");
  }
  if (lower.includes("http 404")) {
    return translateText("接口地址不可用，请检查 API Base URL");
  }
  if (lower.includes("http 429") || lower.includes("频率") || lower.includes("额度")) {
    return translateText("请求过于频繁或额度不足，请稍后重试");
  }
  if (lower.includes("http 5")) {
    return translateText("服务暂时不可用，请稍后重试");
  }
  if (type === "comfy" && lower.includes("system_stats")) {
    return translateText("连接成功，ComfyUI 接口可访问");
  }
  if (type === "llm" && lower.includes("模型接口可调用")) {
    return translateText("连接成功，模型可正常调用");
  }
  return translateText(msg.length > 60 ? `${msg.slice(0, 60)}...` : msg);
}

function load(settings) {
  const llm = settings.llm || {};
  const ui = settings.ui || {};
  const lineAudioQueue = settings.lineAudioQueue || {};
  document.getElementById("comfyUrl").value = settings.comfyUrl || "";
  document.getElementById("proxyUrl").value = settings.proxyUrl || "";
  document.getElementById("llmProvider").value = llm.provider || "grok";
  document.getElementById("llmBase").value = llm.baseUrl || "";
  document.getElementById("llmModel").value = llm.model || "";
  document.getElementById("llmKey").value = llm.apiKey || "";
  document.getElementById("llmTemperature").value = llm.temperature ?? 0.3;
  document.getElementById("llmTokens").value = normalizeProviderMaxTokens(
    llm.provider || "grok",
    llm.maxTokens ?? 8192
  );
  const numCtx = Number(llm.numCtx ?? 65536);
  document.getElementById("llmNumCtx").value = NUM_CTX_OPTIONS.has(numCtx)
    ? String(numCtx)
    : "65536";
  const keepAlive = String(llm.keepAlive || "30m").trim();
  document.getElementById("llmKeepAlive").value = KEEP_ALIVE_OPTIONS.has(keepAlive)
    ? keepAlive
    : "30m";
  const batchChars = Number(llm.batchMaxChars ?? 3500);
  document.getElementById("llmBatchChars").value = BATCH_CHAR_OPTIONS.has(batchChars)
    ? String(batchChars)
    : "3500";
  document.getElementById("uiLanguage").value = UI_LANGUAGE_OPTIONS.has(String(ui.language || ""))
    ? String(ui.language)
    : "zh-CN";
  document.getElementById("uiTimezone").value = UI_TIMEZONE_OPTIONS.has(String(ui.timezone || ""))
    ? String(ui.timezone)
    : "Asia/Shanghai";
  document.getElementById("lineAudioQueueMode").value = String(lineAudioQueue.mode || "immediate") === "scheduled"
    ? "scheduled"
    : "immediate";
  document.getElementById("lineAudioQueueScheduledAt").value = toLocalDateTimeValue(lineAudioQueue.scheduledAt || "");
  syncLineAudioQueueModeVisibility();
  syncOllamaFieldVisibility();
}

function toLocalDateTimeValue(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  const dt = new Date(text);
  if (Number.isNaN(dt.getTime())) return "";
  const y = dt.getFullYear();
  const m = String(dt.getMonth() + 1).padStart(2, "0");
  const d = String(dt.getDate()).padStart(2, "0");
  const hh = String(dt.getHours()).padStart(2, "0");
  const mm = String(dt.getMinutes()).padStart(2, "0");
  return `${y}-${m}-${d}T${hh}:${mm}`;
}

function toUtcIso(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  const dt = new Date(text);
  return Number.isNaN(dt.getTime()) ? "" : dt.toISOString();
}

function syncLineAudioQueueModeVisibility() {
  const mode = document.getElementById("lineAudioQueueMode")?.value || "immediate";
  const wrap = document.getElementById("lineAudioQueueTimeWrap");
  if (!wrap) return;
  wrap.classList.toggle("hidden", mode !== "scheduled");
}

function syncOllamaFieldVisibility() {
  const provider = String(document.getElementById("llmProvider")?.value || "").trim();
  const isOllama = provider === "ollama";
  document.getElementById("llmNumCtxWrap")?.classList.toggle("hidden", !isOllama);
  document.getElementById("llmKeepAliveWrap")?.classList.toggle("hidden", !isOllama);
  const keyInput = document.getElementById("llmKey");
  const keyHint = document.getElementById("llmKeyHint");
  if (keyInput) {
    keyInput.disabled = isOllama;
    keyInput.placeholder = isOllama ? "本地 Ollama 可留空" : "";
  }
  keyHint?.classList.toggle("hidden", !isOllama);
}

function normalizeProviderMaxTokens(provider, value) {
  const raw = Number(value ?? 8192);
  const next = Number.isFinite(raw) && raw > 0 ? Math.round(raw) : 8192;
  const limit = PROVIDER_MAX_TOKENS[String(provider || "").trim()];
  return limit ? Math.min(next, limit) : next;
}

function readSettingsForm() {
  return {
    comfyUrl: document.getElementById("comfyUrl").value.trim(),
    proxyUrl: document.getElementById("proxyUrl").value.trim(),
    llm: {
      provider: document.getElementById("llmProvider").value,
      baseUrl: document.getElementById("llmBase").value.trim(),
      model: document.getElementById("llmModel").value.trim(),
      apiKey: document.getElementById("llmKey").value.trim(),
      temperature: Number(document.getElementById("llmTemperature").value),
      maxTokens: normalizeProviderMaxTokens(
        document.getElementById("llmProvider").value,
        document.getElementById("llmTokens").value
      ),
      numCtx: Number(document.getElementById("llmNumCtx").value || 65536),
      keepAlive: String(document.getElementById("llmKeepAlive").value || "30m"),
      batchMaxChars: Number(document.getElementById("llmBatchChars").value ?? 3500),
    },
    ui: {
      language: document.getElementById("uiLanguage").value,
      timezone: document.getElementById("uiTimezone").value,
    },
    lineAudioQueue: {
      mode: document.getElementById("lineAudioQueueMode").value,
      scheduledAt: document.getElementById("lineAudioQueueMode").value === "scheduled"
        ? toUtcIso(document.getElementById("lineAudioQueueScheduledAt").value)
        : "",
    },
  };
}

function setTestResult(id, status, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove("ok", "fail");
  if (status === "ok") el.classList.add("ok");
  if (status === "fail") el.classList.add("fail");
  el.textContent = translateText(text);
}

async function testComfy() {
  setTestResult("testComfyResult", "", "测试中...");
  const payload = readSettingsForm();
  try {
    const res = await fetch("/api/settings/test-comfy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    setTestResult("testComfyResult", "ok", `可用 · ${friendlyErrorText(data.message || "连接成功", "comfy")}`);
    localizeDocumentText(document);
    toast(t("toast.saved"));
  } catch (err) {
    const text = friendlyErrorText(err.message, "comfy");
    setTestResult("testComfyResult", "fail", `失败 · ${text}`);
    localizeDocumentText(document);
    toast(t("error.operationFailed", { msg: text }));
  }
}

async function testLlm() {
  setTestResult("testLlmResult", "", "测试中...");
  const payload = readSettingsForm();
  try {
    const res = await fetch("/api/settings/test-llm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    setTestResult("testLlmResult", "ok", `可用 · ${friendlyErrorText(data.message || "调用成功", "llm")}`);
    localizeDocumentText(document);
    toast(t("toast.saved"));
  } catch (err) {
    const text = friendlyErrorText(err.message, "llm");
    setTestResult("testLlmResult", "fail", `失败 · ${text}`);
    localizeDocumentText(document);
    toast(t("error.operationFailed", { msg: text }));
  }
}

function markComfyDirty() {
  setTestResult("testComfyResult", "", "未测试");
  localizeDocumentText(document);
}

function markLlmDirty() {
  setTestResult("testLlmResult", "", "未测试");
  localizeDocumentText(document);
}

function bindEvents() {
  document.getElementById("llmProvider").addEventListener("change", (event) => {
    const next = providerDefaults[event.target.value];
    document.getElementById("llmBase").value = next.baseUrl;
    document.getElementById("llmModel").value = next.model;
    document.getElementById("llmTokens").value = normalizeProviderMaxTokens(
      event.target.value,
      document.getElementById("llmTokens").value
    );
    syncOllamaFieldVisibility();
    markLlmDirty();
  });

  document.getElementById("comfyUrl").addEventListener("input", markComfyDirty);
  document.getElementById("proxyUrl").addEventListener("input", markLlmDirty);
  document.getElementById("llmBase").addEventListener("input", markLlmDirty);
  document.getElementById("llmModel").addEventListener("input", markLlmDirty);
  document.getElementById("llmKey").addEventListener("input", markLlmDirty);
  document.getElementById("llmTemperature").addEventListener("input", markLlmDirty);
  document.getElementById("llmTokens").addEventListener("input", markLlmDirty);
  document.getElementById("llmNumCtx").addEventListener("change", markLlmDirty);
  document.getElementById("llmKeepAlive").addEventListener("change", markLlmDirty);
  document.getElementById("llmBatchChars").addEventListener("change", markLlmDirty);
  document.getElementById("uiLanguage").addEventListener("change", markLlmDirty);
  document.getElementById("uiTimezone").addEventListener("change", markLlmDirty);
  document.getElementById("lineAudioQueueMode").addEventListener("change", () => {
    syncLineAudioQueueModeVisibility();
    markLlmDirty();
  });
  document.getElementById("lineAudioQueueScheduledAt").addEventListener("change", markLlmDirty);

  document.getElementById("saveSettingsBtn").addEventListener("click", async () => {
    const payload = readSettingsForm();
    if (payload.lineAudioQueue.mode === "scheduled" && !payload.lineAudioQueue.scheduledAt) {
      toast("请先填写有效的台词队列执行时间");
      return;
    }
    await saveSettings(payload);
    toast(t("toast.saved"));
    localizeDocumentText(document);
    applyTimezoneLabels();
    renderNav();
  });

  document.getElementById("testComfyBtn").addEventListener("click", () => {
    testComfy();
  });

  document.getElementById("testLlmBtn").addEventListener("click", () => {
    testLlm();
  });
}

async function init() {
  renderNav();
  bindEvents();
  const data = await getData();
  load(data.settings || {});
  localizeDocumentText(document);
  applyTimezoneLabels();
}

init().catch((err) => {
  renderNav();
  showPageError(err, t("error.pageLoad"));
});
