from __future__ import annotations

import json
import ipaddress
import hashlib
import mimetypes
import re
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


def ensure_novel_dirs(english_dir: str) -> None:
    (NOVEL_DIR / english_dir / "text").mkdir(parents=True, exist_ok=True)
    (NOVEL_DIR / english_dir / "audio").mkdir(parents=True, exist_ok=True)


def validate_english_dir(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_]{1,25}", value or ""))


def fetch_prompts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id,name,prompt_type,description,content,created_at,updated_at
        FROM json_prompts
        ORDER BY CASE WHEN prompt_type='system' THEN 0 ELSE 1 END, id DESC
        """
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "type": str(r["prompt_type"]),
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
            INSERT INTO json_prompts (name,prompt_type,description,content)
            VALUES (?, 'system', ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                prompt_type='system',
                description=excluded.description,
                content=excluded.content,
                updated_at=CURRENT_TIMESTAMP
            WHERE json_prompts.prompt_type<>'system'
               OR json_prompts.description<>excluded.description
               OR json_prompts.content<>excluded.content
            """,
            (prompt_name, prompt_desc, content),
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
        SELECT id,name,workflow_type,description,json_text,created_at,updated_at
        FROM comfy_workflows
        ORDER BY CASE WHEN workflow_type='system' OR workflow_type='voice_transcribe' OR workflow_type='line_audio' OR workflow_type='voice_sample' THEN 0 ELSE 1 END, id DESC
        """
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "type": "system" if str(r["name"]) in system_workflow_names else "user",
            "workflowType": str(r["workflow_type"]),
            "name": str(r["name"]),
            "description": str(r["description"] or ""),
            "jsonText": str(r["json_text"]),
            "createdAt": str(r["created_at"]),
            "updatedAt": str(r["updated_at"]),
        }
        for r in rows
    ]


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
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # 复制数据
        conn.execute(
            """
            INSERT INTO comfy_workflows_new (id, name, workflow_type, description, json_text, created_at, updated_at)
            SELECT id, name, workflow_type, description, json_text, created_at, updated_at
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
            INSERT INTO comfy_workflows (name,workflow_type,description,json_text)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                workflow_type=excluded.workflow_type,
                description=excluded.description,
                json_text=excluded.json_text,
                updated_at=CURRENT_TIMESTAMP
            WHERE comfy_workflows.workflow_type<>excluded.workflow_type
               OR comfy_workflows.description<>excluded.description
               OR comfy_workflows.json_text<>excluded.json_text
            """,
            (
                wf_config["name"],
                wf_config["workflow_type"],
                wf_config["description"],
                json_text,
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
               n.prompt_id,n.workflow_id,n.voice_sample_workflow_id,n.line_audio_workflow_id,n.voice_transcribe_workflow_id,
               n.total_audio_duration_seconds,n.created_at,n.updated_at,
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
                "jsonProgress": json_progress,
                "audioProgress": audio_progress,
                "totalAudioDurationSeconds": float(
                    r["total_audio_duration_seconds"] or 0
                ),
                "storage": {
                    "txtBytes": txt_bytes,
                    "audioBytes": audio_bytes,
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
        SELECT t.id,t.novel_id,t.chapter_num,t.chapter_title,t.prompt_id,t.status,t.progress,t.updated_at,
               t.created_at,t.error_message,
               (SELECT COUNT(1) FROM task_batches b WHERE b.task_id=t.id) AS batch_total,
               (SELECT COUNT(1) FROM task_batches b WHERE b.task_id=t.id AND b.status='completed') AS batch_done,
               (SELECT COUNT(1) FROM task_batches b WHERE b.task_id=t.id AND b.status='failed') AS batch_failed,
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
            "wordCount": int(r["chapter_word_count"] or 0),
            "status": str(r["status"]),
            "progress": int(r["progress"] or 0),
            "errorMessage": str(r["error_message"] or ""),
            "batchTotal": int(r["batch_total"] or 0),
            "batchDone": int(r["batch_done"] or 0),
            "batchFailed": int(r["batch_failed"] or 0),
            "createdAt": str(r["created_at"]),
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
    if batch_max_chars not in {0, 3500, 4000, 5000, 6000, 7000}:
        batch_max_chars = 3500
    try:
        num_ctx = int(kv.get("llm_num_ctx", "65536"))
    except (TypeError, ValueError):
        num_ctx = 65536
    if num_ctx not in {32768, 65536, 98304, 131072}:
        num_ctx = 65536
    keep_alive = str(kv.get("llm_keep_alive", "30m") or "30m").strip() or "30m"
    if keep_alive not in {"5m", "15m", "30m", "1h", "6h", "24h"}:
        keep_alive = "30m"

    llm = {
        "provider": kv.get("llm_provider", "grok"),
        "baseUrl": kv.get("llm_base_url", "https://api.x.ai/v1"),
        "model": kv.get("llm_model", "grok-2-latest"),
        "apiKey": kv.get("llm_api_key", ""),
        "temperature": float(kv.get("llm_temperature", "0.3")),
        "maxTokens": int(kv.get("llm_max_tokens", "8192")),
        "numCtx": num_ctx,
        "keepAlive": keep_alive,
        "batchMaxChars": batch_max_chars,
    }
    if str(llm.get("provider") or "").strip() == "deepseek":
        llm["maxTokens"] = min(int(llm.get("maxTokens") or 8192), 8192)
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
    }


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
            if attempt >= 2:
                if isinstance(exc, URLError):
                    raise RuntimeError(str(exc.reason)) from exc
                raise RuntimeError(str(exc)) from exc
            time.sleep(1.2 * (attempt + 1))
    if last_exc:
        raise RuntimeError(str(last_exc))
    raise RuntimeError("request failed")


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
    num_ctx: int = 65536,
    keep_alive: str = "30m",
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
        payload["options"] = {"num_ctx": int(num_ctx or 65536)}
        payload["keep_alive"] = str(keep_alive or "30m")
    try:
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
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


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
    if provider == "deepseek":
        max_tokens = min(max_tokens, 8192)
    num_ctx = int(llm.get("numCtx") or 65536)
    keep_alive = str(llm.get("keepAlive") or "30m").strip() or "30m"

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
    request_timeout = 180.0
    if provider == "ollama":
        payload["options"] = {"num_ctx": num_ctx}
        payload["keep_alive"] = keep_alive
        request_timeout = (
            1800.0 if num_ctx >= 65536 or len(chapter_text) > 12000 else 900.0
        )
    code, body = http_json_request(
        "POST",
        f"{base_url.rstrip('/')}/chat/completions",
        payload=payload,
        headers=headers,
        timeout=request_timeout,
        proxy_url=proxy_url,
    )

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
        raise RuntimeError(
            f"LLM request failed (HTTP {code})"
            + (f": {detail[:120]}" if detail else "")
        )

    parsed_body = json.loads(body or "{}")
    if not isinstance(parsed_body, dict):
        raise RuntimeError("LLM response is not object")
    content = extract_chat_content(parsed_body)
    if not content:
        raise RuntimeError("LLM response content is empty")
    return content


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

        chapter_text = read_chapter_text(text_file_path)
        if not chapter_text:
            raise RuntimeError("chapter text is empty or missing")

        raw_batch_max_chars = llm.get("batchMaxChars", 3500)
        if raw_batch_max_chars in (None, ""):
            raw_batch_max_chars = 3500
        batch_max_chars = int(raw_batch_max_chars)
        if batch_max_chars not in {0, 3500, 4000, 5000, 6000, 7000}:
            batch_max_chars = 3500
        batches = split_text_batches(chapter_text, max_chars=batch_max_chars)

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
            SET progress=10,model_name=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (model_name, task_id),
        )
        conn.commit()
        conn.close()

        parsed_outputs: list[dict] = []
        for idx, batch_text in enumerate(batches, start=1):
            conn = db_conn()
            conn.execute(
                """
                UPDATE task_batches
                SET status='processing',updated_at=CURRENT_TIMESTAMP
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
                parsed = parse_model_json(raw)
                parsed_outputs.append(parsed)

                conn = db_conn()
                conn.execute(
                    """
                    UPDATE task_batches
                    SET status='completed',llm_response_text=?,parsed_json_text=?,updated_at=CURRENT_TIMESTAMP
                    WHERE task_id=? AND batch_index=?
                    """,
                    (raw, json.dumps(parsed, ensure_ascii=False), task_id, idx),
                )
                conn.commit()
                conn.close()
            except Exception as exc:
                conn = db_conn()
                conn.execute(
                    """
                    UPDATE task_batches
                    SET status='failed',error_message=?,updated_at=CURRENT_TIMESTAMP
                    WHERE task_id=? AND batch_index=?
                    """,
                    (str(exc), task_id, idx),
                )
                conn.commit()
                conn.close()
                raise

        merged_obj = merge_batch_outputs(parsed_outputs)
        merged = json.dumps(merged_obj, ensure_ascii=False)

        conn = db_conn()
        conn.execute(
            """
            UPDATE json_tasks
            SET status='completed',progress=100,merged_result_json=?,error_message=NULL,
                model_name=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
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
        SET status='running',progress=5,error_message=NULL,updated_at=CURRENT_TIMESTAMP
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
    global TASK_WORKER_HEARTBEAT_TS, TASK_WORKER_LAST_PROGRESS_TS
    from .line_audio import run_line_audio_queue_once

    generation = TASK_WORKER_GENERATION
    while not TASK_WORKER_STOP.is_set() and generation == TASK_WORKER_GENERATION:
        TASK_WORKER_HEARTBEAT_TS = time.time()
        has_json_work = False
        has_line_audio_work = False
        with TASK_WORKER_LOCK:
            try:
                has_json_work = run_json_queue_once()
            except Exception as exc:
                print(f"[task-worker] json queue error: {exc}")
            try:
                has_line_audio_work = run_line_audio_queue_once()
            except Exception as exc:
                print(f"[task-worker] line audio queue error: {exc}")
        TASK_WORKER_HEARTBEAT_TS = time.time()
        if has_json_work or has_line_audio_work:
            TASK_WORKER_LAST_PROGRESS_TS = TASK_WORKER_HEARTBEAT_TS
        TASK_WORKER_STOP.wait(1.0 if (has_json_work or has_line_audio_work) else 3.0)


def _has_active_line_audio_tasks() -> bool:
    conn = db_conn()
    row = conn.execute(
        "SELECT COUNT(1) AS c FROM line_audio_tasks WHERE status IN ('running','processing')"
    ).fetchone()
    conn.close()
    return bool(row and int(row["c"] or 0) > 0)


def kick_line_audio_queue_once() -> None:
    global \
        TASK_WORKER_KICK_THREAD, \
        TASK_WORKER_HEARTBEAT_TS, \
        TASK_WORKER_LAST_PROGRESS_TS
    from .line_audio import run_line_audio_queue_once

    if TASK_WORKER_KICK_THREAD and TASK_WORKER_KICK_THREAD.is_alive():
        return

    def _runner() -> None:
        global TASK_WORKER_HEARTBEAT_TS, TASK_WORKER_LAST_PROGRESS_TS
        with TASK_WORKER_LOCK:
            TASK_WORKER_HEARTBEAT_TS = time.time()
            try:
                did_work = run_line_audio_queue_once()
                TASK_WORKER_HEARTBEAT_TS = time.time()
                if did_work:
                    TASK_WORKER_LAST_PROGRESS_TS = TASK_WORKER_HEARTBEAT_TS
            except Exception as exc:
                TASK_WORKER_HEARTBEAT_TS = time.time()
                print(f"[task-worker] line audio kick error: {exc}")

    TASK_WORKER_KICK_THREAD = threading.Thread(target=_runner, daemon=True)
    TASK_WORKER_KICK_THREAD.start()


def ensure_task_worker() -> None:
    global TASK_WORKER_THREAD, TASK_WORKER_GENERATION, TASK_WORKER_HEARTBEAT_TS
    stale_seconds = 15.0
    now = time.time()
    if TASK_WORKER_THREAD and TASK_WORKER_THREAD.is_alive():
        if (
            TASK_WORKER_HEARTBEAT_TS > 0
            and now - TASK_WORKER_HEARTBEAT_TS > stale_seconds
            and not _has_active_line_audio_tasks()
        ):
            print("[task-worker] heartbeat stale, restarting worker")
            TASK_WORKER_GENERATION += 1
            TASK_WORKER_THREAD = threading.Thread(target=task_worker_loop, daemon=True)
            TASK_WORKER_THREAD.start()
        return
    TASK_WORKER_STOP.clear()
    TASK_WORKER_GENERATION += 1
    TASK_WORKER_THREAD = threading.Thread(target=task_worker_loop, daemon=True)
    TASK_WORKER_THREAD.start()


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
