import { clearWorkflowLogs, fetchWorkflowLogs } from "./store.js";
import { renderNav, toast } from "./ui.js";

let workflowLogs = [];

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text || "");
  return div.innerHTML;
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
        <p class="meta"><strong>时间:</strong> ${escapeHtml(log.createdAt || "-")} | <strong>工作流类别:</strong> ${escapeHtml(log.workflowCategory || "-")}</p>
        <div class="workflow-log-block">
          <h4>工作流JSON</h4>
          <pre>${escapeHtml(log.workflowJson || "")}</pre>
        </div>
        <div class="workflow-log-block">
          <h4>错误日志</h4>
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
