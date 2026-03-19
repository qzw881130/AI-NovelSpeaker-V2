import {
  getData,
  getActiveNovelId,
  setActiveNovelId,
  fetchRoles,
  createRole,
  updateRole,
  updateRoleLevel,
  duplicateRole,
  deleteRole,
  uploadRoleSampleAudio,
} from "./store.js";
import { renderNav, toast } from "./ui.js";
import { t } from "./i18n.js";

let allNovels = [];
let activeNovel = null;
let roleItems = [];
let roleModalMode = "create";
let editingRoleId = null;
let roleAudioBase64 = "";

const rolesFilterState = {
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
  document.getElementById("rolesPageTitle").textContent = `${novel.name} - 角色库`;
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

function getAllRoleNames() {
  return Array.from(new Set(roleItems.map((item) => String(item.name || "").trim()).filter(Boolean))).sort((a, b) => a.localeCompare(b, "zh-CN"));
}

function getFilteredRoleItems() {
  return roleItems.filter((role) => {
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

function renderRoleNameFilter() {
  const roleNameFilterTagsEl = document.getElementById("roleNameFilterTags");
  const roleNameFilterDropdownEl = document.getElementById("roleNameFilterDropdown");
  const roleNameFilterInputEl = document.getElementById("roleNameFilterInput");

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
    roleNameFilterDropdownEl.innerHTML = '<div class="role-name-filter-empty">无匹配角色</div>';
  } else {
    roleNameFilterDropdownEl.innerHTML = names.map((name) => {
      const active = rolesFilterState.names.has(name);
      return `
        <button class="role-name-option${active ? " active" : ""}" data-role-name="${escapeHtml(name)}" type="button">
          <span>${escapeHtml(name)}</span>
          <span>${active ? "已选" : "选择"}</span>
        </button>
      `;
    }).join("");
  }

  for (const option of roleNameFilterDropdownEl.querySelectorAll(".role-name-option")) {
    option.addEventListener("click", () => {
      const name = String(option.dataset.roleName || "");
      if (!name) return;
      if (rolesFilterState.names.has(name)) {
        rolesFilterState.names.delete(name);
      } else {
        rolesFilterState.names.add(name);
      }
      roleNameFilterInputEl.focus();
      renderRoleNameFilter();
      renderRolesTable();
    });
  }
}

function openRoleNameFilterDropdown() {
  document.getElementById("roleNameFilterDropdown").classList.remove("hidden");
}

function closeRoleNameFilterDropdown() {
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
    { value: 1, label: "一等角色" },
    { value: 2, label: "二等角色" },
    { value: 3, label: "三等角色" },
  ]
    .map((item) => `<option value="${item.value}"${item.value === current ? " selected" : ""}>${item.label}</option>`)
    .join("");
}

function buildSampleCell(role) {
  const hasAudio = String(role.sampleAudioPath || "").trim();
  const parts = [];
  if (hasAudio) {
    const cacheKey = encodeURIComponent(String(role.updatedAt || role.sampleAudioPath || "0"));
    parts.push('<div class="role-sample-main">');
    parts.push(`<audio controls preload="metadata" src="/api/novels/${activeNovel.id}/roles/${role.id}/sample?v=${cacheKey}" style="width: 180px;"></audio>`);
    parts.push(`
      <div class="role-sample-actions">
        <button class="ghost-btn btn-sm generate-sample-btn" data-role-id="${role.id}" type="button">重新生成</button>
        <input class="role-upload-input hidden" data-role-id="${role.id}" type="file" accept="audio/*,.flac,.wav,.mp3,.m4a,.aac" />
        <button class="ghost-btn btn-sm upload-sample-btn" data-role-id="${role.id}" type="button">本地上传</button>
      </div>
    `);
    parts.push('</div>');
  } else {
    parts.push('<span class="text-muted">未生成</span>');
    parts.push(`
      <div class="role-sample-actions">
        <button class="ghost-btn btn-sm generate-sample-btn" data-role-id="${role.id}" type="button">生成示例</button>
        <input class="role-upload-input hidden" data-role-id="${role.id}" type="file" accept="audio/*,.flac,.wav,.mp3,.m4a,.aac" />
        <button class="ghost-btn btn-sm upload-sample-btn" data-role-id="${role.id}" type="button">本地上传</button>
      </div>
    `);
  }
  return `<div class="role-sample-cell">${parts.join("")}</div>`;
}

function renderRolesTable() {
  const tbody = document.getElementById("rolesPageTableBody");
  tbody.innerHTML = "";

  const items = getFilteredRoleItems();
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">暂无角色数据</td></tr>';
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
          <button class="ghost-btn btn-sm save-role-btn" data-role-id="${role.id}" type="button">保存</button>
          <button class="ghost-btn btn-sm duplicate-role-btn" data-role-id="${role.id}" type="button">复制</button>
          <button class="ghost-btn btn-sm danger delete-role-btn" data-role-id="${role.id}" data-role-name="${escapeHtml(role.name || "")}" type="button">删除</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
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
        setRolesPageStatus("角色名不能为空", true);
        return;
      }

      btn.disabled = true;
      try {
        const result = await updateRole(activeNovel.id, roleId, { name, instruct, sampleText });
        const idx = roleItems.findIndex((r) => r.id === roleId);
        if (idx >= 0) {
          roleItems[idx] = result.role || roleItems[idx];
        }
        setRolesPageStatus(`已保存「${result.role?.name || "角色"}」`);
        renderRoleStats({
          total: roleItems.length,
          level_1: roleItems.filter((item) => Number(item.roleLevel) === 1).length,
          level_2: roleItems.filter((item) => Number(item.roleLevel) === 2).length,
          level_3: roleItems.filter((item) => Number(item.roleLevel) === 3).length,
          without_sample: roleItems.filter((item) => !String(item.sampleAudioPath || "").trim()).length,
        });
      } catch (err) {
        setRolesPageStatus(err.message || "保存角色失败", true);
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
        setRolesPageStatus(`已复制角色为「${result.role?.name || "新角色"}」`);
        await refreshRolesPage();
      } catch (err) {
        setRolesPageStatus(err.message || "复制角色失败", true);
      } finally {
        btn.disabled = false;
      }
    });
  }

  // 绑定删除按钮
  for (const btn of tbody.querySelectorAll(".delete-role-btn")) {
    btn.addEventListener("click", async () => {
      const roleId = Number(btn.dataset.roleId || 0);
      const roleName = String(btn.dataset.roleName || "角色");
      if (!window.confirm(`确定删除「${roleName}」吗？`)) return;

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
        setRolesPageStatus(`已删除「${roleName}」`);
      } catch (err) {
        setRolesPageStatus(err.message || "删除角色失败", true);
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
        setRolesPageStatus(`已更新「${result.role?.name || "角色"}」级别`);
        await refreshRolesPage();
      } catch (err) {
        selectEl.value = String(previous);
        setRolesPageStatus(err.message || "保存角色级别失败", true);
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
      btn.textContent = "生成中...";
      try {
        const res = await fetch(`/api/novels/${activeNovel.id}/roles/${roleId}/generate-sample`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.error || "生成示例失败");
        }
        const idx = roleItems.findIndex((r) => r.id === roleId);
        if (idx >= 0) {
          roleItems[idx] = data.role || roleItems[idx];
        }
        setRolesPageStatus(`已生成「${data.role?.name || "角色"}」声音示例`);
        renderRolesTable();
      } catch (err) {
        setRolesPageStatus(err.message || "生成示例失败", true);
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
      const previousText = uploadBtn?.textContent || "本地上传";
      if (uploadBtn) {
        uploadBtn.disabled = true;
        uploadBtn.textContent = "上传中...";
      }

      try {
        const base64 = await fileToBase64(file);
        const result = await uploadRoleSampleAudio(activeNovel.id, roleId, base64, "uploaded");
        const idx = roleItems.findIndex((r) => r.id === roleId);
        if (idx >= 0) {
          roleItems[idx] = result.role || roleItems[idx];
        }
        setRolesPageStatus(`已上传「${result.role?.name || "角色"}」声音示例`);
        renderRolesTable();
      } catch (err) {
        setRolesPageStatus(err.message || "上传声音示例失败", true);
      } finally {
        inputEl.value = "";
        if (uploadBtn) {
          uploadBtn.disabled = false;
          uploadBtn.textContent = previousText;
        }
      }
    });
  }
}

async function refreshRolesPage() {
  if (!activeNovel) return;
  try {
    const result = await fetchRoles(activeNovel.id);
    roleItems = result.roles || [];
    const withoutSample = roleItems.filter((item) => !String(item.sampleAudioPath || "").trim()).length;
    renderRoleStats({ ...(result.stats || {}), without_sample: withoutSample });
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
    document.getElementById("roleModalTitle").textContent = "添加角色";
    form.name.value = "";
    form.instruct.value = "";
    form.sampleText.value = "";
    document.getElementById("roleAudioPlayer").classList.add("hidden");
    document.getElementById("roleAudioStatus").textContent = "";
  } else {
    const role = roleItems.find((r) => r.id === roleId);
    if (!role) return;
    document.getElementById("roleModalTitle").textContent = "编辑角色";
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
}

async function saveRoleFromForm() {
  const form = document.getElementById("roleForm");
  const input = {
    name: form.name.value.trim(),
    instruct: form.instruct.value.trim(),
    sampleText: form.sampleText.value.trim(),
  };
  if (!input.name) {
    toast("角色名不能为空");
    return;
  }
  try {
    if (roleModalMode === "create") {
      const result = await createRole(activeNovel.id, input);
      if (roleAudioBase64 && result?.role?.id) {
        await uploadRoleSampleAudio(activeNovel.id, result.role.id, roleAudioBase64, "uploaded");
      }
      roleItems.push(result.role);
      toast("角色已创建");
    } else {
      const result = await updateRole(activeNovel.id, editingRoleId, input);
      if (roleAudioBase64) {
        await uploadRoleSampleAudio(activeNovel.id, editingRoleId, roleAudioBase64, "uploaded");
      }
      const idx = roleItems.findIndex((r) => r.id === editingRoleId);
      if (idx >= 0) {
        roleItems[idx] = result.role || roleItems[idx];
      }
      toast("角色已更新");
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
      setRolesPageStatus("角色列表已刷新");
    } catch (err) {
      setRolesPageStatus("刷新角色列表失败", true);
    }
  });

  document.getElementById("createRoleBtn").addEventListener("click", () => {
    openRoleModal("create");
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
      document.getElementById("roleAudioStatus").textContent = `已选择: ${file.name}`;
    } catch (err) {
      toast("上传失败: " + err.message);
    }
  });

  document.getElementById("generateRoleAudioBtn").addEventListener("click", async () => {
    toast("生成音频功能需要在保存角色后使用");
  });

  document.getElementById("sampleFilterSelect").addEventListener("change", (e) => {
    rolesFilterState.sample = String(e.target.value || "all");
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

  document.getElementById("clearRoleFiltersBtn").addEventListener("click", () => {
    rolesFilterState.sample = "all";
    rolesFilterState.level = "all";
    rolesFilterState.names.clear();
    rolesFilterState.keyword = "";
    document.getElementById("sampleFilterSelect").value = "all";
    document.getElementById("levelFilterSelect").value = "all";
    document.getElementById("roleNameFilterInput").value = "";
    renderRoleNameFilter();
    renderRolesTable();
    setRolesPageStatus("已清空筛选");
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
}

async function init() {
  renderNav();
  const data = await getData();
  allNovels = data.novels || [];
  activeNovel = getNovelByQueryOrActive();

  if (!activeNovel) {
    document.getElementById("rolesPageTitle").textContent = "暂无小说";
    document.getElementById("rolesNovelSelect").innerHTML = '<option value="">暂无小说</option>';
    return;
  }

  setActiveNovelId(activeNovel.id);
  setHeader(activeNovel);
  renderNovelSelect();
  bindActions();
  await refreshRolesPage();
}

init().catch((err) => {
  renderNav();
  toast(t("error.pageLoad", { msg: err.message }));
});
