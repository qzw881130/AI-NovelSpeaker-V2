"""台词音频服务模块"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import shutil
import struct
import subprocess
import tempfile
import time
import wave
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


SILERO_VAD_ONNX_PATH = ROOT_DIR / "models" / "silero_vad.onnx"
LINE_AUDIO_NOISE_MODEL_PATH = ROOT_DIR / "models" / "line_audio_noise_classifier.json"
LINE_AUDIO_NOISE_SAMPLE_DIR = ROOT_DIR / "models" / "line_audio_noise_samples"
LINE_AUDIO_NOISE_SAMPLE_LOG_PATH = ROOT_DIR / "data" / "line_audio_noise_samples.jsonl"


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
        ORDER BY COALESCE(queue_priority, 0) DESC, chapter_id ASC, line_index ASC, id ASC
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

    current_status = str(row["status"] or "").strip()
    if current_status in {"running", "processing"}:
        settings = fetch_settings(conn)
        comfy_url = str(settings.get("comfyUrl") or "").strip()
        if comfy_url:
            try:
                comfy_interrupt_execution(comfy_url)
            except Exception:
                pass

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
    volume_factor: float = 1.0,
    speed_factor: float = 1.0,
    segments: list | None = None,
    collect_training_samples: bool = True,
) -> tuple[bool, str, dict]:
    """编辑台词音频文件。支持裁剪、删除片段、局部静音、调整音量或语速后替换原文件。"""
    edit_mode = str(mode or "keep").strip().lower()
    if edit_mode not in {"keep", "remove", "silence", "volume", "speed"}:
        return False, "不支持的音频编辑模式", {}
    start = max(0.0, float(start_seconds or 0))
    end = max(0.0, float(end_seconds or 0))
    if edit_mode in {"keep", "silence"} and (end <= start or end - start < 0.05):
        return False, "请选择有效的音频片段", {}
    gain = float(volume_factor or 1.0)
    if not math.isfinite(gain):
        return False, "无效的音量倍率", {}
    gain = max(0.1, min(gain, 4.0))
    speed = float(speed_factor or 1.0)
    if not math.isfinite(speed):
        return False, "无效的语速倍率", {}
    speed = max(0.8, min(speed, 1.2))

    conn = db_conn()
    row = conn.execute(
        "SELECT id, status, downloaded_file_path, line_text FROM line_audio_tasks WHERE id=?",
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
    accepted_delete_segments: list[dict[str, float]] = []
    if edit_mode == "volume":
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-af",
            f"volume={gain:.3f}",
            "-c:a",
            "flac",
        ]
    elif edit_mode == "speed":
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-filter:a",
            f"atempo={speed:.3f}",
            "-c:a",
            "flac",
        ]
    elif edit_mode == "silence":
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
            "-i",
            str(audio_path),
            "-af",
            f"volume=0:enable='between(t\\,{start:.3f}\\,{end:.3f})'",
            "-c:a",
            "flac",
        ]
    elif edit_mode == "keep":
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
        delete_segments: list[dict[str, Any]] = []
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            try:
                segment_start = max(0.0, min(float(item.get("start") or 0), original_duration))
                segment_end = max(0.0, min(float(item.get("end") or 0), original_duration))
            except (TypeError, ValueError):
                continue
            if segment_end - segment_start >= 0.05:
                delete_segments.append(
                    {
                        "start": segment_start,
                        "end": segment_end,
                        "type": str(item.get("type") or "").strip(),
                        "reason": str(item.get("reason") or "").strip(),
                    }
                )
        delete_segments.sort(key=lambda item: item["start"])
        normalized_segments: list[dict[str, Any]] = []
        for item in delete_segments:
            if normalized_segments and item["start"] <= normalized_segments[-1]["end"] + 0.02:
                normalized_segments[-1]["end"] = max(normalized_segments[-1]["end"], item["end"])
                if normalized_segments[-1].get("type") != item.get("type"):
                    normalized_segments[-1]["type"] = "mixed"
            else:
                normalized_segments.append(dict(item))
        if not normalized_segments:
            return False, "请先标记要删除的音频片段", {}
        accepted_delete_segments = [dict(item) for item in normalized_segments]

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
        if edit_mode == "remove" and accepted_delete_segments and collect_training_samples:
            _collect_line_audio_noise_training_samples(
                audio_path,
                task_id=task_id,
                line_text=str(row["line_text"] or ""),
                segments=accepted_delete_segments,
            )
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


def _find_matching_line_audio_rows(source_task_id: int) -> tuple[bool, str, dict]:
    conn = db_conn()
    source = conn.execute(
        """
        SELECT t.*, c.title AS source_chapter_title, n.english_dir
        FROM line_audio_tasks t
        JOIN chapters c ON c.id = t.chapter_id
        JOIN novels n ON n.id = t.novel_id
        WHERE t.id=?
        """,
        (source_task_id,),
    ).fetchone()
    if not source:
        conn.close()
        return False, "任务不存在", {}
    if str(source["status"] or "") != "completed":
        conn.close()
        return False, "只有已完成任务可以替换其他台词音频", {}
    source_rel_path = str(source["downloaded_file_path"] or "").strip()
    if not source_rel_path:
        conn.close()
        return False, "当前任务没有可替换的音频文件", {}
    source_audio_path = (ROOT_DIR / source_rel_path).resolve()
    root_resolved = ROOT_DIR.resolve()
    if root_resolved not in source_audio_path.parents and source_audio_path != root_resolved:
        conn.close()
        return False, "无效的音频路径", {}
    if not source_audio_path.exists() or not source_audio_path.is_file():
        conn.close()
        return False, "当前任务音频文件不存在", {}

    novel_id = int(source["novel_id"])
    target_role = _normalize_role_name(source["role_name"])
    target_text = _clean_line_text(source["line_text"])
    if not target_role or not target_text:
        conn.close()
        return False, "当前台词缺少角色或文本", {}

    chapters = conn.execute(
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
        WHERE c.novel_id=?
        ORDER BY c.chapter_num ASC, c.id ASC
        """,
        (novel_id,),
    ).fetchall()
    existing_rows = conn.execute(
        "SELECT * FROM line_audio_tasks WHERE novel_id=? ORDER BY id DESC",
        (novel_id,),
    ).fetchall()
    conn.close()

    existing_by_key: dict[tuple[int, str], Any] = {}
    for row in existing_rows:
        key = (int(row["chapter_id"]), str(row["line_hash"] or ""))
        existing_by_key.setdefault(key, row)

    matches: list[dict] = []
    chapter_counts: dict[int, dict] = {}
    for chapter in chapters:
        merged_json = str(chapter["merged_result_json"] or "").strip()
        if not merged_json:
            continue
        chapter_id = int(chapter["chapter_id"])
        for line in parse_juben_lines_from_json_text(merged_json):
            role_name = _normalize_role_name(line.get("role_name"))
            line_text = _clean_line_text(line.get("line_text"))
            if role_name != target_role or line_text != target_text:
                continue
            line_index = int(line.get("line_index", -1))
            if chapter_id == int(source["chapter_id"]) and line_index == int(source["line_index"]):
                continue
            line_hash = str(line.get("line_hash") or "")
            if not line_hash:
                continue
            raw_line = str(line.get("raw_line") or "")
            existing = existing_by_key.get((chapter_id, line_hash))
            chapter_num = int(chapter["chapter_num"])
            chapter_info = chapter_counts.setdefault(
                chapter_num,
                {
                    "chapterId": chapter_id,
                    "chapterNum": chapter_num,
                    "chapterTitle": str(chapter["chapter_title"] or ""),
                    "count": 0,
                },
            )
            chapter_info["count"] += 1
            matches.append(
                {
                    "chapterId": chapter_id,
                    "chapterNum": chapter_num,
                    "chapterTitle": str(chapter["chapter_title"] or ""),
                    "lineIndex": line_index,
                    "lineNo": line_index + 1,
                    "rawLine": raw_line,
                    "roleName": role_name,
                    "lineText": line_text,
                    "lineHash": line_hash,
                    "existingTaskId": int(existing["id"]) if existing else 0,
                }
            )

    source_dict = _line_audio_task_row_to_dict(source)
    source_dict["audioPath"] = str(source_audio_path)
    source_dict["englishDir"] = str(source["english_dir"] or "")
    return True, "ok", {
        "source": source_dict,
        "matches": matches,
        "chapters": sorted(chapter_counts.values(), key=lambda item: item["chapterNum"]),
        "totalCount": len(matches),
    }


def preview_line_audio_replacement_targets(task_id: int) -> tuple[bool, str, dict]:
    """预检同角色+台词的其他出现位置。"""
    ok, msg, data = _find_matching_line_audio_rows(task_id)
    if not ok:
        return ok, msg, {}
    return True, "ok", {
        "source": {
            "taskId": int(data["source"].get("id") or 0),
            "roleName": str(data["source"].get("roleName") or ""),
            "lineText": str(data["source"].get("lineText") or ""),
            "chapterNum": int(data["source"].get("chapterNum") or 0),
            "lineNo": int(data["source"].get("lineIndex") or 0) + 1,
        },
        "totalCount": int(data.get("totalCount") or 0),
        "chapters": data.get("chapters") or [],
    }


def replace_matching_line_audio_tasks(task_id: int) -> tuple[bool, str, dict]:
    """将当前台词音频复制到所有同角色+台词的其他台词任务。"""
    ok, msg, data = _find_matching_line_audio_rows(task_id)
    if not ok:
        return ok, msg, {}
    matches = data.get("matches") or []
    if not matches:
        return True, "replaced", {"replacedCount": 0, "totalCount": 0, "chapters": []}

    source = data["source"]
    source_audio_path = Path(str(source.get("audioPath") or ""))
    if not source_audio_path.exists() or not source_audio_path.is_file():
        return False, "当前任务音频文件不存在", {}
    duration = round(float(probe_audio_duration_seconds(source_audio_path)), 1)
    if duration <= 0:
        return False, "无法读取当前音频时长", {}

    novel_id = int(source.get("novelId") or 0)
    english_dir = str(source.get("englishDir") or "").strip()
    role_name = str(source.get("roleName") or "").strip()
    line_text = str(source.get("lineText") or "")
    role_map = get_novel_role_library_map(novel_id)
    role = role_map.get(role_name) or {}
    reference_text = str(role.get("sample_text") or source.get("referenceText") or "").strip()
    reference_audio_path = str(role.get("sample_audio_path") or source.get("referenceAudioPath") or "").strip()

    targets: dict[tuple[int, str], dict] = {}
    for match in matches:
        key = (int(match["chapterId"]), str(match["lineHash"]))
        targets.setdefault(key, match)

    replaced_count = 0
    conn = db_conn()
    try:
        for match in targets.values():
            chapter_num = int(match["chapterNum"])
            line_hash = str(match["lineHash"])
            dest_path = _chapter_line_audio_path(english_dir, chapter_num, line_hash)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if source_audio_path.resolve() != dest_path.resolve():
                shutil.copy2(source_audio_path, dest_path)
            rel_path = db_rel_path(dest_path)
            existing = conn.execute(
                "SELECT id FROM line_audio_tasks WHERE novel_id=? AND chapter_id=? AND line_hash=?",
                (novel_id, int(match["chapterId"]), line_hash),
            ).fetchone()
            if existing:
                if int(existing["id"]) == int(task_id):
                    continue
                conn.execute(
                    """
                    UPDATE line_audio_tasks
                    SET chapter_num=?, chapter_title=?, line_index=?, role_name=?, line_text=?,
                        reference_text=?, reference_audio_path=?, status='completed', comfy_status='replaced',
                        comfy_prompt_id=NULL, output_filename='', output_subfolder='', output_type='',
                        downloaded_file_path=?, duration_seconds=?, queue_priority=0, error_message=NULL,
                        comfy_started_at=NULL, comfy_finished_at=CURRENT_TIMESTAMP, scheduled_at=NULL,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        chapter_num,
                        str(match["chapterTitle"]),
                        int(match["lineIndex"]),
                        role_name,
                        line_text,
                        reference_text,
                        reference_audio_path,
                        rel_path,
                        duration,
                        int(existing["id"]),
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO line_audio_tasks(
                        novel_id, chapter_id, chapter_num, chapter_title,
                        line_index, role_name, line_text, reference_text, reference_audio_path,
                        line_hash, status, comfy_status, output_filename, output_subfolder, output_type,
                        downloaded_file_path, duration_seconds, queue_priority, error_message,
                        comfy_started_at, comfy_finished_at, scheduled_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', 'replaced', '', '', '', ?, ?, 0, NULL, NULL, CURRENT_TIMESTAMP, NULL)
                    """,
                    (
                        novel_id,
                        int(match["chapterId"]),
                        chapter_num,
                        str(match["chapterTitle"]),
                        int(match["lineIndex"]),
                        role_name,
                        line_text,
                        reference_text,
                        reference_audio_path,
                        line_hash,
                        rel_path,
                        duration,
                    ),
                )
            replaced_count += 1
        conn.commit()
    finally:
        conn.close()
    return True, "replaced", {
        "replacedCount": replaced_count,
        "totalCount": int(data.get("totalCount") or 0),
        "chapters": data.get("chapters") or [],
        "durationSeconds": duration,
    }


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
        "SELECT id, status, downloaded_file_path, line_text FROM line_audio_tasks WHERE id=?",
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
                        "type": "silence_gap",
                        "reason": f"检测到 {end - current_start:.1f} 秒长空白音频",
                    }
                )
            current_start = None

    if current_start is not None and audio_duration - current_start >= duration:
        segments.append(
            {
                "start": round(max(0.0, min(current_start, audio_duration)), 3),
                "end": round(audio_duration, 3),
                "type": "silence_gap",
                "reason": f"检测到 {audio_duration - current_start:.1f} 秒长空白音频",
            }
        )

    repeat_segments = _detect_repeated_short_line_audio_segments(
        audio_path,
        line_text=str(row["line_text"] or ""),
        audio_duration=audio_duration,
    )
    segments.extend(repeat_segments)

    return True, "detected", {
        "segments": segments,
        "repeatSegments": repeat_segments,
        "durationSeconds": round(audio_duration, 1),
        "noiseDb": noise,
        "minDuration": duration,
    }


def _get_completed_line_audio_path(task_id: int) -> tuple[bool, str, Path | None]:
    conn = db_conn()
    row = conn.execute(
        "SELECT id, status, downloaded_file_path FROM line_audio_tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    conn.close()
    if not row:
        return False, "任务不存在", None
    if str(row["status"] or "") != "completed":
        return False, "只有已完成任务可以分析音频", None
    rel_path = str(row["downloaded_file_path"] or "").strip()
    if not rel_path:
        return False, "任务没有可分析的音频文件", None

    audio_path = (ROOT_DIR / rel_path).resolve()
    root_resolved = ROOT_DIR.resolve()
    if root_resolved not in audio_path.parents and audio_path != root_resolved:
        return False, "无效的音频路径", None
    if not audio_path.exists() or not audio_path.is_file():
        return False, "音频文件不存在", None
    return True, "ok", audio_path


def _get_line_audio_task_text(task_id: int) -> str:
    conn = db_conn()
    row = conn.execute(
        "SELECT role_name, line_text FROM line_audio_tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    conn.close()
    if not row:
        return ""
    role_name = str(row["role_name"] or "").strip()
    line_text = str(row["line_text"] or "").strip()
    return f"{role_name}:{line_text}" if role_name else line_text


def _rms_db_from_samples(samples: tuple[int, ...]) -> float:
    if not samples:
        return -120.0
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    if mean_square <= 0:
        return -120.0
    return 20.0 * math.log10(math.sqrt(mean_square) / 32768.0)


def _next_power_of_two(value: int) -> int:
    size = 1
    while size < value:
        size <<= 1
    return size


def _fft(values: list[complex]) -> list[complex]:
    size = len(values)
    if size <= 1:
        return values
    even = _fft(values[0::2])
    odd = _fft(values[1::2])
    combined = [0j] * size
    half = size // 2
    for index in range(half):
        angle = -2.0 * math.pi * index / size
        factor = complex(math.cos(angle), math.sin(angle)) * odd[index]
        combined[index] = even[index] + factor
        combined[index + half] = even[index] - factor
    return combined


def _spectral_features(samples: tuple[int, ...], sample_rate: int) -> dict:
    if not samples or sample_rate <= 0:
        return {"flatness": 0.0, "centroid": 0.0, "highRatio": 0.0}
    window_size = min(1024, _next_power_of_two(len(samples)))
    if len(samples) < window_size:
        padded = list(samples) + [0] * (window_size - len(samples))
    else:
        padded = list(samples[:window_size])
    windowed = []
    for index, sample in enumerate(padded):
        hann = 0.5 - 0.5 * math.cos((2.0 * math.pi * index) / max(1, window_size - 1))
        windowed.append(complex((sample / 32768.0) * hann, 0.0))
    spectrum = _fft(windowed)
    powers: list[tuple[float, float]] = []
    for bin_index, value in enumerate(spectrum[1 : window_size // 2], start=1):
        freq = bin_index * sample_rate / window_size
        power = (value.real * value.real) + (value.imag * value.imag)
        powers.append((freq, power))
    total_power = sum(power for _, power in powers)
    if total_power <= 1e-12:
        return {"flatness": 0.0, "centroid": 0.0, "highRatio": 0.0}
    eps = 1e-12
    mean_log_power = sum(math.log(max(power, eps)) for _, power in powers) / len(powers)
    arithmetic_mean = total_power / len(powers)
    flatness = math.exp(mean_log_power) / max(arithmetic_mean, eps)
    centroid = sum(freq * power for freq, power in powers) / total_power
    high_power = sum(power for freq, power in powers if freq >= 3500.0)
    return {
        "flatness": max(0.0, min(1.0, flatness)),
        "centroid": centroid,
        "highRatio": high_power / total_power,
    }


def _sample_frame_features(samples: tuple[int, ...], sample_rate: int) -> dict:
    if not samples:
        return {
            "rmsDb": -120.0,
            "peakDb": -120.0,
            "crestDb": 0.0,
            "zeroCrossings": 0,
            "flatness": 0.0,
            "centroid": 0.0,
            "highRatio": 0.0,
        }
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    rms = math.sqrt(mean_square) / 32768.0 if mean_square > 0 else 0.0
    peak = max(abs(sample) for sample in samples) / 32768.0
    zero_crossings = sum(
        1
        for index in range(1, len(samples))
        if (samples[index - 1] < 0 <= samples[index]) or (samples[index - 1] >= 0 > samples[index])
    )
    rms_db = 20.0 * math.log10(rms) if rms > 0 else -120.0
    peak_db = 20.0 * math.log10(peak) if peak > 0 else -120.0
    crest_db = peak_db - rms_db if rms > 0 and peak > 0 else 0.0
    return {
        "rmsDb": rms_db,
        "peakDb": peak_db,
        "crestDb": crest_db,
        "zeroCrossings": zero_crossings,
        **_spectral_features(samples, sample_rate),
    }


def _read_wav_rms_frames(wav_path: Path, frame_seconds: float = 0.1) -> tuple[float, list[dict]]:
    with wave.open(str(wav_path), "rb") as wav_file:
        sample_rate = int(wav_file.getframerate() or 0)
        channels = int(wav_file.getnchannels() or 0)
        sample_width = int(wav_file.getsampwidth() or 0)
        total_frames = int(wav_file.getnframes() or 0)
        if sample_rate <= 0 or channels != 1 or sample_width != 2 or total_frames <= 0:
            return 0.0, []
        frame_size = max(1, int(sample_rate * frame_seconds))
        items: list[dict] = []
        cursor = 0
        while cursor < total_frames:
            read_count = min(frame_size, total_frames - cursor)
            raw = wav_file.readframes(read_count)
            if not raw:
                break
            sample_count = len(raw) // 2
            samples = struct.unpack(f"<{sample_count}h", raw[: sample_count * 2])
            start = cursor / sample_rate
            end = (cursor + read_count) / sample_rate
            features = _sample_frame_features(samples, sample_rate)
            duration = max(0.001, end - start)
            items.append(
                {
                    "start": start,
                    "end": end,
                    "rmsDb": features["rmsDb"],
                    "peakDb": features["peakDb"],
                    "crestDb": features["crestDb"],
                    "zcr": features["zeroCrossings"] / duration,
                    "flatness": features["flatness"],
                    "centroid": features["centroid"],
                    "highRatio": features["highRatio"],
                }
            )
            cursor += read_count
        return total_frames / sample_rate, items


def _read_wav_float_samples(wav_path: Path) -> tuple[int, list[float]]:
    with wave.open(str(wav_path), "rb") as wav_file:
        sample_rate = int(wav_file.getframerate() or 0)
        channels = int(wav_file.getnchannels() or 0)
        sample_width = int(wav_file.getsampwidth() or 0)
        total_frames = int(wav_file.getnframes() or 0)
        if sample_rate <= 0 or channels != 1 or sample_width != 2 or total_frames <= 0:
            return 0, []
        raw = wav_file.readframes(total_frames)
    sample_count = len(raw) // 2
    if sample_count <= 0:
        return 0, []
    samples = struct.unpack(f"<{sample_count}h", raw[: sample_count * 2])
    return sample_rate, [max(-1.0, min(1.0, sample / 32768.0)) for sample in samples]


LINE_AUDIO_NOISE_FEATURE_KEYS = [
    "duration",
    "mean_rms_db",
    "std_rms_db",
    "min_rms_db",
    "max_rms_db",
    "rms_growth_db",
    "mean_peak_db",
    "mean_crest_db",
    "low_crest_ratio",
    "mean_zcr",
    "std_zcr",
    "mean_flatness",
    "std_flatness",
    "mean_centroid_hz",
    "std_centroid_hz",
    "mean_high_ratio",
    "std_high_ratio",
    "silence_ratio",
    "low_level_ratio",
    "loud_ratio",
    "high_frequency_ratio",
    "tonal_ratio",
]


def _mean_values(values: list[float], default: float = 0.0) -> float:
    return sum(values) / len(values) if values else default


def _std_values(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean_values(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _line_audio_noise_feature_dict(frames: list[dict], segment: dict | None = None) -> dict[str, float]:
    segment_frames = _segment_frames(frames, segment) if segment else list(frames)
    if not segment_frames:
        return {key: 0.0 for key in LINE_AUDIO_NOISE_FEATURE_KEYS}
    start = float(segment_frames[0]["start"])
    end = float(segment_frames[-1]["end"])
    duration = max(0.001, end - start)
    rms_values = [float(item.get("rmsDb", -120.0)) for item in segment_frames]
    peak_values = [float(item.get("peakDb", -120.0)) for item in segment_frames]
    crest_values = [float(item.get("crestDb", 0.0)) for item in segment_frames]
    zcr_values = [float(item.get("zcr", 0.0)) for item in segment_frames]
    flatness_values = [float(item.get("flatness", 0.0)) for item in segment_frames]
    centroid_values = [float(item.get("centroid", 0.0)) for item in segment_frames]
    high_ratio_values = [float(item.get("highRatio", 0.0)) for item in segment_frames]
    edge_count = max(1, min(4, len(rms_values) // 3 or 1))
    return {
        "duration": duration,
        "mean_rms_db": _mean_values(rms_values, -120.0),
        "std_rms_db": _std_values(rms_values),
        "min_rms_db": min(rms_values),
        "max_rms_db": max(rms_values),
        "rms_growth_db": _mean_values(rms_values[-edge_count:]) - _mean_values(rms_values[:edge_count]),
        "mean_peak_db": _mean_values(peak_values, -120.0),
        "mean_crest_db": _mean_values(crest_values),
        "low_crest_ratio": sum(1 for value in crest_values if value <= 10.0) / len(crest_values),
        "mean_zcr": _mean_values(zcr_values),
        "std_zcr": _std_values(zcr_values),
        "mean_flatness": _mean_values(flatness_values),
        "std_flatness": _std_values(flatness_values),
        "mean_centroid_hz": _mean_values(centroid_values),
        "std_centroid_hz": _std_values(centroid_values),
        "mean_high_ratio": _mean_values(high_ratio_values),
        "std_high_ratio": _std_values(high_ratio_values),
        "silence_ratio": sum(1 for value in rms_values if value <= -58.0) / len(rms_values),
        "low_level_ratio": sum(1 for value in rms_values if -65.0 <= value <= -38.0) / len(rms_values),
        "loud_ratio": sum(1 for value in rms_values if value >= -28.0) / len(rms_values),
        "high_frequency_ratio": sum(
            1
            for zcr, high_ratio, flatness in zip(zcr_values, high_ratio_values, flatness_values)
            if zcr >= 3500.0 and high_ratio >= 0.12 and flatness >= 0.035
        )
        / len(segment_frames),
        "tonal_ratio": sum(
            1
            for crest, flatness, zcr, high_ratio in zip(crest_values, flatness_values, zcr_values, high_ratio_values)
            if crest <= 9.5 and flatness <= 0.035 and zcr <= 1600.0 and high_ratio <= 0.06
        )
        / len(segment_frames),
    }


def _line_audio_noise_feature_vector(features: dict[str, float]) -> list[float]:
    return [float(features.get(key, 0.0) or 0.0) for key in LINE_AUDIO_NOISE_FEATURE_KEYS]


def _load_line_audio_noise_classifier() -> dict | None:
    try:
        if not LINE_AUDIO_NOISE_MODEL_PATH.exists():
            return None
        data = json.loads(LINE_AUDIO_NOISE_MODEL_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("featureKeys") != LINE_AUDIO_NOISE_FEATURE_KEYS:
            return None
        return data
    except Exception:
        return None


def _classifier_distance(vector: list[float], mean: list[float], std: list[float]) -> float:
    total = 0.0
    for value, mean_value, std_value in zip(vector, mean, std):
        scale = max(float(std_value or 0.0), 1e-6)
        total += ((float(value) - float(mean_value)) / scale) ** 2
    return math.sqrt(total / max(1, len(vector)))


def _classify_line_audio_noise_segment(frames: list[dict], segment: dict) -> dict | None:
    model = _load_line_audio_noise_classifier()
    if not model:
        return None
    try:
        features = _line_audio_noise_feature_dict(frames, segment)
        vector = _line_audio_noise_feature_vector(features)
        normal = model.get("normal") or {}
        abnormal = model.get("abnormal") or {}
        normal_distance = _classifier_distance(vector, normal.get("mean") or [], normal.get("std") or [])
        abnormal_distance = _classifier_distance(vector, abnormal.get("mean") or [], abnormal.get("std") or [])
        probability = 1.0 / (1.0 + math.exp(max(-40.0, min(40.0, abnormal_distance - normal_distance))))
        threshold = float(model.get("threshold", 0.58) or 0.58)
        return {
            "status": "abnormal" if probability >= threshold else "normal",
            "probability": round(probability, 3),
            "threshold": round(threshold, 3),
            "normalDistance": round(normal_distance, 3),
            "abnormalDistance": round(abnormal_distance, 3),
            "modelVersion": str(model.get("version") or "unknown"),
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _apply_line_audio_noise_classifier(segments: list[dict], frames: list[dict]) -> None:
    if not segments or not _load_line_audio_noise_classifier():
        return
    weak_rule_types = {"post_speech_tail_noise", "sensitive_low_level_noise", "vad_non_speech_noise"}
    for segment in segments:
        result = _classify_line_audio_noise_segment(frames, segment)
        if not result:
            continue
        segment["classifier"] = result
        if result.get("status") == "abnormal":
            probability = float(result.get("probability", 0.0) or 0.0)
            segment_type = str(segment.get("type") or "")
            if segment_type not in weak_rule_types:
                segment["score"] = max(int(segment.get("score", 0) or 0), int(round(probability * 100)))
                segment["confidence"] = max(float(segment.get("confidence", 0.0) or 0.0), round(probability, 2))
            reasons = segment.setdefault("reasons", [])
            if isinstance(reasons, list):
                reasons.append(f"本地音频分类器确认异常，概率 {probability:.2f}")


def _append_line_audio_noise_sample_log(record: dict) -> None:
    try:
        LINE_AUDIO_NOISE_SAMPLE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LINE_AUDIO_NOISE_SAMPLE_LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def _collect_line_audio_noise_training_samples(
    audio_path: Path,
    *,
    task_id: int,
    line_text: str,
    segments: list[dict[str, Any]],
    label: str | None = None,
    source: str = "accepted_remove",
) -> None:
    if not segments or not audio_path.exists():
        return
    for index, segment in enumerate(segments):
        start = max(0.0, float(segment.get("start") or 0.0))
        end = max(start, float(segment.get("end") or start))
        if end - start < 0.05:
            continue
        segment_type = str(segment.get("type") or "").strip()
        segment_label = label or _line_audio_training_sample_label(segment_type)
        sample_dir = LINE_AUDIO_NOISE_SAMPLE_DIR / segment_label
        try:
            sample_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        sample_name = f"task-{int(task_id)}-{int(time.time() * 1000)}-{index:02d}.flac"
        sample_path = sample_dir / sample_name
        try:
            subprocess.run(
                [
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
                    str(sample_path),
                ],
                check=True,
                capture_output=True,
            )
            if not sample_path.exists() or sample_path.stat().st_size <= 0:
                continue
            rel_sample_path = db_rel_path(sample_path)
            _append_line_audio_noise_sample_log(
                {
                    "label": segment_label,
                    "source": source,
                    "segmentType": segment_type,
                    "taskId": int(task_id),
                    "lineText": str(line_text or ""),
                    "audioPath": rel_sample_path,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(end - start, 3),
                    "createdAt": int(time.time()),
                }
            )
        except Exception:
            try:
                if sample_path.exists():
                    sample_path.unlink()
            except OSError:
                pass


def record_line_audio_noise_false_positive(task_id: int, segments: list[dict[str, Any]]) -> tuple[bool, str, dict]:
    ok, msg, audio_path = _get_completed_line_audio_path(task_id)
    if not ok or audio_path is None:
        return False, msg, {}
    duration = float(probe_audio_duration_seconds(audio_path))
    if duration <= 0:
        return False, "无法读取音频时长", {}
    normalized_segments: list[dict[str, Any]] = []
    for item in segments or []:
        if not isinstance(item, dict):
            continue
        try:
            start = max(0.0, min(float(item.get("start") or 0.0), duration))
            end = max(0.0, min(float(item.get("end") or 0.0), duration))
        except (TypeError, ValueError):
            continue
        if end - start < 0.05:
            continue
        normalized_segments.append(
            {
                "start": start,
                "end": end,
                "type": str(item.get("type") or "false_positive_noise").strip() or "false_positive_noise",
                "reason": "误报：人工确认不是噪音",
            }
        )
    if not normalized_segments:
        return False, "没有可保存的误报片段", {}
    _collect_line_audio_noise_training_samples(
        audio_path,
        task_id=task_id,
        line_text=_get_line_audio_task_text(task_id),
        segments=normalized_segments,
        label="false_positive_normal",
        source="false_positive_noise",
    )
    return True, "recorded", {"count": len(normalized_segments), "label": "false_positive_normal"}


def _line_audio_training_sample_label(segment_type: str) -> str:
    normalized_type = str(segment_type or "").strip()
    if normalized_type == "silence_gap":
        return "silence_gap"
    if normalized_type == "repeated_short_line_audio":
        return "repeated_speech"
    if not normalized_type or normalized_type == "mixed":
        return "manual_abnormal"
    non_noise_types = {"incomplete_long_line_audio", "short_line_audio_too_long"}
    if normalized_type in non_noise_types:
        return "content_issue"
    return "abnormal"


def _merge_speech_frames_to_segments(speech_frames: list[dict], *, max_gap: float = 0.2, min_duration: float = 0.1) -> list[dict]:
    if not speech_frames:
        return []
    segments: list[dict] = []
    start = float(speech_frames[0]["start"])
    end = float(speech_frames[0]["end"])
    max_probability = float(speech_frames[0].get("probability", 0.0))
    for frame in speech_frames[1:]:
        frame_start = float(frame["start"])
        frame_end = float(frame["end"])
        if frame_start - end <= max_gap:
            end = max(end, frame_end)
            max_probability = max(max_probability, float(frame.get("probability", 0.0)))
            continue
        if end - start >= min_duration:
            segments.append({"start": start, "end": end, "probability": max_probability})
        start = frame_start
        end = frame_end
        max_probability = float(frame.get("probability", 0.0))
    if end - start >= min_duration:
        segments.append({"start": start, "end": end, "probability": max_probability})
    return segments


def _detect_silero_onnx_speech_segments(wav_path: Path) -> tuple[str, list[dict], str]:
    if not SILERO_VAD_ONNX_PATH.exists():
        return "rule", [], "silero_vad.onnx not found"
    try:
        import numpy as np  # type: ignore
        import onnxruntime as ort  # type: ignore
    except Exception as exc:
        return "rule", [], f"onnxruntime unavailable: {exc}"
    sample_rate, samples = _read_wav_float_samples(wav_path)
    if sample_rate != 16000 or not samples:
        return "rule", [], "invalid wav for silero vad"
    try:
        session = ort.InferenceSession(str(SILERO_VAD_ONNX_PATH), providers=["CPUExecutionProvider"])
        input_names = {item.name for item in session.get_inputs()}
        state = np.zeros((2, 1, 128), dtype=np.float32)
        speech_frames: list[dict] = []
        window_size = 512
        for offset in range(0, len(samples), window_size):
            chunk = samples[offset : offset + window_size]
            if len(chunk) < window_size:
                chunk = chunk + [0.0] * (window_size - len(chunk))
            inputs: dict[str, Any] = {}
            if "input" in input_names:
                inputs["input"] = np.asarray(chunk, dtype=np.float32).reshape(1, -1)
            if "state" in input_names:
                inputs["state"] = state
            if "sr" in input_names:
                inputs["sr"] = np.asarray(sample_rate, dtype=np.int64)
            outputs = session.run(None, inputs)
            probability = float(np.asarray(outputs[0]).reshape(-1)[0]) if outputs else 0.0
            if len(outputs) > 1:
                next_state = np.asarray(outputs[1])
                if next_state.shape == state.shape:
                    state = next_state.astype(np.float32)
            if probability >= 0.5:
                speech_frames.append(
                    {
                        "start": offset / sample_rate,
                        "end": min(len(samples), offset + window_size) / sample_rate,
                        "probability": probability,
                    }
                )
        return "silero_onnx", _merge_speech_frames_to_segments(speech_frames), "ok"
    except Exception as exc:
        return "rule", [], f"silero vad failed: {exc}"


def _short_line_audio_char_count(text: str) -> int:
    content = re.sub(r"\s+", "", str(text or ""))
    prefix_match = re.match(r"^([^:：]{1,6})[:：](.+)$", content)
    if prefix_match and not re.search(r"[，。！？、；,.!?;]", prefix_match.group(1)):
        content = prefix_match.group(2)
    content = re.sub(r"[，。！？、；：,.!?;:\"'“”‘’（）()《》]+", "", content)
    return len(content)


def _active_audio_groups(frames: list[dict], threshold: float = -45.0) -> list[dict]:
    groups: list[dict] = []
    index = 0
    while index < len(frames):
        if float(frames[index]["rmsDb"]) < threshold:
            index += 1
            continue
        start_index = index
        gap = 0
        index += 1
        while index < len(frames):
            if float(frames[index]["rmsDb"]) >= threshold:
                gap = 0
                index += 1
                continue
            gap += 1
            if gap > 1:
                break
            index += 1
        end_index = min(len(frames) - 1, max(start_index, index - gap))
        group_frames = frames[start_index : end_index + 1]
        start = float(group_frames[0]["start"])
        end = float(group_frames[-1]["end"])
        duration = end - start
        if duration >= 0.18:
            groups.append(
                {
                    "start": start,
                    "end": end,
                    "duration": duration,
                    "meanRmsDb": _mean_rms_db(group_frames),
                    "meanZcr": _mean_frame_value(group_frames, "zcr"),
                    "meanCentroid": _mean_frame_value(group_frames, "centroid"),
                }
            )
    return groups


def _groups_look_similar(left: dict, right: dict) -> bool:
    left_duration = max(0.01, float(left.get("duration") or 0.0))
    right_duration = max(0.01, float(right.get("duration") or 0.0))
    duration_ratio = min(left_duration, right_duration) / max(left_duration, right_duration)
    rms_delta = abs(float(left.get("meanRmsDb") or -120.0) - float(right.get("meanRmsDb") or -120.0))
    zcr_delta = abs(float(left.get("meanZcr") or 0.0) - float(right.get("meanZcr") or 0.0))
    centroid_delta = abs(float(left.get("meanCentroid") or 0.0) - float(right.get("meanCentroid") or 0.0))
    return duration_ratio >= 0.45 and rms_delta <= 10.0 and zcr_delta <= 2500.0 and centroid_delta <= 900.0


def _detect_repeated_short_line_audio_segments(
    audio_path: Path, *, line_text: str, audio_duration: float
) -> list[dict]:
    char_count = _short_line_audio_char_count(line_text)
    if char_count > 4 or audio_duration < 1.45:
        return []
    expected_duration = max(0.7, 0.22 * max(1, char_count) + 0.45)
    if audio_duration < max(1.45, expected_duration * 1.6):
        return []

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(audio_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
        )
        _, frames = _read_wav_rms_frames(tmp_path)
    except Exception:
        return []
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    groups = _active_audio_groups(frames, threshold=-45.0)
    if len(groups) < 2:
        return []
    first = groups[0]
    second = groups[1]
    gap = float(second["start"]) - float(first["end"])
    if gap < 0.18 or not _groups_look_similar(first, second):
        return []
    if float(second["end"]) < audio_duration * 0.65:
        return []
    delete_start = max(0.0, float(first["end"]) + 0.02)
    if audio_duration - delete_start < 0.35:
        return []
    return [
        {
            "start": round(delete_start, 3),
            "end": round(audio_duration, 3),
            "type": "repeated_short_line_audio",
            "reason": "短台词音频疑似重复生成，保留第一遍并删除后续重复片段",
        }
    ]


def _find_tail_noise_segment(duration: float, frames: list[dict]) -> dict | None:
    if duration <= 0 or len(frames) < 8:
        return None
    active_threshold = -42.0
    max_tail_silence_frames = 2
    index = len(frames) - 1
    silent_tail = 0
    while index >= 0 and frames[index]["rmsDb"] <= active_threshold:
        silent_tail += 1
        if silent_tail > max_tail_silence_frames:
            return None
        index -= 1
    if index < 0:
        return None

    tail_end_index = index
    gap = 0
    while index >= 0:
        if frames[index]["rmsDb"] > active_threshold:
            gap = 0
            index -= 1
            continue
        gap += 1
        if gap > max_tail_silence_frames:
            break
        index -= 1
    tail_start_index = max(0, index + gap + 1)
    tail_frames = frames[tail_start_index : tail_end_index + 1]
    if not tail_frames:
        return None

    start = float(tail_frames[0]["start"])
    end = float(frames[tail_end_index]["end"])
    if duration - end > 0.35:
        return None
    tail_duration = max(0.0, end - start)
    if tail_duration < 1.0:
        return None

    rms_values = [float(item["rmsDb"]) for item in tail_frames]
    edge_count = max(1, min(3, len(rms_values)))
    active_ratio = sum(1 for value in rms_values if value > active_threshold) / len(rms_values)
    start_rms = sum(rms_values[:edge_count]) / edge_count
    end_rms = sum(rms_values[-edge_count:]) / edge_count
    mean_rms = sum(rms_values) / len(rms_values)
    growth = end_rms - start_rms
    slope = growth / max(tail_duration, 0.1)
    previous_frames = frames[max(0, tail_start_index - 10) : tail_start_index]
    previous_low_ratio = 0.0
    if previous_frames:
        previous_low_ratio = sum(1 for item in previous_frames if float(item["rmsDb"]) <= -45.0) / len(previous_frames)

    has_strong_rising_tail = growth >= 12.0 and slope >= 4.0
    has_moderate_rising_tail = previous_low_ratio >= 0.45 and growth >= 8.0 and slope >= 2.5
    if not has_strong_rising_tail and not has_moderate_rising_tail:
        return None

    mean_zcr = _mean_frame_value(tail_frames, "zcr")
    mean_flatness = _mean_frame_value(tail_frames, "flatness")
    mean_centroid = _mean_frame_value(tail_frames, "centroid")
    mean_high_ratio = _mean_frame_value(tail_frames, "highRatio")
    mean_crest = _mean_frame_value(tail_frames, "crestDb")
    is_tonal_tail = mean_crest <= 9.2 and mean_flatness <= 0.018 and mean_zcr <= 1100.0 and mean_high_ratio <= 0.025
    is_high_frequency_tail = mean_zcr >= 4200.0 and mean_high_ratio >= 0.22 and mean_flatness >= 0.045
    is_extreme_rising_tail = has_strong_rising_tail and growth >= 18.0 and mean_crest <= 11.0
    if not is_tonal_tail and not is_high_frequency_tail and not is_extreme_rising_tail:
        return None

    score = 0
    reasons: list[str] = []
    if tail_duration >= 1.0:
        score += 25
        reasons.append(f"尾部非静音持续 {tail_duration:.1f} 秒")
    if tail_duration >= 2.0:
        score += 15
    if active_ratio >= 0.9:
        score += 10
        reasons.append("异常声音一直延续到文件末尾")
    if previous_low_ratio >= 0.45:
        score += 15
        reasons.append("人声结束后出现独立尾部声音")
    if growth >= 12.0:
        score += 15
        reasons.append(f"尾部音量增长 {growth:.1f}dB")
    if slope >= 4.0:
        score += 15
        reasons.append(f"尾部音量增长斜率 {slope:.1f}dB/秒")
    if end_rms >= -30.0:
        score += 10
        reasons.append(f"尾部结束音量较高 {end_rms:.1f}dBFS")
    if mean_rms >= -40.0:
        score += 10
        reasons.append(f"尾部平均音量 {mean_rms:.1f}dBFS")

    if score < 55:
        return None
    status = "abnormal"
    noise_type = "rising_non_speech_noise" if growth >= 12.0 else "tail_non_speech_noise"
    return {
        "start": round(start, 2),
        "end": round(duration, 2),
        "duration": round(max(0.0, duration - start), 2),
        "type": noise_type,
        "confidence": round(min(0.99, max(0.3, score / 100)), 2),
        "score": int(score),
        "status": status,
        "features": {
            "mean_rms_db": round(mean_rms, 1),
            "start_rms_db": round(start_rms, 1),
            "end_rms_db": round(end_rms, 1),
            "rms_growth_db": round(growth, 1),
            "rms_slope_db_per_second": round(slope, 1),
            "active_ratio": round(active_ratio, 2),
            "previous_low_ratio": round(previous_low_ratio, 2),
            "mean_zcr": round(mean_zcr, 1),
            "mean_flatness": round(mean_flatness, 4),
            "mean_centroid_hz": round(mean_centroid, 1),
            "mean_high_ratio": round(mean_high_ratio, 3),
            "mean_crest_db": round(mean_crest, 1),
        },
        "reasons": reasons,
    }


def _mean_rms_db(frames: list[dict]) -> float:
    if not frames:
        return -120.0
    return sum(float(item["rmsDb"]) for item in frames) / len(frames)


def _mean_frame_value(frames: list[dict], key: str, default: float = 0.0) -> float:
    if not frames:
        return default
    return sum(float(item.get(key, default)) for item in frames) / len(frames)


def _segment_frames(frames: list[dict], segment: dict) -> list[dict]:
    start = float(segment.get("start", 0.0) or 0.0)
    end = float(segment.get("end", start) or start)
    return [item for item in frames if float(item["start"]) < end and float(item["end"]) > start]


def _frame_voice_state(frame: dict) -> str:
    rms_db = float(frame.get("rmsDb", -120.0))
    if rms_db <= -48.0:
        return "silence"
    zcr = float(frame.get("zcr", 0.0))
    flatness = float(frame.get("flatness", 0.0))
    centroid = float(frame.get("centroid", 0.0))
    high_ratio = float(frame.get("highRatio", 0.0))
    crest_db = float(frame.get("crestDb", 0.0))
    tonal_artifact = crest_db <= 8.8 and flatness <= 0.016 and zcr <= 900.0 and high_ratio <= 0.025
    high_frequency_noise = zcr >= 5200.0 and high_ratio >= 0.22 and flatness >= 0.04
    if tonal_artifact or high_frequency_noise:
        return "noise"
    speech_like = rms_db >= -38.0 and (
        crest_db >= 10.0
        or zcr >= 1200.0
        or flatness >= 0.018
        or high_ratio >= 0.025
        or centroid >= 500.0
    )
    return "speech" if speech_like else "noise"


def _overlap_seconds(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def _voice_activity_summary(frames: list[dict], segment: dict, speech_segments: list[dict] | None = None) -> dict:
    segment_frames = _segment_frames(frames, segment)
    start = float(segment.get("start", 0.0) or 0.0)
    end = float(segment.get("end", start) or start)
    duration = max(0.001, end - start)
    if speech_segments is not None:
        overlaps = [
            _overlap_seconds(start, end, float(item.get("start", 0.0) or 0.0), float(item.get("end", 0.0) or 0.0))
            for item in speech_segments
        ]
        speech_seconds = sum(overlaps)
        max_speech_run = max(overlaps, default=0.0)
        silence_seconds = sum(
            max(0.0, min(end, float(item["end"])) - max(start, float(item["start"])))
            for item in segment_frames
            if float(item.get("rmsDb", -120.0)) <= -48.0
        )
        noise_seconds = max(0.0, duration - speech_seconds - silence_seconds)
        return {
            "speech_ratio": round(min(1.0, speech_seconds / duration), 3),
            "noise_ratio": round(min(1.0, noise_seconds / duration), 3),
            "silence_ratio": round(min(1.0, silence_seconds / duration), 3),
            "max_speech_run_seconds": round(max_speech_run, 2),
        }
    if not segment_frames:
        return {"speech_ratio": 0.0, "noise_ratio": 0.0, "silence_ratio": 0.0, "max_speech_run_seconds": 0.0}
    speech_count = 0
    noise_count = 0
    silence_count = 0
    current_speech_run = 0.0
    max_speech_run = 0.0
    for frame in segment_frames:
        state = _frame_voice_state(frame)
        frame_duration = max(0.0, float(frame["end"]) - float(frame["start"]))
        if state == "speech":
            speech_count += 1
            current_speech_run += frame_duration
            max_speech_run = max(max_speech_run, current_speech_run)
        else:
            current_speech_run = 0.0
            if state == "silence":
                silence_count += 1
            else:
                noise_count += 1
    total = max(1, len(segment_frames))
    return {
        "speech_ratio": round(speech_count / total, 3),
        "noise_ratio": round(noise_count / total, 3),
        "silence_ratio": round(silence_count / total, 3),
        "max_speech_run_seconds": round(max_speech_run, 2),
    }


def _filter_segments_with_voice_activity(
    segments: list[dict], frames: list[dict], *, sensitivity: str = "balanced", speech_segments: list[dict] | None = None
) -> list[dict]:
    filtered: list[dict] = []
    speech_gated_types = {"sustained_tts_artifact", "tail_non_speech_noise", "rising_non_speech_noise"}
    mode = str(sensitivity or "balanced")
    is_strict = mode in {"strict", "aggressive"}
    is_aggressive = mode == "aggressive"
    for segment in segments:
        voice = _voice_activity_summary(frames, segment, speech_segments)
        segment["voiceActivity"] = voice
        segment_type = str(segment.get("type") or "")
        if segment_type in speech_gated_types:
            speech_ratio = float(voice.get("speech_ratio", 0.0) or 0.0)
            noise_ratio = float(voice.get("noise_ratio", 0.0) or 0.0)
            max_speech_run = float(voice.get("max_speech_run_seconds", 0.0) or 0.0)
            speech_limit = 0.8 if is_aggressive else (0.55 if is_strict else 0.35)
            noise_limit = 0.15 if is_aggressive else (0.35 if is_strict else 0.55)
            speech_run_limit = 1.8 if is_aggressive else (0.9 if is_strict else 0.5)
            if speech_ratio >= speech_limit and noise_ratio < noise_limit and max_speech_run >= speech_run_limit:
                continue
        filtered.append(segment)
    return filtered


def _find_line_audio_quality_issue_segments(line_text: str, duration: float) -> list[dict]:
    char_count = _line_text_char_count(line_text)
    if duration <= 0 or char_count <= 0:
        return []
    efficiency = char_count / duration
    segments: list[dict] = []
    if char_count >= 180:
        min_expected = char_count * 0.20
        is_clearly_too_short = duration < min_expected and efficiency >= 5.6
        if is_clearly_too_short:
            score = 90 if duration < char_count * 0.16 or efficiency >= 6.5 else 80
            segments.append(
                {
                    "start": 0.0,
                    "end": round(duration, 2),
                    "duration": round(duration, 2),
                    "type": "incomplete_long_line_audio",
                    "confidence": round(score / 100, 2),
                    "score": score,
                    "status": "abnormal",
                    "features": {
                        "char_count": char_count,
                        "duration_seconds": round(duration, 1),
                        "chars_per_second": round(efficiency, 2),
                        "min_expected_seconds": round(min_expected, 1),
                    },
                    "reasons": [
                        f"长台词 {char_count} 字，但音频仅 {duration:.1f} 秒",
                        f"字符效率 {efficiency:.1f} 字/秒，且低于保守预计 {min_expected:.1f} 秒，疑似未完整读完",
                        "建议重新生成该台词音频",
                    ],
                }
            )
    elif char_count <= 6 and duration > 4.0:
        segments.append(
            {
                "start": 0.0,
                "end": round(duration, 2),
                "duration": round(duration, 2),
                "type": "short_line_audio_too_long",
                "confidence": 0.85,
                "score": 85,
                "status": "abnormal",
                "features": {"char_count": char_count, "duration_seconds": round(duration, 1)},
                "reasons": [f"超短台词 {char_count} 字，音频 {duration:.1f} 秒，疑似生成异常"],
            }
        )
    elif char_count <= 15 and duration > 15.0:
        segments.append(
            {
                "start": 0.0,
                "end": round(duration, 2),
                "duration": round(duration, 2),
                "type": "short_line_audio_too_long",
                "confidence": 0.8,
                "score": 80,
                "status": "abnormal",
                "features": {"char_count": char_count, "duration_seconds": round(duration, 1)},
                "reasons": [f"短台词 {char_count} 字，音频 {duration:.1f} 秒，疑似生成异常"],
            }
        )
    return segments


def _frame_low_ratio(frames: list[dict], threshold: float = -46.0) -> float:
    if not frames:
        return 0.0
    return sum(1 for item in frames if float(item["rmsDb"]) <= threshold) / len(frames)


def _adjacent_low_gap_seconds(
    frames: list[dict], index: int, *, direction: int, threshold: float = -46.0
) -> tuple[float, int]:
    cursor = int(index)
    total = 0.0
    last_index = cursor
    while 0 <= cursor < len(frames) and float(frames[cursor]["rmsDb"]) <= threshold:
        total += float(frames[cursor]["end"]) - float(frames[cursor]["start"])
        last_index = cursor
        cursor += int(direction)
    return total, last_index


def _find_isolated_noise_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 8:
        return []
    active_threshold = -37.0
    low_threshold = -46.0
    adjacent_low_threshold = -45.0
    max_gap_frames = 1
    results: list[dict] = []
    index = 0
    while index < len(frames):
        if float(frames[index]["rmsDb"]) < active_threshold:
            index += 1
            continue
        start_index = index
        gap = 0
        index += 1
        while index < len(frames):
            if float(frames[index]["rmsDb"]) >= active_threshold:
                gap = 0
                index += 1
                continue
            gap += 1
            if gap > max_gap_frames:
                break
            index += 1
        end_index = min(len(frames) - 1, max(start_index, index - gap))
        group_frames = frames[start_index : end_index + 1]
        if not group_frames:
            continue

        start = float(group_frames[0]["start"])
        end = float(group_frames[-1]["end"])
        segment_duration = end - start
        if segment_duration < 0.08 or segment_duration > 1.4:
            continue

        context_size = 6
        before_frames = frames[max(0, start_index - context_size) : start_index]
        after_frames = frames[end_index + 1 : min(len(frames), end_index + 1 + context_size)]
        if len(before_frames) < 3 or len(after_frames) < 3:
            continue

        before_low_ratio = _frame_low_ratio(before_frames, low_threshold)
        after_low_ratio = _frame_low_ratio(after_frames, low_threshold)
        before_gap_seconds, _ = _adjacent_low_gap_seconds(
            frames, start_index - 1, direction=-1, threshold=adjacent_low_threshold
        )
        after_gap_seconds, after_gap_end_index = _adjacent_low_gap_seconds(
            frames, end_index + 1, direction=1, threshold=adjacent_low_threshold
        )

        mean_rms = _mean_rms_db(group_frames)
        peak_rms = max(float(item["rmsDb"]) for item in group_frames)
        mean_zcr = _mean_frame_value(group_frames, "zcr")
        context_mean = _mean_rms_db(before_frames + after_frames)
        contrast = mean_rms - context_mean
        is_short_burst = (
            segment_duration <= 0.55
            and before_low_ratio >= 0.5
            and after_low_ratio >= 0.5
            and peak_rms >= -25.0
            and contrast >= 14.0
        )
        is_stray_short_sound = (
            0.55 < segment_duration <= 1.4
            and before_gap_seconds >= 0.18
            and after_gap_seconds >= 0.55
            and peak_rms >= -30.0
            and context_mean <= -42.0
            and mean_zcr <= 2800.0
        )
        if not is_short_burst and not is_stray_short_sound:
            continue

        score = 55
        reasons = [f"低能量间隙中出现 {segment_duration:.1f} 秒独立声音"]
        if peak_rms >= -20.0:
            score += 15
            reasons.append(f"独立声音峰值较高 {peak_rms:.1f}dBFS")
        if contrast >= 20.0:
            score += 15
            reasons.append(f"相对前后静音高出 {contrast:.1f}dB")
        if before_low_ratio >= 0.8 and after_low_ratio >= 0.8:
            score += 10
            reasons.append("独立声音前后均接近静音")
        if is_stray_short_sound:
            score += 10
            reasons.append(f"后方低电平间隔持续 {after_gap_seconds:.1f} 秒")

        end_with_gap = end
        if is_stray_short_sound and 0 <= after_gap_end_index < len(frames):
            end_with_gap = float(frames[after_gap_end_index]["end"])

        results.append(
            {
                "start": round(max(0.0, start - 0.03), 2),
                "end": round(min(duration, end_with_gap + 0.03), 2),
                "duration": round(max(0.0, end_with_gap - start), 2),
                "type": "isolated_short_noise" if is_stray_short_sound else "isolated_noise_burst",
                "confidence": round(min(0.99, max(0.3, score / 100)), 2),
                "score": int(score),
                "status": "abnormal",
                "features": {
                    "mean_rms_db": round(mean_rms, 1),
                    "peak_rms_db": round(peak_rms, 1),
                    "context_mean_rms_db": round(context_mean, 1),
                    "contrast_db": round(contrast, 1),
                    "mean_zcr": round(mean_zcr, 1),
                    "before_low_ratio": round(before_low_ratio, 2),
                    "after_low_ratio": round(after_low_ratio, 2),
                    "before_low_gap_seconds": round(before_gap_seconds, 2),
                    "after_low_gap_seconds": round(after_gap_seconds, 2),
                },
                "reasons": reasons,
            }
        )
    return results


def _segments_overlap(left: dict, right: dict) -> bool:
    return float(left.get("start", 0.0)) < float(right.get("end", 0.0)) and float(right.get("start", 0.0)) < float(left.get("end", 0.0))


def _find_low_zcr_artifact_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 8:
        return []
    results: list[dict] = []
    index = 0
    while index < len(frames):
        rms_db = float(frames[index]["rmsDb"])
        zcr = float(frames[index].get("zcr", 0.0))
        crest_db = float(frames[index].get("crestDb", 0.0))
        if rms_db < -24.0 or zcr > 420.0 or crest_db > 6.5:
            index += 1
            continue
        start_index = index
        index += 1
        while index < len(frames):
            rms_db = float(frames[index]["rmsDb"])
            zcr = float(frames[index].get("zcr", 0.0))
            crest_db = float(frames[index].get("crestDb", 0.0))
            if rms_db < -24.0 or zcr > 520.0 or crest_db > 7.5:
                break
            index += 1
        end_index = index - 1
        segment_frames = frames[start_index : end_index + 1]
        if not segment_frames:
            continue
        start = float(segment_frames[0]["start"])
        end = float(segment_frames[-1]["end"])
        segment_duration = end - start
        if segment_duration < 1.6:
            continue
        mean_rms = _mean_rms_db(segment_frames)
        mean_zcr = _mean_frame_value(segment_frames, "zcr")
        mean_crest = _mean_frame_value(segment_frames, "crestDb")
        if mean_rms < -18.0 or mean_zcr > 320.0 or mean_crest > 5.8:
            continue
        reasons = [f"检测到 {segment_duration:.1f} 秒低过零率持续异常声音"]
        score = 60
        if segment_duration >= 1.4:
            score += 15
            reasons.append(f"异常声音持续 {segment_duration:.1f} 秒")
        if mean_zcr <= 400.0:
            score += 10
            reasons.append(f"过零率偏低 {mean_zcr:.0f}/秒")
        if mean_crest <= 6.5:
            score += 10
            reasons.append(f"峰均比较低 {mean_crest:.1f}dB")
        results.append(
            {
                "start": round(max(0.0, start - 0.08), 2),
                "end": round(min(duration, end + 0.12), 2),
                "duration": round(segment_duration, 2),
                "type": "low_zcr_sustained_artifact",
                "confidence": round(min(0.99, max(0.3, score / 100)), 2),
                "score": int(score),
                "status": "abnormal",
                "features": {
                    "mean_rms_db": round(mean_rms, 1),
                    "mean_zcr": round(mean_zcr, 1),
                    "mean_crest_db": round(mean_crest, 1),
                },
                "reasons": reasons,
            }
        )
    return results


def _find_high_frequency_noise_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 10:
        return []
    results: list[dict] = []
    index = 0
    while index < len(frames):
        frame = frames[index]
        is_noisy = (
            float(frame["rmsDb"]) >= -23.0
            and float(frame.get("zcr", 0.0)) >= 4500.0
            and float(frame.get("highRatio", 0.0)) >= 0.24
            and float(frame.get("centroid", 0.0)) >= 1900.0
            and float(frame.get("flatness", 0.0)) >= 0.08
        )
        if not is_noisy:
            index += 1
            continue
        start_index = index
        gap = 0
        index += 1
        while index < len(frames):
            frame = frames[index]
            is_noisy = (
                float(frame["rmsDb"]) >= -24.5
                and float(frame.get("zcr", 0.0)) >= 3800.0
                and float(frame.get("highRatio", 0.0)) >= 0.20
                and float(frame.get("centroid", 0.0)) >= 1700.0
                and float(frame.get("flatness", 0.0)) >= 0.06
            )
            if is_noisy:
                gap = 0
                index += 1
                continue
            gap += 1
            if gap > 1:
                break
            index += 1
        end_index = max(start_index, index - gap)
        segment_frames = frames[start_index : end_index + 1]
        if not segment_frames:
            continue
        segment_duration = float(segment_frames[-1]["end"]) - float(segment_frames[0]["start"])
        if segment_duration < 0.8:
            continue
        mean_rms = _mean_rms_db(segment_frames)
        mean_zcr = _mean_frame_value(segment_frames, "zcr")
        mean_flatness = _mean_frame_value(segment_frames, "flatness")
        mean_centroid = _mean_frame_value(segment_frames, "centroid")
        mean_high_ratio = _mean_frame_value(segment_frames, "highRatio")
        if mean_rms < -22.0 or mean_zcr < 4800.0 or mean_high_ratio < 0.25 or mean_centroid < 2000.0:
            continue

        expanded_start_index = start_index
        cursor = start_index - 1
        while cursor >= 0 and start_index - cursor <= 25:
            if float(frames[cursor]["rmsDb"]) > -12.0:
                break
            expanded_start_index = cursor
            cursor -= 1

        score = 70
        reasons = [f"检测到 {segment_duration:.1f} 秒高频宽带噪声"]
        if mean_zcr >= 5600.0:
            score += 10
            reasons.append(f"过零率明显偏高 {mean_zcr:.0f}/秒")
        if mean_high_ratio >= 0.32:
            score += 10
            reasons.append(f"高频能量占比较高 {mean_high_ratio:.2f}")
        if mean_centroid >= 2500.0:
            score += 10
            reasons.append(f"频谱重心偏高 {mean_centroid:.0f}Hz")
        results.append(
            {
                "start": round(max(0.0, float(frames[expanded_start_index]["start"])), 2),
                "end": round(min(duration, float(segment_frames[-1]["end"]) + 0.12), 2),
                "duration": round(
                    float(segment_frames[-1]["end"]) - float(frames[expanded_start_index]["start"]),
                    2,
                ),
                "type": "high_frequency_broadband_noise",
                "confidence": round(min(0.99, max(0.3, score / 100)), 2),
                "score": int(score),
                "status": "abnormal",
                "features": {
                    "mean_rms_db": round(mean_rms, 1),
                    "mean_zcr": round(mean_zcr, 1),
                    "mean_flatness": round(mean_flatness, 3),
                    "mean_centroid_hz": round(mean_centroid, 1),
                    "mean_high_ratio": round(mean_high_ratio, 3),
                },
                "reasons": reasons,
            }
        )
    return results


def _find_low_level_noise_bed_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 16:
        return []
    results: list[dict] = []
    index = 0
    while index < len(frames):
        frame = frames[index]
        is_bed = -52.0 <= float(frame["rmsDb"]) <= -38.0
        is_bed = is_bed and 550.0 <= float(frame.get("zcr", 0.0)) <= 1800.0
        is_bed = is_bed and 120.0 <= float(frame.get("centroid", 0.0)) <= 520.0
        is_bed = is_bed and float(frame.get("highRatio", 0.0)) <= 0.05
        if not is_bed:
            index += 1
            continue
        start_index = index
        gap = 0
        index += 1
        while index < len(frames):
            frame = frames[index]
            is_bed = -53.5 <= float(frame["rmsDb"]) <= -37.0
            is_bed = is_bed and 450.0 <= float(frame.get("zcr", 0.0)) <= 2100.0
            is_bed = is_bed and 100.0 <= float(frame.get("centroid", 0.0)) <= 650.0
            is_bed = is_bed and float(frame.get("highRatio", 0.0)) <= 0.07
            if is_bed:
                gap = 0
                index += 1
                continue
            gap += 1
            if gap > 1:
                break
            index += 1
        end_index = max(start_index, index - gap)
        segment_frames = frames[start_index : end_index + 1]
        if not segment_frames:
            continue
        segment_duration = float(segment_frames[-1]["end"]) - float(segment_frames[0]["start"])
        if segment_duration < 1.5:
            continue
        mean_rms = _mean_rms_db(segment_frames)
        mean_zcr = _mean_frame_value(segment_frames, "zcr")
        mean_flatness = _mean_frame_value(segment_frames, "flatness")
        mean_centroid = _mean_frame_value(segment_frames, "centroid")
        mean_high_ratio = _mean_frame_value(segment_frames, "highRatio")
        mean_crest = _mean_frame_value(segment_frames, "crestDb")
        if not (-48.0 <= mean_rms <= -39.0):
            continue
        if not (650.0 <= mean_zcr <= 1600.0 and 180.0 <= mean_centroid <= 450.0):
            continue
        if mean_high_ratio > 0.04 or mean_crest > 10.0:
            continue
        previous_frames = frames[max(0, start_index - 5) : start_index]
        next_frames = frames[end_index + 1 : min(len(frames), end_index + 6)]
        previous_louder = any(float(item["rmsDb"]) >= -28.0 for item in previous_frames)
        next_louder = any(float(item["rmsDb"]) >= -28.0 for item in next_frames)
        if not previous_louder and not next_louder:
            continue
        score = 65
        reasons = [f"检测到 {segment_duration:.1f} 秒低电平持续底噪"]
        if segment_duration >= 2.0:
            score += 10
            reasons.append(f"底噪持续 {segment_duration:.1f} 秒")
        if mean_rms >= -45.0:
            score += 10
            reasons.append(f"底噪平均音量 {mean_rms:.1f}dBFS")
        if mean_centroid <= 350.0 and mean_high_ratio <= 0.02:
            score += 10
            reasons.append("低频窄带特征明显")
        results.append(
            {
                "start": round(max(0.0, float(segment_frames[0]["start"]) - 0.05), 2),
                "end": round(min(duration, float(segment_frames[-1]["end"]) + 0.08), 2),
                "duration": round(segment_duration, 2),
                "type": "low_level_noise_bed",
                "confidence": round(min(0.99, max(0.3, score / 100)), 2),
                "score": int(score),
                "status": "abnormal",
                "features": {
                    "mean_rms_db": round(mean_rms, 1),
                    "mean_zcr": round(mean_zcr, 1),
                    "mean_flatness": round(mean_flatness, 3),
                    "mean_centroid_hz": round(mean_centroid, 1),
                    "mean_high_ratio": round(mean_high_ratio, 3),
                    "mean_crest_db": round(mean_crest, 1),
                },
                "reasons": reasons,
            }
        )
    return results


def _find_sensitive_low_level_noise_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 12:
        return []
    results: list[dict] = []
    index = 0
    while index < len(frames):
        frame = frames[index]
        is_noise = -65.0 <= float(frame["rmsDb"]) <= -38.0
        is_noise = is_noise and float(frame.get("zcr", 0.0)) >= 700.0
        is_noise = is_noise and float(frame.get("flatness", 0.0)) >= 0.018
        if not is_noise:
            index += 1
            continue
        start_index = index
        gap = 0
        index += 1
        while index < len(frames):
            frame = frames[index]
            is_noise = -66.0 <= float(frame["rmsDb"]) <= -36.0
            is_noise = is_noise and float(frame.get("zcr", 0.0)) >= 550.0
            is_noise = is_noise and float(frame.get("flatness", 0.0)) >= 0.012
            if is_noise:
                gap = 0
                index += 1
                continue
            gap += 1
            if gap > 1:
                break
            index += 1
        end_index = max(start_index, index - gap)
        segment_frames = frames[start_index : end_index + 1]
        if not segment_frames:
            continue
        segment_duration = float(segment_frames[-1]["end"]) - float(segment_frames[0]["start"])
        if segment_duration < 0.8:
            continue
        mean_rms = _mean_rms_db(segment_frames)
        mean_zcr = _mean_frame_value(segment_frames, "zcr")
        mean_flatness = _mean_frame_value(segment_frames, "flatness")
        mean_centroid = _mean_frame_value(segment_frames, "centroid")
        mean_high_ratio = _mean_frame_value(segment_frames, "highRatio")
        if mean_zcr < 850.0 or mean_flatness < 0.022:
            continue
        before_frames = frames[max(0, start_index - 8) : start_index]
        after_frames = frames[end_index + 1 : min(len(frames), end_index + 9)]
        has_neighbor_speech = any(float(item["rmsDb"]) >= -30.0 for item in before_frames + after_frames)
        if not has_neighbor_speech:
            continue
        score = 55
        reasons = [f"严格模式检测到 {segment_duration:.1f} 秒连续低能量非静音噪声"]
        if segment_duration >= 1.2:
            score += 10
            reasons.append(f"可疑底噪持续 {segment_duration:.1f} 秒")
        if mean_rms >= -50.0:
            score += 10
            reasons.append(f"可疑底噪平均音量 {mean_rms:.1f}dBFS")
        results.append(
            {
                "start": round(max(0.0, float(segment_frames[0]["start"]) - 0.04), 2),
                "end": round(min(duration, float(segment_frames[-1]["end"]) + 0.06), 2),
                "duration": round(segment_duration, 2),
                "type": "sensitive_low_level_noise",
                "confidence": round(min(0.89, max(0.3, score / 100)), 2),
                "score": int(min(89, score)),
                "status": "suspicious",
                "features": {
                    "mean_rms_db": round(mean_rms, 1),
                    "mean_zcr": round(mean_zcr, 1),
                    "mean_flatness": round(mean_flatness, 4),
                    "mean_centroid_hz": round(mean_centroid, 1),
                    "mean_high_ratio": round(mean_high_ratio, 3),
                },
                "reasons": reasons,
            }
        )
    return results


def _find_aggressive_quality_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 8:
        return []
    results: list[dict] = []

    def append_segment(start_index: int, end_index: int, segment_type: str, score: int, reasons: list[str]) -> None:
        segment_frames = frames[start_index : end_index + 1]
        if not segment_frames:
            return
        start = float(segment_frames[0]["start"])
        end = float(segment_frames[-1]["end"])
        results.append(
            {
                "start": round(max(0.0, start - 0.03), 2),
                "end": round(min(duration, end + 0.05), 2),
                "duration": round(end - start, 2),
                "type": segment_type,
                "confidence": round(min(0.95, max(0.3, score / 100)), 2),
                "score": int(min(95, score)),
                "status": "suspicious",
                "features": {
                    "mean_rms_db": round(_mean_rms_db(segment_frames), 1),
                    "mean_zcr": round(_mean_frame_value(segment_frames, "zcr"), 1),
                    "mean_flatness": round(_mean_frame_value(segment_frames, "flatness"), 4),
                    "mean_centroid_hz": round(_mean_frame_value(segment_frames, "centroid"), 1),
                    "mean_high_ratio": round(_mean_frame_value(segment_frames, "highRatio"), 3),
                    "mean_crest_db": round(_mean_frame_value(segment_frames, "crestDb"), 1),
                },
                "reasons": reasons,
            }
        )

    index = 0
    while index < len(frames):
        is_gap = float(frames[index]["rmsDb"]) <= -47.0
        if not is_gap:
            index += 1
            continue
        start_index = index
        index += 1
        while index < len(frames) and float(frames[index]["rmsDb"]) <= -45.0:
            index += 1
        end_index = index - 1
        segment_duration = float(frames[end_index]["end"]) - float(frames[start_index]["start"])
        if segment_duration >= 1.0:
            before = frames[max(0, start_index - 8) : start_index]
            after = frames[end_index + 1 : min(len(frames), end_index + 9)]
            if any(float(item["rmsDb"]) >= -32.0 for item in before) or any(float(item["rmsDb"]) >= -32.0 for item in after):
                append_segment(
                    start_index,
                    end_index,
                    "aggressive_long_low_energy_gap",
                    60 + (10 if segment_duration >= 1.5 else 0),
                    [f"激进模式检测到 {segment_duration:.1f} 秒长低能量间隙"],
                )

    index = 0
    while index < len(frames):
        frame = frames[index]
        is_burst = (
            float(frame["rmsDb"]) >= -42.0
            and float(frame.get("zcr", 0.0)) >= 5000.0
            and float(frame.get("highRatio", 0.0)) >= 0.18
            and float(frame.get("flatness", 0.0)) >= 0.035
        )
        if not is_burst:
            index += 1
            continue
        start_index = index
        index += 1
        while index < len(frames):
            frame = frames[index]
            is_burst = (
                float(frame["rmsDb"]) >= -45.0
                and float(frame.get("zcr", 0.0)) >= 4200.0
                and float(frame.get("highRatio", 0.0)) >= 0.12
                and float(frame.get("flatness", 0.0)) >= 0.025
            )
            if not is_burst:
                break
            index += 1
        end_index = index - 1
        segment_duration = float(frames[end_index]["end"]) - float(frames[start_index]["start"])
        if segment_duration >= 0.18:
            append_segment(
                start_index,
                end_index,
                "aggressive_high_frequency_burst",
                62 + (10 if segment_duration >= 0.4 else 0),
                [f"激进模式检测到 {segment_duration:.1f} 秒高频突发/摩擦噪声"],
            )

    return results


def _find_vad_non_speech_noise_segments(duration: float, frames: list[dict], speech_segments: list[dict]) -> list[dict]:
    if duration <= 0 or not frames or not speech_segments:
        return []
    results: list[dict] = []
    sorted_speech = sorted(
        [
            {"start": max(0.0, float(item.get("start", 0.0) or 0.0)), "end": min(duration, float(item.get("end", 0.0) or 0.0))}
            for item in speech_segments
        ],
        key=lambda item: item["start"],
    )
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for segment in sorted_speech:
        start = float(segment["start"])
        end = float(segment["end"])
        if start - cursor >= 0.8:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor >= 0.8:
        gaps.append((cursor, duration))

    for start, end in gaps:
        gap_frames = [item for item in frames if float(item["start"]) < end and float(item["end"]) > start]
        if not gap_frames:
            continue
        gap_duration = end - start
        mean_rms = _mean_rms_db(gap_frames)
        mean_zcr = _mean_frame_value(gap_frames, "zcr")
        mean_flatness = _mean_frame_value(gap_frames, "flatness")
        mean_centroid = _mean_frame_value(gap_frames, "centroid")
        mean_high_ratio = _mean_frame_value(gap_frames, "highRatio")
        if mean_rms <= -58.0 and mean_flatness < 0.025 and mean_zcr < 1200.0:
            continue
        if mean_rms <= -64.0:
            continue
        score = 58
        reasons = [f"Silero 判断为非人声区间，检测到 {gap_duration:.1f} 秒非纯静音信号"]
        if mean_rms >= -52.0:
            score += 8
            reasons.append(f"非人声区间平均音量 {mean_rms:.1f}dBFS")
        if mean_flatness >= 0.045 or mean_high_ratio >= 0.12:
            score += 8
            reasons.append("非人声区间含宽带/高频成分")
        if gap_duration >= 1.4:
            score += 6
            reasons.append(f"非人声区间持续 {gap_duration:.1f} 秒")
        results.append(
            {
                "start": round(max(0.0, start - 0.03), 2),
                "end": round(min(duration, end + 0.03), 2),
                "duration": round(gap_duration, 2),
                "type": "vad_non_speech_noise",
                "confidence": round(min(0.9, max(0.3, score / 100)), 2),
                "score": int(min(90, score)),
                "status": "suspicious",
                "features": {
                    "mean_rms_db": round(mean_rms, 1),
                    "mean_zcr": round(mean_zcr, 1),
                    "mean_flatness": round(mean_flatness, 4),
                    "mean_centroid_hz": round(mean_centroid, 1),
                    "mean_high_ratio": round(mean_high_ratio, 3),
                },
                "reasons": reasons,
            }
        )
    return results


def _find_mid_gap_rising_noise_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 35:
        return []
    results: list[dict] = []
    index = 0
    while index < len(frames):
        frame = frames[index]
        is_start = -70.0 <= float(frame["rmsDb"]) <= -48.0
        is_start = is_start and float(frame.get("flatness", 0.0)) >= 0.035
        is_start = is_start and float(frame.get("zcr", 0.0)) >= 1200.0
        if not is_start:
            index += 1
            continue

        start_index = index
        gap = 0
        index += 1
        while index < len(frames):
            frame = frames[index]
            is_artifact = -72.0 <= float(frame["rmsDb"]) <= -20.0
            is_artifact = is_artifact and float(frame.get("flatness", 0.0)) <= 0.18
            is_artifact = is_artifact and float(frame.get("centroid", 0.0)) <= 1200.0
            is_artifact = is_artifact and float(frame.get("highRatio", 0.0)) <= 0.1
            is_artifact = is_artifact and float(frame.get("crestDb", 0.0)) <= 12.0
            if is_artifact:
                gap = 0
                index += 1
                continue
            gap += 1
            if gap > 1:
                break
            index += 1
        end_index = max(start_index, index - gap)
        segment_frames = frames[start_index : end_index + 1]
        if not segment_frames:
            continue
        segment_duration = float(segment_frames[-1]["end"]) - float(segment_frames[0]["start"])
        if segment_duration < 0.9 or segment_duration > 2.8:
            continue

        rms_values = [float(item["rmsDb"]) for item in segment_frames]
        edge_count = max(1, min(4, len(rms_values) // 3))
        start_rms = sum(rms_values[:edge_count]) / edge_count
        end_rms = sum(rms_values[-edge_count:]) / edge_count
        growth = end_rms - start_rms
        mean_rms = sum(rms_values) / len(rms_values)
        mean_zcr = _mean_frame_value(segment_frames, "zcr")
        mean_flatness = _mean_frame_value(segment_frames, "flatness")
        mean_centroid = _mean_frame_value(segment_frames, "centroid")
        mean_high_ratio = _mean_frame_value(segment_frames, "highRatio")
        mean_crest = _mean_frame_value(segment_frames, "crestDb")
        quiet_ratio = sum(1 for value in rms_values if value <= -50.0) / len(rms_values)
        low_crest_ratio = sum(1 for item in segment_frames if float(item.get("crestDb", 0.0)) <= 10.5) / len(segment_frames)
        if growth < 24.0 or quiet_ratio < 0.18 or low_crest_ratio < 0.45:
            continue
        if mean_centroid > 700.0 or mean_high_ratio > 0.06 or mean_crest > 10.5:
            continue

        previous_frames = frames[max(0, start_index - 12) : start_index]
        next_frames = frames[end_index + 1 : min(len(frames), end_index + 13)]
        previous_louder = any(float(item["rmsDb"]) >= -28.0 for item in previous_frames)
        next_louder = any(float(item["rmsDb"]) >= -20.0 for item in next_frames)
        if not previous_louder or not next_louder:
            continue

        score = 72
        reasons = [f"人声间隙中检测到 {segment_duration:.1f} 秒渐强非人声噪声"]
        if growth >= 30.0:
            score += 10
            reasons.append(f"噪声音量增长 {growth:.1f}dB")
        if mean_crest <= 9.0:
            score += 10
            reasons.append(f"峰均比较低 {mean_crest:.1f}dB")
        if quiet_ratio >= 0.25:
            score += 8
            reasons.append("由低电平间隙逐步变响")
        results.append(
            {
                "start": round(max(0.0, float(segment_frames[0]["start"]) - 0.05), 2),
                "end": round(min(duration, float(segment_frames[-1]["end"]) + 0.08), 2),
                "duration": round(segment_duration, 2),
                "type": "mid_gap_rising_noise",
                "confidence": round(min(0.99, max(0.3, score / 100)), 2),
                "score": int(min(100, score)),
                "status": "abnormal",
                "features": {
                    "mean_rms_db": round(mean_rms, 1),
                    "start_rms_db": round(start_rms, 1),
                    "end_rms_db": round(end_rms, 1),
                    "rms_growth_db": round(growth, 1),
                    "mean_zcr": round(mean_zcr, 1),
                    "mean_flatness": round(mean_flatness, 4),
                    "mean_centroid_hz": round(mean_centroid, 1),
                    "mean_high_ratio": round(mean_high_ratio, 3),
                    "mean_crest_db": round(mean_crest, 1),
                    "quiet_ratio": round(quiet_ratio, 2),
                    "low_crest_ratio": round(low_crest_ratio, 2),
                },
                "reasons": reasons,
            }
        )
    return results


def _find_low_frequency_hum_gap_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 12:
        return []
    results: list[dict] = []
    index = 0
    while index < len(frames):
        frame = frames[index]
        rms = float(frame["rmsDb"])
        zcr = float(frame.get("zcr", 0.0))
        centroid = float(frame.get("centroid", 0.0))
        flatness = float(frame.get("flatness", 0.0))
        high_ratio = float(frame.get("highRatio", 0.0))
        crest = float(frame.get("crestDb", 0.0))
        is_quiet_hum = -54.0 <= rms <= -36.0 and 80.0 <= zcr <= 420.0 and 45.0 <= centroid <= 180.0
        is_quiet_hum = is_quiet_hum and flatness <= 0.012 and high_ratio <= 0.01
        is_loud_hum = -42.0 <= rms <= -23.0 and 80.0 <= zcr <= 320.0 and 45.0 <= centroid <= 160.0
        is_loud_hum = is_loud_hum and flatness <= 0.006 and high_ratio <= 0.006 and crest <= 8.8
        is_hum = is_quiet_hum or is_loud_hum
        if not is_hum:
            index += 1
            continue
        start_index = index
        gap = 0
        index += 1
        while index < len(frames):
            frame = frames[index]
            rms = float(frame["rmsDb"])
            zcr = float(frame.get("zcr", 0.0))
            centroid = float(frame.get("centroid", 0.0))
            flatness = float(frame.get("flatness", 0.0))
            high_ratio = float(frame.get("highRatio", 0.0))
            crest = float(frame.get("crestDb", 0.0))
            is_quiet_hum = -55.0 <= rms <= -35.0 and 60.0 <= zcr <= 520.0 and 40.0 <= centroid <= 220.0
            is_quiet_hum = is_quiet_hum and flatness <= 0.018 and high_ratio <= 0.015
            is_loud_hum = -58.0 <= rms <= -22.0 and 60.0 <= zcr <= 360.0 and 40.0 <= centroid <= 180.0
            is_loud_hum = is_loud_hum and flatness <= 0.008 and high_ratio <= 0.008 and crest <= 9.2
            is_hum = is_quiet_hum or is_loud_hum
            if is_hum:
                gap = 0
                index += 1
                continue
            gap += 1
            if gap > 1:
                break
            index += 1
        end_index = max(start_index, index - gap)
        segment_frames = frames[start_index : end_index + 1]
        if not segment_frames:
            continue
        segment_duration = float(segment_frames[-1]["end"]) - float(segment_frames[0]["start"])
        if segment_duration < 0.9:
            continue
        mean_rms = _mean_rms_db(segment_frames)
        mean_zcr = _mean_frame_value(segment_frames, "zcr")
        mean_flatness = _mean_frame_value(segment_frames, "flatness")
        mean_centroid = _mean_frame_value(segment_frames, "centroid")
        mean_high_ratio = _mean_frame_value(segment_frames, "highRatio")
        mean_crest = _mean_frame_value(segment_frames, "crestDb")
        quiet_hum = -49.0 <= mean_rms <= -38.0 and 100.0 <= mean_zcr <= 360.0 and 55.0 <= mean_centroid <= 130.0
        loud_hum = -38.0 <= mean_rms <= -24.0 and 100.0 <= mean_zcr <= 300.0 and 55.0 <= mean_centroid <= 130.0
        loud_hum = loud_hum and mean_flatness <= 0.004 and mean_high_ratio <= 0.004 and mean_crest <= 8.5
        if not quiet_hum and not loud_hum:
            continue
        previous_frames = frames[max(0, start_index - 8) : start_index]
        next_frames = frames[end_index + 1 : min(len(frames), end_index + 9)]
        previous_louder = any(float(item["rmsDb"]) >= -28.0 for item in previous_frames)
        next_louder = any(float(item["rmsDb"]) >= -28.0 for item in next_frames)
        if not previous_louder or not next_louder:
            continue
        score = 70
        reasons = [f"检测到 {segment_duration:.1f} 秒低频窄带嗡声"]
        if segment_duration >= 1.2:
            score += 10
            reasons.append(f"嗡声持续 {segment_duration:.1f} 秒")
        if mean_rms >= -44.0:
            score += 10
            reasons.append(f"嗡声平均音量 {mean_rms:.1f}dBFS")
        if mean_flatness <= 0.004 and mean_high_ratio <= 0.003:
            score += 10
            reasons.append("低频窄带特征明显")
        results.append(
            {
                "start": round(max(0.0, float(segment_frames[0]["start"]) - 0.05), 2),
                "end": round(min(duration, float(segment_frames[-1]["end"]) + 0.08), 2),
                "duration": round(segment_duration, 2),
                "type": "low_frequency_hum_gap",
                "confidence": round(min(0.99, max(0.3, score / 100)), 2),
                "score": int(score),
                "status": "abnormal",
                "features": {
                    "mean_rms_db": round(mean_rms, 1),
                    "mean_zcr": round(mean_zcr, 1),
                    "mean_flatness": round(mean_flatness, 3),
                    "mean_centroid_hz": round(mean_centroid, 1),
                    "mean_high_ratio": round(mean_high_ratio, 3),
                    "mean_crest_db": round(mean_crest, 1),
                },
                "reasons": reasons,
            }
        )
    return results


def _find_low_level_residual_gap_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 16:
        return []
    results: list[dict] = []
    index = 0
    while index < len(frames):
        frame = frames[index]
        is_residual = -58.0 <= float(frame["rmsDb"]) <= -38.0
        is_residual = is_residual and 180.0 <= float(frame.get("zcr", 0.0)) <= 1900.0
        is_residual = is_residual and 80.0 <= float(frame.get("centroid", 0.0)) <= 520.0
        is_residual = is_residual and float(frame.get("flatness", 0.0)) <= 0.028
        is_residual = is_residual and float(frame.get("highRatio", 0.0)) <= 0.06
        if not is_residual:
            index += 1
            continue
        start_index = index
        gap = 0
        index += 1
        while index < len(frames):
            frame = frames[index]
            is_residual = -62.0 <= float(frame["rmsDb"]) <= -37.0
            is_residual = is_residual and 120.0 <= float(frame.get("zcr", 0.0)) <= 2100.0
            is_residual = is_residual and 70.0 <= float(frame.get("centroid", 0.0)) <= 650.0
            is_residual = is_residual and float(frame.get("flatness", 0.0)) <= 0.035
            is_residual = is_residual and float(frame.get("highRatio", 0.0)) <= 0.07
            if is_residual:
                gap = 0
                index += 1
                continue
            gap += 1
            if gap > 1:
                break
            index += 1
        end_index = max(start_index, index - gap)
        segment_frames = frames[start_index : end_index + 1]
        if not segment_frames:
            continue
        segment_duration = float(segment_frames[-1]["end"]) - float(segment_frames[0]["start"])
        if segment_duration < 1.2:
            continue
        mean_rms = _mean_rms_db(segment_frames)
        mean_zcr = _mean_frame_value(segment_frames, "zcr")
        mean_flatness = _mean_frame_value(segment_frames, "flatness")
        mean_centroid = _mean_frame_value(segment_frames, "centroid")
        mean_high_ratio = _mean_frame_value(segment_frames, "highRatio")
        mean_crest = _mean_frame_value(segment_frames, "crestDb")
        quiet_tonal_residual = (
            -57.5 <= mean_rms <= -48.0
            and segment_duration >= 1.6
            and 250.0 <= mean_zcr <= 900.0
            and 120.0 <= mean_centroid <= 420.0
            and mean_flatness <= 0.012
            and mean_high_ratio <= 0.025
            and mean_crest <= 9.0
        )
        if not (-49.5 <= mean_rms <= -39.0) and not quiet_tonal_residual:
            continue
        if mean_flatness > 0.025 or mean_high_ratio > 0.055 or mean_crest > 10.2:
            continue
        previous_frames = frames[max(0, start_index - 12) : start_index]
        next_frames = frames[end_index + 1 : min(len(frames), end_index + 13)]
        previous_louder = any(float(item["rmsDb"]) >= -28.0 for item in previous_frames)
        next_louder = any(float(item["rmsDb"]) >= -28.0 for item in next_frames)
        if not previous_louder or not next_louder:
            continue
        expanded_start_index = start_index
        cursor = start_index - 1
        while cursor >= 0 and start_index - cursor <= 8:
            if float(frames[cursor]["rmsDb"]) > -38.0:
                break
            expanded_start_index = cursor
            cursor -= 1
        score = 65
        reasons = [f"检测到 {segment_duration:.1f} 秒低电平残留噪声"]
        if segment_duration >= 1.8:
            score += 10
            reasons.append(f"残留噪声持续 {segment_duration:.1f} 秒")
        if mean_rms >= -45.0:
            score += 10
            reasons.append(f"残留噪声平均音量 {mean_rms:.1f}dBFS")
        if mean_flatness <= 0.012 and mean_high_ratio <= 0.02:
            score += 10
            reasons.append("低频窄带残留特征明显")
        results.append(
            {
                "start": round(max(0.0, float(frames[expanded_start_index]["start"]) - 0.05), 2),
                "end": round(min(duration, float(segment_frames[-1]["end"]) + 0.08), 2),
                "duration": round(float(segment_frames[-1]["end"]) - float(frames[expanded_start_index]["start"]), 2),
                "type": "low_level_residual_gap",
                "confidence": round(min(0.99, max(0.3, score / 100)), 2),
                "score": int(min(100, score)),
                "status": "abnormal",
                "features": {
                    "mean_rms_db": round(mean_rms, 1),
                    "mean_zcr": round(mean_zcr, 1),
                    "mean_flatness": round(mean_flatness, 3),
                    "mean_centroid_hz": round(mean_centroid, 1),
                    "mean_high_ratio": round(mean_high_ratio, 3),
                    "mean_crest_db": round(mean_crest, 1),
                },
                "reasons": reasons,
            }
        )
    return results


def _find_rising_tonal_artifact_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 30:
        return []
    results: list[dict] = []
    index = 0
    while index < len(frames):
        frame = frames[index]
        is_tonal = -66.0 <= float(frame["rmsDb"]) <= -12.0
        is_tonal = is_tonal and 80.0 <= float(frame.get("zcr", 0.0)) <= 850.0
        is_tonal = is_tonal and 55.0 <= float(frame.get("centroid", 0.0)) <= 260.0
        is_tonal = is_tonal and float(frame.get("flatness", 0.0)) <= 0.018
        is_tonal = is_tonal and float(frame.get("highRatio", 0.0)) <= 0.01
        if not is_tonal:
            index += 1
            continue
        start_index = index
        gap = 0
        index += 1
        while index < len(frames):
            frame = frames[index]
            is_tonal = -67.0 <= float(frame["rmsDb"]) <= -11.0
            is_tonal = is_tonal and 60.0 <= float(frame.get("zcr", 0.0)) <= 1000.0
            is_tonal = is_tonal and 50.0 <= float(frame.get("centroid", 0.0)) <= 320.0
            is_tonal = is_tonal and float(frame.get("flatness", 0.0)) <= 0.026
            is_tonal = is_tonal and float(frame.get("highRatio", 0.0)) <= 0.02
            if is_tonal:
                gap = 0
                index += 1
                continue
            gap += 1
            if gap > 1:
                break
            index += 1
        end_index = max(start_index, index - gap)
        segment_frames = frames[start_index : end_index + 1]
        if not segment_frames:
            continue
        segment_duration = float(segment_frames[-1]["end"]) - float(segment_frames[0]["start"])
        if segment_duration < 1.2 or segment_duration > 4.5:
            continue
        mean_rms = _mean_rms_db(segment_frames)
        mean_zcr = _mean_frame_value(segment_frames, "zcr")
        mean_flatness = _mean_frame_value(segment_frames, "flatness")
        mean_centroid = _mean_frame_value(segment_frames, "centroid")
        mean_high_ratio = _mean_frame_value(segment_frames, "highRatio")
        mean_crest = _mean_frame_value(segment_frames, "crestDb")
        if mean_flatness > 0.014 or mean_high_ratio > 0.01 or mean_crest > 8.8:
            continue
        if mean_zcr > 520.0 or mean_centroid > 180.0:
            continue
        rms_values = [float(item["rmsDb"]) for item in segment_frames]
        edge_count = max(1, min(5, len(rms_values) // 3))
        start_rms = sum(rms_values[:edge_count]) / edge_count
        end_rms = sum(rms_values[-edge_count:]) / edge_count
        growth = end_rms - start_rms
        loud_ratio = sum(1 for value in rms_values if value >= -24.0) / len(rms_values)
        quiet_ratio = sum(1 for value in rms_values if value <= -50.0) / len(rms_values)
        if growth < 22.0 or loud_ratio < 0.18 or quiet_ratio < 0.18:
            continue
        previous_frames = frames[max(0, start_index - 12) : start_index]
        next_frames = frames[end_index + 1 : min(len(frames), end_index + 13)]
        previous_speech_like = any(
            float(item["rmsDb"]) >= -28.0 and float(item.get("crestDb", 0.0)) >= 10.0 for item in previous_frames
        )
        next_speech_like = any(
            float(item["rmsDb"]) >= -28.0 and float(item.get("crestDb", 0.0)) >= 10.0 for item in next_frames
        )
        if not previous_speech_like or not next_speech_like:
            continue
        score = 75
        reasons = [f"检测到 {segment_duration:.1f} 秒低频窄带渐强伪影"]
        if growth >= 35.0:
            score += 10
            reasons.append(f"伪影音量增长 {growth:.1f}dB")
        if mean_crest <= 7.5:
            score += 10
            reasons.append(f"峰均比较低 {mean_crest:.1f}dB")
        if mean_flatness <= 0.006:
            score += 10
            reasons.append(f"谱平坦度异常低 {mean_flatness:.3f}")
        results.append(
            {
                "start": round(max(0.0, float(segment_frames[0]["start"]) - 0.05), 2),
                "end": round(min(duration, float(segment_frames[-1]["end"]) + 0.08), 2),
                "duration": round(segment_duration, 2),
                "type": "rising_tonal_artifact",
                "confidence": round(min(0.99, max(0.3, score / 100)), 2),
                "score": int(min(100, score)),
                "status": "abnormal",
                "features": {
                    "mean_rms_db": round(mean_rms, 1),
                    "mean_zcr": round(mean_zcr, 1),
                    "mean_flatness": round(mean_flatness, 4),
                    "mean_centroid_hz": round(mean_centroid, 1),
                    "mean_high_ratio": round(mean_high_ratio, 3),
                    "mean_crest_db": round(mean_crest, 1),
                    "rms_growth_db": round(growth, 1),
                    "loud_ratio": round(loud_ratio, 2),
                    "quiet_ratio": round(quiet_ratio, 2),
                },
                "reasons": reasons,
            }
        )
    return results


def _find_sustained_tonal_noise_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 80:
        return []
    results: list[dict] = []
    index = 0
    while index < len(frames):
        frame = frames[index]
        is_tonal_noise = (
            -32.0 <= float(frame["rmsDb"]) <= -12.0
            and float(frame.get("crestDb", 0.0)) <= 9.2
            and float(frame.get("flatness", 0.0)) <= 0.028
            and float(frame.get("highRatio", 0.0)) <= 0.08
            and float(frame.get("centroid", 0.0)) <= 1200.0
        )
        if not is_tonal_noise:
            index += 1
            continue
        start_index = index
        gap = 0
        index += 1
        while index < len(frames):
            frame = frames[index]
            is_tonal_noise = (
                -36.0 <= float(frame["rmsDb"]) <= -10.0
                and float(frame.get("crestDb", 0.0)) <= 10.0
                and float(frame.get("flatness", 0.0)) <= 0.04
                and float(frame.get("highRatio", 0.0)) <= 0.12
                and float(frame.get("centroid", 0.0)) <= 1600.0
            )
            if is_tonal_noise:
                gap = 0
                index += 1
                continue
            gap += 1
            if gap > 2:
                break
            index += 1
        end_index = max(start_index, index - gap)
        segment_frames = frames[start_index : end_index + 1]
        if not segment_frames:
            continue
        segment_duration = float(segment_frames[-1]["end"]) - float(segment_frames[0]["start"])
        if segment_duration < 4.0 or segment_duration > 12.0:
            continue
        mean_rms = _mean_rms_db(segment_frames)
        mean_zcr = _mean_frame_value(segment_frames, "zcr")
        mean_flatness = _mean_frame_value(segment_frames, "flatness")
        mean_centroid = _mean_frame_value(segment_frames, "centroid")
        mean_high_ratio = _mean_frame_value(segment_frames, "highRatio")
        mean_crest = _mean_frame_value(segment_frames, "crestDb")
        low_crest_ratio = sum(1 for item in segment_frames if float(item.get("crestDb", 0.0)) <= 9.5) / len(segment_frames)
        tonal_ratio = sum(
            1
            for item in segment_frames
            if float(item.get("crestDb", 0.0)) <= 9.5
            and float(item.get("flatness", 0.0)) <= 0.035
            and float(item.get("highRatio", 0.0)) <= 0.12
        ) / len(segment_frames)
        loud_ratio = sum(1 for item in segment_frames if float(item["rmsDb"]) >= -28.0) / len(segment_frames)
        if not (-30.0 <= mean_rms <= -16.0):
            continue
        if mean_crest > 8.8 or low_crest_ratio < 0.82 or tonal_ratio < 0.75 or loud_ratio < 0.45:
            continue
        if mean_flatness > 0.025 or mean_high_ratio > 0.09 or mean_centroid > 900.0:
            continue
        previous_frames = frames[max(0, start_index - 24) : start_index]
        previous_speech_like = any(
            float(item["rmsDb"]) >= -26.0
            and float(item.get("crestDb", 0.0)) >= 10.0
            and float(item.get("flatness", 0.0)) >= 0.01
            for item in previous_frames
        )
        if not previous_speech_like:
            continue

        score = 78
        reasons = [f"检测到 {segment_duration:.1f} 秒持续低峰均比窄带噪声"]
        if segment_duration >= 6.0:
            score += 8
            reasons.append(f"异常段持续 {segment_duration:.1f} 秒")
        if mean_crest <= 7.5:
            score += 8
            reasons.append(f"峰均比较低 {mean_crest:.1f}dB")
        if mean_flatness <= 0.014:
            score += 6
            reasons.append(f"谱平坦度偏低 {mean_flatness:.3f}")
        results.append(
            {
                "start": round(max(0.0, float(segment_frames[0]["start"]) - 0.05), 2),
                "end": round(min(duration, float(segment_frames[-1]["end"]) + 0.08), 2),
                "duration": round(segment_duration, 2),
                "type": "sustained_tonal_noise",
                "confidence": round(min(0.99, max(0.3, score / 100)), 2),
                "score": int(min(100, score)),
                "status": "abnormal",
                "features": {
                    "mean_rms_db": round(mean_rms, 1),
                    "mean_zcr": round(mean_zcr, 1),
                    "mean_flatness": round(mean_flatness, 4),
                    "mean_centroid_hz": round(mean_centroid, 1),
                    "mean_high_ratio": round(mean_high_ratio, 3),
                    "mean_crest_db": round(mean_crest, 1),
                    "low_crest_ratio": round(low_crest_ratio, 2),
                    "tonal_ratio": round(tonal_ratio, 2),
                    "loud_ratio": round(loud_ratio, 2),
                },
                "reasons": reasons,
            }
        )
    return results


def _find_sustained_non_speech_activity_segments(
    line_text: str, duration: float, frames: list[dict], speech_segments: list[dict]
) -> list[dict]:
    if duration < 12.0 or len(frames) < 120 or speech_segments:
        return []
    if _line_text_char_count(line_text) < 40:
        return []
    results: list[dict] = []
    window_size = 80
    index = 0
    while index <= len(frames) - window_size:
        window = frames[index : index + window_size]
        active_frames = [item for item in window if float(item["rmsDb"]) >= -46.0]
        if len(active_frames) / len(window) < 0.68:
            index += 1
            continue
        speech_like_frames = [
            item
            for item in active_frames
            if float(item["rmsDb"]) >= -34.0
            and float(item.get("crestDb", 0.0)) >= 10.5
            and (
                float(item.get("flatness", 0.0)) >= 0.018
                or float(item.get("highRatio", 0.0)) >= 0.04
                or float(item.get("zcr", 0.0)) >= 1200.0
            )
        ]
        low_crest_frames = [item for item in active_frames if float(item.get("crestDb", 0.0)) <= 9.5]
        tonal_frames = [
            item
            for item in active_frames
            if float(item.get("flatness", 0.0)) <= 0.035 and float(item.get("highRatio", 0.0)) <= 0.14
        ]
        speech_like_ratio = len(speech_like_frames) / max(1, len(active_frames))
        low_crest_ratio = len(low_crest_frames) / max(1, len(active_frames))
        tonal_ratio = len(tonal_frames) / max(1, len(active_frames))
        if speech_like_ratio > 0.35 or (low_crest_ratio < 0.38 and tonal_ratio < 0.55):
            index += 1
            continue

        start_index = index
        end_index = index + window_size - 1
        while start_index > 0:
            prev = frames[start_index - 1]
            is_related = float(prev["rmsDb"]) >= -48.0 and not (
                float(prev.get("crestDb", 0.0)) >= 12.0 and float(prev.get("flatness", 0.0)) >= 0.04
            )
            if not is_related:
                break
            start_index -= 1
        while end_index + 1 < len(frames):
            nxt = frames[end_index + 1]
            is_related = float(nxt["rmsDb"]) >= -48.0 and not (
                float(nxt.get("crestDb", 0.0)) >= 12.0 and float(nxt.get("flatness", 0.0)) >= 0.04
            )
            if not is_related:
                break
            end_index += 1

        segment_frames = frames[start_index : end_index + 1]
        segment_duration = float(segment_frames[-1]["end"]) - float(segment_frames[0]["start"])
        if segment_duration < 6.0:
            index += 1
            continue
        features = _line_audio_noise_feature_dict(frames, {"start": segment_frames[0]["start"], "end": segment_frames[-1]["end"]})
        if float(features.get("mean_rms_db", -120.0)) < -36.0:
            index = end_index + 1
            continue
        if float(features.get("silence_ratio", 1.0)) > 0.28 or float(features.get("loud_ratio", 0.0)) < 0.55:
            index = end_index + 1
            continue
        if float(features.get("low_crest_ratio", 0.0)) < 0.38 or float(features.get("tonal_ratio", 0.0)) < 0.35:
            index = end_index + 1
            continue
        score = 78
        reasons = [f"Silero 未识别到人声，检测到 {segment_duration:.1f} 秒持续非人声活跃信号"]
        if float(features.get("low_crest_ratio", 0.0)) >= 0.3:
            score += 8
            reasons.append(f"低峰均比片段占比 {float(features.get('low_crest_ratio', 0.0)):.2f}")
        if float(features.get("tonal_ratio", 0.0)) >= 0.25:
            score += 6
            reasons.append(f"窄带/周期性特征占比 {float(features.get('tonal_ratio', 0.0)):.2f}")
        segment = {
            "start": round(max(0.0, float(segment_frames[0]["start"]) - 0.05), 2),
            "end": round(min(duration, float(segment_frames[-1]["end"]) + 0.08), 2),
            "duration": round(segment_duration, 2),
            "type": "sustained_non_speech_activity",
            "confidence": round(min(0.99, max(0.3, score / 100)), 2),
            "score": int(min(100, score)),
            "status": "abnormal",
            "features": {
                "mean_rms_db": round(float(features.get("mean_rms_db", 0.0)), 1),
                "mean_zcr": round(float(features.get("mean_zcr", 0.0)), 1),
                "mean_flatness": round(float(features.get("mean_flatness", 0.0)), 4),
                "mean_centroid_hz": round(float(features.get("mean_centroid_hz", 0.0)), 1),
                "mean_high_ratio": round(float(features.get("mean_high_ratio", 0.0)), 3),
                "mean_crest_db": round(float(features.get("mean_crest_db", 0.0)), 1),
                "low_crest_ratio": round(float(features.get("low_crest_ratio", 0.0)), 2),
                "tonal_ratio": round(float(features.get("tonal_ratio", 0.0)), 2),
                "loud_ratio": round(float(features.get("loud_ratio", 0.0)), 2),
                "silence_ratio": round(float(features.get("silence_ratio", 0.0)), 2),
            },
            "reasons": reasons,
        }
        if not any(_segments_overlap(segment, item) for item in results):
            results.append(segment)
        index = end_index + 1
    return results


def _find_mid_gap_tts_artifact_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 55:
        return []
    results: list[dict] = []
    index = 0
    while index < len(frames):
        frame = frames[index]
        is_artifact = -42.0 <= float(frame["rmsDb"]) <= -18.0
        is_artifact = is_artifact and float(frame.get("crestDb", 0.0)) <= 10.8
        is_artifact = is_artifact and float(frame.get("flatness", 0.0)) <= 0.06
        is_artifact = is_artifact and float(frame.get("centroid", 0.0)) <= 2300.0
        if not is_artifact:
            index += 1
            continue
        start_index = index
        gap = 0
        index += 1
        while index < len(frames):
            frame = frames[index]
            is_artifact = -43.0 <= float(frame["rmsDb"]) <= -17.0
            is_artifact = is_artifact and float(frame.get("crestDb", 0.0)) <= 12.2
            is_artifact = is_artifact and float(frame.get("flatness", 0.0)) <= 0.12
            is_artifact = is_artifact and float(frame.get("centroid", 0.0)) <= 3600.0
            if is_artifact:
                gap = 0
                index += 1
                continue
            gap += 1
            if gap > 5:
                break
            index += 1
        end_index = min(len(frames) - 1, max(start_index, index - gap))
        artifact_start_index = start_index
        while artifact_start_index <= end_index:
            frame = frames[artifact_start_index]
            if float(frame["rmsDb"]) <= -24.0 and float(frame.get("crestDb", 0.0)) <= 10.8:
                break
            artifact_start_index += 1
        lead_search_end = min(end_index, start_index + 30)
        last_leading_speech_index: int | None = None
        for lead_index in range(start_index, lead_search_end + 1):
            frame = frames[lead_index]
            if (
                float(frame["rmsDb"]) >= -22.0
                or float(frame.get("peakDb", -120.0)) >= -10.0
                or float(frame.get("crestDb", 0.0)) >= 11.5
            ):
                last_leading_speech_index = lead_index
        if last_leading_speech_index is not None:
            trimmed_start_index = last_leading_speech_index + 1
            if trimmed_start_index <= end_index and float(frames[end_index]["end"]) - float(frames[trimmed_start_index]["start"]) >= 4.0:
                artifact_start_index = max(artifact_start_index, trimmed_start_index)
        segment_frames = frames[artifact_start_index : end_index + 1]
        if not segment_frames:
            continue
        segment_duration = float(segment_frames[-1]["end"]) - float(segment_frames[0]["start"])
        if segment_duration < 4.0 or segment_duration > 12.0:
            continue
        mean_rms = _mean_rms_db(segment_frames)
        mean_zcr = _mean_frame_value(segment_frames, "zcr")
        mean_flatness = _mean_frame_value(segment_frames, "flatness")
        mean_centroid = _mean_frame_value(segment_frames, "centroid")
        mean_high_ratio = _mean_frame_value(segment_frames, "highRatio")
        mean_crest = _mean_frame_value(segment_frames, "crestDb")
        if not (-36.0 <= mean_rms <= -22.0):
            continue
        if mean_crest > 9.2 or mean_flatness > 0.035:
            continue
        low_crest_ratio = sum(1 for item in segment_frames if float(item.get("crestDb", 0.0)) <= 9.5) / len(segment_frames)
        low_flat_ratio = sum(1 for item in segment_frames if float(item.get("flatness", 0.0)) <= 0.035) / len(segment_frames)
        if low_crest_ratio < 0.62 or low_flat_ratio < 0.7:
            continue
        previous_frames = frames[max(0, artifact_start_index - 18) : artifact_start_index]
        next_frames = frames[end_index + 1 : min(len(frames), end_index + 16)]
        previous_speech_like = any(
            float(item["rmsDb"]) >= -24.0 and float(item.get("crestDb", 0.0)) >= 10.5 for item in previous_frames
        )
        next_speech_like = any(
            float(item["rmsDb"]) >= -24.0 and float(item.get("crestDb", 0.0)) >= 10.5 for item in next_frames
        )
        next_silence_like = any(float(item["rmsDb"]) <= -55.0 for item in next_frames)
        if not previous_speech_like or not (next_speech_like or next_silence_like):
            continue
        score = 70
        reasons = [f"检测到 {segment_duration:.1f} 秒人声间合成伪影"]
        if segment_duration >= 6.0:
            score += 10
            reasons.append(f"异常段持续 {segment_duration:.1f} 秒")
        if mean_crest <= 8.5:
            score += 10
            reasons.append(f"峰均比较低 {mean_crest:.1f}dB")
        if mean_flatness <= 0.025:
            score += 10
            reasons.append(f"谱平坦度偏低 {mean_flatness:.3f}")
        if mean_high_ratio >= 0.08:
            score += 5
            reasons.append(f"夹杂高频能量 {mean_high_ratio:.2f}")
        results.append(
            {
                "start": round(max(0.0, float(segment_frames[0]["start"]) - 0.05), 2),
                "end": round(min(duration, float(segment_frames[-1]["end"]) + 0.08), 2),
                "duration": round(segment_duration, 2),
                "type": "mid_gap_tts_artifact",
                "confidence": round(min(0.99, max(0.3, score / 100)), 2),
                "score": int(min(100, score)),
                "status": "abnormal",
                "features": {
                    "mean_rms_db": round(mean_rms, 1),
                    "mean_zcr": round(mean_zcr, 1),
                    "mean_flatness": round(mean_flatness, 4),
                    "mean_centroid_hz": round(mean_centroid, 1),
                    "mean_high_ratio": round(mean_high_ratio, 3),
                    "mean_crest_db": round(mean_crest, 1),
                    "low_crest_ratio": round(low_crest_ratio, 2),
                    "low_flat_ratio": round(low_flat_ratio, 2),
                },
                "reasons": reasons,
            }
        )
    return results


def _find_short_trailing_noise_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or duration > 8.0 or len(frames) < 20:
        return []
    search_limit = min(len(frames), max(1, int(len(frames) * 0.7)))
    speech_end_index: int | None = None
    for index, frame in enumerate(frames[:search_limit]):
        is_speech_like = float(frame["rmsDb"]) >= -27.0 and float(frame.get("peakDb", -120.0)) >= -16.5
        if is_speech_like:
            speech_end_index = index
    if speech_end_index is None or speech_end_index + 8 >= len(frames):
        return []

    trailing_frames = frames[speech_end_index + 1 :]
    trailing_start = float(trailing_frames[0]["start"])
    trailing_duration = duration - trailing_start
    if trailing_duration < 1.2 or trailing_start > duration * 0.68:
        return []
    mean_rms = _mean_rms_db(trailing_frames)
    mean_zcr = _mean_frame_value(trailing_frames, "zcr")
    mean_flatness = _mean_frame_value(trailing_frames, "flatness")
    mean_centroid = _mean_frame_value(trailing_frames, "centroid")
    mean_high_ratio = _mean_frame_value(trailing_frames, "highRatio")
    mean_crest = _mean_frame_value(trailing_frames, "crestDb")
    loud_tail_frames = [item for item in trailing_frames if float(item["rmsDb"]) >= -34.0]
    high_tail_frames = [
        item
        for item in trailing_frames
        if float(item.get("zcr", 0.0)) >= 6000.0 and float(item.get("highRatio", 0.0)) >= 0.25
    ]
    low_noise_frames = [
        item
        for item in trailing_frames
        if -65.0 <= float(item["rmsDb"]) <= -38.0 and float(item.get("flatness", 0.0)) >= 0.03
    ]
    high_tail_duration = sum(float(item["end"]) - float(item["start"]) for item in high_tail_frames)
    low_noise_duration = sum(float(item["end"]) - float(item["start"]) for item in low_noise_frames)
    if high_tail_duration < 0.5:
        return []
    if low_noise_duration < 0.7:
        return []
    if mean_rms > -40.0:
        return []

    score = 70
    reasons = [f"短台词人声后检测到 {trailing_duration:.1f} 秒尾部噪声"]
    if high_tail_duration >= 0.5:
        score += 10
        reasons.append(f"末端高频噪声持续 {high_tail_duration:.1f} 秒")
    if low_noise_duration >= 0.8:
        score += 10
        reasons.append(f"低电平拖尾持续 {low_noise_duration:.1f} 秒")
    if max(float(item.get("highRatio", 0.0)) for item in trailing_frames) >= 0.6:
        score += 10
        reasons.append("末端高频能量占比明显偏高")
    return [
        {
            "start": round(max(0.0, trailing_start - 0.03), 2),
            "end": round(duration, 2),
            "duration": round(trailing_duration, 2),
            "type": "short_trailing_noise",
            "confidence": round(min(0.99, max(0.3, score / 100)), 2),
            "score": int(min(100, score)),
            "status": "abnormal",
            "features": {
                "mean_rms_db": round(mean_rms, 1),
                "mean_zcr": round(mean_zcr, 1),
                "mean_flatness": round(mean_flatness, 4),
                "mean_centroid_hz": round(mean_centroid, 1),
                "mean_high_ratio": round(mean_high_ratio, 3),
                "mean_crest_db": round(mean_crest, 1),
                "high_tail_duration": round(high_tail_duration, 2),
                "low_noise_duration": round(low_noise_duration, 2),
            },
            "reasons": reasons,
        }
    ]


def _find_post_speech_tail_noise_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 14:
        return []
    groups = _active_audio_groups(frames, threshold=-45.0)
    if len(groups) < 2:
        return []
    tail = groups[-1]
    previous = groups[-2]
    tail_start = float(tail.get("start", 0.0) or 0.0)
    tail_end = float(tail.get("end", tail_start) or tail_start)
    tail_duration = max(0.0, tail_end - tail_start)
    gap = tail_start - float(previous.get("end", 0.0) or 0.0)
    if duration - tail_end > 0.2:
        return []
    if tail_duration < 0.35 or tail_duration > 2.2:
        return []
    if gap < 0.22:
        return []
    # A short pause followed by a long active tail is often low-energy speech
    # that Silero missed, not post-speech noise.
    if tail_duration > 1.2 and gap < 0.45:
        return []
    if float(previous.get("duration", 0.0) or 0.0) < 0.45:
        return []

    tail_frames = _segment_frames(frames, {"start": tail_start, "end": min(duration, tail_end + 0.12)})
    if not tail_frames:
        return []
    mean_rms = _mean_rms_db(tail_frames)
    mean_zcr = _mean_frame_value(tail_frames, "zcr")
    mean_flatness = _mean_frame_value(tail_frames, "flatness")
    mean_centroid = _mean_frame_value(tail_frames, "centroid")
    mean_high_ratio = _mean_frame_value(tail_frames, "highRatio")
    mean_crest = _mean_frame_value(tail_frames, "crestDb")
    high_frequency_tail = mean_zcr >= 3600.0 and mean_flatness >= 0.055 and mean_high_ratio >= 0.12
    broadband_tail = mean_zcr >= 2600.0 and mean_flatness >= 0.09 and mean_centroid >= 1800.0
    clearly_broadband_tail = mean_flatness >= 0.16 and mean_centroid >= 2600.0 and mean_high_ratio >= 0.22
    if gap < 0.85 and tail_duration > 0.7 and not clearly_broadband_tail:
        return []
    if tail_duration > 0.7 and mean_rms >= -34.0 and mean_crest >= 12.0:
        return []
    if tail_duration > 1.2 and not clearly_broadband_tail:
        return []
    if not high_frequency_tail and not broadband_tail:
        return []

    score = 70
    reasons = [f"人声结束并静默 {gap:.1f} 秒后，尾部出现 {tail_duration:.1f} 秒非人声噪声"]
    if mean_rms >= -32.0:
        score += 10
        reasons.append(f"尾部噪声音量较高 {mean_rms:.1f}dBFS")
    if mean_high_ratio >= 0.2:
        score += 10
        reasons.append(f"尾部高频能量占比 {mean_high_ratio:.2f}")
    if mean_flatness >= 0.12:
        score += 10
        reasons.append(f"尾部宽带噪声特征明显 {mean_flatness:.3f}")
    return [
        {
            "start": round(max(0.0, tail_start - 0.03), 2),
            "end": round(duration, 2),
            "duration": round(max(0.0, duration - tail_start), 2),
            "type": "post_speech_tail_noise",
            "confidence": round(min(0.99, max(0.3, score / 100)), 2),
            "score": int(min(100, score)),
            "status": "abnormal",
            "features": {
                "gap_seconds": round(gap, 2),
                "tail_duration_seconds": round(tail_duration, 2),
                "mean_rms_db": round(mean_rms, 1),
                "mean_zcr": round(mean_zcr, 1),
                "mean_flatness": round(mean_flatness, 4),
                "mean_centroid_hz": round(mean_centroid, 1),
                "mean_high_ratio": round(mean_high_ratio, 3),
                "mean_crest_db": round(mean_crest, 1),
            },
            "reasons": reasons,
        }
    ]


def _find_sustained_tts_artifact_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 90:
        return []
    results: list[dict] = []
    window_size = 80
    index = 0
    while index <= len(frames) - window_size:
        window = frames[index : index + window_size]
        mean_rms = _mean_rms_db(window)
        mean_flatness = _mean_frame_value(window, "flatness")
        mean_crest = _mean_frame_value(window, "crestDb")
        mean_zcr = _mean_frame_value(window, "zcr")
        if not (mean_rms >= -32.0 and mean_flatness <= 0.008 and mean_crest <= 9.8 and mean_zcr >= 900.0):
            index += 1
            continue

        start_index = index
        end_index = index + window_size - 1
        while start_index > 0:
            prev = frames[start_index - 1]
            if float(prev["rmsDb"]) < -50.0 or float(prev.get("flatness", 0.0)) > 0.03:
                break
            start_index -= 1
        while end_index + 1 < len(frames):
            nxt = frames[end_index + 1]
            if float(nxt["rmsDb"]) < -50.0 and float(nxt.get("flatness", 0.0)) > 0.002:
                break
            if float(nxt.get("flatness", 0.0)) > 0.04 and float(nxt.get("crestDb", 0.0)) > 11.5:
                break
            end_index += 1

        segment_frames = frames[start_index : end_index + 1]
        segment_duration = float(segment_frames[-1]["end"]) - float(segment_frames[0]["start"])
        if segment_duration < 10.0:
            index += 1
            continue
        mean_rms = _mean_rms_db(segment_frames)
        mean_flatness = _mean_frame_value(segment_frames, "flatness")
        mean_crest = _mean_frame_value(segment_frames, "crestDb")
        mean_centroid = _mean_frame_value(segment_frames, "centroid")
        mean_high_ratio = _mean_frame_value(segment_frames, "highRatio")
        if mean_flatness > 0.015 or mean_crest > 10.5:
            index = end_index + 1
            continue

        score = 75
        reasons = [f"检测到 {segment_duration:.1f} 秒持续合成伪影"]
        if mean_flatness <= 0.006:
            score += 10
            reasons.append(f"谱平坦度异常低 {mean_flatness:.3f}")
        if mean_crest <= 9.0:
            score += 10
            reasons.append(f"峰均比较低 {mean_crest:.1f}dB")
        if segment_duration >= 15.0:
            score += 10
            reasons.append(f"异常段持续 {segment_duration:.1f} 秒")
        segment = {
            "start": round(max(0.0, float(segment_frames[0]["start"]) - 0.08), 2),
            "end": round(min(duration, float(segment_frames[-1]["end"]) + 0.12), 2),
            "duration": round(segment_duration, 2),
            "type": "sustained_tts_artifact",
            "confidence": round(min(0.99, max(0.3, score / 100)), 2),
            "score": int(min(100, score)),
            "status": "abnormal",
            "features": {
                "mean_rms_db": round(mean_rms, 1),
                "mean_flatness": round(mean_flatness, 4),
                "mean_crest_db": round(mean_crest, 1),
                "mean_centroid_hz": round(mean_centroid, 1),
                "mean_high_ratio": round(mean_high_ratio, 3),
            },
            "reasons": reasons,
        }
        if not any(_segments_overlap(segment, item) for item in results):
            results.append(segment)
        index = end_index + 1
    return results


def _find_short_tts_artifact_segments(duration: float, frames: list[dict]) -> list[dict]:
    if duration <= 0 or len(frames) < 35:
        return []
    results: list[dict] = []
    window_size = 24
    index = 0
    while index <= len(frames) - window_size:
        window = frames[index : index + window_size]
        mean_rms = _mean_rms_db(window)
        mean_crest = _mean_frame_value(window, "crestDb")
        mean_flatness = _mean_frame_value(window, "flatness")
        mean_high_ratio = _mean_frame_value(window, "highRatio")
        if not (mean_rms >= -20.0 and mean_crest <= 9.8 and mean_flatness <= 0.05 and mean_high_ratio <= 0.06):
            index += 1
            continue

        start_index = index
        end_index = index + window_size - 1
        while start_index > 0 and index - start_index < 18:
            prev = frames[start_index - 1]
            if float(prev["rmsDb"]) < -58.0 or float(prev.get("flatness", 0.0)) > 0.09:
                break
            start_index -= 1
        while end_index + 1 < len(frames) and end_index - index < 34:
            nxt = frames[end_index + 1]
            if float(nxt["rmsDb"]) < -45.0 or float(nxt.get("flatness", 0.0)) > 0.09:
                break
            end_index += 1

        segment_frames = frames[start_index : end_index + 1]
        segment_duration = float(segment_frames[-1]["end"]) - float(segment_frames[0]["start"])
        if segment_duration < 2.2 or segment_duration > 7.0:
            index += 1
            continue
        mean_rms = _mean_rms_db(segment_frames)
        mean_crest = _mean_frame_value(segment_frames, "crestDb")
        mean_flatness = _mean_frame_value(segment_frames, "flatness")
        mean_centroid = _mean_frame_value(segment_frames, "centroid")
        mean_high_ratio = _mean_frame_value(segment_frames, "highRatio")
        previous_frames = frames[max(0, start_index - 8) : start_index]
        next_frames = frames[end_index + 1 : min(len(frames), end_index + 9)]
        previous_speech_like = any(float(item["rmsDb"]) >= -28.0 and float(item.get("crestDb", 0.0)) >= 10.0 for item in previous_frames)
        next_speech_like = any(float(item["rmsDb"]) >= -28.0 and float(item.get("crestDb", 0.0)) >= 10.0 for item in next_frames)
        if not previous_speech_like or not next_speech_like:
            index += 1
            continue
        score = 70
        reasons = [f"检测到 {segment_duration:.1f} 秒短持续合成伪影"]
        if mean_crest <= 9.2:
            score += 10
            reasons.append(f"峰均比较低 {mean_crest:.1f}dB")
        if mean_high_ratio <= 0.04:
            score += 10
            reasons.append(f"高频能量占比较低 {mean_high_ratio:.2f}")
        if mean_flatness <= 0.035:
            score += 10
            reasons.append(f"谱平坦度偏低 {mean_flatness:.3f}")
        segment = {
            "start": round(max(0.0, float(segment_frames[0]["start"]) - 0.08), 2),
            "end": round(min(duration, float(segment_frames[-1]["end"]) + 0.12), 2),
            "duration": round(segment_duration, 2),
            "type": "short_tts_artifact",
            "confidence": round(min(0.99, max(0.3, score / 100)), 2),
            "score": int(min(100, score)),
            "status": "abnormal",
            "features": {
                "mean_rms_db": round(mean_rms, 1),
                "mean_flatness": round(mean_flatness, 4),
                "mean_crest_db": round(mean_crest, 1),
                "mean_centroid_hz": round(mean_centroid, 1),
                "mean_high_ratio": round(mean_high_ratio, 3),
            },
            "reasons": reasons,
        }
        if not any(_segments_overlap(segment, item) for item in results):
            results.append(segment)
        index = end_index + 1
    return results


def detect_line_audio_task_noise(task_id: int, *, sensitivity: str = "balanced") -> tuple[bool, str, dict]:
    """检测台词音频中较确定的尾部噪声和持续非语音伪影。"""
    ok, msg, audio_path = _get_completed_line_audio_path(task_id)
    if not ok or audio_path is None:
        return False, msg, {}
    requested_mode = str(sensitivity or "balanced")
    detection_mode = requested_mode if requested_mode in {"strict", "aggressive"} else "balanced"
    line_text = _get_line_audio_task_text(task_id)

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(audio_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-sample_fmt",
                "s16",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
        )
        duration, frames = _read_wav_rms_frames(tmp_path)
        vad_provider, speech_segments, vad_message = _detect_silero_onnx_speech_segments(tmp_path)
        segments: list[dict] = []
        if detection_mode in {"strict", "aggressive"}:
            segments.extend(_find_line_audio_quality_issue_segments(line_text, duration))
        for artifact_segment in _find_low_zcr_artifact_segments(duration, frames):
            if not any(_segments_overlap(artifact_segment, item) for item in segments):
                segments.append(artifact_segment)
        for spectral_segment in _find_high_frequency_noise_segments(duration, frames):
            if not any(_segments_overlap(spectral_segment, item) for item in segments):
                segments.append(spectral_segment)
        for bed_segment in _find_low_level_noise_bed_segments(duration, frames):
            if not any(_segments_overlap(bed_segment, item) for item in segments):
                segments.append(bed_segment)
        if detection_mode in {"strict", "aggressive"} and vad_provider == "silero_onnx":
            for vad_segment in _find_vad_non_speech_noise_segments(duration, frames, speech_segments):
                if not any(_segments_overlap(vad_segment, item) for item in segments):
                    segments.append(vad_segment)
        if detection_mode in {"strict", "aggressive"}:
            for sensitive_segment in _find_sensitive_low_level_noise_segments(duration, frames):
                if not any(_segments_overlap(sensitive_segment, item) for item in segments):
                    segments.append(sensitive_segment)
            for rising_segment in _find_mid_gap_rising_noise_segments(duration, frames):
                if not any(_segments_overlap(rising_segment, item) for item in segments):
                    segments.append(rising_segment)
        if detection_mode == "aggressive":
            for aggressive_segment in _find_aggressive_quality_segments(duration, frames):
                if not any(_segments_overlap(aggressive_segment, item) for item in segments):
                    segments.append(aggressive_segment)
        for hum_segment in _find_low_frequency_hum_gap_segments(duration, frames):
            if not any(_segments_overlap(hum_segment, item) for item in segments):
                segments.append(hum_segment)
        for residual_segment in _find_low_level_residual_gap_segments(duration, frames):
            if not any(_segments_overlap(residual_segment, item) for item in segments):
                segments.append(residual_segment)
        for tonal_segment in _find_rising_tonal_artifact_segments(duration, frames):
            if not any(_segments_overlap(tonal_segment, item) for item in segments):
                segments.append(tonal_segment)
        for tonal_segment in _find_sustained_tonal_noise_segments(duration, frames):
            if not any(_segments_overlap(tonal_segment, item) for item in segments):
                segments.append(tonal_segment)
        if detection_mode in {"strict", "aggressive"} and vad_provider == "silero_onnx":
            for activity_segment in _find_sustained_non_speech_activity_segments(line_text, duration, frames, speech_segments):
                if not any(_segments_overlap(activity_segment, item) for item in segments):
                    segments.append(activity_segment)
        for artifact_segment in _find_mid_gap_tts_artifact_segments(duration, frames):
            if not any(_segments_overlap(artifact_segment, item) for item in segments):
                segments.append(artifact_segment)
        for tail_noise_segment in _find_short_trailing_noise_segments(duration, frames):
            if not any(_segments_overlap(tail_noise_segment, item) for item in segments):
                segments.append(tail_noise_segment)
        for tail_noise_segment in _find_post_speech_tail_noise_segments(duration, frames):
            if not any(_segments_overlap(tail_noise_segment, item) for item in segments):
                segments.append(tail_noise_segment)
        for artifact_segment in _find_sustained_tts_artifact_segments(duration, frames):
            if not any(_segments_overlap(artifact_segment, item) for item in segments):
                segments.append(artifact_segment)
        for artifact_segment in _find_short_tts_artifact_segments(duration, frames):
            if not any(_segments_overlap(artifact_segment, item) for item in segments):
                segments.append(artifact_segment)
        tail_segment = _find_tail_noise_segment(duration, frames)
        if tail_segment and not any(_segments_overlap(tail_segment, item) for item in segments):
            segments.append(tail_segment)
        segments = _filter_segments_with_voice_activity(
            segments,
            frames,
            sensitivity=detection_mode,
            speech_segments=speech_segments if vad_provider == "silero_onnx" else None,
        )
        _apply_line_audio_noise_classifier(segments, frames)
        segments.sort(key=lambda item: (float(item.get("start", 0.0)), float(item.get("end", 0.0))))
        score = max((int(item.get("score", 0)) for item in segments), default=0)
        status = "normal"
        if any(str(item.get("status") or "") == "abnormal" for item in segments):
            status = "abnormal"
        elif segments:
            status = "suspicious"
        return True, "ok", {
            "durationSeconds": round(duration, 1),
            "status": status,
            "score": score,
            "detectionMode": detection_mode,
            "vadProvider": vad_provider,
            "vadMessage": vad_message,
            "segments": segments,
        }
    except subprocess.CalledProcessError as exc:
        error_text = (exc.stderr or b"").decode("utf-8", errors="ignore").strip()
        return False, error_text or "ffmpeg 噪音检测失败", {}
    except Exception as exc:
        return False, f"噪音检测失败: {exc}", {}
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def analyze_line_audio_task_loudness(
    task_id: int, *, target_lufs: float = -20.0
) -> tuple[bool, str, dict]:
    """分析台词音频响度，并给出匹配目标 LUFS 的建议增益。"""
    ok, msg, audio_path = _get_completed_line_audio_path(task_id)
    if not ok or audio_path is None:
        return False, msg, {}
    target = float(target_lufs or -20.0)
    if not math.isfinite(target):
        return False, "无效的目标 LUFS", {}
    target = max(-35.0, min(target, -12.0))

    try:
        loudnorm_proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                str(audio_path),
                "-af",
                f"loudnorm=I={target:.1f}:TP=-1.5:LRA=11:print_format=json",
                "-f",
                "null",
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        peak_proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                str(audio_path),
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return False, f"ffmpeg 响度分析失败: {exc}", {}

    loudnorm_text = "\n".join([loudnorm_proc.stdout or "", loudnorm_proc.stderr or ""])
    json_match = re.search(r"\{\s*\"input_i\".*?\}\s*", loudnorm_text, re.S)
    if not json_match:
        return False, "无法读取音频响度", {}
    try:
        loudness = json.loads(json_match.group(0))
        input_lufs = float(loudness.get("input_i"))
        true_peak = float(loudness.get("input_tp"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False, "无法解析音频响度", {}
    if not math.isfinite(input_lufs) or not math.isfinite(true_peak):
        return False, "无法解析音频响度", {}

    peak_text = "\n".join([peak_proc.stdout or "", peak_proc.stderr or ""])
    peak_match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", peak_text)
    peak_dbfs = float(peak_match.group(1)) if peak_match else true_peak

    raw_gain = target - input_lufs
    max_safe_gain = -1.0 - peak_dbfs
    suggested_gain = max(-20.0, min(12.0, raw_gain, max_safe_gain))
    return True, "analyzed", {
        "targetLufs": round(target, 1),
        "inputLufs": round(input_lufs, 1),
        "truePeakDb": round(true_peak, 1),
        "peakDbfs": round(peak_dbfs, 1),
        "suggestedGainDb": round(suggested_gain, 1),
        "rawGainDb": round(raw_gain, 1),
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
