from __future__ import annotations

import json
import random
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .app_context import ROOT_DIR, db_conn
from .services import (
    apply_prompt_llm_settings,
    build_llm_prompt_json_request,
    call_llm_prompt_json,
    comfy_download_file,
    comfy_request_json,
    db_rel_path,
    fetch_settings,
    load_prompt_llm_settings,
    read_chapter_text,
    touch_illustration_image_worker_heartbeat,
    workflow_json_to_prompt_json,
)

ILLUSTRATION_STAGES = {"scene", "shot", "prompt"}
PROMPT_BATCH_SIZE = 10
SCENE_AUDIO_DIFF_WARNING_SECONDS = 8
STAGE_PROMPT_CATEGORY = {
    "scene": "illustration_scene",
    "shot": "illustration_shot",
    "prompt": "illustration_prompt",
}
STAGE_NOVEL_PROMPT_COLUMN = {
    "scene": "illustration_scene_prompt_id",
    "shot": "illustration_shot_prompt_id",
    "prompt": "illustration_prompt_prompt_id",
}
ILLUSTRATION_IMAGE_QUEUE_LOCK = threading.Lock()
ILLUSTRATION_LLM_QUEUE_LOCK = threading.Lock()
DEFAULT_VISUAL_STYLE = "3D皮克斯动画电影风格"


def _status_value(value: str | None) -> str:
    status = str(value or "").strip()
    return status or "idle"


def _lookup_stage_prompt(conn, stage: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM json_prompts WHERE prompt_category=? ORDER BY CASE WHEN prompt_type='system' THEN 0 ELSE 1 END, id DESC LIMIT 1",
        (STAGE_PROMPT_CATEGORY[stage],),
    ).fetchone()
    return int(row["id"]) if row else None


def _lookup_novel_stage_prompt(conn, novel_id: int, stage: str) -> int | None:
    column = STAGE_NOVEL_PROMPT_COLUMN.get(stage)
    if column:
        row = conn.execute(f"SELECT {column} AS prompt_id FROM novels WHERE id=?", (novel_id,)).fetchone()
        if row and row["prompt_id"] is not None:
            return int(row["prompt_id"])
    return _lookup_stage_prompt(conn, stage)


def _read_subtitle_timeline_text(conn, novel_id: int, chapter_num: int) -> tuple[str, str]:
    row = conn.execute(
        "SELECT asr_file_path,timestamps_text,extracted_text,corrected_srt_file_path FROM chapter_asr_tasks WHERE novel_id=? AND chapter_num=? ORDER BY id DESC LIMIT 1",
        (novel_id, chapter_num),
    ).fetchone()
    if not row:
        return "", ""
    srt_rel = str(row["corrected_srt_file_path"] or "").strip()
    if srt_rel:
        srt_path = (ROOT_DIR / srt_rel).resolve()
        if srt_path.exists() and srt_path.is_file():
            return srt_path.read_text(encoding="utf-8", errors="ignore").strip(), "SRT"
    rel = str(row["asr_file_path"] or "").strip()
    if rel:
        path = (ROOT_DIR / rel).resolve()
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8", errors="ignore").strip(), "ASR"
    fallback = str(row["timestamps_text"] or row["extracted_text"] or "").strip()
    return fallback, "ASR" if fallback else ""


def _format_asr_time(value: str) -> str:
    text = str(value or "").strip().replace(",", ".")
    main = text.split(".", 1)[0]
    parts = main.split(":")
    if len(parts) == 3:
        return ":".join(part.zfill(2) for part in parts)
    return main


def _format_asr_seconds(value: str) -> str:
    seconds = max(0, int(float(str(value or "0").strip())))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _preprocess_asr_timeline(raw: str) -> str:
    lines = [line.strip() for line in str(raw or "").splitlines()]
    items = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line or line.isdigit():
            i += 1
            continue
        if "-->" not in line:
            i += 1
            continue
        start_raw, end_raw = [part.strip() for part in line.split("-->", 1)]
        i += 1
        text_parts = []
        while i < len(lines):
            text_line = lines[i]
            if not text_line:
                i += 1
                break
            if text_line.isdigit() and i + 1 < len(lines) and "-->" in lines[i + 1]:
                break
            if "-->" in text_line:
                break
            text_parts.append(text_line)
            i += 1
        text = "".join(text_parts).strip()
        if text:
            items.append(f"[{_format_asr_time(start_raw)}-{_format_asr_time(end_raw)}] {text}")
    if items:
        return "\n".join(items)
    for line in lines:
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) < 3:
            continue
        text = "\t".join(parts[:-2]).strip()
        try:
            start = _format_asr_seconds(parts[-2])
            end = _format_asr_seconds(parts[-1])
        except ValueError:
            continue
        if text:
            items.append(f"[{start}-{end}] {text}")
    return "\n".join(items)


def _build_user_input(conn, stage: str, row) -> str:
    if stage == "scene":
        chapter_text = read_chapter_text(str(row["text_file_path"] or ""))
        timeline_raw, timeline_source = _read_subtitle_timeline_text(conn, int(row["novel_id"]), int(row["chapter_num"]))
        asr_text = _preprocess_asr_timeline(timeline_raw)
        if not asr_text.strip():
            raise RuntimeError("缺少SRT/ASR时间轴，请先完成音频ASR或修复字幕")
        novel = conn.execute("SELECT visual_style FROM novels WHERE id=?", (int(row["novel_id"]),)).fetchone()
        visual_style = str(novel["visual_style"] if novel else "" or DEFAULT_VISUAL_STYLE).strip() or DEFAULT_VISUAL_STYLE
        return (
            f"插图风格（visual_style）：\n{visual_style}\n\n"
            f"章节名称：\n{str(row['chapter_title'] or '')}\n\n"
            f"小说章节内容：\n{chapter_text}\n\n"
            f"{timeline_source or 'ASR'}时间轴：\n{asr_text}\n"
        )
    scene_json = _get_completed_result(conn, int(row["novel_id"]), int(row["chapter_id"]), "scene")
    if stage == "shot":
        return str(scene_json or "")
    shot_json = _get_completed_result(conn, int(row["novel_id"]), int(row["chapter_id"]), "shot")
    return f"scene.json：\n{scene_json}\n\nshot.json：\n{shot_json}\n"


def _get_completed_result(conn, novel_id: int, chapter_id: int, stage: str) -> str:
    row = conn.execute(
        "SELECT result_json_text FROM chapter_illustration_tasks WHERE novel_id=? AND chapter_id=? AND stage=? AND status='completed'",
        (novel_id, chapter_id, stage),
    ).fetchone()
    return str(row["result_json_text"] or "").strip() if row else ""


def _parse_json_any(raw: str):
    text = str(raw or "").strip()
    candidates = [text]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    raise ValueError(f"Model output is not valid JSON. Raw head: {text[:240]}")


def _json_dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _prompt_items(parsed) -> list:
    if isinstance(parsed, dict):
        prompts = parsed.get("prompts")
    else:
        prompts = parsed
    return prompts if isinstance(prompts, list) else []


def _shot_items(parsed) -> list:
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("shots", "shot", "grid"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value
    return []


def _chunked(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _build_prompt_batch_input(scene_parsed: dict, shot_parsed, scene_grid: list, shot_grid: list) -> str:
    batch_scene = {
        "chapter": scene_parsed.get("chapter", ""),
        "grid_count": len(scene_grid),
        "global_style": scene_parsed.get("global_style") or {},
        "character_registry": scene_parsed.get("character_registry") or [],
        "grid": scene_grid,
    }
    if isinstance(shot_parsed, dict):
        batch_shot = {key: value for key, value in shot_parsed.items() if key not in {"shots", "shot", "grid"}}
        batch_shot["shots"] = shot_grid
    else:
        batch_shot = shot_grid
    return f"scene.json：\n{_json_dumps(batch_scene)}\n\nshot.json：\n{_json_dumps(batch_shot)}\n"


def build_image_prompt(item: dict) -> str:
    parts = [
        str(item.get("positive_style") or "").strip(),
        str(item.get("positive_core") or "").strip(),
        str(item.get("positive_character") or "").strip(),
        str(item.get("positive_camera") or "").strip(),
        str(item.get("positive_scene") or "").strip(),
    ]
    prompt = "，".join(part for part in parts if part)
    try:
        human_count = int(item.get("human_count") or 0)
    except (TypeError, ValueError):
        human_count = 0
    if human_count > 1:
        prompt = f"{prompt}；画面中{human_count}人" if prompt else f"画面中{human_count}人"
    return prompt


def _character_names(item: dict) -> str:
    card = item.get("visual_character_card")
    if isinstance(card, dict):
        return "、".join(str(key) for key in card.keys() if str(key).strip())
    return ""


def _scene_grid_meta(conn, novel_id: int, chapter_id: int) -> dict[int, dict]:
    raw = _get_completed_result(conn, novel_id, chapter_id, "scene")
    if not raw:
        return {}
    try:
        parsed = _parse_json_any(raw)
    except Exception:
        return {}
    grid = parsed.get("grid") if isinstance(parsed, dict) else []
    if not isinstance(grid, list):
        return {}
    meta = {}
    for pos, item in enumerate(grid, start=1):
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index") or pos)
            start = _scene_timecode_to_seconds(item.get("start"))
            end = _scene_timecode_to_seconds(item.get("end"))
            duration = max(0.0, end - start)
        except (TypeError, ValueError):
            continue
        meta[idx] = {"start": start, "end": end, "duration": duration}
    return meta


def _scene_timecode_to_seconds(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value if value is not None else "").strip()
    if not raw:
        raise ValueError("empty timecode")
    if ":" not in raw:
        return float(raw)
    parts = raw.split(":")
    if len(parts) != 3:
        raise ValueError(f"invalid HH:MM:SS timecode: {raw}")
    hours, minutes, seconds = (int(part) for part in parts)
    if minutes < 0 or minutes >= 60 or seconds < 0 or seconds >= 60 or hours < 0:
        raise ValueError(f"invalid HH:MM:SS timecode: {raw}")
    return float(hours * 3600 + minutes * 60 + seconds)


def _safe_unlink_under_root(path: Path) -> None:
    try:
        resolved = path.resolve()
        if resolved.is_file() and resolved.is_relative_to(ROOT_DIR.resolve()):
            resolved.unlink()
    except Exception:
        pass


def _delete_illustration_image_files(conn, novel_id: int, chapter_id: int) -> None:
    rows = conn.execute(
        "SELECT image_file_path FROM chapter_illustration_images WHERE novel_id=? AND chapter_id=?",
        (novel_id, chapter_id),
    ).fetchall()
    touched_dirs: set[Path] = set()
    for row in rows:
        rel = str(row["image_file_path"] or "").strip()
        if not rel:
            continue
        path = ROOT_DIR / rel
        touched_dirs.add(path.parent)
        _safe_unlink_under_root(path)
    chapter = conn.execute(
        """
        SELECT n.english_dir, c.chapter_num
        FROM chapters c
        JOIN novels n ON n.id=c.novel_id
        WHERE c.id=? AND c.novel_id=?
        """,
        (chapter_id, novel_id),
    ).fetchone()
    if chapter:
        chapter_dir = ROOT_DIR / "novel" / str(chapter["english_dir"] or "") / "illustrations" / f"{int(chapter['chapter_num'] or 0):03d}"
        if chapter_dir.exists() and chapter_dir.is_dir():
            touched_dirs.add(chapter_dir)
            for child in chapter_dir.iterdir():
                if child.is_file() and child.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                    _safe_unlink_under_root(child)
    for directory in touched_dirs:
        try:
            resolved = directory.resolve()
            if resolved.is_dir() and resolved.is_relative_to(ROOT_DIR.resolve()) and not any(resolved.iterdir()):
                resolved.rmdir()
        except Exception:
            pass


def _write_optimized_illustration_image(data: bytes, source_filename: str, out_dir: Path, item_index: int) -> Path:
    ext = Path(source_filename).suffix or ".png"
    fallback_path = out_dir / f"{item_index:02d}{ext}"
    cwebp = shutil.which("cwebp")
    if not cwebp:
        fallback_path.write_bytes(data)
        return fallback_path

    temp_path = out_dir / f"{item_index:02d}.source{ext}"
    webp_path = out_dir / f"{item_index:02d}.webp"
    temp_path.write_bytes(data)
    try:
        result = subprocess.run(
            [cwebp, "-quiet", "-q", "86", "-m", "6", str(temp_path), "-o", str(webp_path)],
            check=False,
            capture_output=True,
            timeout=60,
        )
        if result.returncode == 0 and webp_path.exists() and webp_path.stat().st_size > 0 and webp_path.stat().st_size < len(data):
            return webp_path
        fallback_path.write_bytes(data)
        try:
            webp_path.unlink()
        except Exception:
            pass
        return fallback_path
    except Exception:
        fallback_path.write_bytes(data)
        return fallback_path
    finally:
        try:
            temp_path.unlink()
        except Exception:
            pass


def _extract_image_output(history: dict, prompt_id: str, output_node_id: str = "") -> tuple[str, str, str] | None:
    job = history.get(prompt_id) if isinstance(history, dict) else None
    if job is None and isinstance(history, dict) and history:
        job = next(iter(history.values()))
    outputs = job.get("outputs") if isinstance(job, dict) else None
    if not isinstance(outputs, dict):
        return None

    output_key = str(output_node_id or "").strip()
    nodes = []
    if output_key and isinstance(outputs.get(output_key), dict):
        nodes.append(outputs[output_key])
    nodes.extend(node for key, node in outputs.items() if str(key) != output_key)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        images = node.get("images")
        if not isinstance(images, list) or not images:
            continue
        first = images[0]
        if not isinstance(first, dict):
            continue
        filename = str(first.get("filename") or "").strip()
        if filename:
            return filename, str(first.get("subfolder") or "").strip(), str(first.get("type") or "output").strip() or "output"
    return None


def _extract_comfy_history_error(history: dict, prompt_id: str) -> str | None:
    if not isinstance(history, dict) or not history:
        return None
    job = history.get(prompt_id)
    if job is None:
        job = next(iter(history.values())) if history else None
    if not isinstance(job, dict):
        return None
    status = job.get("status")
    if not isinstance(status, dict):
        return None
    messages = status.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, (list, tuple)) or not message:
                continue
            message_type = str(message[0] or "").strip().lower()
            payload = message[1] if len(message) > 1 and isinstance(message[1], dict) else {}
            if "error" not in message_type and message_type != "execution_interrupted":
                continue
            details = [
                str(payload.get("exception_message") or "").strip(),
                str(payload.get("node_errors") or "").strip(),
                str(payload.get("error") or payload.get("message") or "").strip(),
            ]
            detail_text = next((item for item in details if item), "")
            return detail_text or message_type or "ComfyUI workflow failed"
    status_str = str(status.get("status_str") or "").strip().lower()
    if status_str in {"error", "failed", "execution_error", "execution_interrupted"}:
        return str(status.get("status_str") or "ComfyUI workflow failed")
    return None


def _safe_filename_part(value: str, fallback: str = "novel") -> str:
    text = str(value or "").strip()
    safe = "".join(ch for ch in text if ch.isalnum() or ch in {"_", "-"})
    return safe or fallback


def _set_workflow_node_input(workflow: dict, node_id: str, value, preferred_keys: tuple[str, ...]) -> None:
    node_key = str(node_id or "").strip()
    if not node_key or not isinstance(workflow.get(node_key), dict):
        return
    inputs = workflow[node_key].setdefault("inputs", {})
    if not isinstance(inputs, dict):
        return
    for key in preferred_keys:
        if key in inputs:
            inputs[key] = value
            return
    if preferred_keys:
        inputs[preferred_keys[0]] = value


def _randomize_workflow_seeds(workflow: dict) -> None:
    rng = random.SystemRandom()
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for key in ("seed", "noise_seed"):
            if key in inputs:
                inputs[key] = rng.randint(0, 2**31 - 1)
        if "control_after_generate" in inputs:
            inputs["control_after_generate"] = "randomize"


def _apply_workflow_inputs(
    workflow: dict,
    prompt_text: str,
    width: int = 1536,
    height: int = 864,
    filename_prefix: str = "",
    prompt_node_id: str = "",
    width_node_id: str = "",
    height_node_id: str = "",
    output_node_id: str = "",
) -> dict:
    patched = json.loads(json.dumps(workflow, ensure_ascii=False))
    prompt_key = str(prompt_node_id or "").strip() or ("12" if "12" in patched else "")
    width_key = str(width_node_id or "").strip() or ("13" if "13" in patched else "")
    height_key = str(height_node_id or "").strip() or ("14" if "14" in patched else "")
    _set_workflow_node_input(patched, prompt_key, prompt_text, ("prompt", "text", "value"))
    _set_workflow_node_input(patched, width_key, str(width), ("Number", "value", "width"))
    _set_workflow_node_input(patched, height_key, str(height), ("Number", "value", "height"))
    _randomize_workflow_seeds(patched)
    if filename_prefix:
        output_key = str(output_node_id or "").strip()
        save_node = patched.get(output_key) if output_key and isinstance(patched.get(output_key), dict) else None
        if not save_node:
            save_node = next(
                (node for node in patched.values() if isinstance(node, dict) and str(node.get("class_type") or "") == "SaveImage"),
                None,
            )
        if save_node:
            save_node.setdefault("inputs", {})["filename_prefix"] = filename_prefix
    return patched


def _count_prompt_image_items(result_json_text: str) -> int:
    try:
        parsed = _parse_json_any(str(result_json_text or ""))
    except Exception:
        return 0
    prompts = parsed.get("prompts") if isinstance(parsed, dict) else parsed
    return len(prompts) if isinstance(prompts, list) else 0


def _scene_timing_warning(result_json_text: str, audio_duration_seconds: float) -> dict:
    try:
        parsed = _parse_json_any(str(result_json_text or ""))
    except Exception:
        return {"hasWarning": False}
    grid = parsed.get("grid") if isinstance(parsed, dict) else []
    if not isinstance(grid, list) or not grid:
        return {"hasWarning": False}
    last = grid[-1]
    if not isinstance(last, dict):
        return {"hasWarning": False}
    try:
        end = _scene_timecode_to_seconds(last.get("end"))
        duration = float(audio_duration_seconds or 0)
    except (TypeError, ValueError):
        return {"hasWarning": False}
    diff = abs(end - duration)
    return {
        "hasWarning": duration > 0 and diff >= SCENE_AUDIO_DIFF_WARNING_SECONDS,
        "lastEndSeconds": end,
        "audioDurationSeconds": duration,
        "diffSeconds": diff,
    }


def _scene_grid_count(result_json_text: str) -> int:
    try:
        parsed = _parse_json_any(str(result_json_text or ""))
    except Exception:
        return 0
    if not isinstance(parsed, dict):
        return 0
    grid = parsed.get("grid")
    if isinstance(grid, list):
        return len(grid)
    try:
        return int(parsed.get("grid_count") or 0)
    except (TypeError, ValueError):
        return 0


def _shot_item_count(result_json_text: str) -> int:
    try:
        parsed = _parse_json_any(str(result_json_text or ""))
    except Exception:
        return 0
    return len(_shot_items(parsed))


def list_illustration_chapters(novel_id: int) -> list[dict]:
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT c.id, c.chapter_num, c.title, c.word_count, c.audio_duration_seconds,
               s.status AS scene_status, s.progress AS scene_progress, s.error_message AS scene_error,
               s.started_at AS scene_started_at, s.updated_at AS scene_updated_at,
               s.result_json_text AS scene_result_json,
               h.status AS shot_status, h.progress AS shot_progress, h.error_message AS shot_error,
               h.started_at AS shot_started_at, h.updated_at AS shot_updated_at,
               h.result_json_text AS shot_result_json,
                p.status AS prompt_status, p.progress AS prompt_progress, p.error_message AS prompt_error,
                p.started_at AS prompt_started_at, p.updated_at AS prompt_updated_at,
                p.result_json_text AS prompt_result_json,
                COALESCE(pb.batch_total, 0) AS prompt_batch_total,
                COALESCE(pb.batch_completed, 0) AS prompt_batch_completed,
                COALESCE(pb.batch_processing, 0) AS prompt_batch_processing,
                COALESCE(pb.batch_pending, 0) AS prompt_batch_pending,
                COALESCE(pb.batch_failed, 0) AS prompt_batch_failed,
                COALESCE(pb.batch_cancelled, 0) AS prompt_batch_cancelled,
                COALESCE(img.image_total, 0) AS image_total,
               COALESCE(img.image_generated, 0) AS image_generated,
               COALESCE(img.image_queued, 0) AS image_queued,
               COALESCE(img.image_unqueued, 0) AS image_unqueued
        FROM chapters c
        LEFT JOIN chapter_illustration_tasks s ON s.chapter_id=c.id AND s.stage='scene'
        LEFT JOIN chapter_illustration_tasks h ON h.chapter_id=c.id AND h.stage='shot'
        LEFT JOIN chapter_illustration_tasks p ON p.chapter_id=c.id AND p.stage='prompt'
        LEFT JOIN (
            SELECT task_id,
                   COUNT(*) AS batch_total,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS batch_completed,
                   SUM(CASE WHEN status IN ('running','processing') THEN 1 ELSE 0 END) AS batch_processing,
                   SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS batch_pending,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS batch_failed,
                   SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) AS batch_cancelled
            FROM chapter_illustration_prompt_batches
            GROUP BY task_id
        ) pb ON pb.task_id=p.id
        LEFT JOIN (
            SELECT chapter_id,
                   COUNT(*) AS image_total,
                   SUM(CASE WHEN status='completed' AND COALESCE(image_file_path, '')<>'' THEN 1 ELSE 0 END) AS image_generated,
                   SUM(CASE WHEN status<>'idle' AND NOT (status='completed' AND COALESCE(image_file_path, '')<>'') THEN 1 ELSE 0 END) AS image_queued,
                   SUM(CASE WHEN status='idle' THEN 1 ELSE 0 END) AS image_unqueued
            FROM chapter_illustration_images
            WHERE novel_id=?
            GROUP BY chapter_id
        ) img ON img.chapter_id=c.id
        WHERE c.novel_id=?
        ORDER BY c.chapter_num ASC
        """,
        (novel_id, novel_id),
    ).fetchall()
    conn.close()
    items = []
    for r in rows:
        prompt_count = _count_prompt_image_items(str(r["prompt_result_json"] or "")) if _status_value(r["prompt_status"]) == "completed" else 0
        image_total = int(r["image_total"] or 0)
        image_generated = int(r["image_generated"] or 0)
        image_queued = int(r["image_queued"] or 0)
        image_unqueued = int(r["image_unqueued"] or 0)
        image_expected = max(prompt_count, image_total)
        image_missing_records = max(0, image_expected - image_total)
        scene_result = str(r["scene_result_json"] or "") if _status_value(r["scene_status"]) == "completed" else ""
        scene_count = _scene_grid_count(scene_result)
        shot_result = str(r["shot_result_json"] or "") if _status_value(r["shot_status"]) == "completed" else ""
        shot_count = _shot_item_count(shot_result) if shot_result else 0
        prompt_status = _status_value(r["prompt_status"])
        prompt_progress = int(r["prompt_progress"] or 0)
        prompt_error = str(r["prompt_error"] or "")
        prompt_batch_total = int(r["prompt_batch_total"] or 0)
        prompt_batch_completed = int(r["prompt_batch_completed"] or 0)
        prompt_batch_processing = int(r["prompt_batch_processing"] or 0)
        prompt_batch_pending = int(r["prompt_batch_pending"] or 0)
        prompt_batch_failed = int(r["prompt_batch_failed"] or 0)
        prompt_batch_cancelled = int(r["prompt_batch_cancelled"] or 0)
        if prompt_batch_total > 0:
            active_batches = prompt_batch_processing + prompt_batch_pending
            if prompt_batch_processing > 0:
                prompt_status = "processing"
            elif prompt_batch_failed > 0:
                prompt_status = "failed"
            elif prompt_batch_pending > 0:
                prompt_status = "pending"
            elif prompt_batch_completed == prompt_batch_total:
                prompt_status = "completed"
                prompt_error = ""
            elif prompt_batch_cancelled > 0:
                prompt_status = "cancelled"
            if prompt_status in {"pending", "processing"}:
                prompt_error = ""
            if prompt_batch_total > 0 and prompt_status != "completed":
                prompt_progress = max(prompt_progress, int(round(100 * prompt_batch_completed / prompt_batch_total)))
                if active_batches > 0:
                    prompt_progress = max(prompt_progress, min(99, int(round(100 * (prompt_batch_completed + 0.2 * prompt_batch_processing) / prompt_batch_total))))
        items.append({
            "chapterId": int(r["id"]),
            "chapterNum": int(r["chapter_num"] or 0),
            "title": str(r["title"] or ""),
            "wordCount": int(r["word_count"] or 0),
            "audioDurationSeconds": float(r["audio_duration_seconds"] or 0),
            "illustrationCount": scene_count,
            "images": {
                "expected": image_expected,
                "generated": image_generated,
                "queued": image_queued,
                "unqueued": image_unqueued + image_missing_records,
                "missing": max(0, image_expected - image_generated),
            },
            "sceneTimingWarning": _scene_timing_warning(scene_result, float(r["audio_duration_seconds"] or 0)),
            "shotCountWarning": {
                "hasWarning": scene_count > 0 and shot_count > 0 and scene_count != shot_count,
                "sceneCount": scene_count,
                "shotCount": shot_count,
            },
            "stages": {
                "scene": {"status": _status_value(r["scene_status"]), "progress": int(r["scene_progress"] or 0), "errorMessage": str(r["scene_error"] or ""), "startedAt": str(r["scene_started_at"] or ""), "updatedAt": str(r["scene_updated_at"] or "")},
                "shot": {"status": _status_value(r["shot_status"]), "progress": int(r["shot_progress"] or 0), "errorMessage": str(r["shot_error"] or ""), "startedAt": str(r["shot_started_at"] or ""), "updatedAt": str(r["shot_updated_at"] or "")},
                "prompt": {"status": prompt_status, "progress": prompt_progress, "errorMessage": prompt_error, "startedAt": str(r["prompt_started_at"] or ""), "updatedAt": str(r["prompt_updated_at"] or "")},
            },
        })
    return items


def _stage_dependencies_ready(conn, novel_id: int, chapter_id: int, stage: str) -> bool:
    if stage == "shot":
        return bool(_get_completed_result(conn, novel_id, chapter_id, "scene"))
    if stage == "prompt":
        return bool(_get_completed_result(conn, novel_id, chapter_id, "scene") and _get_completed_result(conn, novel_id, chapter_id, "shot"))
    return True


def enqueue_illustration_task(novel_id: int, chapter_id: int, stage: str, allow_waiting: bool = False) -> tuple[bool, str]:
    stage = str(stage or "").strip()
    if stage not in ILLUSTRATION_STAGES:
        return False, "invalid stage"
    conn = db_conn()
    chapter = conn.execute(
        "SELECT id,chapter_num,title FROM chapters WHERE novel_id=? AND id=?",
        (novel_id, chapter_id),
    ).fetchone()
    if not chapter:
        conn.close()
        return False, "chapter not found"
    if not allow_waiting and stage == "shot" and not _get_completed_result(conn, novel_id, chapter_id, "scene"):
        conn.close()
        return False, "scene json not completed"
    if not allow_waiting and stage == "prompt" and (not _get_completed_result(conn, novel_id, chapter_id, "scene") or not _get_completed_result(conn, novel_id, chapter_id, "shot")):
        conn.close()
        return False, "scene/shot json not completed"
    prompt_id = _lookup_novel_stage_prompt(conn, novel_id, stage)
    if not prompt_id:
        conn.close()
        return False, "prompt not found"
    existing = conn.execute(
        "SELECT status FROM chapter_illustration_tasks WHERE novel_id=? AND chapter_id=? AND stage=?",
        (novel_id, chapter_id, stage),
    ).fetchone()
    if existing and str(existing["status"] or "") in {"pending", "running", "processing"}:
        conn.close()
        return False, "task already queued"
    if stage == "scene":
        conn.execute(
            "UPDATE chapter_illustration_tasks SET status='idle',progress=0,input_text='',output_text='',result_json_text='',error_message='',started_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE novel_id=? AND chapter_id=? AND stage IN ('shot','prompt')",
            (novel_id, chapter_id),
        )
        conn.execute("DELETE FROM chapter_illustration_prompt_batches WHERE novel_id=? AND chapter_id=?", (novel_id, chapter_id))
        _delete_illustration_image_files(conn, novel_id, chapter_id)
        conn.execute("DELETE FROM chapter_illustration_images WHERE novel_id=? AND chapter_id=?", (novel_id, chapter_id))
    elif stage == "shot":
        conn.execute(
            "UPDATE chapter_illustration_tasks SET status='idle',progress=0,input_text='',output_text='',result_json_text='',error_message='',started_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE novel_id=? AND chapter_id=? AND stage='prompt'",
            (novel_id, chapter_id),
        )
        conn.execute("DELETE FROM chapter_illustration_prompt_batches WHERE novel_id=? AND chapter_id=?", (novel_id, chapter_id))
        _delete_illustration_image_files(conn, novel_id, chapter_id)
        conn.execute("DELETE FROM chapter_illustration_images WHERE novel_id=? AND chapter_id=?", (novel_id, chapter_id))
    elif stage == "prompt":
        conn.execute("DELETE FROM chapter_illustration_prompt_batches WHERE novel_id=? AND chapter_id=?", (novel_id, chapter_id))
        _delete_illustration_image_files(conn, novel_id, chapter_id)
        conn.execute("DELETE FROM chapter_illustration_images WHERE novel_id=? AND chapter_id=?", (novel_id, chapter_id))
    conn.execute(
        """
            INSERT INTO chapter_illustration_tasks(novel_id,chapter_id,chapter_num,chapter_title,stage,prompt_id,status,progress,input_text,output_text,result_json_text,error_message,started_at,updated_at)
            VALUES(?,?,?,?,?,?,'pending',0,'','','','',NULL,CURRENT_TIMESTAMP)
            ON CONFLICT(novel_id,chapter_id,stage) DO UPDATE SET
                chapter_num=excluded.chapter_num, chapter_title=excluded.chapter_title, prompt_id=excluded.prompt_id,
            status='pending', progress=0, input_text='', output_text='', result_json_text='', error_message='', started_at=NULL, updated_at=CURRENT_TIMESTAMP
        """,
        (novel_id, chapter_id, int(chapter["chapter_num"] or 0), str(chapter["title"] or ""), stage, prompt_id),
    )
    conn.commit()
    conn.close()
    return True, "queued"


def get_illustration_task_payload(novel_id: int, chapter_id: int, stage: str) -> dict:
    conn = db_conn()
    row = conn.execute(
        "SELECT input_text,output_text,result_json_text,error_message,status FROM chapter_illustration_tasks WHERE novel_id=? AND chapter_id=? AND stage=?",
        (novel_id, chapter_id, stage),
    ).fetchone()
    conn.close()
    if not row:
        return {"inputText": "", "outputText": "", "resultJsonText": "", "errorMessage": "", "status": "idle"}
    return {
        "inputText": str(row["input_text"] or ""),
        "outputText": str(row["output_text"] or ""),
        "resultJsonText": str(row["result_json_text"] or ""),
        "errorMessage": str(row["error_message"] or ""),
        "status": str(row["status"] or ""),
    }


def save_illustration_prompt_output(novel_id: int, chapter_id: int, json_text: str) -> dict:
    parsed = _parse_json_any(json_text)
    prompts = _prompt_items(parsed)
    if not prompts:
        raise RuntimeError("prompt output has no prompts")
    normalized = _json_dumps(parsed)
    conn = db_conn()
    row = conn.execute(
        "SELECT id FROM chapter_illustration_tasks WHERE novel_id=? AND chapter_id=? AND stage='prompt'",
        (novel_id, chapter_id),
    ).fetchone()
    if not row:
        conn.close()
        raise RuntimeError("prompt task not found")
    conn.execute(
        "UPDATE chapter_illustration_tasks SET status='completed',progress=100,output_text=?,result_json_text=?,error_message='',updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (normalized, normalized, int(row["id"])),
    )
    conn.commit()
    conn.close()
    items = sync_prompt_images(novel_id, chapter_id)
    return {"promptCount": len(prompts), "imageCount": len(items)}


def save_illustration_scene_output(novel_id: int, chapter_id: int, json_text: str) -> dict:
    parsed = _parse_json_any(json_text)
    if not isinstance(parsed, dict):
        raise RuntimeError("scene output must be a JSON object")
    grid = parsed.get("grid")
    if not isinstance(grid, list) or not grid:
        raise RuntimeError("scene output has no grid")
    normalized = _json_dumps(parsed)
    conn = db_conn()
    row = conn.execute(
        "SELECT id FROM chapter_illustration_tasks WHERE novel_id=? AND chapter_id=? AND stage='scene'",
        (novel_id, chapter_id),
    ).fetchone()
    if not row:
        conn.close()
        raise RuntimeError("scene task not found")
    conn.execute(
        "UPDATE chapter_illustration_tasks SET status='completed',progress=100,output_text=?,result_json_text=?,error_message='',updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (normalized, normalized, int(row["id"])),
    )
    conn.commit()
    conn.close()
    return {"sceneCount": len(grid)}


def get_illustration_llm_request_preview(novel_id: int, chapter_id: int, stage: str) -> dict:
    if stage not in ILLUSTRATION_STAGES:
        raise RuntimeError("invalid illustration stage")
    conn = db_conn()
    row = conn.execute(
        """
        SELECT c.id AS chapter_id, c.novel_id, c.chapter_num, c.title AS chapter_title, c.text_file_path,
               t.prompt_id
        FROM chapters c
        LEFT JOIN chapter_illustration_tasks t ON t.chapter_id=c.id AND t.stage=?
        WHERE c.novel_id=? AND c.id=?
        """,
        (stage, novel_id, chapter_id),
    ).fetchone()
    if not row:
        conn.close()
        raise RuntimeError("chapter not found")
    prompt_id = int(row["prompt_id"] or 0) or (_lookup_novel_stage_prompt(conn, novel_id, stage) or 0)
    if not prompt_id:
        conn.close()
        raise RuntimeError("prompt not found")
    prompt = conn.execute("SELECT content FROM json_prompts WHERE id=?", (prompt_id,)).fetchone()
    if not prompt:
        conn.close()
        raise RuntimeError("prompt not found")
    system_prompt = str(prompt["content"] or "").strip()
    user_input = _build_user_input(conn, stage, row)
    settings = fetch_settings(conn)
    llm = apply_prompt_llm_settings(settings.get("llm") or {}, load_prompt_llm_settings(conn, prompt_id))
    proxy_url = str(settings.get("proxyUrl") or "")
    conn.close()
    request = build_llm_prompt_json_request(
        llm=llm,
        proxy_url=proxy_url,
        system_prompt=system_prompt,
        user_prompt=user_input,
    )
    headers = dict(request.get("headers") or {})
    if headers.get("Authorization"):
        headers["Authorization"] = "Bearer ***"
    return {
        **request,
        "headers": headers,
        "promptId": prompt_id,
        "stage": stage,
        "chapterNum": int(row["chapter_num"] or 0),
    }


def _prepare_prompt_batches(conn, row, prompt_id: int, reset: bool = False) -> list:
    task_id = int(row["id"])
    if reset:
        conn.execute("DELETE FROM chapter_illustration_prompt_batches WHERE task_id=?", (task_id,))
    scene_raw = _get_completed_result(conn, int(row["novel_id"]), int(row["chapter_id"]), "scene")
    shot_raw = _get_completed_result(conn, int(row["novel_id"]), int(row["chapter_id"]), "shot")
    scene_parsed = _parse_json_any(scene_raw)
    shot_parsed = _parse_json_any(shot_raw)
    scene_grid = scene_parsed.get("grid") if isinstance(scene_parsed, dict) else []
    shot_grid = _shot_items(shot_parsed)
    if not isinstance(scene_grid, list) or not scene_grid:
        raise RuntimeError("scene grid is empty")
    scene_count = _scene_grid_count(scene_raw)
    if len(shot_grid) != scene_count:
        raise RuntimeError(f"Shot输出数量 {len(shot_grid)} 与 Scene grid_count {scene_count} 不一致")
    existing = conn.execute(
        "SELECT * FROM chapter_illustration_prompt_batches WHERE task_id=? ORDER BY batch_index ASC",
        (task_id,),
    ).fetchall()
    if existing:
        return existing
    scene_chunks = _chunked(scene_grid, PROMPT_BATCH_SIZE)
    shot_by_index = {
        int(item.get("index") or 0): item
        for item in shot_grid
        if isinstance(item, dict) and int(item.get("index") or 0)
    }
    for pos, grid_chunk in enumerate(scene_chunks, start=1):
        indices = [int(item.get("index") or 0) for item in grid_chunk if isinstance(item, dict)]
        shot_chunk = [shot_by_index[idx] for idx in indices if idx in shot_by_index]
        shot_indices = {int(item.get("index") or 0) for item in shot_chunk if isinstance(item, dict)}
        missing_shot_indices = [idx for idx in indices if idx and idx not in shot_indices]
        if missing_shot_indices:
            raise RuntimeError(f"shot.json 缺少 Scene index: {', '.join(str(idx) for idx in missing_shot_indices)}")
        start_index = min(indices) if indices else ((pos - 1) * PROMPT_BATCH_SIZE + 1)
        end_index = max(indices) if indices else (start_index + len(grid_chunk) - 1)
        batch_input = _build_prompt_batch_input(scene_parsed, shot_parsed, grid_chunk, shot_chunk)
        conn.execute(
            """
            INSERT INTO chapter_illustration_prompt_batches(
                novel_id,chapter_id,task_id,batch_index,start_index,end_index,status,progress,input_text,error_message,updated_at
            ) VALUES(?,?,?,?,?,?,'pending',0,?,'',CURRENT_TIMESTAMP)
            """,
            (int(row["novel_id"]), int(row["chapter_id"]), task_id, pos, start_index, end_index, batch_input),
        )
    return conn.execute(
        "SELECT * FROM chapter_illustration_prompt_batches WHERE task_id=? ORDER BY batch_index ASC",
        (task_id,),
    ).fetchall()


def _merge_prompt_batches(conn, task_id: int, chapter_title: str = "") -> str:
    rows = conn.execute(
        "SELECT input_text,result_json_text FROM chapter_illustration_prompt_batches WHERE task_id=? ORDER BY batch_index ASC",
        (task_id,),
    ).fetchall()
    prompts = []
    chapter = chapter_title
    for row in rows:
        parsed = _parse_json_any(str(row["result_json_text"] or ""))
        if isinstance(parsed, dict) and not chapter:
            chapter = str(parsed.get("chapter") or "")
        batch_prompts = _prompt_items(parsed)
        scene_indices = _prompt_batch_scene_indices(str(row["input_text"] or ""))
        for pos, item in enumerate(batch_prompts):
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            if pos < len(scene_indices):
                normalized["index"] = scene_indices[pos]
            prompts.append(normalized)
    return _json_dumps({"chapter": chapter, "prompt_count": len(prompts), "prompts": prompts})


def _prompt_batch_scene_indices(input_text: str) -> list[int]:
    text = str(input_text or "")
    marker = "shot.json："
    scene_text = text.split(marker, 1)[0].replace("scene.json：", "").strip()
    try:
        scene_parsed = _parse_json_any(scene_text)
    except Exception:
        return []
    grid = scene_parsed.get("grid") if isinstance(scene_parsed, dict) else []
    if not isinstance(grid, list):
        return []
    indices = []
    for pos, item in enumerate(grid, start=1):
        if not isinstance(item, dict):
            continue
        try:
            indices.append(int(item.get("index") or pos))
        except (TypeError, ValueError):
            indices.append(pos)
    return indices


def _safe_llm_request_preview(llm: dict, proxy_url: str, system_prompt: str, user_prompt: str) -> dict:
    request = build_llm_prompt_json_request(llm=llm, proxy_url=proxy_url, system_prompt=system_prompt, user_prompt=user_prompt)
    headers = dict(request.get("headers") or {})
    if headers.get("Authorization"):
        headers["Authorization"] = "Bearer ***"
    request["headers"] = headers
    return request


def list_prompt_batches(novel_id: int, chapter_id: int) -> dict:
    conn = db_conn()
    task = conn.execute(
        "SELECT * FROM chapter_illustration_tasks WHERE novel_id=? AND chapter_id=? AND stage='prompt'",
        (novel_id, chapter_id),
    ).fetchone()
    if not task:
        conn.close()
        return {"task": {"status": "idle", "progress": 0}, "batches": []}
    rows = conn.execute(
        "SELECT * FROM chapter_illustration_prompt_batches WHERE task_id=? ORDER BY batch_index ASC",
        (int(task["id"]),),
    ).fetchall()
    conn.close()
    task_status = str(task["status"] or "")
    task_progress = int(task["progress"] or 0)
    task_error = str(task["error_message"] or "")
    if rows:
        total = len(rows)
        completed = sum(1 for row in rows if str(row["status"] or "") == "completed")
        processing = sum(1 for row in rows if str(row["status"] or "") in {"running", "processing"})
        pending = sum(1 for row in rows if str(row["status"] or "") == "pending")
        failed = sum(1 for row in rows if str(row["status"] or "") == "failed")
        cancelled = sum(1 for row in rows if str(row["status"] or "") == "cancelled")
        if processing:
            task_status = "processing"
        elif failed:
            task_status = "failed"
        elif pending:
            task_status = "pending"
        elif completed == total:
            task_status = "completed"
            task_error = ""
        elif cancelled:
            task_status = "cancelled"
        if task_status in {"pending", "processing"}:
            task_error = ""
        if task_status != "completed":
            task_progress = max(task_progress, int(round(100 * completed / total)))
            if processing:
                task_progress = max(task_progress, min(99, int(round(100 * (completed + 0.2 * processing) / total))))
    return {
        "task": {
            "id": int(task["id"]),
            "status": task_status,
            "progress": task_progress,
            "errorMessage": task_error,
            "startedAt": str(task["started_at"] or ""),
            "updatedAt": str(task["updated_at"] or ""),
        },
        "batches": [
            {
                "id": int(row["id"]),
                "batchIndex": int(row["batch_index"] or 0),
                "startIndex": int(row["start_index"] or 0),
                "endIndex": int(row["end_index"] or 0),
                "status": str(row["status"] or ""),
                "progress": int(row["progress"] or 0),
                "inputText": str(row["input_text"] or ""),
                "llmParamsText": str(row["llm_request_json"] or ""),
                "outputText": str(row["output_text"] or ""),
                "resultJsonText": str(row["result_json_text"] or ""),
                "errorMessage": str(row["error_message"] or ""),
                "startedAt": str(row["started_at"] or ""),
                "updatedAt": str(row["updated_at"] or ""),
            }
            for row in rows
        ],
    }


def retry_prompt_batch(novel_id: int, chapter_id: int, batch_index: int) -> tuple[bool, str]:
    conn = db_conn()
    task = conn.execute(
        "SELECT id,status FROM chapter_illustration_tasks WHERE novel_id=? AND chapter_id=? AND stage='prompt'",
        (novel_id, chapter_id),
    ).fetchone()
    if not task:
        conn.close()
        return False, "prompt task not found"
    if str(task["status"] or "") in {"running", "processing"}:
        conn.close()
        return False, "prompt task is running"
    batch = conn.execute(
        "SELECT id FROM chapter_illustration_prompt_batches WHERE task_id=? AND batch_index=?",
        (int(task["id"]), int(batch_index)),
    ).fetchone()
    if not batch:
        conn.close()
        return False, "batch not found"
    conn.execute(
        "UPDATE chapter_illustration_prompt_batches SET status='pending',progress=0,output_text='',result_json_text='',error_message='',started_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (int(batch["id"]),),
    )
    conn.execute(
        "UPDATE chapter_illustration_tasks SET status='pending',progress=5,error_message='',updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (int(task["id"]),),
    )
    conn.commit()
    conn.close()
    return True, "queued"


def sync_prompt_images(novel_id: int, chapter_id: int) -> list[dict]:
    conn = db_conn()
    row = conn.execute(
        "SELECT chapter_num,result_json_text FROM chapter_illustration_tasks WHERE novel_id=? AND chapter_id=? AND stage='prompt' AND status='completed'",
        (novel_id, chapter_id),
    ).fetchone()
    if not row:
        conn.close()
        return []
    parsed = _parse_json_any(str(row["result_json_text"] or ""))
    prompts = parsed.get("prompts") if isinstance(parsed, dict) else parsed
    if not isinstance(prompts, list):
        conn.close()
        return []
    chapter_num = int(row["chapter_num"] or 0)
    for pos, item in enumerate(prompts, start=1):
        if not isinstance(item, dict):
            continue
        idx = int(item.get("index") or pos)
        prompt_text = build_image_prompt(item)
        existing = conn.execute(
            "SELECT image_file_path,status FROM chapter_illustration_images WHERE novel_id=? AND chapter_id=? AND item_index=?",
            (novel_id, chapter_id, idx),
        ).fetchone()
        status = str(existing["status"] or "idle") if existing else "idle"
        image_path = str(existing["image_file_path"] or "") if existing else ""
        conn.execute(
            """
            INSERT INTO chapter_illustration_images(novel_id,chapter_id,chapter_num,item_index,scene_title,cn_summary,character_names,suggested_size,prompt_text,status,image_file_path,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(novel_id,chapter_id,item_index) DO UPDATE SET
                chapter_num=excluded.chapter_num, scene_title=excluded.scene_title, cn_summary=excluded.cn_summary,
                character_names=excluded.character_names, suggested_size=excluded.suggested_size,
                prompt_text=excluded.prompt_text, updated_at=CURRENT_TIMESTAMP
            """,
            (
                novel_id,
                chapter_id,
                chapter_num,
                idx,
                str(item.get("scene_title") or ""),
                str(item.get("cn_summary") or ""),
                _character_names(item),
                str(item.get("suggested_size") or ""),
                prompt_text,
                status,
                image_path,
            ),
        )
    conn.commit()
    items = list_illustration_images(novel_id, chapter_id, conn=conn)
    conn.close()
    return items


def list_illustration_images(novel_id: int, chapter_id: int, conn=None) -> list[dict]:
    own_conn = conn is None
    conn = conn or db_conn()
    grid_meta = _scene_grid_meta(conn, novel_id, chapter_id)
    rows = conn.execute(
        "SELECT * FROM chapter_illustration_images WHERE novel_id=? AND chapter_id=? ORDER BY item_index ASC",
        (novel_id, chapter_id),
    ).fetchall()
    if own_conn:
        conn.close()
    items = []
    for r in rows:
        idx = int(r["item_index"] or 0)
        meta = grid_meta.get(idx) or {}
        items.append({
            "id": int(r["id"]),
            "index": idx,
            "sceneTitle": str(r["scene_title"] or ""),
            "cnSummary": str(r["cn_summary"] or ""),
            "characterNames": str(r["character_names"] or ""),
            "suggestedSize": str(r["suggested_size"] or ""),
            "promptText": str(r["prompt_text"] or ""),
            "start": meta.get("start"),
            "end": meta.get("end"),
            "duration": meta.get("duration"),
            "status": str(r["status"] or "idle"),
            "progress": int(r["progress"] or 0),
            "imageUrl": f"/api/illustration-images/{int(r['id'])}/file" if str(r["image_file_path"] or "").strip() else "",
            "updatedAt": str(r["updated_at"] or ""),
            "errorMessage": str(r["error_message"] or ""),
        })
    return items


def enqueue_illustration_image(image_id: int) -> tuple[bool, str]:
    conn = db_conn()
    row = conn.execute("SELECT status FROM chapter_illustration_images WHERE id=?", (image_id,)).fetchone()
    if not row:
        conn.close()
        return False, "image item not found"
    if str(row["status"] or "") in {"pending", "running", "processing"}:
        conn.close()
        return False, "image task already queued"
    conn.execute(
        "UPDATE chapter_illustration_images SET status='pending',progress=0,error_message='',started_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (image_id,),
    )
    conn.commit()
    conn.close()
    return True, "queued"


def enqueue_all_illustration_images(novel_id: int, chapter_id: int) -> dict:
    items = sync_prompt_images(novel_id, chapter_id)
    queued = 0
    skipped = 0
    for item in items:
        ok, _ = enqueue_illustration_image(int(item["id"]))
        if ok:
            queued += 1
        else:
            skipped += 1
    return {"queued": queued, "skipped": skipped, "total": len(items)}


def cancel_pending_illustration_tasks(novel_id: int) -> dict:
    conn = db_conn()
    rows = conn.execute(
        "SELECT id FROM chapter_illustration_tasks WHERE novel_id=? AND stage IN ('scene','shot','prompt') AND status IN ('pending','running','processing')",
        (novel_id,),
    ).fetchall()
    ids = [int(row["id"]) for row in rows]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE chapter_illustration_tasks SET status='cancelled',progress=0,error_message='已取消',updated_at=CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
            ids,
        )
        conn.execute(
            f"UPDATE chapter_illustration_prompt_batches SET status='cancelled',progress=0,error_message='已取消',updated_at=CURRENT_TIMESTAMP WHERE task_id IN ({placeholders}) AND status IN ('pending','processing')",
            ids,
        )
    conn.commit()
    conn.close()
    return {"cancelled": len(ids)}


def _illustration_task_cancelled(task_id: int) -> bool:
    conn = db_conn()
    row = conn.execute("SELECT status FROM chapter_illustration_tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    return bool(row and str(row["status"] or "") == "cancelled")


def _illustration_prompt_batch_cancelled(task_id: int, batch_id: int) -> bool:
    conn = db_conn()
    row = conn.execute(
        "SELECT t.status AS task_status, b.status AS batch_status FROM chapter_illustration_tasks t JOIN chapter_illustration_prompt_batches b ON b.task_id=t.id WHERE t.id=? AND b.id=?",
        (task_id, batch_id),
    ).fetchone()
    conn.close()
    return bool(row and (str(row["task_status"] or "") == "cancelled" or str(row["batch_status"] or "") == "cancelled"))


def cancel_pending_illustration_images(novel_id: int) -> dict:
    conn = db_conn()
    cur = conn.execute(
        "UPDATE chapter_illustration_images SET status='idle',progress=0,error_message='',started_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE novel_id=? AND status='pending'",
        (novel_id,),
    )
    conn.commit()
    cancelled = int(cur.rowcount or 0)
    conn.close()
    return {"cancelled": cancelled}


def process_illustration_task(task_id: int) -> None:
    conn = db_conn()
    conn_closed = False
    row = conn.execute(
        """
        SELECT t.*, c.text_file_path
        FROM chapter_illustration_tasks t
        JOIN chapters c ON c.id=t.chapter_id
        WHERE t.id=?
        """,
        (task_id,),
    ).fetchone()
    if not row:
        conn.close()
        return
    try:
        stage = str(row["stage"] or "")
        if not _stage_dependencies_ready(conn, int(row["novel_id"]), int(row["chapter_id"]), stage):
            conn.execute(
                "UPDATE chapter_illustration_tasks SET status='pending',progress=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (task_id,),
            )
            conn.commit()
            conn.close()
            return
        prompt_id = int(row["prompt_id"] or 0)
        prompt = conn.execute("SELECT content FROM json_prompts WHERE id=?", (prompt_id,)).fetchone()
        if not prompt:
            raise RuntimeError("prompt not found")
        system_prompt = str(prompt["content"] or "").strip()
        settings = fetch_settings(conn)
        llm = apply_prompt_llm_settings(settings.get("llm") or {}, load_prompt_llm_settings(conn, prompt_id))
        proxy_url = str(settings.get("proxyUrl") or "")
        if stage == "prompt":
            _process_prompt_task_batches(conn, row, prompt_id, system_prompt, llm, proxy_url)
            return
        user_input = _build_user_input(conn, stage, row)
        conn.execute(
            "UPDATE chapter_illustration_tasks SET status='processing',progress=20,model_name=?,think_enabled=?,input_text=?,output_text='',result_json_text='',error_message='',started_at=COALESCE(started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(llm.get("model") or ""), 1 if bool(llm.get("think", True)) else 0, user_input, task_id),
        )
        conn.commit()
        conn.close()
        conn_closed = True
        raw = call_llm_prompt_json(llm=llm, proxy_url=proxy_url, system_prompt=system_prompt, user_prompt=user_input)
        if _illustration_task_cancelled(task_id):
            return
        parsed = _parse_json_any(raw)
        result_json = json.dumps(parsed, ensure_ascii=False, indent=2)
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_illustration_tasks SET status='completed',progress=100,output_text=?,result_json_text=?,error_message='',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (raw, result_json, task_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        if not conn_closed:
            try:
                conn.close()
            except Exception:
                pass
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_illustration_tasks SET status='failed',progress=0,output_text='',result_json_text='',error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(exc), task_id),
        )
        conn.commit()
        conn.close()


def _process_prompt_task_batches(conn, row, prompt_id: int, system_prompt: str, llm: dict, proxy_url: str) -> None:
    task_id = int(row["id"])
    batches = _prepare_prompt_batches(conn, row, prompt_id)
    total = max(1, len(batches))
    first_input = str(batches[0]["input_text"] or "") if batches else ""
    conn.execute(
        "UPDATE chapter_illustration_tasks SET status='processing',progress=10,model_name=?,think_enabled=?,input_text=?,output_text='',result_json_text='',error_message='',started_at=COALESCE(started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (str(llm.get("model") or ""), 1 if bool(llm.get("think", True)) else 0, first_input, task_id),
    )
    conn.commit()
    conn.close()
    for pos, batch in enumerate(batches, start=1):
        if str(batch["status"] or "") == "completed":
            continue
        batch_id = int(batch["id"])
        user_input = str(batch["input_text"] or "")
        request_preview = _safe_llm_request_preview(llm, proxy_url, system_prompt, user_input)
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_illustration_prompt_batches SET status='processing',progress=20,llm_request_json=?,output_text='',result_json_text='',error_message='',started_at=COALESCE(started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (_json_dumps(request_preview), batch_id),
        )
        conn.execute(
            "UPDATE chapter_illustration_tasks SET progress=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (max(10, min(95, int(((pos - 1) / total) * 90) + 10)), task_id),
        )
        conn.commit()
        conn.close()
        try:
            raw = call_llm_prompt_json(llm=llm, proxy_url=proxy_url, system_prompt=system_prompt, user_prompt=user_input)
            if _illustration_prompt_batch_cancelled(task_id, batch_id):
                return
            parsed = _parse_json_any(raw)
            prompts = _prompt_items(parsed)
            if not prompts:
                raise RuntimeError("prompt batch output has no prompts")
            result_json = _json_dumps(parsed)
            conn = db_conn()
            conn.execute(
                "UPDATE chapter_illustration_prompt_batches SET status='completed',progress=100,output_text=?,result_json_text=?,error_message='',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (raw, result_json, batch_id),
            )
            conn.execute(
                "UPDATE chapter_illustration_tasks SET progress=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (max(10, min(99, int((pos / total) * 90) + 10)), task_id),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            conn = db_conn()
            message = str(exc)
            conn.execute(
                "UPDATE chapter_illustration_prompt_batches SET status='failed',progress=0,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (message, batch_id),
            )
            conn.execute(
                "UPDATE chapter_illustration_tasks SET status='failed',progress=0,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (f"第{int(batch['batch_index'] or pos)}批失败：{message}", task_id),
            )
            conn.commit()
            conn.close()
            return
    if _illustration_task_cancelled(task_id):
        return
    conn = db_conn()
    merged = _merge_prompt_batches(conn, task_id, str(row["chapter_title"] or ""))
    conn.execute(
        "UPDATE chapter_illustration_tasks SET status='completed',progress=100,output_text=?,result_json_text=?,error_message='',updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (merged, merged, task_id),
    )
    conn.commit()
    conn.close()
    sync_prompt_images(int(row["novel_id"]), int(row["chapter_id"]))


def process_illustration_image(image_id: int) -> None:
    conn = db_conn()
    conn_closed = False
    workflow_log_id = 0
    row = conn.execute(
        """
        SELECT i.*, n.english_dir, n.workflow_id
        FROM chapter_illustration_images i
        JOIN novels n ON n.id=i.novel_id
        WHERE i.id=?
        """,
        (image_id,),
    ).fetchone()
    if not row:
        conn.close()
        return
    try:
        settings = fetch_settings(conn)
        comfy_url = str(settings.get("comfyUrl") or "").strip()
        if not comfy_url:
            raise RuntimeError("ComfyUI URL is not configured")
        workflow_id = int(row["workflow_id"] or 0)
        wf = None
        if workflow_id:
            wf = conn.execute(
                "SELECT name,json_text,workflow_io_config FROM comfy_workflows WHERE id=? AND workflow_type='illustration'",
                (workflow_id,),
            ).fetchone()
        if not wf:
            wf = conn.execute(
                "SELECT name,json_text,workflow_io_config FROM comfy_workflows WHERE workflow_type='illustration' ORDER BY CASE WHEN name='生成插画' THEN 0 ELSE 1 END, id DESC LIMIT 1"
            ).fetchone()
        if not wf:
            raise RuntimeError("illustration workflow not found")
        workflow = workflow_json_to_prompt_json(json.loads(str(wf["json_text"] or "{}")))
        workflow_io_config = json.loads(str(wf["workflow_io_config"] or "{}") or "{}")
        inputs_config = workflow_io_config.get("inputs") if isinstance(workflow_io_config, dict) else {}
        outputs_config = workflow_io_config.get("outputs") if isinstance(workflow_io_config, dict) else {}
        prompt_input_config = (inputs_config or {}).get("promptText") if isinstance(inputs_config, dict) else {}
        width_input_config = (inputs_config or {}).get("width") if isinstance(inputs_config, dict) else {}
        height_input_config = (inputs_config or {}).get("height") if isinstance(inputs_config, dict) else {}
        image_output_config = (outputs_config or {}).get("imageFile") if isinstance(outputs_config, dict) else {}
        prompt_node_id = str((prompt_input_config or {}).get("nodeId") or "").strip() if isinstance(prompt_input_config, dict) else ""
        width_node_id = str((width_input_config or {}).get("nodeId") or "").strip() if isinstance(width_input_config, dict) else ""
        height_node_id = str((height_input_config or {}).get("nodeId") or "").strip() if isinstance(height_input_config, dict) else ""
        output_node_id = str((image_output_config or {}).get("nodeId") or "").strip() if isinstance(image_output_config, dict) else ""
        filename_prefix = "/".join(
            [
                _safe_filename_part(str(row["english_dir"] or "")),
                f"ch{int(row['chapter_num']):03d}",
                str(int(row["item_index"] or 0)),
            ]
        )
        prompt = _apply_workflow_inputs(
            workflow,
            str(row["prompt_text"] or ""),
            filename_prefix=filename_prefix,
            prompt_node_id=prompt_node_id,
            width_node_id=width_node_id,
            height_node_id=height_node_id,
            output_node_id=output_node_id,
        )
        log_cursor = conn.execute(
            "INSERT INTO comfy_workflow_logs(workflow_category,workflow_name,workflow_json,error_log) VALUES(?,?,?,?)",
            ("生成插画", f"{str(wf['name'] or '生成插画')} 第{int(row['chapter_num']):03d}回 #{int(row['item_index'])}", json.dumps(prompt, ensure_ascii=False, indent=2), ""),
        )
        workflow_log_id = int(log_cursor.lastrowid or 0)
        conn.execute(
            "UPDATE chapter_illustration_images SET status='processing',progress=10,started_at=COALESCE(started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (image_id,),
        )
        conn.commit()
        conn.close()
        conn_closed = True
        resp = comfy_request_json(comfy_url=comfy_url, path="/prompt", method="POST", payload={"prompt": prompt}, timeout=30.0)
        prompt_id = str(resp.get("prompt_id") or "").strip()
        if not prompt_id:
            raise RuntimeError("ComfyUI prompt_id missing")
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_illustration_images SET progress=20,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (image_id,),
        )
        conn.commit()
        conn.close()
        output = None
        for _ in range(240):
            touch_illustration_image_worker_heartbeat(made_progress=True)
            history = comfy_request_json(comfy_url=comfy_url, path=f"/history/{prompt_id}")
            comfy_error = _extract_comfy_history_error(history, prompt_id)
            if comfy_error:
                raise RuntimeError(f"ComfyUI workflow failed: {comfy_error}")
            output = _extract_image_output(history, prompt_id, output_node_id=output_node_id)
            if output:
                break
            time.sleep(2)
        if not output:
            raise RuntimeError("ComfyUI image output not found")
        filename, subfolder, file_type = output
        data = comfy_download_file(comfy_url=comfy_url, filename=filename, subfolder=subfolder, file_type=file_type)
        out_dir = ROOT_DIR / "novel" / str(row["english_dir"] or "") / "illustrations" / f"{int(row['chapter_num']):03d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = _write_optimized_illustration_image(data, filename, out_dir, int(row["item_index"]))
        old_rel = str(row["image_file_path"] or "").strip()
        if old_rel:
            old_path = ROOT_DIR / old_rel
            if old_path != out_path:
                _safe_unlink_under_root(old_path)
        rel = db_rel_path(out_path.relative_to(ROOT_DIR))
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_illustration_images SET status='completed',progress=100,image_file_path=?,error_message='',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (rel, image_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        if not conn_closed:
            try:
                conn.execute(
                    "INSERT INTO comfy_workflow_logs(workflow_category,workflow_name,workflow_json,error_log) VALUES(?,?,?,?)",
                    ("生成插画", f"图片任务 {image_id}", "{}", str(exc)),
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
        elif workflow_log_id:
            try:
                log_conn = db_conn()
                log_conn.execute(
                    "UPDATE comfy_workflow_logs SET error_log=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (str(exc), workflow_log_id),
                )
                log_conn.commit()
                log_conn.close()
            except Exception:
                pass
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_illustration_images SET status='failed',progress=0,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(exc), image_id),
        )
        conn.commit()
        conn.close()


def run_illustration_image_queue_once() -> bool:
    if not ILLUSTRATION_IMAGE_QUEUE_LOCK.acquire(blocking=False):
        return False
    try:
        return _run_illustration_image_queue_once_locked()
    finally:
        ILLUSTRATION_IMAGE_QUEUE_LOCK.release()


def _run_illustration_image_queue_once_locked() -> bool:
    conn = db_conn()
    image_running = conn.execute(
        "SELECT id FROM chapter_illustration_images WHERE status IN ('running','processing') ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if image_running:
        image_id = int(image_running["id"])
        conn.close()
        process_illustration_image(image_id)
        return True
    image_pending = conn.execute(
        "SELECT id FROM chapter_illustration_images WHERE status='pending' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if image_pending:
        image_id = int(image_pending["id"])
        conn.execute(
            "UPDATE chapter_illustration_images SET status='running',progress=5,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (image_id,),
        )
        conn.commit()
        conn.close()
        process_illustration_image(image_id)
        return True
    conn.close()
    return False


def run_illustration_llm_queue_once() -> bool:
    if not ILLUSTRATION_LLM_QUEUE_LOCK.acquire(blocking=False):
        return False
    try:
        return _run_illustration_llm_queue_once_locked()
    finally:
        ILLUSTRATION_LLM_QUEUE_LOCK.release()


def _run_illustration_llm_queue_once_locked() -> bool:
    conn = db_conn()
    running = conn.execute(
        "SELECT id FROM chapter_illustration_tasks WHERE status IN ('running','processing') ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if running:
        task_id = int(running["id"])
        conn.close()
        process_illustration_task(task_id)
        return True
    pending = conn.execute(
        """
        SELECT t.id
        FROM chapter_illustration_tasks t
        WHERE t.status='pending'
          AND (
            t.stage='scene'
            OR (t.stage='shot' AND EXISTS (
              SELECT 1 FROM chapter_illustration_tasks s
              WHERE s.novel_id=t.novel_id AND s.chapter_id=t.chapter_id AND s.stage='scene' AND s.status='completed'
            ))
            OR (t.stage='prompt' AND EXISTS (
              SELECT 1 FROM chapter_illustration_tasks s
              WHERE s.novel_id=t.novel_id AND s.chapter_id=t.chapter_id AND s.stage='scene' AND s.status='completed'
            ) AND EXISTS (
              SELECT 1 FROM chapter_illustration_tasks h
              WHERE h.novel_id=t.novel_id AND h.chapter_id=t.chapter_id AND h.stage='shot' AND h.status='completed'
            ))
          )
        ORDER BY t.id ASC LIMIT 1
        """
    ).fetchone()
    if not pending:
        conn.close()
        return False
    task_id = int(pending["id"])
    conn.execute(
        "UPDATE chapter_illustration_tasks SET status='running',progress=5,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()
    process_illustration_task(task_id)
    return True


def run_illustration_queue_once() -> bool:
    return run_illustration_image_queue_once() or run_illustration_llm_queue_once()
