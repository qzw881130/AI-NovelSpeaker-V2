from __future__ import annotations

import sys

from .video_export import process_video_export_task


def main() -> int:
    try:
        task_id = int(sys.argv[1])
    except (IndexError, TypeError, ValueError):
        print("usage: python -m server.video_export_runner TASK_ID", file=sys.stderr)
        return 2
    process_video_export_task(task_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
