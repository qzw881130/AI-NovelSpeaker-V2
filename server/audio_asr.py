"""章节音频 ASR 服务模块"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from .app_context import NOVEL_DIR, ROOT_DIR, db_conn
from .services import (
    apply_prompt_llm_settings,
    call_llm_prompt_json,
    chapter_content,
    comfy_interrupt_execution,
    comfy_request_json,
    comfy_upload_input_file,
    create_workflow_log,
    file_md5_hex,
    fetch_settings,
    load_prompt_llm_settings,
    probe_audio_duration_seconds,
    safe_chapter_file_name,
    sync_system_prompt_from_file,
    touch_task_worker_heartbeat,
    update_workflow_log_error,
    update_workflow_log_json,
    workflow_json_to_prompt_json,
)


class AudioAsrTaskCancelledError(RuntimeError):
    pass


class SubtitleFixTaskCancelledError(RuntimeError):
    pass


SUBTITLE_FIX_WORKER_LOCK = threading.Lock()
SUBTITLE_FIX_WORKER_THREAD: threading.Thread | None = None


def _novel_asr_output_dir(english_dir: str) -> Path:
    return NOVEL_DIR / english_dir / "asr"


def _chapter_asr_output_path(english_dir: str, chapter_num: int, title: str) -> Path:
    name = safe_chapter_file_name(chapter_num, title)
    stem = name[:-4] if name.endswith(".txt") else name
    return _novel_asr_output_dir(english_dir) / f"{stem}.asr"


def _chapter_corrected_srt_output_path(english_dir: str, chapter_num: int, title: str) -> Path:
    name = safe_chapter_file_name(chapter_num, title)
    stem = name[:-4] if name.endswith(".txt") else name
    return _novel_asr_output_dir(english_dir) / f"{stem}.srt"


def _asr_output_exists(rel_path: str) -> bool:
    rel = str(rel_path or "").strip()
    if not rel:
        return False
    file_path = (ROOT_DIR / rel).resolve()
    return bool(file_path.exists() and file_path.is_file())


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
    audio_key = file_md5_hex(audio_path)[:12]
    temp_dir = ROOT_DIR / "temp" / "audio_asr_chunks" / f"{audio_path.stem}_{audio_key}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
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


def _strip_llm_srt_fences(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value).strip()
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _parse_srt_blocks(text: str) -> list[dict]:
    blocks: list[dict] = []
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return blocks
    for raw_block in re.split(r"\n\s*\n+", normalized):
        lines = [line.rstrip() for line in raw_block.split("\n") if line.strip()]
        if len(lines) < 3:
            continue
        index = lines[0].strip()
        time_line = lines[1].strip()
        if "-->" not in time_line:
            continue
        start, end = [part.strip() for part in time_line.split("-->", 1)]
        blocks.append({"index": index, "start": start, "end": end, "text": "\n".join(lines[2:]).strip()})
    return blocks


def _parse_srt_timestamp_seconds(value: str) -> float | None:
    match = re.match(r"^\s*(\d{1,2}):(\d{2}):(\d{2}),(\d{1,3})\s*$", str(value or ""))
    if not match:
        return None
    hours, minutes, seconds, millis = match.groups()
    if int(minutes) >= 60 or int(seconds) >= 60:
        return None
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")[:3]) / 1000


def _srt_error_line(lines: list[str], block_index: int) -> int:
    try:
        return int(str(lines[0] if lines else block_index).strip())
    except (TypeError, ValueError):
        return block_index


def inspect_srt_timing_errors(text: str) -> list[dict[str, int | str]]:
    errors: list[dict[str, int | str]] = []
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return errors
    for block_index, raw_block in enumerate(re.split(r"\n\s*\n+", normalized), start=1):
        lines = [line.strip() for line in raw_block.split("\n") if line.strip()]
        time_line = lines[1] if len(lines) > 1 else ""
        if "-->" not in time_line:
            for line in lines:
                if "-->" in line:
                    time_line = line
                    break
        match = re.match(r"^(.*?)\s*-->\s*(.*?)$", time_line)
        line_no = _srt_error_line(lines, block_index)
        if not match:
            errors.append({"line": line_no, "block": block_index, "time": time_line or "-", "type": "format", "message": "非法时间格式"})
            continue
        start_text, end_text = match.groups()
        start = _parse_srt_timestamp_seconds(start_text)
        end = _parse_srt_timestamp_seconds(end_text)
        if start is None or end is None:
            errors.append({"line": line_no, "block": block_index, "time": time_line, "type": "format", "message": "非法时间格式"})
            continue
        if start < end:
            continue
        errors.append({"line": line_no, "block": block_index, "time": time_line, "type": "order", "message": "开始时间必须小于结束时间"})
    return errors


def _normalize_srt_from_blocks(blocks: list[dict]) -> str:
    parts = []
    for block in blocks:
        parts.append(
            f"{block['index']}\n{block['start']} --> {block['end']}\n{str(block.get('text') or '').strip()}"
        )
    return "\n\n".join(parts).strip() + "\n"


def _srt_text_from_blocks(blocks: list[dict]) -> str:
    return _normalize_srt_from_blocks(blocks).strip()


def _chunk_srt_blocks(blocks: list[dict], batch_size: int = 80) -> list[list[dict]]:
    return [blocks[index : index + batch_size] for index in range(0, len(blocks), batch_size)]


def _validate_corrected_srt(original_text: str, corrected_text: str) -> str:
    original_blocks = _parse_srt_blocks(original_text)
    corrected_blocks = _parse_srt_blocks(corrected_text)
    if not original_blocks:
        raise RuntimeError("ASR字幕内容不是有效SRT格式")
    if len(original_blocks) != len(corrected_blocks):
        raise RuntimeError(
            f"修复后字幕段数不一致：原始 {len(original_blocks)}，修复后 {len(corrected_blocks)}"
        )
    normalized_blocks = []
    for idx, (original, corrected) in enumerate(zip(original_blocks, corrected_blocks), start=1):
        if str(original["index"]) != str(corrected["index"]):
            raise RuntimeError(f"第 {idx} 段序号被修改")
        if str(original["start"]) != str(corrected["start"]) or str(original["end"]) != str(corrected["end"]):
            raise RuntimeError(f"第 {idx} 段时间轴被修改")
        text = str(corrected.get("text") or "").strip()
        if not text:
            raise RuntimeError(f"第 {idx} 段字幕为空")
        normalized_blocks.append({**original, "text": text})
    return _normalize_srt_from_blocks(normalized_blocks)


def _normalize_llm_srt_output(text: str) -> str:
    value = _strip_llm_srt_fences(text)
    if not value.strip():
        raise RuntimeError("LLM response content is empty")
    blocks = _parse_srt_blocks(value)
    if blocks:
        return _normalize_srt_from_blocks(blocks)
    return value.strip() + "\n"


def _build_subtitle_fix_prompt(prompt_template: str, *, novel_text: str, asr_text: str, batch_note: str = "") -> str:
    text = prompt_template.replace("{novel_text}", novel_text).replace("{asr_subtitle}", asr_text)
    if batch_note:
        text += (
            "\n\n补充要求：\n"
            f"{batch_note}\n"
            "你仍然只能输出本批次修正后的 SRT 内容，不要输出任何说明。"
        )
    return text


def _clamp_segments_to_duration(
    segments: list[tuple[str, float, float]], max_duration_seconds: float
) -> list[tuple[str, float, float]]:
    if max_duration_seconds <= 0:
        return list(segments or [])
    clamped: list[tuple[str, float, float]] = []
    max_end = float(max_duration_seconds)
    for text, start, end in segments or []:
        safe_start = max(0.0, min(float(start or 0.0), max_end))
        safe_end = max(safe_start, min(float(end or 0.0), max_end))
        if safe_start >= max_end:
            continue
        clamped.append((text, safe_start, safe_end))
    return clamped


def get_audio_asr_task_status(task_id: int) -> str:
    conn = db_conn()
    row = conn.execute(
        "SELECT status FROM chapter_asr_tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    conn.close()
    return str(row["status"] or "").strip() if row else ""


def ensure_audio_asr_task_not_cancelled(task_id: int) -> None:
    if get_audio_asr_task_status(task_id) == "cancelled":
        raise AudioAsrTaskCancelledError("任务被用户终止")


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
    task_id: int,
) -> tuple[str, str, str, str, str, str]:
    upload_info = comfy_upload_input_file(f"audio_asr_task{task_id}_{audio_path.name}", audio_path.read_bytes())
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
        touch_task_worker_heartbeat()
        ensure_audio_asr_task_not_cancelled(task_id)
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
    if not (SUBTITLE_FIX_WORKER_THREAD and SUBTITLE_FIX_WORKER_THREAD.is_alive()):
        conn.execute(
            """
            UPDATE chapter_asr_tasks
            SET subtitle_fix_status='failed', subtitle_fix_error='字幕修复已中断，请重试',
                subtitle_fix_current_batch_index=0, subtitle_fix_total_batch_count=0,
                updated_at=CURRENT_TIMESTAMP
            WHERE novel_id=?
              AND subtitle_fix_status='processing'
              AND corrected_srt_file_path=''
            """,
            (novel_id,),
        )
    conn.execute(
        """
        UPDATE chapter_asr_tasks
        SET subtitle_fix_status='failed', subtitle_fix_error='字幕修复已中断，请重试',
            subtitle_fix_current_batch_index=0, subtitle_fix_total_batch_count=0,
            updated_at=CURRENT_TIMESTAMP
        WHERE novel_id=?
          AND subtitle_fix_status='processing'
          AND corrected_srt_file_path=''
          AND updated_at < datetime('now', '-60 minutes')
        """,
        (novel_id,),
    )
    conn.commit()
    rows = conn.execute(
        """
        SELECT c.id, c.chapter_num, c.title, c.word_count, c.has_audio, c.audio_duration_seconds,
               t.status, t.asr_file_path, t.error_message, t.updated_at,
               t.current_chunk_index, t.total_chunk_count,
               t.subtitle_fix_status, t.subtitle_fix_error, t.corrected_srt_file_path, t.subtitle_fixed_at,
               t.subtitle_fix_current_batch_index, t.subtitle_fix_total_batch_count
        FROM chapters c
        LEFT JOIN chapter_asr_tasks t ON t.chapter_id = c.id AND t.novel_id = c.novel_id
        WHERE c.novel_id=?
        ORDER BY c.chapter_num ASC
        """,
        (novel_id,),
    ).fetchall()
    conn.close()
    should_start_subtitle_worker = any(str(row["subtitle_fix_status"] or "").strip() == "pending" for row in rows)
    items = []
    for row in rows:
        rel = str(row["asr_file_path"] or "").strip()
        file_path = (ROOT_DIR / rel).resolve() if rel else None
        has_asr = bool(file_path and file_path.exists() and file_path.is_file())
        srt_rel = str(row["corrected_srt_file_path"] or "").strip()
        srt_path = (ROOT_DIR / srt_rel).resolve() if srt_rel else None
        has_corrected_srt = bool(srt_path and srt_path.exists() and srt_path.is_file())
        srt_errors = []
        if has_corrected_srt:
            try:
                srt_errors = inspect_srt_timing_errors(srt_path.read_text(encoding="utf-8"))
            except Exception:
                srt_errors = []
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
                "currentChunkIndex": int(row["current_chunk_index"] or 0),
                "totalChunkCount": int(row["total_chunk_count"] or 0),
                "updatedAt": str(row["updated_at"] or ""),
                "downloadUrl": f"/api/novels/{novel_id}/chapters/{int(row['chapter_num'] or 0)}/asr-file" if has_asr else "",
                "subtitleFixStatus": str(row["subtitle_fix_status"] or ""),
                "subtitleFixError": str(row["subtitle_fix_error"] or ""),
                "subtitleFixCurrentBatchIndex": int(row["subtitle_fix_current_batch_index"] or 0),
                "subtitleFixTotalBatchCount": int(row["subtitle_fix_total_batch_count"] or 0),
                "hasCorrectedSrt": has_corrected_srt,
                "correctedSrtFilePath": srt_rel,
                "correctedSrtUpdatedAt": str(row["subtitle_fixed_at"] or ""),
                "correctedSrtDownloadUrl": f"/api/novels/{novel_id}/chapters/{int(row['chapter_num'] or 0)}/corrected-srt-file" if has_corrected_srt else "",
                "correctedSrtErrorCount": len(srt_errors),
                "correctedSrtErrorLines": [int(error["line"] or 0) for error in srt_errors],
            }
        )
    if should_start_subtitle_worker:
        ensure_subtitle_fix_worker()
    return items


def _get_subtitle_fix_prompt(conn) -> tuple[int, str]:
    row = conn.execute(
        """
        SELECT id, content
        FROM json_prompts
        WHERE prompt_category='subtitle_fix'
        ORDER BY CASE WHEN prompt_type='user' THEN 0 ELSE 1 END, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        raise RuntimeError("修复字幕提示词未配置")
    content = str(row["content"] or "").strip()
    if not content:
        raise RuntimeError("修复字幕提示词内容为空")
    return int(row["id"]), content


def ensure_subtitle_fix_task_not_cancelled(task_id: int) -> None:
    conn = db_conn()
    row = conn.execute(
        "SELECT subtitle_fix_status FROM chapter_asr_tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    conn.close()
    if row and str(row["subtitle_fix_status"] or "").strip() == "cancelled":
        raise SubtitleFixTaskCancelledError("字幕修复已终止")


def repair_chapter_audio_asr_subtitle(novel_id: int, chapter_id: int) -> dict:
    conn = db_conn()
    row = conn.execute(
        """
        SELECT t.id AS task_id, t.asr_file_path, c.chapter_num, c.title, c.text_file_path, n.english_dir
        FROM chapters c
        JOIN novels n ON n.id=c.novel_id
        LEFT JOIN chapter_asr_tasks t ON t.chapter_id=c.id AND t.novel_id=c.novel_id
        WHERE c.novel_id=? AND c.id=?
        """,
        (novel_id, chapter_id),
    ).fetchone()
    if not row:
        conn.close()
        raise RuntimeError("chapter not found")
    task_id = int(row["task_id"] or 0)
    if not task_id:
        conn.close()
        raise RuntimeError("ASR任务不存在，请先提取ASR")
    asr_rel = str(row["asr_file_path"] or "").strip()
    asr_path = (ROOT_DIR / asr_rel).resolve() if asr_rel else None
    if not asr_path or not asr_path.exists() or not asr_path.is_file():
        conn.close()
        raise RuntimeError("ASR文件不存在，请先提取ASR")
    sync_system_prompt_from_file(conn)
    conn.commit()
    prompt_id, prompt_template = _get_subtitle_fix_prompt(conn)
    settings = fetch_settings(conn)
    llm = apply_prompt_llm_settings(settings.get("llm") or {}, load_prompt_llm_settings(conn, prompt_id))
    proxy_url = str(settings.get("proxyUrl") or "").strip()
    conn.execute(
        "UPDATE chapter_asr_tasks SET subtitle_fix_status='processing', subtitle_fix_error='', subtitle_fix_current_batch_index=0, subtitle_fix_total_batch_count=0, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()

    try:
        ensure_subtitle_fix_task_not_cancelled(task_id)
        novel_text = chapter_content(
            str(row["english_dir"] or ""),
            int(row["chapter_num"] or 0),
            str(row["title"] or ""),
            str(row["text_file_path"] or ""),
        ).strip()
        asr_text = asr_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not novel_text:
            raise RuntimeError("小说正文为空")
        if not asr_text:
            raise RuntimeError("ASR字幕为空")
        original_blocks = _parse_srt_blocks(asr_text)
        if not original_blocks:
            raise RuntimeError("ASR字幕内容不是有效SRT格式")
        corrected_parts: list[str] = []
        batches = _chunk_srt_blocks(original_blocks)
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_asr_tasks SET subtitle_fix_current_batch_index=0, subtitle_fix_total_batch_count=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (len(batches), task_id),
        )
        conn.commit()
        conn.close()
        for batch_index, batch_blocks in enumerate(batches, start=1):
            ensure_subtitle_fix_task_not_cancelled(task_id)
            batch_asr_text = _srt_text_from_blocks(batch_blocks)
            first_index = str(batch_blocks[0].get("index") or "")
            last_index = str(batch_blocks[-1].get("index") or "")
            batch_note = ""
            if len(batches) > 1:
                batch_note = (
                    f"当前是字幕分批校正 {batch_index}/{len(batches)}，"
                    f"只处理序号 {first_index} 到 {last_index} 的字幕。"
                    "不得输出其他批次字幕。"
                )
            raw_output = call_llm_prompt_json(
                llm=llm,
                proxy_url=proxy_url,
                system_prompt="你只输出SRT字幕内容，不输出解释、Markdown或分析过程。",
                user_prompt=_build_subtitle_fix_prompt(
                    prompt_template,
                    novel_text=novel_text,
                    asr_text=batch_asr_text,
                    batch_note=batch_note,
                ),
            )
            ensure_subtitle_fix_task_not_cancelled(task_id)
            corrected_parts.append(_normalize_llm_srt_output(raw_output).strip())
            conn = db_conn()
            conn.execute(
                "UPDATE chapter_asr_tasks SET subtitle_fix_current_batch_index=?, subtitle_fix_total_batch_count=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (batch_index, len(batches), task_id),
            )
            conn.commit()
            conn.close()
        ensure_subtitle_fix_task_not_cancelled(task_id)
        corrected_srt = _normalize_llm_srt_output("\n\n".join(corrected_parts))
        output_dir = _novel_asr_output_dir(str(row["english_dir"] or ""))
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = _chapter_corrected_srt_output_path(
            str(row["english_dir"] or ""),
            int(row["chapter_num"] or 0),
            str(row["title"] or ""),
        )
        output_path.write_text(corrected_srt, encoding="utf-8")
        rel_path = str(output_path.relative_to(ROOT_DIR))
        conn = db_conn()
        conn.execute(
            """
            UPDATE chapter_asr_tasks
            SET subtitle_fix_status='completed', subtitle_fix_error='', corrected_srt_file_path=?,
                subtitle_fix_current_batch_index=subtitle_fix_total_batch_count,
                subtitle_fixed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (rel_path, task_id),
        )
        conn.commit()
        conn.close()
        return {"status": "completed", "correctedSrtFilePath": rel_path}
    except SubtitleFixTaskCancelledError:
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_asr_tasks SET subtitle_fix_status='cancelled', subtitle_fix_error='字幕修复已终止', subtitle_fix_current_batch_index=0, subtitle_fix_total_batch_count=0, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (task_id,),
        )
        conn.commit()
        conn.close()
        return {"status": "cancelled"}
    except Exception as exc:
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_asr_tasks SET subtitle_fix_status='failed', subtitle_fix_error=?, subtitle_fix_current_batch_index=0, subtitle_fix_total_batch_count=0, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(exc), task_id),
        )
        conn.commit()
        conn.close()
        raise


def cancel_chapter_audio_asr_subtitle_repair(novel_id: int, chapter_id: int) -> tuple[bool, str]:
    conn = db_conn()
    row = conn.execute(
        "SELECT id, subtitle_fix_status FROM chapter_asr_tasks WHERE novel_id=? AND chapter_id=?",
        (novel_id, chapter_id),
    ).fetchone()
    if not row:
        conn.close()
        return False, "ASR任务不存在"
    status = str(row["subtitle_fix_status"] or "").strip()
    if status not in {"pending", "processing"}:
        conn.close()
        return False, "只有待修复或修复中的字幕任务可以终止"
    conn.execute(
        """
        UPDATE chapter_asr_tasks
        SET subtitle_fix_status='cancelled', subtitle_fix_error='字幕修复已终止',
            subtitle_fix_current_batch_index=0, subtitle_fix_total_batch_count=0,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (int(row["id"]),),
    )
    conn.commit()
    conn.close()
    return True, "cancelled"


def enqueue_chapter_audio_asr_subtitle_repair(novel_id: int, chapter_id: int) -> tuple[bool, str, dict]:
    conn = db_conn()
    row = conn.execute(
        """
        SELECT t.id AS task_id, t.asr_file_path, t.subtitle_fix_status
        FROM chapters c
        LEFT JOIN chapter_asr_tasks t ON t.chapter_id=c.id AND t.novel_id=c.novel_id
        WHERE c.novel_id=? AND c.id=?
        """,
        (novel_id, chapter_id),
    ).fetchone()
    if not row:
        conn.close()
        return False, "chapter not found", {}
    task_id = int(row["task_id"] or 0)
    if not task_id:
        conn.close()
        return False, "ASR任务不存在，请先提取ASR", {}
    current_status = str(row["subtitle_fix_status"] or "").strip()
    if current_status in {"pending", "processing"}:
        conn.close()
        ensure_subtitle_fix_worker()
        return True, "subtitle repair already queued", {"action": "skipped", "reason": "already_queued"}
    asr_rel = str(row["asr_file_path"] or "").strip()
    asr_path = (ROOT_DIR / asr_rel).resolve() if asr_rel else None
    if not asr_path or not asr_path.exists() or not asr_path.is_file():
        conn.close()
        return False, "ASR文件不存在，请先提取ASR", {}
    conn.execute(
        """
        UPDATE chapter_asr_tasks
        SET subtitle_fix_status='pending', subtitle_fix_error='', corrected_srt_file_path='',
            subtitle_fix_current_batch_index=0, subtitle_fix_total_batch_count=0,
            subtitle_fixed_at=NULL, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (task_id,),
    )
    conn.commit()
    conn.close()
    ensure_subtitle_fix_worker()
    return True, "queued", {"action": "queued"}


def run_subtitle_fix_queue_once() -> bool:
    conn = db_conn()
    running = conn.execute(
        "SELECT id FROM chapter_asr_tasks WHERE subtitle_fix_status='processing' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if running:
        conn.close()
        return False
    row = conn.execute(
        "SELECT novel_id, chapter_id FROM chapter_asr_tasks WHERE subtitle_fix_status='pending' ORDER BY updated_at ASC, id ASC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return False
    try:
        repair_chapter_audio_asr_subtitle(int(row["novel_id"]), int(row["chapter_id"]))
    except Exception:
        pass
    return True


def subtitle_fix_worker_loop() -> None:
    while True:
        if not run_subtitle_fix_queue_once():
            return


def ensure_subtitle_fix_worker() -> None:
    global SUBTITLE_FIX_WORKER_THREAD
    with SUBTITLE_FIX_WORKER_LOCK:
        if SUBTITLE_FIX_WORKER_THREAD and SUBTITLE_FIX_WORKER_THREAD.is_alive():
            return
        SUBTITLE_FIX_WORKER_THREAD = threading.Thread(target=subtitle_fix_worker_loop, daemon=True)
        SUBTITLE_FIX_WORKER_THREAD.start()


def enqueue_chapter_audio_asr_task(
    novel_id: int, chapter_id: int, *, force_extract: bool = False
) -> tuple[bool, str, dict]:
    conn = db_conn()
    row = conn.execute(
        "SELECT chapter_num, title, audio_file_path FROM chapters WHERE novel_id=? AND id=?",
        (novel_id, chapter_id),
    ).fetchone()
    if not row:
        conn.close()
        return False, "chapter not found", {"action": "error"}
    audio_rel_path = str(row["audio_file_path"] or "").strip()
    if not audio_rel_path:
        conn.close()
        return False, "chapter audio not found", {"action": "error"}
    audio_path = (ROOT_DIR / audio_rel_path).resolve()
    if not audio_path.exists() or not audio_path.is_file():
        conn.close()
        return False, "chapter audio not found", {"action": "error"}
    current_audio_md5 = file_md5_hex(audio_path)
    existing = conn.execute(
        "SELECT status, audio_file_md5, asr_file_path FROM chapter_asr_tasks WHERE novel_id=? AND chapter_id=?",
        (novel_id, chapter_id),
    ).fetchone()
    if existing and str(existing["status"] or "") in {"pending", "running", "processing"}:
        conn.close()
        return False, "audio asr task already queued", {"action": "error"}
    if (
        not force_extract
        and existing
        and str(existing["status"] or "") == "completed"
        and str(existing["audio_file_md5"] or "").strip()
        and str(existing["audio_file_md5"] or "").strip() == current_audio_md5
        and _asr_output_exists(str(existing["asr_file_path"] or ""))
    ):
        conn.close()
        return True, "audio unchanged", {"action": "skipped", "reason": "unchanged"}
    conn.execute(
        """
        INSERT INTO chapter_asr_tasks(
            novel_id, chapter_id, chapter_num, chapter_title, status, progress,
            audio_file_path, audio_file_md5, force_extract, asr_file_path, language,
            extracted_text, timestamps_text, error_message, started_at, updated_at,
            current_chunk_index, total_chunk_count
        ) VALUES(?,?,?,?, 'pending', 0, ?, ?, ?, '', '', '', '', '', NULL, CURRENT_TIMESTAMP, 0, 0)
        ON CONFLICT(novel_id, chapter_id) DO UPDATE SET
            chapter_num=excluded.chapter_num,
            chapter_title=excluded.chapter_title,
            status='pending',
            progress=0,
            audio_file_path=excluded.audio_file_path,
            force_extract=excluded.force_extract,
            current_chunk_index=0,
            total_chunk_count=0,
            error_message='',
            started_at=NULL,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            novel_id,
            chapter_id,
            int(row["chapter_num"] or 0),
            str(row["title"] or ""),
            audio_rel_path,
            str(existing["audio_file_md5"] or "").strip() if existing else "",
            1 if force_extract else 0,
        ),
    )
    conn.commit()
    conn.close()
    return True, "queued", {"action": "queued"}


def enqueue_batch_audio_asr_tasks(
    novel_id: int,
    chapter_nums: list[int] | None = None,
    *,
    force_extract: bool = False,
) -> tuple[bool, str, dict]:
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
    skipped_unchanged = 0
    for row in rows:
        ok, _, data = enqueue_chapter_audio_asr_task(
            novel_id,
            int(row["id"]),
            force_extract=force_extract,
        )
        if ok:
            if str((data or {}).get("action") or "") == "queued":
                queued += 1
            else:
                skipped += 1
                if str((data or {}).get("reason") or "") == "unchanged":
                    skipped_unchanged += 1
        else:
            skipped += 1
    return True, "ok", {
        "queued": queued,
        "skipped": skipped,
        "skippedUnchanged": skipped_unchanged,
        "total": len(rows),
    }


def cancel_chapter_audio_asr_task(novel_id: int, chapter_id: int) -> tuple[bool, str]:
    conn = db_conn()
    row = conn.execute(
        "SELECT id, status FROM chapter_asr_tasks WHERE novel_id=? AND chapter_id=?",
        (novel_id, chapter_id),
    ).fetchone()
    if not row:
        conn.close()
        return False, "audio asr task not found"
    status = str(row["status"] or "").strip()
    if status == "cancelled":
        conn.close()
        return True, "cancelled"
    if status not in {"pending", "running", "processing"}:
        conn.close()
        return False, "only pending or running task can be cancelled"
    conn.execute(
        """
        UPDATE chapter_asr_tasks
        SET status='cancelled', progress=0, error_message='任务被用户终止', current_chunk_index=0, total_chunk_count=0, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (int(row["id"]),),
    )
    conn.commit()
    conn.close()

    settings_conn = db_conn()
    settings = fetch_settings(settings_conn)
    settings_conn.close()
    comfy_url = str(settings.get("comfyUrl") or "").strip()
    if comfy_url:
        try:
            comfy_interrupt_execution(comfy_url)
        except Exception:
            pass
    return True, "cancelled"


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
        touch_task_worker_heartbeat(made_progress=True)
        ensure_audio_asr_task_not_cancelled(task_id)
        audio_rel_path = str(row["audio_file_path"] or "").strip()
        audio_path = (ROOT_DIR / audio_rel_path).resolve()
        if not audio_path.exists() or not audio_path.is_file():
            raise RuntimeError("chapter audio not found")
        current_audio_md5 = file_md5_hex(audio_path)
        audio_duration_seconds = probe_audio_duration_seconds(audio_path)

        if (
            not bool(int(row["force_extract"] or 0))
            and str(row["status"] or "") in {"pending", "running", "processing", "completed"}
            and str(row["audio_file_md5"] or "").strip()
            and str(row["audio_file_md5"] or "").strip() == current_audio_md5
            and _asr_output_exists(str(row["asr_file_path"] or ""))
        ):
            conn = db_conn()
            conn.execute(
                """
                UPDATE chapter_asr_tasks
                SET status='completed', progress=100, error_message='', force_extract=0,
                    current_chunk_index=0, total_chunk_count=0, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (task_id,),
            )
            conn.commit()
            conn.close()
            touch_task_worker_heartbeat(made_progress=True)
            return

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
            "UPDATE chapter_asr_tasks SET status='processing', progress=20, workflow_id=?, started_at=CURRENT_TIMESTAMP, current_chunk_index=0, total_chunk_count=0, updated_at=CURRENT_TIMESTAMP WHERE id=?",
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
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_asr_tasks SET total_chunk_count=?, current_chunk_index=0, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (total_chunks, task_id),
        )
        conn.commit()
        conn.close()

        for chunk_index, (chunk_path, offset_seconds) in enumerate(chunks, start=1):
            touch_task_worker_heartbeat()
            ensure_audio_asr_task_not_cancelled(task_id)
            output_text, language_text, timestamps_text, text_list_text, start_times_text, end_times_text = _run_asr_workflow_on_audio(
                audio_path=chunk_path,
                workflow=workflow,
                workflow_io_config=workflow_io_config,
                comfy_url=comfy_url,
                workflow_log_enabled=workflow_log_enabled,
                workflow_category=workflow_category,
                workflow_name=workflow_name,
                workflow_log_id=workflow_log_id,
                task_id=task_id,
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
                "UPDATE chapter_asr_tasks SET progress=?, current_chunk_index=?, total_chunk_count=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (progress, chunk_index, total_chunks, task_id),
            )
            conn.commit()
            conn.close()
            touch_task_worker_heartbeat(made_progress=True)

        ensure_audio_asr_task_not_cancelled(task_id)
        merged_segments = _clamp_segments_to_duration(
            merged_segments,
            audio_duration_seconds,
        )
        merged_timestamp_lines = [
            f"{text}\t{start:.3f}\t{end:.3f}"
            for text, start, end in merged_segments
        ]
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
            SET status='completed', progress=100, asr_file_path=?, audio_file_md5=?, force_extract=0,
                language=?, extracted_text=?, timestamps_text=?, error_message='',
                current_chunk_index=?, total_chunk_count=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                rel_path,
                current_audio_md5,
                str(detected_language or "").strip(),
                "\n".join(part for part in merged_text_parts if part).strip(),
                "\n".join(merged_timestamp_lines).strip(),
                total_chunks,
                total_chunks,
                task_id,
            ),
        )
        conn.commit()
        conn.close()
        touch_task_worker_heartbeat(made_progress=True)
    except AudioAsrTaskCancelledError:
        touch_task_worker_heartbeat(made_progress=True)
    except Exception as exc:
        update_workflow_log_error(workflow_log_id, str(exc))
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_asr_tasks SET status='failed', error_message=?, force_extract=0, current_chunk_index=0, total_chunk_count=0, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(exc), task_id),
        )
        conn.commit()
        conn.close()
        touch_task_worker_heartbeat(made_progress=True)


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
        "UPDATE chapter_asr_tasks SET status='running', progress=5, current_chunk_index=0, total_chunk_count=0, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()
    process_chapter_audio_asr_task(task_id)
    return True
