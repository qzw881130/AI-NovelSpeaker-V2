"""台词音频服务模块"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from .app_context import NOVEL_DIR, ROOT_DIR, db_conn
from .services import (
    comfy_download_file,
    comfy_interrupt_execution,
    comfy_request_json,
    comfy_upload_input_file,
    create_workflow_log,
    db_rel_path,
    extract_audio_output_from_history,
    fetch_settings,
    probe_audio_duration_seconds,
    parse_datetime_utc,
    update_chapter_non_ver_audio_duration_cache,
    update_novel_total_non_ver_audio_duration_seconds,
    update_workflow_log_error,
    update_workflow_log_json,
    workflow_json_to_prompt_json,
)


def _safe_filename(text: str) -> str:
    """将文本转换为安全的文件名"""
    return re.sub(r'[\\/:*?"<>|]+', "_", str(text or "").strip())[:50]


def _normalize_role_name(name: str) -> str:
    """规范化角色名，用于匹配"""
    return str(name or "").strip()


def _chapter_temp_dir(english_dir: str, chapter_num: int) -> Path:
    return ROOT_DIR / "temp" / english_dir / "audio" / str(chapter_num)


def _chapter_line_audio_path(
    english_dir: str, chapter_num: int, line_hash: str
) -> Path:
    return _chapter_temp_dir(english_dir, chapter_num) / f"{line_hash}.flac"


def _comfy_line_audio_prefix(english_dir: str, chapter_id: int) -> str:
    safe_english_dir = _safe_filename(english_dir) or "novel"
    return f"temp/{safe_english_dir}/{chapter_id}/chapter-{chapter_id}"


def _novel_audio_output_dir(english_dir: str) -> Path:
    return NOVEL_DIR / english_dir / "audio"


def _novel_audio_output_dir_non_ver(english_dir: str) -> Path:
    return NOVEL_DIR / english_dir / "audio_non_ver"


def _assign_text_input(workflow: dict, node_id: str, value: str, purpose: str) -> None:
    node = workflow.get(str(node_id))
    if not isinstance(node, dict):
        raise RuntimeError(f"台词音频工作流缺少{purpose}节点")
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        raise RuntimeError(f"台词音频工作流缺少{purpose}节点输入")
    for key in ("prompt", "text", "string", "value"):
        if key in inputs:
            linked = inputs[key]
            if isinstance(linked, list) and linked:
                linked_node_id = str(linked[0] or "").strip()
                if linked_node_id and linked_node_id != str(node_id):
                    _assign_text_input(workflow, linked_node_id, value, purpose)
                    return
            inputs[key] = value
            return
    raise RuntimeError(f"台词音频工作流缺少{purpose}节点文本输入字段")


def _line_text_char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", str(text or "")))


def _clean_line_text(text: str) -> str:
    raw = str(text or "").rstrip()
    if raw.startswith(" ") and not raw.startswith("  "):
        return raw
    return raw.strip()


def _interrupt_comfy_before_timeout_failure(comfy_url: str) -> str:
    try:
        if comfy_interrupt_execution(comfy_url):
            return "已中断 ComfyUI 当前工作流"
        return "ComfyUI 中断请求返回失败"
    except Exception as exc:
        return f"ComfyUI 中断请求失败: {exc}"


def _chapter_merged_output_path(english_dir: str, chapter_num: int) -> Path:
    return (
        _novel_audio_output_dir(english_dir) / f"chapter-{chapter_num:03d}-merged.flac"
    )


def _chapter_merged_output_path_non_ver(english_dir: str, chapter_num: int) -> Path:
    return (
        _novel_audio_output_dir_non_ver(english_dir)
        / f"chapter-{chapter_num:03d}-merged.flac"
    )


def parse_juben_lines_from_json_text(json_text: str) -> list[dict]:
    """从JSON文本解析剧本台词"""
    raw = str(json_text or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    juben = str(data.get("juben") or "").strip()
    if not juben:
        return []

    items: list[dict] = []
    for idx, line in enumerate(juben.splitlines()):
        raw_line = str(line or "").strip()
        if not raw_line:
            continue
        role_name = ""
        line_text = raw_line
        separator_positions = [
            pos for pos in (raw_line.find(":"), raw_line.find("：")) if pos >= 0
        ]
        if separator_positions:
            split_at = min(separator_positions)
            role_name = raw_line[:split_at].strip()
            line_text = _clean_line_text(raw_line[split_at + 1 :])
        line_hash = hashlib.md5(raw_line.encode("utf-8")).hexdigest()
        items.append(
            {
                "line_index": idx,
                "raw_line": raw_line,
                "role_name": role_name,
                "line_text": line_text,
                "line_hash": line_hash,
            }
        )
    return items


def get_novel_role_library_map(novel_id: int) -> dict[str, dict]:
    """获取小说的角色库映射"""
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT id, name, instruct, sample_text, sample_audio_path
        FROM roles
        WHERE novel_id = ?
        """,
        (novel_id,),
    ).fetchall()
    conn.close()
    mapping: dict[str, dict] = {}
    for row in rows:
        name = _normalize_role_name(row["name"])
        if name:
            mapping[name] = {
                "id": int(row["id"]),
                "name": name,
                "instruct": str(row["instruct"] or "").strip(),
                "sample_text": str(row["sample_text"] or "").strip(),
                "sample_audio_path": str(row["sample_audio_path"] or "").strip(),
            }
    return mapping


def _line_audio_task_row_to_dict(row: Any) -> dict:
    """将数据库行转换为字典"""
    return {
        "id": int(row["id"]),
        "novelId": int(row["novel_id"]),
        "chapterId": int(row["chapter_id"]),
        "chapterNum": int(row["chapter_num"]),
        "chapterTitle": str(row["chapter_title"] or ""),
        "lineIndex": int(row["line_index"] or 0),
        "roleName": str(row["role_name"] or ""),
        "lineText": str(row["line_text"] or ""),
        "referenceText": str(row["reference_text"] or ""),
        "referenceAudioPath": str(row["reference_audio_path"] or ""),
        "lineHash": str(row["line_hash"] or ""),
        "status": str(row["status"] or ""),
        "comfyPromptId": str(row["comfy_prompt_id"] or ""),
        "comfyStatus": str(row["comfy_status"] or ""),
        "outputFilename": str(row["output_filename"] or ""),
        "outputSubfolder": str(row["output_subfolder"] or ""),
        "outputType": str(row["output_type"] or ""),
        "downloadedFilePath": str(row["downloaded_file_path"] or ""),
        "durationSeconds": round(float(row["duration_seconds"] or 0), 1),
        "queuePriority": int(row["queue_priority"] or 0),
        "errorMessage": str(row["error_message"] or ""),
        "createdAt": str(row["created_at"] or ""),
        "updatedAt": str(row["updated_at"] or ""),
        "comfyStartedAt": str(row["comfy_started_at"] or ""),
        "comfyFinishedAt": str(row["comfy_finished_at"] or ""),
        "scheduledAt": str(row["scheduled_at"] or ""),
    }


def _extract_comfy_history_error(history: dict, prompt_id: str) -> str | None:
    if not isinstance(history, dict) or not history:
        return None
    job = history.get(prompt_id)
    if job is None:
        job = next(iter(history.values())) if history else None
    if not isinstance(job, dict):
        return None

    status = job.get("status")
    if not isinstance(status, dict):
        return None

    status_str = str(status.get("status_str") or "").strip().lower()
    messages = status.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, (list, tuple)) or not message:
                continue
            message_type = str(message[0] or "").strip().lower()
            payload = (
                message[1] if len(message) > 1 and isinstance(message[1], dict) else {}
            )
            if "error" not in message_type and message_type not in {
                "execution_interrupted",
                "execution_error",
            }:
                continue
            details = [
                str(payload.get("exception_message") or "").strip(),
                str(payload.get("node_errors") or "").strip(),
                str(payload.get("error") or "").strip(),
            ]
            detail_text = next((item for item in details if item), "")
            if detail_text:
                return detail_text
            if message_type:
                return message_type

    if status_str in {"error", "failed", "execution_error", "execution_interrupted"}:
        return str(status.get("status_str") or "ComfyUI workflow failed")
    return None


def _get_existing_line_task(novel_id: int, chapter_id: int, line_hash: str) -> Any:
    """获取已存在的台词任务"""
    conn = db_conn()
    row = conn.execute(
        """
        SELECT *
        FROM line_audio_tasks
        WHERE novel_id=? AND chapter_id=? AND line_hash=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (novel_id, chapter_id, line_hash),
    ).fetchone()
    conn.close()
    return row


def _line_audio_task_priority(row: Any) -> tuple[int, int]:
    if not row:
        return (-1, -1)
    status = str(row["status"] or "").strip().lower()
    has_audio = bool(str(row["downloaded_file_path"] or "").strip())
    if status == "completed" and has_audio:
        base = 6
    elif status in {"running", "processing"}:
        base = 5
    elif status == "pending":
        base = 4
    elif status == "completed":
        base = 3
    elif status == "failed":
        base = 2
    elif status == "cancelled":
        base = 1
    else:
        base = 0
    return (base, int(row["id"] or 0))


def _pick_best_line_audio_task(rows: list[Any]) -> Any:
    if not rows:
        return None
    return max(rows, key=_line_audio_task_priority)


def get_line_audio_task(task_id: int) -> dict | None:
    """获取单个台词音频任务"""
    conn = db_conn()
    row = conn.execute(
        "SELECT * FROM line_audio_tasks WHERE id=?", (task_id,)
    ).fetchone()
    conn.close()
    return _line_audio_task_row_to_dict(row) if row else None


def list_line_audio_tasks(
    novel_id: int | None = None, limit: int = 100, offset: int = 0
) -> dict:
    """分页获取台词音频任务列表"""
    conn = db_conn()
    params = []
    where_clause = ""
    if novel_id is not None:
        where_clause = "WHERE novel_id = ?"
        params.append(novel_id)

    limit = max(1, min(int(limit or 100), 200))
    offset = max(0, int(offset or 0))

    count_row = conn.execute(
        f"SELECT COUNT(1) AS c FROM line_audio_tasks {where_clause}", params
    ).fetchone()
    pending_row = conn.execute(
        f"SELECT COUNT(1) AS c FROM line_audio_tasks {where_clause}{' AND' if where_clause else ' WHERE'} status IN ('pending','running','processing')",
        params,
    ).fetchone()

    rows = conn.execute(
        f"""
        SELECT *
        FROM line_audio_tasks
        {where_clause}
        ORDER BY
            CASE status
              WHEN 'processing' THEN 0
              WHEN 'running' THEN 0
              WHEN 'pending' THEN 1
              WHEN 'failed' THEN 2
              ELSE 3
            END,
            CASE WHEN status='pending' THEN COALESCE(queue_priority, 0) ELSE 0 END DESC,
            updated_at DESC,
            id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()
    conn.close()
    total_count = int(count_row["c"] or 0) if count_row else 0
    pending_count = int(pending_row["c"] or 0) if pending_row else 0
    task_list = [_line_audio_task_row_to_dict(row) for row in rows]
    return {
        "lineAudioTasks": task_list,
        "pendingCount": pending_count,
        "totalCount": total_count,
        "hasMore": offset + len(task_list) < total_count,
        "nextOffset": offset + len(task_list),
    }


def get_chapter_line_audio_entries(novel_id: int, chapter_id: int) -> list[dict]:
    """获取章节的台词音频条目列表（用于预览）"""
    conn = db_conn()

    # 获取章节信息
    chapter = conn.execute(
        """
        SELECT c.id, c.chapter_num, c.title, n.english_dir,
               t.merged_result_json
        FROM chapters c
        JOIN novels n ON n.id = c.novel_id
        LEFT JOIN json_tasks t ON t.novel_id = c.novel_id
            AND t.chapter_num = c.chapter_num
            AND t.status = 'completed'
            AND t.merged_result_json IS NOT NULL
        WHERE c.novel_id = ? AND c.id = ?
        ORDER BY t.id DESC
        LIMIT 1
        """,
        (novel_id, chapter_id),
    ).fetchone()

    if not chapter:
        conn.close()
        return []

    merged_json = str(chapter["merged_result_json"] or "").strip()
    lines = parse_juben_lines_from_json_text(merged_json)
    if not lines:
        conn.close()
        return []

    # 获取角色库映射
    role_map = get_novel_role_library_map(novel_id)

    # 获取已有的任务
    task_rows = conn.execute(
        "SELECT * FROM line_audio_tasks WHERE novel_id = ? AND chapter_id = ? ORDER BY id DESC",
        (novel_id, chapter_id),
    ).fetchall()
    conn.close()

    task_groups: dict[str, list[Any]] = {}
    for row in task_rows:
        task_groups.setdefault(str(row["line_hash"] or ""), []).append(row)

    items: list[dict] = []
    for line in lines:
        line_hash = str(line["line_hash"])
        row = _pick_best_line_audio_task(task_groups.get(line_hash, []))
        role_name = _normalize_role_name(line["role_name"])
        role = role_map.get(role_name)
        has_role_library = role is not None
        has_role_sample = bool(
            role and str(role.get("sample_audio_path") or "").strip()
        )

        item: dict = {
            "lineIndex": int(line["line_index"]),
            "lineNo": int(line["line_index"]) + 1,
            "rawLine": str(line["raw_line"]),
            "roleName": role_name,
            "lineText": str(line["line_text"]),
            "lineHash": line_hash,
            "canGenerate": bool(role_name) and has_role_library and has_role_sample,
            "roleInLibrary": has_role_library,
            "roleHasSampleAudio": has_role_sample,
            "task": _line_audio_task_row_to_dict(row) if row else None,
            "hasAudio": bool(
                row
                and str(row["status"]) == "completed"
                and str(row["downloaded_file_path"] or "").strip()
            ),
        }

        # 添加音频流URL
        if item["hasAudio"] and row:
            item["streamUrl"] = f"/api/line-audio-tasks/{int(row['id'])}/file"
            item["durationSeconds"] = round(float(row["duration_seconds"] or 0), 1)
        else:
            item["streamUrl"] = ""
            item["durationSeconds"] = 0.0

        items.append(item)

    return items


def list_role_line_audio_entries(
    novel_id: int, role_name: str, page: int = 1, page_size: int = 50, chapter_num: int | None = None
) -> dict:
    """按角色分页列出指定范围内的台词及其音频状态"""
    target_role = _normalize_role_name(role_name)
    if not target_role:
        return {"items": [], "totalCount": 0, "page": 1, "pageSize": 50, "pageCount": 0}

    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))

    conn = db_conn()
    chapter_filter = "AND c.chapter_num = ?" if chapter_num is not None else ""
    chapter_params: tuple[int, ...] = (novel_id, int(chapter_num)) if chapter_num is not None else (novel_id,)
    chapter_rows = conn.execute(
        """
        SELECT c.id AS chapter_id, c.chapter_num, c.title AS chapter_title,
               (
                 SELECT jt.merged_result_json
                 FROM json_tasks jt
                 WHERE jt.novel_id = c.novel_id
                   AND jt.chapter_num = c.chapter_num
                   AND jt.status = 'completed'
                   AND jt.merged_result_json IS NOT NULL
                 ORDER BY jt.id DESC
                 LIMIT 1
               ) AS merged_result_json
        FROM chapters c
        WHERE c.novel_id = ?
        {chapter_filter}
        ORDER BY c.chapter_num ASC, c.id ASC
        """.format(chapter_filter=chapter_filter),
        chapter_params,
    ).fetchall()
    task_rows = conn.execute(
        "SELECT * FROM line_audio_tasks WHERE novel_id = ? ORDER BY id DESC",
        (novel_id,),
    ).fetchall()
    conn.close()

    role_map = get_novel_role_library_map(novel_id)
    role = role_map.get(target_role)
    has_role_library = role is not None
    has_role_sample = bool(role and str(role.get("sample_audio_path") or "").strip())

    task_groups: dict[str, list[Any]] = {}
    for row in task_rows:
        group_key = f"{int(row['chapter_id'])}:{str(row['line_hash'] or '')}"
        task_groups.setdefault(group_key, []).append(row)

    items: list[dict] = []
    for chapter in chapter_rows:
        merged_json = str(chapter["merged_result_json"] or "").strip()
        if not merged_json:
            continue
        for line in parse_juben_lines_from_json_text(merged_json):
            role_name_value = _normalize_role_name(line["role_name"])
            if role_name_value != target_role:
                continue
            line_hash = str(line["line_hash"] or "")
            group_key = f"{int(chapter['chapter_id'])}:{line_hash}"
            row = _pick_best_line_audio_task(task_groups.get(group_key, []))
            item: dict[str, Any] = {
                "key": group_key,
                "novelId": int(novel_id),
                "chapterId": int(chapter["chapter_id"]),
                "chapterNum": int(chapter["chapter_num"] or 0),
                "chapterTitle": str(chapter["chapter_title"] or ""),
                "lineIndex": int(line["line_index"]),
                "lineNo": int(line["line_index"]) + 1,
                "rawLine": str(line["raw_line"]),
                "roleName": role_name_value,
                "lineText": str(line["line_text"]),
                "lineHash": line_hash,
                "canGenerate": bool(role_name_value) and has_role_library and has_role_sample,
                "roleInLibrary": has_role_library,
                "roleHasSampleAudio": has_role_sample,
                "task": _line_audio_task_row_to_dict(row) if row else None,
                "hasAudio": bool(
                    row
                    and str(row["status"] or "") == "completed"
                    and str(row["downloaded_file_path"] or "").strip()
                ),
            }
            item["streamUrl"] = (
                f"/api/line-audio-tasks/{int(row['id'])}/file" if item["hasAudio"] and row else ""
            )
            item["durationSeconds"] = (
                round(float(row["duration_seconds"] or 0), 1)
                if item["hasAudio"] and row
                else 0.0
            )
            items.append(item)

    total_count = len(items)
    page_count = (total_count + page_size - 1) // page_size if total_count else 0
    if page_count and page > page_count:
        page = page_count
    start = (page - 1) * page_size
    end = start + page_size
    paged_items = items[start:end]
    return {
        "items": paged_items,
        "totalCount": total_count,
        "page": page,
        "pageSize": page_size,
        "pageCount": page_count,
    }


def list_role_line_counts(novel_id: int, chapter_num: int | None = None) -> dict[str, int]:
    """统计指定范围内每个角色在最新已完成 JSON 中的台词数。"""
    conn = db_conn()
    chapter_filter = "AND c.chapter_num = ?" if chapter_num is not None else ""
    chapter_params: tuple[int, ...] = (novel_id, int(chapter_num)) if chapter_num is not None else (novel_id,)
    chapter_rows = conn.execute(
        """
        SELECT (
          SELECT jt.merged_result_json
          FROM json_tasks jt
          WHERE jt.novel_id = c.novel_id
            AND jt.chapter_num = c.chapter_num
            AND jt.status = 'completed'
            AND jt.merged_result_json IS NOT NULL
          ORDER BY jt.id DESC
          LIMIT 1
        ) AS merged_result_json
        FROM chapters c
        WHERE c.novel_id = ?
        {chapter_filter}
        ORDER BY c.chapter_num ASC, c.id ASC
        """.format(chapter_filter=chapter_filter),
        chapter_params,
    ).fetchall()
    conn.close()
    counts: dict[str, int] = {}
    for chapter in chapter_rows:
        merged_json = str(chapter["merged_result_json"] or "").strip()
        if not merged_json:
            continue
        for line in parse_juben_lines_from_json_text(merged_json):
            role_name = _normalize_role_name(line.get("role_name"))
            if role_name:
                counts[role_name] = counts.get(role_name, 0) + 1
    return counts


def invalidate_obsolete_chapter_line_audio_tasks(
    novel_id: int, chapter_id: int, json_text: str
) -> int:
    """将已不属于当前章节剧本的台词音频任务标记为失效"""
    active_hashes = {
        str(item["line_hash"])
        for item in parse_juben_lines_from_json_text(str(json_text or ""))
    }

    conn = db_conn()
    rows = conn.execute(
        "SELECT id, line_hash FROM line_audio_tasks WHERE novel_id=? AND chapter_id=?",
        (novel_id, chapter_id),
    ).fetchall()
    obsolete_ids = [
        int(row["id"])
        for row in rows
        if str(row["line_hash"] or "").strip() not in active_hashes
    ]

    if obsolete_ids:
        placeholders = ",".join("?" for _ in obsolete_ids)
        conn.execute(
            f"""
            UPDATE line_audio_tasks
            SET status='cancelled', comfy_status='cancelled',
                error_message='台词已修改，旧音频任务已失效',
                updated_at=CURRENT_TIMESTAMP,
                comfy_finished_at=CASE
                    WHEN status IN ('running', 'processing') AND comfy_finished_at IS NULL THEN CURRENT_TIMESTAMP
                    ELSE comfy_finished_at
                END
            WHERE id IN ({placeholders})
            """,
            obsolete_ids,
        )

    conn.commit()
    conn.close()

    return len(obsolete_ids)


def is_chapter_merged_audio_stale(novel_id: int, chapter_id: int) -> bool:
    merged_path = get_chapter_merged_audio_path(novel_id, chapter_id)
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT downloaded_file_path
        FROM line_audio_tasks
        WHERE novel_id=? AND chapter_id=? AND status='completed'
        """,
        (novel_id, chapter_id),
    ).fetchall()
    conn.close()

    completed_paths: list[Path] = []
    for row in rows:
        file_path = str(row["downloaded_file_path"] or "").strip()
        if not file_path:
            continue
        abs_path = (ROOT_DIR / file_path).resolve()
        if not abs_path.exists() or not abs_path.is_file():
            continue
        completed_paths.append(abs_path)

    if not merged_path or not merged_path.exists() or not merged_path.is_file():
        return bool(completed_paths)

    merged_mtime = merged_path.stat().st_mtime

    for abs_path in completed_paths:
        if abs_path.stat().st_mtime > merged_mtime:
            return True
    return False


def enqueue_line_audio_task(
    novel_id: int, chapter_id: int, line_index: int, scheduled_at: str = ""
) -> tuple[bool, str, int | None]:
    """将单个台词加入音频生成队列"""
    conn = db_conn()

    # 获取章节信息
    chapter = conn.execute(
        """
        SELECT c.id, c.chapter_num, c.title, n.english_dir,
               t.merged_result_json
        FROM chapters c
        JOIN novels n ON n.id = c.novel_id
        LEFT JOIN json_tasks t ON t.novel_id = c.novel_id
            AND t.chapter_num = c.chapter_num
            AND t.status = 'completed'
            AND t.merged_result_json IS NOT NULL
        WHERE c.novel_id = ? AND c.id = ?
        ORDER BY t.id DESC
        LIMIT 1
        """,
        (novel_id, chapter_id),
    ).fetchone()

    if not chapter:
        conn.close()
        return False, "章节不存在", None

    merged_json = str(chapter["merged_result_json"] or "").strip()
    lines = parse_juben_lines_from_json_text(merged_json)

    if not lines:
        conn.close()
        return False, "当前章节没有可用的剧本数据", None

    if line_index < 0:
        conn.close()
        return False, "行号超出范围", None

    line = next(
        (item for item in lines if int(item.get("line_index", -1)) == int(line_index)),
        None,
    )
    if line is None:
        conn.close()
        return False, "行号超出范围", None
    role_name = _normalize_role_name(line["role_name"])
    line_text = _clean_line_text(line["line_text"])

    if not role_name:
        conn.close()
        return False, "台词缺少角色名", None
    if not line_text:
        conn.close()
        return False, "台词内容为空", None

    # 获取角色信息
    role_map = get_novel_role_library_map(novel_id)
    role = role_map.get(role_name)
    if not role:
        conn.close()
        return False, f"角色未加入角色库: {role_name}", None

    sample_text = str(role.get("sample_text") or "").strip()
    sample_audio_path = str(role.get("sample_audio_path") or "").strip()

    if not sample_audio_path:
        conn.close()
        return False, f"角色缺少声音示例: {role_name}", None

    line_hash = str(line["line_hash"])
    existing = _get_existing_line_task(novel_id, chapter_id, line_hash)
    schedule_text = str(scheduled_at or "").strip()

    if existing:
        # 更新现有任务
        conn.execute(
            """
            UPDATE line_audio_tasks
            SET chapter_title=?, line_index=?, role_name=?, line_text=?,
                reference_text=?, reference_audio_path=?, status='pending', comfy_status='queued',
                comfy_prompt_id=NULL, output_filename='', output_subfolder='', output_type='',
                downloaded_file_path='', duration_seconds=0, queue_priority=0,
                error_message=NULL, comfy_started_at=NULL,
                scheduled_at=?,
                comfy_finished_at=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                str(chapter["title"]),
                int(line["line_index"]),
                role_name,
                line_text,
                sample_text,
                sample_audio_path,
                schedule_text,
                int(existing["id"]),
            ),
        )
        task_id = int(existing["id"])
    else:
        # 创建新任务
        cur = conn.execute(
            """
            INSERT INTO line_audio_tasks(
                novel_id, chapter_id, chapter_num, chapter_title,
                line_index, role_name, line_text,
                reference_text, reference_audio_path, line_hash, status, comfy_status, scheduled_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'queued', ?)
            """,
            (
                novel_id,
                chapter_id,
                int(chapter["chapter_num"]),
                str(chapter["title"]),
                int(line["line_index"]),
                role_name,
                line_text,
                sample_text,
                sample_audio_path,
                line_hash,
                schedule_text,
            ),
        )
        task_id = int(cur.lastrowid or 0)

    conn.commit()
    conn.close()

    if task_id <= 0:
        return False, "创建台词音频任务失败", None
    return True, "queued", task_id


def enqueue_all_line_audio_tasks(
    novel_id: int, chapter_id: int, scheduled_at: str = ""
) -> tuple[bool, str, dict]:
    """将章节所有可生成的台词加入音频生成队列"""
    entries = get_chapter_line_audio_entries(novel_id, chapter_id)
    if not entries:
        return (
            False,
            "当前章节没有可用的台词数据",
            {"queued": 0, "taskIds": [], "skipped": []},
        )

    queued = 0
    skipped: list[str] = []
    task_ids: list[int] = []

    for entry in entries:
        if not entry.get("canGenerate"):
            reason = "角色未配置"
            if not entry.get("roleInLibrary"):
                reason = "角色未入库"
            elif not entry.get("roleHasSampleAudio"):
                reason = "角色无声音示例"
            skipped.append(f"{entry['lineNo']}:{reason}")
            continue

        ok, msg, task_id = enqueue_line_audio_task(
            novel_id, chapter_id, entry["lineIndex"], scheduled_at=scheduled_at
        )
        if ok and task_id:
            queued += 1
            task_ids.append(task_id)
        else:
            skipped.append(f"{entry['lineNo']}:{msg}")

    return True, "queued", {"queued": queued, "taskIds": task_ids, "skipped": skipped}


def _get_line_audio_workflow_info(novel_id: int) -> tuple[dict, dict, str, str, bool]:
    """获取小说的台词音频工作流JSON"""
    conn = db_conn()
    row = conn.execute(
        """
        SELECT w.json_text, w.workflow_io_config, w.name, w.workflow_type, w.workflow_log_enabled
        FROM novels n
        LEFT JOIN comfy_workflows w ON w.id = n.line_audio_workflow_id
        WHERE n.id = ?
        """,
        (novel_id,),
    ).fetchone()
    conn.close()

    if row and row["json_text"]:
        try:
            io_config = json.loads(str(row["workflow_io_config"] or "{}") or "{}")
            if not isinstance(io_config, dict):
                io_config = {}
            return (
                json.loads(str(row["json_text"])),
                io_config,
                str(row["name"] or ""),
                str(row["workflow_type"] or "line_audio"),
                bool(int(row["workflow_log_enabled"] or 0)),
            )
        except json.JSONDecodeError:
            pass
    return {}, {}, "", "line_audio", True


def process_line_audio_task(task_id: int) -> None:
    """处理单个台词音频任务"""
    conn = db_conn()
    row = conn.execute(
        "SELECT * FROM line_audio_tasks WHERE id=?", (task_id,)
    ).fetchone()
    conn.close()

    if not row:
        return

    current_status = str(row["status"] or "")
    if current_status not in {"running", "processing"}:
        return

    novel_id = int(row["novel_id"])
    chapter_id = int(row["chapter_id"])
    chapter_num = int(row["chapter_num"])
    reference_audio_path = str(row["reference_audio_path"] or "").strip()
    line_text = _clean_line_text(row["line_text"])
    reference_text = str(row["reference_text"] or "").strip()
    line_hash = str(row["line_hash"] or "").strip()
    existing_prompt_id = str(row["comfy_prompt_id"] or "").strip()
    existing_comfy_status = str(row["comfy_status"] or "").strip()
    workflow_log_id = 0

    if not reference_audio_path or not line_text or not line_hash:
        conn = db_conn()
        conn.execute(
            """
            UPDATE line_audio_tasks
            SET status='failed', comfy_status='failed', error_message=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            ("台词任务参数不完整", task_id),
        )
        conn.commit()
        conn.close()
        return

    try:
        settings = fetch_settings(db_conn())
        comfy_url = str(settings.get("comfyUrl") or "").strip()
        if not comfy_url:
            raise RuntimeError("ComfyUI URL 未配置")

        conn = db_conn()
        chapter_row = conn.execute(
            "SELECT n.english_dir FROM chapters c JOIN novels n ON n.id = c.novel_id WHERE c.id=?",
            (chapter_id,),
        ).fetchone()
        conn.close()
        if not chapter_row:
            raise RuntimeError("章节不存在")
        english_dir = str(chapter_row["english_dir"] or "").strip()
        if not english_dir:
            raise RuntimeError("小说目录未配置")

        # 检查参考音频文件
        ref_path = (ROOT_DIR / reference_audio_path).resolve()
        root_resolved = ROOT_DIR.resolve()
        if root_resolved not in ref_path.parents and ref_path != root_resolved:
            raise RuntimeError("无效的参考音频路径")
        if not ref_path.exists() or not ref_path.is_file():
            raise RuntimeError("参考声音文件不存在")

        output_node = "41"
        prompt_id = existing_prompt_id
        if not (
            current_status == "processing"
            and existing_prompt_id
            and existing_comfy_status == "running"
        ):
            conn = db_conn()
            conn.execute(
                """
                UPDATE line_audio_tasks
                SET status='processing', comfy_status='submitting', error_message=NULL,
                    comfy_started_at=NULL, comfy_finished_at=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (task_id,),
            )
            conn.commit()
            conn.close()

            # 上传参考音频
            upload_info = comfy_upload_input_file(ref_path.name, ref_path.read_bytes())
            filename = (
                str(upload_info.get("name") or ref_path.name).strip() or ref_path.name
            )
            subfolder = str(upload_info.get("subfolder") or "").strip()
            file_type = str(upload_info.get("type") or "input").strip() or "input"

            # 获取工作流并填充
            (
                workflow_json,
                workflow_io_config,
                workflow_name,
                workflow_category,
                workflow_log_enabled,
            ) = _get_line_audio_workflow_info(novel_id)
            workflow = workflow_json_to_prompt_json(deepcopy(workflow_json))
            if not workflow:
                raise RuntimeError("台词音频工作流未配置")
            if workflow_log_enabled:
                workflow_log_id = create_workflow_log(
                    workflow_category or "line_audio",
                    workflow_name or "生成台词音频",
                    workflow,
                )

            # 优先兼容旧项目工作流节点ID，再回退到启发式匹配
            audio_input_node = (
                str(
                    workflow_io_config.get("inputs", {})
                    .get("referenceAudio", {})
                    .get("nodeId")
                    or ("27" if "27" in workflow else "")
                ).strip()
                or None
            )
            text_prompt_node = (
                str(
                    workflow_io_config.get("inputs", {})
                    .get("lineText", {})
                    .get("nodeId")
                    or ("33" if "33" in workflow else "")
                ).strip()
                or None
            )
            ref_text_node = None
            if reference_text:
                ref_text_node_configured = str(
                    workflow_io_config.get("inputs", {})
                    .get("referenceText", {})
                    .get("nodeId")
                    or ""
                ).strip()
                ref_text_node = (
                    ref_text_node_configured
                    if ref_text_node_configured in workflow
                    else ""
                    or None
                )
            output_node = (
                str(
                    workflow_io_config.get("outputs", {})
                    .get("audioFile", {})
                    .get("nodeId")
                    or ("41" if "41" in workflow else "")
                ).strip()
                or None
            )

            for node_id, node in workflow.items():
                if not isinstance(node, dict):
                    continue
                inputs = node.get("inputs", {})
                if "audio" in inputs and audio_input_node is None:
                    audio_input_node = node_id
                if "prompt" in inputs and text_prompt_node is None:
                    text_prompt_node = node_id
                if "filename_prefix" in inputs and output_node is None:
                    output_node = node_id

            if not audio_input_node:
                raise RuntimeError("台词音频工作流缺少音频输入节点")
            if not text_prompt_node:
                raise RuntimeError("台词音频工作流缺少目标文本节点")
            if not output_node:
                raise RuntimeError("台词音频工作流缺少音频输出节点")

            workflow[audio_input_node]["inputs"]["audio"] = filename
            if "audioUI" in workflow[audio_input_node]["inputs"]:
                workflow[audio_input_node]["inputs"]["audioUI"] = (
                    f"/api/view?filename={filename}&type={file_type}&subfolder={subfolder}&rand={time.time():.6f}"
                )
            _assign_text_input(workflow, text_prompt_node, line_text, "目标文本")
            if reference_text and ref_text_node:
                _assign_text_input(workflow, ref_text_node, reference_text, "参考文本")
            workflow[output_node]["inputs"]["filename_prefix"] = (
                _comfy_line_audio_prefix(english_dir, chapter_id)
            )

            for node in workflow.values():
                if not isinstance(node, dict):
                    continue
                inputs = node.get("inputs", {})
                if "seed" in inputs:
                    inputs["seed"] = random.randint(0, 2**31 - 1)

            if workflow_log_enabled:
                update_workflow_log_json(workflow_log_id, workflow)

            submit_result = comfy_request_json(
                comfy_url=comfy_url,
                path="/prompt",
                method="POST",
                payload={"prompt": workflow},
            )
            prompt_id = str(submit_result.get("prompt_id") or "").strip()
            if not prompt_id:
                update_workflow_log_error(workflow_log_id, "ComfyUI 未返回 prompt_id")
                raise RuntimeError("ComfyUI 未返回 prompt_id")

            conn = db_conn()
            conn.execute(
                """
                UPDATE line_audio_tasks
                SET comfy_prompt_id=?, comfy_status='running',
                    comfy_started_at=CURRENT_TIMESTAMP, comfy_finished_at=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (prompt_id, task_id),
            )
            conn.commit()
            conn.close()

        # 等待工作流完成。短台词异常拖长通常意味着 ComfyUI 卡住或尾部静音，尽早中断。
        is_short_line_timeout = _line_text_char_count(line_text) <= 10
        started = time.time()
        timeout_seconds = 15 if is_short_line_timeout else 60 * 60
        poll_interval = 1 if is_short_line_timeout else 3
        output_info = None

        while time.time() - started < timeout_seconds:
            remaining_seconds = max(0.1, timeout_seconds - (time.time() - started))
            try:
                history = comfy_request_json(
                    comfy_url=comfy_url,
                    path=f"/history/{prompt_id}",
                    method="GET",
                    timeout=min(120.0, max(1.0, remaining_seconds)),
                )
            except Exception as exc:
                if time.time() - started >= timeout_seconds:
                    interrupt_result = _interrupt_comfy_before_timeout_failure(comfy_url)
                    if is_short_line_timeout:
                        raise TimeoutError(
                            f"短台词 ComfyUI 工作流超过 15 秒未完成，{interrupt_result}"
                        ) from exc
                    raise TimeoutError(
                        f"ComfyUI 工作流超时，未找到音频输出，{interrupt_result}"
                    ) from exc
                raise
            history_error = _extract_comfy_history_error(history, prompt_id)
            if history_error:
                raise RuntimeError(history_error)
            output_info = extract_audio_output_from_history(
                history, prompt_id, node_id=str(output_node)
            )
            if output_info is not None:
                break
            time.sleep(min(poll_interval, max(0.1, timeout_seconds - (time.time() - started))))

        if output_info is None:
            interrupt_result = _interrupt_comfy_before_timeout_failure(comfy_url)
            if is_short_line_timeout:
                raise TimeoutError(
                    f"短台词 ComfyUI 工作流超过 15 秒未完成，{interrupt_result}"
                )
            raise TimeoutError(f"ComfyUI 工作流超时，未找到音频输出，{interrupt_result}")

        # 下载音频文件
        out_filename, out_subfolder, out_type = output_info
        data = comfy_download_file(
            comfy_url=comfy_url,
            filename=out_filename,
            subfolder=out_subfolder,
            file_type=out_type,
        )

        # 保存到临时目录
        temp_dir = _chapter_temp_dir(english_dir, chapter_num)
        temp_dir.mkdir(parents=True, exist_ok=True)
        local_path = _chapter_line_audio_path(english_dir, chapter_num, line_hash)
        local_path.write_bytes(data)

        rel_path = str(local_path.relative_to(ROOT_DIR))
        duration_seconds = round(float(probe_audio_duration_seconds(local_path)), 1)

        # 更新任务完成状态
        conn = db_conn()
        cur = conn.execute(
            """
            UPDATE line_audio_tasks
            SET status='completed', comfy_status='completed',
                output_filename=?, output_subfolder=?, output_type=?,
                downloaded_file_path=?, duration_seconds=?, error_message=NULL,
                comfy_finished_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status IN ('running', 'processing')
            """,
            (out_filename, out_subfolder, out_type, rel_path, duration_seconds, task_id),
        )
        conn.commit()
        conn.close()
        if int(cur.rowcount or 0) <= 0:
            return

    except Exception as exc:
        update_workflow_log_error(workflow_log_id, str(exc))
        conn = db_conn()
        conn.execute(
            """
            UPDATE line_audio_tasks
            SET status='failed', comfy_status='failed', error_message=?,
                updated_at=CURRENT_TIMESTAMP,
                comfy_finished_at=CASE
                    WHEN comfy_started_at IS NOT NULL AND comfy_finished_at IS NULL THEN CURRENT_TIMESTAMP
                    ELSE comfy_finished_at
                END
            WHERE id=? AND status IN ('running', 'processing')
            """,
            (str(exc), task_id),
        )
        conn.commit()
        conn.close()


def get_chapter_merged_audio_path(
    novel_id: int, chapter_id: int, include_copyright: bool = True
) -> Path | None:
    """获取章节合并后的音频文件路径"""
    conn = db_conn()
    row = conn.execute(
        "SELECT n.english_dir, c.chapter_num FROM chapters c JOIN novels n ON n.id = c.novel_id WHERE c.id = ?",
        (chapter_id,),
    ).fetchone()
    conn.close()

    if not row:
        return None

    english_dir = str(row["english_dir"])
    chapter_num = int(row["chapter_num"])

    path = (
        _chapter_merged_output_path(english_dir, chapter_num)
        if include_copyright
        else _chapter_merged_output_path_non_ver(english_dir, chapter_num)
    )
    if path.exists() and path.is_file() and path.stat().st_size > 0:
        return path

    if include_copyright:
        legacy_path = (
            ROOT_DIR / "temp" / english_dir / "audio" / str(chapter_num) / "merged.flac"
        )
        if (
            legacy_path.exists()
            and legacy_path.is_file()
            and legacy_path.stat().st_size > 0
        ):
            return legacy_path
    return None


def merge_chapter_line_audio(
    novel_id: int, chapter_id: int, include_copyright: bool = True
) -> tuple[bool, str, str | None]:
    """合并章节所有台词音频为一个文件"""
    # 获取章节信息
    conn = db_conn()
    chapter = conn.execute(
        """
        SELECT c.id, c.chapter_num, c.title, n.english_dir,
               t.merged_result_json
        FROM chapters c
        JOIN novels n ON n.id = c.novel_id
        LEFT JOIN json_tasks t ON t.novel_id = c.novel_id
            AND t.chapter_num = c.chapter_num
            AND t.status = 'completed'
            AND t.merged_result_json IS NOT NULL
        WHERE c.novel_id = ? AND c.id = ?
        ORDER BY t.id DESC
        LIMIT 1
        """,
        (novel_id, chapter_id),
    ).fetchone()
    conn.close()

    if not chapter:
        return False, "章节不存在", None

    merged_json = str(chapter["merged_result_json"] or "").strip()
    lines = parse_juben_lines_from_json_text(merged_json)

    if not lines:
        return False, "该章节没有可用台词", None

    english_dir = str(chapter["english_dir"])
    chapter_num = int(chapter["chapter_num"])

    # 检查所有音频是否都已生成
    temp_dir = ROOT_DIR / "temp" / english_dir / "audio" / str(chapter_num)
    missing_count = 0
    files: list[Path] = []

    for line in lines:
        line_hash = str(line["line_hash"])
        path = temp_dir / f"{line_hash}.flac"
        if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            missing_count += 1
        else:
            files.append(path)

    if missing_count > 0:
        return False, f"还有 {missing_count} 条台词未生成音频", None

    intro_path = None
    outro_path = None
    if include_copyright:
        settings_conn = db_conn()
        settings = fetch_settings(settings_conn)
        settings_conn.close()
        copyright_audio = settings.get("copyrightAudio") or {}

        intro_rel = str(copyright_audio.get("introPath") or "").strip()
        if copyright_audio.get("introEnabled") and intro_rel:
            candidate = (ROOT_DIR / intro_rel).resolve()
            if candidate.exists() and candidate.is_file():
                intro_path = candidate

        outro_rel = str(copyright_audio.get("outroPath") or "").strip()
        if copyright_audio.get("outroEnabled") and outro_rel:
            candidate = (ROOT_DIR / outro_rel).resolve()
            if candidate.exists() and candidate.is_file():
                outro_path = candidate

    def prepare_merge_audio(
        source_path: Path, target_path: Path
    ) -> tuple[bool, str, Path | None]:
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(source_path),
                    "-ar",
                    "44100",
                    "-ac",
                    "1",
                    "-c:a",
                    "flac",
                    str(target_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True, "ok", target_path
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            return False, str(exc), None

    # 创建间隔静音文件
    temp_dir.mkdir(parents=True, exist_ok=True)
    silence_path = temp_dir / "__gap_500ms.flac"
    if not silence_path.exists() or silence_path.stat().st_size <= 0:
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=mono",
                    "-t",
                    "0.5",
                    str(silence_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            return False, f"创建静音文件失败: {exc}", None

    if intro_path is not None:
        ok, msg, normalized_intro = prepare_merge_audio(
            intro_path,
            temp_dir / "__intro_normalized.flac",
        )
        if not ok or normalized_intro is None:
            return False, f"处理章回开头音频失败: {msg}", None
        intro_path = normalized_intro

    if outro_path is not None:
        ok, msg, normalized_outro = prepare_merge_audio(
            outro_path,
            temp_dir / "__outro_normalized.flac",
        )
        if not ok or normalized_outro is None:
            return False, f"处理章回结尾音频失败: {msg}", None
        outro_path = normalized_outro

    def concat_file_line(path: Path) -> str:
        safe = str(path).replace("'", "'\\''")
        return f"file '{safe}'\n"

    # 创建合并列表文件
    concat_list_path = temp_dir / "__concat_list.txt"
    with concat_list_path.open("w", encoding="utf-8") as fp:
        if intro_path is not None:
            fp.write(concat_file_line(intro_path))
            fp.write(concat_file_line(silence_path))
        for idx, path in enumerate(files):
            fp.write(concat_file_line(path))
            if idx < len(files) - 1 or outro_path is not None:
                fp.write(concat_file_line(silence_path))
        if outro_path is not None:
            fp.write(concat_file_line(outro_path))

    # 执行合并
    output_dir = (
        _novel_audio_output_dir(english_dir)
        if include_copyright
        else _novel_audio_output_dir_non_ver(english_dir)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = (
        _chapter_merged_output_path(english_dir, chapter_num)
        if include_copyright
        else _chapter_merged_output_path_non_ver(english_dir, chapter_num)
    )
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list_path),
                "-c:a",
                "flac",
                str(output_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return False, f"合并音频失败: {exc}", None

    if not include_copyright:
        conn = db_conn()
        try:
            update_chapter_non_ver_audio_duration_cache(conn, int(chapter["id"]), output_path)
            update_novel_total_non_ver_audio_duration_seconds(conn, int(chapter["novel_id"]))
            conn.commit()
        finally:
            conn.close()

    rel_path = str(output_path.relative_to(ROOT_DIR))
    return True, "merged", rel_path


def get_chapter_merged_audio_stats(
    novel_id: int, chapter_id: int, include_copyright: bool = True
) -> dict:
    path = get_chapter_merged_audio_path(
        novel_id, chapter_id, include_copyright=include_copyright
    )
    if not path:
        return {
            "hasAudio": False,
            "sizeBytes": 0,
            "durationSeconds": 0.0,
            "relPath": "",
        }
    return {
        "hasAudio": True,
        "sizeBytes": int(path.stat().st_size),
        "durationSeconds": float(probe_audio_duration_seconds(path)),
        "relPath": db_rel_path(path.relative_to(ROOT_DIR)),
    }


def run_line_audio_queue_once() -> bool:
    """处理一次台词音频队列"""
    conn = db_conn()

    # 检查是否有运行中或处理中任务；处理中任务在服务重启后也要能恢复
    running = conn.execute(
        "SELECT id FROM line_audio_tasks WHERE status IN ('running','processing') ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if running:
        task_id = int(running["id"])
        conn.close()
        process_line_audio_task(task_id)
        return True

    # 获取待处理任务
    pending_rows = conn.execute(
        """
        SELECT id,scheduled_at
        FROM line_audio_tasks
        WHERE status='pending'
        ORDER BY COALESCE(queue_priority, 0) DESC, id ASC
        """
    ).fetchall()
    picked_id: int | None = None
    now = time.time()
    for pending in pending_rows:
        scheduled = str(pending["scheduled_at"] or "").strip()
        dt = parse_datetime_utc(scheduled)
        if dt is None or dt.timestamp() <= now:
            picked_id = int(pending["id"])
            break
    if picked_id is None:
        conn.close()
        return False

    task_id = int(picked_id)
    conn.execute(
        """
        UPDATE line_audio_tasks
        SET status='running', comfy_status='queued', error_message=NULL,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (task_id,),
    )
    conn.commit()
    conn.close()

    process_line_audio_task(task_id)
    return True


def delete_line_audio_task(task_id: int) -> tuple[bool, str]:
    """删除台词音频任务"""
    conn = db_conn()
    row = conn.execute(
        "SELECT id, status FROM line_audio_tasks WHERE id=?", (task_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False, "任务不存在"

    if str(row["status"]) == "running":
        conn.close()
        return False, "无法删除运行中的任务"

    conn.execute("DELETE FROM line_audio_tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    return True, "deleted"


def retry_line_audio_task(task_id: int) -> tuple[bool, str]:
    """重试失败的台词音频任务"""
    conn = db_conn()
    row = conn.execute(
        "SELECT id, status FROM line_audio_tasks WHERE id=?", (task_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False, "任务不存在"

    current_status = str(row["status"] or "").strip()
    if current_status not in {"failed", "cancelled"}:
        conn.close()
        return False, "只有失败或已取消的任务可以重试"

    conn.execute(
        """
        UPDATE line_audio_tasks
        SET status='pending', comfy_status='queued', comfy_prompt_id='',
            output_filename='', output_subfolder='', output_type='',
            downloaded_file_path='', duration_seconds=0, queue_priority=0, error_message=NULL,
            comfy_started_at=NULL, comfy_finished_at=NULL,
            scheduled_at='', updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (task_id,),
    )
    conn.commit()
    conn.close()
    return True, "queued"


def prioritize_line_audio_task(task_id: int) -> tuple[bool, str]:
    """将待执行台词音频任务提升为当前任务结束后的优先任务"""
    conn = db_conn()
    row = conn.execute(
        "SELECT id, status FROM line_audio_tasks WHERE id=?", (task_id,)
    ).fetchone()
    if not row:
        conn.close()
        return False, "任务不存在"
    if str(row["status"] or "").strip() != "pending":
        conn.close()
        return False, "只有待执行任务可以优先执行"
    max_row = conn.execute(
        "SELECT COALESCE(MAX(queue_priority), 0) AS max_priority FROM line_audio_tasks"
    ).fetchone()
    next_priority = int(max_row["max_priority"] or 0) + 1 if max_row else 1
    conn.execute(
        """
        UPDATE line_audio_tasks
        SET queue_priority=?, scheduled_at='', updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND status='pending'
        """,
        (next_priority, task_id),
    )
    conn.commit()
    conn.close()
    return True, "prioritized"


def edit_line_audio_task_audio(
    task_id: int,
    *,
    mode: str,
    start_seconds: float,
    end_seconds: float,
    segments: list | None = None,
) -> tuple[bool, str, dict]:
    """编辑台词音频文件。支持保留选中片段或删除多个片段后替换原文件。"""
    edit_mode = str(mode or "keep").strip().lower()
    if edit_mode not in {"keep", "remove"}:
        return False, "不支持的音频编辑模式", {}
    start = max(0.0, float(start_seconds or 0))
    end = max(0.0, float(end_seconds or 0))
    if edit_mode == "keep" and (end <= start or end - start < 0.05):
        return False, "请选择有效的音频片段", {}

    conn = db_conn()
    row = conn.execute(
        "SELECT id, status, downloaded_file_path FROM line_audio_tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    conn.close()
    if not row:
        return False, "任务不存在", {}
    if str(row["status"] or "") != "completed":
        return False, "只有已完成任务可以编辑音频", {}
    rel_path = str(row["downloaded_file_path"] or "").strip()
    if not rel_path:
        return False, "任务没有可编辑的音频文件", {}

    audio_path = (ROOT_DIR / rel_path).resolve()
    root_resolved = ROOT_DIR.resolve()
    if root_resolved not in audio_path.parents and audio_path != root_resolved:
        return False, "无效的音频路径", {}
    if not audio_path.exists() or not audio_path.is_file():
        return False, "音频文件不存在", {}

    original_duration = float(probe_audio_duration_seconds(audio_path))
    if original_duration <= 0:
        return False, "无法读取音频时长", {}
    if edit_mode == "keep":
        if start >= original_duration:
            return False, "片段开始时间超出音频时长", {}
        end = min(end, original_duration)
        if end <= start or end - start < 0.05:
            return False, "请选择有效的音频片段", {}
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(audio_path),
            "-c:a",
            "flac",
        ]
    else:
        raw_segments = segments or []
        delete_segments: list[dict[str, float]] = []
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            try:
                segment_start = max(0.0, min(float(item.get("start") or 0), original_duration))
                segment_end = max(0.0, min(float(item.get("end") or 0), original_duration))
            except (TypeError, ValueError):
                continue
            if segment_end - segment_start >= 0.05:
                delete_segments.append({"start": segment_start, "end": segment_end})
        delete_segments.sort(key=lambda item: item["start"])
        normalized_segments: list[dict[str, float]] = []
        for item in delete_segments:
            if normalized_segments and item["start"] <= normalized_segments[-1]["end"] + 0.02:
                normalized_segments[-1]["end"] = max(normalized_segments[-1]["end"], item["end"])
            else:
                normalized_segments.append(dict(item))
        if not normalized_segments:
            return False, "请先标记要删除的音频片段", {}

        keep_segments: list[dict[str, float]] = []
        cursor = 0.0
        for item in normalized_segments:
            if item["start"] - cursor >= 0.05:
                keep_segments.append({"start": cursor, "end": item["start"]})
            cursor = max(cursor, item["end"])
        if original_duration - cursor >= 0.05:
            keep_segments.append({"start": cursor, "end": original_duration})
        if not keep_segments:
            return False, "不能删除整段音频", {}
        if len(keep_segments) == 1:
            keep = keep_segments[0]
            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{keep['start']:.3f}",
                "-to",
                f"{keep['end']:.3f}",
                "-i",
                str(audio_path),
                "-c:a",
                "flac",
            ]
        else:
            filter_parts = []
            concat_inputs = []
            for index, keep in enumerate(keep_segments):
                label = f"a{index}"
                filter_parts.append(
                    f"[0:a]atrim=start={keep['start']:.3f}:end={keep['end']:.3f},asetpts=PTS-STARTPTS[{label}]"
                )
                concat_inputs.append(f"[{label}]")
            filter_parts.append(
                f"{''.join(concat_inputs)}concat=n={len(keep_segments)}:v=0:a=1[outa]"
            )
            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(audio_path),
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[outa]",
                "-c:a",
                "flac",
            ]

    tmp_path = audio_path.with_name(f"{audio_path.stem}.edit-tmp{audio_path.suffix}")
    try:
        subprocess.run(
            [*ffmpeg_cmd, str(tmp_path)],
            check=True,
            capture_output=True,
        )
        if not tmp_path.exists() or tmp_path.stat().st_size <= 0:
            return False, "音频编辑输出为空", {}
        new_duration = round(float(probe_audio_duration_seconds(tmp_path)), 1)
        if new_duration <= 0:
            return False, "无法读取编辑后的音频时长", {}
        shutil.move(str(tmp_path), str(audio_path))
        conn = db_conn()
        conn.execute(
            """
            UPDATE line_audio_tasks
            SET duration_seconds=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (new_duration, task_id),
        )
        conn.commit()
        conn.close()
        return True, "edited", {"durationSeconds": new_duration}
    except subprocess.CalledProcessError as exc:
        error_text = (exc.stderr or b"").decode("utf-8", errors="ignore").strip()
        return False, error_text or "ffmpeg 音频编辑失败", {}
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def detect_line_audio_task_silences(
    task_id: int, *, noise_db: str = "-45dB", min_duration: float = 1.2
) -> tuple[bool, str, dict]:
    """使用 ffmpeg silencedetect 检测台词音频中的静音片段。"""
    duration = max(0.1, float(min_duration or 1.2))
    noise = str(noise_db or "-45dB").strip() or "-45dB"
    if not re.fullmatch(r"-?\d+(?:\.\d+)?dB", noise):
        return False, "invalid silence noise threshold", {}

    conn = db_conn()
    row = conn.execute(
        "SELECT id, status, downloaded_file_path FROM line_audio_tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    conn.close()
    if not row:
        return False, "任务不存在", {}
    if str(row["status"] or "") != "completed":
        return False, "只有已完成任务可以检测静音", {}
    rel_path = str(row["downloaded_file_path"] or "").strip()
    if not rel_path:
        return False, "任务没有可检测的音频文件", {}

    audio_path = (ROOT_DIR / rel_path).resolve()
    root_resolved = ROOT_DIR.resolve()
    if root_resolved not in audio_path.parents and audio_path != root_resolved:
        return False, "无效的音频路径", {}
    if not audio_path.exists() or not audio_path.is_file():
        return False, "音频文件不存在", {}

    audio_duration = float(probe_audio_duration_seconds(audio_path))
    if audio_duration <= 0:
        return False, "无法读取音频时长", {}

    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                str(audio_path),
                "-af",
                f"silencedetect=noise={noise}:d={duration:.3f}",
                "-f",
                "null",
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return False, f"ffmpeg 静音检测失败: {exc}", {}

    text = "\n".join([proc.stdout or "", proc.stderr or ""])
    segments: list[dict[str, float]] = []
    current_start: float | None = None
    for line in text.splitlines():
        start_match = re.search(r"silence_start:\s*([0-9.]+)", line)
        if start_match:
            current_start = float(start_match.group(1))
            continue
        end_match = re.search(r"silence_end:\s*([0-9.]+).*?silence_duration:\s*([0-9.]+)", line)
        if end_match and current_start is not None:
            end = float(end_match.group(1))
            if end - current_start >= duration:
                segments.append(
                    {
                        "start": round(max(0.0, min(current_start, audio_duration)), 3),
                        "end": round(max(0.0, min(end, audio_duration)), 3),
                    }
                )
            current_start = None

    if current_start is not None and audio_duration - current_start >= duration:
        segments.append(
            {
                "start": round(max(0.0, min(current_start, audio_duration)), 3),
                "end": round(audio_duration, 3),
            }
        )

    return True, "detected", {
        "segments": segments,
        "durationSeconds": round(audio_duration, 1),
        "noiseDb": noise,
        "minDuration": duration,
    }


def cancel_all_line_audio_tasks(novel_id: int | None = None) -> dict:
    """取消所有待处理或运行中的台词音频任务"""
    conn = db_conn()

    params = []
    where_clause = "status IN ('pending', 'running', 'processing')"
    if novel_id is not None:
        where_clause += " AND novel_id = ?"
        params.append(novel_id)

    rows = conn.execute(
        f"SELECT id, status FROM line_audio_tasks WHERE {where_clause}",
        params,
    ).fetchall()

    if not rows:
        conn.close()
        return {
            "cancelledCount": 0,
            "message": "没有需要终止的任务",
        }

    has_running = any(str(r["status"]) == "running" for r in rows)
    conn.close()

    # 如果有运行中的任务，尝试中断
    if has_running:
        settings = fetch_settings(db_conn())
        comfy_url = str(settings.get("comfyUrl") or "").strip()
        if comfy_url:
            try:
                comfy_request_json(
                    comfy_url=comfy_url,
                    path="/interrupt",
                    method="POST",
                    payload={},
                )
            except Exception:
                pass

    # 更新任务状态
    conn = db_conn()
    conn.execute(
        f"""
        UPDATE line_audio_tasks
        SET status='cancelled', comfy_status='cancelled', error_message='任务被用户终止',
            updated_at=CURRENT_TIMESTAMP
        WHERE {where_clause}
        """,
        params,
    )
    cancelled_count = int(conn.total_changes)
    conn.commit()
    conn.close()

    return {
        "cancelledCount": cancelled_count,
        "message": f"已终止 {cancelled_count} 个台词音频任务",
    }
