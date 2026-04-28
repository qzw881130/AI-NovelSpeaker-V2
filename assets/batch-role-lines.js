import {
  enqueueLineAudio,
  fetchRoleLineAudios,
  fetchRoles,
  getActiveNovelId,
  getData,
  setActiveNovelId,
} from "./store.js";
import { fmtDateTime, incrementNavBadge, renderNav, showPageError, toast } from "./ui.js";
import { localizeDocumentText, translateText } from "./i18n.js";

const PAGE_SIZE = 50;

let allNovels = [];
let activeNovel = null;
let roleItems = [];
let currentSettings = null;
let currentPage = 1;
let currentItems = [];
let currentPageCount = 0;
let currentTotalCount = 0;
const selectedRows = new Map();
let selectedRoleName = "";
let roleFilterKeyword = "";
let roleFilterDropdownShouldStayOpen = false;

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

function getQueueSchedule() {
  const queue = currentSettings?.lineAudioQueue || {};
  const mode = String(queue.mode || "immediate").trim();
  const scheduledAt = String(queue.scheduledAt || "").trim();
  if (mode !== "scheduled" || !scheduledAt) {
    return { scheduledAt: "", label: translateText("立即执行") };
  }
  return {
    scheduledAt,
    label: `${translateText("指定时间执行")} ${fmtDateTime(scheduledAt) || scheduledAt}`,
  };
}

function getSelectedRoleName() {
  return String(selectedRoleName || "").trim();
}

function getSearchKeyword() {
  return String(document.getElementById("batchRoleSearchInput")?.value || "").trim().toLowerCase();
}

function rowKey(item) {
  return `${Number(item.chapterNum || 0)}:${Number(item.lineIndex || 0)}`;
}

function renderNovelSelect() {
  const select = document.getElementById("batchRoleNovelSelect");
  if (!select) return;
  select.innerHTML = allNovels.map((item) => `<option value="${item.id}">${item.name}</option>`).join("");
  if (activeNovel) select.value = String(activeNovel.id);
}

function getAllRoleNames() {
  return roleItems.map((item) => String(item.name || "").trim()).filter(Boolean).sort((a, b) => a.localeCompare(b, "zh-CN"));
}

function openRoleFilterDropdown() {
  roleFilterDropdownShouldStayOpen = true;
  document.getElementById("batchRoleFilterDropdown")?.classList.remove("hidden");
}

function closeRoleFilterDropdown() {
  roleFilterDropdownShouldStayOpen = false;
  document.getElementById("batchRoleFilterDropdown")?.classList.add("hidden");
}

function renderRoleSelect() {
  const tagsEl = document.getElementById("batchRoleFilterTags");
  const inputEl = document.getElementById("batchRoleFilterInput");
  const dropdownEl = document.getElementById("batchRoleFilterDropdown");
  const filterEl = document.getElementById("batchRoleFilter");
  if (!tagsEl || !inputEl || !dropdownEl || !filterEl) return;

  tagsEl.innerHTML = selectedRoleName
    ? `<button class="role-name-chip" data-role-name="${escapeHtml(selectedRoleName)}" type="button">${escapeHtml(selectedRoleName)} <span>×</span></button>`
    : "";
  tagsEl.querySelectorAll(".role-name-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      selectedRoleName = "";
      roleFilterKeyword = "";
      inputEl.value = "";
      renderRoleSelect();
      void loadLines();
    });
  });

  const keyword = String(roleFilterKeyword || "").trim().toLowerCase();
  const names = getAllRoleNames().filter((name) => !keyword || name.toLowerCase().includes(keyword));
  if (!names.length) {
    dropdownEl.innerHTML = `<div class="role-name-filter-empty">${translateText("无匹配角色")}</div>`;
  } else {
    dropdownEl.innerHTML = names.map((name) => {
      const active = selectedRoleName === name;
      return `
        <button class="role-name-option${active ? " active" : ""}" data-role-name="${escapeHtml(name)}" type="button">
          <span>${escapeHtml(name)}</span>
          <span>${active ? translateText("已选") : translateText("选择")}</span>
        </button>
      `;
    }).join("");
  }
  localizeDocumentText(document);

  dropdownEl.querySelectorAll(".role-name-option").forEach((option) => {
    option.addEventListener("mousedown", (event) => {
      event.preventDefault();
    });
    option.addEventListener("click", async () => {
      const name = String(option.dataset.roleName || "").trim();
      if (!name) return;
      selectedRoleName = name;
      roleFilterKeyword = "";
      inputEl.value = "";
      selectedRows.clear();
      currentPage = 1;
      renderRoleSelect();
      closeRoleFilterDropdown();
      await loadLines();
    });
  });

  if (roleFilterDropdownShouldStayOpen || filterEl.contains(document.activeElement)) {
    openRoleFilterDropdown();
  } else {
    closeRoleFilterDropdown();
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text || "");
  return div.innerHTML;
}

function getLineAudioViewState(entry) {
  let statusText = "未生成";
  let statusClass = "text-muted";
  let disabled = false;
  let hasAudio = false;
  let src = "";

  if (entry) {
    if (entry.hasAudio && entry.streamUrl) {
      statusText = "已生成";
      statusClass = "status-completed";
      hasAudio = true;
      const version = String(entry.task?.updatedAt || entry.lineHash || "0");
      src = `${entry.streamUrl}?v=${encodeURIComponent(version)}`;
    } else if (entry.task?.status === "pending") {
      statusText = "待生成";
      statusClass = "status-pending";
      disabled = true;
    } else if (["processing", "running"].includes(String(entry.task?.status || ""))) {
      statusText = "生成中";
      statusClass = "status-processing";
      disabled = true;
    } else if (entry.task?.status === "failed") {
      statusText = "生成失败";
      statusClass = "status-failed";
    } else if (!entry.roleInLibrary) {
      statusText = "角色未加入角色库";
      statusClass = "status-failed";
      disabled = true;
    } else if (!entry.roleHasSampleAudio) {
      statusText = "缺少角色示例音频";
      statusClass = "status-failed";
      disabled = true;
    }
  }
  return { statusText, statusClass, disabled, hasAudio, src };
}

function getFilteredItems() {
  const keyword = getSearchKeyword();
  if (!keyword) return currentItems;
  return currentItems.filter((item) => String(item.rawLine || "").toLowerCase().includes(keyword));
}

function renderSummary() {
  const filteredItems = getFilteredItems();
  document.getElementById("batchRoleLinesSummary").textContent = `结果 ${filteredItems.length} / ${currentTotalCount} 条 · 已选 ${selectedRows.size} 条`;
  document.getElementById("batchRoleLinesPageInfo").textContent = `第 ${currentPageCount ? currentPage : 0} / ${currentPageCount} 页`;
  document.getElementById("batchRoleLinesFirstBtn").disabled = currentPage <= 1;
  document.getElementById("batchRoleLinesPrevBtn").disabled = currentPage <= 1;
  document.getElementById("batchRoleLinesNextBtn").disabled = currentPage >= currentPageCount;
  document.getElementById("batchRoleLinesLastBtn").disabled = currentPage >= currentPageCount;
  const generateAllBtn = document.getElementById("generateAllBatchRoleLinesBtn");
  if (generateAllBtn) {
    generateAllBtn.disabled = !getSelectedRoleName() || currentTotalCount <= 0;
  }
}

function syncSelectAllCheckbox() {
  const visible = getFilteredItems();
  const checkbox = document.getElementById("selectAllPageBatchRoleLinesCheckbox");
  if (!checkbox) return;
  if (!visible.length) {
    checkbox.checked = false;
    checkbox.indeterminate = false;
    checkbox.disabled = true;
    return;
  }
  const selectableItems = visible.filter((item) => !getLineAudioViewState(item).disabled);
  if (!selectableItems.length) {
    checkbox.checked = false;
    checkbox.indeterminate = false;
    checkbox.disabled = true;
    return;
  }
  checkbox.disabled = false;
  const selectedCount = selectableItems.filter((item) => selectedRows.has(rowKey(item))).length;
  checkbox.checked = selectedCount === selectableItems.length;
  checkbox.indeterminate = selectedCount > 0 && selectedCount < selectableItems.length;
}

function renderTable() {
  const body = document.getElementById("batchRoleLinesTableBody");
  if (!body) return;
  const items = getFilteredItems();
  if (!getSelectedRoleName()) {
    body.innerHTML = '<tr><td colspan="6" class="empty-cell">请选择角色</td></tr>';
    renderSummary();
    syncSelectAllCheckbox();
    return;
  }
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty-cell">当前筛选条件下暂无台词</td></tr>';
    renderSummary();
    syncSelectAllCheckbox();
    return;
  }
  body.innerHTML = items.map((item, index) => {
    const key = rowKey(item);
    const view = getLineAudioViewState(item);
    const checked = selectedRows.has(key) ? " checked" : "";
    const selectable = !view.disabled;
    return `
      <tr>
        <td><input class="batch-role-line-check" data-row-key="${escapeHtml(key)}" type="checkbox"${checked}${selectable ? "" : " disabled"} /></td>
        <td>${String((currentPage - 1) * PAGE_SIZE + index + 1).padStart(3, "0")}</td>
        <td>第${Number(item.chapterNum || 0)}回 ${escapeHtml(item.chapterTitle || "")}</td>
        <td class="batch-role-line-text">${escapeHtml(item.rawLine || "")}</td>
        <td>
          <div class="juben-line-audio">
            ${view.hasAudio ? `<audio controls preload="metadata" src="${escapeHtml(view.src)}"></audio>` : ""}
            <span class="${view.statusClass}">${escapeHtml(view.statusText)}</span>
          </div>
        </td>
        <td><button class="ghost-btn btn-sm batch-role-line-generate-btn" data-row-key="${escapeHtml(key)}" type="button"${view.disabled ? " disabled" : ""}>生成音频</button></td>
      </tr>
    `;
  }).join("");
  body.querySelectorAll(".batch-role-line-check").forEach((el) => {
    el.addEventListener("change", () => {
      const item = items.find((entry) => rowKey(entry) === String(el.dataset.rowKey || ""));
      if (!item) return;
      if (el.checked) selectedRows.set(rowKey(item), item);
      else selectedRows.delete(rowKey(item));
      renderSummary();
      syncSelectAllCheckbox();
    });
  });
  body.querySelectorAll(".batch-role-line-generate-btn").forEach((el) => {
    el.addEventListener("click", async () => {
      const item = items.find((entry) => rowKey(entry) === String(el.dataset.rowKey || ""));
      if (!item) return;
      await generateOne(item);
    });
  });
  renderSummary();
  syncSelectAllCheckbox();
  localizeDocumentText(document);
}

function setPageStatus(message, kind = "") {
  const el = document.getElementById("batchRoleLinesStatus");
  if (!el) return;
  el.className = `roles-page-status${kind ? ` ${kind}` : ""}`;
  el.textContent = message;
  el.classList.toggle("hidden", !message);
}

async function generateOne(item) {
  try {
    const schedule = getQueueSchedule();
    await enqueueLineAudio(item.novelId, item.chapterNum, item.lineIndex, {
      scheduledAt: schedule.scheduledAt,
    });
    selectedRows.delete(rowKey(item));
    incrementNavBadge("lineAudio", 1);
    renderNav();
    toast(schedule.label);
    await loadLines({ keepPage: true });
  } catch (err) {
    toast(err.message);
  }
}

async function generateSelected() {
  const selected = Array.from(selectedRows.values());
  if (!selected.length) {
    toast("请先选择台词");
    return;
  }
  const schedule = getQueueSchedule();
  let queued = 0;
  const failed = [];
  for (const item of selected) {
    if (getLineAudioViewState(item).disabled) continue;
    try {
      await enqueueLineAudio(item.novelId, item.chapterNum, item.lineIndex, {
        scheduledAt: schedule.scheduledAt,
      });
      selectedRows.delete(rowKey(item));
      queued += 1;
    } catch (err) {
      failed.push(`第${item.chapterNum}回#${item.lineNo}: ${err.message}`);
    }
  }
  if (queued > 0) {
    incrementNavBadge("lineAudio", queued);
    renderNav();
  }
  if (failed.length) {
    setPageStatus(`已入队 ${queued} 条，失败 ${failed.length} 条`, "error");
    toast(`已入队 ${queued} 条，失败 ${failed.length} 条`);
  } else {
    setPageStatus(`已入队 ${queued} 条台词`, "success");
    toast(`已入队 ${queued} 条台词`);
  }
  await loadLines({ keepPage: true });
}

async function generateAll() {
  if (!activeNovel) return;
  const roleName = getSelectedRoleName();
  if (!roleName) {
    toast("请先选择角色");
    return;
  }
  const schedule = getQueueSchedule();
  let page = 1;
  let pageCount = 1;
  let queued = 0;
  const failed = [];
  do {
    const data = await fetchRoleLineAudios(activeNovel.id, roleName, {
      page,
      pageSize: PAGE_SIZE,
    });
    pageCount = Number(data.pageCount || 0);
    for (const item of data.items || []) {
      if (getLineAudioViewState(item).disabled) continue;
      try {
        await enqueueLineAudio(item.novelId, item.chapterNum, item.lineIndex, {
          scheduledAt: schedule.scheduledAt,
        });
        selectedRows.delete(rowKey(item));
        queued += 1;
      } catch (err) {
        failed.push(`第${item.chapterNum}回#${item.lineNo}: ${err.message}`);
      }
    }
    page += 1;
  } while (page <= pageCount);

  if (queued > 0) {
    incrementNavBadge("lineAudio", queued);
    renderNav();
  }
  if (failed.length) {
    setPageStatus(`已入队 ${queued} 条，失败 ${failed.length} 条`, "error");
    toast(`已入队 ${queued} 条，失败 ${failed.length} 条`);
  } else {
    setPageStatus(`已为角色 ${roleName} 入队 ${queued} 条台词`, "success");
    toast(`已为角色 ${roleName} 入队 ${queued} 条台词`);
  }
  await loadLines({ keepPage: true });
}

async function loadLines(options = {}) {
  if (!activeNovel) return;
  const roleName = getSelectedRoleName();
  if (!roleName) {
    currentItems = [];
    currentPageCount = 0;
    currentTotalCount = 0;
    renderTable();
    return;
  }
  const data = await fetchRoleLineAudios(activeNovel.id, roleName, {
    page: options.keepPage ? currentPage : 1,
    pageSize: PAGE_SIZE,
  });
  currentPage = Number(data.page || 1);
  currentItems = data.items || [];
  currentPageCount = Number(data.pageCount || 0);
  currentTotalCount = Number(data.totalCount || 0);
  for (const item of currentItems) {
    if (selectedRows.has(rowKey(item))) {
      selectedRows.set(rowKey(item), item);
    }
  }
  renderTable();
}

async function loadRoles() {
  if (!activeNovel) return;
  const result = await fetchRoles(activeNovel.id);
  roleItems = result.roles || [];
  if (selectedRoleName && !roleItems.some((item) => String(item.name || "").trim() === selectedRoleName)) {
    selectedRoleName = "";
  }
  renderRoleSelect();
}

function setHeader() {
  if (!activeNovel) return;
  document.getElementById("batchRoleLinesPageTitle").textContent = `${activeNovel.name} - 批量生成台词`;
}

async function switchNovel(novelId) {
  activeNovel = allNovels.find((item) => String(item.id) === String(novelId)) || allNovels[0] || null;
  if (!activeNovel) return;
  setActiveNovelId(activeNovel.id);
  selectedRows.clear();
  currentPage = 1;
  setHeader();
  renderNovelSelect();
  await loadRoles();
  await loadLines();
}

function bindEvents() {
  document.getElementById("batchRoleNovelSelect")?.addEventListener("change", async (event) => {
    await switchNovel(event.target.value);
  });
  const roleFilterInputEl = document.getElementById("batchRoleFilterInput");
  roleFilterInputEl?.addEventListener("focus", () => {
    roleFilterDropdownShouldStayOpen = false;
    renderRoleSelect();
    openRoleFilterDropdown();
  });
  roleFilterInputEl?.addEventListener("input", () => {
    roleFilterKeyword = String(roleFilterInputEl.value || "");
    renderRoleSelect();
    openRoleFilterDropdown();
  });
  roleFilterInputEl?.addEventListener("blur", () => {
    window.setTimeout(() => {
      const filterEl = document.getElementById("batchRoleFilter");
      if (!filterEl?.contains(document.activeElement) && !roleFilterDropdownShouldStayOpen) {
        closeRoleFilterDropdown();
      }
    }, 120);
  });
  document.getElementById("batchRoleSearchInput")?.addEventListener("input", () => {
    renderTable();
  });
  document.getElementById("clearBatchRoleSearchBtn")?.addEventListener("click", () => {
    document.getElementById("batchRoleSearchInput").value = "";
    renderTable();
  });
  document.getElementById("refreshBatchRoleLinesBtn")?.addEventListener("click", async () => {
    await loadRoles();
    await loadLines({ keepPage: true });
    toast("已刷新");
  });
  document.getElementById("batchRoleLinesPrevBtn")?.addEventListener("click", async () => {
    if (currentPage <= 1) return;
    currentPage -= 1;
    await loadLines({ keepPage: true });
  });
  document.getElementById("batchRoleLinesFirstBtn")?.addEventListener("click", async () => {
    if (currentPage <= 1) return;
    currentPage = 1;
    await loadLines({ keepPage: true });
  });
  document.getElementById("batchRoleLinesNextBtn")?.addEventListener("click", async () => {
    if (currentPage >= currentPageCount) return;
    currentPage += 1;
    await loadLines({ keepPage: true });
  });
  document.getElementById("batchRoleLinesLastBtn")?.addEventListener("click", async () => {
    if (currentPage >= currentPageCount) return;
    currentPage = currentPageCount;
    await loadLines({ keepPage: true });
  });
  document.getElementById("selectPageBatchRoleLinesBtn")?.addEventListener("click", () => {
    for (const item of getFilteredItems()) {
      if (!getLineAudioViewState(item).disabled) {
        selectedRows.set(rowKey(item), item);
      }
    }
    renderTable();
  });
  document.getElementById("clearSelectedBatchRoleLinesBtn")?.addEventListener("click", () => {
    selectedRows.clear();
    renderTable();
  });
  document.getElementById("generateSelectedBatchRoleLinesBtn")?.addEventListener("click", async () => {
    await generateSelected();
  });
  document.getElementById("generateAllBatchRoleLinesBtn")?.addEventListener("click", async () => {
    const roleName = getSelectedRoleName();
    if (!roleName) {
      toast("请先选择角色");
      return;
    }
    if (!window.confirm(`确认将角色「${roleName}」的全部台词加入生成队列吗？`)) {
      return;
    }
    await generateAll();
  });
  document.getElementById("selectAllPageBatchRoleLinesCheckbox")?.addEventListener("change", (event) => {
    if (event.target.checked) {
      for (const item of getFilteredItems()) {
        if (!getLineAudioViewState(item).disabled) {
          selectedRows.set(rowKey(item), item);
        }
      }
    } else {
      for (const item of getFilteredItems()) {
        selectedRows.delete(rowKey(item));
      }
    }
    renderTable();
  });
  document.addEventListener("click", (event) => {
    const filterEl = document.getElementById("batchRoleFilter");
    if (filterEl && !filterEl.contains(event.target)) {
      closeRoleFilterDropdown();
    }
  });
}

async function init() {
  renderNav();
  const data = await getData();
  currentSettings = data.settings || {};
  allNovels = data.novels || [];
  activeNovel = getNovelByQueryOrActive();
  if (!activeNovel) {
    throw new Error("未找到小说");
  }
  renderNovelSelect();
  setHeader();
  bindEvents();
  await loadRoles();
  await loadLines();
  localizeDocumentText(document);
}

init().catch((err) => {
  showPageError(err, "页面初始化失败");
});
