#!/usr/bin/env python3
"""Backfill duration_seconds for completed line audio tasks."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server.services import probe_audio_duration_seconds  # noqa: E402


def ensure_duration_column(conn: sqlite3.Connection) -> None:
    columns = conn.execute("PRAGMA table_info(line_audio_tasks)").fetchall()
    column_names = {str(col[1]) for col in columns}
    if "duration_seconds" not in column_names:
        conn.execute(
            "ALTER TABLE line_audio_tasks ADD COLUMN duration_seconds REAL NOT NULL DEFAULT 0"
        )


def parse_chapter_ids(text: str) -> list[int]:
    raw = str(text or "").strip()
    if not raw:
        return []
    chapter_ids: list[int] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        chapter_ids.append(int(value))
    return chapter_ids


def backfill(
    novel_id: int,
    *,
    db_path: Path,
    chapter_ids: list[int] | None = None,
    dry_run: bool = False,
    quiet: bool = False,
) -> dict[str, int | list[int]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    ensure_duration_column(conn)
    if not dry_run:
        conn.commit()
    selected_chapter_ids = [int(item) for item in (chapter_ids or []) if int(item) > 0]
    chapter_filter = ""
    params: list[int] = [int(novel_id)]
    if selected_chapter_ids:
        placeholders = ",".join("?" for _ in selected_chapter_ids)
        chapter_filter = f" AND chapter_num IN ({placeholders})"
        params.extend(selected_chapter_ids)
    rows = conn.execute(
        f"""
        SELECT id, chapter_num, downloaded_file_path
        FROM line_audio_tasks
        WHERE novel_id=?
          {chapter_filter}
          AND status='completed'
          AND COALESCE(downloaded_file_path, '')<>''
          AND COALESCE(duration_seconds, 0)<=0
        ORDER BY chapter_num ASC, id ASC
        """,
        params,
    ).fetchall()
    rows_by_chapter: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        rows_by_chapter[int(row["chapter_num"] or 0)].append(row)

    updated = 0
    missing = 0
    skipped = 0
    if not quiet:
        mode = "DRY-RUN" if dry_run else "WRITE"
        print(
            f"Start backfill novel_id={novel_id}, chapters={selected_chapter_ids or 'ALL'}, "
            f"matched={len(rows)}, mode={mode}",
            flush=True,
        )
    for chapter_num in sorted(rows_by_chapter):
        chapter_rows = rows_by_chapter[chapter_num]
        chapter_updated = 0
        chapter_missing = 0
        chapter_skipped = 0
        for row in chapter_rows:
            rel_path = str(row["downloaded_file_path"] or "").strip()
            audio_path = (ROOT_DIR / rel_path).resolve()
            if not audio_path.exists() or not audio_path.is_file():
                missing += 1
                chapter_missing += 1
                continue
            duration = round(float(probe_audio_duration_seconds(audio_path)), 1)
            if duration <= 0:
                skipped += 1
                chapter_skipped += 1
                continue
            if not dry_run:
                conn.execute(
                    "UPDATE line_audio_tasks SET duration_seconds=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (duration, int(row["id"])),
                )
            updated += 1
            chapter_updated += 1
        if not quiet:
            print(
                f"chapter {chapter_num:03d}: matched={len(chapter_rows)}, "
                f"updated={chapter_updated}, missingFile={chapter_missing}, "
                f"durationUnavailable={chapter_skipped}",
                flush=True,
            )
        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    conn.close()
    return {
        "novelId": int(novel_id),
        "chapterIds": selected_chapter_ids,
        "matched": len(rows),
        "updated": updated,
        "missingFile": missing,
        "durationUnavailable": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill line_audio_tasks.duration_seconds for a novel."
    )
    parser.add_argument("--novel-id", type=int, required=True, help="Novel ID to backfill")
    parser.add_argument(
        "--chapter-id",
        default="",
        help="Optional chapter numbers, comma-separated. Empty means all chapters.",
    )
    parser.add_argument(
        "--db",
        default=str(ROOT_DIR / "data" / "novels.db"),
        help="SQLite database path",
    )
    parser.add_argument("--dry-run", action="store_true", help="Scan without writing")
    parser.add_argument("--quiet", action="store_true", help="Only print final summary")
    args = parser.parse_args()

    result = backfill(
        args.novel_id,
        db_path=Path(args.db),
        chapter_ids=parse_chapter_ids(args.chapter_id),
        dry_run=args.dry_run,
        quiet=args.quiet,
    )
    print(result)


if __name__ == "__main__":
    main()
