import { deleteLineAudioNoiseSample, editLineAudioNoiseSample, fetchLineAudioNoiseSamples } from "./store.js";
import { renderNav } from "./ui.js";

let samples = [];
const SAMPLE_LABELS = ["manual-abnormal", "abnormal"];
let waveSurferModulesPromise = null;
let waveSurfers = [];
let editorWaveSurfer = null;
let editorRegions = null;
let editorSelectionRegion = null;
let editorDeleteRegions = [];
let editorSampleName = "";
let editorSampleLabel = "";
let editorSampleUrl = "";

function toast(message) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  window.clearTimeout(toast._timer);
  toast._timer = window.setTimeout(() => el.classList.remove("show"), 2400);
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function formatDate(seconds) {
  const value = Number(seconds || 0);
  if (!value) return "-";
  return new Date(value * 1000).toLocaleString();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function destroyWaveSurfers() {
  for (const item of waveSurfers) {
    try {
      item.destroy?.();
    } catch (_err) {
      // ignore stale waveform instances
    }
  }
  waveSurfers = [];
}

async function loadWaveSurfer() {
  if (!waveSurferModulesPromise) {
    waveSurferModulesPromise = Promise.all([
      import("https://unpkg.com/wavesurfer.js@7/dist/wavesurfer.esm.js"),
      import("https://unpkg.com/wavesurfer.js@7/dist/plugins/regions.esm.js"),
    ]).then(([waveSurferModule, regionsModule]) => ({
      WaveSurfer: waveSurferModule.default,
      RegionsPlugin: regionsModule.default,
    }));
  }
  return await waveSurferModulesPromise;
}

function getFilteredSamples() {
  const keyword = String(document.getElementById("sampleKeyword")?.value || "").trim().toLowerCase();
  if (!keyword) return samples;
  return samples.filter((sample) => String(sample.name || "").toLowerCase().includes(keyword));
}

function sampleKey(sample) {
  return `${sample?.label || "manual-abnormal"}/${sample?.name || ""}`;
}

function sampleLabelText(label) {
  return label === "abnormal" ? "abnormal" : "manual_abnormal";
}

function renderSummary() {
  const count = samples.length;
  const totalSize = samples.reduce((sum, sample) => sum + Number(sample.size || 0), 0);
  const abnormalCount = samples.filter((sample) => sample.label === "abnormal").length;
  const manualCount = count - abnormalCount;
  const countEl = document.getElementById("sampleCount");
  const sizeEl = document.getElementById("sampleTotalSize");
  if (countEl) countEl.textContent = `${count}（manual ${manualCount} / abnormal ${abnormalCount}）`;
  if (sizeEl) sizeEl.textContent = formatBytes(totalSize);
}

async function renderWaveforms() {
  destroyWaveSurfers();
  const { WaveSurfer } = await loadWaveSurfer();
  for (const container of document.querySelectorAll(".manual-sample-waveform")) {
    const url = container.getAttribute("data-url");
    if (!url) continue;
    const waveSurfer = WaveSurfer.create({
      container,
      url,
      waveColor: "#d8b995",
      progressColor: "#a85224",
      cursorColor: "#2b2118",
      height: 72,
      normalize: true,
      interact: true,
    });
    container.addEventListener("click", () => waveSurfer.playPause());
    waveSurfers.push(waveSurfer);
  }
}

function renderSamples() {
  const list = document.getElementById("sampleList");
  if (!list) return;
  destroyWaveSurfers();
  const rows = getFilteredSamples();
  if (!rows.length) {
    list.innerHTML = `<div class="empty-state">没有可展示的异常样本。</div>`;
    return;
  }
  list.innerHTML = rows
    .map((sample) => {
      const name = escapeHtml(sample.name);
      const url = escapeHtml(sample.url);
      const label = sample.label || "manual-abnormal";
      const key = escapeHtml(sampleKey(sample));
      return `
        <article class="manual-sample-card" data-key="${key}">
          <div class="manual-sample-head">
            <div>
              <h3>${name}</h3>
              <p class="meta"><span class="manual-sample-label">${sampleLabelText(label)}</span> · ${formatBytes(sample.size)} · ${formatDate(sample.updatedAt)}</p>
            </div>
            <div class="manual-sample-card-actions">
              <button class="ghost-btn manual-sample-edit-btn" type="button" data-key="${key}">编辑</button>
              <button class="danger-btn manual-sample-delete-btn" type="button" data-key="${key}">删除</button>
            </div>
          </div>
          <audio class="manual-sample-audio" controls preload="metadata" src="${url}"></audio>
          <div class="manual-sample-waveform" data-url="${url}"></div>
        </article>
      `;
    })
    .join("");
  list.querySelectorAll(".manual-sample-delete-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const key = button.getAttribute("data-key") || "";
      await deleteSample(key);
    });
  });
  list.querySelectorAll(".manual-sample-edit-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      const key = button.getAttribute("data-key") || "";
      await openSampleEditor(key);
    });
  });
  renderWaveforms().catch((err) => toast(err.message || "波形加载失败"));
}

function destroySampleEditor() {
  try {
    editorWaveSurfer?.destroy?.();
  } catch (_err) {
    // ignore stale editor instance
  }
  editorWaveSurfer = null;
  editorRegions = null;
  editorSelectionRegion = null;
  editorDeleteRegions = [];
  editorSampleName = "";
  editorSampleLabel = "";
  editorSampleUrl = "";
}

function syncEditorInputs(start, end) {
  const startEl = document.getElementById("sampleEditorStart");
  const endEl = document.getElementById("sampleEditorEnd");
  if (startEl) startEl.value = Math.max(0, Number(start || 0)).toFixed(1);
  if (endEl) endEl.value = Math.max(0, Number(end || 0)).toFixed(1);
}

function updateEditorRegionFromInputs() {
  if (!editorSelectionRegion || !editorWaveSurfer) return;
  const duration = editorWaveSurfer.getDuration() || 0;
  const start = Math.max(0, Math.min(Number(document.getElementById("sampleEditorStart")?.value || 0), duration));
  const end = Math.max(start + 0.05, Math.min(Number(document.getElementById("sampleEditorEnd")?.value || duration), duration));
  editorSelectionRegion.setOptions?.({ start, end });
}

function getEditorSelection() {
  if (!editorSelectionRegion || !editorWaveSurfer) return { start: 0, end: 0 };
  const duration = editorWaveSurfer.getDuration() || 0;
  return {
    start: Math.max(0, Math.min(Number(editorSelectionRegion.start || 0), duration)),
    end: Math.max(0, Math.min(Number(editorSelectionRegion.end || 0), duration)),
  };
}

function normalizeSegments(segments, duration) {
  const maxDuration = Math.max(0, Number(duration || 0));
  return segments
    .map((segment) => ({
      start: Math.max(0, Math.min(Number(segment.start || 0), maxDuration)),
      end: Math.max(0, Math.min(Number(segment.end || 0), maxDuration)),
    }))
    .filter((segment) => segment.end - segment.start >= 0.05)
    .sort((left, right) => left.start - right.start)
    .reduce((items, segment) => {
      const last = items[items.length - 1];
      if (last && segment.start <= last.end + 0.02) {
        last.end = Math.max(last.end, segment.end);
      } else {
        items.push({ ...segment });
      }
      return items;
    }, []);
}

function getEditorDeleteSegments() {
  return normalizeSegments(
    editorDeleteRegions.map((region) => ({ start: region.start, end: region.end })),
    editorWaveSurfer?.getDuration?.() || 0,
  );
}

function updateEditorDeleteSummary() {
  const summary = document.getElementById("sampleEditorDeleteSummary");
  if (!summary) return;
  const segments = getEditorDeleteSegments();
  if (!segments.length) {
    summary.textContent = "还没有标记删除片段。";
    return;
  }
  summary.textContent = `已标记删除 ${segments.length} 段：${segments
    .map((segment, index) => `${index + 1}. ${segment.start.toFixed(1)}-${segment.end.toFixed(1)}秒`)
    .join("；")}`;
}

function addEditorDeleteRegion(start, end) {
  if (!editorRegions || !editorWaveSurfer || end - start < 0.05) return false;
  const region = editorRegions.addRegion({
    start,
    end,
    color: "rgba(168, 54, 47, 0.28)",
    drag: true,
    resize: true,
  });
  editorDeleteRegions.push(region);
  region.on?.("remove", () => {
    editorDeleteRegions = editorDeleteRegions.filter((item) => item !== region);
    updateEditorDeleteSummary();
  });
  updateEditorDeleteSummary();
  return true;
}

function clearEditorDeleteRegions() {
  for (const region of editorDeleteRegions) {
    region.remove?.();
  }
  editorDeleteRegions = [];
  updateEditorDeleteSummary();
}

async function openSampleEditor(key) {
  const sample = samples.find((item) => sampleKey(item) === key);
  if (!sample) return;
  const dialog = document.getElementById("sampleEditorDialog");
  const waveform = document.getElementById("sampleEditorWaveform");
  const title = document.getElementById("sampleEditorTitle");
  const durationEl = document.getElementById("sampleEditorDuration");
  if (!dialog || !waveform) return;
  destroySampleEditor();
  editorSampleName = sample.name;
  editorSampleLabel = sample.label || "manual-abnormal";
  editorSampleUrl = `${sample.url}?v=${encodeURIComponent(String(sample.updatedAt || Date.now()))}`;
  if (title) title.textContent = `编辑样本 ${sampleLabelText(editorSampleLabel)} / ${sample.name}`;
  if (durationEl) durationEl.textContent = "时长：-";
  syncEditorInputs(0, 0);
  updateEditorDeleteSummary();
  waveform.textContent = "正在加载波形...";
  if (!dialog.open) dialog.showModal();

  const { WaveSurfer, RegionsPlugin } = await loadWaveSurfer();
  waveform.textContent = "";
  editorRegions = RegionsPlugin.create();
  editorWaveSurfer = WaveSurfer.create({
    container: waveform,
    url: editorSampleUrl,
    waveColor: "#d8b995",
    progressColor: "#a85224",
    cursorColor: "#2b2118",
    height: 150,
    normalize: true,
    plugins: [editorRegions],
  });
  editorWaveSurfer.on("ready", () => {
    const duration = editorWaveSurfer.getDuration() || 0;
    if (durationEl) durationEl.textContent = `时长：${duration.toFixed(1)}秒`;
    editorSelectionRegion = editorRegions.addRegion({
      start: 0,
      end: duration,
      color: "rgba(168, 82, 36, 0.16)",
      drag: true,
      resize: true,
    });
    syncEditorInputs(0, duration);
  });
  editorRegions.on("region-updated", (region) => {
    if (region === editorSelectionRegion) {
      syncEditorInputs(region.start, region.end);
    } else if (editorDeleteRegions.includes(region)) {
      updateEditorDeleteSummary();
    }
  });
}

async function playEditorSelection() {
  if (!editorWaveSurfer) return;
  const { start, end } = getEditorSelection();
  if (end <= start) return;
  await editorWaveSurfer.play(start, end);
}

function markEditorDeleteRegion() {
  const { start, end } = getEditorSelection();
  if (!addEditorDeleteRegion(start, end)) {
    toast("请选择有效的样本片段");
  }
}

async function saveEditorKeep() {
  if (!editorSampleName) return;
  const { start, end } = getEditorSelection();
  if (end <= start || end - start < 0.05) {
    toast("请选择有效的保留片段");
    return;
  }
  if (!window.confirm(`确定只保留 ${start.toFixed(1)}-${end.toFixed(1)} 秒并覆盖样本吗？`)) return;
  await saveEditorEdit({ mode: "keep", startSeconds: start, endSeconds: end });
}

async function saveEditorRemove() {
  if (!editorSampleName || !editorWaveSurfer) return;
  const duration = editorWaveSurfer.getDuration() || 0;
  const segments = getEditorDeleteSegments();
  if (!segments.length) {
    toast("请先标记要删除的样本片段");
    return;
  }
  const totalDelete = segments.reduce((sum, segment) => sum + (segment.end - segment.start), 0);
  if (duration > 0 && duration - totalDelete < 0.05) {
    toast("不能删除整段样本");
    return;
  }
  if (!window.confirm(`确定删除 ${segments.length} 个标记片段并覆盖样本吗？`)) return;
  await saveEditorEdit({ mode: "remove", segments });
}

async function saveEditorEdit(options) {
  const name = editorSampleName;
  try {
    await editLineAudioNoiseSample(editorSampleLabel, name, options);
    toast("样本已保存");
    destroySampleEditor();
    document.getElementById("sampleEditorDialog")?.close?.();
    await loadSamples();
  } catch (err) {
    toast(err.message || "保存样本失败");
  }
}

async function deleteSample(key) {
  const sample = samples.find((item) => sampleKey(item) === key);
  if (!sample) return;
  if (!window.confirm(`确定删除样本 ${sampleLabelText(sample.label)} / ${sample.name} 吗？`)) return;
  try {
    await deleteLineAudioNoiseSample(sample.label, sample.name);
    samples = samples.filter((item) => sampleKey(item) !== key);
    renderSummary();
    renderSamples();
    toast("样本已删除");
  } catch (err) {
    toast(err.message || "删除样本失败");
  }
}

async function loadSamples() {
  const list = document.getElementById("sampleList");
  if (list) list.innerHTML = `<div class="empty-state">正在加载样本...</div>`;
  try {
    const results = await Promise.all(SAMPLE_LABELS.map((label) => fetchLineAudioNoiseSamples(label)));
    samples = results
      .flatMap((data) => Array.isArray(data.samples) ? data.samples : [])
      .sort((left, right) => Number(right.updatedAt || 0) - Number(left.updatedAt || 0));
    renderSummary();
    renderSamples();
  } catch (err) {
    if (list) list.innerHTML = `<div class="empty-state">加载失败：${escapeHtml(err.message || "未知错误")}</div>`;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  renderNav();
  document.getElementById("refreshSamplesBtn")?.addEventListener("click", loadSamples);
  document.getElementById("sampleKeyword")?.addEventListener("input", renderSamples);
  document.getElementById("sampleEditorDialog")?.addEventListener("close", destroySampleEditor);
  document.getElementById("sampleEditorStart")?.addEventListener("change", updateEditorRegionFromInputs);
  document.getElementById("sampleEditorEnd")?.addEventListener("change", updateEditorRegionFromInputs);
  document.getElementById("sampleEditorPlayBtn")?.addEventListener("click", playEditorSelection);
  document.getElementById("sampleEditorMarkDeleteBtn")?.addEventListener("click", markEditorDeleteRegion);
  document.getElementById("sampleEditorClearDeleteBtn")?.addEventListener("click", clearEditorDeleteRegions);
  document.getElementById("sampleEditorKeepSaveBtn")?.addEventListener("click", saveEditorKeep);
  document.getElementById("sampleEditorRemoveSaveBtn")?.addEventListener("click", saveEditorRemove);
  await loadSamples();
});
