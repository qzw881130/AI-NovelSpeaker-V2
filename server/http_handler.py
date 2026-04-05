import zipfile
from pathlib import Path
from urllib.parse import quote

from .services import *  # noqa: F401,F403
from .roles import (
    list_roles,
    get_role,
    upsert_role_default,
    update_role_fields,
    update_role_level,
    duplicate_role,
    delete_role,
    save_role_sample_audio,
    generate_role_sample_audio,
    extract_role_sample_text,
    apply_roles_to_all_chapters,
)
from .line_audio import (
    list_line_audio_tasks,
    get_line_audio_task,
    get_chapter_line_audio_entries,
    is_chapter_merged_audio_stale,
    invalidate_obsolete_chapter_line_audio_tasks,
    enqueue_line_audio_task,
    enqueue_all_line_audio_tasks,
    merge_chapter_line_audio,
    get_chapter_merged_audio_path,
    delete_line_audio_task,
    retry_line_audio_task,
)


def _resolve_storage_path(raw_path: str) -> Path | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = ROOT_DIR / path
    try:
        return path.resolve()
    except Exception:
        return None


def _bundle_output_dir() -> Path:
    output_dir = ROOT_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _build_bundle_record(zip_path: Path) -> dict:
    stat = zip_path.stat()
    return {
        "fileName": zip_path.name,
        "sizeBytes": int(stat.st_size),
        "createdAt": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def _get_novel_bundle_entries(
    novel_id: int,
) -> tuple[bool, str, str, list[tuple[Path, Path]]]:
    conn = db_conn()
    row = conn.execute(
        "SELECT id,name,english_dir FROM novels WHERE id=?",
        (novel_id,),
    ).fetchone()
    if not row:
        conn.close()
        return False, "novel not found", "", []
    chapters = conn.execute(
        """
        SELECT c.id,c.novel_id,c.chapter_num,c.title,c.text_file_path,c.audio_file_path,n.english_dir
        FROM chapters c
        JOIN novels n ON n.id = c.novel_id
        WHERE novel_id=?
        ORDER BY chapter_num ASC
        """,
        (novel_id,),
    ).fetchall()
    conn.close()

    english_dir = str(row["english_dir"] or "").strip()
    if not english_dir:
        return False, "novel english_dir missing", "", []

    bundle_entries: list[tuple[Path, Path]] = []
    for chapter in chapters:
        chapter_num = int(chapter["chapter_num"] or 0)
        title = str(chapter["title"] or "")
        text_name = safe_chapter_file_name(chapter_num, title)
        audio_name = text_name.replace(".txt", ".flac")

        text_src = _resolve_storage_path(str(chapter["text_file_path"] or ""))
        if text_src and text_src.exists() and text_src.is_file():
            arc = Path(english_dir) / "text" / text_name
            bundle_entries.append((text_src, arc))

        audio_src = resolve_audio_file(chapter)
        if audio_src and audio_src.exists() and audio_src.is_file():
            arc = Path(english_dir) / "audio" / audio_name
            bundle_entries.append((audio_src, arc))

    if not bundle_entries:
        return False, "novel text/audio files not found", english_dir, []
    return True, "ok", english_dir, bundle_entries


def _create_novel_bundle_file(novel_id: int) -> tuple[bool, str, dict | None]:
    ok, msg, english_dir, bundle_entries = _get_novel_bundle_entries(novel_id)
    if not ok:
        return False, msg, None

    out_name = f"{english_dir}-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.zip"
    zip_path = _bundle_output_dir() / out_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src_path, arcname in bundle_entries:
            zf.write(src_path, arcname=str(arcname))
    return True, "created", _build_bundle_record(zip_path)


def _list_novel_bundle_files(novel_id: int) -> tuple[bool, str, str, list[dict]]:
    conn = db_conn()
    row = conn.execute(
        "SELECT english_dir FROM novels WHERE id=?", (novel_id,)
    ).fetchone()
    conn.close()
    if not row:
        return False, "novel not found", "", []
    english_dir = str(row["english_dir"] or "").strip()
    if not english_dir:
        return False, "novel english_dir missing", "", []

    files = sorted(
        _bundle_output_dir().glob(f"{english_dir}-*.zip"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return (
        True,
        "ok",
        english_dir,
        [_build_bundle_record(path) for path in files if path.is_file()],
    )


def _delete_novel_bundle_file(novel_id: int, file_name: str) -> tuple[bool, str]:
    ok, msg, english_dir, bundles = _list_novel_bundle_files(novel_id)
    if not ok:
        return False, msg
    allowed = {str(item["fileName"]) for item in bundles}
    if file_name not in allowed or not file_name.startswith(f"{english_dir}-"):
        return False, "bundle not found"
    zip_path = (_bundle_output_dir() / file_name).resolve()
    if not zip_path.exists() or not zip_path.is_file():
        return False, "bundle not found"
    zip_path.unlink()
    return True, "deleted"


def _role_voice_bundle_dir(english_dir: str) -> Path:
    bundle_dir = ROOT_DIR / "temp" / english_dir
    bundle_dir.mkdir(parents=True, exist_ok=True)
    return bundle_dir


def _build_role_bundle_record(zip_path: Path) -> dict:
    stat = zip_path.stat()
    return {
        "fileName": zip_path.name,
        "sizeBytes": int(stat.st_size),
        "createdAt": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def _list_role_voice_bundle_files(novel_id: int) -> tuple[bool, str, str, list[dict]]:
    conn = db_conn()
    row = conn.execute(
        "SELECT english_dir FROM novels WHERE id=?", (novel_id,)
    ).fetchone()
    conn.close()
    if not row:
        return False, "novel not found", "", []
    english_dir = str(row["english_dir"] or "").strip()
    if not english_dir:
        return False, "novel english_dir missing", "", []
    files = sorted(
        _role_voice_bundle_dir(english_dir).glob(f"{english_dir}-voice-*.zip"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return (
        True,
        "ok",
        english_dir,
        [_build_role_bundle_record(path) for path in files if path.is_file()],
    )


def _create_role_voice_bundle_file(novel_id: int) -> tuple[bool, str, dict | None]:
    conn = db_conn()
    novel_row = conn.execute(
        "SELECT english_dir FROM novels WHERE id=?", (novel_id,)
    ).fetchone()
    if not novel_row:
        conn.close()
        return False, "novel not found", None
    english_dir = str(novel_row["english_dir"] or "").strip()
    if not english_dir:
        conn.close()
        return False, "novel english_dir missing", None
    rows = conn.execute(
        "SELECT id, name, sample_audio_path FROM roles WHERE novel_id=? ORDER BY id ASC",
        (novel_id,),
    ).fetchall()
    conn.close()

    bundle_entries: list[tuple[Path, str]] = []
    for row in rows:
        sample_audio_path = str(row["sample_audio_path"] or "").strip()
        if not sample_audio_path:
            continue
        src = _resolve_storage_path(sample_audio_path)
        if not src or not src.exists() or not src.is_file():
            continue
        role_id = int(row["id"])
        role_name = str(row["name"] or "role").strip() or "role"
        safe_role_name = re.sub(r'[\\/:*?"<>|]+', "_", role_name).strip() or "role"
        bundle_entries.append((src, f"role-{role_id}-{safe_role_name}.flac"))

    if not bundle_entries:
        return False, "role sample audio files not found", None

    out_name = f"{english_dir}-voice-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.zip"
    zip_path = _role_voice_bundle_dir(english_dir) / out_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src_path, arcname in bundle_entries:
            zf.write(src_path, arcname=arcname)
    return True, "created", _build_role_bundle_record(zip_path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def send_json(self, payload: dict | list, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size).decode("utf-8", errors="ignore")
        return json.loads(raw) if raw else {}

    def _send_range_not_satisfiable(self, file_size: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(416)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes */{file_size}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _parse_range_header(self, file_size: int) -> tuple[int, int] | None:
        range_header = str(self.headers.get("Range") or "").strip()
        if not range_header:
            return None
        match = re.match(r"bytes=(\d*)-(\d*)$", range_header)
        if not match:
            self._send_range_not_satisfiable(file_size, "invalid range")
            return None

        start_raw, end_raw = match.groups()
        if not start_raw and not end_raw:
            self._send_range_not_satisfiable(file_size, "invalid range")
            return None

        if start_raw:
            start = int(start_raw)
            end = int(end_raw) if end_raw else file_size - 1
        else:
            suffix_length = int(end_raw)
            if suffix_length <= 0:
                self._send_range_not_satisfiable(file_size, "invalid range")
                return None
            start = max(file_size - suffix_length, 0)
            end = file_size - 1

        if start < 0 or end < start or start >= file_size:
            self._send_range_not_satisfiable(file_size, "range not satisfiable")
            return None

        end = min(end, file_size - 1)
        return start, end

    def send_file_response(
        self,
        file_path: Path,
        ctype: str,
        cache_control: str | None = None,
        download_name: str | None = None,
    ) -> None:
        file_size = file_path.stat().st_size
        range_values = self._parse_range_header(file_size)

        if str(self.headers.get("Range") or "").strip() and range_values is None:
            return

        start = 0
        end = file_size - 1
        status = 200
        if range_values is not None:
            start, end = range_values
            status = 206

        content_length = max(end - start + 1, 0)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        if download_name:
            ascii_download_name = re.sub(r"[^A-Za-z0-9._-]+", "_", download_name).strip(
                "._"
            )
            if not ascii_download_name:
                ascii_download_name = "download.bin"
            self.send_header(
                "Content-Disposition",
                f"attachment; filename=\"{ascii_download_name}\"; filename*=UTF-8''{quote(download_name)}",
            )
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(content_length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()

        with file_path.open("rb") as fp:
            fp.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = fp.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def serve_static(self, route: str) -> bool:
        path = route.lstrip("/") or "index.html"
        fs_path = ROOT_DIR / path
        if fs_path.is_dir():
            fs_path = fs_path / "index.html"
        if not fs_path.exists() or not fs_path.is_file():
            return False
        ctype = "text/plain; charset=utf-8"
        if fs_path.suffix in {".html"}:
            ctype = "text/html; charset=utf-8"
        elif fs_path.suffix in {".js"}:
            ctype = "application/javascript; charset=utf-8"
        elif fs_path.suffix in {".css"}:
            ctype = "text/css; charset=utf-8"
        body = fs_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/api/capture-service/status":
            self.send_json(capture_service_status())
            return

        if route == "/api/capture/logs":
            query = parse_qs(parsed.query or "")
            novel_id = int((query.get("novelId") or ["0"])[0])
            limit = int((query.get("limit") or ["50"])[0])
            limit = max(1, min(200, limit))
            conn = db_conn()
            rows = conn.execute(
                """
                SELECT l.created_at,l.chapter_num,l.chapter_title,l.word_count,n.name AS novel_name
                FROM capture_upload_logs l
                JOIN novels n ON n.id=l.novel_id
                WHERE (? = 0 OR l.novel_id = ?)
                ORDER BY l.id DESC
                LIMIT ?
                """,
                (novel_id, novel_id, limit),
            ).fetchall()
            conn.close()
            logs = [
                {
                    "time": str(r["created_at"]),
                    "chapterNum": int(r["chapter_num"]),
                    "chapterTitle": str(r["chapter_title"]),
                    "wordCount": int(r["word_count"] or 0),
                    "novelName": str(r["novel_name"]),
                }
                for r in rows
            ]
            self.send_json({"logs": logs})
            return

        if route == "/health":
            self.send_json({"status": "ok"})
            return

        if route == "/api/bootstrap":
            conn = db_conn()
            sync_system_prompt_from_file(conn)
            sync_system_workflow_from_file(conn)
            conn.commit()
            data = {
                "novels": fetch_novels(conn),
                "prompts": fetch_prompts(conn),
                "workflows": fetch_workflows(conn),
                "settings": fetch_settings(conn),
                "jsonTasks": fetch_json_tasks(conn),
            }
            conn.close()
            self.send_json(data)
            return

        if route == "/api/novels":
            conn = db_conn()
            data = fetch_novels(conn)
            conn.close()
            self.send_json({"novels": data})
            return

        m_refresh_audio_duration = re.match(
            r"^/api/novels/(\d+)/audio-duration$", route
        )
        if m_refresh_audio_duration:
            novel_id = int(m_refresh_audio_duration.group(1))
            conn = db_conn()
            row = conn.execute(
                "SELECT id FROM novels WHERE id=?", (novel_id,)
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "novel not found"}, 404)
                return
            total_seconds = update_novel_total_audio_duration_seconds(conn, novel_id)
            conn.commit()
            conn.close()
            self.send_json({"status": "ok", "totalAudioDurationSeconds": total_seconds})
            return

        m_bundle = re.match(r"^/api/novels/(\d+)/bundle$", route)
        if m_bundle:
            novel_id = int(m_bundle.group(1))
            ok, msg, record = _create_novel_bundle_file(novel_id)
            if not ok or not record:
                code = 404 if "not found" in msg else 400
                self.send_json({"error": msg}, code)
                return
            zip_path = _bundle_output_dir() / str(record["fileName"])
            body = zip_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header(
                "Content-Disposition", f'attachment; filename="{record["fileName"]}"'
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        m_bundle_list = re.match(r"^/api/novels/(\d+)/bundles$", route)
        if m_bundle_list:
            novel_id = int(m_bundle_list.group(1))
            ok, msg, _, bundles = _list_novel_bundle_files(novel_id)
            if not ok:
                code = 404 if "not found" in msg else 400
                self.send_json({"error": msg}, code)
                return
            self.send_json({"bundles": bundles})
            return

        m_bundle_file = re.match(r"^/api/novels/(\d+)/bundles/(.+)$", route)
        if m_bundle_file:
            novel_id = int(m_bundle_file.group(1))
            file_name = str(m_bundle_file.group(2) or "").strip()
            ok, msg, english_dir, bundles = _list_novel_bundle_files(novel_id)
            if not ok:
                code = 404 if "not found" in msg else 400
                self.send_json({"error": msg}, code)
                return
            allowed = {str(item["fileName"]) for item in bundles}
            if file_name not in allowed or not file_name.startswith(f"{english_dir}-"):
                self.send_json({"error": "bundle not found"}, 404)
                return
            zip_path = (_bundle_output_dir() / file_name).resolve()
            if not zip_path.exists() or not zip_path.is_file():
                self.send_json({"error": "bundle not found"}, 404)
                return
            body = zip_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header(
                "Content-Disposition", f'attachment; filename="{file_name}"'
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        m_role_bundle_list = re.match(r"^/api/novels/(\d+)/role-voice-bundles$", route)
        if m_role_bundle_list:
            novel_id = int(m_role_bundle_list.group(1))
            ok, msg, _, bundles = _list_role_voice_bundle_files(novel_id)
            if not ok:
                code = 404 if "not found" in msg else 400
                self.send_json({"error": msg}, code)
                return
            self.send_json({"bundles": bundles})
            return

        m_role_bundle_file = re.match(
            r"^/api/novels/(\d+)/role-voice-bundles/(.+)$", route
        )
        if m_role_bundle_file:
            novel_id = int(m_role_bundle_file.group(1))
            file_name = str(m_role_bundle_file.group(2) or "").strip()
            ok, msg, english_dir, bundles = _list_role_voice_bundle_files(novel_id)
            if not ok:
                code = 404 if "not found" in msg else 400
                self.send_json({"error": msg}, code)
                return
            allowed = {str(item["fileName"]) for item in bundles}
            if file_name not in allowed or not file_name.startswith(
                f"{english_dir}-voice-"
            ):
                self.send_json({"error": "bundle not found"}, 404)
                return
            zip_path = (_role_voice_bundle_dir(english_dir) / file_name).resolve()
            if not zip_path.exists() or not zip_path.is_file():
                self.send_json({"error": "bundle not found"}, 404)
                return
            body = zip_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header(
                "Content-Disposition", f'attachment; filename="{file_name}"'
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        m_chapters = re.match(r"^/api/novels/(\d+)/chapters$", route)
        if m_chapters:
            novel_id = int(m_chapters.group(1))
            conn = db_conn()
            rows = fetch_chapters(conn, novel_id)
            conn.close()
            self.send_json({"chapters": rows})
            return

        m_download_chapters = re.match(r"^/api/novels/(\d+)/download-chapters$", route)
        if m_download_chapters:
            novel_id = int(m_download_chapters.group(1))
            conn = db_conn()
            rows = fetch_novel_download_chapters(conn, novel_id)
            conn.commit()
            conn.close()
            self.send_json({"chapters": rows})
            return

        m_audio_file = re.match(r"^/api/novels/(\d+)/chapters/(\d+)/audio-file$", route)
        if m_audio_file:
            novel_id = int(m_audio_file.group(1))
            chapter_num = int(m_audio_file.group(2))
            conn = db_conn()
            row = conn.execute(
                """
                SELECT c.id,c.novel_id,c.chapter_num,c.title,c.audio_file_path,n.english_dir
                FROM chapters c
                JOIN novels n ON n.id=c.novel_id
                WHERE c.novel_id=? AND c.chapter_num=?
                """,
                (novel_id, chapter_num),
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "chapter not found"}, 404)
                return
            abs_audio = resolve_audio_file(row)
            if not abs_audio:
                conn.close()
                self.send_json({"error": "audio file not found"}, 404)
                return
            rel_audio = abs_audio.relative_to(ROOT_DIR)
            if str(row["audio_file_path"] or "").strip() != db_rel_path(rel_audio):
                conn.execute(
                    "UPDATE chapters SET audio_file_path=?,has_audio=1,updated_at=CURRENT_TIMESTAMP WHERE novel_id=? AND chapter_num=?",
                    (db_rel_path(rel_audio), novel_id, chapter_num),
                )
                conn.commit()
            conn.close()

            body = abs_audio.read_bytes()
            ctype = (
                mimetypes.guess_type(abs_audio.name)[0] or "application/octet-stream"
            )
            download_name = safe_chapter_file_name(
                chapter_num, str(row["title"] or f"chapter_{chapter_num}")
            ).replace(".txt", ".flac")
            ascii_download_name = re.sub(r"[^A-Za-z0-9._-]+", "_", download_name).strip(
                "_"
            )
            if not ascii_download_name:
                ascii_download_name = f"{chapter_num:03d}.flac"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header(
                "Content-Disposition",
                f"attachment; filename=\"{ascii_download_name}\"; filename*=UTF-8''{quote(download_name)}",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        m_audio_stream = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/audio-stream$", route
        )
        if m_audio_stream:
            novel_id = int(m_audio_stream.group(1))
            chapter_num = int(m_audio_stream.group(2))
            conn = db_conn()
            row = conn.execute(
                """
                SELECT c.id,c.novel_id,c.chapter_num,c.audio_file_path,n.english_dir
                FROM chapters c
                JOIN novels n ON n.id=c.novel_id
                WHERE c.novel_id=? AND c.chapter_num=?
                """,
                (novel_id, chapter_num),
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "chapter not found"}, 404)
                return
            abs_audio = resolve_audio_file(row)
            if not abs_audio:
                conn.close()
                self.send_json({"error": "audio file not found"}, 404)
                return
            rel_audio = abs_audio.relative_to(ROOT_DIR)
            if str(row["audio_file_path"] or "").strip() != db_rel_path(rel_audio):
                conn.execute(
                    "UPDATE chapters SET audio_file_path=?,has_audio=1,updated_at=CURRENT_TIMESTAMP WHERE novel_id=? AND chapter_num=?",
                    (db_rel_path(rel_audio), novel_id, chapter_num),
                )
                conn.commit()
            conn.close()

            ctype = (
                mimetypes.guess_type(abs_audio.name)[0] or "application/octet-stream"
            )
            self.send_file_response(abs_audio, ctype, cache_control="no-store")
            return

        m_chapter = re.match(r"^/api/novels/(\d+)/chapters/(\d+)$", route)
        if m_chapter:
            novel_id = int(m_chapter.group(1))
            chapter_num = int(m_chapter.group(2))
            conn = db_conn()
            row = conn.execute(
                """
                SELECT c.id,c.novel_id,c.chapter_num,c.title,c.word_count,c.text_file_path,c.audio_file_path,c.has_json,c.has_audio,
                       n.name AS novel_name,n.english_dir
                FROM chapters c
                JOIN novels n ON n.id=c.novel_id
                WHERE c.novel_id=? AND c.chapter_num=?
                """,
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            self.send_json(
                {
                    "id": int(row["id"]),
                    "chapterNum": int(row["chapter_num"]),
                    "title": str(row["title"]),
                    "wordCount": int(row["word_count"] or 0),
                    "hasJson": bool(row["has_json"]),
                    "hasAudio": resolve_audio_file(row) is not None,
                    "content": chapter_content(
                        str(row["english_dir"]),
                        chapter_num,
                        str(row["title"]),
                        str(row["text_file_path"] or ""),
                    ),
                    "novelName": str(row["novel_name"]),
                }
            )
            return

        m_json_output = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/json-output$", route
        )
        if m_json_output:
            novel_id = int(m_json_output.group(1))
            chapter_num = int(m_json_output.group(2))
            conn = db_conn()
            row = conn.execute(
                "SELECT merged_result_json,status FROM json_tasks WHERE novel_id=? AND chapter_num=? ORDER BY id DESC LIMIT 1",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not row:
                self.send_json({"hasJson": False, "jsonText": ""})
                return
            text = str(row["merged_result_json"] or "")
            self.send_json(
                {
                    "hasJson": json_text_ready(text),
                    "jsonText": text,
                    "status": str(row["status"] or ""),
                }
            )
            return

        if route == "/api/prompts":
            conn = db_conn()
            sync_system_prompt_from_file(conn)
            conn.commit()
            data = fetch_prompts(conn)
            conn.close()
            self.send_json({"prompts": data})
            return

        if route == "/api/workflows":
            conn = db_conn()
            sync_system_workflow_from_file(conn)
            conn.commit()
            data = fetch_workflows(conn)
            conn.close()
            self.send_json({"workflows": data})
            return

        if route == "/api/workflow-logs":
            conn = db_conn()
            data = list_workflow_logs(conn)
            conn.close()
            self.send_json({"logs": data})
            return

        if route == "/api/settings":
            conn = db_conn()
            data = fetch_settings(conn)
            conn.close()
            self.send_json(data)
            return

        if route == "/api/json-tasks":
            conn = db_conn()
            data = fetch_json_tasks(conn)
            conn.close()
            self.send_json({"jsonTasks": data})
            return

        m_json_task_detail = re.match(r"^/api/json-tasks/(\d+)$", route)
        if m_json_task_detail:
            task_id = int(m_json_task_detail.group(1))
            conn = db_conn()
            task = conn.execute(
                """
                SELECT id,novel_id,chapter_num,chapter_title,status,progress,error_message,
                       created_at,started_at,updated_at,merged_result_json
                FROM json_tasks WHERE id=?
                """,
                (task_id,),
            ).fetchone()
            if not task:
                conn.close()
                self.send_json({"error": "json task not found"}, 404)
                return
            batches = conn.execute(
                """
                SELECT batch_index,input_word_count,status,error_message,
                       input_text,llm_response_text,parsed_json_text,updated_at
                FROM task_batches WHERE task_id=? ORDER BY batch_index ASC
                """,
                (task_id,),
            ).fetchall()
            conn.close()
            self.send_json(
                {
                    "id": int(task["id"]),
                    "novelId": int(task["novel_id"]),
                    "chapter": int(task["chapter_num"]),
                    "title": str(task["chapter_title"]),
                    "status": str(task["status"]),
                    "progress": int(task["progress"] or 0),
                    "errorMessage": str(task["error_message"] or ""),
                    "createdAt": str(task["created_at"]),
                    "startedAt": str(task["started_at"] or ""),
                    "updatedAt": str(task["updated_at"]),
                    "mergedResultJson": str(task["merged_result_json"] or ""),
                    "batches": [
                        {
                            "batchIndex": int(x["batch_index"]),
                            "inputWordCount": int(x["input_word_count"] or 0),
                            "status": str(x["status"]),
                            "errorMessage": str(x["error_message"] or ""),
                            "inputText": str(x["input_text"] or ""),
                            "llmResponseText": str(x["llm_response_text"] or ""),
                            "parsedJsonText": str(x["parsed_json_text"] or ""),
                            "updatedAt": str(x["updated_at"]),
                        }
                        for x in batches
                    ],
                }
            )
            return

        # 角色库API
        m_roles = re.match(r"^/api/novels/(\d+)/roles$", route)
        if m_roles:
            novel_id = int(m_roles.group(1))
            result = list_roles(novel_id)
            self.send_json(result)
            return

        m_role_detail = re.match(r"^/api/novels/(\d+)/roles/(\d+)$", route)
        if m_role_detail:
            role_id = int(m_role_detail.group(2))
            role = get_role(role_id)
            if not role:
                self.send_json({"error": "role not found"}, 404)
                return
            self.send_json({"role": role})
            return

        m_role_sample = re.match(r"^/api/novels/(\d+)/roles/(\d+)/sample$", route)
        if m_role_sample:
            role_id = int(m_role_sample.group(2))
            role = get_role(role_id)
            if not role:
                self.send_json({"error": "role not found"}, 404)
                return
            file_path = str(role.get("sampleAudioPath") or "").strip()
            if not file_path:
                self.send_json({"error": "role sample audio not found"}, 404)
                return
            abs_path = (ROOT_DIR / file_path).resolve()
            if not abs_path.exists() or not abs_path.is_file():
                self.send_json({"error": "audio file not found"}, 404)
                return
            body = abs_path.read_bytes()
            ctype = mimetypes.guess_type(abs_path.name)[0] or "application/octet-stream"
            role_name = str(role.get("name") or "role").strip() or "role"
            download_name = f"{role_id}-{role_name}.flac"
            ascii_download_name = re.sub(r"[^A-Za-z0-9._-]+", "_", download_name).strip(
                "._"
            )
            if not ascii_download_name:
                ascii_download_name = f"{role_id}.flac"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header(
                "Content-Disposition",
                f"attachment; filename=\"{ascii_download_name}\"; filename*=UTF-8''{quote(download_name)}",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 台词音频API
        m_chapter_line_audios = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/line-audios$", route
        )
        if m_chapter_line_audios:
            novel_id = int(m_chapter_line_audios.group(1))
            chapter_num = int(m_chapter_line_audios.group(2))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            entries = get_chapter_line_audio_entries(novel_id, int(chapter_row["id"]))
            merged_audio_outdated = is_chapter_merged_audio_stale(
                novel_id, int(chapter_row["id"])
            )
            self.send_json(
                {
                    "lineAudios": entries,
                    "mergedAudioOutdated": merged_audio_outdated,
                }
            )
            return

        m_line_audio_tasks = re.match(r"^/api/novels/(\d+)/line-audio-tasks$", route)
        if m_line_audio_tasks:
            ensure_task_worker()
            kick_line_audio_queue_once()
            novel_id = int(m_line_audio_tasks.group(1))
            query = parse_qs(parsed.query or "")
            limit = int((query.get("limit") or ["100"])[0])
            offset = int((query.get("offset") or ["0"])[0])
            data = list_line_audio_tasks(novel_id, limit=limit, offset=offset)
            self.send_json(data)
            return

        m_line_audio_task_detail = re.match(r"^/api/line-audio-tasks/(\d+)$", route)
        if m_line_audio_task_detail:
            task_id = int(m_line_audio_task_detail.group(1))
            task = get_line_audio_task(task_id)
            if not task:
                self.send_json({"error": "line audio task not found"}, 404)
                return
            self.send_json({"task": task})
            return

        m_line_audio_file = re.match(r"^/api/line-audio-tasks/(\d+)/file$", route)
        if m_line_audio_file:
            task_id = int(m_line_audio_file.group(1))
            task = get_line_audio_task(task_id)
            if not task:
                self.send_json({"error": "line audio task not found"}, 404)
                return
            file_path = str(task.get("downloadedFilePath") or "").strip()
            if task.get("status") != "completed" or not file_path:
                self.send_json({"error": "audio file not available"}, 409)
                return
            abs_path = (ROOT_DIR / file_path).resolve()
            if not abs_path.exists() or not abs_path.is_file():
                self.send_json({"error": "audio file not found"}, 404)
                return
            ctype = mimetypes.guess_type(abs_path.name)[0] or "audio/flac"
            download_name = (
                abs_path.name if abs_path.suffix else f"line-audio-{task_id}.flac"
            )
            self.send_file_response(abs_path, ctype, download_name=download_name)
            return

        m_merged_audio = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/merged-audio$", route
        )
        if m_merged_audio:
            novel_id = int(m_merged_audio.group(1))
            chapter_num = int(m_merged_audio.group(2))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            merged_path = get_chapter_merged_audio_path(
                novel_id, int(chapter_row["id"])
            )
            if not merged_path:
                self.send_json({"error": "merged audio not found"}, 404)
                return
            ctype = mimetypes.guess_type(merged_path.name)[0] or "audio/flac"
            self.send_file_response(merged_path, ctype)
            return

        if not self.serve_static(route):
            self.send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path

        if route == "/api/capture-service/start":
            body = self.read_json()
            ok, msg = start_capture_service(str(body.get("url") or ""))
            self.send_json({"ok": ok, "message": msg}, 200 if ok else 409)
            return

        if route == "/api/capture-service/stop":
            ok, msg = stop_capture_service()
            self.send_json({"ok": ok, "message": msg}, 200 if ok else 409)
            return

        if route == "/api/settings/test-comfy":
            body = self.read_json()
            comfy_url = str(body.get("comfyUrl") or "").strip()
            ok, msg = test_comfy_endpoint(comfy_url)
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "ok", "message": msg})
            return

        if route == "/api/settings/test-llm":
            body = self.read_json()
            llm = body.get("llm") or {}
            ok, msg = test_llm_endpoint(
                provider=str(llm.get("provider") or "custom"),
                base_url=str(llm.get("baseUrl") or "").strip(),
                model=str(llm.get("model") or "").strip(),
                api_key=str(llm.get("apiKey") or "").strip(),
                proxy_url=str(body.get("proxyUrl") or "").strip(),
                num_ctx=int(llm.get("numCtx") or 65536),
                keep_alive=str(llm.get("keepAlive") or "30m").strip() or "30m",
            )
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "ok", "message": msg})
            return

        if route == "/api/network/probe":
            body = self.read_json()
            target = str(body.get("url") or "").strip().rstrip("/")
            if not target:
                self.send_json({"error": "url is required"}, 400)
                return
            health_url = f"{target}/health"
            try:
                code, _ = http_json_request("GET", health_url, timeout=4.0)
            except RuntimeError as exc:
                self.send_json({"ok": False, "message": str(exc)})
                return
            if 200 <= code < 300:
                self.send_json({"ok": True, "message": f"{health_url} 返回 {code}"})
                return
            self.send_json({"ok": False, "message": f"{health_url} 返回 {code}"})
            return

        m_bundle_create = re.match(r"^/api/novels/(\d+)/bundles$", route)
        if m_bundle_create:
            novel_id = int(m_bundle_create.group(1))
            ok, msg, record = _create_novel_bundle_file(novel_id)
            if not ok or not record:
                code = 404 if "not found" in msg else 400
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": "created", "bundle": record})
            return

        m_role_bundle_create = re.match(
            r"^/api/novels/(\d+)/role-voice-bundles$", route
        )
        if m_role_bundle_create:
            novel_id = int(m_role_bundle_create.group(1))
            ok, msg, record = _create_role_voice_bundle_file(novel_id)
            if not ok or not record:
                code = 404 if "not found" in msg else 400
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": "created", "bundle": record})
            return

        if route == "/chapter":
            body = self.read_json()
            try:
                novel_id = int(body.get("novel_id"))
                chapter_num = int(body.get("chapter_num"))
            except (TypeError, ValueError):
                self.send_json({"error": "novel_id and chapter_num are required"}, 400)
                return
            title = str(body.get("title") or "").strip()
            content = str(body.get("content") or "")
            if not title or not content.strip():
                self.send_json({"error": "title and content are required"}, 400)
                return

            conn = db_conn()
            novel = conn.execute(
                "SELECT english_dir FROM novels WHERE id=?", (novel_id,)
            ).fetchone()
            if not novel:
                conn.close()
                self.send_json({"error": "novel not found"}, 404)
                return
            english_dir = str(novel["english_dir"])
            ensure_novel_dirs(english_dir)
            safe_name = (
                re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title).strip("_")
                or f"chapter_{chapter_num}"
            )
            rel_path = (
                Path("novel")
                / english_dir
                / "text"
                / f"{chapter_num:03d}_{safe_name}.txt"
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
            conn.commit()
            conn.close()
            self.send_json({"status": "saved", "saved_file": db_rel_path(rel_path)})
            return

        if route == "/finalize":
            body = self.read_json()
            try:
                novel_id = int(body.get("novel_id"))
            except (TypeError, ValueError):
                self.send_json({"error": "novel_id is required"}, 400)
                return
            conn = db_conn()
            row = conn.execute(
                "SELECT id FROM novels WHERE id=?", (novel_id,)
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "novel not found"}, 404)
                return
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
            self.send_json({"status": "ok"})
            return

        if route == "/api/novels":
            body = self.read_json()
            english_dir = str(body.get("englishDir") or "").strip()
            if not validate_english_dir(english_dir):
                self.send_json({"error": "invalid englishDir"}, 400)
                return
            conn = db_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO novels (name,author,english_dir,intro,prompt_id,workflow_id,voice_sample_workflow_id,line_audio_workflow_id,voice_transcribe_workflow_id,chapter_count,total_words)
                    VALUES (?,?,?,?,?,?,?,?,?,0,0)
                    """,
                    (
                        str(body.get("name") or "").strip(),
                        str(body.get("author") or "").strip(),
                        english_dir,
                        str(body.get("intro") or "").strip(),
                        int(body.get("promptId")) if body.get("promptId") else None,
                        int(body.get("workflowId")) if body.get("workflowId") else None,
                        int(body.get("voiceSampleWorkflowId"))
                        if body.get("voiceSampleWorkflowId")
                        else None,
                        int(body.get("lineAudioWorkflowId"))
                        if body.get("lineAudioWorkflowId")
                        else None,
                        int(body.get("voiceTranscribeWorkflowId"))
                        if body.get("voiceTranscribeWorkflowId")
                        else None,
                    ),
                )
                conn.commit()
                ensure_novel_dirs(english_dir)
            except sqlite3.IntegrityError:
                conn.close()
                self.send_json({"error": "englishDir already exists"}, 409)
                return
            conn.close()
            self.send_json({"status": "ok"})
            return

        m_create_chapter = re.match(r"^/api/novels/(\d+)/chapters$", route)
        if m_create_chapter:
            novel_id = int(m_create_chapter.group(1))
            body = self.read_json()
            try:
                chapter_num = int(body.get("chapterNum"))
            except (TypeError, ValueError):
                self.send_json({"error": "invalid chapterNum"}, 400)
                return
            title = str(body.get("title") or "").strip()
            content = str(body.get("content") or "")
            if chapter_num <= 0 or not title:
                self.send_json({"error": "chapterNum and title are required"}, 400)
                return
            conn = db_conn()
            ok, msg = create_or_update_chapter_record(
                conn,
                novel_id=novel_id,
                current_chapter_num=None,
                next_chapter_num=chapter_num,
                title=title,
                content=content,
            )
            if not ok:
                conn.close()
                status = 409 if "exists" in msg else 404
                self.send_json({"error": msg}, status)
                return
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        m_import_texts = re.match(r"^/api/novels/(\d+)/import-text-chapters$", route)
        if m_import_texts:
            novel_id = int(m_import_texts.group(1))
            conn = db_conn()
            result = import_text_chapters(conn, novel_id)
            if not result.get("ok"):
                conn.close()
                self.send_json({"error": result.get("error", "import failed")}, 404)
                return
            conn.commit()
            conn.close()
            self.send_json({"status": "ok", "imported": int(result.get("imported", 0))})
            return

        m_retry_json_task = re.match(r"^/api/json-tasks/(\d+)/retry$", route)
        if m_retry_json_task:
            task_id = int(m_retry_json_task.group(1))
            conn = db_conn()
            row = conn.execute(
                "SELECT status FROM json_tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "json task not found"}, 404)
                return
            if str(row["status"]) != "failed":
                conn.close()
                self.send_json({"error": "only failed task can be retried"}, 409)
                return
            conn.execute(
                """
                UPDATE json_tasks
                SET status='pending',progress=0,error_message=NULL,started_at=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (task_id,),
            )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        if route == "/api/json-tasks":
            body = self.read_json()
            conn = db_conn()
            novel = conn.execute(
                "SELECT id,prompt_id FROM novels WHERE id=?",
                (int(body.get("novelId")),),
            ).fetchone()
            if not novel:
                conn.close()
                self.send_json({"error": "novel not found"}, 404)
                return
            if novel["prompt_id"] is None:
                conn.close()
                self.send_json({"error": "novel prompt is not configured"}, 409)
                return
            chapter = conn.execute(
                "SELECT id,title FROM chapters WHERE novel_id=? AND chapter_num=?",
                (int(body.get("novelId")), int(body.get("chapter"))),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO json_tasks (novel_id,chapter_id,chapter_num,chapter_title,prompt_id,model_name,status,progress)
                VALUES (?,?,?,?,?,'', 'pending',0)
                """,
                (
                    int(body.get("novelId")),
                    int(chapter["id"]) if chapter else None,
                    int(body.get("chapter")),
                    str(body.get("title") or "").strip()
                    or (
                        str(chapter["title"])
                        if chapter
                        else f"第{int(body.get('chapter'))}回"
                    ),
                    int(novel["prompt_id"]),
                ),
            )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        if route == "/api/json-tasks/simulate":
            conn = db_conn()
            advance_status(conn, "json_tasks")
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        if route == "/api/prompts":
            body = self.read_json()
            conn = db_conn()
            try:
                conn.execute(
                    "INSERT INTO json_prompts (name,prompt_type,description,content) VALUES (?, 'user', ?, ?)",
                    (
                        str(body.get("name") or ""),
                        str(body.get("description") or ""),
                        str(body.get("content") or ""),
                    ),
                )
                conn.commit()
                conn.close()
                self.send_json({"status": "ok"})
            except sqlite3.IntegrityError:
                conn.close()
                self.send_json({"error": "prompt name already exists"}, 409)
            except sqlite3.OperationalError:
                conn.close()
                self.send_json({"error": "database is busy, please retry"}, 503)
            return

        m_copy_prompt = re.match(r"^/api/prompts/(\d+)/duplicate$", route)
        if m_copy_prompt:
            prompt_id = int(m_copy_prompt.group(1))
            conn = db_conn()
            src = conn.execute(
                "SELECT name,content FROM json_prompts WHERE id=?", (prompt_id,)
            ).fetchone()
            if not src:
                conn.close()
                self.send_json({"error": "prompt not found"}, 404)
                return
            try:
                src_name = str(src["name"])
                new_name = next_prompt_copy_name(conn, src_name)
                conn.execute(
                    "INSERT INTO json_prompts (name,prompt_type,description,content) VALUES (?, 'user', ?, ?)",
                    (
                        new_name,
                        f"基于 {src_name} 复制",
                        str(src["content"]),
                    ),
                )
                conn.commit()
                conn.close()
                self.send_json({"status": "ok"})
            except sqlite3.IntegrityError:
                conn.close()
                self.send_json({"error": "prompt name already exists"}, 409)
            except sqlite3.OperationalError:
                conn.close()
                self.send_json({"error": "database is busy, please retry"}, 503)
            return

        if route == "/api/workflows":
            body = self.read_json()
            conn = db_conn()
            try:
                workflow_type = str(body.get("workflowType") or "").strip()
                workflow_io_config = body.get("workflowIoConfig") or {}
                if not isinstance(workflow_io_config, dict):
                    workflow_io_config = {}
                workflow_log_enabled = 1 if body.get("workflowLogEnabled", True) else 0
                if workflow_type not in {
                    "voice_sample",
                    "line_audio",
                    "voice_transcribe",
                }:
                    conn.close()
                    self.send_json({"error": "invalid workflowType"}, 400)
                    return
                conn.execute(
                    "INSERT INTO comfy_workflows (name,workflow_type,description,json_text,workflow_io_config,workflow_log_enabled) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(body.get("name") or ""),
                        workflow_type,
                        str(body.get("description") or ""),
                        str(body.get("jsonText") or ""),
                        json.dumps(workflow_io_config, ensure_ascii=False),
                        workflow_log_enabled,
                    ),
                )
                conn.commit()
                conn.close()
                self.send_json({"status": "ok"})
            except sqlite3.IntegrityError:
                conn.close()
                self.send_json({"error": "workflow name already exists"}, 409)
            except sqlite3.OperationalError:
                conn.close()
                self.send_json({"error": "database is busy, please retry"}, 503)
            return

        m_copy_workflow = re.match(r"^/api/workflows/(\d+)/duplicate$", route)
        if m_copy_workflow:
            workflow_id = int(m_copy_workflow.group(1))
            conn = db_conn()
            src = conn.execute(
                "SELECT name,json_text,workflow_type,workflow_io_config,workflow_log_enabled FROM comfy_workflows WHERE id=?",
                (workflow_id,),
            ).fetchone()
            if not src:
                conn.close()
                self.send_json({"error": "workflow not found"}, 404)
                return
            try:
                src_name = str(src["name"])
                src_workflow_type = str(src["workflow_type"] or "").strip()
                new_name = next_workflow_copy_name(conn, src_name)
                conn.execute(
                    "INSERT INTO comfy_workflows (name,workflow_type,description,json_text,workflow_io_config,workflow_log_enabled) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        new_name,
                        src_workflow_type,
                        f"基于 {src_name} 复制",
                        str(src["json_text"]),
                        str(src["workflow_io_config"] or "{}"),
                        int(src["workflow_log_enabled"] or 0),
                    ),
                )
                conn.commit()
                conn.close()
                self.send_json({"status": "ok"})
            except sqlite3.IntegrityError:
                conn.close()
                self.send_json({"error": "workflow name already exists"}, 409)
            except sqlite3.OperationalError:
                conn.close()
                self.send_json({"error": "database is busy, please retry"}, 503)
            return

        m_convert = re.match(r"^/api/novels/(\d+)/chapters/(\d+)/convert-json$", route)
        if m_convert:
            novel_id = int(m_convert.group(1))
            chapter_num = int(m_convert.group(2))
            conn = db_conn()
            novel = conn.execute(
                "SELECT id,prompt_id FROM novels WHERE id=?", (novel_id,)
            ).fetchone()
            if not novel:
                conn.close()
                self.send_json({"error": "novel not found"}, 404)
                return
            if novel["prompt_id"] is None:
                conn.close()
                self.send_json({"error": "novel prompt is not configured"}, 409)
                return
            chapter = conn.execute(
                "SELECT id,title FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            title = str(chapter["title"]) if chapter else f"第{chapter_num}回"
            chapter_id = int(chapter["id"]) if chapter else None
            conn.execute(
                """
                INSERT INTO json_tasks (novel_id,chapter_id,chapter_num,chapter_title,prompt_id,status,progress)
                VALUES (?, ?, ?, ?, ?, 'pending', 0)
                """,
                (novel_id, chapter_id, chapter_num, title, int(novel["prompt_id"])),
            )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        # 角色库POST API
        m_roles_post = re.match(r"^/api/novels/(\d+)/roles$", route)
        if m_roles_post:
            novel_id = int(m_roles_post.group(1))
            body = self.read_json()
            name = str(body.get("name") or "").strip()
            instruct = str(body.get("instruct") or "").strip()
            sample_text = str(body.get("sampleText") or "").strip()
            if not name:
                self.send_json({"error": "role name is required"}, 400)
                return
            ok, msg, role = upsert_role_default(novel_id, name, instruct, sample_text)
            if not ok or role is None:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "saved", "role": role})
            return

        m_role_level = re.match(r"^/api/novels/(\d+)/roles/(\d+)/level$", route)
        if m_role_level:
            role_id = int(m_role_level.group(2))
            body = self.read_json()
            role_level = body.get("roleLevel")
            if not isinstance(role_level, int):
                self.send_json({"error": "roleLevel must be integer"}, 400)
                return
            ok, msg, role = update_role_level(role_id, role_level)
            if not ok or role is None:
                code = 404 if msg == "Role not found" else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": "saved", "role": role})
            return

        m_role_duplicate = re.match(r"^/api/novels/(\d+)/roles/(\d+)/duplicate$", route)
        if m_role_duplicate:
            role_id = int(m_role_duplicate.group(2))
            ok, msg, role = duplicate_role(role_id)
            if not ok or role is None:
                code = 404 if msg == "Role not found" else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": "duplicated", "role": role})
            return

        m_role_sample_audio = re.match(
            r"^/api/novels/(\d+)/roles/(\d+)/sample-audio$", route
        )
        if m_role_sample_audio:
            role_id = int(m_role_sample_audio.group(2))
            body = self.read_json()
            audio_base64 = str(body.get("audioBase64") or "").strip()
            source = str(body.get("source") or "uploaded").strip()
            if not audio_base64:
                self.send_json({"error": "audioBase64 is required"}, 400)
                return
            ok, msg, role = save_role_sample_audio(role_id, audio_base64, source)
            if not ok or role is None:
                code = 404 if msg == "Role not found" else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": "saved", "role": role})
            return

        # 生成角色示例音频
        m_role_generate_sample = re.match(
            r"^/api/novels/(\d+)/roles/(\d+)/generate-sample$", route
        )
        if m_role_generate_sample:
            novel_id = int(m_role_generate_sample.group(1))
            role_id = int(m_role_generate_sample.group(2))
            ok, msg, role, workflow = generate_role_sample_audio(role_id, novel_id)
            if not ok or role is None:
                code = 404 if msg == "Role not found" else 409
                payload = {"error": msg}
                if workflow is not None:
                    payload["workflow"] = workflow
                self.send_json(payload, code)
                return
            self.send_json({"status": "generated", "role": role, "workflow": workflow})
            return

        # 提取角色示例音频文本
        m_role_extract_text = re.match(
            r"^/api/novels/(\d+)/roles/(\d+)/extract-sample-text$", route
        )
        if m_role_extract_text and self.command == "POST":
            novel_id = int(m_role_extract_text.group(1))
            role_id = int(m_role_extract_text.group(2))
            ok, msg, text = extract_role_sample_text(role_id, novel_id)
            if not ok:
                code = 404 if "not found" in msg.lower() else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": "ok", "text": text})
            return

        # 应用角色到全部章节
        m_apply_roles_all = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/apply-roles-to-all$", route
        )
        if m_apply_roles_all and self.command == "POST":
            novel_id = int(m_apply_roles_all.group(1))
            source_chapter_num = int(m_apply_roles_all.group(2))
            ok, msg, updated_count = apply_roles_to_all_chapters(
                novel_id, source_chapter_num
            )
            if not ok:
                code = 404 if "not found" in msg.lower() else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": "ok", "updated_count": updated_count})
            return

        # 台词音频POST API
        m_line_audio_enqueue = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/line-audio/enqueue$", route
        )
        if m_line_audio_enqueue:
            ensure_task_worker()
            novel_id = int(m_line_audio_enqueue.group(1))
            chapter_num = int(m_line_audio_enqueue.group(2))
            body = self.read_json()
            line_index = body.get("lineIndex")
            scheduled_at = str(body.get("scheduledAt") or "").strip()
            if not isinstance(line_index, int):
                self.send_json({"error": "lineIndex must be integer"}, 400)
                return
            if scheduled_at and parse_datetime_utc(scheduled_at) is None:
                self.send_json({"error": "invalid scheduledAt"}, 400)
                return
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            ok, msg, task_id = enqueue_line_audio_task(
                novel_id, int(chapter_row["id"]), line_index, scheduled_at=scheduled_at
            )
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            kick_line_audio_queue_once()
            self.send_json({"status": "queued", "taskId": task_id})
            return

        m_line_audio_enqueue_all = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/line-audio/enqueue-all$", route
        )
        if m_line_audio_enqueue_all:
            ensure_task_worker()
            novel_id = int(m_line_audio_enqueue_all.group(1))
            chapter_num = int(m_line_audio_enqueue_all.group(2))
            body = self.read_json()
            scheduled_at = str(body.get("scheduledAt") or "").strip()
            if scheduled_at and parse_datetime_utc(scheduled_at) is None:
                self.send_json({"error": "invalid scheduledAt"}, 400)
                return
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            ok, msg, data = enqueue_all_line_audio_tasks(
                novel_id, int(chapter_row["id"]), scheduled_at=scheduled_at
            )
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            kick_line_audio_queue_once()
            self.send_json({"status": "queued", **data})
            return

        m_retry_line_task = re.match(r"^/api/line-audio-tasks/(\d+)/retry$", route)
        if m_retry_line_task:
            ensure_task_worker()
            task_id = int(m_retry_line_task.group(1))
            ok, msg = retry_line_audio_task(task_id)
            if not ok:
                code = 404 if "不存在" in msg or "not found" in msg else 409
                self.send_json({"error": msg}, code)
                return
            kick_line_audio_queue_once()
            self.send_json({"status": msg})
            return

        m_merge_audio = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/merge-line-audio$", route
        )
        if m_merge_audio:
            novel_id = int(m_merge_audio.group(1))
            chapter_num = int(m_merge_audio.group(2))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            ok, msg, path = merge_chapter_line_audio(novel_id, int(chapter_row["id"]))
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            if path:
                conn = db_conn()
                conn.execute(
                    "UPDATE chapters SET audio_file_path=?,has_audio=1,updated_at=CURRENT_TIMESTAMP WHERE novel_id=? AND chapter_num=?",
                    (path, novel_id, chapter_num),
                )
                conn.commit()
                conn.close()
                refresh_novel_audio_duration_cache_async(
                    novel_id,
                    chapter_id=int(chapter_row["id"]),
                    audio_rel_path=path,
                )
            self.send_json({"status": "merged", "path": path})
            return

        self.send_json({"error": "not found"}, 404)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        body = self.read_json()

        m_save_json_output = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/json-output$", route
        )
        if m_save_json_output:
            novel_id = int(m_save_json_output.group(1))
            chapter_num = int(m_save_json_output.group(2))
            json_text = str(body.get("jsonText") or "").strip()
            if not json_text:
                self.send_json({"error": "jsonText is required"}, 400)
                return
            try:
                parsed_json = parse_model_json(json_text)
            except Exception as exc:
                self.send_json({"error": f"invalid json: {exc}"}, 400)
                return
            if not isinstance(parsed_json.get("role_list", []), list):
                self.send_json({"error": "role_list must be array"}, 400)
                return
            if not isinstance(parsed_json.get("juben", ""), str):
                self.send_json({"error": "juben must be string"}, 400)
                return

            merged = json.dumps(parsed_json, ensure_ascii=False)
            conn = db_conn()
            chapter = conn.execute(
                "SELECT id,title FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            if not chapter:
                conn.close()
                self.send_json({"error": "chapter not found"}, 404)
                return
            novel = conn.execute(
                "SELECT prompt_id FROM novels WHERE id=?", (novel_id,)
            ).fetchone()
            prompt_id = (
                int(novel["prompt_id"])
                if novel and novel["prompt_id"] is not None
                else None
            )
            latest = conn.execute(
                "SELECT id FROM json_tasks WHERE novel_id=? AND chapter_num=? ORDER BY id DESC LIMIT 1",
                (novel_id, chapter_num),
            ).fetchone()
            if latest:
                conn.execute(
                    """
                    UPDATE json_tasks
                    SET merged_result_json=?,status='completed',progress=100,error_message=NULL,
                        prompt_id=COALESCE(prompt_id, ?),updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (merged, prompt_id, int(latest["id"])),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO json_tasks
                    (novel_id,chapter_id,chapter_num,chapter_title,prompt_id,model_name,status,progress,merged_result_json)
                    VALUES (?, ?, ?, ?, ?, '', 'completed', 100, ?)
                    """,
                    (
                        novel_id,
                        int(chapter["id"]),
                        chapter_num,
                        str(chapter["title"]),
                        prompt_id,
                        merged,
                    ),
                )
            conn.execute(
                "UPDATE chapters SET has_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (1 if json_payload_ready(parsed_json) else 0, int(chapter["id"])),
            )
            conn.commit()
            conn.close()
            invalidate_obsolete_chapter_line_audio_tasks(
                novel_id, int(chapter["id"]), merged
            )
            self.send_json({"status": "ok"})
            return

        m_update_role = re.match(r"^/api/novels/(\d+)/roles/(\d+)$", route)
        if m_update_role:
            role_id = int(m_update_role.group(2))
            name = str(body.get("name") or "").strip()
            instruct = str(body.get("instruct") or "").strip()
            sample_text = str(body.get("sampleText") or "").strip()
            if not name:
                self.send_json({"error": "role name is required"}, 400)
                return
            ok, msg, role = update_role_fields(role_id, name, instruct, sample_text)
            if not ok or role is None:
                code = 404 if msg == "Role not found" else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": "saved", "role": role})
            return

        m_update_chapter = re.match(r"^/api/novels/(\d+)/chapters/(\d+)$", route)
        if m_update_chapter:
            novel_id = int(m_update_chapter.group(1))
            current_num = int(m_update_chapter.group(2))
            try:
                next_num = int(body.get("chapterNum"))
            except (TypeError, ValueError):
                self.send_json({"error": "invalid chapterNum"}, 400)
                return
            title = str(body.get("title") or "").strip()
            content = str(body.get("content") or "")
            if next_num <= 0 or not title:
                self.send_json({"error": "chapterNum and title are required"}, 400)
                return
            conn = db_conn()
            ok, msg = create_or_update_chapter_record(
                conn,
                novel_id=novel_id,
                current_chapter_num=current_num,
                next_chapter_num=next_num,
                title=title,
                content=content,
            )
            if not ok:
                conn.close()
                status = 409 if "exists" in msg else 404
                self.send_json({"error": msg}, status)
                return
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        m_novel = re.match(r"^/api/novels/(\d+)$", route)
        if m_novel:
            novel_id = int(m_novel.group(1))
            english_dir = str(body.get("englishDir") or "").strip()
            if not validate_english_dir(english_dir):
                self.send_json({"error": "invalid englishDir"}, 400)
                return
            conn = db_conn()
            old = conn.execute(
                "SELECT english_dir FROM novels WHERE id=?", (novel_id,)
            ).fetchone()
            if not old:
                conn.close()
                self.send_json({"error": "novel not found"}, 404)
                return
            old_dir = str(old["english_dir"])
            try:
                conn.execute(
                    """
                    UPDATE novels
                    SET name=?,author=?,english_dir=?,intro=?,prompt_id=?,workflow_id=?,voice_sample_workflow_id=?,line_audio_workflow_id=?,voice_transcribe_workflow_id=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        str(body.get("name") or "").strip(),
                        str(body.get("author") or "").strip(),
                        english_dir,
                        str(body.get("intro") or "").strip(),
                        int(body.get("promptId")) if body.get("promptId") else None,
                        int(body.get("workflowId")) if body.get("workflowId") else None,
                        int(body.get("voiceSampleWorkflowId"))
                        if body.get("voiceSampleWorkflowId")
                        else None,
                        int(body.get("lineAudioWorkflowId"))
                        if body.get("lineAudioWorkflowId")
                        else None,
                        int(body.get("voiceTranscribeWorkflowId"))
                        if body.get("voiceTranscribeWorkflowId")
                        else None,
                        novel_id,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.close()
                self.send_json({"error": "englishDir already exists"}, 409)
                return
            conn.close()
            if old_dir != english_dir:
                src = NOVEL_DIR / old_dir
                dst = NOVEL_DIR / english_dir
                if src.exists() and not dst.exists():
                    src.rename(dst)
            ensure_novel_dirs(english_dir)
            self.send_json({"status": "ok"})
            return

        m_prompt = re.match(r"^/api/prompts/(\d+)$", route)
        if m_prompt:
            prompt_id = int(m_prompt.group(1))
            conn = db_conn()
            row = conn.execute(
                "SELECT prompt_type FROM json_prompts WHERE id=?", (prompt_id,)
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "prompt not found"}, 404)
                return
            if str(row["prompt_type"]) == "system":
                conn.close()
                self.send_json({"error": "system prompt can not be edited"}, 409)
                return
            conn.execute(
                "UPDATE json_prompts SET name=?,description=?,content=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (
                    str(body.get("name") or ""),
                    str(body.get("description") or ""),
                    str(body.get("content") or ""),
                    prompt_id,
                ),
            )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        m_workflow = re.match(r"^/api/workflows/(\d+)$", route)
        if m_workflow:
            workflow_id = int(m_workflow.group(1))
            conn = db_conn()
            row = conn.execute(
                "SELECT workflow_type, name FROM comfy_workflows WHERE id=?",
                (workflow_id,),
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "workflow not found"}, 404)
                return
            if str(row["workflow_type"]) == "system":
                conn.close()
                self.send_json({"error": "system workflow can not be edited"}, 409)
                return
            workflow_io_config = body.get("workflowIoConfig") or {}
            if not isinstance(workflow_io_config, dict):
                workflow_io_config = {}
            workflow_log_enabled = 1 if body.get("workflowLogEnabled", True) else 0
            workflow_type = str(
                body.get("workflowType") or row["workflow_type"] or ""
            ).strip()
            if workflow_type not in {
                "voice_sample",
                "line_audio",
                "voice_transcribe",
            }:
                conn.close()
                self.send_json({"error": "invalid workflowType"}, 400)
                return
            conn.execute(
                "UPDATE comfy_workflows SET name=?,workflow_type=?,description=?,json_text=?,workflow_io_config=?,workflow_log_enabled=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (
                    str(body.get("name") or ""),
                    workflow_type,
                    str(body.get("description") or ""),
                    str(body.get("jsonText") or ""),
                    json.dumps(workflow_io_config, ensure_ascii=False),
                    workflow_log_enabled,
                    workflow_id,
                ),
            )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        if route == "/api/workflow-logs":
            conn = db_conn()
            clear_workflow_logs(conn)
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        if route == "/api/settings":
            llm = body.get("llm") or {}
            ui = body.get("ui") or {}
            raw_batch_max_chars = llm.get("batchMaxChars", 3500)
            if raw_batch_max_chars in (None, ""):
                raw_batch_max_chars = 3500
            try:
                batch_max_chars = int(raw_batch_max_chars)
            except (TypeError, ValueError):
                batch_max_chars = 3500
            if batch_max_chars not in {0, 3500, 4000, 5000, 6000, 7000}:
                batch_max_chars = 3500
            raw_max_tokens = llm.get("maxTokens", 8192)
            if raw_max_tokens in (None, ""):
                raw_max_tokens = 8192
            try:
                llm_max_tokens = int(raw_max_tokens)
            except (TypeError, ValueError):
                llm_max_tokens = 8192
            if str(llm.get("provider") or "").strip() == "deepseek":
                llm_max_tokens = min(llm_max_tokens, 8192)

            ui_language = str(ui.get("language") or "zh-CN").strip() or "zh-CN"
            if ui_language not in {"zh-CN", "zh-TW", "en-US", "ja-JP", "ko-KR"}:
                ui_language = "zh-CN"

            ui_timezone = (
                str(ui.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
            )
            if ui_timezone not in {
                "Asia/Shanghai",
                "Asia/Hong_Kong",
                "Asia/Tokyo",
                "Asia/Seoul",
                "America/New_York",
                "America/Los_Angeles",
                "Europe/London",
                "Europe/Paris",
                "Australia/Sydney",
                "UTC",
            }:
                ui_timezone = "Asia/Shanghai"

            line_audio_queue = body.get("lineAudioQueue") or {}
            if not isinstance(line_audio_queue, dict):
                line_audio_queue = {}
            line_audio_queue_mode = (
                str(line_audio_queue.get("mode") or "immediate").strip() or "immediate"
            )
            if line_audio_queue_mode not in {"immediate", "scheduled"}:
                line_audio_queue_mode = "immediate"
            line_audio_queue_scheduled_at = str(
                line_audio_queue.get("scheduledAt") or ""
            ).strip()
            if line_audio_queue_mode == "scheduled":
                if (
                    not line_audio_queue_scheduled_at
                    or parse_datetime_utc(line_audio_queue_scheduled_at) is None
                ):
                    self.send_json({"error": "invalid lineAudioQueue.scheduledAt"}, 400)
                    return
            else:
                line_audio_queue_scheduled_at = ""

            pairs = {
                "comfy_url": str(body.get("comfyUrl") or ""),
                "proxy_url": str(body.get("proxyUrl") or ""),
                "llm_provider": str(llm.get("provider") or "grok"),
                "llm_base_url": str(llm.get("baseUrl") or ""),
                "llm_model": str(llm.get("model") or ""),
                "llm_api_key": str(llm.get("apiKey") or ""),
                "llm_temperature": str(llm.get("temperature") or 0.3),
                "llm_max_tokens": str(llm_max_tokens),
                "llm_num_ctx": str(llm.get("numCtx") or 65536),
                "llm_keep_alive": str(llm.get("keepAlive") or "30m"),
                "llm_batch_max_chars": str(batch_max_chars),
                "ui_language": ui_language,
                "ui_timezone": ui_timezone,
                "line_audio_queue_mode": line_audio_queue_mode,
                "line_audio_queue_scheduled_at": line_audio_queue_scheduled_at,
            }
            conn = db_conn()
            for k, v in pairs.items():
                conn.execute(
                    """
                    INSERT INTO app_settings (setting_key,setting_value,updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(setting_key) DO UPDATE SET
                        setting_value=excluded.setting_value,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (k, v),
                )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        self.send_json({"error": "not found"}, 404)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path

        m_delete_json_task = re.match(r"^/api/json-tasks/(\d+)$", route)
        if m_delete_json_task:
            task_id = int(m_delete_json_task.group(1))
            conn = db_conn()
            row = conn.execute(
                "SELECT status FROM json_tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "json task not found"}, 404)
                return
            if str(row["status"]) == "running":
                conn.close()
                self.send_json({"error": "running task can not be deleted"}, 409)
                return
            conn.execute("DELETE FROM json_tasks WHERE id=?", (task_id,))
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        m_delete_chapter = re.match(r"^/api/novels/(\d+)/chapters/(\d+)$", route)
        if m_delete_chapter:
            novel_id = int(m_delete_chapter.group(1))
            chapter_num = int(m_delete_chapter.group(2))
            conn = db_conn()
            row = conn.execute(
                "SELECT id,text_file_path,audio_file_path FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "chapter not found"}, 404)
                return

            running_json = conn.execute(
                "SELECT COUNT(1) AS c FROM json_tasks WHERE novel_id=? AND chapter_num=? AND status='running'",
                (novel_id, chapter_num),
            ).fetchone()
            if int(running_json["c"] or 0) > 0:
                conn.close()
                self.send_json(
                    {"error": "chapter has running tasks, please terminate them first"},
                    409,
                )
                return

            file_paths: set[str] = set()
            text_path = str(row["text_file_path"] or "").strip()
            if text_path:
                file_paths.add(text_path)
            chapter_audio_path = str(row["audio_file_path"] or "").strip()
            if chapter_audio_path:
                file_paths.add(chapter_audio_path)

            conn.execute(
                "DELETE FROM json_tasks WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            )
            conn.execute(
                "DELETE FROM capture_upload_logs WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            )
            conn.execute("DELETE FROM chapters WHERE id=?", (int(row["id"]),))
            recalc_novel_stats(conn, novel_id)
            conn.commit()
            conn.close()

            for rel in file_paths:
                abs_path = (ROOT_DIR / rel).resolve()
                try:
                    abs_path.relative_to(ROOT_DIR)
                except ValueError:
                    continue
                if abs_path.exists() and abs_path.is_file():
                    abs_path.unlink()
            self.send_json({"status": "ok"})
            return

        m_novel = re.match(r"^/api/novels/(\d+)$", route)
        if m_novel:
            novel_id = int(m_novel.group(1))
            conn = db_conn()
            row = conn.execute(
                "SELECT english_dir FROM novels WHERE id=?", (novel_id,)
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "novel not found"}, 404)
                return
            english_dir = str(row["english_dir"])
            conn.execute("DELETE FROM novels WHERE id=?", (novel_id,))
            conn.commit()
            conn.close()
            target = NOVEL_DIR / english_dir
            if target.exists():
                shutil.rmtree(target)
            self.send_json({"status": "ok"})
            return

        m_prompt = re.match(r"^/api/prompts/(\d+)$", route)
        if m_prompt:
            prompt_id = int(m_prompt.group(1))
            conn = db_conn()
            row = conn.execute(
                "SELECT prompt_type FROM json_prompts WHERE id=?", (prompt_id,)
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "prompt not found"}, 404)
                return
            if str(row["prompt_type"]) == "system":
                conn.close()
                self.send_json({"error": "system prompt can not be deleted"}, 409)
                return
            conn.execute("DELETE FROM json_prompts WHERE id=?", (prompt_id,))
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        m_workflow = re.match(r"^/api/workflows/(\d+)$", route)
        if m_workflow:
            workflow_id = int(m_workflow.group(1))
            conn = db_conn()
            row = conn.execute(
                "SELECT workflow_type FROM comfy_workflows WHERE id=?", (workflow_id,)
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "workflow not found"}, 404)
                return
            if str(row["workflow_type"]) == "system":
                conn.close()
                self.send_json({"error": "system workflow can not be deleted"}, 409)
                return
            conn.execute("DELETE FROM comfy_workflows WHERE id=?", (workflow_id,))
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        if route == "/api/workflow-logs":
            conn = db_conn()
            clear_workflow_logs(conn)
            conn.commit()
            conn.close()
            self.send_json({"status": "deleted"})
            return

        # 角色库DELETE API
        m_delete_role = re.match(r"^/api/novels/(\d+)/roles/(\d+)$", route)
        if m_delete_role:
            role_id = int(m_delete_role.group(2))
            ok, msg = delete_role(role_id)
            if not ok:
                code = 404 if msg == "Role not found" else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": "deleted"})
            return

        # 台词音频DELETE API
        m_delete_line_task = re.match(r"^/api/line-audio-tasks/(\d+)$", route)
        if m_delete_line_task:
            ensure_task_worker()
            task_id = int(m_delete_line_task.group(1))
            ok, msg = delete_line_audio_task(task_id)
            if not ok:
                code = 404 if "not found" in msg else 409
                self.send_json({"error": msg}, code)
                return
            kick_line_audio_queue_once()
            self.send_json({"status": "deleted"})
            return

        m_delete_bundle = re.match(r"^/api/novels/(\d+)/bundles/(.+)$", route)
        if m_delete_bundle:
            novel_id = int(m_delete_bundle.group(1))
            file_name = str(m_delete_bundle.group(2) or "").strip()
            ok, msg = _delete_novel_bundle_file(novel_id, file_name)
            if not ok:
                code = 404 if "not found" in msg else 400
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": "deleted"})
            return

        self.send_json({"error": "not found"}, 404)
