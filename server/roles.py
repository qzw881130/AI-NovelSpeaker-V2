"""角色库管理模块"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import time
from copy import deepcopy
from pathlib import Path

from .app_context import (
    DB_PATH,
    NOVEL_DIR,
    ROOT_DIR,
    WORKFLOWS_DIR,
    db_conn,
)
from .services import (
    fetch_novels,
    comfy_request_json,
    comfy_download_file,
    fetch_settings,
    comfy_upload_input_file,
    create_workflow_log,
    update_workflow_log_error,
    update_workflow_log_json,
    workflow_json_to_prompt_json,
)


ROLE_LEVEL_LABELS = {
    1: "一等角色",
    2: "二等角色",
    3: "三等角色",
}


def _temp_role_voices_dir(english_dir: str) -> Path:
    return ROOT_DIR / "temp" / english_dir / "voices"


def _temp_role_voices_rel_dir(english_dir: str) -> str:
    return f"temp/{english_dir}/voices"


def _get_novel_english_dir(conn, novel_id: int) -> str:
    row = conn.execute(
        "SELECT english_dir FROM novels WHERE id=?", (novel_id,)
    ).fetchone()
    return str(row["english_dir"] or "").strip() if row else ""


def _is_sample_audio_referenced_elsewhere(
    conn, sample_audio_path: str, exclude_role_id: int | None = None
) -> bool:
    path = str(sample_audio_path or "").strip()
    if not path:
        return False
    if exclude_role_id is None:
        row = conn.execute(
            "SELECT 1 FROM roles WHERE sample_audio_path=? LIMIT 1",
            (path,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM roles WHERE sample_audio_path=? AND id<>? LIMIT 1",
            (path, int(exclude_role_id)),
        ).fetchone()
    return bool(row)


def _normalize_role_level(level: object) -> int:
    try:
        value = int(str(level))
    except (TypeError, ValueError):
        return 3
    return value if value in ROLE_LEVEL_LABELS else 3


def _row_to_role(row) -> dict:
    if not row:
        return {}
    level = _normalize_role_level(row["role_level"])
    return {
        "id": int(row["id"]),
        "novelId": int(row["novel_id"]),
        "name": str(row["name"] or ""),
        "instruct": str(row["instruct"] or ""),
        "sampleText": str(row["sample_text"] or ""),
        "sampleAudioPath": str(row["sample_audio_path"] or ""),
        "sampleAudioSource": str(row["sample_audio_source"] or ""),
        "roleLevel": level,
        "roleLevelLabel": ROLE_LEVEL_LABELS[level],
        "createdAt": str(row["created_at"] or ""),
        "updatedAt": str(row["updated_at"] or ""),
    }


def list_roles(novel_id: int | None = None) -> dict:
    conn = db_conn()
    params = []
    where_clause = ""
    if novel_id is not None:
        where_clause = "WHERE novel_id = ?"
        params.append(novel_id)

    rows = conn.execute(
        f"""
        SELECT id, novel_id, name, instruct, sample_text, sample_audio_path, sample_audio_source, role_level,
               created_at, updated_at
        FROM roles
        {where_clause}
        ORDER BY role_level ASC, name COLLATE NOCASE ASC, id ASC
        """,
        params,
    ).fetchall()
    conn.close()

    roles = [_row_to_role(row) for row in rows]
    stats = {
        "total": len(roles),
        "level_1": sum(1 for role in roles if role["roleLevel"] == 1),
        "level_2": sum(1 for role in roles if role["roleLevel"] == 2),
        "level_3": sum(1 for role in roles if role["roleLevel"] == 3),
        "without_sample": sum(1 for role in roles if not role["sampleAudioPath"]),
    }
    return {"stats": stats, "roles": roles}


def get_role(role_id: int) -> dict | None:
    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    conn.close()
    return _row_to_role(row) if row else None


def get_role_by_name(novel_id: int, name: str) -> dict | None:
    conn = db_conn()
    row = conn.execute(
        "SELECT * FROM roles WHERE novel_id=? AND name=?",
        (novel_id, name),
    ).fetchone()
    conn.close()
    return _row_to_role(row) if row else None


def upsert_role_default(
    novel_id: int, name: str, instruct: str, sample_text: str
) -> tuple[bool, str, dict | None]:
    role_name = str(name or "").strip()
    role_instruct = str(instruct or "").strip()
    role_sample = str(sample_text or "").strip()
    if not role_name:
        return False, "role name cannot be empty", None

    conn = db_conn()
    row = conn.execute(
        "SELECT * FROM roles WHERE novel_id=? AND name=?",
        (novel_id, role_name),
    ).fetchone()
    if row:
        old_audio_path = str(row["sample_audio_path"] or "").strip()
        conn.execute(
            """
            UPDATE roles
            SET instruct=?, sample_text=?, sample_audio_path='', sample_audio_source='', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (role_instruct, role_sample, int(row["id"])),
        )
        conn.commit()
        saved = conn.execute(
            "SELECT * FROM roles WHERE id=?", (int(row["id"]),)
        ).fetchone()
        conn.close()

        if old_audio_path:
            old_path = (ROOT_DIR / old_audio_path).resolve()
            root_path = ROOT_DIR.resolve()
            if root_path in old_path.parents and old_path != root_path:
                _remove_cached_playable_variants(old_path)

        return True, "saved", _row_to_role(saved)

    cur = conn.execute(
        """
        INSERT INTO roles(novel_id, name, instruct, sample_text, role_level)
        VALUES(?, ?, ?, ?, 3)
        """,
        (novel_id, role_name, role_instruct, role_sample),
    )
    conn.commit()
    role_id = int(cur.lastrowid or 0)
    saved = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    conn.close()
    if role_id <= 0 or not saved:
        return False, "failed to save role", None
    return True, "saved", _row_to_role(saved)


def update_role_fields(
    role_id: int, name: str, instruct: str, sample_text: str
) -> tuple[bool, str, dict | None]:
    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Role not found", None

    novel_id = int(row["novel_id"])
    role_name = str(name or "").strip()
    if not role_name:
        conn.close()
        return False, "Role name cannot be empty", None

    dup = conn.execute(
        "SELECT 1 FROM roles WHERE novel_id=? AND name=? AND id<>? LIMIT 1",
        (novel_id, role_name, role_id),
    ).fetchone()
    if dup:
        conn.close()
        return False, f"duplicate role name: {role_name}", None

    # Check if sample_text changed - if so, delete old audio
    old_sample_text = str(row["sample_text"] or "").strip()
    new_sample_text = str(sample_text or "").strip()
    old_audio_path = str(row["sample_audio_path"] or "").strip()
    old_audio_source = str(row["sample_audio_source"] or "").strip()
    sample_text_changed = old_sample_text != new_sample_text
    should_clear_audio = sample_text_changed and old_audio_source != "uploaded"

    if should_clear_audio:
        # Clear audio path and source when sample text changes
        conn.execute(
            """
            UPDATE roles
            SET name=?, instruct=?, sample_text=?, sample_audio_path='', sample_audio_source='', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (role_name, str(instruct or "").strip(), new_sample_text, role_id),
        )
    else:
        conn.execute(
            """
            UPDATE roles
            SET name=?, instruct=?, sample_text=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (role_name, str(instruct or "").strip(), new_sample_text, role_id),
        )
    conn.commit()
    saved = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    conn.close()

    # Delete old audio file if sample text changed
    if should_clear_audio and old_audio_path:
        conn = db_conn()
        still_referenced = _is_sample_audio_referenced_elsewhere(
            conn, old_audio_path, role_id
        )
        conn.close()
        if still_referenced:
            return True, "saved", _row_to_role(saved)
        old_full_path = (ROOT_DIR / old_audio_path).resolve()
        _remove_cached_playable_variants(old_full_path)

    return True, "saved", _row_to_role(saved)


def update_role_level(role_id: int, role_level: int) -> tuple[bool, str, dict | None]:
    level = _normalize_role_level(role_level)
    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Role not found", None
    conn.execute(
        "UPDATE roles SET role_level=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (level, role_id),
    )
    conn.commit()
    saved = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    conn.close()
    return True, "saved", _row_to_role(saved)


def duplicate_role(role_id: int) -> tuple[bool, str, dict | None]:
    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Role not found", None

    novel_id = int(row["novel_id"])
    base_name = str(row["name"] or "").strip() or f"role-{role_id}"
    next_name = f"{base_name}-副本"
    suffix = 2
    while conn.execute(
        "SELECT 1 FROM roles WHERE novel_id=? AND name=? LIMIT 1",
        (novel_id, next_name),
    ).fetchone():
        next_name = f"{base_name}-副本{suffix}"
        suffix += 1

    cur = conn.execute(
        """
        INSERT INTO roles(novel_id, name, instruct, sample_text, sample_audio_path, sample_audio_source, role_level)
        SELECT novel_id, ?, instruct, sample_text, '', '', role_level FROM roles WHERE id=?
        """,
        (next_name, role_id),
    )
    conn.commit()
    new_id = int(cur.lastrowid or 0)
    saved = conn.execute("SELECT * FROM roles WHERE id=?", (new_id,)).fetchone()
    conn.close()
    return True, "duplicated", _row_to_role(saved)


def create_role_alias(role_id: int, alias_name: str) -> tuple[bool, str, dict | None]:
    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Role not found", None

    novel_id = int(row["novel_id"])
    target_name = str(alias_name or "").strip()
    if not target_name:
        conn.close()
        return False, "Alias name cannot be empty", None

    dup = conn.execute(
        "SELECT 1 FROM roles WHERE novel_id=? AND name=? LIMIT 1",
        (novel_id, target_name),
    ).fetchone()
    if dup:
        conn.close()
        return False, f"duplicate role name: {target_name}", None

    cur = conn.execute(
        """
        INSERT INTO roles(novel_id, name, instruct, sample_text, sample_audio_path, sample_audio_source, role_level)
        SELECT novel_id, ?, instruct, sample_text, sample_audio_path, sample_audio_source, role_level FROM roles WHERE id=?
        """,
        (target_name, role_id),
    )
    conn.commit()
    new_id = int(cur.lastrowid or 0)
    saved = conn.execute("SELECT * FROM roles WHERE id=?", (new_id,)).fetchone()
    conn.close()
    return True, "aliased", _row_to_role(saved)


def delete_role(role_id: int) -> tuple[bool, str]:
    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Role not found"

    audio_path = str(row["sample_audio_path"] or "").strip()
    conn.execute("DELETE FROM roles WHERE id=?", (role_id,))
    conn.commit()
    conn.close()

    if audio_path:
        conn = db_conn()
        still_referenced = _is_sample_audio_referenced_elsewhere(
            conn, audio_path, role_id
        )
        conn.close()
        if still_referenced:
            return True, "deleted"
        path = (ROOT_DIR / audio_path).resolve()
        root_path = ROOT_DIR.resolve()
        if root_path in path.parents and path != root_path:
            _remove_cached_playable_variants(path)

    return True, "deleted"


def save_role_sample_audio(
    role_id: int, audio_base64: str, source: str
) -> tuple[bool, str, dict | None]:
    audio_bytes = base64.b64decode(audio_base64)
    if len(audio_bytes) == 0:
        return False, "audio data is empty", None

    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Role not found", None

    novel_id = int(row["novel_id"])
    role_name = str(row["name"] or "").strip()
    old_audio_path = str(row["sample_audio_path"] or "").strip()
    english_dir = _get_novel_english_dir(conn, novel_id)
    if not english_dir:
        conn.close()
        return False, "Novel not found", None

    file_name = f"role-{role_id}-{int(time.time())}.flac"
    rel_dir = _temp_role_voices_rel_dir(english_dir)
    abs_dir = _temp_role_voices_dir(english_dir)
    abs_dir.mkdir(parents=True, exist_ok=True)
    abs_path = abs_dir / file_name
    rel_path = f"{rel_dir}/{file_name}"

    with open(abs_path, "wb") as f:
        f.write(audio_bytes)

    conn.execute(
        """
        UPDATE roles
        SET sample_audio_path=?, sample_audio_source=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (rel_path, source, role_id),
    )
    conn.commit()
    saved = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    conn.close()

    if old_audio_path:
        old_full_path = (ROOT_DIR / old_audio_path).resolve()
        _remove_cached_playable_variants(old_full_path)

    return True, "saved", _row_to_role(saved)


def _remove_cached_playable_variants(path: Path):
    if not path.exists():
        return
    try:
        path.unlink()
    except Exception:
        pass


# For backwards compatibility with existing code
def list_roles_summary(novel_id: int | None = None) -> dict:
    return list_roles(novel_id)


def get_roles_by_novel(novel_id: int) -> list[dict]:
    result = list_roles(novel_id)
    return result.get("roles", [])


def get_role_library_map(novel_id: int) -> dict[str, dict]:
    conn = db_conn()
    rows = conn.execute(
        "SELECT id, name, sample_text, sample_audio_path FROM roles WHERE novel_id=?",
        (novel_id,),
    ).fetchall()
    conn.close()
    mapping: dict[str, dict] = {}
    for row in rows:
        name = str(row["name"] or "").strip()
        if name:
            mapping[name] = {
                "id": int(row["id"]),
                "name": name,
                "sample_text": str(row["sample_text"] or "").strip(),
                "sample_audio_path": str(row["sample_audio_path"] or "").strip(),
            }
    return mapping


def _get_voice_sample_workflow(
    novel_id: int,
) -> tuple[dict | None, dict, str, str, bool]:
    """获取小说的voice_sample工作流配置"""
    conn = db_conn()
    row = conn.execute(
        """
        SELECT w.json_text, w.workflow_io_config, w.name, w.workflow_type, w.workflow_log_enabled FROM comfy_workflows w
        JOIN novels n ON n.voice_sample_workflow_id = w.id
        WHERE n.id = ?
        """,
        (novel_id,),
    ).fetchone()
    conn.close()

    if not row:
        return None, {}, "", "voice_sample", True

    try:
        workflow = json.loads(str(row["json_text"] or "{}"))
        io_config = json.loads(str(row["workflow_io_config"] or "{}") or "{}")
        if not isinstance(io_config, dict):
            io_config = {}
        return (
            workflow if isinstance(workflow, dict) else None,
            io_config,
            str(row["name"] or ""),
            str(row["workflow_type"] or "voice_sample"),
            bool(int(row["workflow_log_enabled"] or 0)),
        )
    except json.JSONDecodeError:
        return (
            None,
            {},
            str(row["name"] or ""),
            str(row["workflow_type"] or "voice_sample"),
            bool(int(row["workflow_log_enabled"] or 0)),
        )


def generate_role_sample_audio(
    role_id: int, novel_id: int
) -> tuple[bool, str, dict | None, dict | None]:
    """生成角色示例音频"""
    conn = db_conn()
    row = conn.execute(
        "SELECT * FROM roles WHERE id=? AND novel_id=?", (role_id, novel_id)
    ).fetchone()
    if not row:
        conn.close()
        return False, "Role not found", None, None

    role_name = str(row["name"] or "").strip()
    instruct = str(row["instruct"] or "").strip()
    sample_text = str(row["sample_text"] or "").strip()
    old_audio_path = str(row["sample_audio_path"] or "").strip()
    english_dir = _get_novel_english_dir(conn, novel_id)
    conn.close()

    if not role_name:
        return False, "Role name is empty", None, None
    if not instruct:
        return False, "Role instruct is empty", None, None
    if not sample_text:
        return False, "Role sample text is empty", None, None
    if not english_dir:
        return False, "Novel not found", None, None

    # 获取voice_sample工作流
    (
        workflow,
        workflow_io_config,
        workflow_name,
        workflow_category,
        workflow_log_enabled,
    ) = _get_voice_sample_workflow(novel_id)
    if not workflow:
        return False, "Voice sample workflow not configured for this novel", None, None

    # 查找并修改工作流中的节点
    # 假设工作流中有用于instruct和text的节点
    workflow_copy = workflow_json_to_prompt_json(deepcopy(workflow))
    log_id = 0

    # 尝试找到instruct节点（通常是描述音色的prompt）
    # 尝试找到text节点（示例台词）
    # 尝试找到保存音频的节点
    instruct_node_found = False
    text_node_found = False
    save_node_found = False

    for node_id, node in workflow_copy.items():
        if not isinstance(node, dict) or "inputs" not in node:
            continue
        inputs = node.get("inputs", {})

        # 查找包含特定关键词的节点
        class_type = node.get("class_type", "")
        if "instruct" in class_type.lower() or "prompt" in class_type.lower():
            if not instruct_node_found and "text" in inputs or "prompt" in inputs:
                for key in inputs:
                    if isinstance(inputs[key], str) and len(inputs[key]) < 100:
                        inputs[key] = instruct
                        instruct_node_found = True
                        break

        if "text" in inputs and not text_node_found:
            inputs["text"] = sample_text
            text_node_found = True

        # 查找保存音频的节点（SaveAudio）
        if "SaveAudio" in class_type:
            if "filename_prefix" in inputs:
                inputs["filename_prefix"] = f"voices/role-{role_id:03d}"
                save_node_found = True

    if not save_node_found:
        return False, "Workflow missing SaveAudio node", None, workflow_copy

    for node in workflow_copy.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs", {})
        if "seed" in inputs:
            inputs["seed"] = random.randint(0, 2**63 - 1)

    # 获取ComfyUI配置
    settings = fetch_settings(db_conn())
    comfy_url = str(settings.get("comfyUrl") or "").strip()
    if not comfy_url:
        return False, "ComfyUI URL not configured", None, workflow_copy

    # 提交工作流到ComfyUI
    try:
        if workflow_log_enabled:
            log_id = create_workflow_log(
                workflow_category or "voice_sample",
                workflow_name or "生成示例音频",
                workflow_copy,
            )
            update_workflow_log_json(log_id, workflow_copy)
        result = comfy_request_json(
            comfy_url=comfy_url,
            path="/prompt",
            method="POST",
            payload={"prompt": workflow_copy},
        )
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            update_workflow_log_error(log_id, "Failed to submit workflow to ComfyUI")
            return False, "Failed to submit workflow to ComfyUI", None, workflow_copy
    except Exception as e:
        update_workflow_log_error(log_id, f"Failed to submit workflow: {str(e)}")
        return False, f"Failed to submit workflow: {str(e)}", None, workflow_copy

    # 等待工作流完成
    started = time.time()
    timeout_seconds = 30 * 60  # 30分钟超时
    output_info = None

    while time.time() - started < timeout_seconds:
        try:
            history = comfy_request_json(
                comfy_url=comfy_url, path=f"/history/{prompt_id}", method="GET"
            )

            # 从history中提取音频输出
            for node_id, node_output in (
                history.get(prompt_id, {}).get("outputs", {}).items()
            ):
                if "audio" in node_output and len(node_output["audio"]) > 0:
                    audio_info = node_output["audio"][0]
                    output_info = (
                        audio_info.get("filename"),
                        audio_info.get("subfolder", ""),
                        audio_info.get("type", "output"),
                    )
                    break

            if output_info:
                break
        except Exception:
            pass

        time.sleep(3)

    if output_info is None:
        update_workflow_log_error(
            log_id, "ComfyUI workflow timeout; sample audio output not found"
        )
        return (
            False,
            "ComfyUI workflow timeout; sample audio output not found",
            None,
            workflow_copy,
        )

    # 下载生成的音频文件
    try:
        filename, subfolder, file_type = output_info
        audio_data = comfy_download_file(
            comfy_url=comfy_url,
            filename=filename,
            subfolder=subfolder,
            file_type=file_type,
        )

        # 保存文件
        suffix = Path(filename).suffix or ".flac"
        rel_dir = _temp_role_voices_rel_dir(english_dir)
        abs_dir = _temp_role_voices_dir(english_dir)
        abs_dir.mkdir(parents=True, exist_ok=True)

        file_name = f"role-{role_id}-{int(time.time())}{suffix}"
        rel_path = f"{rel_dir}/{file_name}"
        abs_path = abs_dir / file_name
        abs_path.write_bytes(audio_data)

        # 删除旧文件
        if old_audio_path:
            old_full_path = (ROOT_DIR / old_audio_path).resolve()
            _remove_cached_playable_variants(old_full_path)

        # 更新数据库
        conn = db_conn()
        conn.execute(
            """
            UPDATE roles
            SET sample_audio_path=?, sample_audio_source='generated', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (rel_path, role_id),
        )
        conn.commit()
        saved = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
        conn.close()

        return True, "generated", _row_to_role(saved), workflow_copy

    except Exception as e:
        update_workflow_log_error(log_id, f"Failed to save audio file: {str(e)}")
        return False, f"Failed to save audio file: {str(e)}", None, workflow_copy


def _get_voice_transcribe_workflow(
    novel_id: int,
) -> tuple[dict | None, dict, str, str, bool]:
    """获取小说的voice_transcribe工作流配置"""
    conn = db_conn()
    row = conn.execute(
        """
        SELECT w.json_text, w.workflow_io_config, w.name, w.workflow_type, w.workflow_log_enabled FROM comfy_workflows w
        JOIN novels n ON n.voice_transcribe_workflow_id = w.id
        WHERE n.id = ?
        """,
        (novel_id,),
    ).fetchone()
    conn.close()

    if not row:
        return None, {}, "", "voice_transcribe", True

    try:
        workflow = json.loads(str(row["json_text"] or "{}"))
        io_config = json.loads(str(row["workflow_io_config"] or "{}") or "{}")
        if not isinstance(io_config, dict):
            io_config = {}
        return (
            workflow if isinstance(workflow, dict) else None,
            io_config,
            str(row["name"] or ""),
            str(row["workflow_type"] or "voice_transcribe"),
            bool(int(row["workflow_log_enabled"] or 0)),
        )
    except json.JSONDecodeError:
        return (
            None,
            {},
            str(row["name"] or ""),
            str(row["workflow_type"] or "voice_transcribe"),
            bool(int(row["workflow_log_enabled"] or 0)),
        )


def _extract_text_output_from_history(
    history: dict, prompt_id: str, node_id: str = "1"
) -> str | None:
    """从ComfyUI历史记录中提取文本输出"""
    if not history:
        return None

    job = history.get(prompt_id)
    if job is None and history:
        job = next(iter(history.values()))
    if not isinstance(job, dict):
        return None

    outputs = job.get("outputs")
    if not isinstance(outputs, dict):
        return None

    def _read_node_text(node_output: dict) -> str | None:
        for key in ("text", "string", "value"):
            value = node_output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list) and value:
                text = "\n".join(str(x) for x in value if str(x).strip()).strip()
                if text:
                    return text

        ui = node_output.get("ui")
        if isinstance(ui, dict):
            for key in ("text", "string", "value"):
                value = ui.get(key)
                if isinstance(value, list) and value:
                    text = "\n".join(str(x) for x in value if str(x).strip()).strip()
                    if text:
                        return text
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return None

    preferred_node_ids = []
    for candidate in (str(node_id), "1", "4"):
        if candidate not in preferred_node_ids:
            preferred_node_ids.append(candidate)

    for candidate in preferred_node_ids:
        node_output = outputs.get(candidate)
        if isinstance(node_output, dict):
            text = _read_node_text(node_output)
            if text:
                return text

    for node_output in outputs.values():
        if isinstance(node_output, dict):
            text = _read_node_text(node_output)
            if text:
                return text

    return None


def extract_role_sample_text(
    role_id: int, novel_id: int
) -> tuple[bool, str, str | None]:
    """从角色示例音频中提取文本"""
    conn = db_conn()
    row = conn.execute(
        "SELECT * FROM roles WHERE id=? AND novel_id=?", (role_id, novel_id)
    ).fetchone()
    if not row:
        conn.close()
        return False, "Role not found", None

    file_path = str(row["sample_audio_path"] or "").strip()
    conn.close()

    if not file_path:
        return False, "Role sample audio not found", None

    media_path = (ROOT_DIR / file_path).resolve()
    root_resolved = ROOT_DIR.resolve()
    if root_resolved not in media_path.parents and media_path != root_resolved:
        return False, "Invalid file path", None
    if not media_path.exists() or not media_path.is_file():
        return False, "Role sample audio not found", None

    # 获取voice_transcribe工作流
    (
        workflow,
        workflow_io_config,
        workflow_name,
        workflow_category,
        workflow_log_enabled,
    ) = _get_voice_transcribe_workflow(novel_id)
    if not workflow:
        return False, "Voice transcribe workflow not configured for this novel", None

    # 上传音频文件到ComfyUI
    try:
        upload_info = comfy_upload_input_file(media_path.name, media_path.read_bytes())
        filename = (
            str(upload_info.get("name") or media_path.name).strip() or media_path.name
        )
        subfolder = str(upload_info.get("subfolder") or "").strip()
        file_type = str(upload_info.get("type") or "input").strip() or "input"
    except Exception as e:
        return False, f"Failed to upload audio to ComfyUI: {str(e)}", None

    # 修改工作流节点
    workflow_copy = workflow_json_to_prompt_json(deepcopy(workflow))
    log_id = 0
    input_node_id = (
        str(
            workflow_io_config.get("inputs", {}).get("audioFile", {}).get("nodeId")
            or "2"
        ).strip()
        or "2"
    )
    output_node_id = (
        str(
            workflow_io_config.get("outputs", {}).get("textOutput", {}).get("nodeId")
            or "4"
        ).strip()
        or "4"
    )

    if input_node_id not in workflow_copy:
        return (
            False,
            f"Voice transcribe workflow missing node {input_node_id}. Available nodes: {list(workflow_copy.keys())}",
            None,
        )
    if "inputs" not in workflow_copy[input_node_id]:
        return (
            False,
            f"Node {input_node_id} missing inputs. Node {input_node_id}: {workflow_copy[input_node_id]}",
            None,
        )

    workflow_copy[input_node_id]["inputs"]["audio"] = filename
    if "audioUI" in workflow_copy[input_node_id]["inputs"]:
        workflow_copy[input_node_id]["inputs"]["audioUI"] = (
            f"/api/view?filename={filename}&type={file_type}&subfolder={subfolder}&rand={time.time():.6f}"
        )

    # 获取ComfyUI配置
    settings = fetch_settings(db_conn())
    comfy_url = str(settings.get("comfyUrl") or "").strip()
    if not comfy_url:
        return False, "ComfyUI URL not configured", None

    try:
        if workflow_log_enabled:
            log_id = create_workflow_log(
                workflow_category or "voice_transcribe",
                workflow_name or "提取声音文本",
                workflow_copy,
            )
        result = comfy_request_json(
            comfy_url=comfy_url,
            path="/prompt",
            method="POST",
            payload={"prompt": workflow_copy},
        )

        prompt_id = result.get("prompt_id")
        if not prompt_id:
            update_workflow_log_error(
                log_id, f"Failed to submit workflow to ComfyUI: {result}"
            )
            return False, f"Failed to submit workflow to ComfyUI: {result}", None
    except Exception as e:
        update_workflow_log_error(log_id, f"Failed to submit workflow: {str(e)}")
        return False, f"Failed to submit workflow: {str(e)}", None

    # 等待工作流完成
    started = time.time()
    timeout_seconds = 10 * 60
    output_text = None

    while time.time() - started < timeout_seconds:
        try:
            history = comfy_request_json(
                comfy_url=comfy_url, path=f"/history/{prompt_id}", method="GET"
            )
            output_text = _extract_text_output_from_history(
                history, prompt_id, node_id=output_node_id
            )
            if output_text:
                break
        except Exception:
            pass

        time.sleep(2)

    if not output_text:
        update_workflow_log_error(
            log_id, "ComfyUI workflow timeout; extracted text not found"
        )
        return False, "ComfyUI workflow timeout; extracted text not found", None

    return True, "ok", output_text


def apply_roles_to_all_chapters(
    novel_id: int, source_chapter_num: int
) -> tuple[bool, str, int]:
    """Apply roles from source chapter to all chapters in the same novel"""
    conn = db_conn()

    # Get source chapter's role_list
    source_row = conn.execute(
        "SELECT json_output FROM chapters WHERE novel_id=? AND chapter_num=?",
        (novel_id, source_chapter_num),
    ).fetchone()

    if not source_row:
        conn.close()
        return False, "Source chapter not found", 0

    source_json_str = str(source_row["json_output"] or "").strip()
    if not source_json_str:
        conn.close()
        return False, "Source chapter has no JSON output", 0

    try:
        source_json = json.loads(source_json_str)
    except json.JSONDecodeError:
        conn.close()
        return False, "Invalid JSON in source chapter", 0

    role_list = source_json.get("role_list", [])
    if not isinstance(role_list, list):
        conn.close()
        return False, "Invalid role_list format", 0

    # Get all chapters in the novel
    chapters = conn.execute(
        "SELECT chapter_num, json_output FROM chapters WHERE novel_id=?",
        (novel_id,),
    ).fetchall()

    updated_count = 0
    for chapter in chapters:
        chapter_num = int(chapter["chapter_num"])
        if chapter_num == source_chapter_num:
            continue  # Skip source chapter

        chapter_json_str = str(chapter["json_output"] or "").strip()
        if not chapter_json_str:
            continue

        try:
            chapter_json = json.loads(chapter_json_str)
            chapter_json["role_list"] = role_list
            updated_json_str = json.dumps(chapter_json, indent=2, ensure_ascii=False)

            # Update the chapter
            conn.execute(
                """
                UPDATE chapters
                SET json_output=?, updated_at=CURRENT_TIMESTAMP
                WHERE novel_id=? AND chapter_num=?
                """,
                (updated_json_str, novel_id, chapter_num),
            )
            updated_count += 1
        except json.JSONDecodeError:
            continue

    conn.commit()
    conn.close()

    return True, "ok", updated_count
