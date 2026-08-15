import {
  fetchSocialUploadWorkerStatus,
  fetchYoutubeOAuthUrl,
  fetchYoutubePlaylists,
  fetchYoutubeSettings,
  restartSocialUploadWorker,
  saveYoutubeSettings,
  uploadYoutubeConfigFile,
} from "./store.js";
import { renderNav, toast } from "./ui.js";
import { localizeDocumentText } from "./i18n.js";

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formPayload() {
  return {
    defaultTags: document.getElementById("youtubeDefaultTags")?.value || "",
    clientId: document.getElementById("youtubeClientId")?.value || "",
    clientSecret: document.getElementById("youtubeClientSecret")?.value || "",
    redirectUri: document.getElementById("youtubeRedirectUri")?.value || "http://localhost:8080/oauth",
    proxyEnabled: Boolean(document.getElementById("youtubeProxyEnabled")?.checked),
    proxyUrl: document.getElementById("youtubeProxyUrl")?.value || "http://127.0.0.1:7897",
  };
}

function renderSettings(settings) {
  document.getElementById("youtubeClientSecretPathText").textContent = settings.clientSecretPath || "未上传";
  document.getElementById("youtubeTokenPathText").textContent = settings.tokenPath || "未生成";
  document.getElementById("youtubeClientId").value = settings.clientId || "";
  document.getElementById("youtubeClientSecret").value = settings.clientSecret || "";
  document.getElementById("youtubeRedirectUri").value = settings.redirectUri || "http://localhost:8080/oauth";
  document.getElementById("youtubeDefaultTags").value = settings.defaultTags || "四大名著,三国演义,有声小说,旺仔有声小说";
  document.getElementById("youtubeProxyEnabled").checked = Boolean(settings.proxyEnabled);
  document.getElementById("youtubeProxyUrl").value = settings.proxyUrl || "http://127.0.0.1:7897";
  renderFiles(settings.files || []);
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",").pop() || "");
    reader.onerror = () => reject(reader.error || new Error("读取文件失败"));
    reader.readAsDataURL(file);
  });
}

async function uploadConfigFile(kind, input) {
  const file = input?.files?.[0];
  if (!file) return;
  try {
    const text = await file.text();
    JSON.parse(text);
    const encoded = await readFileAsBase64(file);
    renderSettings(await uploadYoutubeConfigFile(kind, encoded));
    toast("已上传配置文件");
  } catch (err) {
    toast(err.message || "上传失败");
  } finally {
    if (input) input.value = "";
  }
}

function renderFiles(files) {
  const root = document.getElementById("youtubeConfigFiles");
  if (!files.length) {
    root.innerHTML = `<p class="empty-text">暂无配置文件。</p>`;
    return;
  }
  root.innerHTML = files.map((file) => `
    <article class="task-detail-block">
      <div class="task-detail-head">
        <div>
          <h3>${escapeHtml(file.label)}</h3>
          <p class="meta">${escapeHtml(file.path || "未设置")}</p>
        </div>
        <span class="status-badge ${file.exists ? "status-completed" : "status-pending"}">${file.exists ? "已找到" : "未找到"}</span>
      </div>
    </article>
  `).join("");
}

function renderPlaylists(items) {
  const root = document.getElementById("youtubePlaylists");
  if (!items.length) {
    root.innerHTML = `<p class="empty-text">没有读取到播放列表。</p>`;
    return;
  }
  root.innerHTML = items.map((item) => `
    <article class="task-detail-block compact-block">
      <h3>${escapeHtml(item.title)}</h3>
      <p class="meta">${escapeHtml(item.id)}</p>
    </article>
  `).join("");
}

async function refreshWorkerStatus() {
  const el = document.getElementById("socialUploadWorkerStatus");
  try {
    const status = await fetchSocialUploadWorkerStatus();
    const label = { running: "运行中", stale: "心跳超时", stopped: "未运行" }[status.state] || status.state || "-";
    el.textContent = `上传Worker: ${label}${status.heartbeatAgeSeconds != null ? ` · 心跳${status.heartbeatAgeSeconds}s` : ""}`;
  } catch {
    el.textContent = "上传Worker: 未运行";
  }
}

async function loadSettings() {
  renderSettings(await fetchYoutubeSettings());
  renderPlaylists(await fetchYoutubePlaylists());
  await refreshWorkerStatus();
}

function bindEvents() {
  document.getElementById("refreshSocialMediaBtn")?.addEventListener("click", () => loadSettings().catch((err) => toast(err.message)));
  document.getElementById("restartSocialUploadWorkerBtn")?.addEventListener("click", async () => {
    try {
      await restartSocialUploadWorker();
      await refreshWorkerStatus();
      toast("已重启上传 Worker");
    } catch (err) {
      toast(err.message);
    }
  });
  document.getElementById("saveYoutubeSettingsBtn")?.addEventListener("click", async () => {
    try {
      renderSettings(await saveYoutubeSettings(formPayload()));
      toast("已保存油管配置");
    } catch (err) {
      toast(err.message);
    }
  });
  document.getElementById("loadYoutubePlaylistsBtn")?.addEventListener("click", async () => {
    try {
      renderPlaylists(await fetchYoutubePlaylists({ refresh: true }));
    } catch (err) {
      toast(err.message);
    }
  });
  document.getElementById("youtubeClientSecretFile")?.addEventListener("change", (event) => {
    uploadConfigFile("client-secret", event.target);
  });
  document.getElementById("youtubeAuthorizeBtn")?.addEventListener("click", async () => {
    try {
      renderSettings(await saveYoutubeSettings(formPayload()));
      const authUrl = await fetchYoutubeOAuthUrl();
      if (!authUrl) throw new Error("未生成授权链接");
      window.open(authUrl, "_blank", "noopener,noreferrer");
      toast("已打开 Google 授权页面，授权完成后返回刷新");
    } catch (err) {
      toast(err.message);
    }
  });
}

async function init() {
  renderNav();
  bindEvents();
  await loadSettings();
  localizeDocumentText(document);
}

init().catch((err) => toast(err.message));
