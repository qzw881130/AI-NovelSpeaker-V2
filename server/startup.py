from __future__ import annotations

import os
from http.server import ThreadingHTTPServer

from .app_context import DB_PATH, db_conn
from .http_handler import Handler
from .services import (
    ensure_task_worker,
    sync_system_prompts_from_files,
    sync_system_workflow_from_file,
)


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit("Please run: python3 scripts/init_storage.py")

    conn = db_conn()
    sync_system_prompts_from_files(conn)
    sync_system_workflow_from_file(conn)
    conn.commit()
    conn.close()

    ensure_task_worker()

    host = str(os.getenv("NOVELSPEAKER_HOST", "0.0.0.0") or "0.0.0.0")
    try:
        port = int(os.getenv("NOVELSPEAKER_PORT", "8080") or "8080")
    except ValueError:
        port = 8080
    if port <= 0 or port > 65535:
        port = 8080
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving on http://{host}:{port}")
    server.serve_forever()
