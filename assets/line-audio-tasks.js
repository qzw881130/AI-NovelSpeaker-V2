import {
  getData,
  getActiveNovelId,
  setActiveNovelId,
  fetchLineAudioTasks,
  deleteLineAudioTask,
} from "./store.js";
import { renderNav, toast, fmtDateTime } from "./ui.js";
import { t } from "./i18n.js";

let allNovels = [];
let activeNovel = null;
let lineAudioTasks = [];
let activeLineAudioTaskId = null;
let lineAudioRefreshTimerId = null;
let activeLineAudioTaskSignature = "";

function isTaskDetailAudioPlaying() {
  const player = document.getElementById("lineAudioTaskPlayer");
  return Boolean(player && !player.paused && !player.ended);
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

function setHeader(novel) {
  document.getElementById("lineAudioPageTitle").textContent = `${novel.name} - 台词音频任务队列`;
}

function renderNovelSelect() {
  const select = document.getElementById("lineAudioNovelSelect");
  select.innerHTML = allNovels.map((n) => `<option value="${n.id}">${n.name}</option>`).join("");
  if (activeNovel) select.value = String(activeNovel.id);
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function parseDbTime(text) {
  const raw = String(text || "").trim();
  if (!raw) return null;
  const iso = raw.includes("T") ? raw : raw.replace(" ", "T");
  const dt = new Date(`${iso}Z`);
  return Number.isNaN(dt.getTime()) ? null : dt;
}

function formatHms(totalSeconds) {
  const sec = Math.max(0, Math.floor(totalSeconds || 0));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function calcTaskRuntime(task) {
  const start = parseDbTime(task.comfyStartedAt);
  if (!start) return "-";
  const finished = parseDbTime(task.comfyFinishedAt);
  const end = finished || (task.comfyStatus === "running" ? new Date() : null);
  if (!end) return "-";
  return formatHms((end.getTime() - start.getTime()) / 1000);
}

function getScheduleLabel(task) {
  const scheduledAt = String(task.scheduledAt || "").trim();
  return scheduledAt ? `指定时间执行 ${fmtDateTime(scheduledAt) || scheduledAt}` : "立即执行";
}

function statusClass(status) {
  if (status === "processing" || status === "running") return "status-running";
  if (status === "completed") return "status-completed";
  if (status === "failed") return "status-failed";
  return "status-pending";
}

function renderLineAudioTaskList() {
  const listEl = document.getElementById("lineAudioTaskList");
  listEl.innerHTML = "";

  if (!lineAudioTasks || lineAudioTasks.length === 0) {
    listEl.innerHTML = '<li class="task-list-empty">暂无台词音频任务</li>';
    return;
  }

  for (const task of lineAudioTasks) {
    const li = document.createElement("li");
    li.className = "task-list-item" + (task.id === activeLineAudioTaskId ? " active" : "");
    li.dataset.taskId = task.id;

    const status = task.status || "pending";
    const comfyStatus = task.comfyStatus || "-";

    li.innerHTML = `
      <div class="task-list-title">#${task.id} | ${task.chapterTitle || `第${task.chapterNum}章`}</div>
      <div class="task-list-subtitle">${escapeHtml(task.roleName || "未知角色")}：${escapeHtml(task.lineText?.substring(0, 20) || "")}...</div>
      <div class="task-list-meta">
        <span class="status-badge ${statusClass(status)}">${status}</span>
        <span>行号: ${task.lineIndex + 1}</span>
        <span>${escapeHtml(getScheduleLabel(task))}</span>
      </div>
    `;

    li.addEventListener("click", () => loadLineAudioTaskDetail(task.id));
    listEl.appendChild(li);
  }
}

function getLineAudioTaskSignature(task) {
  return JSON.stringify({
    id: task.id,
    status: task.status || "",
    comfyStatus: task.comfyStatus || "",
    comfyPromptId: task.comfyPromptId || "",
    outputFilename: task.outputFilename || "",
    downloadedFilePath: task.downloadedFilePath || "",
    scheduledAt: task.scheduledAt || "",
    errorMessage: task.errorMessage || "",
    updatedAt: task.updatedAt || "",
  });
}

function renderLineAudioTaskDetail(task) {
  const detailEl = document.getElementById("lineAudioTaskDetail");
  activeLineAudioTaskSignature = getLineAudioTaskSignature(task);

  let html = '<div class="task-detail-grid">';

  html += `<div class="detail-row"><span class="detail-label">任务ID:</span><span class="detail-value">${task.id}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">章节:</span><span class="detail-value">${escapeHtml(task.chapterTitle || `第${task.chapterNum}章`)}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">台词序号:</span><span class="detail-value">${task.lineIndex + 1}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">角色:</span><span class="detail-value">${escapeHtml(task.roleName || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">台词:</span><span class="detail-value">${escapeHtml(task.lineText || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">参考文本:</span><span class="detail-value">${escapeHtml(task.referenceText || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">参考音频:</span><span class="detail-value">${escapeHtml(task.referenceAudioPath || "-")}</span></div>`;

  const status = task.status || "pending";
  html += `<div class="detail-row"><span class="detail-label">状态:</span><span class="detail-value"><span class="status-badge ${statusClass(status)}">${status}</span></span></div>`;
  html += `<div class="detail-row"><span class="detail-label">Comfy状态:</span><span class="detail-value">${escapeHtml(task.comfyStatus || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">Comfy Prompt ID:</span><span class="detail-value">${escapeHtml(task.comfyPromptId || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">输出文件:</span><span class="detail-value">${escapeHtml(task.outputFilename || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">本地下载路径:</span><span class="detail-value">${escapeHtml(task.downloadedFilePath || "-")}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">执行设置:</span><span class="detail-value">${escapeHtml(getScheduleLabel(task))}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">任务用时:</span><span class="detail-value">${calcTaskRuntime(task)}</span></div>`;

  if (task.errorMessage) {
    html += `<div class="detail-row"><span class="detail-label">错误信息:</span><span class="detail-value error-text">${escapeHtml(task.errorMessage)}</span></div>`;
  }

  html += `<div class="detail-row"><span class="detail-label">创建时间:</span><span class="detail-value">${fmtDateTime(task.createdAt)}</span></div>`;
  html += `<div class="detail-row"><span class="detail-label">更新时间:</span><span class="detail-value">${fmtDateTime(task.updatedAt)}</span></div>`;

  html += '</div>';

  // 音频播放器
  if (status === "completed" && task.id) {
    html += '<div class="task-audio-section">';
    html += '<h4>音频试听</h4>';
    html += `<audio id="lineAudioTaskPlayer" controls preload="metadata" src="/api/line-audio-tasks/${Number(task.id)}/file"></audio>`;
    html += '</div>';
  }

  // 操作按钮
  html += '<div class="task-actions">';
  html += `<button class="ghost-btn danger delete-task-btn" data-task-id="${task.id}" type="button">删除任务</button>`;
  html += '</div>';

  detailEl.innerHTML = html;

  // 绑定删除按钮
  const deleteBtn = detailEl.querySelector(".delete-task-btn");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", async () => {
      if (!window.confirm("确定要删除这个任务吗？")) return;
      try {
        await deleteLineAudioTask(task.id);
        toast("任务已删除");
        lineAudioTasks = lineAudioTasks.filter((t) => t.id !== task.id);
        if (activeLineAudioTaskId === task.id) {
          activeLineAudioTaskId = null;
          detailEl.innerHTML = '<p class="empty-text">请选择左侧任务查看详情。</p>';
        }
        renderLineAudioTaskList();
      } catch (err) {
        toast(err.message);
      }
    });
  }
}

async function loadLineAudioTaskList() {
  if (!activeNovel) return;
  try {
    lineAudioTasks = await fetchLineAudioTasks(activeNovel.id);
    renderLineAudioTaskList();
    if (activeLineAudioTaskId == null && lineAudioTasks.length > 0) {
      await loadLineAudioTaskDetail(lineAudioTasks[0].id);
    } else if (activeLineAudioTaskId != null) {
      await loadLineAudioTaskDetail(activeLineAudioTaskId);
    }
  } catch (err) {
    toast(t("error.loadFailed", { msg: err.message }));
  }
}

async function loadLineAudioTaskDetail(taskId) {
  if (!activeNovel) return;
  const task = lineAudioTasks.find((t) => t.id === taskId);
  if (!task) return;

  activeLineAudioTaskId = taskId;
  renderLineAudioTaskList();

  const nextSignature = getLineAudioTaskSignature(task);
  if (activeLineAudioTaskSignature === nextSignature) {
    return;
  }
  renderLineAudioTaskDetail(task);
}

async function refreshLineAudioTasks() {
  if (isTaskDetailAudioPlaying()) {
    return;
  }
  await loadLineAudioTaskList();
}

function applyLineAudioRefreshInterval() {
  if (lineAudioRefreshTimerId) {
    window.clearInterval(lineAudioRefreshTimerId);
    lineAudioRefreshTimerId = null;
  }
  const seconds = Number(document.getElementById("refreshLineAudioIntervalSelect").value || 0);
  if (!Number.isFinite(seconds) || seconds <= 0) return;
  lineAudioRefreshTimerId = window.setInterval(() => {
    refreshLineAudioTasks();
  }, seconds * 1000);
}

function bindActions() {
  document.getElementById("refreshLineAudioTasksBtn").addEventListener("click", () => {
    refreshLineAudioTasks();
  });

  document.getElementById("refreshLineAudioIntervalSelect").addEventListener("change", () => {
    applyLineAudioRefreshInterval();
    const seconds = Number(document.getElementById("refreshLineAudioIntervalSelect").value || 0);
    if (seconds > 0) {
      toast(`已设置自动刷新: ${seconds}秒`);
    }
  });

  document.getElementById("lineAudioNovelSelect").addEventListener("change", async (event) => {
    const id = Number(event.target.value);
    setActiveNovelId(id);
    activeNovel = allNovels.find((n) => Number(n.id) === id) || null;
    if (!activeNovel) return;
    setHeader(activeNovel);
    activeLineAudioTaskId = null;
    document.getElementById("lineAudioTaskDetail").innerHTML = '<p class="empty-text">请选择左侧任务查看详情。</p>';
    await refreshLineAudioTasks();
    toast(`${t("common.view")}: ${activeNovel.name}`);
  });
}

async function init() {
  renderNav();
  const data = await getData();
  allNovels = data.novels || [];
  activeNovel = getNovelByQueryOrActive();

  if (!activeNovel) {
    document.getElementById("lineAudioPageTitle").textContent = "暂无小说";
    document.getElementById("lineAudioNovelSelect").innerHTML = '<option value="">暂无小说</option>';
    return;
  }

  setActiveNovelId(activeNovel.id);
  setHeader(activeNovel);
  renderNovelSelect();
  bindActions();
  await refreshLineAudioTasks();
  applyLineAudioRefreshInterval();
}

init().catch((err) => {
  renderNav();
  toast(t("error.pageLoad", { msg: err.message }));
});
