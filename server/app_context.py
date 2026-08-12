from __future__ import annotations

import sqlite3
import threading
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

ILLUSTRATION_PROMPT_LLM_DEFAULTS = {
    "illustration_scene": {
        "enabled": True,
        "llm": {
            "temperature": 0.2,
            "topP": 0.85,
            "maxTokens": 84000,
            "numCtx": 84000,
            "keepAlive": "30m",
            "unloadAfterCall": False,
            "batchTimeoutMinutes": 15,
            "think": True,
            "batchMaxChars": 0,
        },
    },
    "illustration_shot": {
        "enabled": True,
        "llm": {
            "temperature": 0.3,
            "topP": 0.85,
            "maxTokens": 84000,
            "numCtx": 84000,
            "keepAlive": "30m",
            "unloadAfterCall": False,
            "batchTimeoutMinutes": 15,
            "think": False,
            "batchMaxChars": 0,
        },
    },
    "illustration_prompt": {
        "enabled": True,
        "llm": {
            "temperature": 0.25,
            "topP": 0.85,
            "maxTokens": 84000,
            "numCtx": 84000,
            "keepAlive": "30m",
            "unloadAfterCall": False,
            "batchTimeoutMinutes": 15,
            "think": False,
            "batchMaxChars": 0,
        },
    },
}

ILLUSTRATION_MULTI_SCENE_PROMPT_LLM_DEFAULTS = {
    "enabled": True,
    "llm": {
        "temperature": 0.2,
        "topP": 0.85,
        "maxTokens": 84000,
        "numCtx": 98304,
        "keepAlive": "30m",
        "unloadAfterCall": True,
        "batchTimeoutMinutes": 10,
        "think": True,
        "batchMaxChars": 0,
    },
}

ILLUSTRATION_MULTI_PROMPT_LLM_DEFAULTS = {
    "enabled": True,
    "llm": {
        "temperature": 0.25,
        "topP": 0.85,
        "maxTokens": 76384,
        "numCtx": 98304,
        "keepAlive": "30m",
        "unloadAfterCall": True,
        "batchTimeoutMinutes": 20,
        "think": False,
        "batchMaxChars": 0,
    },
}

SUBTITLE_FIX_PROMPT_LLM_DEFAULTS = {
    "enabled": True,
    "llm": {
        "temperature": 0.1,
        "topP": 0.85,
        "maxTokens": 84000,
        "numCtx": 84000,
        "keepAlive": "30m",
        "unloadAfterCall": False,
        "batchTimeoutMinutes": 10,
        "think": False,
        "batchMaxChars": 0,
    },
}

ILLUSTRATION_PROMPT_OPTIMIZE_LLM_DEFAULTS = {
    "enabled": True,
    "llm": {
        "temperature": 0.2,
        "topP": 0.85,
        "maxTokens": 32768,
        "numCtx": 65536,
        "keepAlive": "30m",
        "unloadAfterCall": False,
        "batchTimeoutMinutes": 10,
        "think": False,
        "batchMaxChars": 0,
    },
}

SYSTEM_PROMPTS = [
    {
        "file": SYSTEM_PROMPT_FILE,
        "name": SYSTEM_PROMPT_NAME,
        "description": SYSTEM_PROMPT_DESC,
        "category": "json_parse",
        "default_content": DEFAULT_SYSTEM_PROMPT_CONTENT,
        "legacy_names": ["古本水浒传系统提示词", "古本水浒传系统Prompt"],
    },
    {
        "file": PROMPTS_DIR / "fish_audio_s2_system_prompt.txt",
        "name": "FishAudioS2支持情绪提示词",
        "description": "系统内置，适用于 FishAudioS2 情绪标签脚本输出",
        "category": "json_parse",
        "default_content": "请将章回文本拆分为 role_list、juben 与 fish_juben 的 JSON 结构。",
        "legacy_names": [],
    },
    {
        "file": PROMPTS_DIR / "nsfw_review_system_prompt.txt",
        "name": "NSFW审查提示词",
        "description": "系统内置，适用于小说章回NSFW内容审查",
        "category": "nsfw_review",
        "default_content": "请审查小说章回文本中的NSFW内容，并按JSON格式返回违规类型与原文句子。",
        "legacy_names": [],
    },
    {
        "file": PROMPTS_DIR / "illustration_scene_system_prompt.txt",
        "name": "插画-scene提示词",
        "description": "系统内置，适用于插画时间线scene.json生成",
        "category": "illustration_scene",
        "default_content": "请根据小说章节内容与ASR时间轴输出插画时间线scene.json。",
        "default_llm_settings": ILLUSTRATION_PROMPT_LLM_DEFAULTS["illustration_scene"],
        "legacy_names": [],
    },
    {
        "file": PROMPTS_DIR / "illustration_scene_multi_system_prompt.txt",
        "name": "插画-scene提示词-多图",
        "description": "系统内置，适用于插画时间线scene.json生成，平均40秒一图",
        "category": "illustration_scene",
        "default_content": "请根据小说章节内容与ASR时间轴输出平均40秒一图的插画时间线scene.json。",
        "default_llm_settings": ILLUSTRATION_MULTI_SCENE_PROMPT_LLM_DEFAULTS,
        "legacy_names": [],
    },
    {
        "file": PROMPTS_DIR / "illustration_shot_system_prompt.txt",
        "name": "插画-shot提示词",
        "description": "系统内置，适用于插画镜头方案shot.json生成",
        "category": "illustration_shot",
        "default_content": "请根据scene.json输出插画镜头方案shot.json。",
        "default_llm_settings": ILLUSTRATION_PROMPT_LLM_DEFAULTS["illustration_shot"],
        "legacy_names": [],
    },
    {
        "file": PROMPTS_DIR / "illustration_prompt_system_prompt.txt",
        "name": "插画-prompt提示词",
        "description": "系统内置，适用于AI绘画prompt.json生成",
        "category": "illustration_prompt",
        "default_content": "请根据scene.json与shot.json输出AI绘画prompt.json。",
        "default_llm_settings": ILLUSTRATION_PROMPT_LLM_DEFAULTS["illustration_prompt"],
        "legacy_names": [],
    },
    {
        "file": PROMPTS_DIR / "illustration_prompt_multi_system_prompt.txt",
        "name": "插画-prompt提示词-多图",
        "description": "系统内置，适用于AI绘画prompt.json生成，配合多图",
        "category": "illustration_prompt",
        "default_content": "请根据scene.json与shot.json输出配合多图的AI绘画prompt.json。",
        "default_llm_settings": ILLUSTRATION_MULTI_PROMPT_LLM_DEFAULTS,
        "legacy_names": [],
    },
    {
        "file": PROMPTS_DIR / "illustration_prompt_multi_no_negative_system_prompt.txt",
        "name": "插画-prompt提示词-多图（无negative）",
        "description": "基于 插画-prompt提示词 复制",
        "category": "illustration_prompt",
        "default_content": "请根据scene.json与shot.json输出配合多图的AI绘画prompt.json，negative保持为空。",
        "default_llm_settings": ILLUSTRATION_MULTI_PROMPT_LLM_DEFAULTS,
        "legacy_names": [],
    },
    {
        "file": PROMPTS_DIR / "subtitle_fix_system_prompt.txt",
        "name": "修复字幕提示词",
        "description": "系统内置，适用于根据小说正文修复ASR字幕错误",
        "category": "subtitle_fix",
        "default_content": "请根据小说正文校正ASR字幕，并只输出修正后的SRT内容。",
        "default_llm_settings": SUBTITLE_FIX_PROMPT_LLM_DEFAULTS,
        "legacy_names": [],
    },
    {
        "file": PROMPTS_DIR / "illustration_prompt_optimize_system_prompt.txt",
        "name": "插画-提示词优化",
        "description": "系统内置，适用于优化单个 AI 绘画 prompt.json 项",
        "category": "illustration_prompt_optimize",
        "default_content": "请优化输入的 prompt.json，使其更适合 AI 图片生成，并只输出完整 JSON。",
        "default_llm_settings": ILLUSTRATION_PROMPT_OPTIMIZE_LLM_DEFAULTS,
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
        "workflow_log_enabled": 0,
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
        "workflow_log_enabled": 0,
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
        "workflow_log_enabled": 0,
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
        "workflow_log_enabled": 0,
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
        "workflow_log_enabled": 0,
        "workflow_io_config": {
            "inputs": {"audioFile": {"nodeId": "12"}},
            "outputs": {"textOutput": {"nodeId": "13"}},
        },
    },
    {
        "file": WORKFLOWS_DIR / "audio_asr_workflow_qwen3-asr.json",
        "name": "提取音频ASR",
        "description": "系统内置，使用 Qwen3-ASR 提取章节音频ASR与时间轴",
        "workflow_type": "audio_asr",
        "workflow_log_enabled": 0,
        "workflow_io_config": {
            "inputs": {"audioFile": {"nodeId": "12"}},
            "outputs": {
                "textOutput": {"nodeId": "13"},
                "languageOutput": {"nodeId": "14"},
                "timestampsOutput": {"nodeId": "17"},
                "textListOutput": {"nodeId": "19"},
                "startTimesOutput": {"nodeId": "20"},
                "endTimesOutput": {"nodeId": "21"},
            },
        },
    },
    {
        "file": WORKFLOWS_DIR / "illustration_z_image_turbo_workflow.json",
        "name": "生成插画",
        "description": "系统内置，使用 z-image-turbo 生成小说插画",
        "workflow_type": "illustration",
        "workflow_log_enabled": 0,
        "workflow_io_config": {
            "inputs": {
                "promptText": {"nodeId": "12"},
                "width": {"nodeId": "13"},
                "height": {"nodeId": "14"},
            },
            "outputs": {"imageFile": {"nodeId": "7"}},
        },
    },
]
DB_INIT_LOCK = threading.Lock()
DB_INIT_DONE = False


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

    if "audio_asr_workflow_id" not in column_names:
        conn.execute(
            "ALTER TABLE novels ADD COLUMN audio_asr_workflow_id INTEGER REFERENCES comfy_workflows(id)"
        )

    if "nsfw_prompt_id" not in column_names:
        conn.execute(
            "ALTER TABLE novels ADD COLUMN nsfw_prompt_id INTEGER REFERENCES json_prompts(id)"
        )

    if "illustration_scene_prompt_id" not in column_names:
        conn.execute(
            "ALTER TABLE novels ADD COLUMN illustration_scene_prompt_id INTEGER REFERENCES json_prompts(id)"
        )

    if "illustration_shot_prompt_id" not in column_names:
        conn.execute(
            "ALTER TABLE novels ADD COLUMN illustration_shot_prompt_id INTEGER REFERENCES json_prompts(id)"
        )

    if "illustration_prompt_prompt_id" not in column_names:
        conn.execute(
            "ALTER TABLE novels ADD COLUMN illustration_prompt_prompt_id INTEGER REFERENCES json_prompts(id)"
        )

    if "visual_style" not in column_names:
        conn.execute(
            "ALTER TABLE novels ADD COLUMN visual_style TEXT NOT NULL DEFAULT '3D皮克斯动画电影风格'"
        )

    # 添加总音频时长缓存字段（秒）
    if "total_audio_duration_seconds" not in column_names:
        conn.execute(
            "ALTER TABLE novels ADD COLUMN total_audio_duration_seconds REAL NOT NULL DEFAULT 0"
        )

    if "total_audio_non_ver_duration_seconds" not in column_names:
        conn.execute(
            "ALTER TABLE novels ADD COLUMN total_audio_non_ver_duration_seconds REAL NOT NULL DEFAULT 0"
        )


def migrate_json_prompts_table(conn: sqlite3.Connection) -> None:
    columns = conn.execute("PRAGMA table_info(json_prompts)").fetchall()
    column_names = [col[1] for col in columns]
    if "prompt_category" not in column_names:
        conn.execute(
            "ALTER TABLE json_prompts ADD COLUMN prompt_category TEXT NOT NULL DEFAULT 'json_parse'"
        )
        conn.execute(
            "UPDATE json_prompts SET prompt_category='nsfw_review' WHERE name LIKE '%NSFW%审查提示词%' OR description LIKE '%NSFW%审查%'"
        )
    if "llm_config_json" not in column_names:
        conn.execute(
            "ALTER TABLE json_prompts ADD COLUMN llm_config_json TEXT NOT NULL DEFAULT '{}'"
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

    if "non_ver_audio_duration_seconds" not in column_names:
        conn.execute(
            "ALTER TABLE chapters ADD COLUMN non_ver_audio_duration_seconds REAL NOT NULL DEFAULT 0"
        )

    if "non_ver_audio_duration_md5" not in column_names:
        conn.execute(
            "ALTER TABLE chapters ADD COLUMN non_ver_audio_duration_md5 TEXT NOT NULL DEFAULT ''"
        )


def migrate_line_audio_tasks_table(conn: sqlite3.Connection) -> None:
    """迁移：为 line_audio_tasks 表添加调度、音频时长和队列优先级字段"""
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='line_audio_tasks'"
    ).fetchone()
    if not tables:
        return
    columns = conn.execute("PRAGMA table_info(line_audio_tasks)").fetchall()
    column_names = [col[1] for col in columns]
    if "scheduled_at" not in column_names:
        conn.execute("ALTER TABLE line_audio_tasks ADD COLUMN scheduled_at DATETIME")
    if "duration_seconds" not in column_names:
        conn.execute(
            "ALTER TABLE line_audio_tasks ADD COLUMN duration_seconds REAL NOT NULL DEFAULT 0"
        )
    if "queue_priority" not in column_names:
        conn.execute(
            "ALTER TABLE line_audio_tasks ADD COLUMN queue_priority INTEGER NOT NULL DEFAULT 0"
        )


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
    if "think_enabled" not in column_names:
        conn.execute(
            "ALTER TABLE json_tasks ADD COLUMN think_enabled INTEGER NOT NULL DEFAULT 1"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_json_tasks_id_desc ON json_tasks(id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapters_novel_chapter_id ON chapters(novel_id, chapter_num, id DESC)"
    )


def migrate_task_batches_table(conn: sqlite3.Connection) -> None:
    columns = conn.execute("PRAGMA table_info(task_batches)").fetchall()
    column_names = [col[1] for col in columns]
    if "retry_count" not in column_names:
        conn.execute(
            "ALTER TABLE task_batches ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
        )
    if "auto_retry_count" not in column_names:
        conn.execute(
            "ALTER TABLE task_batches ADD COLUMN auto_retry_count INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_batches_task_status ON task_batches(task_id, status)"
    )


def migrate_chapter_asr_tasks_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapter_asr_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            chapter_num INTEGER NOT NULL,
            chapter_title TEXT NOT NULL DEFAULT '',
            workflow_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            progress INTEGER NOT NULL DEFAULT 0,
            audio_file_path TEXT NOT NULL DEFAULT '',
            audio_file_md5 TEXT NOT NULL DEFAULT '',
            force_extract INTEGER NOT NULL DEFAULT 0,
            asr_file_path TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT '',
            extracted_text TEXT NOT NULL DEFAULT '',
            timestamps_text TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(novel_id, chapter_id),
            FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            FOREIGN KEY(workflow_id) REFERENCES comfy_workflows(id) ON DELETE SET NULL
        )
        """
    )
    column_names = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(chapter_asr_tasks)").fetchall()
    }
    if "current_chunk_index" not in column_names:
        conn.execute(
            "ALTER TABLE chapter_asr_tasks ADD COLUMN current_chunk_index INTEGER NOT NULL DEFAULT 0"
        )
    if "total_chunk_count" not in column_names:
        conn.execute(
            "ALTER TABLE chapter_asr_tasks ADD COLUMN total_chunk_count INTEGER NOT NULL DEFAULT 0"
        )
    if "audio_file_md5" not in column_names:
        conn.execute(
            "ALTER TABLE chapter_asr_tasks ADD COLUMN audio_file_md5 TEXT NOT NULL DEFAULT ''"
        )
    if "force_extract" not in column_names:
        conn.execute(
            "ALTER TABLE chapter_asr_tasks ADD COLUMN force_extract INTEGER NOT NULL DEFAULT 0"
        )
    if "subtitle_fix_status" not in column_names:
        conn.execute(
            "ALTER TABLE chapter_asr_tasks ADD COLUMN subtitle_fix_status TEXT NOT NULL DEFAULT ''"
        )
    if "subtitle_fix_error" not in column_names:
        conn.execute(
            "ALTER TABLE chapter_asr_tasks ADD COLUMN subtitle_fix_error TEXT NOT NULL DEFAULT ''"
        )
    if "corrected_srt_file_path" not in column_names:
        conn.execute(
            "ALTER TABLE chapter_asr_tasks ADD COLUMN corrected_srt_file_path TEXT NOT NULL DEFAULT ''"
        )
    if "subtitle_fixed_at" not in column_names:
        conn.execute(
            "ALTER TABLE chapter_asr_tasks ADD COLUMN subtitle_fixed_at DATETIME"
        )
    if "subtitle_fix_current_batch_index" not in column_names:
        conn.execute(
            "ALTER TABLE chapter_asr_tasks ADD COLUMN subtitle_fix_current_batch_index INTEGER NOT NULL DEFAULT 0"
        )
    if "subtitle_fix_total_batch_count" not in column_names:
        conn.execute(
            "ALTER TABLE chapter_asr_tasks ADD COLUMN subtitle_fix_total_batch_count INTEGER NOT NULL DEFAULT 0"
        )


def migrate_chapter_nsfw_tasks_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapter_nsfw_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            chapter_num INTEGER NOT NULL,
            chapter_title TEXT NOT NULL DEFAULT '',
            prompt_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            progress INTEGER NOT NULL DEFAULT 0,
            model_name TEXT NOT NULL DEFAULT '',
            think_enabled INTEGER NOT NULL DEFAULT 1,
            result_json_text TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(novel_id, chapter_id),
            FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            FOREIGN KEY(prompt_id) REFERENCES json_prompts(id) ON DELETE SET NULL
        )
        """
    )


def migrate_chapter_illustration_tasks_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapter_illustration_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            chapter_num INTEGER NOT NULL,
            chapter_title TEXT NOT NULL DEFAULT '',
            stage TEXT NOT NULL,
            prompt_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            progress INTEGER NOT NULL DEFAULT 0,
            model_name TEXT NOT NULL DEFAULT '',
            think_enabled INTEGER NOT NULL DEFAULT 1,
            input_text TEXT NOT NULL DEFAULT '',
            output_text TEXT NOT NULL DEFAULT '',
            result_json_text TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(novel_id, chapter_id, stage),
            FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            FOREIGN KEY(prompt_id) REFERENCES json_prompts(id) ON DELETE SET NULL
        )
        """
    )


def migrate_chapter_illustration_images_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapter_illustration_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            chapter_num INTEGER NOT NULL,
            item_index INTEGER NOT NULL,
            scene_title TEXT NOT NULL DEFAULT '',
            cn_summary TEXT NOT NULL DEFAULT '',
            character_names TEXT NOT NULL DEFAULT '',
            suggested_size TEXT NOT NULL DEFAULT '',
            original_prompt_json_text TEXT NOT NULL DEFAULT '',
            prompt_text TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'idle',
            progress INTEGER NOT NULL DEFAULT 0,
            image_file_path TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(novel_id, chapter_id, item_index),
            FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
        )
        """
    )
    columns = conn.execute("PRAGMA table_info(chapter_illustration_images)").fetchall()
    column_names = [col[1] for col in columns]
    if "character_names" not in column_names:
        conn.execute("ALTER TABLE chapter_illustration_images ADD COLUMN character_names TEXT NOT NULL DEFAULT ''")
    if "suggested_size" not in column_names:
        conn.execute("ALTER TABLE chapter_illustration_images ADD COLUMN suggested_size TEXT NOT NULL DEFAULT ''")
    if "original_prompt_json_text" not in column_names:
        conn.execute("ALTER TABLE chapter_illustration_images ADD COLUMN original_prompt_json_text TEXT NOT NULL DEFAULT ''")


def migrate_chapter_illustration_prompt_batches_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapter_illustration_prompt_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            batch_index INTEGER NOT NULL,
            start_index INTEGER NOT NULL DEFAULT 0,
            end_index INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            progress INTEGER NOT NULL DEFAULT 0,
            input_text TEXT NOT NULL DEFAULT '',
            llm_request_json TEXT NOT NULL DEFAULT '',
            output_text TEXT NOT NULL DEFAULT '',
            result_json_text TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            started_at DATETIME,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id, batch_index),
            FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            FOREIGN KEY(task_id) REFERENCES chapter_illustration_tasks(id) ON DELETE CASCADE
        )
        """
    )


def migrate_chapter_video_export_tasks_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chapter_video_export_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            chapter_num INTEGER NOT NULL,
            chapter_title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            progress INTEGER NOT NULL DEFAULT 0,
            width INTEGER NOT NULL DEFAULT 1080,
            height INTEGER NOT NULL DEFAULT 1920,
            fps INTEGER NOT NULL DEFAULT 30,
            subtitle_mode TEXT NOT NULL DEFAULT 'srt',
            duration_seconds REAL NOT NULL DEFAULT 0,
            current_frame INTEGER NOT NULL DEFAULT 0,
            total_frames INTEGER NOT NULL DEFAULT 0,
            process_id INTEGER NOT NULL DEFAULT 0,
            output_file_path TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(novel_id, chapter_id, width, height, fps, subtitle_mode),
            FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
        )
        """
    )
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(chapter_video_export_tasks)").fetchall()}
    if "process_id" not in columns:
        conn.execute("ALTER TABLE chapter_video_export_tasks ADD COLUMN process_id INTEGER NOT NULL DEFAULT 0")
    if "subtitle_mode" not in columns:
        conn.execute("ALTER TABLE chapter_video_export_tasks ADD COLUMN subtitle_mode TEXT NOT NULL DEFAULT 'srt'")
    unique_columns = []
    for idx in conn.execute("PRAGMA index_list(chapter_video_export_tasks)").fetchall():
        if int(idx[2] or 0) != 1:
            continue
        cols = [str(col[2]) for col in conn.execute(f"PRAGMA index_info({idx[1]})").fetchall()]
        unique_columns.append(cols)
    if ["novel_id", "chapter_id", "width", "height", "fps", "subtitle_mode"] not in unique_columns:
        conn.execute("ALTER TABLE chapter_video_export_tasks RENAME TO chapter_video_export_tasks_old")
        conn.execute(
            """
            CREATE TABLE chapter_video_export_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                novel_id INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                chapter_num INTEGER NOT NULL,
                chapter_title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                progress INTEGER NOT NULL DEFAULT 0,
                width INTEGER NOT NULL DEFAULT 1080,
                height INTEGER NOT NULL DEFAULT 1920,
                fps INTEGER NOT NULL DEFAULT 30,
                subtitle_mode TEXT NOT NULL DEFAULT 'srt',
                duration_seconds REAL NOT NULL DEFAULT 0,
                current_frame INTEGER NOT NULL DEFAULT 0,
                total_frames INTEGER NOT NULL DEFAULT 0,
                process_id INTEGER NOT NULL DEFAULT 0,
                output_file_path TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at DATETIME,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(novel_id, chapter_id, width, height, fps, subtitle_mode),
                FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
                FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO chapter_video_export_tasks(
                id,novel_id,chapter_id,chapter_num,chapter_title,status,progress,width,height,fps,
                subtitle_mode,duration_seconds,current_frame,total_frames,process_id,output_file_path,error_message,created_at,started_at,updated_at
            )
            SELECT id,novel_id,chapter_id,chapter_num,chapter_title,status,progress,width,height,fps,
                   COALESCE(NULLIF(subtitle_mode,''),'srt'),duration_seconds,current_frame,total_frames,process_id,output_file_path,error_message,created_at,started_at,updated_at
            FROM chapter_video_export_tasks_old
            """
        )
        conn.execute("DROP TABLE chapter_video_export_tasks_old")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapter_video_export_tasks_status_id ON chapter_video_export_tasks(status, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chapter_video_export_tasks_updated ON chapter_video_export_tasks(updated_at)"
    )


def db_conn() -> sqlite3.Connection:
    global DB_INIT_DONE
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    if not DB_INIT_DONE:
        with DB_INIT_LOCK:
            if not DB_INIT_DONE:
                conn.execute("PRAGMA journal_mode = WAL")
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
                # Migrations are startup work; running DDL on every request causes SQLite write locks.
                migrate_novels_table(conn)
                migrate_json_prompts_table(conn)
                migrate_chapters_table(conn)
                migrate_line_audio_tasks_table(conn)
                migrate_json_tasks_table(conn)
                migrate_task_batches_table(conn)
                migrate_chapter_asr_tasks_table(conn)
                migrate_chapter_nsfw_tasks_table(conn)
                migrate_chapter_illustration_tasks_table(conn)
                migrate_chapter_illustration_prompt_batches_table(conn)
                migrate_chapter_illustration_images_table(conn)
                migrate_chapter_video_export_tasks_table(conn)
                migrate_workflow_io_config_column(conn)
                migrate_workflow_logs_table(conn)
                conn.commit()
                DB_INIT_DONE = True
    return conn
