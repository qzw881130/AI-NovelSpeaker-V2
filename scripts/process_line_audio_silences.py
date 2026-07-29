#!/usr/bin/env python3
"""Detect and remove long silent regions from line audio tasks by chapter range."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from server.app_context import db_conn  # noqa: E402
from server.line_audio import (  # noqa: E402
    detect_line_audio_task_silences,
    edit_line_audio_task_audio,
    get_chapter_line_audio_entries,
)


def parse_chapter_num(value: str) -> int:
    raw = str(value or "").strip()
    if not raw:
        raise argparse.ArgumentTypeError("chapter number is required")
    try:
        num = int(raw, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid chapter number: {value}") from exc
    if num <= 0:
        raise argparse.ArgumentTypeError("chapter number must be positive")
    return num


def normalize_segments(segments: list[dict], duration: float = 0) -> list[dict[str, float]]:
    max_duration = max(0.0, float(duration or 0))
    normalized: list[dict[str, float]] = []
    for segment in segments:
        start = max(0.0, min(float(segment.get("start") or 0), max_duration))
        end = max(0.0, min(float(segment.get("end") or 0), max_duration))
        if end - start >= 0.05:
            normalized.append({"start": start, "end": end})
    normalized.sort(key=lambda item: item["start"])

    merged: list[dict[str, float]] = []
    for segment in normalized:
        if merged and segment["start"] <= merged[-1]["end"] + 0.02:
            merged[-1]["end"] = max(merged[-1]["end"], segment["end"])
        else:
            merged.append(dict(segment))
    return merged


def reserve_middle_silence(segments: list[dict], duration: float = 0) -> list[dict]:
    max_duration = max(0.0, float(duration or 0))
    reserve_seconds = 0.4
    reserved: list[dict] = []
    for segment in segments:
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or 0)
        if start <= 0.05 or end >= max_duration - 0.05 or end - start <= reserve_seconds + 0.05:
            reserved.append(segment)
        else:
            reserved.append({**segment, "end": end - reserve_seconds})
    return reserved


def list_chapters(novel_id: int, start: int, end: int) -> list[dict]:
    conn = db_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, chapter_num, title
            FROM chapters
            WHERE novel_id=? AND chapter_num BETWEEN ? AND ?
            ORDER BY chapter_num ASC, id ASC
            """,
            (int(novel_id), int(start), int(end)),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": int(row["id"]),
            "chapterNum": int(row["chapter_num"]),
            "title": str(row["title"] or ""),
        }
        for row in rows
    ]


def detect_chapter_marks(
    novel_id: int,
    chapter: dict,
    *,
    noise_db: str,
    min_duration: float,
    quiet: bool,
) -> list[dict]:
    entries = get_chapter_line_audio_entries(novel_id, int(chapter["id"]))
    editable = [
        entry
        for entry in entries
        if entry.get("hasAudio") and entry.get("streamUrl") and entry.get("task", {}).get("id")
    ]
    marks: list[dict] = []
    for entry in editable:
        task_id = int(entry["task"]["id"])
        ok, msg, data = detect_line_audio_task_silences(
            task_id,
            noise_db=noise_db,
            min_duration=min_duration,
        )
        if not ok:
            if not quiet:
                print(
                    f"chapter {chapter['chapterNum']:03d} line {int(entry.get('lineNo') or 0)} task {task_id}: detect failed: {msg}",
                    flush=True,
                )
            continue
        duration = float(entry.get("durationSeconds") or data.get("durationSeconds") or 0)
        segments = normalize_segments(
            reserve_middle_silence(data.get("segments") or [], duration),
            duration,
        )
        if not segments:
            continue
        marks.append(
            {
                "taskId": task_id,
                "lineNo": int(entry.get("lineNo") or 0),
                "lineIndex": int(entry.get("lineIndex") or 0),
                "segments": segments,
            }
        )
    if not quiet:
        print(
            f"chapter {chapter['chapterNum']:03d}: editable={len(editable)}, marked={len(marks)}",
            flush=True,
        )
    return marks


def process_range(
    *,
    novel_id: int,
    start: int,
    end: int,
    noise_db: str,
    min_duration: float,
    dry_run: bool,
    yes: bool,
    quiet: bool,
) -> dict[str, int]:
    if end < start:
        raise ValueError("--end must be greater than or equal to --start")

    chapters = list_chapters(novel_id, start, end)
    if not chapters:
        raise RuntimeError("No chapters matched the requested range")

    all_marks: list[tuple[dict, dict]] = []
    for chapter in chapters:
        for mark in detect_chapter_marks(
            novel_id,
            chapter,
            noise_db=noise_db,
            min_duration=min_duration,
            quiet=quiet,
        ):
            all_marks.append((chapter, mark))

    total_segments = sum(len(mark["segments"]) for _, mark in all_marks)
    unique_task_count = len({int(mark["taskId"]) for _, mark in all_marks})
    if not quiet:
        print(
            f"Detected chapters={len(chapters)}, markedLines={len(all_marks)}, uniqueTasks={unique_task_count}, markedSegments={total_segments}",
            flush=True,
        )

    if dry_run or not all_marks:
        return {
            "chapters": len(chapters),
            "markedTasks": len(all_marks),
            "markedSegments": total_segments,
            "uniqueTasks": unique_task_count,
            "editedTasks": 0,
            "failedTasks": 0,
        }

    if not yes:
        answer = input(f"Delete marked segments from {unique_task_count} unique line audio tasks? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            return {
                "chapters": len(chapters),
                "markedTasks": len(all_marks),
                "markedSegments": total_segments,
                "uniqueTasks": unique_task_count,
                "editedTasks": 0,
                "failedTasks": 0,
            }

    deduped_marks: dict[int, tuple[dict, dict]] = {}
    for chapter, mark in all_marks:
        task_id = int(mark["taskId"])
        existing = deduped_marks.get(task_id)
        if existing:
            existing[1]["segments"].extend(mark["segments"])
        else:
            deduped_marks[task_id] = (chapter, {**mark, "segments": list(mark["segments"])})

    edited = 0
    failed = 0
    for chapter, mark in deduped_marks.values():
        ok, msg, _data = edit_line_audio_task_audio(
            int(mark["taskId"]),
            mode="remove",
            start_seconds=0,
            end_seconds=0,
            segments=mark["segments"],
        )
        if ok:
            edited += 1
            if not quiet:
                print(
                    f"chapter {chapter['chapterNum']:03d} line {mark['lineNo']}: edited task {mark['taskId']}",
                    flush=True,
                )
        else:
            failed += 1
            print(
                f"chapter {chapter['chapterNum']:03d} line {mark['lineNo']}: edit failed task {mark['taskId']}: {msg}",
                file=sys.stderr,
                flush=True,
            )
    return {
        "chapters": len(chapters),
        "markedTasks": len(all_marks),
        "markedSegments": total_segments,
        "uniqueTasks": len(deduped_marks),
        "editedTasks": edited,
        "failedTasks": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect long silences in completed line audio tasks and remove marked segments."
    )
    parser.add_argument("--novel-id", type=int, required=True, help="Novel ID")
    parser.add_argument("--start", type=parse_chapter_num, required=True, help="Start chapter number, e.g. 061")
    parser.add_argument("--end", type=parse_chapter_num, required=True, help="End chapter number, e.g. 084")
    parser.add_argument("--noise-db", default="-45dB", help="ffmpeg silencedetect noise threshold")
    parser.add_argument("--min-duration", type=float, default=1.2, help="Minimum silence duration in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Only detect and report marks; do not edit audio")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation before editing audio")
    parser.add_argument("--quiet", action="store_true", help="Only print final summary and errors")
    args = parser.parse_args()

    result = process_range(
        novel_id=args.novel_id,
        start=args.start,
        end=args.end,
        noise_db=args.noise_db,
        min_duration=args.min_duration,
        dry_run=args.dry_run,
        yes=args.yes,
        quiet=args.quiet,
    )
    print(result)


if __name__ == "__main__":
    main()
