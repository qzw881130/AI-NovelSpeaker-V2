from __future__ import annotations

import json
import ipaddress
import hashlib
import mimetypes
import re
import socket
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from copy import deepcopy
from http.client import IncompleteRead, RemoteDisconnected
from urllib import request
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse

from .app_context import (
    DB_PATH,
    DEFAULT_SYSTEM_PROMPT_CONTENT,
    NOVEL_DIR,
    PROMPTS_DIR,
    ROOT_DIR,
    PROMPTS_DIR,
    SYSTEM_PROMPT_DESC,
    SYSTEM_PROMPT_FILE,
    SYSTEM_PROMPT_NAME,
    SYSTEM_PROMPTS,
    SYSTEM_WORKFLOWS,
    WORKFLOWS_DIR,
    db_conn,
)


CAPTURE_SERVER: ThreadingHTTPServer | None = None
CAPTURE_THREAD: threading.Thread | None = None
CAPTURE_BIND: tuple[str, int] | None = None
CAPTURE_LOCK = threading.Lock()
TASK_WORKER_THREAD: threading.Thread | None = None
TASK_WORKER_STOP = threading.Event()
TASK_WORKER_LOCK = threading.Lock()
TASK_WORKER_HEARTBEAT_TS = 0.0
TASK_WORKER_LAST_PROGRESS_TS = 0.0
TASK_WORKER_GENERATION = 0
TASK_WORKER_KICK_THREAD: threading.Thread | None = None
TASK_WORKER_STALE_SECONDS = 90.0
LINE_AUDIO_WORKER_THREAD: threading.Thread | None = None
LINE_AUDIO_WORKER_STOP = threading.Event()
LINE_AUDIO_WORKER_HEARTBEAT_TS = 0.0
LINE_AUDIO_WORKER_LAST_PROGRESS_TS = 0.0
LINE_AUDIO_WORKER_GENERATION = 0
LINE_AUDIO_WORKER_STALE_SECONDS = 90.0
AUDIO_ASR_WORKER_THREAD: threading.Thread | None = None
AUDIO_ASR_WORKER_STOP = threading.Event()
AUDIO_ASR_WORKER_HEARTBEAT_TS = 0.0
AUDIO_ASR_WORKER_LAST_PROGRESS_TS = 0.0
AUDIO_ASR_WORKER_GENERATION = 0
AUDIO_ASR_WORKER_STALE_SECONDS = 90.0
NSFW_REVIEW_WORKER_THREAD: threading.Thread | None = None
NSFW_REVIEW_WORKER_STOP = threading.Event()
NSFW_REVIEW_WORKER_HEARTBEAT_TS = 0.0
NSFW_REVIEW_WORKER_LAST_PROGRESS_TS = 0.0
NSFW_REVIEW_WORKER_GENERATION = 0
NSFW_REVIEW_WORKER_STALE_SECONDS = 90.0
DURATION_CACHE_LOCK = threading.Lock()
DURATION_CACHE_PENDING: set[int] = set()
JSON_LLM_THROTTLE_LOCK = threading.Lock()
JSON_LLM_LAST_REQUEST_TS = 0.0
JSON_LLM_MIN_INTERVAL_SECONDS = 3.0
LEGACY_SYSTEM_WORKFLOW_NAME = "古典小说默认工作流"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def db_rel_path(value: Path | str) -> str:
    return Path(value).as_posix()


def dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def touch_task_worker_heartbeat(*, made_progress: bool = False) -> None:
    global TASK_WORKER_HEARTBEAT_TS, TASK_WORKER_LAST_PROGRESS_TS
    now = time.time()
    TASK_WORKER_HEARTBEAT_TS = now
    if made_progress:
        TASK_WORKER_LAST_PROGRESS_TS = now


def touch_line_audio_worker_heartbeat(*, made_progress: bool = False) -> None:
    global LINE_AUDIO_WORKER_HEARTBEAT_TS, LINE_AUDIO_WORKER_LAST_PROGRESS_TS
    now = time.time()
    LINE_AUDIO_WORKER_HEARTBEAT_TS = now
    if made_progress:
        LINE_AUDIO_WORKER_LAST_PROGRESS_TS = now


def touch_audio_asr_worker_heartbeat(*, made_progress: bool = False) -> None:
    global AUDIO_ASR_WORKER_HEARTBEAT_TS, AUDIO_ASR_WORKER_LAST_PROGRESS_TS
    now = time.time()
    AUDIO_ASR_WORKER_HEARTBEAT_TS = now
    if made_progress:
        AUDIO_ASR_WORKER_LAST_PROGRESS_TS = now


def touch_nsfw_review_worker_heartbeat(*, made_progress: bool = False) -> None:
    global NSFW_REVIEW_WORKER_HEARTBEAT_TS, NSFW_REVIEW_WORKER_LAST_PROGRESS_TS
    now = time.time()
    NSFW_REVIEW_WORKER_HEARTBEAT_TS = now
    if made_progress:
        NSFW_REVIEW_WORKER_LAST_PROGRESS_TS = now


def probe_audio_duration_seconds(file_path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return max(0.0, float(str(result.stdout or "0").strip() or 0.0))
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return 0.0


def file_md5_hex(file_path: Path) -> str:
    hasher = hashlib.md5()
    try:
        with file_path.open("rb") as fp:
            while True:
                chunk = fp.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        return ""
    return hasher.hexdigest()


def update_chapter_audio_duration_cache(
    conn: sqlite3.Connection, chapter_id: int, audio_path: Path | None
) -> float:
    if audio_path is None or not audio_path.exists() or not audio_path.is_file():
        conn.execute(
            "UPDATE chapters SET audio_duration_seconds=0, audio_duration_md5='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (chapter_id,),
        )
        return 0.0

    md5_hex = file_md5_hex(audio_path)
    row = conn.execute(
        "SELECT audio_duration_seconds, audio_duration_md5 FROM chapters WHERE id=?",
        (chapter_id,),
    ).fetchone()
    if row and str(row["audio_duration_md5"] or "") == md5_hex:
        return float(row["audio_duration_seconds"] or 0)

    duration = probe_audio_duration_seconds(audio_path)
    conn.execute(
        "UPDATE chapters SET audio_duration_seconds=?, audio_duration_md5=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (duration, md5_hex, chapter_id),
    )
    return duration


def calculate_novel_total_audio_duration_seconds(
    conn: sqlite3.Connection, novel_id: int
) -> float:
    rows = conn.execute(
        "SELECT id, audio_file_path, audio_duration_seconds, audio_duration_md5 FROM chapters WHERE novel_id=?",
        (novel_id,),
    ).fetchall()
    total = 0.0
    for row in rows:
        raw_path = str(row["audio_file_path"] or "").strip()
        if not raw_path:
            continue
        abs_path = (ROOT_DIR / raw_path).resolve()
        if not abs_path.exists() or not abs_path.is_file():
            continue
        md5_hex = file_md5_hex(abs_path)
        if md5_hex and str(row["audio_duration_md5"] or "") == md5_hex:
            total += float(row["audio_duration_seconds"] or 0)
            continue
        total += update_chapter_audio_duration_cache(conn, int(row["id"]), abs_path)
    return total


def update_novel_total_audio_duration_seconds(
    conn: sqlite3.Connection, novel_id: int
) -> float:
    total = calculate_novel_total_audio_duration_seconds(conn, novel_id)
    conn.execute(
        "UPDATE novels SET total_audio_duration_seconds=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (total, novel_id),
    )
    return total


def update_chapter_non_ver_audio_duration_cache(
    conn: sqlite3.Connection, chapter_id: int, audio_path: Path | None
) -> float:
    if audio_path is None:
        conn.execute(
            "UPDATE chapters SET non_ver_audio_duration_seconds=0, non_ver_audio_duration_md5='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (chapter_id,),
        )
        return 0.0

    md5_hex = file_md5_hex(audio_path)
    row = conn.execute(
        "SELECT non_ver_audio_duration_seconds, non_ver_audio_duration_md5 FROM chapters WHERE id=?",
        (chapter_id,),
    ).fetchone()
    if row and str(row["non_ver_audio_duration_md5"] or "") == md5_hex:
        return float(row["non_ver_audio_duration_seconds"] or 0)

    duration = probe_audio_duration_seconds(audio_path)
    conn.execute(
        "UPDATE chapters SET non_ver_audio_duration_seconds=?, non_ver_audio_duration_md5=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (duration, md5_hex, chapter_id),
    )
    return duration


def calculate_novel_total_non_ver_audio_duration_seconds(
    conn: sqlite3.Connection, novel_id: int
) -> float:
    rows = conn.execute(
        "SELECT c.id, c.chapter_num, c.non_ver_audio_duration_seconds, c.non_ver_audio_duration_md5, n.english_dir FROM chapters c JOIN novels n ON n.id=c.novel_id WHERE c.novel_id=?",
        (novel_id,),
    ).fetchall()
    total = 0.0
    for row in rows:
        abs_path = NOVEL_DIR / str(row["english_dir"] or "") / "audio_non_ver" / f"chapter-{int(row['chapter_num'] or 0):03d}-merged.flac"
        if not abs_path.exists() or not abs_path.is_file():
            continue
        md5_hex = file_md5_hex(abs_path)
        if md5_hex and str(row["non_ver_audio_duration_md5"] or "") == md5_hex:
            total += float(row["non_ver_audio_duration_seconds"] or 0)
            continue
        total += update_chapter_non_ver_audio_duration_cache(conn, int(row["id"]), abs_path)
    return total


def update_novel_total_non_ver_audio_duration_seconds(
    conn: sqlite3.Connection, novel_id: int
) -> float:
    total = calculate_novel_total_non_ver_audio_duration_seconds(conn, novel_id)
    conn.execute(
        "UPDATE novels SET total_audio_non_ver_duration_seconds=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (total, novel_id),
    )
    return total


def refresh_novel_audio_duration_cache_async(
    novel_id: int,
    chapter_id: int | None = None,
    audio_rel_path: str | None = None,
) -> bool:
    with DURATION_CACHE_LOCK:
        if novel_id in DURATION_CACHE_PENDING:
            return False
        DURATION_CACHE_PENDING.add(novel_id)

    def _runner() -> None:
        try:
            conn = db_conn()
            try:
                if chapter_id is not None:
                    abs_audio = None
                    raw_path = str(audio_rel_path or "").strip()
                    if raw_path:
                        abs_audio = (ROOT_DIR / raw_path).resolve()
                    update_chapter_audio_duration_cache(conn, chapter_id, abs_audio)
                update_novel_total_audio_duration_seconds(conn, novel_id)
                update_novel_total_non_ver_audio_duration_seconds(conn, novel_id)
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            print(f"[audio-duration] refresh failed for novel {novel_id}: {exc}")
        finally:
            with DURATION_CACHE_LOCK:
                DURATION_CACHE_PENDING.discard(novel_id)

    threading.Thread(target=_runner, daemon=True).start()
    return True


def ensure_novel_dirs(english_dir: str) -> None:
    (NOVEL_DIR / english_dir / "text").mkdir(parents=True, exist_ok=True)
    (NOVEL_DIR / english_dir / "audio").mkdir(parents=True, exist_ok=True)
    (NOVEL_DIR / english_dir / "audio_non_ver").mkdir(parents=True, exist_ok=True)
    (NOVEL_DIR / english_dir / "asr").mkdir(parents=True, exist_ok=True)


def validate_english_dir(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_]{1,25}", value or ""))


def fetch_prompts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id,name,prompt_type,prompt_category,description,content,created_at,updated_at
        FROM json_prompts
        ORDER BY CASE WHEN prompt_type='system' THEN 0 ELSE 1 END, id DESC
        """
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "type": str(r["prompt_type"]),
            "category": str(r["prompt_category"] or "json_parse"),
            "name": str(r["name"]),
            "description": str(r["description"] or ""),
            "content": str(r["content"]),
            "createdAt": str(r["created_at"]),
            "updatedAt": str(r["updated_at"]),
        }
        for r in rows
    ]


def load_system_prompt_content() -> str:
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    if not SYSTEM_PROMPT_FILE.exists():
        SYSTEM_PROMPT_FILE.write_text(DEFAULT_SYSTEM_PROMPT_CONTENT, encoding="utf-8")
        return DEFAULT_SYSTEM_PROMPT_CONTENT
    text = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8", errors="ignore").strip()
    return text or DEFAULT_SYSTEM_PROMPT_CONTENT


def load_system_prompt_content_from_file(file_path: Path, default_content: str) -> str:
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    if not file_path.exists():
        file_path.write_text(default_content, encoding="utf-8")
        return default_content
    text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
    return text or default_content


def sync_system_prompts_from_files(conn: sqlite3.Connection) -> None:
    for prompt in SYSTEM_PROMPTS:
        file_path = Path(prompt["file"])
        prompt_name = str(prompt["name"])
        prompt_desc = str(prompt["description"])
        prompt_category = str(prompt.get("category") or "json_parse")
        default_content = str(prompt["default_content"])
        legacy_names = [str(name) for name in prompt.get("legacy_names", [])]
        content = load_system_prompt_content_from_file(file_path, default_content)

        current_row = conn.execute(
            "SELECT id FROM json_prompts WHERE name=?",
            (prompt_name,),
        ).fetchone()
        for legacy in legacy_names:
            if legacy == prompt_name:
                continue
            legacy_row = conn.execute(
                "SELECT id FROM json_prompts WHERE name=?",
                (legacy,),
            ).fetchone()
            if not legacy_row:
                continue
            legacy_id = legacy_row["id"]
            if current_row:
                current_id = current_row["id"]
                conn.execute(
                    "UPDATE novels SET prompt_id=? WHERE prompt_id=?",
                    (current_id, legacy_id),
                )
                conn.execute(
                    "UPDATE novels SET nsfw_prompt_id=? WHERE nsfw_prompt_id=?",
                    (current_id, legacy_id),
                )
                conn.execute("DELETE FROM json_prompts WHERE id=?", (legacy_id,))
            else:
                conn.execute(
                    "UPDATE json_prompts SET name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (prompt_name, legacy_id),
                )
                current_row = conn.execute(
                    "SELECT id FROM json_prompts WHERE name=?",
                    (prompt_name,),
                ).fetchone()

        conn.execute(
            """
            INSERT INTO json_prompts (name,prompt_type,prompt_category,description,content)
            VALUES (?, 'system', ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                prompt_type='system',
                prompt_category=excluded.prompt_category,
                description=excluded.description,
                content=excluded.content,
                updated_at=CURRENT_TIMESTAMP
            WHERE json_prompts.prompt_type<>'system'
               OR json_prompts.prompt_category<>excluded.prompt_category
               OR json_prompts.description<>excluded.description
               OR json_prompts.content<>excluded.content
            """,
            (prompt_name, prompt_category, prompt_desc, content),
        )


def sync_system_prompt_from_file(conn: sqlite3.Connection) -> None:
    sync_system_prompts_from_files(conn)


def next_prompt_copy_name(conn: sqlite3.Connection, src_name: str) -> str:
    base = f"{src_name}-副本"
    exists = conn.execute("SELECT 1 FROM json_prompts WHERE name=?", (base,)).fetchone()
    if not exists:
        return base
    idx = 2
    while True:
        candidate = f"{base}{idx}"
        exists = conn.execute(
            "SELECT 1 FROM json_prompts WHERE name=?", (candidate,)
        ).fetchone()
        if not exists:
            return candidate
        idx += 1


def fetch_workflows(conn: sqlite3.Connection) -> list[dict]:
    system_workflow_names = {str(item["name"]) for item in SYSTEM_WORKFLOWS}
    rows = conn.execute(
        """
        SELECT id,name,workflow_type,description,json_text,workflow_io_config,workflow_log_enabled,created_at,updated_at
        FROM comfy_workflows
        ORDER BY CASE WHEN workflow_type='system' OR workflow_type='voice_transcribe' OR workflow_type='line_audio' OR workflow_type='voice_sample' OR workflow_type='audio_asr' THEN 0 ELSE 1 END, id DESC
        """
    ).fetchall()

    def parse_workflow_io_config(raw: str) -> dict:
        try:
            parsed = json.loads(str(raw or "{}") or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    return [
        {
            "id": int(r["id"]),
            "type": "system" if str(r["name"]) in system_workflow_names else "user",
            "workflowType": str(r["workflow_type"]),
            "name": str(r["name"]),
            "description": str(r["description"] or ""),
            "jsonText": str(r["json_text"]),
            "workflowIoConfig": parse_workflow_io_config(
                str(r["workflow_io_config"] or "{}")
            ),
            "workflowLogEnabled": bool(int(r["workflow_log_enabled"] or 0)),
            "createdAt": str(r["created_at"]),
            "updatedAt": str(r["updated_at"]),
        }
        for r in rows
    ]


def create_workflow_log(
    workflow_category: str,
    workflow_name: str,
    workflow_json: dict | str,
    error_log: str = "",
) -> int:
    conn = db_conn()
    cur = conn.execute(
        """
        INSERT INTO comfy_workflow_logs (workflow_category, workflow_name, workflow_json, error_log)
        VALUES (?, ?, ?, ?)
        """,
        (
            str(workflow_category or "").strip(),
            str(workflow_name or "").strip(),
            workflow_json
            if isinstance(workflow_json, str)
            else json.dumps(workflow_json or {}, ensure_ascii=False, indent=2),
            str(error_log or "").strip(),
        ),
    )
    conn.commit()
    log_id = int(cur.lastrowid or 0)
    conn.close()
    return log_id


def update_workflow_log_error(log_id: int, error_log: str) -> None:
    if int(log_id or 0) <= 0:
        return
    conn = db_conn()
    conn.execute(
        "UPDATE comfy_workflow_logs SET error_log=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (str(error_log or "").strip(), int(log_id)),
    )
    conn.commit()
    conn.close()


def update_workflow_log_json(log_id: int, workflow_json: dict | str) -> None:
    if int(log_id or 0) <= 0:
        return
    conn = db_conn()
    conn.execute(
        "UPDATE comfy_workflow_logs SET workflow_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (
            workflow_json
            if isinstance(workflow_json, str)
            else json.dumps(workflow_json or {}, ensure_ascii=False, indent=2),
            int(log_id),
        ),
    )
    conn.commit()
    conn.close()


WORKFLOW_WIDGET_KEY_FALLBACKS: dict[str, list[str]] = {
    "CR Prompt Text": ["prompt"],
    "Qwen3ASRLoader": ["model_name"],
    "Qwen3ASRTranscribe": ["language"],
    "Qwen3ForcedAlignerLoader": ["model_name"],
    "Qwen3ForcedAlign": ["language", "segment_by_sentence"],
    "FB_Qwen3TTSVoiceClone": [
        "voice_clone_prompt",
        "model_choice",
        "device",
        "precision",
        "language",
        "config",
        "seed",
        "control_after_generate",
        "max_new_tokens",
        "top_p",
        "top_k",
        "temperature",
        "repetition_penalty",
        "x_vector_only",
        "attention",
        "unload_model_after_generate",
        "custom_model_path",
    ],
}


def workflow_json_to_prompt_json(workflow: dict) -> dict:
    if not isinstance(workflow, dict):
        return {}
    if workflow and all(str(key).isdigit() for key in workflow.keys()):
        return deepcopy(workflow)
    nodes = workflow.get("nodes")
    links = workflow.get("links")
    if not isinstance(nodes, list) or not isinstance(links, list):
        return deepcopy(workflow)

    link_map: dict[int, list] = {}
    for item in links:
        if isinstance(item, list) and len(item) >= 4:
            try:
                link_map[int(item[0])] = item
            except Exception:
                continue

    prompt: dict[str, dict] = {}
    for node in nodes:
        if not isinstance(node, dict) or node.get("id") is None:
            continue
        node_id = str(node.get("id"))
        properties = node.get("properties") or {}
        class_type = str(
            properties.get("Node name for S&R")
            or node.get("type")
            or node.get("class_type")
            or ""
        ).strip()
        prompt_node = {
            "inputs": {},
            "class_type": class_type,
            "_meta": {"title": str(node.get("title") or "")},
        }

        widget_keys = []
        for input_item in node.get("inputs") or []:
            if not isinstance(input_item, dict):
                continue
            widget = input_item.get("widget") or {}
            widget_name = str(widget.get("name") or "").strip()
            if widget_name:
                widget_keys.append(widget_name)

        fallback_widget_keys = WORKFLOW_WIDGET_KEY_FALLBACKS.get(class_type)
        if fallback_widget_keys:
            widget_keys = fallback_widget_keys
        elif not widget_keys:
            widget_keys = []

        for key, value in zip(widget_keys, list(node.get("widgets_values") or [])):
            prompt_node["inputs"][key] = value

        for input_item in node.get("inputs") or []:
            if not isinstance(input_item, dict):
                continue
            link_id = input_item.get("link")
            if link_id is None:
                continue
            link_info = link_map.get(int(link_id))
            if not link_info:
                continue
            input_name = str(input_item.get("name") or "").strip()
            if not input_name:
                continue
            prompt_node["inputs"][input_name] = [str(link_info[1]), int(link_info[2])]

        prompt[node_id] = prompt_node

    return prompt


def list_workflow_logs(conn: sqlite3.Connection, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        "SELECT id, workflow_category, workflow_name, workflow_json, error_log, created_at, updated_at FROM comfy_workflow_logs ORDER BY id DESC LIMIT ?",
        (max(1, min(int(limit or 500), 2000)),),
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "workflowCategory": str(r["workflow_category"] or ""),
            "workflowName": str(r["workflow_name"] or ""),
            "workflowJson": str(r["workflow_json"] or ""),
            "errorLog": str(r["error_log"] or ""),
            "createdAt": str(r["created_at"] or ""),
            "updatedAt": str(r["updated_at"] or ""),
        }
        for r in rows
    ]


def clear_workflow_logs(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM comfy_workflow_logs")


def load_system_workflow_file(file_path: Path) -> str:
    """加载系统工作流JSON文件内容"""
    if not file_path.exists():
        return "{}"
    text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return "{}"
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return "{}"
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return "{}"


def migrate_workflow_type_constraint(conn: sqlite3.Connection) -> None:
    """迁移：移除 workflow_type 的 CHECK 约束，以支持新的工作流类型"""
    # 检查是否存在 CHECK 约束（通过尝试插入一个无效值来测试）
    try:
        conn.execute(
            "INSERT INTO comfy_workflows (name, workflow_type, description, json_text) VALUES ('__test__', 'test_type', '', '')"
        )
        conn.execute("DELETE FROM comfy_workflows WHERE name = '__test__'")
        # 如果插入成功，说明没有 CHECK 约束，无需迁移
        return
    except sqlite3.IntegrityError:
        # CHECK 约束存在，需要迁移
        pass

    # 禁用外键约束
    conn.execute("PRAGMA foreign_keys = OFF")

    try:
        # 创建新表（不包含 CHECK 约束）
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS comfy_workflows_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                workflow_type TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                json_text TEXT NOT NULL,
                workflow_io_config TEXT NOT NULL DEFAULT '{}',
                workflow_log_enabled INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # 复制数据
        conn.execute(
            """
            INSERT INTO comfy_workflows_new (id, name, workflow_type, description, json_text, workflow_io_config, workflow_log_enabled, created_at, updated_at)
            SELECT id, name, workflow_type, description, json_text, '{}', 1, created_at, updated_at
            FROM comfy_workflows
            """
        )

        # 删除旧表
        conn.execute("DROP TABLE comfy_workflows")

        # 重命名新表
        conn.execute("ALTER TABLE comfy_workflows_new RENAME TO comfy_workflows")

        conn.commit()
    finally:
        # 重新启用外键约束
        conn.execute("PRAGMA foreign_keys = ON")


def sync_system_workflow_from_file(conn: sqlite3.Connection) -> None:
    """同步所有系统工作流到数据库"""
    # 先执行迁移
    migrate_workflow_type_constraint(conn)

    # 删除旧版单系统工作流记录
    # 先解除外键引用（将 novels 表中引用该工作流的记录设为 NULL）
    conn.execute(
        "UPDATE novels SET workflow_id=NULL WHERE workflow_id IN (SELECT id FROM comfy_workflows WHERE name=?)",
        (LEGACY_SYSTEM_WORKFLOW_NAME,),
    )
    conn.execute(
        "DELETE FROM comfy_workflows WHERE name=?", (LEGACY_SYSTEM_WORKFLOW_NAME,)
    )

    # 同步系统工作流（voice_sample, voice_transcribe, line_audio）
    for wf_config in SYSTEM_WORKFLOWS:
        json_text = load_system_workflow_file(wf_config["file"])
        conn.execute(
            """
            INSERT INTO comfy_workflows (name,workflow_type,description,json_text,workflow_io_config,workflow_log_enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                workflow_type=excluded.workflow_type,
                description=excluded.description,
                json_text=excluded.json_text,
                workflow_io_config=excluded.workflow_io_config,
                updated_at=CURRENT_TIMESTAMP
            WHERE comfy_workflows.workflow_type<>excluded.workflow_type
               OR comfy_workflows.description<>excluded.description
               OR comfy_workflows.json_text<>excluded.json_text
               OR comfy_workflows.workflow_io_config<>excluded.workflow_io_config
            """,
            (
                wf_config["name"],
                wf_config["workflow_type"],
                wf_config["description"],
                json_text,
                json.dumps(
                    wf_config.get("workflow_io_config") or {}, ensure_ascii=False
                ),
                int(wf_config.get("workflow_log_enabled", 0) or 0),
            ),
        )


def next_workflow_copy_name(conn: sqlite3.Connection, src_name: str) -> str:
    base = f"{src_name}-副本"
    exists = conn.execute(
        "SELECT 1 FROM comfy_workflows WHERE name=?", (base,)
    ).fetchone()
    if not exists:
        return base
    idx = 2
    while True:
        candidate = f"{base}{idx}"
        exists = conn.execute(
            "SELECT 1 FROM comfy_workflows WHERE name=?", (candidate,)
        ).fetchone()
        if not exists:
            return candidate
        idx += 1


def fetch_novels(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT n.id,n.name,n.author,n.english_dir,n.intro,n.chapter_count,n.total_words,
               n.prompt_id,n.nsfw_prompt_id,n.workflow_id,n.voice_sample_workflow_id,n.line_audio_workflow_id,n.voice_transcribe_workflow_id,
               n.audio_asr_workflow_id,n.total_audio_duration_seconds,n.total_audio_non_ver_duration_seconds,n.created_at,n.updated_at,
               COALESCE(SUM(CASE WHEN c.has_audio=1 THEN 1 ELSE 0 END),0) AS audio_done,
               COALESCE(COUNT(c.id),0) AS chapter_total,
               COALESCE(SUM(c.word_count),0) AS chapter_words
        FROM novels n
        LEFT JOIN chapters c ON c.novel_id=n.id
        GROUP BY n.id
        ORDER BY n.id ASC
        """
    ).fetchall()
    chapter_json_rows = conn.execute(
        """
        SELECT c.novel_id,c.chapter_num,
               (
                   SELECT t.merged_result_json
                   FROM json_tasks t
                   WHERE t.novel_id=c.novel_id AND t.chapter_num=c.chapter_num AND t.status='completed'
                   ORDER BY t.id DESC LIMIT 1
               ) AS latest_json
        FROM chapters c
        """
    ).fetchall()
    strict_json_done: dict[int, int] = {}
    for row in chapter_json_rows:
        novel_id = int(row["novel_id"])
        if json_text_ready(str(row["latest_json"] or "")):
            strict_json_done[novel_id] = strict_json_done.get(novel_id, 0) + 1
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0

    result: list[dict] = []
    for r in rows:
        chapter_total = int(r["chapter_total"] or 0)
        chapter_count = int(r["chapter_count"] or chapter_total)
        total_words = int(r["total_words"] or 0)
        if total_words <= 0:
            total_words = int(r["chapter_words"] or 0)
        base_dir = NOVEL_DIR / str(r["english_dir"]) if r["english_dir"] else NOVEL_DIR
        temp_dir = (
            ROOT_DIR / "temp" / str(r["english_dir"])
            if r["english_dir"]
            else ROOT_DIR / "temp"
        )
        txt_bytes = dir_size_bytes(base_dir / "text")
        audio_bytes = dir_size_bytes(base_dir / "audio")
        audio_non_ver_bytes = dir_size_bytes(base_dir / "audio_non_ver")
        temp_bytes = dir_size_bytes(temp_dir)
        json_progress = 0
        audio_progress = 0
        if chapter_count > 0:
            json_done = strict_json_done.get(int(r["id"]), 0)
            json_progress = int(round(100 * json_done / chapter_count))
            audio_progress = int(round(100 * int(r["audio_done"] or 0) / chapter_count))
        result.append(
            {
                "id": int(r["id"]),
                "name": str(r["name"]),
                "author": str(r["author"]),
                "englishDir": str(r["english_dir"]),
                "intro": str(r["intro"] or ""),
                "chapterCount": chapter_count,
                "totalWords": total_words,
                "promptId": int(r["prompt_id"]) if r["prompt_id"] is not None else None,
                "nsfwPromptId": int(r["nsfw_prompt_id"]) if r["nsfw_prompt_id"] is not None else None,
                "workflowId": int(r["workflow_id"])
                if r["workflow_id"] is not None
                else None,
                "voiceSampleWorkflowId": int(r["voice_sample_workflow_id"])
                if r["voice_sample_workflow_id"] is not None
                else None,
                "lineAudioWorkflowId": int(r["line_audio_workflow_id"])
                if r["line_audio_workflow_id"] is not None
                else None,
                "voiceTranscribeWorkflowId": int(r["voice_transcribe_workflow_id"])
                if r["voice_transcribe_workflow_id"] is not None
                else None,
                "audioAsrWorkflowId": int(r["audio_asr_workflow_id"])
                if r["audio_asr_workflow_id"] is not None
                else None,
                "jsonProgress": json_progress,
                "audioProgress": audio_progress,
                "totalAudioDurationSeconds": float(
                    r["total_audio_duration_seconds"] or 0
                ),
                "totalAudioNonVerDurationSeconds": float(
                    r["total_audio_non_ver_duration_seconds"] or 0
                ),
                "storage": {
                    "txtBytes": txt_bytes,
                    "audioBytes": audio_bytes,
                    "audioNonVerBytes": audio_non_ver_bytes,
                    "tempBytes": temp_bytes,
                    "dbBytes": db_size,
                },
                "createdAt": str(r["created_at"]),
                "updatedAt": str(r["updated_at"]),
            }
        )
    return result


def fetch_json_tasks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.id,t.novel_id,t.chapter_num,t.chapter_title,t.prompt_id,t.model_name,t.think_enabled,t.status,t.progress,t.updated_at,
               t.created_at,t.started_at,t.error_message,
               (SELECT COUNT(1) FROM task_batches b WHERE b.task_id=t.id) AS batch_total,
               (SELECT COUNT(1) FROM task_batches b WHERE b.task_id=t.id AND b.status='completed') AS batch_done,
               (SELECT COUNT(1) FROM task_batches b WHERE b.task_id=t.id AND b.status='failed') AS batch_failed,
               (SELECT COUNT(1) FROM task_batches b WHERE b.task_id=t.id AND b.status='cancelled') AS batch_cancelled,
               (SELECT COUNT(1) FROM task_batches b WHERE b.task_id=t.id AND b.status='timeout') AS batch_timeout,
               n.name AS novel_name,
               COALESCE(
                   c.word_count,
                   (
                       SELECT c2.word_count
                       FROM chapters c2
                       WHERE c2.novel_id=t.novel_id AND c2.chapter_num=t.chapter_num
                       ORDER BY c2.id DESC
                       LIMIT 1
                   ),
                   0
               ) AS chapter_word_count
        FROM json_tasks t
        JOIN novels n ON n.id=t.novel_id
        LEFT JOIN chapters c ON c.id=t.chapter_id
        ORDER BY t.id DESC
        """
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "novelId": int(r["novel_id"]),
            "novelName": str(r["novel_name"]),
            "chapter": int(r["chapter_num"]),
            "title": str(r["chapter_title"]),
            "promptId": int(r["prompt_id"]) if r["prompt_id"] is not None else None,
            "modelName": str(r["model_name"] or ""),
            "thinkEnabled": int(r["think_enabled"] or 0) != 0,
            "wordCount": int(r["chapter_word_count"] or 0),
            "status": str(r["status"]),
            "progress": int(r["progress"] or 0),
            "errorMessage": str(r["error_message"] or ""),
            "batchTotal": int(r["batch_total"] or 0),
            "batchDone": int(r["batch_done"] or 0),
            "batchFailed": int(r["batch_failed"] or 0),
            "batchCancelled": int(r["batch_cancelled"] or 0),
            "batchTimeout": int(r["batch_timeout"] or 0),
            "createdAt": str(r["created_at"]),
            "startedAt": str(r["started_at"] or ""),
            "updatedAt": str(r["updated_at"]),
        }
        for r in rows
    ]


def fetch_settings(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT setting_key,setting_value FROM app_settings").fetchall()
    kv = {str(r["setting_key"]): str(r["setting_value"]) for r in rows}
    comfy_url = str(kv.get("comfy_url", "") or "").strip() or "http://127.0.0.1:8188"

    try:
        batch_max_chars = int(kv.get("llm_batch_max_chars", "3500"))
    except (TypeError, ValueError):
        batch_max_chars = 3500
    if batch_max_chars not in {0, 3500, 4000, 5000, 6000, 7000, 8000, 9000, 10000}:
        batch_max_chars = 3500
    try:
        num_ctx = int(kv.get("llm_num_ctx", "65536"))
    except (TypeError, ValueError):
        num_ctx = 65536
    if num_ctx not in {32768, 65536, 98304, 131072}:
        num_ctx = 65536
    keep_alive = str(kv.get("llm_keep_alive", "30m") or "30m").strip() or "30m"
    unload_after_call = str(
        kv.get("llm_unload_after_call", "0") or "0"
    ).strip().lower() not in {"0", "false", "off", "no", ""}
    if keep_alive == "unload":
        unload_after_call = True
        keep_alive = "30m"
    if keep_alive not in {"5m", "15m", "30m", "1h", "6h", "24h"}:
        keep_alive = "30m"
    try:
        batch_timeout_minutes = int(kv.get("llm_batch_timeout_minutes", "15"))
    except (TypeError, ValueError):
        batch_timeout_minutes = 15
    if batch_timeout_minutes not in {5, 10, 15, 20, 30, 40}:
        batch_timeout_minutes = 15
    llm_think = str(kv.get("llm_think", "1") or "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }

    llm = {
        "provider": kv.get("llm_provider", "grok"),
        "baseUrl": kv.get("llm_base_url", "https://api.x.ai/v1"),
        "model": kv.get("llm_model", "grok-2-latest"),
        "apiKey": kv.get("llm_api_key", ""),
        "temperature": float(kv.get("llm_temperature", "0.3")),
        "maxTokens": int(kv.get("llm_max_tokens", "8192")),
        "numCtx": num_ctx,
        "keepAlive": keep_alive,
        "unloadAfterCall": unload_after_call,
        "batchTimeoutMinutes": batch_timeout_minutes,
        "think": llm_think,
        "batchMaxChars": batch_max_chars,
    }
    ui_language = str(kv.get("ui_language", "zh-CN") or "zh-CN").strip() or "zh-CN"
    ui_timezone = (
        str(kv.get("ui_timezone", "Asia/Shanghai") or "Asia/Shanghai").strip()
        or "Asia/Shanghai"
    )
    return {
        "comfyUrl": comfy_url,
        "proxyUrl": kv.get("proxy_url", ""),
        "llm": llm,
        "ui": {
            "language": ui_language,
            "timezone": ui_timezone,
        },
        "lineAudioQueue": {
            "mode": str(
                kv.get("line_audio_queue_mode", "immediate") or "immediate"
            ).strip()
            or "immediate",
            "scheduledAt": str(
                kv.get("line_audio_queue_scheduled_at", "") or ""
            ).strip(),
        },
        "copyrightAudio": {
            "introEnabled": str(kv.get("copyright_audio_intro_enabled", "0") or "0")
            == "1",
            "introPath": str(kv.get("copyright_audio_intro_path", "") or "").strip(),
            "outroEnabled": str(kv.get("copyright_audio_outro_enabled", "0") or "0")
            == "1",
            "outroPath": str(kv.get("copyright_audio_outro_path", "") or "").strip(),
        },
        "liveEndingAudio": {
            "items": normalize_live_ending_audio_items(
                str(kv.get("live_ending_audio_items", "") or "").strip(),
                str(kv.get("live_ending_audio_path", "") or "").strip(),
            ),
        },
    }


def normalize_live_ending_audio_items(raw_json: str, legacy_path: str = "") -> list[dict]:
    items = []
    text = str(raw_json or "").strip()
    if text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    path = str(item.get("path") or "").strip()
                    if not path:
                        continue
                    items.append({
                        "label": str(item.get("label") or "直播结束语").strip() or "直播结束语",
                        "path": path,
                    })
        except Exception:
            pass
    if not items:
        legacy = str(legacy_path or "").strip()
        if legacy:
            items.append({"label": "直播结束语", "path": legacy})
    return items


def fetch_chapters(conn: sqlite3.Connection, novel_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.id,c.novel_id,c.chapter_num,c.title,c.word_count,c.text_file_path,c.audio_file_path,c.has_audio,
               n.english_dir,
               (
                   SELECT t.merged_result_json
                   FROM json_tasks t
                   WHERE t.novel_id=c.novel_id AND t.chapter_num=c.chapter_num AND t.status='completed'
                   ORDER BY t.id DESC LIMIT 1
               ) AS latest_json
        FROM chapters c
        JOIN novels n ON n.id = c.novel_id
        WHERE c.novel_id=? ORDER BY c.chapter_num ASC
        """,
        (novel_id,),
    ).fetchall()
    result = []
    for r in rows:
        result.append(
            {
                "id": int(r["id"]),
                "chapterNum": int(r["chapter_num"]),
                "title": str(r["title"]),
                "wordCount": int(r["word_count"] or 0),
                "textFilePath": str(r["text_file_path"] or ""),
                "audioFilePath": str(r["audio_file_path"] or ""),
                "hasJson": json_text_ready(str(r["latest_json"] or "")),
                "hasAudio": resolve_audio_file(r) is not None,
            }
        )
    return result


def fetch_novel_download_chapters(
    conn: sqlite3.Connection, novel_id: int
) -> list[dict]:
    from .line_audio import get_chapter_merged_audio_stats

    rows = conn.execute(
        """
        SELECT c.id,c.novel_id,c.chapter_num,c.title,c.word_count,c.audio_file_path,
               c.audio_duration_seconds,c.audio_duration_md5,
               c.non_ver_audio_duration_seconds,c.non_ver_audio_duration_md5,
               n.english_dir
        FROM chapters c
        JOIN novels n ON n.id = c.novel_id
        WHERE c.novel_id=?
        ORDER BY c.chapter_num ASC
        """,
        (novel_id,),
    ).fetchall()
    result: list[dict] = []
    for row in rows:
        abs_audio = resolve_audio_file(row)
        size_bytes = 0
        duration_seconds = float(row["audio_duration_seconds"] or 0)
        download_url = ""
        if abs_audio and abs_audio.exists() and abs_audio.is_file():
            size_bytes = int(abs_audio.stat().st_size)
            duration_seconds = update_chapter_audio_duration_cache(
                conn, int(row["id"]), abs_audio
            )
            download_url = f"/api/novels/{int(row['novel_id'])}/chapters/{int(row['chapter_num'])}/audio-file"
        non_ver_stats = get_chapter_merged_audio_stats(
            int(row["novel_id"]), int(row["id"]), include_copyright=False
        )
        non_ver_duration = float(row["non_ver_audio_duration_seconds"] or 0)
        non_ver_size = int(non_ver_stats["sizeBytes"] or 0)
        if non_ver_stats["hasAudio"]:
            rel_path = str(non_ver_stats["relPath"] or "").strip()
            non_ver_path = (ROOT_DIR / rel_path).resolve() if rel_path else None
            if non_ver_path and non_ver_path.exists() and non_ver_path.is_file():
                md5_hex = file_md5_hex(non_ver_path)
                if md5_hex and md5_hex != str(row["non_ver_audio_duration_md5"] or ""):
                    non_ver_duration = update_chapter_non_ver_audio_duration_cache(
                        conn, int(row["id"]), non_ver_path
                    )
        result.append(
            {
                "id": int(row["id"]),
                "chapterNum": int(row["chapter_num"]),
                "title": str(row["title"] or ""),
                "wordCount": int(row["word_count"] or 0),
                "audioDurationSeconds": duration_seconds,
                "audioSizeBytes": size_bytes,
                "downloadUrl": download_url,
                "hasAudio": bool(download_url),
                "nonVerAudioDurationSeconds": non_ver_duration,
                "nonVerAudioSizeBytes": non_ver_size,
                "nonVerDownloadUrl": (
                    f"/api/novels/{int(row['novel_id'])}/chapters/{int(row['chapter_num'])}/merged-audio?variant=nonver"
                    if non_ver_stats["hasAudio"]
                    else ""
                ),
                "hasNonVerAudio": bool(non_ver_stats["hasAudio"]),
            }
        )
    return result


def chapter_content(
    english_dir: str, chapter_num: int, title: str, file_path: str
) -> str:
    if file_path:
        abs_path = (ROOT_DIR / file_path).resolve()
        if abs_path.exists() and abs_path.is_file():
            return abs_path.read_text(encoding="utf-8", errors="ignore")
    return f"{title}\n\n当前章节正文尚未导入。\n请将 txt 文件放入 novel/{english_dir}/text 后再刷新。"


def split_title_and_content(text: str, fallback_title: str) -> tuple[str, str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [x.strip() for x in normalized.split("\n")]
    non_empty = [x for x in lines if x]
    if not non_empty:
        return fallback_title, ""
    title = non_empty[0]
    content = "\n".join(non_empty[1:]).strip()
    return title or fallback_title, content


def count_words(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def count_words_from_file(file_path: str) -> int:
    path = str(file_path or "").strip()
    if not path:
        return 0
    abs_path = (ROOT_DIR / path).resolve()
    if not abs_path.exists() or not abs_path.is_file():
        return 0
    try:
        raw = abs_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    return count_words(raw)


def infer_chapter_num_and_title(file_path: Path) -> tuple[int | None, str]:
    stem = file_path.stem
    m = re.match(r"^(\d{1,4})[_\-\s]*(.*)$", stem)
    if not m:
        return None, stem
    num = int(m.group(1))
    title = (m.group(2) or "").strip()
    return num, title


def import_text_chapters(conn: sqlite3.Connection, novel_id: int) -> dict:
    novel = conn.execute(
        "SELECT english_dir,name FROM novels WHERE id=?", (novel_id,)
    ).fetchone()
    if not novel:
        return {"ok": False, "error": "novel not found"}

    english_dir = str(novel["english_dir"])
    text_dir = NOVEL_DIR / english_dir / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    imported = 0

    for fp in sorted(text_dir.glob("*.txt")):
        num, inferred_title = infer_chapter_num_and_title(fp)
        if num is None:
            continue
        raw = fp.read_text(encoding="utf-8", errors="ignore")
        title, content = split_title_and_content(raw, inferred_title or f"第{num}回")
        word_count = len(re.sub(r"\s+", "", content or raw))
        rel_path = fp.relative_to(ROOT_DIR)

        row = conn.execute(
            "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
            (novel_id, num),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE chapters
                SET title=?,word_count=?,text_file_path=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (title, word_count, db_rel_path(rel_path), int(row["id"])),
            )
        else:
            conn.execute(
                """
                INSERT INTO chapters (novel_id,chapter_num,title,word_count,text_file_path)
                VALUES (?,?,?,?,?)
                """,
                (novel_id, num, title, word_count, db_rel_path(rel_path)),
            )
        imported += 1

    conn.execute(
        """
        UPDATE novels
        SET chapter_count=(SELECT COUNT(1) FROM chapters WHERE novel_id=?),
            total_words=(SELECT COALESCE(SUM(word_count),0) FROM chapters WHERE novel_id=?),
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (novel_id, novel_id, novel_id),
    )
    return {"ok": True, "imported": imported}


def recalc_novel_stats(conn: sqlite3.Connection, novel_id: int) -> None:
    conn.execute(
        """
        UPDATE novels
        SET chapter_count=(SELECT COUNT(1) FROM chapters WHERE novel_id=?),
            total_words=(SELECT COALESCE(SUM(word_count),0) FROM chapters WHERE novel_id=?),
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (novel_id, novel_id, novel_id),
    )


def search_novel_text_occurrences(
    conn: sqlite3.Connection, novel_id: int, needle: str
) -> dict:
    target = str(needle or "")
    if not target:
        return {"totalCount": 0, "chapterCount": 0, "matches": []}
    rows = conn.execute(
        "SELECT id, chapter_num, title, text_file_path FROM chapters WHERE novel_id=? ORDER BY chapter_num ASC",
        (novel_id,),
    ).fetchall()
    json_rows = conn.execute(
        "SELECT id, chapter_num, chapter_title, merged_result_json FROM json_tasks WHERE novel_id=? ORDER BY chapter_num ASC, id ASC",
        (novel_id,),
    ).fetchall()

    match_map: dict[int, dict] = {}
    total_count = 0

    for row in rows:
        chapter_num = int(row["chapter_num"] or 0)
        title = str(row["title"] or f"第{chapter_num}章")
        file_path = str(row["text_file_path"] or "").strip()
        if not file_path:
            continue
        abs_path = (ROOT_DIR / file_path).resolve()
        if not abs_path.exists() or not abs_path.is_file():
            continue
        raw = abs_path.read_text(encoding="utf-8", errors="ignore")
        count = raw.count(target)
        if count <= 0:
            continue
        total_count += count
        item = match_map.setdefault(
            chapter_num,
            {"chapterNum": chapter_num, "title": title, "txtCount": 0, "jsonCount": 0},
        )
        item["txtCount"] += count

    for row in json_rows:
        chapter_num = int(row["chapter_num"] or 0)
        title = str(row["chapter_title"] or f"第{chapter_num}章")
        raw = str(row["merged_result_json"] or "")
        count = raw.count(target)
        if count <= 0:
            continue
        total_count += count
        item = match_map.setdefault(
            chapter_num,
            {"chapterNum": chapter_num, "title": title, "txtCount": 0, "jsonCount": 0},
        )
        item["jsonCount"] += count

    matches = sorted(match_map.values(), key=lambda x: x["chapterNum"])
    return {
        "totalCount": total_count,
        "chapterCount": len(matches),
        "matches": matches,
    }


def replace_novel_text_occurrences(
    conn: sqlite3.Connection, novel_id: int, search_text: str, replace_text: str
) -> dict:
    needle = str(search_text or "")
    replacement = str(replace_text or "")
    if not needle:
        return {"ok": False, "error": "search text is empty"}

    rows = conn.execute(
        "SELECT id, chapter_num, title, text_file_path FROM chapters WHERE novel_id=? ORDER BY chapter_num ASC",
        (novel_id,),
    ).fetchall()
    txt_replaced = 0
    for row in rows:
        file_path = str(row["text_file_path"] or "").strip()
        if not file_path:
            continue
        abs_path = (ROOT_DIR / file_path).resolve()
        if not abs_path.exists() or not abs_path.is_file():
            continue
        raw = abs_path.read_text(encoding="utf-8", errors="ignore")
        count = raw.count(needle)
        if count <= 0:
            continue
        updated = raw.replace(needle, replacement)
        abs_path.write_text(updated, encoding="utf-8")
        _, content = split_title_and_content(updated, str(row["title"] or ""))
        conn.execute(
            "UPDATE chapters SET word_count=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (count_words(content), int(row["id"])),
        )
        txt_replaced += count

    json_rows = conn.execute(
        "SELECT id, chapter_title, merged_result_json FROM json_tasks WHERE novel_id=?",
        (novel_id,),
    ).fetchall()
    json_replaced = 0
    for row in json_rows:
        raw = str(row["merged_result_json"] or "")
        count = raw.count(needle)
        if count <= 0:
            continue
        updated = raw.replace(needle, replacement)
        conn.execute(
            "UPDATE json_tasks SET merged_result_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (updated, int(row["id"])),
        )
        json_replaced += count

    recalc_novel_stats(conn, novel_id)
    return {"ok": True, "txtReplaced": txt_replaced, "jsonReplaced": json_replaced}


def safe_chapter_file_name(chapter_num: int, title: str) -> str:
    clean = (
        re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title).strip("_")
        or f"chapter_{chapter_num}"
    )
    return f"{chapter_num:03d}_{clean}.txt"


def create_or_update_chapter_record(
    conn: sqlite3.Connection,
    novel_id: int,
    current_chapter_num: int | None,
    next_chapter_num: int,
    title: str,
    content: str,
) -> tuple[bool, str]:
    novel = conn.execute(
        "SELECT english_dir FROM novels WHERE id=?", (novel_id,)
    ).fetchone()
    if not novel:
        return False, "novel not found"
    english_dir = str(novel["english_dir"])
    ensure_novel_dirs(english_dir)

    if current_chapter_num is None:
        existing = conn.execute(
            "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
            (novel_id, next_chapter_num),
        ).fetchone()
        if existing:
            return False, "chapter number already exists"
        text_path = ""
        wc = 0
        if content.strip():
            rel = (
                Path("novel")
                / english_dir
                / "text"
                / safe_chapter_file_name(next_chapter_num, title)
            )
            (ROOT_DIR / rel).write_text(content, encoding="utf-8")
            text_path = db_rel_path(rel)
            wc = count_words(content)
        conn.execute(
            """
            INSERT INTO chapters (novel_id,chapter_num,title,word_count,text_file_path)
            VALUES (?,?,?,?,?)
            """,
            (novel_id, next_chapter_num, title, wc, text_path),
        )
        recalc_novel_stats(conn, novel_id)
        return True, "ok"

    chapter = conn.execute(
        "SELECT id,text_file_path FROM chapters WHERE novel_id=? AND chapter_num=?",
        (novel_id, current_chapter_num),
    ).fetchone()
    if not chapter:
        return False, "chapter not found"

    conflict = conn.execute(
        "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=? AND chapter_num<>?",
        (novel_id, next_chapter_num, current_chapter_num),
    ).fetchone()
    if conflict:
        return False, "chapter number already exists"

    old_text_path = str(chapter["text_file_path"] or "")
    text_path = old_text_path
    wc = count_words_from_file(text_path)
    if content.strip():
        rel = (
            Path("novel")
            / english_dir
            / "text"
            / safe_chapter_file_name(next_chapter_num, title)
        )
        (ROOT_DIR / rel).write_text(content, encoding="utf-8")
        text_path = db_rel_path(rel)
        wc = count_words(content)
        if old_text_path and old_text_path != text_path:
            old_abs_path = (ROOT_DIR / old_text_path).resolve()
            if old_abs_path.exists() and old_abs_path.is_file():
                try:
                    old_abs_path.unlink()
                except OSError:
                    pass
    conn.execute(
        """
        UPDATE chapters
        SET chapter_num=?,title=?,word_count=?,text_file_path=?,updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (next_chapter_num, title, wc, text_path, int(chapter["id"])),
    )
    recalc_novel_stats(conn, novel_id)
    return True, "ok"


def resolve_audio_file(chapter_row: sqlite3.Row) -> Path | None:
    english_dir = str(chapter_row["english_dir"])
    chapter_num = int(chapter_row["chapter_num"])

    try:
        from .line_audio import get_chapter_merged_audio_path

        novel_id = int(chapter_row["novel_id"])
        chapter_id = int(chapter_row["id"])
        merged_path = get_chapter_merged_audio_path(novel_id, chapter_id)
        if merged_path is not None:
            return merged_path
    except Exception:
        pass

    audio_path = str(chapter_row["audio_file_path"] or "").strip()
    if audio_path:
        abs_path = (ROOT_DIR / audio_path).resolve()
        file_name = abs_path.name.lower()
        chapter_prefixes = (
            f"{chapter_num:03d}_",
            f"{chapter_num:03d}.",
            f"chapter-{chapter_num:03d}-",
        )
        if (
            abs_path.exists()
            and abs_path.is_file()
            and any(file_name.startswith(prefix) for prefix in chapter_prefixes)
        ):
            return abs_path

    audio_dir = NOVEL_DIR / english_dir / "audio"
    if not audio_dir.exists():
        return None

    patterns = [
        f"{chapter_num:03d}_*",
        f"{chapter_num:03d}.*",
        f"chapter-{chapter_num:03d}-*",
    ]
    for pattern in patterns:
        candidates = sorted([p for p in audio_dir.glob(pattern) if p.is_file()])
        if candidates:
            return candidates[0]
    return None


def http_json_request(
    method: str,
    url: str,
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
    proxy_url: str = "",
) -> tuple[int, str]:
    data = None
    req_headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "curl/8.7.1",
    }
    if headers:
        req_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = request.Request(url, method=method.upper(), headers=req_headers, data=data)
    parsed_url = urlparse(url)
    host = str(parsed_url.hostname or "").strip().lower()
    bypass_env_proxy = False
    if host in {"localhost", "127.0.0.1", "::1"}:
        bypass_env_proxy = True
    else:
        try:
            bypass_env_proxy = ipaddress.ip_address(host).is_private
        except ValueError:
            bypass_env_proxy = host.endswith(".local")

    if proxy_url:
        opener = request.build_opener(
            request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
    elif bypass_env_proxy:
        opener = request.build_opener(request.ProxyHandler({}))
    else:
        opener = request.build_opener()

    transient_errors = (
        URLError,
        TimeoutError,
        RemoteDisconnected,
        ConnectionError,
        IncompleteRead,
    )
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with opener.open(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
                return int(resp.getcode() or 200), body
        except HTTPError as exc:
            body = (
                exc.read().decode("utf-8", errors="ignore")
                if hasattr(exc, "read")
                else str(exc)
            )
            return int(exc.code), body
        except transient_errors as exc:
            last_exc = exc
            if is_timeout_error(exc):
                raise LlmRequestTimeoutError("request timeout") from exc
            if attempt >= 2:
                if isinstance(exc, URLError):
                    raise RuntimeError(str(exc.reason)) from exc
                raise RuntimeError(str(exc)) from exc
            time.sleep(1.2 * (attempt + 1))
    if last_exc:
        if is_timeout_error(last_exc):
            raise LlmRequestTimeoutError("request timeout") from last_exc
        raise RuntimeError(str(last_exc))
    raise RuntimeError("request failed")


class LlmRequestTimeoutError(RuntimeError):
    pass


class LlmRateLimitError(RuntimeError):
    pass


def wait_for_json_llm_request_slot() -> None:
    global JSON_LLM_LAST_REQUEST_TS
    with JSON_LLM_THROTTLE_LOCK:
        now = time.time()
        wait_seconds = max(0.0, JSON_LLM_MIN_INTERVAL_SECONDS - (now - JSON_LLM_LAST_REQUEST_TS))
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        JSON_LLM_LAST_REQUEST_TS = time.time()


def is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, URLError):
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return True
        return "timed out" in str(reason).lower() or "timeout" in str(reason).lower()
    return "timed out" in str(exc).lower() or "timeout" in str(exc).lower()


def test_comfy_endpoint(comfy_url: str) -> tuple[bool, str]:
    base = comfy_url.rstrip("/")
    if not base:
        return False, "ComfyUI 地址不能为空"
    for path in ["/system_stats", "/queue", "/prompt"]:
        try:
            code, _ = http_json_request("GET", f"{base}{path}", timeout=6.0)
        except RuntimeError as exc:
            return False, str(exc)
        if code in {200, 201}:
            return True, f"{path} 返回 {code}"
    return False, "未获取到可用响应（期望 /system_stats 或 /queue 可访问）"


def test_llm_endpoint(
    provider: str,
    base_url: str,
    model: str,
    api_key: str,
    proxy_url: str,
    think: bool = True,
    num_ctx: int = 65536,
    keep_alive: str = "30m",
    unload_after_call: bool = False,
) -> tuple[bool, str]:
    if not base_url:
        return False, "API Base URL 不能为空"
    if not model:
        return False, "模型名称不能为空"
    if provider not in {"custom", "ollama"} and not api_key:
        return False, "API Key 不能为空"

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {}
    if api_key and provider != "ollama":
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a healthcheck bot."},
            {"role": "user", "content": "reply with pong"},
        ],
        "max_tokens": 8,
        "temperature": 0,
    }
    if provider == "ollama":
        url = build_ollama_chat_url(base_url)
        request_keep_alive = normalize_ollama_keep_alive(keep_alive)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a healthcheck bot."},
                {"role": "user", "content": "reply with pong"},
            ],
            "stream": False,
            "think": bool(think),
            "keep_alive": request_keep_alive,
            "options": {
                "num_ctx": int(num_ctx or 65536),
                "temperature": 0,
                "num_predict": 8,
            },
        }
    try:
        wait_for_json_llm_request_slot()
        code, body = http_json_request(
            "POST",
            url,
            payload=payload,
            headers=headers,
            timeout=15.0,
            proxy_url=proxy_url,
        )
    except RuntimeError as exc:
        return False, str(exc)
    finally:
        if provider == "ollama" and unload_after_call:
            try:
                unload_ollama_model(
                    base_url=base_url,
                    model=model,
                    proxy_url=proxy_url,
                    timeout=15.0,
                )
            except Exception:
                pass

    if 200 <= code < 300:
        return True, "模型接口可调用"

    detail = ""
    try:
        parsed = json.loads(body or "{}")
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict):
                detail = str(err.get("message") or err.get("msg") or "").strip()
            elif isinstance(err, str):
                detail = err.strip()
            if not detail:
                detail = str(parsed.get("message") or parsed.get("msg") or "").strip()
    except Exception:
        detail = ""

    if code in {401, 403}:
        return False, "认证失败，请检查 API Key 是否正确"
    if code == 404:
        return False, "接口地址不可用，请检查 API Base URL"
    if code == 429:
        return False, "请求频率或额度受限，请稍后重试"
    if code >= 500:
        return False, "模型服务暂时不可用，请稍后重试"
    if detail:
        return False, f"请求失败（HTTP {code}）：{detail[:80]}"
    return False, f"请求失败（HTTP {code}）"


def extract_json_text(raw: str) -> str:
    text = str(raw or "").strip()
    start = text.find("{")
    if start < 0:
        return text

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
            continue
    return text


def build_ollama_chat_url(base_url: str) -> str:
    base = str(base_url or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/api/chat"


def build_ollama_generate_url(base_url: str) -> str:
    base = str(base_url or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/api/generate"


def normalize_ollama_keep_alive(keep_alive: str) -> str:
    value = str(keep_alive or "30m").strip() or "30m"
    return "30m" if value == "unload" else value


def unload_ollama_model(
    *, base_url: str, model: str, proxy_url: str = "", timeout: float = 30.0
) -> None:
    if not str(base_url or "").strip() or not str(model or "").strip():
        return
    http_json_request(
        "POST",
        build_ollama_generate_url(base_url),
        payload={
            "model": str(model).strip(),
            "prompt": "",
            "stream": False,
            "keep_alive": 0,
        },
        timeout=timeout,
        proxy_url=proxy_url,
    )


def parse_model_json(raw: str) -> dict:
    text = str(raw or "").strip()
    candidates: list[str] = []
    if text:
        candidates.append(text)

    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1).strip())

    extracted = extract_json_text(text)
    if extracted:
        candidates.append(extracted)

    tried: set[str] = set()
    for candidate in candidates:
        if candidate in tried:
            continue
        tried.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    snippet = text[:240].replace("\n", "\\n")
    raise ValueError(f"Model output is not valid JSON object. Raw head: {snippet}")


def json_payload_ready(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    role_list = payload.get("role_list")
    if not isinstance(role_list, list) or not role_list:
        return False
    juben = str(payload.get("juben") or "").strip()
    return bool(juben)


def json_text_ready(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    try:
        parsed = json.loads(raw)
    except Exception:
        return False
    return json_payload_ready(parsed)


def split_text_batches(text: str, max_chars: int = 3500) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return [""]
    if int(max_chars or 0) <= 0:
        return [normalized]

    lines = normalized.split("\n")
    batches: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        line_size = len(line) + 1
        if buf and size + line_size > max_chars:
            batches.append("\n".join(buf).strip())
            buf = [line]
            size = line_size
        else:
            buf.append(line)
            size += line_size
    if buf:
        batches.append("\n".join(buf).strip())
    return [x for x in batches if x] or [normalized]


def merge_batch_outputs(outputs: list[dict]) -> dict:
    role_map: dict[str, dict] = {}
    juben_parts: list[str] = []
    for output in outputs:
        role_list = output.get("role_list")
        if isinstance(role_list, list):
            for role in role_list:
                if not isinstance(role, dict):
                    continue
                name = str(role.get("name") or "").strip()
                if not name:
                    continue
                if name not in role_map:
                    role_map[name] = {
                        "name": name,
                        "instruct": str(role.get("instruct") or "").strip(),
                        "text": str(role.get("text") or "").strip(),
                    }
                else:
                    if not role_map[name].get("instruct"):
                        role_map[name]["instruct"] = str(
                            role.get("instruct") or ""
                        ).strip()
                    if not role_map[name].get("text"):
                        role_map[name]["text"] = str(role.get("text") or "").strip()
        juben = str(output.get("juben") or "").strip()
        if juben:
            juben_parts.append(juben)

    return {
        "role_list": list(role_map.values()),
        "juben": "\n".join(juben_parts).strip(),
    }


def extract_chat_content(data: dict) -> str:
    message = data.get("message")
    if isinstance(message, dict):
        return str(message.get("content") or "").strip()

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("chat response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("chat response missing first choice object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ValueError("chat response missing message")
    content = message.get("content", "")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return str(content or "").strip()


def read_chapter_text(file_path: str) -> str:
    rel = str(file_path or "").strip()
    if not rel:
        return ""
    abs_path = (ROOT_DIR / rel).resolve()
    if not abs_path.exists() or not abs_path.is_file():
        return ""
    return abs_path.read_text(encoding="utf-8", errors="ignore").strip()


def call_llm_json_parse(
    *,
    llm: dict,
    proxy_url: str,
    system_prompt: str,
    chapter_title: str,
    chapter_text: str,
    batch_index: int = 1,
    batch_total: int = 1,
) -> str:
    base_url = str(llm.get("baseUrl") or "").strip()
    provider = str(llm.get("provider") or "").strip()
    model = str(llm.get("model") or "").strip()
    api_key = str(llm.get("apiKey") or "").strip()
    temperature = float(llm.get("temperature") or 0.3)
    max_tokens = int(llm.get("maxTokens") or 8192)
    num_ctx = int(llm.get("numCtx") or 65536)
    keep_alive = str(llm.get("keepAlive") or "30m").strip() or "30m"
    unload_after_call = bool(llm.get("unloadAfterCall", False))
    batch_timeout_minutes = int(llm.get("batchTimeoutMinutes") or 15)
    think = bool(llm.get("think", True))

    if not base_url:
        raise RuntimeError("LLM baseUrl is empty")
    if not model:
        raise RuntimeError("LLM model is empty")

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    batch_note = ""
    if batch_total > 1:
        batch_note = (
            f"补充说明：当前是拆分批次 {batch_index}/{batch_total}，"
            "请仅基于本批次原文输出 role_list 与 juben。\n"
        )
    user_prompt = (
        "请将以下章回文本解析为 JSON 对象。\n"
        "必须满足：\n"
        "1) 输出仅为一个 JSON 对象，不要输出解释文字。\n"
        "2) 必须包含 role_list(数组) 与 juben(字符串) 两个键。\n"
        "3) role_list 每项应包含 name、instruct、text 字段（字符串）。\n"
        f"{batch_note}\n"
        f"章回名：{chapter_title}\n"
        f"原文：\n{chapter_text}\n"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    request_timeout = float(max(60, batch_timeout_minutes * 60))
    url = f"{base_url.rstrip('/')}/chat/completions"
    if provider == "ollama":
        url = build_ollama_chat_url(base_url)
        request_keep_alive = normalize_ollama_keep_alive(keep_alive)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "think": think,
            "keep_alive": request_keep_alive,
            "options": {
                "num_ctx": num_ctx,
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
    try:
        code, body = http_json_request(
            "POST",
            url,
            payload=payload,
            headers=headers,
            timeout=request_timeout,
            proxy_url=proxy_url,
        )
    finally:
        if provider == "ollama" and unload_after_call:
            try:
                unload_ollama_model(
                    base_url=base_url,
                    model=model,
                    proxy_url=proxy_url,
                    timeout=30.0,
                )
            except Exception:
                pass

    if not (200 <= code < 300):
        detail = ""
        try:
            parsed = json.loads(body or "{}")
            if isinstance(parsed, dict):
                err = parsed.get("error")
                if isinstance(err, dict):
                    detail = str(err.get("message") or err.get("msg") or "").strip()
                elif isinstance(err, str):
                    detail = err.strip()
                if not detail:
                    detail = str(
                        parsed.get("message") or parsed.get("msg") or ""
                    ).strip()
        except Exception:
            detail = ""
        message = f"LLM request failed (HTTP {code})" + (f": {detail[:120]}" if detail else "")
        if code == 429:
            friendly = "模型限流（HTTP 429），请稍后重试"
            if detail:
                friendly += f"：{detail[:120]}"
            raise LlmRateLimitError(friendly)
        raise RuntimeError(message)

    parsed_body = json.loads(body or "{}")
    if not isinstance(parsed_body, dict):
        raise RuntimeError("LLM response is not object")
    content = extract_chat_content(parsed_body)
    if not content:
        raise RuntimeError("LLM response content is empty")
    return content


class JsonTaskCancelledError(RuntimeError):
    pass


class JsonTaskTimeoutError(RuntimeError):
    pass


def get_json_task_status(task_id: int) -> str:
    conn = db_conn()
    row = conn.execute("SELECT status FROM json_tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    return str(row["status"] or "") if row else ""


def is_json_task_cancelled(task_id: int) -> bool:
    return get_json_task_status(task_id) == "cancelled"


def ensure_json_task_not_cancelled(task_id: int) -> None:
    if is_json_task_cancelled(task_id):
        raise JsonTaskCancelledError("任务已被用户终止")


def cancel_json_task(task_id: int) -> tuple[bool, str]:
    conn = db_conn()
    row = conn.execute(
        "SELECT status, model_name FROM json_tasks WHERE id=?", (task_id,)
    ).fetchone()
    if not row:
        conn.close()
        return False, "json task not found"

    status = str(row["status"] or "")
    if status == "cancelled":
        conn.close()
        return True, "ok"
    if status not in {"pending", "running"}:
        conn.close()
        return False, "only pending or running task can be cancelled"

    conn.execute(
        """
        UPDATE json_tasks
        SET status='cancelled',error_message='任务被用户终止',updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (task_id,),
    )
    conn.execute(
        """
        UPDATE task_batches
        SET status='cancelled',error_message='任务被用户终止',updated_at=CURRENT_TIMESTAMP
        WHERE task_id=? AND status IN ('pending','processing')
        """,
        (task_id,),
    )
    conn.commit()
    conn.close()

    if status == "running":
        settings_conn = db_conn()
        settings = fetch_settings(settings_conn)
        settings_conn.close()
        llm = settings.get("llm") or {}
        if str(llm.get("provider") or "") == "ollama":
            try:
                unload_ollama_model(
                    base_url=str(llm.get("baseUrl") or "").strip(),
                    model=str(row["model_name"] or llm.get("model") or "").strip(),
                    proxy_url=str(settings.get("proxyUrl") or "").strip(),
                    timeout=10.0,
                )
            except Exception:
                pass
    return True, "ok"


def process_json_task(task_id: int) -> None:
    model_name = ""
    chapter_id: int | None = None
    try:
        conn = db_conn()
        task = conn.execute(
            """
            SELECT t.id,t.novel_id,t.chapter_id,t.chapter_num,t.chapter_title,t.status,t.prompt_id,
                   n.prompt_id AS novel_prompt_id,
                   c.id AS c_id,c.title AS c_title,c.text_file_path,
                   p.content AS prompt_content
            FROM json_tasks t
            JOIN novels n ON n.id=t.novel_id
            LEFT JOIN chapters c ON c.id=t.chapter_id
            LEFT JOIN json_prompts p ON p.id=t.prompt_id
            WHERE t.id=?
            """,
            (task_id,),
        ).fetchone()
        if not task:
            conn.close()
            return
        if str(task["status"]) != "running":
            conn.close()
            return

        chapter_row = task
        if chapter_row["c_id"] is None:
            chapter_row = conn.execute(
                """
                SELECT id AS c_id,title AS c_title,text_file_path
                FROM chapters WHERE novel_id=? AND chapter_num=?
                ORDER BY id DESC LIMIT 1
                """,
                (int(task["novel_id"]), int(task["chapter_num"])),
            ).fetchone()
            if chapter_row:
                conn.execute(
                    "UPDATE json_tasks SET chapter_id=? WHERE id=?",
                    (int(chapter_row["c_id"]), task_id),
                )

        chapter_id = (
            int(chapter_row["c_id"])
            if chapter_row and chapter_row["c_id"] is not None
            else None
        )
        chapter_title = (
            str(chapter_row["c_title"])
            if chapter_row and chapter_row["c_title"]
            else str(task["chapter_title"] or f"第{int(task['chapter_num'])}回")
        )
        text_file_path = str(chapter_row["text_file_path"] or "") if chapter_row else ""

        prompt_id = (
            int(task["prompt_id"])
            if task["prompt_id"] is not None
            else (
                int(task["novel_prompt_id"])
                if task["novel_prompt_id"] is not None
                else None
            )
        )
        if prompt_id is None:
            raise RuntimeError("novel prompt is not configured")

        prompt_row = conn.execute(
            "SELECT content FROM json_prompts WHERE id=?", (prompt_id,)
        ).fetchone()
        if not prompt_row:
            raise RuntimeError("prompt not found")
        system_prompt = str(prompt_row["content"] or "").strip()
        if not system_prompt:
            raise RuntimeError("prompt content is empty")

        settings = fetch_settings(conn)
        llm = settings.get("llm") or {}
        proxy_url = str(settings.get("proxyUrl") or "")
        model_name = str(llm.get("model") or "")
        think_enabled = 1 if bool(llm.get("think", True)) else 0

        chapter_text = read_chapter_text(text_file_path)
        if not chapter_text:
            raise RuntimeError("chapter text is empty or missing")

        raw_batch_max_chars = llm.get("batchMaxChars", 3500)
        if raw_batch_max_chars in (None, ""):
            raw_batch_max_chars = 3500
        batch_max_chars = int(raw_batch_max_chars)
        if batch_max_chars not in {0, 3500, 4000, 5000, 6000, 7000, 8000, 9000, 10000}:
            batch_max_chars = 3500
        batches = split_text_batches(chapter_text, max_chars=batch_max_chars)

        ensure_json_task_not_cancelled(task_id)
        conn.execute("DELETE FROM task_batches WHERE task_id=?", (task_id,))
        for idx, batch_text in enumerate(batches, start=1):
            conn.execute(
                """
                INSERT INTO task_batches(task_id,batch_index,input_text,input_word_count,status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (task_id, idx, batch_text, count_words(batch_text)),
            )

        conn.execute(
            """
            UPDATE json_tasks
            SET progress=10,model_name=?,think_enabled=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (model_name, think_enabled, task_id),
        )
        conn.commit()
        conn.close()

        parsed_outputs: list[dict] = []
        for idx, batch_text in enumerate(batches, start=1):
            ensure_json_task_not_cancelled(task_id)
            raw = ""
            parsed = None
            last_exc: Exception | None = None
            conn = db_conn()
            conn.execute(
                """
                UPDATE task_batches
                SET status='processing',updated_at=CURRENT_TIMESTAMP,auto_retry_count=0
                WHERE task_id=? AND batch_index=?
                """,
                (task_id, idx),
            )
            conn.execute(
                """
                UPDATE json_tasks
                SET progress=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (min(90, 10 + int(70 * (idx - 1) / max(1, len(batches)))), task_id),
            )
            conn.commit()
            conn.close()
            for attempt in range(1, 6):
                ensure_json_task_not_cancelled(task_id)
                try:
                    raw = call_llm_json_parse(
                        llm=llm,
                        proxy_url=proxy_url,
                        system_prompt=system_prompt,
                        chapter_title=chapter_title,
                        chapter_text=batch_text,
                        batch_index=idx,
                        batch_total=len(batches),
                    )
                    ensure_json_task_not_cancelled(task_id)
                    parsed = parse_model_json(raw)
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if isinstance(exc, JsonTaskCancelledError):
                        break
                    if isinstance(exc, LlmRequestTimeoutError):
                        last_exc = JsonTaskTimeoutError(
                            f"批次执行超时（>{int(llm.get('batchTimeoutMinutes') or 15)}分钟）"
                        )
                        break
                    if isinstance(exc, LlmRateLimitError):
                        if attempt < 5:
                            time.sleep(min(90, 12 * attempt))
                            continue
                    conn = db_conn()
                    conn.execute(
                        "UPDATE task_batches SET auto_retry_count=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND batch_index=?",
                        (attempt, task_id, idx),
                    )
                    conn.commit()
                    conn.close()
                    if attempt < 5:
                        time.sleep(0.8 * attempt)

            ensure_json_task_not_cancelled(task_id)
            if last_exc is not None or parsed is None:
                failed_status = "timeout" if isinstance(last_exc, JsonTaskTimeoutError) else "failed"
                conn = db_conn()
                conn.execute(
                    """
                    UPDATE task_batches
                    SET status=?,llm_response_text=?,error_message=?,updated_at=CURRENT_TIMESTAMP
                    WHERE task_id=? AND batch_index=? AND status<>'cancelled'
                    """,
                    (failed_status, raw, str(last_exc or "批次处理失败"), task_id, idx),
                )
                if isinstance(last_exc, JsonTaskTimeoutError):
                    conn.execute(
                        """
                        UPDATE json_tasks
                        SET status='timeout',progress=0,error_message=?,model_name=?,updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (str(last_exc), model_name, task_id),
                    )
                conn.commit()
                conn.close()
                raise last_exc or RuntimeError("批次处理失败")

            parsed_outputs.append(parsed)
            ensure_json_task_not_cancelled(task_id)
            conn = db_conn()
            conn.execute(
                """
                UPDATE task_batches
                SET status='completed',llm_response_text=?,parsed_json_text=?,error_message=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE task_id=? AND batch_index=? AND status<>'cancelled'
                """,
                (raw, json.dumps(parsed, ensure_ascii=False), task_id, idx),
            )
            conn.commit()
            conn.close()

        ensure_json_task_not_cancelled(task_id)
        merged_obj = merge_batch_outputs(parsed_outputs)
        merged = json.dumps(merged_obj, ensure_ascii=False)

        conn = db_conn()
        conn.execute(
            """
            UPDATE json_tasks
            SET status='completed',progress=100,merged_result_json=?,error_message=NULL,
                model_name=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND status<>'cancelled'
            """,
            (merged, model_name, task_id),
        )
        if chapter_id is not None:
            chapter_has_json = 1 if json_payload_ready(merged_obj) else 0
            conn.execute(
                "UPDATE chapters SET has_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (chapter_has_json, chapter_id),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        if isinstance(exc, JsonTaskCancelledError) or is_json_task_cancelled(task_id):
            return
        if isinstance(exc, JsonTaskTimeoutError):
            return
        conn = db_conn()
        conn.execute(
            """
            UPDATE json_tasks
            SET status='failed',progress=0,error_message=?,model_name=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (str(exc), model_name, task_id),
        )
        conn.commit()
        conn.close()


def _load_json_task_context(task_id: int) -> dict:
    conn = db_conn()
    task = conn.execute(
        """
        SELECT t.id,t.novel_id,t.chapter_id,t.chapter_num,t.chapter_title,t.status,t.prompt_id,
               n.prompt_id AS novel_prompt_id,
               c.id AS c_id,c.title AS c_title,c.text_file_path,
               p.content AS prompt_content
        FROM json_tasks t
        JOIN novels n ON n.id=t.novel_id
        LEFT JOIN chapters c ON c.id=t.chapter_id
        LEFT JOIN json_prompts p ON p.id=t.prompt_id
        WHERE t.id=?
        """,
        (task_id,),
    ).fetchone()
    if not task:
        conn.close()
        raise RuntimeError("json task not found")

    chapter_row = task
    if chapter_row["c_id"] is None:
        chapter_row = conn.execute(
            """
            SELECT id AS c_id,title AS c_title,text_file_path
            FROM chapters WHERE novel_id=? AND chapter_num=?
            ORDER BY id DESC LIMIT 1
            """,
            (int(task["novel_id"]), int(task["chapter_num"])),
        ).fetchone()
        if chapter_row:
            conn.execute(
                "UPDATE json_tasks SET chapter_id=? WHERE id=?",
                (int(chapter_row["c_id"]), task_id),
            )
            conn.commit()

    chapter_id = (
        int(chapter_row["c_id"])
        if chapter_row and chapter_row["c_id"] is not None
        else None
    )
    chapter_title = (
        str(chapter_row["c_title"])
        if chapter_row and chapter_row["c_title"]
        else str(task["chapter_title"] or f"第{int(task['chapter_num'])}回")
    )
    text_file_path = str(chapter_row["text_file_path"] or "") if chapter_row else ""

    prompt_id = (
        int(task["prompt_id"])
        if task["prompt_id"] is not None
        else (
            int(task["novel_prompt_id"])
            if task["novel_prompt_id"] is not None
            else None
        )
    )
    if prompt_id is None:
        conn.close()
        raise RuntimeError("novel prompt is not configured")

    prompt_row = conn.execute(
        "SELECT content FROM json_prompts WHERE id=?", (prompt_id,)
    ).fetchone()
    if not prompt_row:
        conn.close()
        raise RuntimeError("prompt not found")
    system_prompt = str(prompt_row["content"] or "").strip()
    if not system_prompt:
        conn.close()
        raise RuntimeError("prompt content is empty")

    settings = fetch_settings(conn)
    llm = settings.get("llm") or {}
    proxy_url = str(settings.get("proxyUrl") or "")
    model_name = str(llm.get("model") or "")
    think_enabled = bool(llm.get("think", True))
    conn.close()
    return {
        "task": task,
        "chapterId": chapter_id,
        "chapterTitle": chapter_title,
        "textFilePath": text_file_path,
        "systemPrompt": system_prompt,
        "llm": llm,
        "proxyUrl": proxy_url,
        "modelName": model_name,
        "thinkEnabled": think_enabled,
    }


def _process_json_batch_once(
    context: dict, batch_index: int, batch_text: str
) -> tuple[str, dict]:
    raw = ""
    parsed = None
    last_exc: Exception | None = None
    for attempt in range(1, 6):
        ensure_json_task_not_cancelled(int(context["task"]["id"]))
        try:
            raw = call_llm_json_parse(
                llm=context["llm"],
                proxy_url=context["proxyUrl"],
                system_prompt=context["systemPrompt"],
                chapter_title=context["chapterTitle"],
                chapter_text=batch_text,
                batch_index=batch_index,
                batch_total=max(1, int(context.get("batchTotal") or 1)),
            )
            ensure_json_task_not_cancelled(int(context["task"]["id"]))
            parsed = parse_model_json(raw)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            if isinstance(exc, JsonTaskCancelledError):
                break
            if isinstance(exc, LlmRequestTimeoutError):
                last_exc = JsonTaskTimeoutError(
                    f"批次执行超时（>{int((context['llm'] or {}).get('batchTimeoutMinutes') or 15)}分钟）"
                )
                break
            if isinstance(exc, LlmRateLimitError):
                if attempt < 5:
                    time.sleep(min(90, 12 * attempt))
                    continue
            if attempt < 5:
                time.sleep(0.8 * attempt)
    ensure_json_task_not_cancelled(int(context["task"]["id"]))
    if last_exc is not None or parsed is None:
        raise RuntimeError(str(last_exc or "批次处理失败")) from last_exc
    return raw, parsed


def _finalize_json_task_if_ready(task_id: int) -> bool:
    conn = db_conn()
    task = conn.execute(
        "SELECT id, chapter_id, status FROM json_tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    if not task:
        conn.close()
        return False
    if str(task["status"] or "") == "cancelled":
        conn.close()
        return False
    rows = conn.execute(
        "SELECT batch_index, status, parsed_json_text FROM task_batches WHERE task_id=? ORDER BY batch_index ASC",
        (task_id,),
    ).fetchall()
    if not rows:
        conn.close()
        return False
    if any(str(row["status"] or "") != "completed" for row in rows):
        conn.close()
        return False
    parsed_outputs = []
    for row in rows:
        parsed_outputs.append(json.loads(str(row["parsed_json_text"] or "{}") or "{}"))
    merged_obj = merge_batch_outputs(parsed_outputs)
    merged = json.dumps(merged_obj, ensure_ascii=False)
    conn.execute(
        """
        UPDATE json_tasks
        SET status='completed',progress=100,merged_result_json=?,error_message=NULL,updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND status<>'cancelled'
        """,
        (merged, task_id),
    )
    if task["chapter_id"] is not None:
        conn.execute(
            "UPDATE chapters SET has_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (1 if json_payload_ready(merged_obj) else 0, int(task["chapter_id"])),
        )
    conn.commit()
    conn.close()
    return True


def retry_json_task_batch(task_id: int, batch_index: int) -> tuple[bool, str]:
    context = _load_json_task_context(task_id)
    task = context["task"]
    if str(task["status"] or "") not in {"failed", "completed", "timeout"}:
        return False, "only completed failed or timeout task batches can be retried"

    conn = db_conn()
    batch = conn.execute(
        "SELECT id, input_text, retry_count FROM task_batches WHERE task_id=? AND batch_index=?",
        (task_id, batch_index),
    ).fetchone()
    if not batch:
        conn.close()
        return False, "batch not found"
    retry_count = int(batch["retry_count"] or 0)
    if retry_count >= 10:
        conn.close()
        return False, "batch retry limit reached"
    conn.execute(
        """
        UPDATE task_batches
        SET status='processing',parsed_json_text=NULL,error_message=NULL,updated_at=CURRENT_TIMESTAMP,retry_count=retry_count+1,auto_retry_count=0
        WHERE task_id=? AND batch_index=?
        """,
        (task_id, batch_index),
    )
    conn.execute(
        "UPDATE json_tasks SET model_name=?, think_enabled=?, started_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (context["modelName"], 1 if context["thinkEnabled"] else 0, task_id),
    )
    conn.commit()
    conn.close()

    try:
        batch_text = str(batch["input_text"] or "")
        batch_total_conn = db_conn()
        context["batchTotal"] = int(
            batch_total_conn.execute(
                "SELECT COUNT(1) AS c FROM task_batches WHERE task_id=?", (task_id,)
            ).fetchone()["c"]
            or 0
        )
        batch_total_conn.close()
        raw, parsed = _process_json_batch_once(context, batch_index, batch_text)
        conn = db_conn()
        conn.execute(
            """
            UPDATE task_batches
            SET status='completed',llm_response_text=?,parsed_json_text=?,error_message=NULL,updated_at=CURRENT_TIMESTAMP
            WHERE task_id=? AND batch_index=?
            """,
            (raw, json.dumps(parsed, ensure_ascii=False), task_id, batch_index),
        )
        conn.commit()
        conn.close()
        merged = _finalize_json_task_if_ready(task_id)
        if not merged:
            conn = db_conn()
            conn.execute(
                "UPDATE json_tasks SET status='failed', error_message=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (task_id,),
            )
            conn.commit()
            conn.close()
        return True, "ok"
    except Exception as exc:
        failed_status = "timeout" if isinstance(exc, JsonTaskTimeoutError) else "failed"
        conn = db_conn()
        conn.execute(
            """
            UPDATE task_batches
            SET status=?,llm_response_text=COALESCE(llm_response_text,''),error_message=?,updated_at=CURRENT_TIMESTAMP
            WHERE task_id=? AND batch_index=?
            """,
            (failed_status, str(exc), task_id, batch_index),
        )
        conn.execute(
            "UPDATE json_tasks SET status=?,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (failed_status, str(exc), task_id),
        )
        conn.commit()
        conn.close()
        return False, str(exc)


def run_json_queue_once() -> bool:
    conn = db_conn()
    running = conn.execute(
        "SELECT id FROM json_tasks WHERE status='running' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if running:
        task_id = int(running["id"])
        conn.close()
        process_json_task(task_id)
        return True

    pending = conn.execute(
        "SELECT id FROM json_tasks WHERE status='pending' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if not pending:
        conn.close()
        return False
    task_id = int(pending["id"])
    conn.execute(
        """
        UPDATE json_tasks
        SET status='running',progress=5,error_message=NULL,
            started_at=COALESCE(started_at, CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (task_id,),
    )
    conn.commit()
    conn.close()
    process_json_task(task_id)
    return True


def parse_datetime_utc(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(text.replace(" ", "T"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt
    except ValueError:
        return None


def extract_audio_output_from_history(
    history: dict, prompt_id: str, node_id: str = "21"
) -> tuple[str, str, str] | None:
    if not isinstance(history, dict) or not history:
        return None
    job = history.get(prompt_id)
    if job is None:
        job = next(iter(history.values())) if history else None
    if not isinstance(job, dict):
        return None
    outputs = job.get("outputs")
    if not isinstance(outputs, dict):
        return None
    preferred_node_ids: list[str] = []
    for candidate in (str(node_id), "21"):
        if candidate not in preferred_node_ids:
            preferred_node_ids.append(candidate)

    candidate_nodes = [outputs.get(node_key) for node_key in preferred_node_ids]
    candidate_nodes.extend(outputs.values())

    for node in candidate_nodes:
        if not isinstance(node, dict):
            continue
        audio_items = node.get("audio")
        if not isinstance(audio_items, list) or not audio_items:
            continue
        first = audio_items[0]
        if not isinstance(first, dict):
            continue
        filename = str(first.get("filename") or "").strip()
        if not filename:
            continue
        subfolder = str(first.get("subfolder") or "").strip()
        file_type = str(first.get("type") or "output").strip() or "output"
        return filename, subfolder, file_type
    return None


def comfy_request_json(
    *,
    comfy_url: str,
    path: str,
    method: str = "GET",
    payload: dict | None = None,
) -> dict:
    url = f"{comfy_url.rstrip('/')}{path}"
    code, body = http_json_request(method, url, payload=payload, timeout=120.0)
    if not (200 <= code < 300):
        raise RuntimeError(f"ComfyUI request failed (HTTP {code})")
    parsed = json.loads(body or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("ComfyUI response is not object")
    return parsed


def comfy_download_file(
    *, comfy_url: str, filename: str, subfolder: str, file_type: str
) -> bytes:
    query = urlencode({"filename": filename, "subfolder": subfolder, "type": file_type})
    url = f"{comfy_url.rstrip('/')}/view?{query}"
    opener = request.build_opener(request.ProxyHandler({}))
    req = request.Request(url, method="GET")
    with opener.open(req, timeout=300) as resp:
        if int(resp.getcode() or 200) >= 400:
            raise RuntimeError("ComfyUI download failed")
        return resp.read()


def comfy_clear_queue(comfy_url: str) -> bool:
    url = f"{comfy_url.rstrip('/')}/queue"
    opener = request.build_opener(request.ProxyHandler({}))
    req = request.Request(
        url,
        data=json.dumps({"clear": True}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with opener.open(req, timeout=15) as resp:
        return int(resp.getcode() or 200) < 400


def comfy_interrupt_execution(comfy_url: str) -> bool:
    url = f"{comfy_url.rstrip('/')}/interrupt"
    opener = request.build_opener(request.ProxyHandler({}))
    req = request.Request(url, data=b"{}", method="POST")
    with opener.open(req, timeout=15) as resp:
        return int(resp.getcode() or 200) < 400


def save_capture_chapter(body: dict) -> tuple[int, dict]:
    try:
        novel_id = int(body.get("novel_id"))
        chapter_num = int(body.get("chapter_num"))
    except (TypeError, ValueError):
        return 400, {"error": "novel_id and chapter_num are required"}

    title = str(body.get("title") or "").strip()
    content = str(body.get("content") or "")
    if not title or not content.strip():
        return 400, {"error": "title and content are required"}

    conn = db_conn()
    novel = conn.execute(
        "SELECT english_dir FROM novels WHERE id=?", (novel_id,)
    ).fetchone()
    if not novel:
        conn.close()
        return 404, {"error": "novel not found"}

    english_dir = str(novel["english_dir"])
    ensure_novel_dirs(english_dir)
    safe_name = (
        re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title).strip("_")
        or f"chapter_{chapter_num}"
    )
    rel_path = (
        Path("novel") / english_dir / "text" / f"{chapter_num:03d}_{safe_name}.txt"
    )
    abs_path = ROOT_DIR / rel_path
    abs_path.write_text(content, encoding="utf-8")
    word_count = len(re.sub(r"\s+", "", content))

    chapter = conn.execute(
        "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
        (novel_id, chapter_num),
    ).fetchone()
    if chapter:
        conn.execute(
            """
            UPDATE chapters
            SET title=?,word_count=?,text_file_path=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (title, word_count, db_rel_path(rel_path), int(chapter["id"])),
        )
    else:
        conn.execute(
            """
            INSERT INTO chapters (novel_id,chapter_num,title,word_count,text_file_path)
            VALUES (?,?,?,?,?)
            """,
            (novel_id, chapter_num, title, word_count, db_rel_path(rel_path)),
        )
    conn.execute(
        """
        UPDATE novels
        SET chapter_count=(SELECT COUNT(1) FROM chapters WHERE novel_id=?),
            total_words=(SELECT COALESCE(SUM(word_count),0) FROM chapters WHERE novel_id=?),
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (novel_id, novel_id, novel_id),
    )
    conn.execute(
        """
        INSERT INTO capture_upload_logs (novel_id,chapter_num,chapter_title,word_count)
        VALUES (?, ?, ?, ?)
        """,
        (novel_id, chapter_num, title, word_count),
    )
    conn.commit()
    conn.close()
    return 200, {"status": "saved", "saved_file": db_rel_path(rel_path)}


def finalize_capture(body: dict) -> tuple[int, dict]:
    try:
        novel_id = int(body.get("novel_id"))
    except (TypeError, ValueError):
        return 400, {"error": "novel_id is required"}
    conn = db_conn()
    row = conn.execute("SELECT id FROM novels WHERE id=?", (novel_id,)).fetchone()
    if not row:
        conn.close()
        return 404, {"error": "novel not found"}
    conn.execute(
        """
        UPDATE novels
        SET chapter_count=(SELECT COUNT(1) FROM chapters WHERE novel_id=?),
            total_words=(SELECT COALESCE(SUM(word_count),0) FROM chapters WHERE novel_id=?),
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (novel_id, novel_id, novel_id),
    )
    conn.commit()
    conn.close()
    return 200, {"status": "ok"}


def parse_bind_url(raw_url: str) -> tuple[str, int] | None:
    text = (raw_url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = f"http://{text}"
    parsed = urlparse(text)
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        return None
    return host, int(port)


class CaptureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def set_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.set_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.set_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size).decode("utf-8", errors="ignore")
        return json.loads(raw) if raw else {}

    def do_GET(self) -> None:
        if self.path.split("?")[0] == "/health":
            self.send_json({"status": "ok"})
            return
        self.send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        route = self.path.split("?")[0]
        body = self.read_json()
        if route == "/chapter":
            status, payload = save_capture_chapter(body)
            self.send_json(payload, status)
            return
        if route == "/finalize":
            status, payload = finalize_capture(body)
            self.send_json(payload, status)
            return
        self.send_json({"error": "not found"}, 404)


def start_capture_service(bind_url: str) -> tuple[bool, str]:
    global CAPTURE_SERVER, CAPTURE_THREAD, CAPTURE_BIND
    bind = parse_bind_url(bind_url)
    if not bind:
        return False, "服务地址格式错误"
    host, port = bind

    with CAPTURE_LOCK:
        if CAPTURE_SERVER and CAPTURE_THREAD and CAPTURE_THREAD.is_alive():
            if CAPTURE_BIND == bind:
                return True, "抓取服务已在运行"
            if CAPTURE_BIND:
                return False, f"抓取服务正在 {CAPTURE_BIND[0]}:{CAPTURE_BIND[1]} 运行"
            return False, "抓取服务正在运行"

        try:
            server = ThreadingHTTPServer((host, port), CaptureHandler)
        except OSError as exc:
            return False, f"启动失败: {exc}"

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        CAPTURE_SERVER = server
        CAPTURE_THREAD = thread
        CAPTURE_BIND = bind
        return True, f"已启动于 {host}:{port}"


def stop_capture_service() -> tuple[bool, str]:
    global CAPTURE_SERVER, CAPTURE_THREAD, CAPTURE_BIND
    with CAPTURE_LOCK:
        if not CAPTURE_SERVER:
            return True, "抓取服务未运行"
        try:
            CAPTURE_SERVER.shutdown()
            CAPTURE_SERVER.server_close()
        finally:
            CAPTURE_SERVER = None
            CAPTURE_THREAD = None
            CAPTURE_BIND = None
    return True, "抓取服务已停止"


def capture_service_status() -> dict:
    running = bool(CAPTURE_SERVER and CAPTURE_THREAD and CAPTURE_THREAD.is_alive())
    if CAPTURE_BIND and running:
        host, port = CAPTURE_BIND
        return {"running": True, "host": host, "port": port}
    return {"running": False, "host": "", "port": 0}


def advance_status(conn: sqlite3.Connection, table: str) -> None:
    running = conn.execute(
        f"SELECT id,progress,chapter_id FROM {table} WHERE status='running' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if running:
        progress = int(running["progress"] or 0)
        next_progress = min(100, progress + 25)
        next_status = "completed" if next_progress >= 100 else "running"
        conn.execute(
            f"UPDATE {table} SET status=?,progress=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (next_status, next_progress, int(running["id"])),
        )
        if next_status == "completed" and running["chapter_id"] is not None:
            if table == "json_tasks":
                conn.execute(
                    "UPDATE chapters SET has_json=1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (int(running["chapter_id"]),),
                )
        return

    pending = conn.execute(
        f"SELECT id FROM {table} WHERE status='pending' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if pending:
        conn.execute(
            f"UPDATE {table} SET status='running',progress=8,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(pending["id"]),),
        )


def task_worker_loop() -> None:
    generation = TASK_WORKER_GENERATION
    while not TASK_WORKER_STOP.is_set() and generation == TASK_WORKER_GENERATION:
        touch_task_worker_heartbeat()
        has_json_work = False
        with TASK_WORKER_LOCK:
            try:
                has_json_work = run_json_queue_once()
            except Exception as exc:
                print(f"[task-worker] json queue error: {exc}")
        touch_task_worker_heartbeat(made_progress=has_json_work)
        TASK_WORKER_STOP.wait(1.0 if has_json_work else 3.0)


def kick_line_audio_queue_once() -> None:
    ensure_line_audio_worker()


def line_audio_worker_loop() -> None:
    from .line_audio import run_line_audio_queue_once

    generation = LINE_AUDIO_WORKER_GENERATION
    while not LINE_AUDIO_WORKER_STOP.is_set() and generation == LINE_AUDIO_WORKER_GENERATION:
        touch_line_audio_worker_heartbeat()
        has_line_audio_work = False
        try:
            has_line_audio_work = run_line_audio_queue_once()
        except Exception as exc:
            print(f"[line-audio-worker] queue error: {exc}")
        touch_line_audio_worker_heartbeat(made_progress=has_line_audio_work)
        LINE_AUDIO_WORKER_STOP.wait(1.0 if has_line_audio_work else 3.0)


def audio_asr_worker_loop() -> None:
    from .audio_asr import run_audio_asr_queue_once

    generation = AUDIO_ASR_WORKER_GENERATION
    while not AUDIO_ASR_WORKER_STOP.is_set() and generation == AUDIO_ASR_WORKER_GENERATION:
        touch_audio_asr_worker_heartbeat()
        has_audio_asr_work = False
        try:
            has_audio_asr_work = run_audio_asr_queue_once()
        except Exception as exc:
            print(f"[audio-asr-worker] queue error: {exc}")
        touch_audio_asr_worker_heartbeat(made_progress=has_audio_asr_work)
        AUDIO_ASR_WORKER_STOP.wait(1.0 if has_audio_asr_work else 3.0)


def nsfw_review_worker_loop() -> None:
    from .nsfw_review import run_nsfw_review_queue_once

    generation = NSFW_REVIEW_WORKER_GENERATION
    while not NSFW_REVIEW_WORKER_STOP.is_set() and generation == NSFW_REVIEW_WORKER_GENERATION:
        touch_nsfw_review_worker_heartbeat()
        has_nsfw_review_work = False
        try:
            has_nsfw_review_work = run_nsfw_review_queue_once()
        except Exception as exc:
            print(f"[nsfw-review-worker] queue error: {exc}")
        touch_nsfw_review_worker_heartbeat(made_progress=has_nsfw_review_work)
        NSFW_REVIEW_WORKER_STOP.wait(1.0 if has_nsfw_review_work else 3.0)


def ensure_line_audio_worker() -> None:
    global LINE_AUDIO_WORKER_THREAD, LINE_AUDIO_WORKER_GENERATION, LINE_AUDIO_WORKER_HEARTBEAT_TS
    now = time.time()
    if LINE_AUDIO_WORKER_THREAD and LINE_AUDIO_WORKER_THREAD.is_alive():
        if LINE_AUDIO_WORKER_HEARTBEAT_TS > 0 and now - LINE_AUDIO_WORKER_HEARTBEAT_TS > LINE_AUDIO_WORKER_STALE_SECONDS:
            print("[line-audio-worker] heartbeat stale, restarting worker")
            LINE_AUDIO_WORKER_GENERATION += 1
            LINE_AUDIO_WORKER_STOP.clear()
            LINE_AUDIO_WORKER_THREAD = threading.Thread(target=line_audio_worker_loop, daemon=True)
            LINE_AUDIO_WORKER_THREAD.start()
        return
    LINE_AUDIO_WORKER_STOP.clear()
    LINE_AUDIO_WORKER_GENERATION += 1
    LINE_AUDIO_WORKER_THREAD = threading.Thread(target=line_audio_worker_loop, daemon=True)
    LINE_AUDIO_WORKER_THREAD.start()


def ensure_audio_asr_worker() -> None:
    global AUDIO_ASR_WORKER_THREAD, AUDIO_ASR_WORKER_GENERATION, AUDIO_ASR_WORKER_HEARTBEAT_TS
    now = time.time()
    if AUDIO_ASR_WORKER_THREAD and AUDIO_ASR_WORKER_THREAD.is_alive():
        if AUDIO_ASR_WORKER_HEARTBEAT_TS > 0 and now - AUDIO_ASR_WORKER_HEARTBEAT_TS > AUDIO_ASR_WORKER_STALE_SECONDS:
            print("[audio-asr-worker] heartbeat stale, restarting worker")
            AUDIO_ASR_WORKER_GENERATION += 1
            AUDIO_ASR_WORKER_STOP.clear()
            AUDIO_ASR_WORKER_THREAD = threading.Thread(target=audio_asr_worker_loop, daemon=True)
            AUDIO_ASR_WORKER_THREAD.start()
        return
    AUDIO_ASR_WORKER_STOP.clear()
    AUDIO_ASR_WORKER_GENERATION += 1
    AUDIO_ASR_WORKER_THREAD = threading.Thread(target=audio_asr_worker_loop, daemon=True)
    AUDIO_ASR_WORKER_THREAD.start()


def ensure_nsfw_review_worker() -> None:
    global NSFW_REVIEW_WORKER_THREAD, NSFW_REVIEW_WORKER_GENERATION, NSFW_REVIEW_WORKER_HEARTBEAT_TS
    now = time.time()
    if NSFW_REVIEW_WORKER_THREAD and NSFW_REVIEW_WORKER_THREAD.is_alive():
        if NSFW_REVIEW_WORKER_HEARTBEAT_TS > 0 and now - NSFW_REVIEW_WORKER_HEARTBEAT_TS > NSFW_REVIEW_WORKER_STALE_SECONDS:
            print("[nsfw-review-worker] heartbeat stale, restarting worker")
            NSFW_REVIEW_WORKER_GENERATION += 1
            NSFW_REVIEW_WORKER_STOP.clear()
            NSFW_REVIEW_WORKER_THREAD = threading.Thread(target=nsfw_review_worker_loop, daemon=True)
            NSFW_REVIEW_WORKER_THREAD.start()
        return
    NSFW_REVIEW_WORKER_STOP.clear()
    NSFW_REVIEW_WORKER_GENERATION += 1
    NSFW_REVIEW_WORKER_THREAD = threading.Thread(target=nsfw_review_worker_loop, daemon=True)
    NSFW_REVIEW_WORKER_THREAD.start()


def ensure_task_worker() -> None:
    global TASK_WORKER_THREAD, TASK_WORKER_GENERATION, TASK_WORKER_HEARTBEAT_TS
    now = time.time()
    if TASK_WORKER_THREAD and TASK_WORKER_THREAD.is_alive():
        if TASK_WORKER_HEARTBEAT_TS > 0 and now - TASK_WORKER_HEARTBEAT_TS > TASK_WORKER_STALE_SECONDS:
            print("[task-worker] heartbeat stale, restarting worker")
            TASK_WORKER_GENERATION += 1
            TASK_WORKER_STOP.clear()
            TASK_WORKER_THREAD = threading.Thread(target=task_worker_loop, daemon=True)
            TASK_WORKER_THREAD.start()
        return
    TASK_WORKER_STOP.clear()
    TASK_WORKER_GENERATION += 1
    TASK_WORKER_THREAD = threading.Thread(target=task_worker_loop, daemon=True)
    TASK_WORKER_THREAD.start()


def restart_task_worker() -> None:
    global TASK_WORKER_THREAD, TASK_WORKER_GENERATION, TASK_WORKER_HEARTBEAT_TS, TASK_WORKER_LAST_PROGRESS_TS
    TASK_WORKER_STOP.set()
    TASK_WORKER_GENERATION += 1
    TASK_WORKER_STOP.clear()
    TASK_WORKER_HEARTBEAT_TS = 0.0
    TASK_WORKER_LAST_PROGRESS_TS = 0.0
    TASK_WORKER_THREAD = threading.Thread(target=task_worker_loop, daemon=True)
    TASK_WORKER_THREAD.start()


def restart_line_audio_worker() -> None:
    global LINE_AUDIO_WORKER_THREAD, LINE_AUDIO_WORKER_GENERATION, LINE_AUDIO_WORKER_HEARTBEAT_TS, LINE_AUDIO_WORKER_LAST_PROGRESS_TS
    LINE_AUDIO_WORKER_STOP.set()
    LINE_AUDIO_WORKER_GENERATION += 1
    LINE_AUDIO_WORKER_STOP.clear()
    LINE_AUDIO_WORKER_HEARTBEAT_TS = 0.0
    LINE_AUDIO_WORKER_LAST_PROGRESS_TS = 0.0
    LINE_AUDIO_WORKER_THREAD = threading.Thread(target=line_audio_worker_loop, daemon=True)
    LINE_AUDIO_WORKER_THREAD.start()


def restart_audio_asr_worker() -> None:
    global AUDIO_ASR_WORKER_THREAD, AUDIO_ASR_WORKER_GENERATION, AUDIO_ASR_WORKER_HEARTBEAT_TS, AUDIO_ASR_WORKER_LAST_PROGRESS_TS
    AUDIO_ASR_WORKER_STOP.set()
    AUDIO_ASR_WORKER_GENERATION += 1
    AUDIO_ASR_WORKER_STOP.clear()
    AUDIO_ASR_WORKER_HEARTBEAT_TS = 0.0
    AUDIO_ASR_WORKER_LAST_PROGRESS_TS = 0.0
    AUDIO_ASR_WORKER_THREAD = threading.Thread(target=audio_asr_worker_loop, daemon=True)
    AUDIO_ASR_WORKER_THREAD.start()


def restart_nsfw_review_worker() -> None:
    global NSFW_REVIEW_WORKER_THREAD, NSFW_REVIEW_WORKER_GENERATION, NSFW_REVIEW_WORKER_HEARTBEAT_TS, NSFW_REVIEW_WORKER_LAST_PROGRESS_TS
    NSFW_REVIEW_WORKER_STOP.set()
    NSFW_REVIEW_WORKER_GENERATION += 1
    NSFW_REVIEW_WORKER_STOP.clear()
    NSFW_REVIEW_WORKER_HEARTBEAT_TS = 0.0
    NSFW_REVIEW_WORKER_LAST_PROGRESS_TS = 0.0
    NSFW_REVIEW_WORKER_THREAD = threading.Thread(target=nsfw_review_worker_loop, daemon=True)
    NSFW_REVIEW_WORKER_THREAD.start()


def get_task_worker_status() -> dict:
    now = time.time()
    heartbeat_age = max(0.0, now - TASK_WORKER_HEARTBEAT_TS) if TASK_WORKER_HEARTBEAT_TS > 0 else -1.0
    progress_age = max(0.0, now - TASK_WORKER_LAST_PROGRESS_TS) if TASK_WORKER_LAST_PROGRESS_TS > 0 else -1.0
    state = "stopped"
    if TASK_WORKER_THREAD and TASK_WORKER_THREAD.is_alive():
        state = "stale" if (TASK_WORKER_HEARTBEAT_TS > 0 and heartbeat_age > TASK_WORKER_STALE_SECONDS) else "running"
    return {
        "state": state,
        "heartbeatAgeSeconds": round(heartbeat_age, 1) if heartbeat_age >= 0 else None,
        "progressAgeSeconds": round(progress_age, 1) if progress_age >= 0 else None,
        "generation": TASK_WORKER_GENERATION,
    }


def get_line_audio_worker_status() -> dict:
    now = time.time()
    heartbeat_age = max(0.0, now - LINE_AUDIO_WORKER_HEARTBEAT_TS) if LINE_AUDIO_WORKER_HEARTBEAT_TS > 0 else -1.0
    progress_age = max(0.0, now - LINE_AUDIO_WORKER_LAST_PROGRESS_TS) if LINE_AUDIO_WORKER_LAST_PROGRESS_TS > 0 else -1.0
    state = "stopped"
    if LINE_AUDIO_WORKER_THREAD and LINE_AUDIO_WORKER_THREAD.is_alive():
        state = "stale" if (LINE_AUDIO_WORKER_HEARTBEAT_TS > 0 and heartbeat_age > LINE_AUDIO_WORKER_STALE_SECONDS) else "running"
    return {
        "state": state,
        "heartbeatAgeSeconds": round(heartbeat_age, 1) if heartbeat_age >= 0 else None,
        "progressAgeSeconds": round(progress_age, 1) if progress_age >= 0 else None,
        "generation": LINE_AUDIO_WORKER_GENERATION,
    }


def get_audio_asr_worker_status() -> dict:
    now = time.time()
    heartbeat_age = max(0.0, now - AUDIO_ASR_WORKER_HEARTBEAT_TS) if AUDIO_ASR_WORKER_HEARTBEAT_TS > 0 else -1.0
    progress_age = max(0.0, now - AUDIO_ASR_WORKER_LAST_PROGRESS_TS) if AUDIO_ASR_WORKER_LAST_PROGRESS_TS > 0 else -1.0
    state = "stopped"
    if AUDIO_ASR_WORKER_THREAD and AUDIO_ASR_WORKER_THREAD.is_alive():
        state = "stale" if (AUDIO_ASR_WORKER_HEARTBEAT_TS > 0 and heartbeat_age > AUDIO_ASR_WORKER_STALE_SECONDS) else "running"
    return {
        "state": state,
        "heartbeatAgeSeconds": round(heartbeat_age, 1) if heartbeat_age >= 0 else None,
        "progressAgeSeconds": round(progress_age, 1) if progress_age >= 0 else None,
        "generation": AUDIO_ASR_WORKER_GENERATION,
    }


def get_nsfw_review_worker_status() -> dict:
    now = time.time()
    heartbeat_age = max(0.0, now - NSFW_REVIEW_WORKER_HEARTBEAT_TS) if NSFW_REVIEW_WORKER_HEARTBEAT_TS > 0 else -1.0
    progress_age = max(0.0, now - NSFW_REVIEW_WORKER_LAST_PROGRESS_TS) if NSFW_REVIEW_WORKER_LAST_PROGRESS_TS > 0 else -1.0
    state = "stopped"
    if NSFW_REVIEW_WORKER_THREAD and NSFW_REVIEW_WORKER_THREAD.is_alive():
        state = "stale" if (NSFW_REVIEW_WORKER_HEARTBEAT_TS > 0 and heartbeat_age > NSFW_REVIEW_WORKER_STALE_SECONDS) else "running"
    return {
        "state": state,
        "heartbeatAgeSeconds": round(heartbeat_age, 1) if heartbeat_age >= 0 else None,
        "progressAgeSeconds": round(progress_age, 1) if progress_age >= 0 else None,
        "generation": NSFW_REVIEW_WORKER_GENERATION,
    }


def comfy_upload_input_file(filename: str, data: bytes) -> dict:
    """上传文件到 ComfyUI input 目录"""
    conn = db_conn()
    settings = fetch_settings(conn)
    conn.close()
    comfy_url = str(settings.get("comfyUrl") or "").strip()
    if not comfy_url:
        raise RuntimeError("ComfyUI URL is not configured")

    # 使用与旧jpm相同的方式：绕过代理
    import uuid

    opener = request.build_opener(request.ProxyHandler({}))
    boundary = f"----OpenCode{uuid.uuid4().hex}"
    safe_name = Path(str(filename or "sample.flac")).name or "sample.flac"

    def part(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    body = bytearray()
    body.extend(part("type", "input"))
    body.extend(part("overwrite", "true"))
    body.extend(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{safe_name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(data)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    # 尝试多个上传端点
    last_error: Exception | None = None
    endpoints = ["/upload/image", "/upload/audio", "/upload"]

    for path in endpoints:
        req = request.Request(
            f"{comfy_url.rstrip('/')}{path}",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with opener.open(req, timeout=300) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            payload = json.loads(raw) if raw else {}
            if isinstance(payload, dict):
                return payload
        except Exception as exc:
            last_error = exc
            continue

    raise RuntimeError(f"ComfyUI upload failed: {last_error}")
