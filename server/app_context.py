from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "novels.db"
NOVEL_DIR = ROOT_DIR / "novel"
PROMPTS_DIR = ROOT_DIR / "prompts"
WORKFLOWS_DIR = ROOT_DIR / "workflows"
SYSTEM_PROMPT_FILE = PROMPTS_DIR / "xhz_system_prompt.txt"
SYSTEM_PROMPT_NAME = "系统提示词"
SYSTEM_PROMPT_DESC = "系统内置"
DEFAULT_SYSTEM_PROMPT_CONTENT = "请将章回文本拆分为 role_list 与 juben 的 JSON 结构。"

SYSTEM_PROMPTS = [
    {
        "file": SYSTEM_PROMPT_FILE,
        "name": SYSTEM_PROMPT_NAME,
        "description": SYSTEM_PROMPT_DESC,
        "default_content": DEFAULT_SYSTEM_PROMPT_CONTENT,
        "legacy_names": ["古本水浒传系统提示词", "古本水浒传系统Prompt"],
    },
    {
        "file": PROMPTS_DIR / "fish_audio_s2_system_prompt.txt",
        "name": "FishAudioS2支持情绪提示词",
        "description": "系统内置，适用于 FishAudioS2 情绪标签脚本输出",
        "default_content": "请将章回文本拆分为 role_list、juben 与 fish_juben 的 JSON 结构。",
        "legacy_names": [],
    },
]

# 系统工作流定义
SYSTEM_WORKFLOWS = [
    {
        "file": WORKFLOWS_DIR / "voice_transcribe_workflow.json",
        "name": "提取声音文本",
        "description": "系统内置，使用 Whisper 提取音频中的文本",
        "workflow_type": "voice_transcribe",
        "workflow_io_config": {
            "inputs": {"audioFile": {"nodeId": "2"}},
            "outputs": {"textOutput": {"nodeId": "4"}},
        },
    },
    {
        "file": WORKFLOWS_DIR / "line_audio_workflow.json",
        "name": "生成台词音频",
        "description": "系统内置，使用 FishS2 Voice Clone 生成台词音频",
        "workflow_type": "line_audio",
        "workflow_io_config": {
            "inputs": {
                "referenceAudio": {"nodeId": "27"},
                "lineText": {"nodeId": "33"},
                "referenceText": {"nodeId": "40"},
            },
            "outputs": {"audioFile": {"nodeId": "41"}},
        },
    },
    {
        "file": WORKFLOWS_DIR / "line_audio_workflow_qwen3-tts.json",
        "name": "Qwen3 TTS 生成台词音频【低显存】",
        "description": "系统内置，使用 Qwen3 TTS 生成台词音频（低显存）",
        "workflow_type": "line_audio",
        "workflow_io_config": {
            "inputs": {
                "referenceAudio": {"nodeId": "6"},
                "lineText": {"nodeId": "7"},
                "referenceText": {"nodeId": "8"},
            },
            "outputs": {"audioFile": {"nodeId": "4"}},
        },
    },
    {
        "file": WORKFLOWS_DIR / "voice_sample_workflow.json",
        "name": "生成示例音频",
        "description": "系统内置，使用 Qwen3-TTS VoiceDesign 生成示例音频",
        "workflow_type": "voice_sample",
        "workflow_io_config": {
            "inputs": {
                "lineText": {"nodeId": "7"},
                "voiceDescription": {"nodeId": "6"},
            },
            "outputs": {"audioFile": {"nodeId": "9"}},
        },
    },
    {
        "file": WORKFLOWS_DIR / "voice_transcribe_workflow_qwen3-asr.json",
        "name": "Qwen3-ASR提取声音文本【低显存】",
        "description": "系统内置，使用 Qwen3-ASR 提取声音文本（低显存）",
        "workflow_type": "voice_transcribe",
        "workflow_io_config": {
            "inputs": {"audioFile": {"nodeId": "12"}},
            "outputs": {"textOutput": {"nodeId": "13"}},
        },
    },
]


def migrate_novels_table(conn: sqlite3.Connection) -> None:
    """迁移：为 novels 表添加新的工作流字段"""
    # 检查字段是否存在
    columns = conn.execute("PRAGMA table_info(novels)").fetchall()
    column_names = [col[1] for col in columns]

    # 添加 voice_sample_workflow_id 字段
    if "voice_sample_workflow_id" not in column_names:
        conn.execute(
            "ALTER TABLE novels ADD COLUMN voice_sample_workflow_id INTEGER REFERENCES comfy_workflows(id)"
        )

    # 添加 line_audio_workflow_id 字段
    if "line_audio_workflow_id" not in column_names:
        conn.execute(
            "ALTER TABLE novels ADD COLUMN line_audio_workflow_id INTEGER REFERENCES comfy_workflows(id)"
        )

    # 添加 voice_transcribe_workflow_id 字段
    if "voice_transcribe_workflow_id" not in column_names:
        conn.execute(
            "ALTER TABLE novels ADD COLUMN voice_transcribe_workflow_id INTEGER REFERENCES comfy_workflows(id)"
        )

    # 添加总音频时长缓存字段（秒）
    if "total_audio_duration_seconds" not in column_names:
        conn.execute(
            "ALTER TABLE novels ADD COLUMN total_audio_duration_seconds REAL NOT NULL DEFAULT 0"
        )


def migrate_chapters_table(conn: sqlite3.Connection) -> None:
    """迁移：为 chapters 表添加章节音频时长缓存字段"""
    columns = conn.execute("PRAGMA table_info(chapters)").fetchall()
    column_names = [col[1] for col in columns]

    if "audio_duration_seconds" not in column_names:
        conn.execute(
            "ALTER TABLE chapters ADD COLUMN audio_duration_seconds REAL NOT NULL DEFAULT 0"
        )

    if "audio_duration_md5" not in column_names:
        conn.execute(
            "ALTER TABLE chapters ADD COLUMN audio_duration_md5 TEXT NOT NULL DEFAULT ''"
        )


def migrate_line_audio_tasks_table(conn: sqlite3.Connection) -> None:
    """迁移：为 line_audio_tasks 表添加调度字段"""
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='line_audio_tasks'"
    ).fetchone()
    if not tables:
        return
    columns = conn.execute("PRAGMA table_info(line_audio_tasks)").fetchall()
    column_names = [col[1] for col in columns]
    if "scheduled_at" not in column_names:
        conn.execute("ALTER TABLE line_audio_tasks ADD COLUMN scheduled_at DATETIME")


def migrate_workflow_io_config_column(conn: sqlite3.Connection) -> None:
    columns = conn.execute("PRAGMA table_info(comfy_workflows)").fetchall()
    column_names = [col[1] for col in columns]
    if "workflow_io_config" not in column_names:
        conn.execute(
            "ALTER TABLE comfy_workflows ADD COLUMN workflow_io_config TEXT NOT NULL DEFAULT '{}'"
        )
    if "workflow_log_enabled" not in column_names:
        conn.execute(
            "ALTER TABLE comfy_workflows ADD COLUMN workflow_log_enabled INTEGER NOT NULL DEFAULT 1"
        )


def migrate_workflow_logs_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS comfy_workflow_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_category TEXT NOT NULL DEFAULT '',
            workflow_name TEXT NOT NULL DEFAULT '',
            workflow_json TEXT NOT NULL DEFAULT '{}',
            error_log TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def migrate_json_tasks_table(conn: sqlite3.Connection) -> None:
    columns = conn.execute("PRAGMA table_info(json_tasks)").fetchall()
    column_names = [col[1] for col in columns]
    if "started_at" not in column_names:
        conn.execute("ALTER TABLE json_tasks ADD COLUMN started_at DATETIME")


def migrate_task_batches_table(conn: sqlite3.Connection) -> None:
    columns = conn.execute("PRAGMA table_info(task_batches)").fetchall()
    column_names = [col[1] for col in columns]
    if "retry_count" not in column_names:
        conn.execute(
            "ALTER TABLE task_batches ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
        )


def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=12.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 12000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS capture_upload_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL,
            chapter_num INTEGER NOT NULL,
            chapter_title TEXT NOT NULL,
            word_count INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE
        )
        """
    )
    # 执行迁移
    migrate_novels_table(conn)
    migrate_chapters_table(conn)
    migrate_line_audio_tasks_table(conn)
    migrate_json_tasks_table(conn)
    migrate_task_batches_table(conn)
    migrate_workflow_io_config_column(conn)
    migrate_workflow_logs_table(conn)
    return conn
