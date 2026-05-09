"""章节音频 ASR 服务模块"""

from __future__ import annotations

import json
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from .app_context import NOVEL_DIR, ROOT_DIR, db_conn
from .services import (
    comfy_request_json,
    comfy_upload_input_file,
    create_workflow_log,
    fetch_settings,
    probe_audio_duration_seconds,
    safe_chapter_file_name,
    update_workflow_log_error,
    update_workflow_log_json,
    workflow_json_to_prompt_json,
)


def _novel_asr_output_dir(english_dir: str) -> Path:
    return NOVEL_DIR / english_dir / "asr"


def _chapter_asr_output_path(english_dir: str, chapter_num: int, title: str) -> Path:
    name = safe_chapter_file_name(chapter_num, title)
    stem = name[:-4] if name.endswith(".txt") else name
    return _novel_asr_output_dir(english_dir) / f"{stem}.asr"


def _extract_text_output_from_history(
    history: dict, prompt_id: str, node_id: str
) -> str | None:
    if not history:
        return None
    job = history.get(prompt_id)
    if job is None and history:
        job = next(iter(history.values()))
    if not isinstance(job, dict):
        return None
    outputs = job.get("outputs")
    if not isinstance(outputs, dict):
        return None
    node_output = outputs.get(str(node_id))
    if not isinstance(node_output, dict):
        return None
    for key in ("text", "string", "value"):
        value = node_output.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            text = "\n".join(str(x) for x in value if str(x).strip()).strip()
            if text:
                return text
    ui = node_output.get("ui")
    if isinstance(ui, dict):
        for key in ("text", "string", "value"):
            value = ui.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list) and value:
                text = "\n".join(str(x) for x in value if str(x).strip()).strip()
                if text:
                    return text
    return None


def _extract_comfy_history_error(history: dict, prompt_id: str) -> str | None:
    if not history:
        return None
    job = history.get(prompt_id)
    if job is None and history:
        job = next(iter(history.values()))
    if not isinstance(job, dict):
        return None
    status = job.get("status")
    if isinstance(status, dict):
        messages = status.get("messages")
        if isinstance(messages, list):
            for item in messages:
                if isinstance(item, list) and len(item) >= 2:
                    msg_type = str(item[0] or "").strip().lower()
                    payload = item[1]
                    if msg_type in {"execution_error", "error"} and isinstance(payload, dict):
                        exc_message = str(payload.get("exception_message") or payload.get("message") or "").strip()
                        if exc_message:
                            return exc_message
    return None


def _history_has_node_output(history: dict, prompt_id: str, node_id: str) -> bool:
    if not isinstance(history, dict) or not history:
        return False
    job = history.get(prompt_id)
    if job is None and history:
        job = next(iter(history.values()))
    if not isinstance(job, dict):
        return False
    outputs = job.get("outputs")
    if not isinstance(outputs, dict):
        return False
    return str(node_id) in outputs


def _get_audio_asr_workflow(
    novel_id: int,
) -> tuple[int | None, dict | None, dict, str, str, bool]:
    conn = db_conn()
    row = conn.execute(
        """
        SELECT w.id, w.json_text, w.workflow_io_config, w.name, w.workflow_type, w.workflow_log_enabled
        FROM comfy_workflows w
        JOIN novels n ON n.audio_asr_workflow_id = w.id
        WHERE n.id = ?
        """,
        (novel_id,),
    ).fetchone()
    if not row:
        row = conn.execute(
            """
            SELECT id, json_text, workflow_io_config, name, workflow_type, workflow_log_enabled
            FROM comfy_workflows
            WHERE workflow_type='audio_asr'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
    conn.close()
    if not row:
        return None, None, {}, "", "audio_asr", True
    try:
        workflow = json.loads(str(row["json_text"] or "{}"))
        io_config = json.loads(str(row["workflow_io_config"] or "{}") or "{}")
        if not isinstance(io_config, dict):
            io_config = {}
        return (
            int(row["id"] or 0),
            workflow if isinstance(workflow, dict) else None,
            io_config,
            str(row["name"] or ""),
            str(row["workflow_type"] or "audio_asr"),
            bool(int(row["workflow_log_enabled"] or 0)),
        )
    except json.JSONDecodeError:
        return int(row["id"] or 0), None, {}, str(row["name"] or ""), str(row["workflow_type"] or "audio_asr"), bool(int(row["workflow_log_enabled"] or 0))


def _format_asr_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds or 0) * 1000)))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _parse_line_list(text: str) -> list[str]:
    return [str(line).strip() for line in str(text or "").replace("\r", "").split("\n") if str(line).strip()]


def _split_audio_for_alignment(audio_path: Path, *, chunk_seconds: int = 60) -> list[tuple[Path, float]]:
    temp_dir = ROOT_DIR / "temp" / "audio_asr_chunks" / audio_path.stem
    temp_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = temp_dir / "chunk_%03d.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-f",
            "segment",
            "-segment_time",
            str(int(chunk_seconds)),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(output_pattern),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    chunks = sorted(temp_dir.glob("chunk_*.wav"))
    result: list[tuple[Path, float]] = []
    offset = 0.0
    for chunk_path in chunks:
        duration = probe_audio_duration_seconds(chunk_path)
        # ffmpeg segment may generate a trailing near-empty chunk (eg. 1ms).
        # Feeding such chunk into Qwen3-ASR/Whisper feature extraction will crash.
        if duration < 0.5 or chunk_path.stat().st_size < 1024:
            continue
        result.append((chunk_path, offset))
        offset += duration
    return result


def _collect_segments(
    *, timestamps_text: str, text_list_text: str, start_times_text: str, end_times_text: str, offset_seconds: float = 0.0
) -> list[tuple[str, float, float]]:
    text_list = _parse_line_list(text_list_text)
    start_times = _parse_line_list(start_times_text)
    end_times = _parse_line_list(end_times_text)
    segments: list[tuple[str, float, float]] = []
    if text_list and start_times and end_times:
        count = min(len(text_list), len(start_times), len(end_times))
        for idx in range(count):
            try:
                start = float(start_times[idx]) + offset_seconds
                end = float(end_times[idx]) + offset_seconds
            except ValueError:
                continue
            if text_list[idx]:
                segments.append((text_list[idx], start, end))
        return segments
    for line in _parse_line_list(timestamps_text):
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            start = float(parts[-2]) + offset_seconds
            end = float(parts[-1]) + offset_seconds
        except ValueError:
            continue
        text = "\t".join(parts[:-2]).strip()
        if text:
            segments.append((text, start, end))
    return segments


def _build_asr_content(
    *, timestamps_text: str = "", text_list_text: str = "", start_times_text: str = "", end_times_text: str = "", segments: list[tuple[str, float, float]] | None = None
) -> str:
    segments = list(segments or []) or _collect_segments(
        timestamps_text=timestamps_text,
        text_list_text=text_list_text,
        start_times_text=start_times_text,
        end_times_text=end_times_text,
        offset_seconds=0.0,
    )
    chunks = []
    for index, (text, start, end) in enumerate(segments, start=1):
        chunks.append(
            f"{index}\n{_format_asr_timestamp(start)} --> {_format_asr_timestamp(end)}\n{text}"
        )
    return "\n\n".join(chunks).strip()


def _run_asr_workflow_on_audio(
    *,
    audio_path: Path,
    workflow: dict,
    workflow_io_config: dict,
    comfy_url: str,
    workflow_log_enabled: bool,
    workflow_category: str,
    workflow_name: str,
    workflow_log_id: int,
) -> tuple[str, str, str, str, str, str]:
    upload_info = comfy_upload_input_file(audio_path.name, audio_path.read_bytes())
    filename = str(upload_info.get("name") or audio_path.name).strip() or audio_path.name
    subfolder = str(upload_info.get("subfolder") or "").strip()
    file_type = str(upload_info.get("type") or "input").strip() or "input"

    workflow_copy = workflow_json_to_prompt_json(deepcopy(workflow))
    input_node_id = str(workflow_io_config.get("inputs", {}).get("audioFile", {}).get("nodeId") or "12").strip() or "12"
    if input_node_id not in workflow_copy:
        raise RuntimeError(f"audio asr workflow missing node {input_node_id}")
    workflow_copy[input_node_id]["inputs"]["audio"] = filename
    if "audioUI" in workflow_copy[input_node_id]["inputs"]:
        workflow_copy[input_node_id]["inputs"]["audioUI"] = f"/api/view?filename={filename}&type={file_type}&subfolder={subfolder}&rand={time.time():.6f}"

    output_node_ids = {
        "text": str(workflow_io_config.get("outputs", {}).get("textOutput", {}).get("nodeId") or "13").strip() or "13",
        "language": str(workflow_io_config.get("outputs", {}).get("languageOutput", {}).get("nodeId") or "14").strip() or "14",
        "timestamps": str(workflow_io_config.get("outputs", {}).get("timestampsOutput", {}).get("nodeId") or "17").strip() or "17",
        "text_list": str(workflow_io_config.get("outputs", {}).get("textListOutput", {}).get("nodeId") or "19").strip() or "19",
        "start_times": str(workflow_io_config.get("outputs", {}).get("startTimesOutput", {}).get("nodeId") or "20").strip() or "20",
        "end_times": str(workflow_io_config.get("outputs", {}).get("endTimesOutput", {}).get("nodeId") or "21").strip() or "21",
    }

    if workflow_log_enabled and workflow_log_id > 0:
        update_workflow_log_json(workflow_log_id, workflow_copy)

    result = comfy_request_json(comfy_url=comfy_url, path="/prompt", method="POST", payload={"prompt": workflow_copy})
    prompt_id = str(result.get("prompt_id") or "").strip()
    if not prompt_id:
        raise RuntimeError(f"Failed to submit workflow to ComfyUI: {result}")

    started = time.time()
    timeout_seconds = 30 * 60
    output_text = language_text = timestamps_text = text_list_text = start_times_text = end_times_text = None
    while time.time() - started < timeout_seconds:
        history = comfy_request_json(comfy_url=comfy_url, path=f"/history/{prompt_id}", method="GET")
        history_error = _extract_comfy_history_error(history, prompt_id)
        if history_error:
            raise RuntimeError(history_error)
        output_text = _extract_text_output_from_history(history, prompt_id, output_node_ids["text"])
        language_text = _extract_text_output_from_history(history, prompt_id, output_node_ids["language"])
        timestamps_text = _extract_text_output_from_history(history, prompt_id, output_node_ids["timestamps"])
        text_list_text = _extract_text_output_from_history(history, prompt_id, output_node_ids["text_list"])
        start_times_text = _extract_text_output_from_history(history, prompt_id, output_node_ids["start_times"])
        end_times_text = _extract_text_output_from_history(history, prompt_id, output_node_ids["end_times"])
        if timestamps_text or (text_list_text and start_times_text and end_times_text):
            break
        # Some forced-align chunks may finish with empty outputs (eg. 0 segments).
        # In that case ComfyUI history already contains the node outputs, and we
        # should stop waiting instead of hanging until timeout.
        if (
            _history_has_node_output(history, prompt_id, output_node_ids["timestamps"])
            or _history_has_node_output(history, prompt_id, output_node_ids["text_list"])
            or _history_has_node_output(history, prompt_id, output_node_ids["start_times"])
            or _history_has_node_output(history, prompt_id, output_node_ids["end_times"])
        ):
            break
        time.sleep(2)
    return (
        str(output_text or "").strip(),
        str(language_text or "").strip(),
        str(timestamps_text or "").strip(),
        str(text_list_text or "").strip(),
        str(start_times_text or "").strip(),
        str(end_times_text or "").strip(),
    )


def list_audio_asr_chapters(novel_id: int) -> list[dict]:
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT c.id, c.chapter_num, c.title, c.word_count, c.has_audio, c.audio_duration_seconds,
               t.status, t.asr_file_path, t.error_message, t.updated_at
        FROM chapters c
        LEFT JOIN chapter_asr_tasks t ON t.chapter_id = c.id AND t.novel_id = c.novel_id
        WHERE c.novel_id=?
        ORDER BY c.chapter_num ASC
        """,
        (novel_id,),
    ).fetchall()
    conn.close()
    items = []
    for row in rows:
        rel = str(row["asr_file_path"] or "").strip()
        file_path = (ROOT_DIR / rel).resolve() if rel else None
        has_asr = bool(file_path and file_path.exists() and file_path.is_file())
        status = str(row["status"] or "").strip()
        if not status:
            status = "completed" if has_asr else "idle"
        items.append(
            {
                "chapterId": int(row["id"]),
                "chapterNum": int(row["chapter_num"] or 0),
                "title": str(row["title"] or ""),
                "wordCount": int(row["word_count"] or 0),
                "hasAudio": bool(row["has_audio"]),
                "audioDurationSeconds": float(row["audio_duration_seconds"] or 0),
                "status": status,
                "hasAsr": has_asr,
                "asrFilePath": rel,
                "errorMessage": str(row["error_message"] or ""),
                "updatedAt": str(row["updated_at"] or ""),
                "downloadUrl": f"/api/novels/{novel_id}/chapters/{int(row['chapter_num'] or 0)}/asr-file" if has_asr else "",
            }
        )
    return items


def enqueue_chapter_audio_asr_task(novel_id: int, chapter_id: int) -> tuple[bool, str]:
    conn = db_conn()
    row = conn.execute(
        "SELECT chapter_num, title, audio_file_path FROM chapters WHERE novel_id=? AND id=?",
        (novel_id, chapter_id),
    ).fetchone()
    if not row:
        conn.close()
        return False, "chapter not found"
    if not str(row["audio_file_path"] or "").strip():
        conn.close()
        return False, "chapter audio not found"
    existing = conn.execute(
        "SELECT status FROM chapter_asr_tasks WHERE novel_id=? AND chapter_id=?",
        (novel_id, chapter_id),
    ).fetchone()
    if existing and str(existing["status"] or "") in {"pending", "running", "processing"}:
        conn.close()
        return False, "audio asr task already queued"
    conn.execute(
        """
        INSERT INTO chapter_asr_tasks(
            novel_id, chapter_id, chapter_num, chapter_title, status, progress,
            audio_file_path, asr_file_path, language, extracted_text, timestamps_text,
            error_message, started_at, updated_at
        ) VALUES(?,?,?,?, 'pending', 0, ?, '', '', '', '', '', NULL, CURRENT_TIMESTAMP)
        ON CONFLICT(novel_id, chapter_id) DO UPDATE SET
            chapter_num=excluded.chapter_num,
            chapter_title=excluded.chapter_title,
            status='pending',
            progress=0,
            audio_file_path=excluded.audio_file_path,
            error_message='',
            started_at=NULL,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            novel_id,
            chapter_id,
            int(row["chapter_num"] or 0),
            str(row["title"] or ""),
            str(row["audio_file_path"] or "").strip(),
        ),
    )
    conn.commit()
    conn.close()
    return True, "queued"


def enqueue_batch_audio_asr_tasks(novel_id: int, chapter_nums: list[int] | None = None) -> tuple[bool, str, dict]:
    conn = db_conn()
    query = "SELECT id, chapter_num FROM chapters WHERE novel_id=? AND COALESCE(audio_file_path,'')<>''"
    params: list[Any] = [novel_id]
    if chapter_nums:
        placeholders = ",".join("?" for _ in chapter_nums)
        query += f" AND chapter_num IN ({placeholders})"
        params.extend(chapter_nums)
    query += " ORDER BY chapter_num ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    queued = 0
    skipped = 0
    for row in rows:
        ok, _ = enqueue_chapter_audio_asr_task(novel_id, int(row["id"]))
        if ok:
            queued += 1
        else:
            skipped += 1
    return True, "ok", {"queued": queued, "skipped": skipped, "total": len(rows)}


def process_chapter_audio_asr_task(task_id: int) -> None:
    conn = db_conn()
    row = conn.execute(
        """
        SELECT t.*, c.audio_file_path, c.title, n.english_dir
        FROM chapter_asr_tasks t
        JOIN chapters c ON c.id=t.chapter_id
        JOIN novels n ON n.id=t.novel_id
        WHERE t.id=?
        """,
        (task_id,),
    ).fetchone()
    conn.close()
    if not row:
        return

    workflow_log_id = 0
    try:
        audio_rel_path = str(row["audio_file_path"] or "").strip()
        audio_path = (ROOT_DIR / audio_rel_path).resolve()
        if not audio_path.exists() or not audio_path.is_file():
            raise RuntimeError("chapter audio not found")

        workflow_id, workflow, workflow_io_config, workflow_name, workflow_category, workflow_log_enabled = _get_audio_asr_workflow(int(row["novel_id"]))
        if not workflow:
            raise RuntimeError("audio asr workflow not configured")

        if workflow_log_enabled:
            workflow_log_id = create_workflow_log(workflow_category or "audio_asr", workflow_name or "提取音频ASR", workflow)

        settings_conn = db_conn()
        settings = fetch_settings(settings_conn)
        settings_conn.close()
        comfy_url = str(settings.get("comfyUrl") or "").strip()
        if not comfy_url:
            raise RuntimeError("ComfyUI URL not configured")

        conn = db_conn()
        conn.execute(
            "UPDATE chapter_asr_tasks SET status='processing', progress=20, workflow_id=?, started_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (workflow_id, task_id),
        )
        conn.commit()
        conn.close()

        chunks = _split_audio_for_alignment(audio_path, chunk_seconds=60)
        if not chunks:
            raise RuntimeError("failed to split audio for alignment")

        merged_segments: list[tuple[str, float, float]] = []
        merged_text_parts: list[str] = []
        merged_timestamp_lines: list[str] = []
        detected_language = ""
        total_chunks = len(chunks)

        for chunk_index, (chunk_path, offset_seconds) in enumerate(chunks, start=1):
            output_text, language_text, timestamps_text, text_list_text, start_times_text, end_times_text = _run_asr_workflow_on_audio(
                audio_path=chunk_path,
                workflow=workflow,
                workflow_io_config=workflow_io_config,
                comfy_url=comfy_url,
                workflow_log_enabled=workflow_log_enabled,
                workflow_category=workflow_category,
                workflow_name=workflow_name,
                workflow_log_id=workflow_log_id,
            )
            if output_text:
                merged_text_parts.append(output_text)
            if language_text and not detected_language:
                detected_language = language_text
            chunk_segments = _collect_segments(
                timestamps_text=timestamps_text,
                text_list_text=text_list_text,
                start_times_text=start_times_text,
                end_times_text=end_times_text,
                offset_seconds=offset_seconds,
            )
            merged_segments.extend(chunk_segments)
            for text, start, end in chunk_segments:
                merged_timestamp_lines.append(f"{text}\t{start:.3f}\t{end:.3f}")

            conn = db_conn()
            progress = min(95, 20 + int(round((chunk_index / total_chunks) * 70)))
            conn.execute(
                "UPDATE chapter_asr_tasks SET progress=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (progress, task_id),
            )
            conn.commit()
            conn.close()

        asr_content = _build_asr_content(segments=merged_segments)
        if not asr_content:
            raise RuntimeError("ASR output is empty")

        output_dir = _novel_asr_output_dir(str(row["english_dir"] or ""))
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = _chapter_asr_output_path(str(row["english_dir"] or ""), int(row["chapter_num"] or 0), str(row["chapter_title"] or row["title"] or ""))
        output_path.write_text(asr_content, encoding="utf-8")
        rel_path = str(output_path.relative_to(ROOT_DIR))

        conn = db_conn()
        conn.execute(
            """
            UPDATE chapter_asr_tasks
            SET status='completed', progress=100, asr_file_path=?, language=?, extracted_text=?, timestamps_text=?, error_message='', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                rel_path,
                str(detected_language or "").strip(),
                "\n".join(part for part in merged_text_parts if part).strip(),
                "\n".join(merged_timestamp_lines).strip(),
                task_id,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        update_workflow_log_error(workflow_log_id, str(exc))
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_asr_tasks SET status='failed', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(exc), task_id),
        )
        conn.commit()
        conn.close()


def run_audio_asr_queue_once() -> bool:
    conn = db_conn()
    running = conn.execute(
        "SELECT id FROM chapter_asr_tasks WHERE status IN ('running','processing') ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if running:
        task_id = int(running["id"])
        conn.close()
        process_chapter_audio_asr_task(task_id)
        return True

    pending = conn.execute(
        "SELECT id FROM chapter_asr_tasks WHERE status='pending' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if not pending:
        conn.close()
        return False
    task_id = int(pending["id"])
    conn.execute(
        "UPDATE chapter_asr_tasks SET status='running', progress=5, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()
    process_chapter_audio_asr_task(task_id)
    return True
