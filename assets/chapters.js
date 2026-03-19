import {
  createChapter,
  deleteChapter,
  downloadChapterAudio,
  fetchChapterDetail,
  fetchChapterJsonOutput,
  fetchNovelChapters,
  getData,
  requestConvertJson,
  requestGenerateAudio,
  saveChapterJsonOutput,
  setActiveNovelId,
  updateChapter,
  fetchChapterLineAudios,
  enqueueLineAudio,
  enqueueAllLineAudios,
  mergeChapterLineAudio,
  getMergedAudioUrl,
} from "./store.js";
import { fmtDateTime, fmtNumber, incrementNavBadge, renderNav, showPageError, toast } from "./ui.js";
import { localizeDocumentText, t, translateText } from "./i18n.js";

let allNovels = [];
let activeNovel = null;
let chapterState = [];
let activeChapterNum = null;
let activeChapterDetail = null;
let chapterModalMode = "create";
let chapterEditSourceNum = null;
let modalInitialWordCount = 0;
let jsonViewMode = "raw";
let jsonViewRawText = "";
let jsonViewParsed = null;
let jsonViewEditing = false;

// 台词音频状态
let lineAudioEntries = [];

function setGenerateAudioVisible(visible) {
  document.getElementById("generateAudioBtn").classList.toggle("hidden", !visible);
  document.getElementById("audioScheduleWrap").classList.toggle("hidden", !visible);
  document.getElementById("downloadAudioBtn").classList.toggle("hidden", !visible);
  const modeEl = document.getElementById("audioScheduleMode");
  const atEl = document.getElementById("audioScheduleAt");
  if (!visible) {
    atEl.classList.add("hidden");
    return;
  }
  const isScheduled = String(modeEl.value || "immediate") === "scheduled";
  atEl.classList.toggle("hidden", !isScheduled);
  if (isScheduled && !atEl.value) {
    const dt = new Date(Date.now() + 10 * 60 * 1000);
    dt.setSeconds(0, 0);
    atEl.value = formatLocalDateTime(dt);
  }
}

function canGenerateAudioFromJsonText(jsonText) {
  const raw = String(jsonText || "").trim();
  if (!raw) return false;
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return false;
  }
  const roleList = Array.isArray(parsed?.role_list) ? parsed.role_list : [];
  const juben = String(parsed?.juben || "").trim();
  return roleList.length > 0 && juben.length > 0;
}

async function syncGenerateAudioVisibility() {
  if (!activeNovel || !activeChapterNum) {
    setGenerateAudioVisible(false);
    return;
  }
  const chapter = getCurrentChapterState();
  if (!chapter?.hasJson) {
    setGenerateAudioVisible(false);
    return;
  }
  try {
    const output = await fetchChapterJsonOutput(activeNovel.id, activeChapterNum);
    setGenerateAudioVisible(canGenerateAudioFromJsonText(output?.jsonText || ""));
  } catch {
    setGenerateAudioVisible(false);
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const base64 = reader.result?.toString()?.split(",")?.[1];
      if (base64) resolve(base64);
      else reject(new Error("Failed to convert file to base64"));
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function fmtDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "-";
  }
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) {
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function calcWordCount(text) {
  return String(text || "").replace(/\s+/g, "").length;
}

function formatLocalDateTime(dt) {
  const y = dt.getFullYear();
  const m = String(dt.getMonth() + 1).padStart(2, "0");
  const d = String(dt.getDate()).padStart(2, "0");
  const hh = String(dt.getHours()).padStart(2, "0");
  const mm = String(dt.getMinutes()).padStart(2, "0");
  return `${y}-${m}-${d}T${hh}:${mm}`;
}

function parseScheduleToUtcIso(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return "";
  return dt.toISOString();
}

function bindAudioScheduleControl() {
  const modeEl = document.getElementById("audioScheduleMode");
  const atEl = document.getElementById("audioScheduleAt");
  const syncScheduleInputVisibility = () => {
    if (document.getElementById("audioScheduleWrap").classList.contains("hidden")) {
      atEl.classList.add("hidden");
      return;
    }
    const isScheduled = modeEl.value === "scheduled";
    atEl.classList.toggle("hidden", !isScheduled);
    if (isScheduled && !atEl.value) {
      const dt = new Date(Date.now() + 10 * 60 * 1000);
      dt.setSeconds(0, 0);
      atEl.value = formatLocalDateTime(dt);
    }
  };
  modeEl.addEventListener("change", syncScheduleInputVisibility);
  syncScheduleInputVisibility();
}

function syncModalWordCount(form) {
  const content = String(form.content.value || "");
  const wc = content.trim() ? calcWordCount(content) : modalInitialWordCount;
  form.wordCount.value = String(wc);
}

function getNovelByQueryOrActive() {
  const url = new URL(window.location.href);
  const queryId = String(url.searchParams.get("novelId") || "");
  if (queryId) {
    return allNovels.find((n) => String(n.id) === queryId) || null;
  }
  return allNovels[0] || null;
}

function setHeader(novel) {
  document.getElementById("chapterPageTitle").textContent = `${novel.name} ${t("nav.chapters")}`;
}

function renderNovelSelect() {
  const select = document.getElementById("chapterNovelSelect");
  select.innerHTML = allNovels.map((n) => `<option value="${n.id}">${n.name}</option>`).join("");
  if (activeNovel) select.value = String(activeNovel.id);
}

function renderQuickJump(list) {
  const wrap = document.getElementById("chapterQuickJump");
  wrap.innerHTML = list
    .slice(0, 200)
    .map((c) => {
      const progress = c.hasAudio ? 100 : c.hasJson ? 55 : 0;
      const activeClass = c.chapterNum === activeChapterNum ? "active" : "";
      return `<button class="quick-chip ${activeClass}" style="--progress:${progress}%" data-chapter-num="${c.chapterNum}">${String(c.chapterNum).padStart(3, "0")}</button>`;
    })
    .join("");
  wrap.querySelectorAll("[data-chapter-num]").forEach((el) => {
    el.addEventListener("click", () => loadChapter(Number(el.dataset.chapterNum)));
  });
}

function renderChapterList() {
  const keyword = document.getElementById("chapterSearch").value.trim();
  const list = keyword
    ? chapterState.filter((c) => c.title.includes(keyword) || String(c.chapterNum).includes(keyword))
    : chapterState;

  document.getElementById("chapterList").innerHTML = list
    .map((c) => {
      const activeClass = c.chapterNum === activeChapterNum ? "active" : "";
      return `<li class="chapter-item ${activeClass}" data-chapter-num="${c.chapterNum}">
        <strong>${c.title}</strong>
        <div class="meta chapter-item-meta">
          <span>字数 ${fmtNumber(c.wordCount)}</span>
          <span class="chapter-state-icons">
            <span class="state-icon state-json ${c.hasJson ? "done" : "todo"}" title="JSON"></span>
            <span class="state-icon state-audio ${c.hasAudio ? "done" : "todo"}" title="音频"></span>
          </span>
        </div>
      </li>`;
    })
    .join("");

  document.querySelectorAll(".chapter-item").forEach((el) => {
    el.addEventListener("click", () => loadChapter(Number(el.dataset.chapterNum)));
  });
  renderQuickJump(chapterState);
  localizeDocumentText(document);
}

function setStatus(text) {
  document.getElementById("chapterStatus").textContent = translateText(text);
}

function getCurrentChapterState() {
  return chapterState.find((c) => c.chapterNum === activeChapterNum) || null;
}

function resetChapterAudioPlayer() {
  const box = document.getElementById("chapterAudioBox");
  const player = document.getElementById("chapterAudioPlayer");
  const duration = document.getElementById("chapterAudioDuration");
  box.classList.add("hidden");
  duration.textContent = "-";
  player.pause();
  player.removeAttribute("src");
  player.load();
}

function refreshChapterAudioState(detail) {
  const downloadBtn = document.getElementById("downloadAudioBtn");
  if (!activeNovel || !detail?.hasAudio) {
    downloadBtn.disabled = true;
    resetChapterAudioPlayer();
    return;
  }
  const box = document.getElementById("chapterAudioBox");
  const player = document.getElementById("chapterAudioPlayer");
  const duration = document.getElementById("chapterAudioDuration");
  player.src = `/api/novels/${Number(activeNovel.id)}/chapters/${Number(detail.chapterNum)}/audio-stream`;
  duration.textContent = "...";
  box.classList.remove("hidden");
  downloadBtn.disabled = false;
}

async function loadChapter(chapterNum) {
  if (!activeNovel) return;
  activeChapterNum = chapterNum;
  try {
    const detail = await fetchChapterDetail(activeNovel.id, chapterNum);
    activeChapterDetail = detail;
    document.getElementById("chapterTitle").textContent = detail.title;
    document.getElementById("chapterMeta").textContent = `${detail.novelName} · 章节 ${detail.chapterNum} · 字数 ${fmtNumber(detail.wordCount)}`;
    document.getElementById("chapterContent").textContent = detail.content;
    refreshChapterAudioState(detail);
    await syncGenerateAudioVisibility();
    setStatus("就绪");
    renderChapterList();
    localizeDocumentText(document);
  } catch (err) {
    setGenerateAudioVisible(false);
    resetChapterAudioPlayer();
    document.getElementById("downloadAudioBtn").disabled = true;
    setStatus(t("error.loadFailed", { msg: err.message }));
  }
}

function copyText(text, successText) {
  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    return navigator.clipboard.writeText(text).then(() => toast(successText));
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  document.execCommand("copy");
  document.body.removeChild(ta);
  toast(successText);
  return Promise.resolve();
}

function downloadText(filename, content) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function renderJsonViewMode() {
  const preview = document.getElementById("chapterJsonPreview");
  const editor = document.getElementById("chapterJsonEditor");
  const editBtn = document.getElementById("editJsonViewBtn");
  const saveBtn = document.getElementById("saveJsonViewBtn");
  const rawBtn = document.getElementById("viewJsonRawBtn");
  const jubenBtn = document.getElementById("viewJsonJubenBtn");
  const rolesBtn = document.getElementById("viewJsonRolesBtn");
  rawBtn.classList.toggle("active", jsonViewMode === "raw");
  jubenBtn.classList.toggle("active", jsonViewMode === "juben");
  rolesBtn.classList.toggle("active", jsonViewMode === "roles");

  const canEdit = jsonViewMode === "juben" || jsonViewMode === "roles";
  editBtn.classList.toggle("hidden", !canEdit);
  saveBtn.classList.toggle("hidden", !jsonViewEditing);
  editBtn.textContent = jsonViewEditing ? t("common.cancelEdit") : t("common.edit");
  preview.classList.toggle("hidden", jsonViewEditing);
  editor.classList.toggle("hidden", !jsonViewEditing);

  if (jsonViewEditing) {
    if (jsonViewMode === "juben") {
      editor.value = String(jsonViewParsed?.juben || "").trim();
    } else if (jsonViewMode === "roles") {
      const roles = Array.isArray(jsonViewParsed?.role_list) ? jsonViewParsed.role_list : [];
      editor.value = JSON.stringify(roles, null, 2);
    } else {
      editor.value = jsonViewRawText || "";
    }
    return;
  }

  if (jsonViewMode === "raw") {
    if (jsonViewParsed && typeof jsonViewParsed === "object") {
      preview.textContent = JSON.stringify(jsonViewParsed, null, 2);
    } else {
      preview.textContent = jsonViewRawText || JSON.stringify({ role_list: [], juben: "" }, null, 2);
    }
    return;
  }

  if (!jsonViewParsed || typeof jsonViewParsed !== "object") {
    preview.textContent = "JSON 解析失败，无法显示此视图。";
    return;
  }

  if (jsonViewMode === "juben") {
    const juben = String(jsonViewParsed.juben || "").trim();
    preview.textContent = juben || "该 JSON 没有 juben 字段。";
    return;
  }

  const list = Array.isArray(jsonViewParsed.role_list) ? jsonViewParsed.role_list : [];
  if (!list.length) {
    preview.textContent = "该 JSON 没有 role_list 数据。";
    return;
  }
  const lines = list.map((x, i) => {
    const name = String(x?.name || "").trim() || `角色${i + 1}`;
    const instruct = String(x?.instruct || "").trim() || "-";
    const sample = String(x?.text || "").trim() || "-";
    return `【${name}】\n人设: ${instruct}\n示例: ${sample}`;
  });
  preview.textContent = lines.join("\n\n");
  localizeDocumentText(document);
}

function applyAudioScheduleLabels() {
  const label = document.getElementById("audioScheduleLabel");
  if (label) label.textContent = t("chapter.audioExecution");
  const select = document.getElementById("audioScheduleMode");
  if (!select) return;
  const immediateOpt = select.querySelector('option[value="immediate"]');
  const scheduledOpt = select.querySelector('option[value="scheduled"]');
  if (immediateOpt) immediateOpt.textContent = t("common.immediate");
  if (scheduledOpt) scheduledOpt.textContent = t("common.scheduled");
}

async function saveJsonViewEdit() {
  if (!activeNovel || !activeChapterNum || !jsonViewParsed || typeof jsonViewParsed !== "object") {
    toast("当前章节 JSON 不可编辑");
    return;
  }
  const editor = document.getElementById("chapterJsonEditor");
  const draft = JSON.parse(JSON.stringify(jsonViewParsed));

  if (jsonViewMode === "juben") {
    draft.juben = String(editor.value || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
  } else if (jsonViewMode === "roles") {
    let roles;
    try {
      roles = JSON.parse(String(editor.value || "[]"));
    } catch {
      toast("角色编辑内容必须是合法 JSON 数组");
      return;
    }
    if (!Array.isArray(roles)) {
      toast("角色编辑内容必须是 JSON 数组");
      return;
    }
    draft.role_list = roles.map((x) => ({
      name: String(x?.name || "").trim(),
      instruct: String(x?.instruct || "").trim(),
      text: String(x?.text || "").trim(),
    }));
  } else {
    toast("当前模式不支持编辑");
    return;
  }

  const merged = JSON.stringify(draft, null, 2);
  await saveChapterJsonOutput(activeNovel.id, activeChapterNum, merged);
  jsonViewParsed = draft;
  jsonViewRawText = merged;
  jsonViewEditing = false;
  renderJsonViewMode();
  await syncGenerateAudioVisibility();
  setStatus("JSON 已保存");
  toast(t("toast.saved"));
}

async function refreshChapters() {
  if (!activeNovel) return;
  chapterState = await fetchNovelChapters(activeNovel.id);
  if (chapterState.length === 0) {
    activeChapterNum = null;
    activeChapterDetail = null;
    setGenerateAudioVisible(false);
    resetChapterAudioPlayer();
    document.getElementById("downloadAudioBtn").disabled = true;
    document.getElementById("chapterTitle").textContent = "暂无章节";
    document.getElementById("chapterMeta").textContent = "当前小说尚未创建章节";
    document.getElementById("chapterContent").textContent = "请先点击"创建章回"录入章节信息。";
    renderChapterList();
    localizeDocumentText(document);
    return;
  }
  if (!chapterState.some((x) => x.chapterNum === activeChapterNum)) {
    activeChapterNum = chapterState[0].chapterNum;
  }
  renderChapterList();
  await loadChapter(activeChapterNum);
}

function bindActions() {
  document.getElementById("copyChapterBtn").addEventListener("click", () => {
    if (!activeChapterDetail) return;
    copyText(`${activeChapterDetail.title}\n\n${activeChapterDetail.content}`, t("toast.copied"));
  });

  document.getElementById("downloadChapterBtn").addEventListener("click", () => {
    if (!activeChapterDetail || !activeNovel) return;
    downloadText(`${activeNovel.name}-${activeChapterDetail.title}.txt`, activeChapterDetail.content || "");
    setStatus("开始下载文本");
  });

  document.getElementById("convertJsonBtn").addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    try {
      await requestConvertJson(activeNovel.id, activeChapterNum);
      incrementNavBadge("json", 1);
      renderNav();
      setStatus("已加入 JSON 转换队列");
      toast(t("toast.created"));
      await refreshChapters();
    } catch (err) {
      setStatus(t("error.operationFailed", { msg: err.message }));
      toast(t("error.operationFailed", { msg: err.message }));
    }
  });

  document.getElementById("viewJsonBtn").addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    const output = await fetchChapterJsonOutput(activeNovel.id, activeChapterNum);
    const text = output.jsonText || JSON.stringify({ role_list: [], juben: "" }, null, 2);
    jsonViewRawText = text;
    jsonViewParsed = null;
    try {
      jsonViewParsed = JSON.parse(text);
    } catch {
      jsonViewParsed = null;
    }
    jsonViewMode = "raw";
    jsonViewEditing = false;
    renderJsonViewMode();
    localizeDocumentText(document);
    document.getElementById("jsonDialog").showModal();
  });

  document.getElementById("viewJsonRawBtn").addEventListener("click", () => {
    jsonViewMode = "raw";
    jsonViewEditing = false;
    renderJsonViewMode();
    localizeDocumentText(document);
  });
  document.getElementById("viewJsonJubenBtn").addEventListener("click", () => {
    jsonViewMode = "juben";
    jsonViewEditing = false;
    renderJsonViewMode();
    localizeDocumentText(document);
  });
  document.getElementById("viewJsonRolesBtn").addEventListener("click", () => {
    jsonViewMode = "roles";
    jsonViewEditing = false;
    renderJsonViewMode();
    localizeDocumentText(document);
  });

  document.getElementById("editJsonViewBtn").addEventListener("click", () => {
    if (jsonViewMode !== "juben" && jsonViewMode !== "roles") return;
    jsonViewEditing = !jsonViewEditing;
    renderJsonViewMode();
    localizeDocumentText(document);
  });
  document.getElementById("saveJsonViewBtn").addEventListener("click", async () => {
    await saveJsonViewEdit();
  });

  document.getElementById("copyJsonBtn").addEventListener("click", () => {
    const text = jsonViewEditing
      ? document.getElementById("chapterJsonEditor").value || ""
      : document.getElementById("chapterJsonPreview").textContent || "";
    copyText(text, t("toast.copied"));
  });

  document.getElementById("generateAudioBtn").addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    const scheduleMode = String(document.getElementById("audioScheduleMode").value || "immediate");
    let scheduledAt = "";
    let scheduledAtText = "";
    if (scheduleMode === "scheduled") {
      const rawLocal = document.getElementById("audioScheduleAt").value;
      scheduledAt = parseScheduleToUtcIso(rawLocal);
      if (!scheduledAt) {
        setStatus(t("api.invalidScheduledAt"));
        toast(t("api.invalidScheduledAt"));
        return;
      }
      scheduledAtText = fmtDateTime(scheduledAt) || rawLocal;
    }
    try {
      await requestGenerateAudio(activeNovel.id, activeChapterNum, { scheduledAt });
      incrementNavBadge("audio", 1);
      renderNav();
      if (scheduleMode === "scheduled") {
        setStatus(`${t("toast.created")} (${scheduledAtText})`);
        toast(`${t("toast.created")} (${scheduledAtText})`);
      } else {
        setStatus("开始下载音频");
        toast(t("toast.created"));
      }
      await refreshChapters();
    } catch (err) {
      setStatus(t("error.operationFailed", { msg: err.message }));
      toast(t("error.operationFailed", { msg: err.message }));
    }
  });

  document.getElementById("downloadAudioBtn").addEventListener("click", () => {
    if (!activeNovel || !activeChapterDetail) return;
    if (!activeChapterDetail.hasAudio) {
      setStatus(t("api.notFound"));
      return;
    }
    downloadChapterAudio(activeNovel.id, activeChapterDetail.chapterNum)
      .then(() => {
        setStatus("开始下载音频");
      })
      .catch((err) => {
        setStatus(t("error.operationFailed", { msg: err.message }));
      });
  });

  document.getElementById("createChapterBtn").addEventListener("click", () => {
    openChapterModal("create");
  });

  document.getElementById("editChapterBtn").addEventListener("click", () => {
    openChapterModal("edit");
  });

  document.getElementById("deleteChapterBtn").addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    const chapter = getCurrentChapterState();
    if (!chapter) return;
    if (!window.confirm(t("confirm.deleteChapter", { title: chapter.title }))) return;
    try {
      await deleteChapter(activeNovel.id, activeChapterNum);
      toast(t("toast.deleted"));
      await refreshChapters();
    } catch (err) {
      toast(t("error.deleteFailed", { msg: err.message }));
    }
  });

  document.getElementById("chapterCancelBtn").addEventListener("click", () => {
    document.getElementById("chapterModal").close();
  });

  document.getElementById("chapterForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!activeNovel) return;
    const form = event.currentTarget;
    const input = {
      chapterNum: Number(form.chapterNum.value),
      title: form.title.value.trim(),
      content: form.content.value,
    };
    if (!input.chapterNum || !input.title) {
      toast(t("error.operationFailed", { msg: "invalid input" }));
      return;
    }
    try {
      if (chapterModalMode === "create") {
        await createChapter(activeNovel.id, input);
        toast(t("toast.created"));
      } else {
        await updateChapter(activeNovel.id, chapterEditSourceNum, input);
        toast(t("toast.updated"));
      }
      document.getElementById("chapterModal").close();
      activeChapterNum = input.chapterNum;
      await refreshChapters();
    } catch (err) {
      toast(t("error.saveFailed", { msg: err.message }));
    }
  });

  const chapterForm = document.getElementById("chapterForm");
  const contentInput = chapterForm.elements.namedItem("content");
  if (contentInput) {
    contentInput.addEventListener("input", () => {
      syncModalWordCount(chapterForm);
    });
  }

  document.getElementById("chapterSearch").addEventListener("input", renderChapterList);
  document.getElementById("chapterNovelSelect").addEventListener("change", async (event) => {
    const id = Number(event.target.value);
    setActiveNovelId(id);
    activeNovel = allNovels.find((n) => Number(n.id) === id) || null;
    if (!activeNovel) return;
    setHeader(activeNovel);
    await refreshChapters();
    toast(`${t("common.view")}: ${activeNovel.name}`);
  });

  // 台词预览按钮
  document.getElementById("viewLineAudioBtn")?.addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) {
      toast("请先选择章节");
      return;
    }
    const chapter = getCurrentChapterState();
    if (!chapter?.hasJson) {
      toast("当前章节没有JSON数据，请先转换");
      return;
    }
    await loadLineAudios();
    document.getElementById("lineAudioDialog")?.showModal();
  });

  // 台词音频弹窗内按钮
  document.getElementById("enqueueAllLineAudioBtn")?.addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    try {
      const result = await enqueueAllLineAudios(activeNovel.id, activeChapterNum);
      toast(`已加入队列: ${result.queued || 0} 个, 跳过: ${result.skipped?.length || 0} 个`);
      await loadLineAudios();
    } catch (err) {
      toast(err.message);
    }
  });

  document.getElementById("mergeLineAudioBtn")?.addEventListener("click", async () => {
    if (!activeNovel || !activeChapterNum) return;
    try {
      await mergeChapterLineAudio(activeNovel.id, activeChapterNum);
      toast("音频已合并");
      const url = getMergedAudioUrl(activeNovel.id, activeChapterNum);
      const audio = new Audio(url);
      audio.play();
    } catch (err) {
      toast(err.message);
    }
  });

  const player = document.getElementById("chapterAudioPlayer");
  const duration = document.getElementById("chapterAudioDuration");
  player.addEventListener("loadedmetadata", () => {
    duration.textContent = `时长：${fmtDuration(player.duration)}`;
  });
  player.addEventListener("durationchange", () => {
    duration.textContent = `时长：${fmtDuration(player.duration)}`;
  });
  player.addEventListener("error", () => {
    duration.textContent = "时长：读取失败";
  });

  bindAudioScheduleControl();
}

// ============ 台词音频功能 ============

async function loadLineAudios() {
  if (!activeNovel || !activeChapterNum) return;
  try {
    lineAudioEntries = await fetchChapterLineAudios(activeNovel.id, activeChapterNum);
    renderLineAudioTable();
  } catch (err) {
    toast(t("error.loadFailed", { msg: err.message }));
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function renderLineAudioTable() {
  const tbody = document.getElementById("lineAudioTableBody");
  if (!tbody) return;

  tbody.innerHTML = lineAudioEntries.map(entry => `
    <tr data-line-index="${entry.lineIndex}">
      <td>${entry.lineNo}</td>
      <td>
        ${entry.roleName}
        ${!entry.roleInLibrary ? '<span class="badge badge-warning">未入库</span>' : ""}
        ${entry.roleInLibrary && !entry.roleHasSampleAudio ? '<span class="badge badge-warning">无音频</span>' : ""}
      </td>
      <td>${escapeHtml(entry.lineText)}</td>
      <td>
        ${entry.task
          ? `<span class="status-${entry.task.status}">${entry.task.status}</span>`
          : '<span class="text-muted">-</span>'
        }
      </td>
      <td>
        ${entry.hasAudio && entry.streamUrl
          ? `<audio controls src="${entry.streamUrl}" style="width: 150px;"></audio>`
          : '<span class="text-muted">未生成</span>'
        }
      </td>
      <td>
        <button class="ghost-btn btn-sm enqueue-line-btn" data-line-index="${entry.lineIndex}" ${!entry.canGenerate ? "disabled" : ""}>
          生成
        </button>
      </td>
    </tr>
  `).join("");

  tbody.querySelectorAll(".enqueue-line-btn").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      const lineIndex = Number(e.target.dataset.lineIndex);
      try {
        await enqueueLineAudio(activeNovel.id, activeChapterNum, lineIndex);
        toast("已加入生成队列");
        await loadLineAudios();
      } catch (err) {
        toast(err.message);
      }
    });
  });
}

function openChapterModal(mode) {
  if (!activeNovel) return;
  const form = document.getElementById("chapterForm");
  const modal = document.getElementById("chapterModal");
  chapterModalMode = mode;
  chapterEditSourceNum = null;

  if (mode === "create") {
    const maxNum = chapterState.reduce((m, c) => Math.max(m, c.chapterNum), 0);
    document.getElementById("chapterModalTitle").textContent = "创建章回";
    form.chapterNum.value = String(maxNum + 1);
    form.title.value = "";
    modalInitialWordCount = 0;
    form.content.value = "";
    syncModalWordCount(form);
    localizeDocumentText(document);
    modal.showModal();
    return;
  }

  if (!activeChapterDetail) {
    toast(t("api.chapterNotFound"));
    return;
  }
  chapterEditSourceNum = activeChapterDetail.chapterNum;
  document.getElementById("chapterModalTitle").textContent = "编辑章回";
  form.chapterNum.value = String(activeChapterDetail.chapterNum);
  form.title.value = activeChapterDetail.title || "";
  modalInitialWordCount = Number(activeChapterDetail.wordCount) || 0;
  form.content.value = activeChapterDetail.content || "";
  syncModalWordCount(form);
  localizeDocumentText(document);
  modal.showModal();
}

async function init() {
  renderNav();
  applyAudioScheduleLabels();
  document.getElementById("downloadAudioBtn").disabled = true;
  setGenerateAudioVisible(false);
  resetChapterAudioPlayer();
  const data = await getData();
  allNovels = data.novels || [];
  activeNovel = getNovelByQueryOrActive();
  if (!activeNovel) {
    document.getElementById("chapterPageTitle").textContent = "暂无小说可管理";
    return;
  }
  setActiveNovelId(activeNovel.id);
  setHeader(activeNovel);
  renderNovelSelect();
  bindActions();
  await refreshChapters();
  localizeDocumentText(document);
}

init().catch((err) => {
  renderNav();
  showPageError(err, t("error.pageLoad"));
});
