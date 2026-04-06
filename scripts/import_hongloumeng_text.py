from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.app_context import ROOT_DIR, db_conn
from server.services import (
    import_text_chapters,
    recalc_novel_stats,
    safe_chapter_file_name,
)


SOURCE_PATH = Path("/Volumes/Data/books/literature-books/红楼梦.txt")
TARGET_ENGLISH_DIR = "hlm"


def split_hongloumeng_chapters(full_text: str) -> list[tuple[int, str, str]]:
    normalized = full_text.replace("\r\n", "\n").replace("\r", "\n")
    pattern = re.compile(r"(?m)^第(\d+)章[ \t]*(.+)$")
    matches = list(pattern.finditer(normalized))
    chapters: list[tuple[int, str, str]] = []
    for idx, match in enumerate(matches):
        chapter_num = int(match.group(1))
        title = f"第{chapter_num}章 {match.group(2).strip()}"
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)
        block = normalized[start:end].strip()
        block = re.sub(r"\n?[（(]本章完[）)]\s*$", "", block).strip()
        chapters.append((chapter_num, title, block))
    return chapters


def write_chapter_files(chapters: list[tuple[int, str, str]], text_dir: Path) -> None:
    text_dir.mkdir(parents=True, exist_ok=True)
    for fp in text_dir.glob("*.txt"):
        fp.unlink()
    for chapter_num, title, content in chapters:
        file_name = safe_chapter_file_name(chapter_num, title)
        (text_dir / file_name).write_text(content.strip() + "\n", encoding="utf-8")


def main() -> None:
    if not SOURCE_PATH.exists():
        raise SystemExit(f"source file not found: {SOURCE_PATH}")

    full_text = SOURCE_PATH.read_text(encoding="utf-8", errors="ignore")
    chapters = split_hongloumeng_chapters(full_text)
    if not chapters:
        raise SystemExit("no chapters found in source text")

    conn = db_conn()
    novel = conn.execute(
        "SELECT id, english_dir, name FROM novels WHERE english_dir=? LIMIT 1",
        (TARGET_ENGLISH_DIR,),
    ).fetchone()
    if not novel:
        conn.close()
        raise SystemExit(f"novel not found for english_dir={TARGET_ENGLISH_DIR}")

    text_dir = ROOT_DIR / "novel" / str(novel["english_dir"]) / "text"
    write_chapter_files(chapters, text_dir)
    result = import_text_chapters(conn, int(novel["id"]))
    if not result.get("ok"):
        conn.close()
        raise SystemExit(result.get("error", "import failed"))
    recalc_novel_stats(conn, int(novel["id"]))
    conn.commit()
    conn.close()

    print(
        f"Imported {int(result.get('imported', 0))} chapters into novel/{novel['english_dir']}/text"
    )


if __name__ == "__main__":
    main()
