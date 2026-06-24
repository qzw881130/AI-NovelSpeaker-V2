import { clearWorkflowLogs, fetchWorkflowLogs } from "./store.js";
import { fmtDateTime, renderNav, toast } from "./ui.js";

let workflowLogs = [];

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text || "");
  return div.innerHTML;
}

function formatDbDateTime(text) {
  const raw = String(text || "").trim();
  if (!raw) return "-";
  const hasZone = /[zZ]|[+-]\d\d:\d\d$/.test(raw);
  const iso = raw.includes("T") ? raw : raw.replace(" ", "T");
  const dt = new Date(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(dt.getTime()) ? escapeHtml(raw) : fmtDateTime(dt);
}

function copyText(text) {
  const value = String(text || "");
  if (!value.trim() || value === "-") {
    toast("暂无可复制内容");
    return Promise.resolve();
  }
  if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    return navigator.clipboard.writeText(value).then(() => toast("日志已复制"));
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
  toast("日志已复制");
  return Promise.resolve();
}

function copyIconSvg() {
  return `<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M8 7a3 3 0 0 1 3-3h6a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3h-1v1a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-6a3 3 0 0 1 3-3h1V7Zm2 1h3a3 3 0 0 1 3 3v3h1a1 1 0 0 0 1-1V7a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v1Zm-3 2a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-6a1 1 0 0 0-1-1H7Z" fill="currentColor" /></svg>`;
}

function render() {
  const list = document.getElementById("workflowLogsList");
  const summary = document.getElementById("workflowLogsSummary");
  summary.textContent = `共 ${workflowLogs.length} 条`;
  if (!workflowLogs.length) {
    list.innerHTML = '<p class="empty-text">暂无工作流日志</p>';
    return;
  }
  list.innerHTML = workflowLogs
    .map(
      (log) => `
      <article class="asset-card workflow-log-card ${log.errorLog ? "workflow-log-error" : ""}">
        <div class="queue-head">
          <h3>#${Number(log.id || 0)} ${escapeHtml(log.workflowName || "-")}</h3>
          <span class="chip ${log.errorLog ? "failed" : "completed"}">${log.errorLog ? "失败" : "成功"}</span>
        </div>
        <p class="meta"><strong>时间:</strong> ${formatDbDateTime(log.createdAt)} | <strong>工作流类别:</strong> ${escapeHtml(log.workflowCategory || "-")}</p>
        <div class="workflow-log-block">
          <div class="workflow-log-block-head">
            <h4>工作流JSON</h4>
            <button class="ghost-btn icon-btn workflow-log-copy-btn" type="button" data-log-id="${Number(log.id || 0)}" data-copy-kind="workflow" aria-label="复制工作流JSON" title="复制工作流JSON">${copyIconSvg()}</button>
          </div>
          <pre>${escapeHtml(log.workflowJson || "")}</pre>
        </div>
        <div class="workflow-log-block">
          <div class="workflow-log-block-head">
            <h4>错误日志</h4>
            <button class="ghost-btn icon-btn workflow-log-copy-btn" type="button" data-log-id="${Number(log.id || 0)}" data-copy-kind="error" aria-label="复制错误日志" title="复制错误日志">${copyIconSvg()}</button>
          </div>
          <pre>${escapeHtml(log.errorLog || "-")}</pre>
        </div>
      </article>
    `
    )
    .join("");
}

async function refresh() {
  workflowLogs = await fetchWorkflowLogs();
  render();
}

function bindEvents() {
  document.getElementById("refreshWorkflowLogsBtn").addEventListener("click", async () => {
    await refresh();
    toast("工作流日志已刷新");
  });
  document.getElementById("clearWorkflowLogsBtn").addEventListener("click", async () => {
    if (!window.confirm("确定要清除所有工作流日志吗？")) return;
    await clearWorkflowLogs();
    await refresh();
    toast("工作流日志已清空");
  });
  document.getElementById("workflowLogsList").addEventListener("click", (event) => {
    const btn = event.target.closest(".workflow-log-copy-btn");
    if (!btn) return;
    const log = workflowLogs.find((item) => Number(item.id || 0) === Number(btn.dataset.logId || 0));
    if (!log) return;
    const text = btn.dataset.copyKind === "error" ? log.errorLog : log.workflowJson;
    copyText(text).catch((err) => toast(`复制失败：${err.message}`));
  });
}

async function init() {
  renderNav();
  bindEvents();
  await refresh();
}

init().catch((err) => {
  renderNav();
  toast(err.message || "加载失败");
});
