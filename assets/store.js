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
    unloadAfterCall: false,
    batchTimeoutMinutes: 15,
    think: true,
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
  copyrightAudio: {
    introEnabled: false,
    introPath: "",
    outroEnabled: false,
    outroPath: "",
  },
  liveEndingAudio: {
    items: [],
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
    copyrightAudio: {
      ...DEFAULT_SETTINGS.copyrightAudio,
      ...(next.copyrightAudio || {}),
    },
    liveEndingAudio: {
      ...DEFAULT_SETTINGS.liveEndingAudio,
      ...(next.liveEndingAudio || {}),
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

function buildBootstrapPath(options = {}) {
  const include = Array.isArray(options.include) ? options.include : [];
  const sections = include
    .map((item) => String(item || "").trim())
    .filter(Boolean);
  return sections.length ? `/api/bootstrap?include=${encodeURIComponent(sections.join(","))}` : "/api/bootstrap";
}

async function refreshCache(options = {}) {
  const data = await api(buildBootstrapPath(options));
  cache = normalizeData({ ...cache, ...data });
  localStorage.setItem("ai_novel_ui_language", String(cache.settings?.ui?.language || "zh-CN"));
  return cache;
}

async function getData(options = {}) {
  return refreshCache(options);
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
    nsfwPromptId: input.nsfwPromptId ? Number(input.nsfwPromptId) : null,
    illustrationScenePromptId: input.illustrationScenePromptId ? Number(input.illustrationScenePromptId) : null,
    illustrationShotPromptId: input.illustrationShotPromptId ? Number(input.illustrationShotPromptId) : null,
    illustrationPromptPromptId: input.illustrationPromptPromptId ? Number(input.illustrationPromptPromptId) : null,
    workflowId: input.workflowId ? Number(input.workflowId) : null,
    voiceSampleWorkflowId: input.voiceSampleWorkflowId ? Number(input.voiceSampleWorkflowId) : null,
    lineAudioWorkflowId: input.lineAudioWorkflowId ? Number(input.lineAudioWorkflowId) : null,
    voiceTranscribeWorkflowId: input.voiceTranscribeWorkflowId ? Number(input.voiceTranscribeWorkflowId) : null,
    audioAsrWorkflowId: input.audioAsrWorkflowId ? Number(input.audioAsrWorkflowId) : null,
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

async function createNovelBundle(novelId, options = {}) {
  const data = await api(`/api/novels/${Number(novelId)}/bundles`, {
    method: "POST",
    body: JSON.stringify({
      audioPreset: String(options.audioPreset || "lossless"),
      audioVariant: String(options.audioVariant || "ver"),
    }),
  });
  return data.task || null;
}

async function fetchNovelBundleStatus(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/bundles/status`);
  return data.task || null;
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

async function cancelJsonTask(taskId) {
  await api(`/api/json-tasks/${Number(taskId)}/cancel`, {
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
    category: String(input.category || "json_parse").trim() || "json_parse",
    description: String(input.description || "").trim(),
    content: String(input.content || "").trim(),
  };
  if (id) {
    await api(`/api/prompts/${Number(id)}`, { method: "PUT", body: JSON.stringify(payload) });
  } else {
    await api("/api/prompts", { method: "POST", body: JSON.stringify(payload) });
  }
  return refreshCache({ include: ["prompts"] });
}

async function duplicatePrompt(id) {
  await api(`/api/prompts/${Number(id)}/duplicate`, { method: "POST", body: "{}" });
  return refreshCache({ include: ["prompts"] });
}

async function deletePrompt(id) {
  await api(`/api/prompts/${Number(id)}`, { method: "DELETE" });
  return refreshCache({ include: ["prompts"] });
}

async function savePromptSettings(id, settings) {
  await api(`/api/prompts/${Number(id)}/settings`, {
    method: "PUT",
    body: JSON.stringify(settings || { enabled: false }),
  });
  return refreshCache({ include: ["prompts"] });
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
  return refreshCache({ include: ["workflows"] });
}

async function deleteWorkflow(id) {
  await api(`/api/workflows/${Number(id)}`, { method: "DELETE" });
  return refreshCache({ include: ["workflows"] });
}

async function duplicateWorkflow(id) {
  await api(`/api/workflows/${Number(id)}/duplicate`, { method: "POST", body: "{}" });
  return refreshCache();
}

async function fetchWorkflowLogs() {
  const data = await api("/api/workflow-logs");
  return data.logs || [];
}

async function fetchTaskWorkerStatus() {
  return api("/api/task-worker/status");
}

async function restartTaskWorker() {
  return api("/api/task-worker/restart", {
    method: "POST",
    body: "{}",
  });
}

async function fetchLineAudioWorkerStatus() {
  return api("/api/line-audio-worker/status");
}

async function restartLineAudioWorker() {
  return api("/api/line-audio-worker/restart", {
    method: "POST",
    body: "{}",
  });
}

async function fetchAudioAsrWorkerStatus() {
  return api("/api/audio-asr-worker/status");
}

async function restartAudioAsrWorker() {
  return api("/api/audio-asr-worker/restart", {
    method: "POST",
    body: "{}",
  });
}

async function fetchNsfwReviewWorkerStatus() {
  return api("/api/nsfw-review-worker/status");
}

async function restartNsfwReviewWorker() {
  return api("/api/nsfw-review-worker/restart", {
    method: "POST",
    body: "{}",
  });
}

async function fetchIllustrationWorkerStatus() {
  return api("/api/illustration-worker/status");
}

async function fetchIllustrationLlmWorkerStatus() {
  return api("/api/illustration-llm-worker/status");
}

async function fetchIllustrationImageWorkerStatus() {
  return api("/api/illustration-image-worker/status");
}

async function restartIllustrationWorker() {
  return api("/api/illustration-worker/restart", {
    method: "POST",
    body: "{}",
  });
}

async function restartIllustrationLlmWorker() {
  return api("/api/illustration-llm-worker/restart", {
    method: "POST",
    body: "{}",
  });
}

async function restartIllustrationImageWorker() {
  return api("/api/illustration-image-worker/restart", {
    method: "POST",
    body: "{}",
  });
}

async function cancelPendingIllustrationTasks(novelId) {
  return api(`/api/novels/${Number(novelId)}/illustration/cancel-pending-tasks`, {
    method: "POST",
    body: "{}",
  });
}

async function cancelPendingIllustrationImages(novelId) {
  return api(`/api/novels/${Number(novelId)}/illustration/cancel-pending-images`, {
    method: "POST",
    body: "{}",
  });
}

async function searchNovelText(novelId, searchText) {
  return api(`/api/novels/${Number(novelId)}/text-fix/search`, {
    method: "POST",
    body: JSON.stringify({ searchText: String(searchText || "") }),
  });
}

async function replaceNovelText(novelId, searchText, replaceText) {
  return api(`/api/novels/${Number(novelId)}/text-fix/replace`, {
    method: "POST",
    body: JSON.stringify({
      searchText: String(searchText || ""),
      replaceText: String(replaceText || ""),
    }),
  });
}

async function clearWorkflowLogs() {
  await api("/api/workflow-logs", { method: "DELETE" });
}

async function saveSettings(nextSettings) {
  await api("/api/settings", { method: "PUT", body: JSON.stringify(nextSettings) });
  return refreshCache({ include: ["settings"] });
}

async function fetchNovelChapters(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/chapters`);
  return data.chapters || [];
}

async function fetchNovelDownloadChapters(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/download-chapters`);
  return data.chapters || [];
}

async function fetchNovelAudioAsrChapters(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/audio-asr-chapters`);
  return data.chapters || [];
}

async function fetchNovelNsfwReviewChapters(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/nsfw-review-chapters`);
  return data.chapters || [];
}

async function fetchNovelIllustrationChapters(novelId) {
  const data = await api(`/api/novels/${Number(novelId)}/illustration-chapters`);
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

async function enqueueChapterAudioAsr(novelId, chapterNum, options = {}) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/audio-asr/enqueue`, {
    method: "POST",
    body: JSON.stringify({ forceExtract: Boolean(options.forceExtract) }),
  });
}

async function cancelChapterAudioAsr(novelId, chapterNum) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/audio-asr/cancel`, {
    method: "POST",
    body: "{}",
  });
}

async function enqueueBatchAudioAsr(novelId, chapterNums = [], options = {}) {
  return api(`/api/novels/${Number(novelId)}/audio-asr/enqueue-batch`, {
    method: "POST",
    body: JSON.stringify({ chapterNums, forceExtract: Boolean(options.forceExtract) }),
  });
}

async function enqueueChapterNsfwReview(novelId, chapterNum) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/nsfw-review/enqueue`, {
    method: "POST",
    body: "{}",
  });
}

async function enqueueBatchNsfwReview(novelId, chapterNums = []) {
  return api(`/api/novels/${Number(novelId)}/nsfw-review/enqueue-batch`, {
    method: "POST",
    body: JSON.stringify({ chapterNums }),
  });
}

async function enqueueChapterIllustration(novelId, chapterNum, stage, options = {}) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/illustration/${String(stage)}/enqueue`, {
    method: "POST",
    body: JSON.stringify({ allowWaiting: Boolean(options.allowWaiting) }),
  });
}

async function fetchChapterIllustrationPayload(novelId, chapterNum, stage, kind) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/illustration/${String(stage)}/${String(kind)}`, {
    method: "POST",
    body: "{}",
  });
}

async function fetchChapterIllustrationLlmParams(novelId, chapterNum, stage) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/illustration/${String(stage)}/llm-params`, {
    method: "POST",
    body: "{}",
  });
}

async function fetchChapterIllustrationPromptBatches(novelId, chapterNum) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/illustration/prompt/batches`, {
    method: "POST",
    body: "{}",
  });
}

async function retryChapterIllustrationPromptBatch(novelId, chapterNum, batchIndex) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/illustration/prompt/batches/${Number(batchIndex)}/retry`, {
    method: "POST",
    body: "{}",
  });
}

async function saveChapterIllustrationPromptOutput(novelId, chapterNum, jsonText) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/illustration/prompt/output/save`, {
    method: "POST",
    body: JSON.stringify({ jsonText }),
  });
}

async function fetchChapterIllustrationImages(novelId, chapterNum) {
  const data = await api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/illustration/images`, {
    method: "POST",
    body: "{}",
  });
  return data.images || [];
}

async function enqueueIllustrationImage(imageId) {
  return api(`/api/illustration-images/${Number(imageId)}/enqueue`, {
    method: "POST",
    body: "{}",
  });
}

async function enqueueAllIllustrationImages(novelId, chapterNum) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/illustration/images/enqueue-all`, {
    method: "POST",
    body: "{}",
  });
}

async function fetchChapterAsrFile(novelId, chapterNum) {
  return api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/asr-file`);
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

async function createRoleAlias(novelId, roleId, aliasName) {
  const res = await api(`/api/novels/${Number(novelId)}/roles/${Number(roleId)}/alias`, {
    method: "POST",
    body: JSON.stringify({ aliasName: String(aliasName || "").trim() }),
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

async function fetchRoleLineAudios(novelId, roleName, options = {}) {
  const page = Math.max(1, Number(options.page || 1));
  const pageSize = Math.max(1, Number(options.pageSize || 50));
  const params = new URLSearchParams({
    roleName: String(roleName || ""),
    page: String(page),
    pageSize: String(pageSize),
  });
  if (options.chapterNum) {
    params.set("chapterNum", String(Number(options.chapterNum)));
  }
  const data = await api(`/api/novels/${Number(novelId)}/role-line-audios?${params.toString()}`);
  return {
    items: data.items || [],
    totalCount: Number(data.totalCount || 0),
    page: Number(data.page || 1),
    pageSize: Number(data.pageSize || pageSize),
    pageCount: Number(data.pageCount || 0),
  };
}

async function fetchRoleLineCounts(novelId, options = {}) {
  const params = new URLSearchParams();
  if (options.chapterNum) {
    params.set("chapterNum", String(Number(options.chapterNum)));
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const data = await api(`/api/novels/${Number(novelId)}/role-line-counts${suffix}`);
  return data.counts || {};
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

async function mergeChapterLineAudio(novelId, chapterNum, options = {}) {
  const res = await api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/merge-line-audio`, {
    method: "POST",
    body: JSON.stringify({ variant: String(options.variant || "ver") }),
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

async function fetchVideoExportTasks(novelId = "") {
  const suffix = novelId ? `?novelId=${encodeURIComponent(String(novelId))}` : "";
  const data = await api(`/api/video-export-tasks${suffix}`);
  return data.tasks || [];
}

async function fetchVideoExportWorkerStatus() {
  return await api("/api/video-export-worker/status");
}

async function restartVideoExportWorker() {
  await api("/api/video-export-worker/restart", { method: "POST", body: "{}" });
}

async function enqueueChapterVideoExport(novelId, chapterNum, options = {}) {
  return await api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/video-export/enqueue`, {
    method: "POST",
    body: JSON.stringify({
      width: Number(options.width || 1080),
      height: Number(options.height || 1920),
      fps: Number(options.fps || 30),
    }),
  });
}

async function fetchChapterVideoExportStatus(novelId, chapterNum) {
  const data = await api(`/api/novels/${Number(novelId)}/chapters/${Number(chapterNum)}/video-export/status`);
  return data.task || null;
}

async function retryVideoExportTask(taskId) {
  await api(`/api/video-export-tasks/${Number(taskId)}/retry`, { method: "POST", body: "{}" });
}

async function cancelVideoExportTask(taskId) {
  await api(`/api/video-export-tasks/${Number(taskId)}/cancel`, { method: "POST", body: "{}" });
}

function getVideoExportFileUrl(taskId) {
  return `/api/video-export-tasks/${Number(taskId)}/file`;
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
  fetchAudioAsrWorkerStatus,
  fetchLineAudioWorkerStatus,
  fetchNsfwReviewWorkerStatus,
  fetchWorkflowLogs,
  fetchTaskWorkerStatus,
  searchNovelText,
  replaceNovelText,
  duplicatePrompt,
  fetchChapterDetail,
  fetchChapterCompareData,
  fetchChapterJsonOutput,
  fetchJsonTaskDetail,
  fetchNovelChapters,
  fetchNovelDownloadChapters,
  fetchNovelAudioAsrChapters,
  fetchNovelNsfwReviewChapters,
  fetchNovelIllustrationChapters,
  getActiveNovelId,
  getCachedData,
  getData,
  requestConvertJson,
  saveChapterJsonOutput,
  retryJsonTask,
  cancelJsonTask,
  retryJsonTaskBatch,
  deleteJsonTask,
  importNovelTextChapters,
  downloadChapterAudio,
  enqueueChapterAudioAsr,
  cancelChapterAudioAsr,
  cancelPendingIllustrationImages,
  cancelPendingIllustrationTasks,
  enqueueBatchAudioAsr,
  enqueueChapterNsfwReview,
  enqueueBatchNsfwReview,
  enqueueChapterIllustration,
  fetchChapterIllustrationPayload,
  fetchChapterIllustrationLlmParams,
  fetchChapterIllustrationPromptBatches,
  saveChapterIllustrationPromptOutput,
  fetchChapterIllustrationImages,
  enqueueIllustrationImage,
  enqueueAllIllustrationImages,
  retryChapterIllustrationPromptBatch,
  fetchChapterAsrFile,
  createChapter,
  updateChapter,
  deleteChapter,
  saveNovel,
  listNovelBundles,
  createNovelBundle,
  fetchNovelBundleStatus,
  listRoleVoiceBundles,
  createRoleVoiceBundle,
  deleteNovelBundleFile,
  savePrompt,
  savePromptSettings,
  saveSettings,
  saveWorkflow,
  restartAudioAsrWorker,
  restartLineAudioWorker,
  restartNsfwReviewWorker,
  restartIllustrationWorker,
  restartIllustrationLlmWorker,
  restartIllustrationImageWorker,
  fetchIllustrationWorkerStatus,
  fetchIllustrationLlmWorkerStatus,
  fetchIllustrationImageWorkerStatus,
  restartTaskWorker,
  setActiveNovelId,
  clearWorkflowLogs,
  // 角色库
  fetchRoles,
  createRole,
  updateRole,
  updateRoleLevel,
  duplicateRole,
  createRoleAlias,
  deleteRole,
  uploadRoleSampleAudio,
  generateRoleSampleAudio,
  downloadRoleVoiceBundleFile,
  // 台词音频
  fetchChapterLineAudios,
  fetchRoleLineAudios,
  fetchRoleLineCounts,
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
  // 视频导出
  fetchVideoExportTasks,
  fetchVideoExportWorkerStatus,
  restartVideoExportWorker,
  enqueueChapterVideoExport,
  fetchChapterVideoExportStatus,
  retryVideoExportTask,
  cancelVideoExportTask,
  getVideoExportFileUrl,
  downloadNovelBundleFile,
};
