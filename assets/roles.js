import {
  getData,
  getActiveNovelId,
  setActiveNovelId,
  fetchRoles,
  fetchNovelChapters,
  fetchChapterJsonOutput,
  createRole,
  updateRole,
  updateRoleLevel,
  duplicateRole,
  deleteRole,
  uploadRoleSampleAudio,
  generateRoleSampleAudio,
} from "./store.js";
import { renderNav, toast } from "./ui.js";
import { localizeDocumentText, t, translateText } from "./i18n.js";

let allNovels = [];
let activeNovel = null;
let roleItems = [];
let roleModalMode = "create";
let editingRoleId = null;
let roleAudioBase64 = "";
let chapterItems = [];
let chapterRoleNamesCache = new Map();
let roleNameDropdownShouldStayOpen = false;

const rolesFilterState = {
  chapter: "all",
  sample: "all",
  level: "all",
  names: new Set(),
  keyword: "",
};

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
  document.getElementById("rolesPageTitle").textContent = `${novel.name} - ${translateText("角色库")}`;
}

function renderNovelSelect() {
  const select = document.getElementById("rolesNovelSelect");
  select.innerHTML = allNovels.map((n) => `<option value="${n.id}">${n.name}</option>`).join("");
  if (activeNovel) select.value = String(activeNovel.id);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function renderRoleStats(stats) {
  document.getElementById("roleTotalCount").textContent = String(stats?.total || 0);
  document.getElementById("roleLevel1Count").textContent = String(stats?.level_1 || 0);
  document.getElementById("roleLevel2Count").textContent = String(stats?.level_2 || 0);
  document.getElementById("roleLevel3Count").textContent = String(stats?.level_3 || 0);
  document.getElementById("roleNoSampleCount").textContent = String(stats?.without_sample || 0);
}

function parseRoleNamesFromJsonText(jsonText) {
  const text = String(jsonText || "").trim();
  if (!text) return [];
  try {
    const parsed = JSON.parse(text);
    const roleList = Array.isArray(parsed?.role_list) ? parsed.role_list : [];
    return Array.from(
      new Set(
        roleList
          .map((item) => String(item?.name || "").trim())
          .filter(Boolean)
      )
    );
  } catch {
    return [];
  }
}

async function getSelectedChapterRoleNames() {
  if (!activeNovel || rolesFilterState.chapter === "all") {
    return null;
  }
  const chapterNum = Number(rolesFilterState.chapter);
  if (!Number.isFinite(chapterNum) || chapterNum <= 0) {
    return null;
  }
  if (chapterRoleNamesCache.has(chapterNum)) {
    return chapterRoleNamesCache.get(chapterNum) || [];
  }
  try {
    const output = await fetchChapterJsonOutput(activeNovel.id, chapterNum);
    const names = parseRoleNamesFromJsonText(output?.jsonText);
    chapterRoleNamesCache.set(chapterNum, names);
    return names;
  } catch {
    chapterRoleNamesCache.set(chapterNum, []);
    return [];
  }
}

function renderChapterFilter() {
  const select = document.getElementById("chapterFilterSelect");
  if (!select) return;
  const options = [`<option value="all">${translateText("全部")}</option>`];
  const validChapterValues = new Set(["all"]);
  for (const chapter of chapterItems) {
    const chapterNum = Number(chapter.chapterNum || 0);
    const title = String(chapter.title || `#${chapterNum}`).trim();
    validChapterValues.add(String(chapterNum));
    options.push(`<option value="${chapterNum}">${escapeHtml(title)}</option>`);
  }
  select.innerHTML = options.join("");
  if (!validChapterValues.has(String(rolesFilterState.chapter))) {
    rolesFilterState.chapter = "all";
  }
  select.value = rolesFilterState.chapter;
  localizeDocumentText(document);
}

function getChapterFilteredRoleItems() {
  if (rolesFilterState.chapter === "all") {
    return roleItems;
  }
  const chapterNum = Number(rolesFilterState.chapter);
  const chapterRoleNames = chapterRoleNamesCache.get(chapterNum) || [];
  if (!chapterRoleNames.length) {
    return [];
  }
  const chapterRoleSet = new Set(chapterRoleNames);
  return roleItems.filter((role) => chapterRoleSet.has(String(role.name || "").trim()));
}

function getAllRoleNames() {
  return Array.from(new Set(getChapterFilteredRoleItems().map((item) => String(item.name || "").trim()).filter(Boolean))).sort((a, b) => a.localeCompare(b, "zh-CN"));
}

function getFilteredRoleItems() {
  return getChapterFilteredRoleItems().filter((role) => {
    const hasAudio = Boolean(String(role.sampleAudioPath || "").trim());
    if (rolesFilterState.sample === "with" && !hasAudio) {
      return false;
    }
    if (rolesFilterState.sample === "without" && hasAudio) {
      return false;
    }
    if (rolesFilterState.level !== "all" && String(role.roleLevel || "") !== rolesFilterState.level) {
      return false;
    }
    if (rolesFilterState.names.size > 0 && !rolesFilterState.names.has(String(role.name || "").trim())) {
      return false;
    }
    return true;
  });
}

function getRolesMissingSampleAudio() {
  return roleItems.filter((role) => !String(role.sampleAudioPath || "").trim());
}

function renderRoleNameFilter() {
  const roleNameFilterTagsEl = document.getElementById("roleNameFilterTags");
  const roleNameFilterDropdownEl = document.getElementById("roleNameFilterDropdown");
  const roleNameFilterInputEl = document.getElementById("roleNameFilterInput");
  const roleNameFilterEl = document.getElementById("roleNameFilter");

  const selected = Array.from(rolesFilterState.names);
  roleNameFilterTagsEl.innerHTML = selected.map((name) => `
    <button class="role-name-chip" data-role-name="${escapeHtml(name)}" type="button">
      ${escapeHtml(name)} <span>×</span>
    </button>
  `).join("");

  for (const chip of roleNameFilterTagsEl.querySelectorAll(".role-name-chip")) {
    chip.addEventListener("click", () => {
      rolesFilterState.names.delete(String(chip.dataset.roleName || ""));
      renderRoleNameFilter();
      renderRolesTable();
    });
  }

  const keyword = String(rolesFilterState.keyword || "").trim().toLowerCase();
  const names = getAllRoleNames().filter((name) => !keyword || name.toLowerCase().includes(keyword));
  if (!names.length) {
    roleNameFilterDropdownEl.innerHTML = `<div class="role-name-filter-empty">${translateText("无匹配角色")}</div>`;
  } else {
    roleNameFilterDropdownEl.innerHTML = names.map((name) => {
      const active = rolesFilterState.names.has(name);
      return `
        <button class="role-name-option${active ? " active" : ""}" data-role-name="${escapeHtml(name)}" type="button">
          <span>${escapeHtml(name)}</span>
          <span>${active ? translateText("已选") : translateText("选择")}</span>
        </button>
      `;
    }).join("");
  }
  localizeDocumentText(document);

  for (const option of roleNameFilterDropdownEl.querySelectorAll(".role-name-option")) {
    option.addEventListener("mousedown", (event) => {
      event.preventDefault();
    });
    option.addEventListener("click", () => {
      const name = String(option.dataset.roleName || "");
      if (!name) return;
      roleNameDropdownShouldStayOpen = true;
      if (rolesFilterState.names.has(name)) {
        rolesFilterState.names.delete(name);
      } else {
        rolesFilterState.names.add(name);
      }
      roleNameFilterInputEl.focus();
      renderRoleNameFilter();
      openRoleNameFilterDropdown();
      renderRolesTable();
    });
  }

  if (roleNameDropdownShouldStayOpen || roleNameFilterEl.contains(document.activeElement)) {
    openRoleNameFilterDropdown();
  } else {
    closeRoleNameFilterDropdown();
  }
}

function openRoleNameFilterDropdown() {
  roleNameDropdownShouldStayOpen = true;
  document.getElementById("roleNameFilterDropdown").classList.remove("hidden");
}

function closeRoleNameFilterDropdown() {
  roleNameDropdownShouldStayOpen = false;
  document.getElementById("roleNameFilterDropdown").classList.add("hidden");
}

function setRolesPageStatus(text, isError = false) {
  const el = document.getElementById("rolesPageStatus");
  el.textContent = text;
  el.className = "roles-page-status" + (isError ? " error" : text ? " success" : "");
  if (text) {
    setTimeout(() => {
      el.textContent = "";
      el.className = "roles-page-status";
    }, 3000);
  }
}

function roleLevelOptions(value) {
  const current = Number(value || 3);
  return [
    { value: 1, label: translateText("一等角色") },
    { value: 2, label: translateText("二等角色") },
    { value: 3, label: translateText("三等角色") },
  ]
    .map((item) => `<option value="${item.value}"${item.value === current ? " selected" : ""}>${item.label}</option>`)
    .join("");
}

function buildSampleCell(role) {
  const hasAudio = String(role.sampleAudioPath || "").trim();
  const source = String(role.sampleAudioSource || "").trim();
  const sourceIcon = hasAudio
    ? `<span class="role-sample-source role-sample-source-${escapeHtml(source || "unknown")}" title="${source === "uploaded" ? translateText("本地上传") : source === "generated" ? translateText("AI生成") : translateText("未知来源")}">${source === "uploaded" ? "↑" : source === "generated" ? "AI" : "?"}</span>`
    : "";
  const parts = [];
  if (hasAudio) {
    const cacheKey = encodeURIComponent(String(role.updatedAt || role.sampleAudioPath || "0"));
    parts.push('<div class="role-sample-main">');
    parts.push(`<audio class="role-sample-player" controls preload="metadata" src="/api/novels/${activeNovel.id}/roles/${role.id}/sample?v=${cacheKey}"></audio>`);
    parts.push(sourceIcon);
    parts.push(`
      <div class="role-sample-actions">
        <button class="ghost-btn btn-sm generate-sample-btn" data-role-id="${role.id}" type="button">${translateText("重新生成")}</button>
        <input class="role-upload-input hidden" data-role-id="${role.id}" type="file" accept="audio/*,.flac,.wav,.mp3,.m4a,.aac" />
        <button class="ghost-btn btn-sm upload-sample-btn" data-role-id="${role.id}" type="button">${translateText("本地上传")}</button>
        <button class="ghost-btn btn-sm extract-text-btn" data-role-id="${role.id}" type="button">${translateText("提取声音文本")}</button>
      </div>
    `);
    parts.push('</div>');
  } else {
    parts.push(`<span class="text-muted">${translateText("未生成")}</span>`);
    parts.push(`
      <div class="role-sample-actions">
        <button class="ghost-btn btn-sm generate-sample-btn" data-role-id="${role.id}" type="button">${translateText("生成示例")}</button>
        <input class="role-upload-input hidden" data-role-id="${role.id}" type="file" accept="audio/*,.flac,.wav,.mp3,.m4a,.aac" />
        <button class="ghost-btn btn-sm upload-sample-btn" data-role-id="${role.id}" type="button">${translateText("本地上传")}</button>
        <button class="ghost-btn btn-sm extract-text-btn" data-role-id="${role.id}" type="button" disabled>${translateText("提取声音文本")}</button>
      </div>
    `);
  }
  return `<div class="role-sample-cell">${parts.join("")}</div>`;
}

function buildRoleJson(role) {
  return JSON.stringify(
    {
      name: String(role.name || "").trim(),
      instruct: String(role.instruct || "").trim(),
      text: String(role.sampleText || "").trim(),
    },
    null,
    2
  );
}

function openRoleJsonModal(role) {
  document.getElementById("roleJsonModalContent").value = buildRoleJson(role);
  document.querySelector('#roleJsonModal h3').textContent = translateText("角色JSON");
  document.getElementById("copyRoleJsonBtn").textContent = translateText("复制JSON");
  document.getElementById("roleJsonModal").showModal();
  localizeDocumentText(document);
}

function renderRolesTable() {
  const tbody = document.getElementById("rolesPageTableBody");
  const countEl = document.getElementById("rolesFilteredCount");
  tbody.innerHTML = "";

  const items = getFilteredRoleItems();
  if (countEl) {
    countEl.textContent = `${translateText("结果")} ${items.length} ${translateText("条")}`;
  }
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">${translateText("暂无角色数据")}</td></tr>`;
    localizeDocumentText(document);
    return;
  }

  for (const role of items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input class="role-page-input" data-role-id="${role.id}" data-field="name" value="${escapeHtml(role.name || "")}" /></td>
      <td><textarea class="role-page-textarea" data-role-id="${role.id}" data-field="instruct" rows="2">${escapeHtml(role.instruct || "")}</textarea></td>
      <td><textarea class="role-page-textarea" data-role-id="${role.id}" data-field="sampleText" rows="2">${escapeHtml(role.sampleText || "")}</textarea></td>
      <td>${buildSampleCell(role)}</td>
      <td>
        <select class="role-level-select" data-role-id="${role.id}">
          ${roleLevelOptions(role.roleLevel)}
        </select>
      </td>
      <td>
        <div class="role-row-actions">
          <button class="ghost-btn btn-sm role-json-btn" data-role-id="${role.id}" type="button" title="${translateText("复制角色JSON")}" aria-label="${translateText("复制角色JSON")}">{ }</button>
          <button class="ghost-btn btn-sm save-role-btn" data-role-id="${role.id}" type="button">${translateText("保存")}</button>
          <button class="ghost-btn btn-sm duplicate-role-btn" data-role-id="${role.id}" type="button">${translateText("复制")}</button>
          <button class="ghost-btn btn-sm danger delete-role-btn" data-role-id="${role.id}" data-role-name="${escapeHtml(role.name || "")}" type="button">${translateText("删除")}</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  }

  for (const btn of tbody.querySelectorAll(".role-json-btn")) {
    btn.addEventListener("click", () => {
      const roleId = Number(btn.dataset.roleId || 0);
      const role = roleItems.find((item) => Number(item.id) === roleId);
      if (!role) return;
      openRoleJsonModal(role);
    });
  }

  // 绑定保存按钮
  for (const btn of tbody.querySelectorAll(".save-role-btn")) {
    btn.addEventListener("click", async () => {
      const roleId = Number(btn.dataset.roleId || 0);
      const nameEl = tbody.querySelector(`.role-page-input[data-role-id="${roleId}"][data-field="name"]`);
      const instructEl = tbody.querySelector(`.role-page-textarea[data-role-id="${roleId}"][data-field="instruct"]`);
      const sampleTextEl = tbody.querySelector(`.role-page-textarea[data-role-id="${roleId}"][data-field="sampleText"]`);
      const name = String(nameEl?.value || "").trim();
      const instruct = String(instructEl?.value || "").trim();
      const sampleText = String(sampleTextEl?.value || "").trim();

      if (!name) {
        setRolesPageStatus(translateText("角色名不能为空"), true);
        return;
      }

      btn.disabled = true;
      try {
        const result = await updateRole(activeNovel.id, roleId, { name, instruct, sampleText });
        const idx = roleItems.findIndex((r) => r.id === roleId);
        if (idx >= 0) {
          roleItems[idx] = result.role || roleItems[idx];
        }
        setRolesPageStatus(`${translateText("已保存")}: ${result.role?.name || translateText("角色")}`);
        renderRoleStats({
          total: roleItems.length,
          level_1: roleItems.filter((item) => Number(item.roleLevel) === 1).length,
          level_2: roleItems.filter((item) => Number(item.roleLevel) === 2).length,
          level_3: roleItems.filter((item) => Number(item.roleLevel) === 3).length,
          without_sample: roleItems.filter((item) => !String(item.sampleAudioPath || "").trim()).length,
        });
        renderRolesTable();
      } catch (err) {
        setRolesPageStatus(err.message || translateText("保存角色失败"), true);
      } finally {
        btn.disabled = false;
      }
    });
  }

  // 绑定复制按钮
  for (const btn of tbody.querySelectorAll(".duplicate-role-btn")) {
    btn.addEventListener("click", async () => {
      const roleId = Number(btn.dataset.roleId || 0);
      btn.disabled = true;
      try {
        const result = await duplicateRole(activeNovel.id, roleId);
        roleItems.push(result.role);
        setRolesPageStatus(`${translateText("已复制角色为")} ${result.role?.name || translateText("新角色")}`);
        await refreshRolesPage();
      } catch (err) {
        setRolesPageStatus(err.message || translateText("复制角色失败"), true);
      } finally {
        btn.disabled = false;
      }
    });
  }

  // 绑定删除按钮
  for (const btn of tbody.querySelectorAll(".delete-role-btn")) {
    btn.addEventListener("click", async () => {
      const roleId = Number(btn.dataset.roleId || 0);
      const roleName = String(btn.dataset.roleName || translateText("角色"));
      if (!window.confirm(`${translateText("确定删除")}: ${roleName}?`)) return;

      btn.disabled = true;
      try {
        await deleteRole(activeNovel.id, roleId);
        roleItems = roleItems.filter((item) => item.id !== roleId);
        renderRoleStats({
          total: roleItems.length,
          level_1: roleItems.filter((item) => Number(item.roleLevel) === 1).length,
          level_2: roleItems.filter((item) => Number(item.roleLevel) === 2).length,
          level_3: roleItems.filter((item) => Number(item.roleLevel) === 3).length,
          without_sample: roleItems.filter((item) => !String(item.sampleAudioPath || "").trim()).length,
        });
        renderRolesTable();
        setRolesPageStatus(`${translateText("已删除")}: ${roleName}`);
      } catch (err) {
        setRolesPageStatus(err.message || translateText("删除角色失败"), true);
      } finally {
        btn.disabled = false;
      }
    });
  }

  // 绑定级别选择
  for (const selectEl of tbody.querySelectorAll(".role-level-select")) {
    selectEl.addEventListener("change", async () => {
      const roleId = Number(selectEl.dataset.roleId || 0);
      const roleLevel = Number(selectEl.value || 3);
      const previous = roleItems.find((item) => item.id === roleId)?.roleLevel || 3;
      selectEl.disabled = true;
      try {
        const result = await updateRoleLevel(activeNovel.id, roleId, roleLevel);
        const idx = roleItems.findIndex((r) => r.id === roleId);
        if (idx >= 0) {
          roleItems[idx] = result.role || roleItems[idx];
        }
        setRolesPageStatus(`${translateText("已更新")}: ${result.role?.name || translateText("角色")} ${translateText("级别")}`);
        await refreshRolesPage();
      } catch (err) {
        selectEl.value = String(previous);
        setRolesPageStatus(err.message || translateText("保存角色级别失败"), true);
      } finally {
        selectEl.disabled = false;
      }
    });
  }

  // 绑定生成示例按钮
  for (const btn of tbody.querySelectorAll(".generate-sample-btn")) {
    btn.addEventListener("click", async () => {
      const roleId = Number(btn.dataset.roleId || 0);
      if (!roleId) return;
      btn.disabled = true;
      const previousText = btn.textContent;
      btn.textContent = translateText("生成中...");
      try {
        const data = await generateRoleSampleAudio(activeNovel.id, roleId);
        const idx = roleItems.findIndex((r) => r.id === roleId);
        if (idx >= 0) {
          roleItems[idx] = data.role || roleItems[idx];
        }
        setRolesPageStatus(`${translateText("已生成")}: ${data.role?.name || translateText("角色")} ${translateText("声音示例")}`);
        renderRolesTable();
      } catch (err) {
        setRolesPageStatus(err.message || translateText("生成示例失败"), true);
      } finally {
        btn.disabled = false;
        btn.textContent = previousText;
      }
    });
  }

  // 绑定提取声音文本按钮
  for (const btn of tbody.querySelectorAll(".extract-text-btn")) {
    btn.addEventListener("click", async () => {
      const roleId = Number(btn.dataset.roleId || 0);
      if (!roleId || btn.disabled) return;
      btn.disabled = true;
      const previousText = btn.textContent;
      btn.textContent = translateText("提取中...");
      try {
        const res = await fetch(`/api/novels/${activeNovel.id}/roles/${roleId}/extract-sample-text`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.error || translateText("提取声音文本失败"));
        }
        openRoleTextModal(data.text || "");
        setRolesPageStatus(translateText("已提取声音文本"));
      } catch (err) {
        setRolesPageStatus(err.message || translateText("提取声音文本失败"), true);
      } finally {
        btn.disabled = false;
        btn.textContent = previousText;
      }
    });
  }

  // 绑定上传按钮
  for (const btn of tbody.querySelectorAll(".upload-sample-btn")) {
    btn.addEventListener("click", () => {
      const roleId = Number(btn.dataset.roleId || 0);
      const inputEl = tbody.querySelector(`.role-upload-input[data-role-id="${roleId}"]`);
      inputEl?.click();
    });
  }

  // 绑定文件选择
  for (const inputEl of tbody.querySelectorAll(".role-upload-input")) {
    inputEl.addEventListener("change", async () => {
      const roleId = Number(inputEl.dataset.roleId || 0);
      const file = inputEl.files && inputEl.files[0];
      if (!roleId || !file) return;

      const uploadBtn = tbody.querySelector(`.upload-sample-btn[data-role-id="${roleId}"]`);
      const previousText = uploadBtn?.textContent || translateText("本地上传");
      if (uploadBtn) {
        uploadBtn.disabled = true;
        uploadBtn.textContent = translateText("上传中...");
      }

      try {
        const base64 = await fileToBase64(file);
        const result = await uploadRoleSampleAudio(activeNovel.id, roleId, base64, "uploaded");
        const idx = roleItems.findIndex((r) => r.id === roleId);
        if (idx >= 0) {
          roleItems[idx] = result.role || roleItems[idx];
        }
        setRolesPageStatus(`${translateText("已上传")}: ${result.role?.name || translateText("角色")} ${translateText("声音示例")}`);
        renderRolesTable();
      } catch (err) {
        setRolesPageStatus(err.message || translateText("上传声音示例失败"), true);
      } finally {
        inputEl.value = "";
        if (uploadBtn) {
          uploadBtn.disabled = false;
          uploadBtn.textContent = previousText;
        }
      }
    });
  }
  localizeDocumentText(document);
}

async function refreshRolesPage() {
  if (!activeNovel) return;
  try {
    const [result, chapters] = await Promise.all([
      fetchRoles(activeNovel.id),
      fetchNovelChapters(activeNovel.id),
    ]);
    roleItems = result.roles || [];
    chapterItems = chapters || [];
    chapterRoleNamesCache = new Map();
    const withoutSample = roleItems.filter((item) => !String(item.sampleAudioPath || "").trim()).length;
    renderRoleStats({ ...(result.stats || {}), without_sample: withoutSample });
    renderChapterFilter();
    if (rolesFilterState.chapter !== "all") {
      await getSelectedChapterRoleNames();
    }
    renderRoleNameFilter();
    renderRolesTable();
  } catch (err) {
    setRolesPageStatus(t("error.loadFailed", { msg: err.message }), true);
  }
}

function openRoleModal(mode, roleId = null) {
  const modal = document.getElementById("roleModal");
  const form = document.getElementById("roleForm");
  roleModalMode = mode;
  editingRoleId = roleId;
  roleAudioBase64 = "";

  if (mode === "create") {
    document.getElementById("roleModalTitle").textContent = translateText("添加角色");
    form.name.value = "";
    form.instruct.value = "";
    form.sampleText.value = "";
    document.getElementById("roleAudioPlayer").classList.add("hidden");
    document.getElementById("roleAudioStatus").textContent = "";
  } else {
    const role = roleItems.find((r) => r.id === roleId);
    if (!role) return;
    document.getElementById("roleModalTitle").textContent = translateText("编辑角色");
    form.name.value = role.name || "";
    form.instruct.value = role.instruct || "";
    form.sampleText.value = role.sampleText || "";
    if (role.sampleAudioPath) {
      document.getElementById("roleAudioPlayer").src = `/api/novels/${activeNovel.id}/roles/${role.id}/sample`;
      document.getElementById("roleAudioPlayer").classList.remove("hidden");
    } else {
      document.getElementById("roleAudioPlayer").classList.add("hidden");
    }
  }
  modal.showModal();
  localizeDocumentText(document);
}

async function saveRoleFromForm() {
  const form = document.getElementById("roleForm");
  const input = {
    name: form.name.value.trim(),
    instruct: form.instruct.value.trim(),
    sampleText: form.sampleText.value.trim(),
  };
  if (!input.name) {
    toast(translateText("角色名不能为空"));
    return;
  }
  try {
    if (roleModalMode === "create") {
      const result = await createRole(activeNovel.id, input);
      if (roleAudioBase64 && result?.role?.id) {
        await uploadRoleSampleAudio(activeNovel.id, result.role.id, roleAudioBase64, "uploaded");
      }
      roleItems.push(result.role);
      toast(translateText("角色已创建"));
    } else {
      const result = await updateRole(activeNovel.id, editingRoleId, input);
      if (roleAudioBase64) {
        await uploadRoleSampleAudio(activeNovel.id, editingRoleId, roleAudioBase64, "uploaded");
      }
      const idx = roleItems.findIndex((r) => r.id === editingRoleId);
      if (idx >= 0) {
        roleItems[idx] = result.role || roleItems[idx];
      }
      toast(translateText("角色已更新"));
    }
    document.getElementById("roleModal").close();
    await refreshRolesPage();
  } catch (err) {
    toast(err.message);
  }
}

function bindActions() {
  document.getElementById("refreshRolesBtn").addEventListener("click", async () => {
    setRolesPageStatus("");
    try {
      await refreshRolesPage();
      setRolesPageStatus(translateText("角色列表已刷新"));
    } catch (err) {
      setRolesPageStatus(translateText("刷新角色列表失败"), true);
    }
  });

  document.getElementById("createRoleBtn").addEventListener("click", () => {
    openRoleModal("create");
  });

  document.getElementById("generateMissingSamplesBtn").addEventListener("click", async () => {
    if (!activeNovel) return;
    const missingRoles = getRolesMissingSampleAudio();
    if (!missingRoles.length) {
      toast(translateText("当前没有缺失声音示例的角色"));
      return;
    }
    const btn = document.getElementById("generateMissingSamplesBtn");
    btn.disabled = true;
    try {
      let queuedCount = 0;
      for (const role of missingRoles) {
        await generateRoleSampleAudio(activeNovel.id, role.id);
        queuedCount += 1;
      }
      setRolesPageStatus(`${translateText("缺失声音示例已加入生成队列")}: ${queuedCount}`);
      await refreshRolesPage();
    } catch (err) {
      setRolesPageStatus(err.message || translateText("生成示例失败"), true);
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("roleCancelBtn").addEventListener("click", () => {
    document.getElementById("roleModal").close();
  });

  document.getElementById("roleForm").addEventListener("submit", (e) => {
    e.preventDefault();
    saveRoleFromForm();
  });

  document.getElementById("uploadRoleAudioBtn").addEventListener("click", () => {
    document.getElementById("roleAudioFile").click();
  });

  document.getElementById("roleAudioFile").addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const base64 = await fileToBase64(file);
      roleAudioBase64 = base64;
      document.getElementById("roleAudioStatus").textContent = `${translateText("已选择")}: ${file.name}`;
    } catch (err) {
      toast(`${translateText("上传失败")}: ${err.message}`);
    }
  });

  document.getElementById("generateRoleAudioBtn").addEventListener("click", async () => {
    toast(translateText("生成音频功能需要在保存角色后使用"));
  });

  document.getElementById("sampleFilterSelect").addEventListener("change", (e) => {
    rolesFilterState.sample = String(e.target.value || "all");
    renderRolesTable();
  });

  document.getElementById("chapterFilterSelect").addEventListener("change", async (e) => {
    rolesFilterState.chapter = String(e.target.value || "all");
    rolesFilterState.names.clear();
    rolesFilterState.keyword = "";
    document.getElementById("roleNameFilterInput").value = "";
    await getSelectedChapterRoleNames();
    renderRoleNameFilter();
    renderRolesTable();
  });

  document.getElementById("levelFilterSelect").addEventListener("change", (e) => {
    rolesFilterState.level = String(e.target.value || "all");
    renderRolesTable();
  });

  const roleNameFilterInputEl = document.getElementById("roleNameFilterInput");
  roleNameFilterInputEl.addEventListener("focus", () => {
    openRoleNameFilterDropdown();
    renderRoleNameFilter();
  });

  roleNameFilterInputEl.addEventListener("input", () => {
    rolesFilterState.keyword = String(roleNameFilterInputEl.value || "");
    openRoleNameFilterDropdown();
    renderRoleNameFilter();
  });

  roleNameFilterInputEl.addEventListener("blur", () => {
    window.setTimeout(() => {
      const roleNameFilterEl = document.getElementById("roleNameFilter");
      if (!roleNameFilterEl.contains(document.activeElement) && !roleNameDropdownShouldStayOpen) {
        closeRoleNameFilterDropdown();
      }
    }, 0);
  });

  document.getElementById("clearRoleFiltersBtn").addEventListener("click", () => {
    rolesFilterState.chapter = "all";
    rolesFilterState.sample = "all";
    rolesFilterState.level = "all";
    rolesFilterState.names.clear();
    rolesFilterState.keyword = "";
    document.getElementById("chapterFilterSelect").value = "all";
    document.getElementById("sampleFilterSelect").value = "all";
    document.getElementById("levelFilterSelect").value = "all";
    document.getElementById("roleNameFilterInput").value = "";
    closeRoleNameFilterDropdown();
    renderRoleNameFilter();
    renderRolesTable();
    setRolesPageStatus(translateText("已清空筛选"));
  });

  document.addEventListener("click", (event) => {
    const roleNameFilterEl = document.getElementById("roleNameFilter");
    if (!roleNameFilterEl.contains(event.target)) {
      closeRoleNameFilterDropdown();
    }
  });

  document.getElementById("rolesNovelSelect").addEventListener("change", async (event) => {
    const id = Number(event.target.value);
    setActiveNovelId(id);
    activeNovel = allNovels.find((n) => Number(n.id) === id) || null;
    if (!activeNovel) return;
    setHeader(activeNovel);
    await refreshRolesPage();
    toast(`${t("common.view")}: ${activeNovel.name}`);
  });

  document.getElementById("copyRoleJsonBtn").addEventListener("click", async () => {
    const text = String(document.getElementById("roleJsonModalContent").value || "");
    await navigator.clipboard.writeText(text);
    toast(translateText("JSON已复制"));
  });
}

function openRoleTextModal(text) {
  document.getElementById("roleTextModalContent").textContent = String(text || "").trim() || translateText("未提取到文本");
  document.getElementById("roleTextModal").classList.remove("hidden");
  document.getElementById("roleTextModal").showModal();
  localizeDocumentText(document);
}

function closeRoleTextModal() {
  document.getElementById("roleTextModal").close();
  document.getElementById("roleTextModal").classList.add("hidden");
}

async function init() {
  renderNav();
  const data = await getData();
  allNovels = data.novels || [];
  activeNovel = getNovelByQueryOrActive();

  if (!activeNovel) {
    document.getElementById("rolesPageTitle").textContent = translateText("暂无小说");
    document.getElementById("rolesNovelSelect").innerHTML = `<option value="">${translateText("暂无小说")}</option>`;
    localizeDocumentText(document);
    return;
  }

  setActiveNovelId(activeNovel.id);
  setHeader(activeNovel);
  renderNovelSelect();
  bindActions();
  await refreshRolesPage();
  localizeDocumentText(document);
}

init().catch((err) => {
  renderNav();
  toast(t("error.pageLoad", { msg: err.message }));
});
