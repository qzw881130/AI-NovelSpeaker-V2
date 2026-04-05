from __future__ import annotations

import re
import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
NOVEL_ROOT_DIR = ROOT_DIR / "novel"
DB_PATH = DATA_DIR / "novels.db"
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


DDL_STATEMENTS = [
    """
    PRAGMA foreign_keys = ON
    """,
    """
    CREATE TABLE IF NOT EXISTS json_prompts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        prompt_type TEXT NOT NULL CHECK (prompt_type IN ('system', 'user')),
        description TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS comfy_workflows (
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
    """,
    """
    CREATE TABLE IF NOT EXISTS novels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        author TEXT NOT NULL,
        english_dir TEXT NOT NULL UNIQUE,
        intro TEXT NOT NULL DEFAULT '',
        chapter_count INTEGER NOT NULL DEFAULT 0,
        total_words INTEGER NOT NULL DEFAULT 0,
        json_progress INTEGER NOT NULL DEFAULT 0,
        audio_progress INTEGER NOT NULL DEFAULT 0,
        txt_bytes INTEGER NOT NULL DEFAULT 0,
        audio_bytes INTEGER NOT NULL DEFAULT 0,
        db_bytes INTEGER NOT NULL DEFAULT 0,
        total_audio_duration_seconds REAL NOT NULL DEFAULT 0,
        prompt_id INTEGER,
        workflow_id INTEGER,
        voice_sample_workflow_id INTEGER,
        line_audio_workflow_id INTEGER,
        voice_transcribe_workflow_id INTEGER,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (length(english_dir) BETWEEN 1 AND 25),
        CHECK (english_dir GLOB '[A-Za-z0-9_]*'),
        FOREIGN KEY(prompt_id) REFERENCES json_prompts(id),
        FOREIGN KEY(workflow_id) REFERENCES comfy_workflows(id),
        FOREIGN KEY(voice_sample_workflow_id) REFERENCES comfy_workflows(id),
        FOREIGN KEY(line_audio_workflow_id) REFERENCES comfy_workflows(id),
        FOREIGN KEY(voice_transcribe_workflow_id) REFERENCES comfy_workflows(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chapters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        novel_id INTEGER NOT NULL,
        chapter_num INTEGER NOT NULL,
        title TEXT NOT NULL,
        word_count INTEGER NOT NULL DEFAULT 0,
        text_file_path TEXT NOT NULL DEFAULT '',
        audio_file_path TEXT NOT NULL DEFAULT '',
        audio_duration_seconds REAL NOT NULL DEFAULT 0,
        audio_duration_md5 TEXT NOT NULL DEFAULT '',
        has_json INTEGER NOT NULL DEFAULT 0,
        has_audio INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(novel_id, chapter_num),
        FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_configs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider TEXT NOT NULL,
        base_url TEXT NOT NULL,
        model_name TEXT NOT NULL,
        api_key TEXT NOT NULL DEFAULT '',
        proxy_url TEXT NOT NULL DEFAULT '',
        temperature REAL NOT NULL DEFAULT 0.3,
        max_tokens INTEGER NOT NULL DEFAULT 8192,
        is_default INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        setting_key TEXT PRIMARY KEY,
        setting_value TEXT NOT NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
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
    """,
    """
    CREATE TABLE IF NOT EXISTS json_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        novel_id INTEGER NOT NULL,
        chapter_id INTEGER,
        chapter_num INTEGER NOT NULL,
        chapter_title TEXT NOT NULL,
        prompt_id INTEGER,
        model_name TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        progress INTEGER NOT NULL DEFAULT 0,
        merged_result_json TEXT,
        error_message TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0,
        next_retry_at DATETIME,
        started_at DATETIME,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
        FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE SET NULL,
        FOREIGN KEY(prompt_id) REFERENCES json_prompts(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        batch_index INTEGER NOT NULL,
        input_text TEXT NOT NULL,
        input_word_count INTEGER NOT NULL,
        status TEXT NOT NULL,
        llm_response_text TEXT,
        parsed_json_text TEXT,
        error_message TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(task_id) REFERENCES json_tasks(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audio_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        novel_id INTEGER NOT NULL,
        chapter_id INTEGER,
        chapter_num INTEGER NOT NULL,
        chapter_title TEXT NOT NULL,
        json_task_id INTEGER,
        workflow_id INTEGER,
        status TEXT NOT NULL,
        progress INTEGER NOT NULL DEFAULT 0,
        comfy_prompt_id TEXT,
        comfy_status TEXT,
        output_filename TEXT,
        output_subfolder TEXT,
        output_type TEXT,
        downloaded_file_path TEXT,
        error_message TEXT,
        comfy_started_at DATETIME,
        comfy_finished_at DATETIME,
        scheduled_at DATETIME,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
        FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE SET NULL,
        FOREIGN KEY(json_task_id) REFERENCES json_tasks(id) ON DELETE SET NULL,
        FOREIGN KEY(workflow_id) REFERENCES comfy_workflows(id)
    )
    """,
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
    """,
    """
    CREATE TABLE IF NOT EXISTS roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        novel_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        instruct TEXT NOT NULL DEFAULT '',
        sample_text TEXT NOT NULL DEFAULT '',
        sample_audio_path TEXT NOT NULL DEFAULT '',
        sample_audio_source TEXT NOT NULL DEFAULT '',
        role_level INTEGER NOT NULL DEFAULT 3,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(novel_id, name),
        FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS line_audio_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        novel_id INTEGER NOT NULL,
        chapter_id INTEGER NOT NULL,
        chapter_num INTEGER NOT NULL,
        chapter_title TEXT NOT NULL,
        line_index INTEGER NOT NULL,
        role_name TEXT NOT NULL,
        line_text TEXT NOT NULL,
        reference_text TEXT NOT NULL DEFAULT '',
        reference_audio_path TEXT NOT NULL DEFAULT '',
        line_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        comfy_prompt_id TEXT,
        comfy_status TEXT,
        output_filename TEXT,
        output_subfolder TEXT,
        output_type TEXT,
        downloaded_file_path TEXT,
        error_message TEXT,
        comfy_started_at DATETIME,
        comfy_finished_at DATETIME,
        scheduled_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
        FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chapters_novel_num ON chapters(novel_id, chapter_num)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_roles_novel_id ON roles(novel_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_line_audio_tasks_novel_chapter ON line_audio_tasks(novel_id, chapter_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_line_audio_tasks_hash ON line_audio_tasks(novel_id, chapter_id, line_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_json_tasks_novel_status ON json_tasks(novel_id, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_audio_tasks_novel_status ON audio_tasks(novel_id, status)
    """,
]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    NOVEL_ROOT_DIR.mkdir(parents=True, exist_ok=True)
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)


def load_system_prompt_content() -> str:
    if not SYSTEM_PROMPT_FILE.exists():
        SYSTEM_PROMPT_FILE.write_text(DEFAULT_SYSTEM_PROMPT_CONTENT, encoding="utf-8")
        return DEFAULT_SYSTEM_PROMPT_CONTENT
    text = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8", errors="ignore").strip()
    return text or DEFAULT_SYSTEM_PROMPT_CONTENT


def load_system_prompt_content_from_file(file_path: Path, default_content: str) -> str:
    if not file_path.exists():
        file_path.write_text(default_content, encoding="utf-8")
        return default_content
    text = file_path.read_text(encoding="utf-8", errors="ignore").strip()
    return text or default_content


def init_schema(conn: sqlite3.Connection) -> None:
    for ddl in DDL_STATEMENTS:
        conn.execute(ddl)


def seed_core_data(conn: sqlite3.Connection) -> None:
    for prompt in SYSTEM_PROMPTS:
        prompt_name = str(prompt["name"])
        prompt_desc = str(prompt["description"])
        prompt_content = load_system_prompt_content_from_file(
            Path(prompt["file"]), str(prompt["default_content"])
        )
        legacy_names = [str(name) for name in prompt.get("legacy_names", [])]
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
            legacy_id = legacy_row[0]
            if current_row:
                current_id = current_row[0]
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
            INSERT INTO json_prompts (name, prompt_type, description, content)
            VALUES (?, 'system', ?, ?)
            ON CONFLICT(name) DO NOTHING
            """,
            (prompt_name, prompt_desc, prompt_content),
        )

    prompt_id = conn.execute(
        "SELECT id FROM json_prompts WHERE name=?",
        (SYSTEM_PROMPT_NAME,),
    ).fetchone()[0]

    novels = [
        ("古本水浒传", "施耐庵", "xhz", prompt_id),
        ("红楼梦", "曹雪芹", "hlm", prompt_id),
    ]
    for name, author, english_dir, p_id in novels:
        conn.execute(
            """
            INSERT INTO novels (
                name,
                author,
                english_dir,
                intro,
                chapter_count,
                total_words,
                json_progress,
                audio_progress,
                prompt_id
            )
            VALUES (?, ?, ?, '', 0, 0, 0, 0, ?)
            ON CONFLICT(english_dir) DO UPDATE SET
                name=excluded.name,
                author=excluded.author,
                prompt_id=excluded.prompt_id,
                updated_at=CURRENT_TIMESTAMP
            """,
            (name, author, english_dir, p_id),
        )

    conn.execute(
        """
        INSERT INTO app_settings (setting_key,setting_value,updated_at)
        VALUES ('comfy_url', 'http://127.0.0.1:8188', CURRENT_TIMESTAMP)
        ON CONFLICT(setting_key) DO NOTHING
        """
    )
    conn.execute(
        """
        INSERT INTO app_settings (setting_key,setting_value,updated_at)
        VALUES ('ui_language', 'zh-CN', CURRENT_TIMESTAMP)
        ON CONFLICT(setting_key) DO NOTHING
        """
    )
    conn.execute(
        """
        INSERT INTO app_settings (setting_key,setting_value,updated_at)
        VALUES ('ui_timezone', 'Asia/Shanghai', CURRENT_TIMESTAMP)
        ON CONFLICT(setting_key) DO NOTHING
        """
    )


def ensure_novel_dirs(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT english_dir FROM novels ORDER BY id ASC").fetchall()
    created_paths: list[str] = []
    for row in rows:
        english_dir = str(row[0]).strip()
        if not english_dir:
            continue
        text_dir = NOVEL_ROOT_DIR / english_dir / "text"
        audio_dir = NOVEL_ROOT_DIR / english_dir / "audio"
        text_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)
        created_paths.append(str(text_dir.relative_to(ROOT_DIR)))
        created_paths.append(str(audio_dir.relative_to(ROOT_DIR)))
    return created_paths


def infer_chapter_num_and_title(file_path: Path) -> tuple[int | None, str]:
    stem = file_path.stem.strip()
    m = re.match(r"^(\d{1,3})(?:[_-](.+))?$", stem)
    if not m:
        return None, ""
    chapter_num = int(m.group(1))
    raw_title = str(m.group(2) or "").strip()
    if not raw_title:
        return chapter_num, f"第{chapter_num}回"
    title = re.sub(r"[_\s]+", "", raw_title)
    return chapter_num, title or f"第{chapter_num}回"


def seed_xhz_chapters_from_text_dir(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id,english_dir FROM novels WHERE english_dir='xhz'"
    ).fetchone()
    if not row:
        return 0

    novel_id = int(row[0])
    english_dir = str(row[1])
    text_dir = NOVEL_ROOT_DIR / english_dir / "text"
    if not text_dir.exists() or not text_dir.is_dir():
        return 0

    seeded = 0
    for fp in sorted(text_dir.glob("*.txt")):
        chapter_num, title = infer_chapter_num_and_title(fp)
        if chapter_num is None:
            continue
        text = fp.read_text(encoding="utf-8", errors="ignore")
        word_count = len(re.sub(r"\s+", "", text))
        rel_path = fp.relative_to(ROOT_DIR).as_posix()

        conn.execute(
            """
            INSERT INTO chapters (novel_id,chapter_num,title,word_count,text_file_path)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(novel_id, chapter_num) DO UPDATE SET
                title=excluded.title,
                word_count=excluded.word_count,
                text_file_path=excluded.text_file_path,
                updated_at=CURRENT_TIMESTAMP
            """,
            (novel_id, chapter_num, title, word_count, rel_path),
        )
        seeded += 1

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
    return seeded


def main() -> None:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    try:
        init_schema(conn)
        seed_core_data(conn)
        dirs = ensure_novel_dirs(conn)
        seeded_chapters = seed_xhz_chapters_from_text_dir(conn)
        conn.commit()
    finally:
        conn.close()

    print(f"Database initialized: {DB_PATH}")
    for item in dirs:
        print(f"Created/ensured directory: {item}")
    print(f"Seeded xhz chapters from text files: {seeded_chapters}")


if __name__ == "__main__":
    main()
