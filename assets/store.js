import { translateBackendError } from "./i18n.js";

const ACTIVE_KEY = "ai_novel_speaker_v1_active_novel";

const DEFAULT_SETTINGS = {
  comfyUrl: "http://127.0.0.1:8188",
  proxyUrl: "",
  llm: {
    provider: "grok",
    baseUrl: "",
    model: "",
    apiKey: "",
    temperature: 0.3,
    maxTokens: 8192,
    numCtx: 65536,
    keepAlive: "30m",
    batchMaxChars: 3500,
  },
  ui: {
    language: "zh-CN",
    timezone: "Asia/Shanghai",
  },
  lineAudioQueue: {
    mode: "immediate",
    scheduledAt: "",
  },
};

let cache = {
  novels: [],
  prompts: [],
  workflows: [],
  jsonTasks: [],
  settings: DEFAULT_SETTINGS,
};

function normalizeSettings(raw) {
  const next = raw || {};
  return {
    comfyUrl: String(next.comfyUrl || DEFAULT_SETTINGS.comfyUrl),
    proxyUrl: String(next.proxyUrl || ""),
    llm: {
      ...DEFAULT_SETTINGS.llm,
      ...(next.llm || {}),
    },
    ui: {
      ...DEFAULT_SETTINGS.ui,
      ...(next.ui || {}),
    },
    lineAudioQueue: {
      ...DEFAULT_SETTINGS.lineAudioQueue,
      ...(next.lineAudioQueue || {}),
    },
  };
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let errorText = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      errorText = translateBackendError(data.error || errorText);
    } catch {
      // ignore
    }
    throw new Error(errorText);
  }
  const contentType = res.headers.get("Content-Type") || "";
  if (contentType.includes("application/json")) {
    return res.json();
  }
  return res.text();
}

function bytesToText(bytes) {
  const num = Number(bytes || 0);
  if (num < 1024 * 1024) return `${(num / 1024).toFixed(1)} KB`;
  if (num < 1024 * 1024 * 1024) return `${(num / (1024 * 1024)).toFixed(1)} MB`;
  return `${(num / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function normalizeData(raw) {
  return {
    novels: raw.novels || [],
    prompts: raw.prompts || [],
    workflows: raw.workflows || [],
    jsonTasks: raw.jsonTasks || [],
    settings: normalizeSettings(raw.settings || cache.settings),
  };
}

async function refreshCache() {
  const data = await api("/api/bootstrap");
  cache = normalizeData(data);
  localStorage.setItem("ai_novel_ui_language", String(cache.settings?.ui?.language || "zh-CN"));
  return cache;
}

async function getData() {
  return refreshCache();
}

function getCachedData() {
  return cache;
}

function getActiveNovelId() {
  return localStorage.getItem(ACTIVE_KEY) || "";
}

function setActiveNovelId(id) {
  localStorage.setItem(ACTIVE_KEY, String(id || ""));
}

async function saveNovel(input, id) {
  const payload = {
    name: String(input.name || "").trim(),
    author: String(input.author || "").trim(),
    englishDir: String(input.englishDir || "").trim(),
    intro: String(input.intro || "").trim(),
    promptId: input.promptId ? Number(input.promptId) : null,
    voiceSampleWorkflowId: input.voiceSampleWorkflowId ? Number(input.voiceSampleWorkflowId) : null,
    lineAudioWorkflowId: input.lineAudioWorkflowId ? Number(input.lineAudioWorkflowId) : null,
    voiceTranscribeWorkflowId: input.voiceTranscribeWorkflowId ? Number(input.voiceTranscribeWorkflowId) : null,
  };
  if (id) {
    await api(`/api/novels/${Number(id)}`, { method: "PUT", body: JSON.stringify(payload) });
  } else {
    await api("/api/novels", { method: "POST", body: JSON.stringify(payload) });
  }
  return refreshCache();
}

async function deleteNovel(id) {
  await api(`/api/novels/${Number(id)}`, { method: "DELETE" });
  await refreshCache();
  const active = getActiveNovelId();
  if (String(active) === String(id)) {
    const next = cache.novels[0]?.id || "";
    setActiveNovelId(next);
  }
}

async function refreshNovelAudioDuration(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/audio-duration`);
  const target = cache.novels.find((item) => String(item.id) === String(novelId));
  if (target) {
    target.totalAudioDurationSeconds = Number(data.totalAudioDurationSeconds || 0);
  }
  return Number(data.totalAudioDurationSeconds || 0);
}

async function downloadNovelBundle(novelId) {
  const a = document.createElement("a");
  a.href = `/api/novels/${Number(novelId)}/bundle`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function listNovelBundles(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/bundles`);
  return data.bundles || [];
}

async function createNovelBundle(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/bundles`, {
    method: "POST",
    body: "{}",
  });
  return data.bundle || null;
}

async function downloadNovelBundleFile(novelId, fileName) {
  const a = document.createElement("a");
  a.href = `/api/novels/${Number(novelId)}/bundles/${encodeURIComponent(String(fileName || ""))}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function deleteNovelBundleFile(novelId, fileName) {
  await api(`/api/novels/${Number(novelId)}/bundles/${encodeURIComponent(String(fileName || ""))}`, {
    method: "DELETE",
  });
}

async function listRoleVoiceBundles(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/role-voice-bundles`);
  return data.bundles || [];
}

async function createRoleVoiceBundle(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/role-voice-bundles`, {
    method: "POST",
    body: "{}",
  });
  return data.bundle || null;
}

async function downloadRoleVoiceBundleFile(novelId, fileName) {
  const a = document.createElement("a");
  a.href = `/api/novels/${Number(novelId)}/role-voice-bundles/${encodeURIComponent(String(fileName || ""))}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function createJsonTask(input) {
  await api("/api/json-tasks", {
    method: "POST",
    body: JSON.stringify({
      novelId: Number(input.novelId),
      chapter: Number(input.chapter),
      title: String(input.title || "").trim(),
    }),
  });
  return refreshCache();
}

async function retryJsonTask(taskId) {
  await api(`/api/json-tasks/${Number(taskId)}/retry`, {
    method: "POST",
    body: "{}",
  });
  return refreshCache();
}

async function retryJsonTaskBatch(taskId, batchIndex) {
  await api(`/api/json-tasks/${Number(taskId)}/batches/${Number(batchIndex)}/retry`, {
    method: "POST",
    body: "{}",
  });
  return refreshCache();
}

async function deleteJsonTask(taskId) {
  await api(`/api/json-tasks/${Number(taskId)}`, { method: "DELETE" });
  return refreshCache();
}

async function fetchJsonTaskDetail(taskId) {
  return api(`/api/json-tasks/${Number(taskId)}`);
}

async function advanceJsonTasks() {
  await api("/api/json-tasks/simulate", { method: "POST", body: "{}" });
  return refreshCache();
}

async function savePrompt(input, id) {
  const payload = {
    name: String(input.name || "").trim(),
    description: String(input.description || "").trim(),
    content: String(input.content || "").trim(),
  };
  if (id) {
    await api(`/api/prompts/${Number(id)}`, { method: "PUT", body: JSON.stringify(payload) });
  } else {
    await api("/api/prompts", { method: "POST", body: JSON.stringify(payload) });
  }
  return refreshCache();
}

async function duplicatePrompt(id) {
  await api(`/api/prompts/${Number(id)}/duplicate`, { method: "POST", body: "{}" });
  return refreshCache();
}

async function deletePrompt(id) {
  await api(`/api/prompts/${Number(id)}`, { method: "DELETE" });
  return refreshCache();
}

async function saveWorkflow(input, id) {
  const payload = {
    name: String(input.name || "").trim(),
    workflowType: String(input.workflowType || "").trim(),
    description: String(input.description || "").trim(),
    jsonText: String(input.jsonText || "").trim(),
    workflowIoConfig: input.workflowIoConfig || {},
    workflowLogEnabled: input.workflowLogEnabled !== false,
  };
  if (id) {
    await api(`/api/workflows/${Number(id)}`, { method: "PUT", body: JSON.stringify(payload) });
  } else {
    await api("/api/workflows", { method: "POST", body: JSON.stringify(payload) });
  }
  return refreshCache();
}

async function deleteWorkflow(id) {
  await api(`/api/workflows/${Number(id)}`, { method: "DELETE" });
  return refreshCache();
}

async function duplicateWorkflow(id) {
  await api(`/api/workflows/${Number(id)}/duplicate`, { method: "POST", body: "{}" });
  return refreshCache();
}

async function fetchWorkflowLogs() {
  const data = await api("/api/workflow-logs");
  return data.logs || [];
}

async function clearWorkflowLogs() {
  await api("/api/workflow-logs", { method: "DELETE" });
}

async function saveSettings(nextSettings) {
  await api("/api/settings", { method: "PUT", body: JSON.stringify(nextSettings) });
  return refreshCache();
}

async function fetchNovelChapters(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/chapters`);
  return data.chapters || [];
}

async function fetchNovelDownloadChapters(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/download-chapters`);
  return data.chapters || [];
}

async function fetchChapterDetail(novelId, chapterNum) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}`);
}

async function fetchChapterJsonOutput(novelId, chapterNum) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/json-output`);
}

async function fetchChapterCompareData(novelId, chapterNum) {
  const [detail, jsonOutput] = await Promise.all([
    fetchChapterDetail(novelId, chapterNum),
    fetchChapterJsonOutput(novelId, chapterNum),
  ]);
  return { detail, jsonOutput };
}

async function requestConvertJson(novelId, chapterNum) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/convert-json`, {
    method: "POST",
    body: "{}",
  });
}

async function saveChapterJsonOutput(novelId, chapterNum, jsonText) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/json-output`, {
    method: "PUT",
    body: JSON.stringify({ jsonText: String(jsonText || "") }),
  });
}

async function importNovelTextChapters(novelId) {
  return api(`/api/novels/${Number(novelId)}/import-text-chapters`, {
    method: "POST",
    body: "{}",
  });
}

async function createChapter(novelId, input) {
  return api(`/api/novels/${Number(novelId)}/chapters`, {
    method: "POST",
    body: JSON.stringify({
      chapterNum: Number(input.chapterNum),
      title: String(input.title || "").trim(),
      content: String(input.content || ""),
    }),
  });
}

async function updateChapter(novelId, chapterNum, input) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}`, {
    method: "PUT",
    body: JSON.stringify({
      chapterNum: Number(input.chapterNum),
      title: String(input.title || "").trim(),
      content: String(input.content || ""),
    }),
  });
}

async function deleteChapter(novelId, chapterNum) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}`, {
    method: "DELETE",
  });
}

async function downloadChapterAudio(novelId, chapterNum) {
  const res = await fetch(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/audio-file`);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      msg = j.error || msg;
    } catch {
      // ignore
    }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const mUtf = cd.match(/filename\*=UTF-8''([^;]+)/i);
  const m = cd.match(/filename=([^;]+)/i);
  let filename = `chapter-${chapterNum}.audio`;
  if (mUtf) {
    try {
      filename = decodeURIComponent(mUtf[1]);
    } catch {
      filename = mUtf[1];
    }
  } else if (m) {
    filename = m[1].replace(/"/g, "");
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

// 角色库API
async function fetchRoles(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/roles`);
  return data || { stats: {}, roles: [] };
}

async function createRole(novelId, input) {
  const res = await api(`/api/novels/${Number(novelId)}/roles`, {
    method: "POST",
    body: JSON.stringify({
      name: String(input.name || "").trim(),
      instruct: String(input.instruct || "").trim(),
      sampleText: String(input.sampleText || "").trim(),
    }),
  });
  return res;
}

async function updateRole(novelId, roleId, input) {
  const res = await api(`/api/novels/${Number(novelId)}/roles/${Number(roleId)}`, {
    method: "PUT",
    body: JSON.stringify({
      name: String(input.name || "").trim(),
      instruct: String(input.instruct || "").trim(),
      sampleText: String(input.sampleText || "").trim(),
    }),
  });
  return res;
}

async function updateRoleLevel(novelId, roleId, level) {
  const res = await api(`/api/novels/${Number(novelId)}/roles/${Number(roleId)}/level`, {
    method: "POST",
    body: JSON.stringify({ roleLevel: Number(level) }),
  });
  return res;
}

async function duplicateRole(novelId, roleId) {
  const res = await api(`/api/novels/${Number(novelId)}/roles/${Number(roleId)}/duplicate`, {
    method: "POST",
    body: "{}",
  });
  return res;
}

async function deleteRole(novelId, roleId) {
  await api(`/api/novels/${Number(novelId)}/roles/${Number(roleId)}`, { method: "DELETE" });
}

async function uploadRoleSampleAudio(novelId, roleId, audioBase64, source = "uploaded") {
  const res = await api(`/api/novels/${Number(novelId)}/roles/${Number(roleId)}/sample-audio`, {
    method: "POST",
    body: JSON.stringify({ audioBase64, source }),
  });
  return res;
}

async function generateRoleSampleAudio(novelId, roleId) {
  const res = await api(`/api/novels/${Number(novelId)}/roles/${Number(roleId)}/generate-sample`, {
    method: "POST",
    body: "{}",
  });
  return res;
}

async function getRoleSampleAudioUrl(novelId, roleId) {
  return `/api/novels/${Number(novelId)}/roles/${Number(roleId)}/sample`;
}

// 台词音频API
async function fetchChapterLineAudios(novelId, chapterNum) {
  const data = await api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/line-audios`);
  return data.lineAudios || [];
}

async function fetchChapterLineAudioOverview(novelId, chapterNum) {
  const data = await api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/line-audios`);
  return {
    lineAudios: data.lineAudios || [],
    mergedAudioOutdated: Boolean(data.mergedAudioOutdated),
  };
}

async function enqueueLineAudio(novelId, chapterNum, lineIndex, options = {}) {
  const res = await api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/line-audio/enqueue`, {
    method: "POST",
    body: JSON.stringify({
      lineIndex: Number(lineIndex),
      scheduledAt: String(options.scheduledAt || ""),
    }),
  });
  return res;
}

async function enqueueAllLineAudios(novelId, chapterNum, options = {}) {
  const res = await api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/line-audio/enqueue-all`, {
    method: "POST",
    body: JSON.stringify({
      scheduledAt: String(options.scheduledAt || ""),
    }),
  });
  return res;
}

async function mergeChapterLineAudio(novelId, chapterNum) {
  const res = await api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/merge-line-audio`, {
    method: "POST",
    body: "{}",
  });
  return res;
}

async function fetchLineAudioTasks(novelId, options = {}) {
  const limit = Number(options.limit || 100);
  const offset = Number(options.offset || 0);
  const data = await api(`/api/novels/${Number(novelId)}/line-audio-tasks?limit=${limit}&offset=${offset}`);
  return {
    lineAudioTasks: data.lineAudioTasks || [],
    pendingCount: Number(data.pendingCount || 0),
    totalCount: Number(data.totalCount || 0),
    hasMore: Boolean(data.hasMore),
    nextOffset: Number(data.nextOffset || 0),
  };
}

async function fetchLineAudioTaskDetail(taskId) {
  return await api(`/api/line-audio-tasks/${Number(taskId)}`);
}

async function deleteLineAudioTask(taskId) {
  await api(`/api/line-audio-tasks/${Number(taskId)}`, { method: "DELETE" });
}

async function retryLineAudioTask(taskId) {
  await api(`/api/line-audio-tasks/${Number(taskId)}/retry`, { method: "POST", body: "{}" });
}

function getLineAudioFileUrl(taskId) {
  return `/api/line-audio-tasks/${Number(taskId)}/file`;
}

function getMergedAudioUrl(novelId, chapterNum) {
  return `/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/merged-audio`;
}

export {
  advanceJsonTasks,
  bytesToText,
  createJsonTask,
  deleteNovel,
  refreshNovelAudioDuration,
  deletePrompt,
  deleteWorkflow,
  downloadNovelBundle,
  duplicateWorkflow,
  fetchWorkflowLogs,
  duplicatePrompt,
  fetchChapterDetail,
  fetchChapterCompareData,
  fetchChapterJsonOutput,
  fetchJsonTaskDetail,
  fetchNovelChapters,
  fetchNovelDownloadChapters,
  getActiveNovelId,
  getCachedData,
  getData,
  requestConvertJson,
  saveChapterJsonOutput,
  retryJsonTask,
  retryJsonTaskBatch,
  deleteJsonTask,
  importNovelTextChapters,
  downloadChapterAudio,
  createChapter,
  updateChapter,
  deleteChapter,
  saveNovel,
  listNovelBundles,
  createNovelBundle,
  listRoleVoiceBundles,
  createRoleVoiceBundle,
  deleteNovelBundleFile,
  savePrompt,
  saveSettings,
  saveWorkflow,
  setActiveNovelId,
  clearWorkflowLogs,
  // 角色库
  fetchRoles,
  createRole,
  updateRole,
  updateRoleLevel,
  duplicateRole,
  deleteRole,
  uploadRoleSampleAudio,
  generateRoleSampleAudio,
  downloadRoleVoiceBundleFile,
  // 台词音频
  fetchChapterLineAudios,
  fetchChapterLineAudioOverview,
  enqueueLineAudio,
  enqueueAllLineAudios,
  mergeChapterLineAudio,
  fetchLineAudioTasks,
  fetchLineAudioTaskDetail,
  deleteLineAudioTask,
  retryLineAudioTask,
  getLineAudioFileUrl,
  getMergedAudioUrl,
  downloadNovelBundleFile,
};
