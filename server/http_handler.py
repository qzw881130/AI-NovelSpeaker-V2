import zipfile

from .services import *  # noqa: F401,F403


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
                "audioTasks": fetch_audio_tasks(conn),
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

        m_bundle = re.match(r"^/api/novels/(\d+)/bundle$", route)
        if m_bundle:
            novel_id = int(m_bundle.group(1))
            conn = db_conn()
            row = conn.execute(
                "SELECT id,name,english_dir FROM novels WHERE id=?",
                (novel_id,),
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "novel not found"}, 404)
                return
            chapters = conn.execute(
                """
                SELECT chapter_num,title,text_file_path,audio_file_path
                FROM chapters
                WHERE novel_id=?
                ORDER BY chapter_num ASC
                """,
                (novel_id,),
            ).fetchall()
            conn.close()

            english_dir = str(row["english_dir"] or "").strip()
            if not english_dir:
                self.send_json({"error": "novel english_dir missing"}, 400)
                return

            def resolve_storage_path(raw_path: str) -> Path | None:
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

            bundle_entries: list[tuple[Path, Path]] = []
            for chapter in chapters:
                chapter_num = int(chapter["chapter_num"] or 0)
                title = str(chapter["title"] or "")
                text_name = safe_chapter_file_name(chapter_num, title)
                audio_name = text_name.replace(".txt", ".flac")

                text_src = resolve_storage_path(str(chapter["text_file_path"] or ""))
                if text_src and text_src.exists() and text_src.is_file():
                    arc = Path(english_dir) / "text" / text_name
                    bundle_entries.append((text_src, arc))

                audio_src = resolve_storage_path(str(chapter["audio_file_path"] or ""))
                if audio_src and audio_src.exists() and audio_src.is_file():
                    arc = Path(english_dir) / "audio" / audio_name
                    bundle_entries.append((audio_src, arc))

            if not bundle_entries:
                self.send_json({"error": "novel text/audio files not found"}, 404)
                return

            out_name = f"{english_dir}-{datetime.now().strftime('%Y-%m-%d_%H%M')}.zip"
            output_dir = ROOT_DIR / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            zip_path = output_dir / out_name

            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for src_path, arcname in bundle_entries:
                    zf.write(src_path, arcname=str(arcname))

            body = zip_path.read_bytes()

            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header(
                "Content-Disposition", f'attachment; filename="{out_name}"'
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

        m_audio_file = re.match(r"^/api/novels/(\d+)/chapters/(\d+)/audio-file$", route)
        if m_audio_file:
            novel_id = int(m_audio_file.group(1))
            chapter_num = int(m_audio_file.group(2))
            conn = db_conn()
            row = conn.execute(
                """
                SELECT c.chapter_num,c.audio_file_path,n.english_dir
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
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header(
                "Content-Disposition", f"attachment; filename={abs_audio.name}"
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
                SELECT c.chapter_num,c.audio_file_path,n.english_dir
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
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        m_chapter = re.match(r"^/api/novels/(\d+)/chapters/(\d+)$", route)
        if m_chapter:
            novel_id = int(m_chapter.group(1))
            chapter_num = int(m_chapter.group(2))
            conn = db_conn()
            row = conn.execute(
                """
                SELECT c.id,c.chapter_num,c.title,c.word_count,c.text_file_path,c.audio_file_path,c.has_json,c.has_audio,
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
                    "hasAudio": bool(row["has_audio"]),
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
                       created_at,updated_at,merged_result_json
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

        if route == "/api/audio-tasks":
            conn = db_conn()
            data = fetch_audio_tasks(conn)
            conn.close()
            self.send_json({"audioTasks": data})
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
                SET status='pending',progress=0,error_message=NULL,updated_at=CURRENT_TIMESTAMP
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

        if route == "/api/audio-tasks":
            body = self.read_json()
            conn = db_conn()
            novel = conn.execute(
                "SELECT id,workflow_id FROM novels WHERE id=?",
                (int(body.get("novelId")),),
            ).fetchone()
            if not novel:
                conn.close()
                self.send_json({"error": "novel not found"}, 404)
                return
            if novel["workflow_id"] is None:
                conn.close()
                self.send_json({"error": "novel workflow is not configured"}, 409)
                return
            chapter = conn.execute(
                "SELECT id,title FROM chapters WHERE novel_id=? AND chapter_num=?",
                (int(body.get("novelId")), int(body.get("chapter"))),
            ).fetchone()
            if not chapter:
                conn.close()
                self.send_json({"error": "chapter not found"}, 404)
                return
            json_task = conn.execute(
                """
                SELECT id FROM json_tasks
                WHERE novel_id=? AND chapter_num=? AND status='completed' AND merged_result_json IS NOT NULL
                ORDER BY id DESC LIMIT 1
                """,
                (int(body.get("novelId")), int(body.get("chapter"))),
            ).fetchone()
            if not json_task:
                conn.close()
                self.send_json({"error": "chapter has no completed JSON result"}, 409)
                return
            conn.execute(
                """
                INSERT INTO audio_tasks (novel_id,chapter_id,chapter_num,chapter_title,json_task_id,workflow_id,status,progress,scheduled_at)
                VALUES (?,?,?,?,?,?,'pending',0,?)
                """,
                (
                    int(body.get("novelId")),
                    int(chapter["id"]),
                    int(body.get("chapter")),
                    str(body.get("title") or "").strip()
                    or (
                        str(chapter["title"])
                        if chapter
                        else f"第{int(body.get('chapter'))}回"
                    ),
                    int(json_task["id"]),
                    int(novel["workflow_id"]),
                    str(body.get("scheduledAt") or ""),
                ),
            )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        if route == "/api/audio-tasks/cancel-all":
            result = cancel_all_audio_tasks()
            self.send_json({"status": "ok", **result})
            return

        if route == "/api/json-tasks/simulate":
            conn = db_conn()
            advance_status(conn, "json_tasks")
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        if route == "/api/audio-tasks/simulate":
            conn = db_conn()
            advance_status(conn, "audio_tasks")
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
                conn.execute(
                    "INSERT INTO comfy_workflows (name,workflow_type,description,json_text) VALUES (?, 'user', ?, ?)",
                    (
                        str(body.get("name") or ""),
                        str(body.get("description") or ""),
                        str(body.get("jsonText") or ""),
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
                "SELECT name,json_text FROM comfy_workflows WHERE id=?", (workflow_id,)
            ).fetchone()
            if not src:
                conn.close()
                self.send_json({"error": "workflow not found"}, 404)
                return
            try:
                src_name = str(src["name"])
                new_name = next_workflow_copy_name(conn, src_name)
                conn.execute(
                    "INSERT INTO comfy_workflows (name,workflow_type,description,json_text) VALUES (?, 'user', ?, ?)",
                    (
                        new_name,
                        f"基于 {src_name} 复制",
                        str(src["json_text"]),
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

        m_audio = re.match(r"^/api/novels/(\d+)/chapters/(\d+)/generate-audio$", route)
        if m_audio:
            body = self.read_json()
            novel_id = int(m_audio.group(1))
            chapter_num = int(m_audio.group(2))
            scheduled_at = str(body.get("scheduledAt") or "").strip()
            if scheduled_at and parse_datetime_utc(scheduled_at) is None:
                self.send_json({"error": "invalid scheduledAt"}, 400)
                return
            conn = db_conn()
            novel = conn.execute(
                "SELECT id,workflow_id FROM novels WHERE id=?", (novel_id,)
            ).fetchone()
            if not novel:
                conn.close()
                self.send_json({"error": "novel not found"}, 404)
                return
            if novel["workflow_id"] is None:
                conn.close()
                self.send_json({"error": "novel workflow is not configured"}, 409)
                return
            chapter = conn.execute(
                "SELECT id,title FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            if not chapter:
                conn.close()
                self.send_json({"error": "chapter not found"}, 404)
                return
            json_task = conn.execute(
                """
                SELECT id FROM json_tasks
                WHERE novel_id=? AND chapter_num=? AND status='completed' AND merged_result_json IS NOT NULL
                ORDER BY id DESC LIMIT 1
                """,
                (novel_id, chapter_num),
            ).fetchone()
            if not json_task:
                conn.close()
                self.send_json({"error": "chapter has no completed JSON result"}, 409)
                return
            title = str(chapter["title"]) if chapter else f"第{chapter_num}回"
            chapter_id = int(chapter["id"])
            conn.execute(
                """
                INSERT INTO audio_tasks (novel_id,chapter_id,chapter_num,chapter_title,json_task_id,workflow_id,status,progress,scheduled_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?)
                """,
                (
                    novel_id,
                    chapter_id,
                    chapter_num,
                    title,
                    int(json_task["id"]),
                    int(novel["workflow_id"]),
                    scheduled_at,
                ),
            )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
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
            self.send_json({"status": "ok"})
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
                "SELECT workflow_type FROM comfy_workflows WHERE id=?", (workflow_id,)
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "workflow not found"}, 404)
                return
            if str(row["workflow_type"]) == "system":
                conn.close()
                self.send_json({"error": "system workflow can not be edited"}, 409)
                return
            conn.execute(
                "UPDATE comfy_workflows SET name=?,description=?,json_text=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (
                    str(body.get("name") or ""),
                    str(body.get("description") or ""),
                    str(body.get("jsonText") or ""),
                    workflow_id,
                ),
            )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        if route == "/api/settings":
            llm = body.get("llm") or {}
            ui = body.get("ui") or {}
            try:
                batch_max_chars = int(llm.get("batchMaxChars") or 3500)
            except (TypeError, ValueError):
                batch_max_chars = 3500
            if batch_max_chars not in {3500, 4000, 5000, 6000, 7000}:
                batch_max_chars = 3500

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

            pairs = {
                "comfy_url": str(body.get("comfyUrl") or ""),
                "proxy_url": str(body.get("proxyUrl") or ""),
                "llm_provider": str(llm.get("provider") or "grok"),
                "llm_base_url": str(llm.get("baseUrl") or ""),
                "llm_model": str(llm.get("model") or ""),
                "llm_api_key": str(llm.get("apiKey") or ""),
                "llm_temperature": str(llm.get("temperature") or 0.3),
                "llm_max_tokens": str(llm.get("maxTokens") or 8192),
                "llm_batch_max_chars": str(batch_max_chars),
                "ui_language": ui_language,
                "ui_timezone": ui_timezone,
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

        m_delete_audio_task = re.match(r"^/api/audio-tasks/(\d+)$", route)
        if m_delete_audio_task:
            task_id = int(m_delete_audio_task.group(1))
            conn = db_conn()
            row = conn.execute(
                "SELECT status FROM audio_tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "audio task not found"}, 404)
                return
            if str(row["status"]) == "running":
                conn.close()
                self.send_json({"error": "running audio task can not be deleted"}, 409)
                return
            conn.execute("DELETE FROM audio_tasks WHERE id=?", (task_id,))
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

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
            running_audio = conn.execute(
                "SELECT COUNT(1) AS c FROM audio_tasks WHERE novel_id=? AND chapter_num=? AND status='running'",
                (novel_id, chapter_num),
            ).fetchone()
            if int(running_json["c"] or 0) > 0 or int(running_audio["c"] or 0) > 0:
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

            audio_rows = conn.execute(
                "SELECT downloaded_file_path FROM audio_tasks WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchall()
            for a in audio_rows:
                p = str(a["downloaded_file_path"] or "").strip()
                if p:
                    file_paths.add(p)

            conn.execute(
                "DELETE FROM audio_tasks WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            )
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

        self.send_json({"error": "not found"}, 404)
