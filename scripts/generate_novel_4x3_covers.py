#!/usr/bin/env python3
"""Generate missing 4:3 covers for a novel's completed video exports."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def ensure_pillow_runtime() -> None:
    try:
        import PIL  # noqa: F401

        return
    except ImportError:
        pass

    candidates = [
        ROOT_DIR / ".venv" / "bin" / "python",
        ROOT_DIR / ".venv" / "Scripts" / "python.exe",
    ]
    current_python = Path(sys.executable).absolute()
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK) and candidate.absolute() != current_python:
            print(f"Pillow is unavailable in {current_python}; switching to {candidate}", flush=True)
            os.execv(
                str(candidate),
                [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]],
            )
    raise SystemExit(
        "缺少 Pillow。请先运行 ./start.sh 安装项目依赖，或使用 "
        ".venv/bin/python 执行本脚本。"
    )


ensure_pillow_runtime()

from server.app_context import DB_PATH, db_conn  # noqa: E402
from server.illustration import generate_illustration_image_4x3  # noqa: E402
from server.services import sync_system_workflow_from_file  # noqa: E402
from server.video_export import get_video_export_cover_path  # noqa: E402


def parse_chapter_range(value: str) -> tuple[int, int]:
    raw = str(value or "").strip()
    if not raw:
        raise argparse.ArgumentTypeError("chapter range is required")
    parts = [part.strip() for part in raw.split("-", 1)]
    try:
        start = int(parts[0], 10)
        end = int(parts[1], 10) if len(parts) == 2 else start
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid chapter range: {value}") from exc
    if start <= 0 or end <= 0:
        raise argparse.ArgumentTypeError("chapter numbers must be positive")
    if start > end:
        raise argparse.ArgumentTypeError("chapter range start must not exceed end")
    return start, end


def list_completed_video_tasks(
    novel_id: int,
    chapter_range: tuple[int, int] | None = None,
) -> tuple[str, list[dict]]:
    conn = db_conn()
    try:
        novel = conn.execute(
            "SELECT name FROM novels WHERE id=?",
            (int(novel_id),),
        ).fetchone()
        if not novel:
            raise RuntimeError(f"novel not found: {novel_id}")
        params: list[int] = [int(novel_id)]
        chapter_filter = ""
        if chapter_range:
            chapter_filter = " AND chapter_num BETWEEN ? AND ?"
            params.extend([int(chapter_range[0]), int(chapter_range[1])])
        rows = conn.execute(
            f"""
            SELECT id,chapter_id,chapter_num,chapter_title,cover_image_index,output_file_path
            FROM chapter_video_export_tasks
            WHERE novel_id=? AND status='completed' AND COALESCE(output_file_path,'')<>''
              {chapter_filter}
            ORDER BY chapter_num ASC,id ASC
            """,
            tuple(params),
        ).fetchall()
        return str(novel["name"] or ""), [dict(row) for row in rows]
    finally:
        conn.close()


def get_task_cover_image(task: dict, novel_id: int) -> dict | None:
    conn = db_conn()
    try:
        params: list[int] = [int(novel_id), int(task["chapter_id"])]
        image_filter = ""
        selected_index = int(task.get("cover_image_index") or 0)
        if selected_index > 0:
            image_filter = " AND item_index=?"
            params.append(selected_index)
        row = conn.execute(
            f"""
            SELECT id,item_index,image_file_path,image_4x3_file_path
            FROM chapter_illustration_images
            WHERE novel_id=? AND chapter_id=? AND status='completed'
              AND COALESCE(image_file_path,'')<>''{image_filter}
            ORDER BY item_index ASC LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def local_file_exists(raw_path: str) -> bool:
    rel = str(raw_path or "").strip()
    if not rel:
        return False
    path = (ROOT_DIR / rel).resolve()
    return path.is_file() and path.is_relative_to(ROOT_DIR.resolve())


def generate_missing_covers(
    novel_id: int,
    *,
    chapter_range: tuple[int, int] | None = None,
    dry_run: bool = False,
    force: bool = False,
    progress_callback=None,
) -> dict[str, int]:
    if int(novel_id) <= 0:
        raise RuntimeError("novel id must be positive")
    if not DB_PATH.exists():
        raise RuntimeError("database not found; run scripts/init_storage.py first")

    if not dry_run:
        conn = db_conn()
        sync_system_workflow_from_file(conn)
        conn.commit()
        conn.close()

    novel_name, tasks = list_completed_video_tasks(int(novel_id), chapter_range)
    summary = {
        "matched": len(tasks),
        "skipped": 0,
        "generatedImages": 0,
        "generatedCovers": 0,
        "failed": 0,
    }
    mode = "DRY-RUN" if dry_run else "RUN"
    print(
        f"[{mode}] novel_id={int(novel_id)} name={novel_name!r} "
        f"chapters={chapter_range or 'ALL'} completed_tasks={len(tasks)} force={force}",
        flush=True,
    )
    generated_image_ids: set[int] = set()
    if progress_callback:
        progress_callback(0, len(tasks), dict(summary))

    for position, task in enumerate(tasks, start=1):
        task_id = int(task["id"])
        chapter_num = int(task["chapter_num"] or 0)
        prefix = f"[{position}/{len(tasks)}] chapter={chapter_num:03d} task={task_id}"
        try:
            output_path = (ROOT_DIR / str(task["output_file_path"] or "")).resolve()
            if not output_path.is_file() or not output_path.is_relative_to(ROOT_DIR.resolve()):
                raise RuntimeError("video output file not found")

            cached_cover, _ = get_video_export_cover_path(
                task_id,
                aspect="4x3",
                generate_if_missing=False,
            )
            if cached_cover and not force:
                summary["skipped"] += 1
                print(f"{prefix} SKIP existing 4:3 cover", flush=True)
                continue

            image = get_task_cover_image(task, int(novel_id))
            if not image:
                raise RuntimeError("current cover illustration not found")
            has_4x3_image = local_file_exists(str(image["image_4x3_file_path"] or ""))
            if dry_run:
                action = (
                    "force regenerate 4:3 image and cover"
                    if force
                    else "compose cover"
                    if has_4x3_image
                    else "generate 4:3 image, then compose cover"
                )
                print(
                    f"{prefix} PLAN image={int(image['item_index'])} {action}",
                    flush=True,
                )
                continue

            image_id = int(image["id"])
            should_generate_image = (force or not has_4x3_image) and image_id not in generated_image_ids
            if should_generate_image:
                print(
                    f"{prefix} GENERATE image={int(image['item_index'])} 4:3 illustration",
                    flush=True,
                )
                generate_illustration_image_4x3(image_id)
                generated_image_ids.add(image_id)
                summary["generatedImages"] += 1
            else:
                print(
                    f"{prefix} REUSE image={int(image['item_index'])} existing 4:3 illustration",
                    flush=True,
                )

            if force and cached_cover and cached_cover.is_file():
                cached_cover.unlink()
            cover_path, _ = get_video_export_cover_path(task_id, aspect="4x3")
            if not cover_path or not cover_path.is_file():
                raise RuntimeError("4:3 cover was not created")
            summary["generatedCovers"] += 1
            print(f"{prefix} DONE {cover_path.name}", flush=True)
        except KeyboardInterrupt:
            print(f"{prefix} INTERRUPTED", flush=True)
            raise
        except Exception as exc:
            summary["failed"] += 1
            print(f"{prefix} FAILED {exc}", file=sys.stderr, flush=True)
        finally:
            if progress_callback:
                progress_callback(position, len(tasks), dict(summary))

    print(
        "Summary: " + " ".join(f"{key}={value}" for key, value in summary.items()),
        flush=True,
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Serially generate missing 4:3 illustrations and covers for all completed "
            "video export tasks of one novel. Existing current covers are skipped."
        )
    )
    parser.add_argument("--novel-id", type=int, required=True, help="Novel ID")
    parser.add_argument(
        "--chapters",
        type=parse_chapter_range,
        default=None,
        metavar="001-008",
        help="Optional chapter number or inclusive range, for example 001 or 001-008",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show tasks and planned actions without generating files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate 4:3 illustrations and covers even when covers already exist",
    )
    args = parser.parse_args()
    result = generate_missing_covers(
        args.novel_id,
        chapter_range=args.chapters,
        dry_run=args.dry_run,
        force=args.force,
    )
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
