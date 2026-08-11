import zipfile
import base64
import io
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote

from .services import *  # noqa: F401,F403
from .services import normalize_live_ending_audio_items
from .roles import (
    list_roles,
    get_role,
    upsert_role_default,
    update_role_fields,
    update_role_level,
    duplicate_role,
    create_role_alias,
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
    list_role_line_audio_entries,
    list_role_line_counts,
    is_chapter_merged_audio_stale,
    invalidate_obsolete_chapter_line_audio_tasks,
    enqueue_line_audio_task,
    enqueue_all_line_audio_tasks,
    merge_chapter_line_audio,
    get_chapter_merged_audio_path,
    get_chapter_merged_audio_stats,
    delete_line_audio_task,
    retry_line_audio_task,
    prioritize_line_audio_task,
    edit_line_audio_task_audio,
    record_line_audio_noise_false_positive,
    detect_line_audio_task_silences,
    detect_line_audio_task_noise,
    analyze_line_audio_task_loudness,
    preview_line_audio_replacement_targets,
    replace_matching_line_audio_tasks,
)
from .audio_asr import (
    cancel_chapter_audio_asr_task,
    cancel_chapter_audio_asr_subtitle_repair,
    enqueue_batch_audio_asr_tasks,
    enqueue_chapter_audio_asr_task,
    enqueue_chapter_audio_asr_subtitle_repair,
    inspect_srt_timing_errors,
    list_audio_asr_chapters,
)
from .nsfw_review import (
    enqueue_batch_nsfw_review_tasks,
    enqueue_chapter_nsfw_review_task,
    list_nsfw_review_chapters,
)
from .illustration import (
    cancel_pending_illustration_images,
    cancel_pending_illustration_tasks,
    enqueue_illustration_task,
    enqueue_all_illustration_images,
    enqueue_illustration_image,
    get_illustration_task_payload,
    get_illustration_llm_request_preview,
    get_illustration_prompt_item_original,
    list_illustration_images,
    list_illustration_chapters,
    list_prompt_batches,
    optimize_illustration_prompt_item,
    prepare_illustration_prompt_item_optimization,
    retry_prompt_batch,
    save_illustration_scene_output,
    save_illustration_prompt_item,
    save_illustration_prompt_output,
    sync_prompt_images,
)
from .video_export import (
    cancel_video_export_task,
    enqueue_video_export_task,
    get_video_export_cover_path,
    get_video_export_file_path,
    get_video_export_task,
    list_video_export_tasks,
    retry_video_export_task,
)

VISUAL_STYLE_OPTIONS = {
    "中国古典工笔画",
    "法国现实主义文学插画",
    "3D皮克斯动画电影风格",
    "吉卜力动画风格",
}
DEFAULT_VISUAL_STYLE = "3D皮克斯动画电影风格"


LINE_AUDIO_NOISE_SAMPLE_LABEL_DIRS = {
    "manual-abnormal": "manual_abnormal",
    "abnormal": "abnormal",
    "false-positive-normal": "false_positive_normal",
}


def _line_audio_noise_sample_dir(label: str) -> Path | None:
    directory = LINE_AUDIO_NOISE_SAMPLE_LABEL_DIRS.get(str(label or "").strip())
    if not directory:
        return None
    return ROOT_DIR / "models" / "line_audio_noise_samples" / directory


def _resolve_line_audio_noise_sample(label: str, name: str) -> Path | None:
    file_name = Path(str(name or "")).name
    if not file_name or file_name in {".", ".."}:
        return None
    raw_sample_dir = _line_audio_noise_sample_dir(label)
    if raw_sample_dir is None:
        return None
    sample_dir = raw_sample_dir.resolve()
    sample_path = (sample_dir / file_name).resolve()
    try:
        sample_path.relative_to(sample_dir)
    except ValueError:
        return None
    return sample_path


def _sample_label_display_name(label: str) -> str:
    return LINE_AUDIO_NOISE_SAMPLE_LABEL_DIRS.get(str(label or "").strip(), str(label or ""))


def _probe_manual_sample_duration(sample_path: Path) -> float:
    return float(probe_audio_duration_seconds(sample_path))


def _edit_manual_abnormal_sample(sample_path: Path, body: dict) -> tuple[bool, str, dict]:
    mode = str(body.get("mode") or "keep").strip().lower()
    if mode not in {"keep", "remove"}:
        return False, "unsupported edit mode", {}
    original_duration = _probe_manual_sample_duration(sample_path)
    if original_duration <= 0:
        return False, "无法读取样本时长", {}

    ffmpeg_cmd: list[str]
    if mode == "keep":
        try:
            start = max(0.0, min(float(body.get("startSeconds") or 0), original_duration))
            end = max(0.0, min(float(body.get("endSeconds") or 0), original_duration))
        except (TypeError, ValueError):
            return False, "invalid edit range", {}
        if end <= start or end - start < 0.05:
            return False, "请选择有效的保留片段", {}
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(sample_path),
            "-c:a",
            "flac",
        ]
    else:
        raw_segments = body.get("segments") if isinstance(body.get("segments"), list) else []
        delete_segments: list[dict[str, float]] = []
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            try:
                segment_start = max(0.0, min(float(item.get("start") or 0), original_duration))
                segment_end = max(0.0, min(float(item.get("end") or 0), original_duration))
            except (TypeError, ValueError):
                continue
            if segment_end - segment_start >= 0.05:
                delete_segments.append({"start": segment_start, "end": segment_end})
        delete_segments.sort(key=lambda item: item["start"])
        normalized_segments: list[dict[str, float]] = []
        for item in delete_segments:
            if normalized_segments and item["start"] <= normalized_segments[-1]["end"] + 0.02:
                normalized_segments[-1]["end"] = max(normalized_segments[-1]["end"], item["end"])
            else:
                normalized_segments.append(dict(item))
        if not normalized_segments:
            return False, "请先标记要删除的样本片段", {}
        keep_segments: list[dict[str, float]] = []
        cursor = 0.0
        for item in normalized_segments:
            if item["start"] - cursor >= 0.05:
                keep_segments.append({"start": cursor, "end": item["start"]})
            cursor = max(cursor, item["end"])
        if original_duration - cursor >= 0.05:
            keep_segments.append({"start": cursor, "end": original_duration})
        if not keep_segments:
            return False, "不能删除整段样本", {}
        if len(keep_segments) == 1:
            keep = keep_segments[0]
            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{keep['start']:.3f}",
                "-to",
                f"{keep['end']:.3f}",
                "-i",
                str(sample_path),
                "-c:a",
                "flac",
            ]
        else:
            filter_parts = []
            concat_inputs = []
            for index, keep in enumerate(keep_segments):
                label = f"a{index}"
                filter_parts.append(
                    f"[0:a]atrim=start={keep['start']:.3f}:end={keep['end']:.3f},asetpts=PTS-STARTPTS[{label}]"
                )
                concat_inputs.append(f"[{label}]")
            filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(keep_segments)}:v=0:a=1[outa]")
            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(sample_path),
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                "[outa]",
                "-c:a",
                "flac",
            ]

    tmp_path = sample_path.with_name(f"{sample_path.stem}.edit-tmp{sample_path.suffix}")
    try:
        subprocess.run([*ffmpeg_cmd, str(tmp_path)], check=True, capture_output=True)
        if not tmp_path.exists() or tmp_path.stat().st_size <= 0:
            return False, "样本编辑输出为空", {}
        new_duration = round(_probe_manual_sample_duration(tmp_path), 3)
        if new_duration <= 0:
            return False, "无法读取编辑后的样本时长", {}
        shutil.move(str(tmp_path), str(sample_path))
        stat = sample_path.stat()
        return True, "edited", {"durationSeconds": new_duration, "size": int(stat.st_size), "updatedAt": int(stat.st_mtime)}
    except subprocess.CalledProcessError as exc:
        error_text = (exc.stderr or b"").decode("utf-8", errors="ignore").strip()
        return False, error_text or "ffmpeg 样本编辑失败", {}
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


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


def _build_media_cache_token(file_path: Path | None) -> str:
    if not file_path or not file_path.exists() or not file_path.is_file():
        return ""
    stat = file_path.stat()
    return f"{int(stat.st_mtime_ns)}-{int(stat.st_size)}"


MEDIA_OPEN_RANGE_CHUNK_BYTES = 512 * 1024


def _build_bundle_record(zip_path: Path) -> dict:
    stat = zip_path.stat()
    return {
        "fileName": zip_path.name,
        "sizeBytes": int(stat.st_size),
        "createdAt": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "audioPreset": _detect_bundle_audio_preset(zip_path.name),
        "audioVariant": _detect_bundle_audio_variant(zip_path.name),
    }


BUNDLE_AUDIO_PRESETS = {
    "lossless": {"suffix": "lossless", "audio_ext": ".flac", "codec": None, "bitrate": None, "channels": None},
    "mp3-128k": {"suffix": "mp3-128k", "audio_ext": ".mp3", "codec": "libmp3lame", "bitrate": "128k", "channels": None},
    "mp3-96k": {"suffix": "mp3-96k", "audio_ext": ".mp3", "codec": "libmp3lame", "bitrate": "96k", "channels": None},
    "mp3-64k": {"suffix": "mp3-64k", "audio_ext": ".mp3", "codec": "libmp3lame", "bitrate": "64k", "channels": None},
    "mp3-48k-mono": {"suffix": "mp3-48k-mono", "audio_ext": ".mp3", "codec": "libmp3lame", "bitrate": "48k", "channels": 1},
}

BUNDLE_TASKS_LOCK = threading.Lock()
BUNDLE_TASKS: dict[int, dict] = {}
VIDEO_COVER_BUNDLE_TASKS_LOCK = threading.Lock()
VIDEO_COVER_BUNDLE_TASKS: dict[int, dict] = {}


def _normalize_bundle_audio_preset(value: str) -> str:
    preset = str(value or "lossless").strip().lower()
    return preset if preset in BUNDLE_AUDIO_PRESETS else "lossless"


def _get_bundle_task_status(novel_id: int) -> dict | None:
    with BUNDLE_TASKS_LOCK:
        task = BUNDLE_TASKS.get(int(novel_id))
        return dict(task) if task else None


def _set_bundle_task_status(novel_id: int, **updates) -> dict:
    with BUNDLE_TASKS_LOCK:
        current = dict(BUNDLE_TASKS.get(int(novel_id)) or {})
        current.update(updates)
        BUNDLE_TASKS[int(novel_id)] = current
        return dict(current)


def _video_cover_bundle_output_dir() -> Path:
    output_dir = ROOT_DIR / "output" / "video-covers"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _build_video_cover_bundle_record(zip_path: Path) -> dict:
    stat = zip_path.stat()
    return {
        "fileName": zip_path.name,
        "sizeBytes": int(stat.st_size),
        "createdAt": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def _get_video_cover_bundle_status(novel_id: int) -> dict | None:
    with VIDEO_COVER_BUNDLE_TASKS_LOCK:
        task = VIDEO_COVER_BUNDLE_TASKS.get(int(novel_id))
        return dict(task) if task else None


def _set_video_cover_bundle_status(novel_id: int, **updates) -> dict:
    with VIDEO_COVER_BUNDLE_TASKS_LOCK:
        current = dict(VIDEO_COVER_BUNDLE_TASKS.get(int(novel_id)) or {})
        current.update(updates)
        VIDEO_COVER_BUNDLE_TASKS[int(novel_id)] = current
        return dict(current)


def _get_video_cover_bundle_entries(novel_id: int) -> tuple[bool, str, str, list[dict]]:
    conn = db_conn()
    novel = conn.execute(
        "SELECT id,english_dir FROM novels WHERE id=?",
        (novel_id,),
    ).fetchone()
    if not novel:
        conn.close()
        return False, "novel not found", "", []
    rows = conn.execute(
        """
        SELECT id,chapter_num,chapter_title
        FROM chapter_video_export_tasks
        WHERE novel_id=? AND status='completed' AND COALESCE(output_file_path,'')<>''
        ORDER BY chapter_num ASC,id ASC
        """,
        (novel_id,),
    ).fetchall()
    conn.close()
    english_dir = str(novel["english_dir"] or "").strip()
    if not english_dir:
        return False, "novel english_dir missing", "", []
    entries = [
        {
            "taskId": int(row["id"]),
            "chapterNum": int(row["chapter_num"] or 0),
            "chapterTitle": str(row["chapter_title"] or ""),
        }
        for row in rows
    ]
    if not entries:
        return False, "completed video export covers not found", english_dir, []
    return True, "ok", english_dir, entries


def _run_video_cover_bundle_task(novel_id: int) -> None:
    try:
        ok, msg, english_dir, entries = _get_video_cover_bundle_entries(novel_id)
        if not ok:
            _set_video_cover_bundle_status(novel_id, status="failed", error=msg)
            return
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_name = f"{english_dir}-video-covers-{stamp}.zip"
        zip_path = _video_cover_bundle_output_dir() / out_name
        total = len(entries)
        _set_video_cover_bundle_status(
            novel_id,
            status="running",
            current=0,
            total=total,
            error="",
            fileName=out_name,
            bundle=None,
        )
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for index, entry in enumerate(entries, start=1):
                cover_path, cover_name = get_video_export_cover_path(int(entry["taskId"]))
                if not cover_path or not cover_path.exists():
                    _set_video_cover_bundle_status(novel_id, current=index)
                    continue
                safe_name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", cover_name or f"cover-{entry['taskId']}.jpg").strip(" ._") or "cover.jpg"
                arcname = Path(english_dir) / "video-covers" / f"{int(entry['chapterNum']):03d}-{int(entry['taskId'])}-{safe_name}"
                zf.write(cover_path, arcname=str(arcname))
                _set_video_cover_bundle_status(novel_id, current=index)
        _set_video_cover_bundle_status(
            novel_id,
            status="completed",
            current=total,
            total=total,
            bundle=_build_video_cover_bundle_record(zip_path),
        )
    except Exception as exc:
        _set_video_cover_bundle_status(novel_id, status="failed", error=str(exc))


def _start_video_cover_bundle_task(novel_id: int) -> tuple[bool, str, dict | None]:
    current = _get_video_cover_bundle_status(novel_id)
    if current and current.get("status") == "running":
        return True, "running", current
    task = _set_video_cover_bundle_status(
        novel_id,
        status="queued",
        current=0,
        total=0,
        error="",
        startedAt=datetime.now().isoformat(),
        fileName="",
        bundle=None,
    )
    threading.Thread(target=_run_video_cover_bundle_task, args=(int(novel_id),), daemon=True).start()
    return True, "started", task


def _list_video_cover_bundle_files(novel_id: int) -> tuple[bool, str, str, list[dict]]:
    conn = db_conn()
    row = conn.execute("SELECT english_dir FROM novels WHERE id=?", (novel_id,)).fetchone()
    conn.close()
    if not row:
        return False, "novel not found", "", []
    english_dir = str(row["english_dir"] or "").strip()
    if not english_dir:
        return False, "novel english_dir missing", "", []
    files = sorted(
        _video_cover_bundle_output_dir().glob(f"{english_dir}-video-covers-*.zip"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return True, "ok", english_dir, [_build_video_cover_bundle_record(path) for path in files if path.is_file()]


def _detect_bundle_audio_preset(file_name: str) -> str:
    text = str(file_name or "")
    for key, preset in BUNDLE_AUDIO_PRESETS.items():
        suffix = str(preset["suffix"])
        if f"-{suffix}-" in text:
            return key
    return "lossless"


def _normalize_bundle_audio_variant(value: str) -> str:
    variant = str(value or "ver").strip().lower()
    return variant if variant in {"ver", "nonver"} else "ver"


def _detect_bundle_audio_variant(file_name: str) -> str:
    return "nonver" if "-nonver-" in str(file_name or "") else "ver"


def _bundle_temp_dir(english_dir: str, stamp: str) -> Path:
    path = ROOT_DIR / "temp" / "bundle-build" / english_dir / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def _transcode_bundle_audio(src_path: Path, out_path: Path, audio_preset: str) -> None:
    preset = BUNDLE_AUDIO_PRESETS[_normalize_bundle_audio_preset(audio_preset)]
    codec = preset.get("codec")
    if not codec:
        shutil.copy2(src_path, out_path)
        return
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(src_path),
        "-vn",
        "-codec:a",
        str(codec),
        "-b:a",
        str(preset["bitrate"]),
    ]
    if preset.get("channels"):
        command.extend(["-ac", str(int(preset["channels"]))])
    command.append(str(out_path))
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _get_novel_bundle_entries(
    novel_id: int,
    audio_preset: str = "lossless",
    audio_variant: str = "ver",
) -> tuple[bool, str, str, list[dict]]:
    audio_variant = _normalize_bundle_audio_variant(audio_variant)
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

    preset = BUNDLE_AUDIO_PRESETS[_normalize_bundle_audio_preset(audio_preset)]
    bundle_entries: list[dict] = []
    for chapter in chapters:
        chapter_num = int(chapter["chapter_num"] or 0)
        title = str(chapter["title"] or "")
        text_name = safe_chapter_file_name(chapter_num, title)
        audio_name = text_name.replace(".txt", str(preset["audio_ext"]))

        text_src = _resolve_storage_path(str(chapter["text_file_path"] or ""))
        if not (text_src and text_src.exists() and text_src.is_file()):
            text_src = None

        if audio_variant == "ver":
            audio_src = resolve_audio_file(chapter)
        else:
            audio_src = get_chapter_merged_audio_path(
                novel_id,
                int(chapter["id"]),
                include_copyright=False,
            )
        if not (audio_src and audio_src.exists() and audio_src.is_file()):
            audio_src = None

        if text_src or audio_src:
            bundle_entries.append(
                {
                    "chapterNum": chapter_num,
                    "textSrc": text_src,
                    "textArc": Path(english_dir) / "text" / text_name,
                    "audioSrc": audio_src,
                    "audioArc": Path(english_dir) / ("audio" if audio_variant == "ver" else "audio_non_ver") / audio_name,
                }
            )

    if not bundle_entries:
        return False, "novel text/audio files not found", english_dir, []
    return True, "ok", english_dir, bundle_entries


def _create_novel_bundle_file(
    novel_id: int, audio_preset: str = "lossless", audio_variant: str = "ver"
) -> tuple[bool, str, dict | None]:
    audio_preset = _normalize_bundle_audio_preset(audio_preset)
    audio_variant = _normalize_bundle_audio_variant(audio_variant)
    ok, msg, english_dir, bundle_entries = _get_novel_bundle_entries(novel_id, audio_preset, audio_variant)
    if not ok:
        return False, msg, None

    stamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    suffix = str(BUNDLE_AUDIO_PRESETS[audio_preset]["suffix"])
    out_name = f"{english_dir}-{audio_variant}-{suffix}-{stamp}.zip"
    zip_path = _bundle_output_dir() / out_name
    temp_dir = _bundle_temp_dir(english_dir, stamp)
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for entry in bundle_entries:
                text_src = entry.get("textSrc")
                audio_src = entry.get("audioSrc")
                if text_src:
                    zf.write(text_src, arcname=str(entry["textArc"]))
                if audio_src:
                    target_path = audio_src
                    if audio_preset != "lossless":
                        target_path = temp_dir / Path(str(entry["audioArc"])).name
                        _transcode_bundle_audio(audio_src, target_path, audio_preset)
                    zf.write(target_path, arcname=str(entry["audioArc"]))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return True, "created", _build_bundle_record(zip_path)


def _run_novel_bundle_task(novel_id: int, audio_preset: str, audio_variant: str) -> None:
    try:
        ok, msg, english_dir, bundle_entries = _get_novel_bundle_entries(novel_id, audio_preset, audio_variant)
        if not ok:
            _set_bundle_task_status(novel_id, status="failed", error=msg)
            return

        audio_preset = _normalize_bundle_audio_preset(audio_preset)
        audio_variant = _normalize_bundle_audio_variant(audio_variant)
        stamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        suffix = str(BUNDLE_AUDIO_PRESETS[audio_preset]["suffix"])
        out_name = f"{english_dir}-{audio_variant}-{suffix}-{stamp}.zip"
        zip_path = _bundle_output_dir() / out_name
        temp_dir = _bundle_temp_dir(english_dir, stamp)
        total = len(bundle_entries)
        _set_bundle_task_status(
            novel_id,
            status="running",
            current=0,
            total=total,
            fileName=out_name,
            error="",
            audioPreset=audio_preset,
            audioVariant=audio_variant,
        )
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for index, entry in enumerate(bundle_entries, start=1):
                    text_src = entry.get("textSrc")
                    audio_src = entry.get("audioSrc")
                    if text_src:
                        zf.write(text_src, arcname=str(entry["textArc"]))
                    if audio_src:
                        target_path = audio_src
                        if audio_preset != "lossless":
                            target_path = temp_dir / Path(str(entry["audioArc"])).name
                            _transcode_bundle_audio(audio_src, target_path, audio_preset)
                        zf.write(target_path, arcname=str(entry["audioArc"]))
                    _set_bundle_task_status(novel_id, current=index)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        _set_bundle_task_status(
            novel_id,
            status="completed",
            current=total,
            total=total,
            bundle=_build_bundle_record(zip_path),
        )
    except Exception as exc:
        _set_bundle_task_status(novel_id, status="failed", error=str(exc))


def _start_novel_bundle_task(novel_id: int, audio_preset: str, audio_variant: str) -> tuple[bool, str, dict | None]:
    current = _get_bundle_task_status(novel_id)
    if current and current.get("status") == "running":
        return True, "running", current
    audio_preset = _normalize_bundle_audio_preset(audio_preset)
    audio_variant = _normalize_bundle_audio_variant(audio_variant)
    task = _set_bundle_task_status(
        novel_id,
        status="queued",
        current=0,
        total=0,
        error="",
        audioPreset=audio_preset,
        audioVariant=audio_variant,
        startedAt=datetime.now().isoformat(),
        bundle=None,
        fileName="",
    )
    threading.Thread(
        target=_run_novel_bundle_task,
        args=(int(novel_id), audio_preset, audio_variant),
        daemon=True,
    ).start()
    return True, "started", task


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
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _guess_media_type(self, file_path: Path, fallback: str = "application/octet-stream") -> str:
        guessed = mimetypes.guess_type(file_path.name)[0]
        if guessed == "audio/x-flac":
            return "audio/flac"
        return guessed or fallback

    def send_json(self, payload: dict | list, status: int = 200) -> None:
        self._drain_unread_request_body()
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _reset_request_body_state(self) -> None:
        self._request_body_cache = None
        self._request_body_consumed = False

    def _read_request_body(self) -> bytes:
        if getattr(self, "_request_body_consumed", False):
            return getattr(self, "_request_body_cache", None) or b""
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size) if size > 0 else b""
        self._request_body_cache = raw
        self._request_body_consumed = True
        return raw

    def _drain_unread_request_body(self) -> None:
        if self.command not in {"POST", "PUT", "DELETE", "PATCH"}:
            return
        if getattr(self, "_request_body_consumed", False):
            return
        self._read_request_body()

    def read_json(self) -> dict:
        raw = self._read_request_body().decode("utf-8", errors="ignore")
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
        send_body: bool = True,
        max_open_range_bytes: int | None = None,
    ) -> None:
        file_size = file_path.stat().st_size
        stat = file_path.stat()
        range_values = self._parse_range_header(file_size)

        if str(self.headers.get("Range") or "").strip() and range_values is None:
            return

        start = 0
        end = file_size - 1
        status = 200
        if range_values is not None:
            start, end = range_values
            range_header = str(self.headers.get("Range") or "").strip()
            if max_open_range_bytes and re.match(r"^bytes=\d+-$", range_header):
                end = min(end, start + int(max_open_range_bytes) - 1)
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
        self.send_header("ETag", f'W/"{int(stat.st_mtime_ns)}-{int(file_size)}"')
        self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
        self.send_header("Content-Length", str(content_length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()

        if not send_body:
            return

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
        query = parse_qs(parsed.query or "")

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
            query = parse_qs(parsed.query or "")
            include_raw = str((query.get("include") or [""])[0] or "").strip()
            requested = {
                item.strip()
                for item in include_raw.split(",")
                if item.strip()
            }
            allowed = {"novels", "novelsFull", "prompts", "workflows", "settings", "jsonTasks"}
            sections = requested & allowed if requested else allowed
            conn = db_conn()
            if "prompts" in sections:
                sync_system_prompt_from_file(conn)
            if "workflows" in sections:
                sync_system_workflow_from_file(conn)
            conn.commit()
            data = {}
            if "novelsFull" in sections:
                data["novels"] = fetch_novels(conn)
            elif "novels" in sections:
                data["novels"] = fetch_novels_light(conn)
            if "prompts" in sections:
                data["prompts"] = fetch_prompts(conn)
            if "workflows" in sections:
                data["workflows"] = fetch_workflows(conn)
            if "settings" in sections:
                data["settings"] = fetch_settings(conn)
            if "jsonTasks" in sections:
                data["jsonTasks"] = fetch_json_tasks(conn)
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

        m_bundle_status = re.match(r"^/api/novels/(\d+)/bundles/status$", route)
        if m_bundle_status:
            novel_id = int(m_bundle_status.group(1))
            task = _get_bundle_task_status(novel_id) or {
                "status": "idle",
                "current": 0,
                "total": 0,
                "error": "",
                "audioPreset": "lossless",
                "fileName": "",
                "bundle": None,
            }
            self.send_json({"task": task})
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

        m_audio_asr_chapters = re.match(r"^/api/novels/(\d+)/audio-asr-chapters$", route)
        if m_audio_asr_chapters:
            ensure_task_worker()
            novel_id = int(m_audio_asr_chapters.group(1))
            self.send_json({"chapters": list_audio_asr_chapters(novel_id)})
            return

        m_nsfw_review_chapters = re.match(r"^/api/novels/(\d+)/nsfw-review-chapters$", route)
        if m_nsfw_review_chapters:
            ensure_task_worker()
            novel_id = int(m_nsfw_review_chapters.group(1))
            self.send_json({"chapters": list_nsfw_review_chapters(novel_id)})
            return

        m_illustration_chapters = re.match(r"^/api/novels/(\d+)/illustration-chapters$", route)
        if m_illustration_chapters:
            ensure_illustration_workers()
            novel_id = int(m_illustration_chapters.group(1))
            self.send_json({"chapters": list_illustration_chapters(novel_id)})
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

            ctype = self._guess_media_type(abs_audio)
            self.send_file_response(
                abs_audio,
                ctype,
                cache_control="public, max-age=31536000, immutable",
                max_open_range_bytes=MEDIA_OPEN_RANGE_CHUNK_BYTES,
            )
            return

        m_asr_file = re.match(r"^/api/novels/(\d+)/chapters/(\d+)/asr-file$", route)
        if m_asr_file:
            novel_id = int(m_asr_file.group(1))
            chapter_num = int(m_asr_file.group(2))
            conn = db_conn()
            row = conn.execute(
                "SELECT title FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            task = conn.execute(
                "SELECT asr_file_path FROM chapter_asr_tasks WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            rel = str(task["asr_file_path"] or "").strip() if task else ""
            file_path = (ROOT_DIR / rel).resolve() if rel else None
            if not file_path or not file_path.exists() or not file_path.is_file():
                self.send_json({"error": "asr file not found"}, 404)
                return
            title = str(row["title"] or f"chapter_{chapter_num}") if row else f"chapter_{chapter_num}"
            download_name = safe_chapter_file_name(chapter_num, title).replace(".txt", ".asr")
            self.send_file_response(
                file_path,
                "text/plain; charset=utf-8",
                cache_control="no-store",
                download_name=download_name,
            )
            return

        m_corrected_srt_file = re.match(r"^/api/novels/(\d+)/chapters/(\d+)/corrected-srt-file$", route)
        if m_corrected_srt_file:
            novel_id = int(m_corrected_srt_file.group(1))
            chapter_num = int(m_corrected_srt_file.group(2))
            conn = db_conn()
            row = conn.execute(
                "SELECT title FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            task = conn.execute(
                "SELECT corrected_srt_file_path FROM chapter_asr_tasks WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            rel = str(task["corrected_srt_file_path"] or "").strip() if task else ""
            file_path = (ROOT_DIR / rel).resolve() if rel else None
            if not file_path or not file_path.exists() or not file_path.is_file():
                self.send_json({"error": "corrected srt file not found"}, 404)
                return
            title = str(row["title"] or f"chapter_{chapter_num}") if row else f"chapter_{chapter_num}"
            download_name = safe_chapter_file_name(chapter_num, title).replace(".txt", ".srt")
            self.send_file_response(
                file_path,
                "application/x-subrip; charset=utf-8",
                cache_control="no-store",
                download_name=download_name,
            )
            return

        m_illustration_image_file = re.match(r"^/api/illustration-images/(\d+)/file$", route)
        if m_illustration_image_file:
            image_id = int(m_illustration_image_file.group(1))
            conn = db_conn()
            row = conn.execute(
                "SELECT image_file_path FROM chapter_illustration_images WHERE id=?",
                (image_id,),
            ).fetchone()
            conn.close()
            rel = str(row["image_file_path"] or "").strip() if row else ""
            path = (ROOT_DIR / rel).resolve() if rel else None
            if not path or not path.exists() or not path.is_file():
                self.send_json({"error": "image not found"}, 404)
                return
            self.send_file_response(path, self._guess_media_type(path), cache_control="no-store")
            return

        m_chapter = re.match(r"^/api/novels/(\d+)/chapters/(\d+)$", route)
        if m_chapter:
            novel_id = int(m_chapter.group(1))
            chapter_num = int(m_chapter.group(2))
            conn = db_conn()
            row = conn.execute(
                """
                SELECT c.id,c.novel_id,c.chapter_num,c.title,c.word_count,c.text_file_path,c.audio_file_path,c.audio_duration_seconds,c.has_json,c.has_audio,
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
            ver_audio_path = resolve_audio_file(row)
            non_ver_audio_path = get_chapter_merged_audio_path(
                novel_id, int(row["id"]), include_copyright=False
            )
            non_ver_stats = get_chapter_merged_audio_stats(
                novel_id, int(row["id"]), include_copyright=False
            )
            self.send_json(
                {
                    "id": int(row["id"]),
                    "chapterNum": int(row["chapter_num"]),
                    "title": str(row["title"]),
                    "wordCount": int(row["word_count"] or 0),
                    "hasJson": bool(row["has_json"]),
                    "hasAudio": ver_audio_path is not None,
                    "audioDurationSeconds": float(row["audio_duration_seconds"] or 0),
                    "audioVersion": _build_media_cache_token(ver_audio_path),
                    "hasNonVerAudio": bool(non_ver_stats["hasAudio"]),
                    "nonVerAudioDurationSeconds": float(non_ver_stats["durationSeconds"] or 0),
                    "nonVerAudioVersion": _build_media_cache_token(non_ver_audio_path),
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

        if route == "/api/task-worker/status":
            self.send_json(get_task_worker_status())
            return

        if route == "/api/line-audio-worker/status":
            self.send_json(get_line_audio_worker_status())
            return

        if route == "/api/audio-asr-worker/status":
            self.send_json(get_audio_asr_worker_status())
            return

        if route == "/api/nsfw-review-worker/status":
            self.send_json(get_nsfw_review_worker_status())
            return

        if route == "/api/illustration-worker/status":
            self.send_json(get_illustration_workers_status())
            return

        if route == "/api/illustration-llm-worker/status":
            self.send_json(get_illustration_worker_status())
            return

        if route == "/api/illustration-image-worker/status":
            self.send_json(get_illustration_image_worker_status())
            return

        if route == "/api/video-export-worker/status":
            self.send_json(get_video_export_worker_status())
            return

        if route == "/api/video-export-tasks":
            novel_id = int(query.get("novelId", [0])[0] or 0)
            self.send_json({"tasks": list_video_export_tasks(novel_id or None)})
            return

        m_video_cover_bundle_status = re.match(r"^/api/novels/(\d+)/video-cover-bundle/status$", route)
        if m_video_cover_bundle_status:
            novel_id = int(m_video_cover_bundle_status.group(1))
            task = _get_video_cover_bundle_status(novel_id) or {
                "status": "idle",
                "current": 0,
                "total": 0,
                "error": "",
                "fileName": "",
                "bundle": None,
            }
            self.send_json({"task": task})
            return

        m_video_cover_bundle_file = re.match(r"^/api/novels/(\d+)/video-cover-bundles/(.+)$", route)
        if m_video_cover_bundle_file:
            novel_id = int(m_video_cover_bundle_file.group(1))
            file_name = unquote(str(m_video_cover_bundle_file.group(2) or "").strip())
            ok, msg, english_dir, bundles = _list_video_cover_bundle_files(novel_id)
            if not ok:
                code = 404 if "not found" in msg else 400
                self.send_json({"error": msg}, code)
                return
            allowed = {str(item["fileName"]) for item in bundles}
            if file_name not in allowed or not file_name.startswith(f"{english_dir}-video-covers-"):
                self.send_json({"error": "cover bundle not found"}, 404)
                return
            zip_path = (_video_cover_bundle_output_dir() / file_name).resolve()
            if not zip_path.exists() or not zip_path.is_file():
                self.send_json({"error": "cover bundle not found"}, 404)
                return
            self.send_file_response(zip_path, "application/zip", download_name=file_name, cache_control="no-store")
            return

        m_video_export_status = re.match(r"^/api/novels/(\d+)/chapters/(\d+)/video-export/status$", route)
        if m_video_export_status:
            novel_id = int(m_video_export_status.group(1))
            chapter_num = int(m_video_export_status.group(2))
            conn = db_conn()
            row = conn.execute(
                """
                SELECT t.id
                FROM chapter_video_export_tasks t
                JOIN chapters c ON c.id=t.chapter_id
                WHERE t.novel_id=? AND c.chapter_num=?
                ORDER BY t.id DESC LIMIT 1
                """,
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            self.send_json({"task": get_video_export_task(int(row["id"])) if row else None})
            return

        m_video_export_file = re.match(r"^/api/video-export-tasks/(\d+)/file$", route)
        if m_video_export_file:
            task_id = int(m_video_export_file.group(1))
            path, filename = get_video_export_file_path(task_id)
            if not path:
                self.send_json({"error": "video file not found"}, 404)
                return
            self.send_file_response(path, "video/mp4", download_name=filename, cache_control="no-store")
            return

        m_video_export_cover = re.match(r"^/api/video-export-tasks/(\d+)/cover$", route)
        if m_video_export_cover:
            task_id = int(m_video_export_cover.group(1))
            image_index_raw = str((query.get("imageIndex") or [""])[0] or "").strip()
            image_index = int(image_index_raw) if image_index_raw.isdigit() else None
            try:
                path, filename = get_video_export_cover_path(task_id, image_index=image_index)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            if not path:
                self.send_json({"error": "cover file not found"}, 404)
                return
            self.send_file_response(path, "image/jpeg", download_name=filename, cache_control="no-store")
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
                       input_text,llm_response_text,parsed_json_text,retry_count,auto_retry_count,updated_at
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
                            "retryCount": int(x["retry_count"] or 0),
                            "autoRetryCount": int(x["auto_retry_count"] or 0),
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

        m_role_sample = re.match(r"^/api/novels/(\d+)/roles/(\d+)/sample(?:/[^/]+)?$", route)
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
            suffix = abs_path.suffix if abs_path.suffix else ".flac"
            download_name = f"{role_id}-{role_name}{suffix}"
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

        m_copyright_audio_file = re.match(
            r"^/api/settings/copyright-audio/(intro|outro)/file$", route
        )
        if m_copyright_audio_file:
            kind = m_copyright_audio_file.group(1)
            conn = db_conn()
            row = conn.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key=?",
                (f"copyright_audio_{kind}_path",),
            ).fetchone()
            conn.close()
            file_path = str(row["setting_value"] or "").strip() if row else ""
            if not file_path:
                self.send_json({"error": "audio file not found"}, 404)
                return
            abs_path = (ROOT_DIR / file_path).resolve()
            if not abs_path.exists() or not abs_path.is_file():
                self.send_json({"error": "audio file not found"}, 404)
                return
            ctype = mimetypes.guess_type(abs_path.name)[0] or "audio/flac"
            self.send_file_response(abs_path, ctype, cache_control="no-store")
            return

        if route == "/api/settings/live-ending-audio/file":
            requested_path = str((parse_qs(parsed.query or "").get("path") or [""])[0] or "").strip()
            conn = db_conn()
            row = conn.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key=?",
                ("live_ending_audio_items",),
            ).fetchone()
            legacy_row = conn.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key=?",
                ("live_ending_audio_path",),
            ).fetchone()
            conn.close()
            items = normalize_live_ending_audio_items(
                str(row["setting_value"] or "").strip() if row else "",
                str(legacy_row["setting_value"] or "").strip() if legacy_row else "",
            )
            file_path = requested_path or (items[0]["path"] if items else "")
            if not file_path:
                self.send_json({"error": "audio file not found"}, 404)
                return
            abs_path = (ROOT_DIR / file_path).resolve()
            allowed_dir = (ROOT_DIR / "temp" / "settings").resolve()
            if requested_path:
                try:
                    abs_path.relative_to(allowed_dir)
                except ValueError:
                    if items and file_path not in {str(item.get("path") or "").strip() for item in items}:
                        self.send_json({"error": "audio file not found"}, 404)
                        return
            if not abs_path.exists() or not abs_path.is_file():
                self.send_json({"error": "audio file not found"}, 404)
                return
            ctype = mimetypes.guess_type(abs_path.name)[0] or "audio/flac"
            self.send_file_response(abs_path, ctype, cache_control="no-store")
            return

        if route == "/api/settings/video-cover-logo/file":
            conn = db_conn()
            settings = fetch_settings(conn)
            conn.close()
            path = resolve_video_cover_logo_path(settings)
            if not path or not path.exists() or not path.is_file():
                self.send_json({"error": "logo file not found"}, 404)
                return
            ctype = mimetypes.guess_type(path.name)[0] or "image/png"
            self.send_file_response(path, ctype, cache_control="no-store")
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
            ensure_line_audio_worker()
            kick_line_audio_queue_once()
            novel_id = int(m_line_audio_tasks.group(1))
            query = parse_qs(parsed.query or "")
            limit = int((query.get("limit") or ["100"])[0])
            offset = int((query.get("offset") or ["0"])[0])
            data = list_line_audio_tasks(novel_id, limit=limit, offset=offset)
            self.send_json(data)
            return

        m_role_line_audios = re.match(r"^/api/novels/(\d+)/role-line-audios$", route)
        if m_role_line_audios:
            novel_id = int(m_role_line_audios.group(1))
            query = parse_qs(parsed.query or "")
            role_name = str((query.get("roleName") or [""])[0] or "").strip()
            page = int((query.get("page") or ["1"])[0])
            page_size = int((query.get("pageSize") or ["50"])[0])
            raw_chapter_num = str((query.get("chapterNum") or [""])[0] or "").strip()
            chapter_num = int(raw_chapter_num) if raw_chapter_num else None
            data = list_role_line_audio_entries(
                novel_id, role_name=role_name, page=page, page_size=page_size, chapter_num=chapter_num
            )
            self.send_json(data)
            return

        m_role_line_counts = re.match(r"^/api/novels/(\d+)/role-line-counts$", route)
        if m_role_line_counts:
            novel_id = int(m_role_line_counts.group(1))
            query = parse_qs(parsed.query or "")
            raw_chapter_num = str((query.get("chapterNum") or [""])[0] or "").strip()
            chapter_num = int(raw_chapter_num) if raw_chapter_num else None
            self.send_json({"counts": list_role_line_counts(novel_id, chapter_num=chapter_num)})
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

        m_line_audio_silences = re.match(r"^/api/line-audio-tasks/(\d+)/silences$", route)
        if m_line_audio_silences:
            task_id = int(m_line_audio_silences.group(1))
            query = parse_qs(parsed.query or "")
            noise_db = str((query.get("noiseDb") or ["-45dB"])[0] or "-45dB").strip()
            try:
                min_duration = float((query.get("minDuration") or ["1.2"])[0] or 1.2)
            except (TypeError, ValueError):
                self.send_json({"error": "invalid silence min duration"}, 400)
                return
            ok, msg, data = detect_line_audio_task_silences(
                task_id,
                noise_db=noise_db,
                min_duration=min_duration,
            )
            if not ok:
                code = 404 if "不存在" in msg or "not found" in msg else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json(data)
            return

        m_line_audio_loudness = re.match(r"^/api/line-audio-tasks/(\d+)/loudness$", route)
        if m_line_audio_loudness:
            task_id = int(m_line_audio_loudness.group(1))
            query = parse_qs(parsed.query or "")
            try:
                target_lufs = float((query.get("targetLufs") or ["-20"])[0] or -20)
            except (TypeError, ValueError):
                self.send_json({"error": "invalid target LUFS"}, 400)
                return
            ok, msg, data = analyze_line_audio_task_loudness(
                task_id,
                target_lufs=target_lufs,
            )
            if not ok:
                code = 404 if "不存在" in msg or "not found" in msg else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json(data)
            return

        m_line_audio_noise = re.match(r"^/api/line-audio-tasks/(\d+)/noise$", route)
        if m_line_audio_noise:
            task_id = int(m_line_audio_noise.group(1))
            sensitivity = str((parse_qs(parsed.query or "").get("sensitivity") or ["balanced"])[0] or "balanced")
            ok, msg, data = detect_line_audio_task_noise(task_id, sensitivity=sensitivity)
            if not ok:
                code = 404 if "不存在" in msg or "not found" in msg else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json(data)
            return

        m_noise_samples = re.match(r"^/api/line-audio-noise-samples/(manual-abnormal|abnormal|false-positive-normal)$", route)
        if m_noise_samples:
            label = m_noise_samples.group(1)
            sample_dir = _line_audio_noise_sample_dir(label)
            samples = []
            if sample_dir and sample_dir.exists() and sample_dir.is_dir():
                for path in sorted(sample_dir.iterdir(), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
                    if not path.is_file() or path.suffix.lower() not in {".flac", ".wav", ".mp3", ".m4a", ".ogg"}:
                        continue
                    stat = path.stat()
                    name = path.name
                    samples.append(
                        {
                            "label": label,
                            "directory": _sample_label_display_name(label),
                            "name": name,
                            "size": int(stat.st_size),
                            "updatedAt": int(stat.st_mtime),
                            "url": f"/api/line-audio-noise-samples/{label}/files/{quote(name)}",
                        }
                    )
            self.send_json({"label": label, "directory": _sample_label_display_name(label), "samples": samples, "count": len(samples)})
            return

        m_noise_sample_file = re.match(r"^/api/line-audio-noise-samples/(manual-abnormal|abnormal|false-positive-normal)/files/(.+)$", route)
        if m_noise_sample_file:
            sample_path = _resolve_line_audio_noise_sample(m_noise_sample_file.group(1), unquote(m_noise_sample_file.group(2)))
            if sample_path is None or not sample_path.exists() or not sample_path.is_file():
                self.send_json({"error": "sample not found"}, 404)
                return
            ctype = mimetypes.guess_type(sample_path.name)[0] or "audio/flac"
            self.send_file_response(sample_path, ctype, cache_control="no-store")
            return

        m_line_audio_replacements = re.match(r"^/api/line-audio-tasks/(\d+)/replacement-targets$", route)
        if m_line_audio_replacements:
            task_id = int(m_line_audio_replacements.group(1))
            ok, msg, data = preview_line_audio_replacement_targets(task_id)
            if not ok:
                code = 404 if "不存在" in msg or "not found" in msg else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json(data)
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
            variant = _normalize_bundle_audio_variant(parse_qs(parsed.query).get("variant", ["ver"])[0])
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id, title FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            merged_path = get_chapter_merged_audio_path(
                novel_id, int(chapter_row["id"]), include_copyright=(variant == "ver")
            )
            if not merged_path:
                self.send_json({"error": "merged audio not found"}, 404)
                return
            ctype = self._guess_media_type(merged_path, "audio/flac")
            download_name = safe_chapter_file_name(
                chapter_num,
                str(chapter_row["title"] or f"chapter_{chapter_num}"),
            ).replace(".txt", ".flac")
            self.send_file_response(
                merged_path,
                ctype,
                cache_control="public, max-age=31536000, immutable",
                download_name=download_name,
                max_open_range_bytes=MEDIA_OPEN_RANGE_CHUNK_BYTES,
            )
            return

        if not self.serve_static(route):
            self.send_json({"error": "not found"}, 404)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path

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
            conn.close()
            if not row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            abs_audio = resolve_audio_file(row)
            if not abs_audio:
                self.send_json({"error": "audio file not found"}, 404)
                return
            self.send_file_response(
                abs_audio,
                self._guess_media_type(abs_audio),
                cache_control="public, max-age=31536000, immutable",
                send_body=False,
            )
            return

        m_merged_audio = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/merged-audio$", route
        )
        if m_merged_audio:
            novel_id = int(m_merged_audio.group(1))
            chapter_num = int(m_merged_audio.group(2))
            variant = _normalize_bundle_audio_variant(parse_qs(parsed.query).get("variant", ["ver"])[0])
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id, title FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            merged_path = get_chapter_merged_audio_path(
                novel_id, int(chapter_row["id"]), include_copyright=(variant == "ver")
            )
            if not merged_path:
                self.send_json({"error": "merged audio not found"}, 404)
                return
            self.send_file_response(
                merged_path,
                self._guess_media_type(merged_path, "audio/flac"),
                cache_control="public, max-age=31536000, immutable",
                download_name=safe_chapter_file_name(
                    chapter_num,
                    str(chapter_row["title"] or f"chapter_{chapter_num}"),
                ).replace(".txt", ".flac"),
                send_body=False,
            )
            return

        self.send_error(501, "Unsupported method ('HEAD')")

    def do_POST(self) -> None:
        self._reset_request_body_state()
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
                proxy_url=str(body.get("proxyUrl") or "").strip()
                if bool(body.get("proxyEnabled", False))
                else "",
                think=bool(llm.get("think", True)),
                num_ctx=int(llm.get("numCtx") or 65536),
                keep_alive=str(llm.get("keepAlive") or "30m").strip() or "30m",
                unload_after_call=bool(llm.get("unloadAfterCall", False)),
            )
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "ok", "message": msg})
            return

        if route == "/api/settings/local-llama/clear-context":
            body = self.read_json()
            llm = body.get("llm") or {}
            provider = str(llm.get("provider") or "").strip()
            base_url = str(llm.get("baseUrl") or "").strip()
            if provider != "local_llama":
                self.send_json({"error": "仅本地LLama支持清空上下文"}, 400)
                return
            if not base_url:
                self.send_json({"error": "LLM baseUrl is empty"}, 400)
                return
            try:
                clear_local_llama_context(
                    base_url=base_url,
                    proxy_url=str(body.get("proxyUrl") or "").strip()
                    if bool(body.get("proxyEnabled", False))
                    else "",
                    timeout=10.0,
                )
            except Exception as exc:
                self.send_json({"error": str(exc)}, 409)
                return
            self.send_json({"status": "ok", "message": "上下文已清空"})
            return

        m_video_cover_bundle_start = re.match(r"^/api/novels/(\d+)/video-cover-bundle$", route)
        if m_video_cover_bundle_start:
            novel_id = int(m_video_cover_bundle_start.group(1))
            ok, msg, task = _start_video_cover_bundle_task(novel_id)
            if not ok:
                code = 404 if "not found" in msg else 400
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": msg, "task": task})
            return

        m_copyright_audio_upload = re.match(
            r"^/api/settings/copyright-audio/(intro|outro)$", route
        )
        if m_copyright_audio_upload:
            body = self.read_json()
            kind = m_copyright_audio_upload.group(1)
            audio_base64 = str(body.get("audioBase64") or "").strip()
            file_name = (
                str(body.get("fileName") or f"copyright-{kind}.flac").strip()
                or f"copyright-{kind}.flac"
            )
            if not audio_base64:
                self.send_json({"error": "audioBase64 is required"}, 400)
                return
            try:
                audio_bytes = base64.b64decode(audio_base64)
            except Exception:
                self.send_json({"error": "invalid audioBase64"}, 400)
                return
            safe_name = (
                re.sub(r"[^A-Za-z0-9._-]+", "_", file_name).strip("._")
                or f"copyright-{kind}.flac"
            )
            target_dir = ROOT_DIR / "temp" / "settings"
            target_dir.mkdir(parents=True, exist_ok=True)
            suffix = Path(safe_name).suffix or ".flac"
            target_path = target_dir / f"copyright-{kind}{suffix}"
            target_path.write_bytes(audio_bytes)
            rel_path = db_rel_path(target_path.relative_to(ROOT_DIR))
            conn = db_conn()
            conn.execute(
                """
                INSERT INTO app_settings (setting_key,setting_value,updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value, updated_at=CURRENT_TIMESTAMP
                """,
                (f"copyright_audio_{kind}_path", rel_path),
            )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok", "path": rel_path})
            return

        if route == "/api/settings/video-cover-logo":
            body = self.read_json()
            image_base64 = str(body.get("imageBase64") or "").strip()
            file_name = str(body.get("fileName") or "video-cover-logo.png").strip() or "video-cover-logo.png"
            if not image_base64:
                self.send_json({"error": "imageBase64 is required"}, 400)
                return
            try:
                image_bytes = base64.b64decode(image_base64)
            except Exception:
                self.send_json({"error": "invalid imageBase64"}, 400)
                return
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", file_name).strip("._") or "video-cover-logo.png"
            suffix = Path(safe_name).suffix.lower() or ".png"
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                self.send_json({"error": "unsupported logo image type"}, 400)
                return
            target_dir = ROOT_DIR / "temp" / "settings"
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / f"video-cover-logo{suffix}"
            try:
                from PIL import Image

                with Image.open(io.BytesIO(image_bytes)) as img:
                    img.verify()
            except Exception:
                self.send_json({"error": "invalid logo image"}, 400)
                return
            target_path.write_bytes(image_bytes)
            rel_path = db_rel_path(target_path.relative_to(ROOT_DIR))
            conn = db_conn()
            for setting_key, setting_value in (
                ("video_cover_logo_path", rel_path),
                ("video_cover_logo_enabled", "1"),
            ):
                conn.execute(
                    """
                    INSERT INTO app_settings (setting_key,setting_value,updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value, updated_at=CURRENT_TIMESTAMP
                    """,
                    (setting_key, setting_value),
                )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok", "path": rel_path})
            return

        if route == "/api/settings/live-ending-audio":
            body = self.read_json()
            try:
                item_index = int(body.get("index"))
            except (TypeError, ValueError):
                item_index = -1
            audio_base64 = str(body.get("audioBase64") or "").strip()
            file_name = (
                str(body.get("fileName") or "live-ending.flac").strip()
                or "live-ending.flac"
            )
            if item_index < 0:
                self.send_json({"error": "index is required"}, 400)
                return
            if not audio_base64:
                self.send_json({"error": "audioBase64 is required"}, 400)
                return
            try:
                audio_bytes = base64.b64decode(audio_base64)
            except Exception:
                self.send_json({"error": "invalid audioBase64"}, 400)
                return
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", file_name).strip("._") or "live-ending.flac"
            target_dir = ROOT_DIR / "temp" / "settings"
            target_dir.mkdir(parents=True, exist_ok=True)
            suffix = Path(safe_name).suffix or ".flac"
            target_path = target_dir / f"live-ending-{item_index}{suffix}"
            target_path.write_bytes(audio_bytes)
            rel_path = db_rel_path(target_path.relative_to(ROOT_DIR))
            conn = db_conn()
            conn.execute(
                """
                INSERT INTO app_settings (setting_key,setting_value,updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value, updated_at=CURRENT_TIMESTAMP
                """,
                ("live_ending_audio_path", rel_path),
            )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok", "path": rel_path})
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
            body = self.read_json()
            audio_preset = str(body.get("audioPreset") or "lossless").strip()
            audio_variant = str(body.get("audioVariant") or "ver").strip()
            ok, msg, task = _start_novel_bundle_task(novel_id, audio_preset, audio_variant)
            if not ok or not task:
                code = 404 if "not found" in msg else 400
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": msg, "task": task})
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
            visual_style = str(body.get("visualStyle") or DEFAULT_VISUAL_STYLE).strip() or DEFAULT_VISUAL_STYLE
            if visual_style not in VISUAL_STYLE_OPTIONS:
                visual_style = DEFAULT_VISUAL_STYLE
            if not validate_english_dir(english_dir):
                self.send_json({"error": "invalid englishDir"}, 400)
                return
            conn = db_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO novels (name,author,english_dir,intro,prompt_id,nsfw_prompt_id,illustration_scene_prompt_id,illustration_shot_prompt_id,illustration_prompt_prompt_id,visual_style,workflow_id,voice_sample_workflow_id,line_audio_workflow_id,voice_transcribe_workflow_id,audio_asr_workflow_id,chapter_count,total_words)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)
                    """,
                    (
                        str(body.get("name") or "").strip(),
                        str(body.get("author") or "").strip(),
                        english_dir,
                        str(body.get("intro") or "").strip(),
                        int(body.get("promptId")) if body.get("promptId") else None,
                        int(body.get("nsfwPromptId")) if body.get("nsfwPromptId") else None,
                        int(body.get("illustrationScenePromptId"))
                        if body.get("illustrationScenePromptId")
                        else None,
                        int(body.get("illustrationShotPromptId"))
                        if body.get("illustrationShotPromptId")
                        else None,
                        int(body.get("illustrationPromptPromptId"))
                        if body.get("illustrationPromptPromptId")
                        else None,
                        visual_style,
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
                        int(body.get("audioAsrWorkflowId"))
                        if body.get("audioAsrWorkflowId")
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

        m_text_fix_search = re.match(r"^/api/novels/(\d+)/text-fix/search$", route)
        if m_text_fix_search:
            novel_id = int(m_text_fix_search.group(1))
            body = self.read_json()
            query = str(body.get("searchText") or "").strip()
            conn = db_conn()
            data = search_novel_text_occurrences(conn, novel_id, query)
            conn.close()
            self.send_json(data)
            return

        m_text_fix_replace = re.match(r"^/api/novels/(\d+)/text-fix/replace$", route)
        if m_text_fix_replace:
            novel_id = int(m_text_fix_replace.group(1))
            body = self.read_json()
            search_text = str(body.get("searchText") or "")
            replace_text = str(body.get("replaceText") or "")
            scope = str(body.get("scope") or "all")
            conn = db_conn()
            result = replace_novel_text_occurrences(
                conn, novel_id, search_text, replace_text, scope
            )
            if not result.get("ok"):
                conn.close()
                self.send_json(
                    {"error": str(result.get("error") or "replace failed")}, 400
                )
                return
            conn.commit()
            conn.close()
            self.send_json(result)
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
            if str(row["status"]) not in {"failed", "timeout"}:
                conn.close()
                self.send_json({"error": "only failed or timeout task can be retried"}, 409)
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

        m_cancel_json_task = re.match(r"^/api/json-tasks/(\d+)/cancel$", route)
        if m_cancel_json_task:
            task_id = int(m_cancel_json_task.group(1))
            ok, message = cancel_json_task(task_id)
            if not ok:
                code = 404 if message == "json task not found" else 409
                self.send_json({"error": message}, code)
                return
            self.send_json({"status": "ok"})
            return

        m_retry_json_batch = re.match(
            r"^/api/json-tasks/(\d+)/batches/(\d+)/retry$", route
        )
        if m_retry_json_batch:
            task_id = int(m_retry_json_batch.group(1))
            batch_index = int(m_retry_json_batch.group(2))
            ok, message = retry_json_task_batch(task_id, batch_index)
            if not ok:
                self.send_json({"error": message}, 409)
                return
            self.send_json({"status": "ok"})
            return

        m_retry_json_batch = re.match(
            r"^/api/json-tasks/(\d+)/batches/(\d+)/retry$", route
        )
        if m_retry_json_batch:
            task_id = int(m_retry_json_batch.group(1))
            batch_index = int(m_retry_json_batch.group(2))
            ok, message = retry_json_task_batch(task_id, batch_index)
            if not ok:
                self.send_json({"error": message}, 409)
                return
            self.send_json({"status": "ok"})
            return

        if route == "/api/json-tasks":
            body = self.read_json()
            conn = db_conn()
            settings = fetch_settings(conn)
            llm = settings.get("llm") or {}
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
                INSERT INTO json_tasks (novel_id,chapter_id,chapter_num,chapter_title,prompt_id,model_name,think_enabled,status,progress)
                VALUES (?,?,?,?,?,?,?, 'pending',0)
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
                    str(llm.get("model") or ""),
                    1 if bool(llm.get("think", True)) else 0,
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

        if route == "/api/task-worker/restart":
            restart_task_worker()
            self.send_json({"status": "ok"})
            return

        if route == "/api/line-audio-worker/restart":
            restart_line_audio_worker()
            self.send_json({"status": "ok"})
            return

        if route == "/api/audio-asr-worker/restart":
            restart_audio_asr_worker()
            self.send_json({"status": "ok"})
            return

        if route == "/api/nsfw-review-worker/restart":
            restart_nsfw_review_worker()
            self.send_json({"status": "ok"})
            return

        if route == "/api/illustration-worker/restart":
            restart_illustration_workers()
            self.send_json({"status": "ok"})
            return

        if route == "/api/illustration-llm-worker/restart":
            restart_illustration_worker()
            self.send_json({"status": "ok"})
            return

        if route == "/api/illustration-image-worker/restart":
            restart_illustration_image_worker()
            self.send_json({"status": "ok"})
            return

        if route == "/api/video-export-worker/restart":
            restart_video_export_worker()
            self.send_json({"status": "ok"})
            return

        m_video_export_enqueue = re.match(r"^/api/novels/(\d+)/chapters/(\d+)/video-export/enqueue$", route)
        if m_video_export_enqueue:
            novel_id = int(m_video_export_enqueue.group(1))
            chapter_num = int(m_video_export_enqueue.group(2))
            body = self.read_json()
            try:
                width = int(body.get("width") or 1080)
                height = int(body.get("height") or 1920)
                fps = int(body.get("fps") or 30)
            except (TypeError, ValueError):
                self.send_json({"error": "invalid video size"}, 400)
                return
            subtitle_mode = str(body.get("subtitleMode") or "srt").strip().lower()
            ok, msg, task_id = enqueue_video_export_task(novel_id, chapter_num, width=width, height=height, fps=fps, subtitle_mode=subtitle_mode)
            if not ok:
                self.send_json({"error": msg}, 400)
                return
            ensure_video_export_worker()
            self.send_json({"status": "ok", "message": msg, "taskId": task_id})
            return

        m_video_export_retry = re.match(r"^/api/video-export-tasks/(\d+)/retry$", route)
        if m_video_export_retry:
            ok, msg = retry_video_export_task(int(m_video_export_retry.group(1)))
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            ensure_video_export_worker()
            self.send_json({"status": "ok"})
            return

        m_video_export_cancel = re.match(r"^/api/video-export-tasks/(\d+)/cancel$", route)
        if m_video_export_cancel:
            ok, msg = cancel_video_export_task(int(m_video_export_cancel.group(1)))
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "ok"})
            return

        if route == "/api/prompts":
            body = self.read_json()
            conn = db_conn()
            try:
                conn.execute(
                    "INSERT INTO json_prompts (name,prompt_type,prompt_category,description,content) VALUES (?, 'user', ?, ?, ?)",
                    (
                        str(body.get("name") or ""),
                        str(body.get("category") or "json_parse"),
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
                "SELECT name,content,prompt_category,llm_config_json FROM json_prompts WHERE id=?", (prompt_id,)
            ).fetchone()
            if not src:
                conn.close()
                self.send_json({"error": "prompt not found"}, 404)
                return
            try:
                src_name = str(src["name"])
                new_name = next_prompt_copy_name(conn, src_name)
                conn.execute(
                    "INSERT INTO json_prompts (name,prompt_type,prompt_category,description,content,llm_config_json) VALUES (?, 'user', ?, ?, ?, ?)",
                    (
                        new_name,
                        str(src["prompt_category"] or "json_parse"),
                        f"基于 {src_name} 复制",
                        str(src["content"]),
                        str(src["llm_config_json"] or "{}"),
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
                    "audio_asr",
                    "illustration",
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
            settings = fetch_settings(conn)
            llm = settings.get("llm") or {}
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
                INSERT INTO json_tasks (novel_id,chapter_id,chapter_num,chapter_title,prompt_id,model_name,think_enabled,status,progress)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0)
                """,
                (
                    novel_id,
                    chapter_id,
                    chapter_num,
                    title,
                    int(novel["prompt_id"]),
                    str(llm.get("model") or ""),
                    1 if bool(llm.get("think", True)) else 0,
                ),
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

        m_role_alias = re.match(r"^/api/novels/(\d+)/roles/(\d+)/alias$", route)
        if m_role_alias and self.command == "POST":
            body = self.read_json()
            ok, msg, role = create_role_alias(
                int(m_role_alias.group(2)),
                str(body.get("aliasName") or ""),
            )
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": msg, "role": role})
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
            ensure_line_audio_worker()
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
            ensure_line_audio_worker()
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

        m_audio_asr_enqueue = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/audio-asr/enqueue$", route
        )
        if m_audio_asr_enqueue:
            ensure_audio_asr_worker()
            novel_id = int(m_audio_asr_enqueue.group(1))
            chapter_num = int(m_audio_asr_enqueue.group(2))
            body = self.read_json()
            force_extract = bool(body.get("forceExtract")) if isinstance(body, dict) else False
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            ok, msg, data = enqueue_chapter_audio_asr_task(
                novel_id,
                int(chapter_row["id"]),
                force_extract=force_extract,
            )
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": str((data or {}).get("action") or "queued"), "message": msg, **(data or {})})
            return

        m_audio_asr_repair_subtitle = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/audio-asr/repair-subtitle$", route
        )
        if m_audio_asr_repair_subtitle:
            novel_id = int(m_audio_asr_repair_subtitle.group(1))
            chapter_num = int(m_audio_asr_repair_subtitle.group(2))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            ok, msg, data = enqueue_chapter_audio_asr_subtitle_repair(novel_id, int(chapter_row["id"]))
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": str((data or {}).get("action") or "queued"), "message": msg, **(data or {})})
            return

        m_audio_asr_repair_subtitle_cancel = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/audio-asr/repair-subtitle/cancel$", route
        )
        if m_audio_asr_repair_subtitle_cancel:
            novel_id = int(m_audio_asr_repair_subtitle_cancel.group(1))
            chapter_num = int(m_audio_asr_repair_subtitle_cancel.group(2))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            ok, msg = cancel_chapter_audio_asr_subtitle_repair(novel_id, int(chapter_row["id"]))
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "cancelled"})
            return

        m_illustration_enqueue = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/illustration/(scene|shot|prompt)/enqueue$", route
        )
        if m_illustration_enqueue:
            ensure_illustration_worker()
            novel_id = int(m_illustration_enqueue.group(1))
            chapter_num = int(m_illustration_enqueue.group(2))
            stage = str(m_illustration_enqueue.group(3))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            payload = self.read_json()
            ok, msg = enqueue_illustration_task(novel_id, int(chapter_row["id"]), stage, allow_waiting=bool(payload.get("allowWaiting")))
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "queued"})
            return

        m_illustration_cancel_tasks = re.match(
            r"^/api/novels/(\d+)/illustration/cancel-pending-tasks$", route
        )
        if m_illustration_cancel_tasks:
            novel_id = int(m_illustration_cancel_tasks.group(1))
            self.send_json({"status": "cancelled", **cancel_pending_illustration_tasks(novel_id)})
            return

        m_illustration_cancel_images = re.match(
            r"^/api/novels/(\d+)/illustration/cancel-pending-images$", route
        )
        if m_illustration_cancel_images:
            novel_id = int(m_illustration_cancel_images.group(1))
            self.send_json({"status": "cancelled", **cancel_pending_illustration_images(novel_id)})
            return

        m_illustration_view = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/illustration/(scene|shot|prompt)/(input|output)$", route
        )
        if m_illustration_view:
            novel_id = int(m_illustration_view.group(1))
            chapter_num = int(m_illustration_view.group(2))
            stage = str(m_illustration_view.group(3))
            kind = str(m_illustration_view.group(4))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            payload = get_illustration_task_payload(novel_id, int(chapter_row["id"]), stage)
            self.send_json({
                "stage": stage,
                "kind": kind,
                "text": payload["inputText"] if kind == "input" else (payload["resultJsonText"] or payload["outputText"]),
                **payload,
            })
            return

        m_illustration_llm_params = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/illustration/(scene|shot|prompt)/llm-params$", route
        )
        if m_illustration_llm_params:
            novel_id = int(m_illustration_llm_params.group(1))
            chapter_num = int(m_illustration_llm_params.group(2))
            stage = str(m_illustration_llm_params.group(3))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            try:
                self.send_json(get_illustration_llm_request_preview(novel_id, int(chapter_row["id"]), stage))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
            return

        m_prompt_batches = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/illustration/(shot|prompt)/batches$", route
        )
        if m_prompt_batches:
            novel_id = int(m_prompt_batches.group(1))
            chapter_num = int(m_prompt_batches.group(2))
            stage = str(m_prompt_batches.group(3))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            self.send_json(list_prompt_batches(novel_id, int(chapter_row["id"]), stage))
            return

        m_prompt_batch_retry = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/illustration/(shot|prompt)/batches/(\d+)/retry$", route
        )
        if m_prompt_batch_retry:
            ensure_illustration_worker()
            novel_id = int(m_prompt_batch_retry.group(1))
            chapter_num = int(m_prompt_batch_retry.group(2))
            stage = str(m_prompt_batch_retry.group(3))
            batch_index = int(m_prompt_batch_retry.group(4))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            ok, msg, deleted_images = retry_prompt_batch(novel_id, int(chapter_row["id"]), batch_index, stage)
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "queued", "deletedImages": deleted_images})
            return

        m_prompt_output_save = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/illustration/prompt/output/save$", route
        )
        if m_prompt_output_save:
            novel_id = int(m_prompt_output_save.group(1))
            chapter_num = int(m_prompt_output_save.group(2))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            payload = self.read_json()
            try:
                data = save_illustration_prompt_output(novel_id, int(chapter_row["id"]), str(payload.get("jsonText") or ""))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json({"status": "saved", **data})
            return

        m_prompt_item_save = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/illustration/prompt/items/(\d+)/save$", route
        )
        if m_prompt_item_save:
            novel_id = int(m_prompt_item_save.group(1))
            chapter_num = int(m_prompt_item_save.group(2))
            item_index = int(m_prompt_item_save.group(3))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            payload = self.read_json()
            try:
                data = save_illustration_prompt_item(novel_id, int(chapter_row["id"]), item_index, str(payload.get("jsonText") or ""))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json({"status": "saved", **data})
            return

        m_scene_output_save = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/illustration/scene/output/save$", route
        )
        if m_scene_output_save:
            novel_id = int(m_scene_output_save.group(1))
            chapter_num = int(m_scene_output_save.group(2))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            payload = self.read_json()
            try:
                data = save_illustration_scene_output(novel_id, int(chapter_row["id"]), str(payload.get("jsonText") or ""))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json({"status": "saved", **data})
            return

        m_illustration_images = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/illustration/images$", route
        )
        if m_illustration_images:
            novel_id = int(m_illustration_images.group(1))
            chapter_num = int(m_illustration_images.group(2))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            self.send_json({"images": sync_prompt_images(novel_id, int(chapter_row["id"]))})
            return

        m_illustration_image_enqueue = re.match(r"^/api/illustration-images/(\d+)/enqueue$", route)
        if m_illustration_image_enqueue:
            ensure_illustration_image_worker()
            image_id = int(m_illustration_image_enqueue.group(1))
            ok, msg = enqueue_illustration_image(image_id)
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "queued"})
            return

        m_illustration_image_optimize = re.match(r"^/api/illustration-images/(\d+)/prompt/optimize$", route)
        if m_illustration_image_optimize:
            image_id = int(m_illustration_image_optimize.group(1))
            payload = self.read_json()
            try:
                data = optimize_illustration_prompt_item(image_id, str(payload.get("jsonText") or ""))
            except Exception as exc:
                detail = getattr(exc, "detail", None)
                if isinstance(detail, dict):
                    self.send_json({"error": str(exc), **detail}, 400)
                else:
                    self.send_json({"error": str(exc)}, 400)
                return
            self.send_json({"status": "optimized", **data})
            return

        m_illustration_image_optimize_prepare = re.match(r"^/api/illustration-images/(\d+)/prompt/optimize/prepare$", route)
        if m_illustration_image_optimize_prepare:
            image_id = int(m_illustration_image_optimize_prepare.group(1))
            payload = self.read_json()
            try:
                data = prepare_illustration_prompt_item_optimization(image_id, str(payload.get("jsonText") or ""))
            except Exception as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            self.send_json({"status": "prepared", **data})
            return

        m_illustration_image_original = re.match(r"^/api/illustration-images/(\d+)/prompt/original$", route)
        if m_illustration_image_original:
            image_id = int(m_illustration_image_original.group(1))
            try:
                data = get_illustration_prompt_item_original(image_id)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 404)
                return
            self.send_json({"status": "ok", **data})
            return

        m_illustration_images_enqueue_all = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/illustration/images/enqueue-all$", route
        )
        if m_illustration_images_enqueue_all:
            ensure_illustration_image_worker()
            novel_id = int(m_illustration_images_enqueue_all.group(1))
            chapter_num = int(m_illustration_images_enqueue_all.group(2))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            self.send_json({"status": "queued", **enqueue_all_illustration_images(novel_id, int(chapter_row["id"]))})
            return

        m_audio_asr_cancel = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/audio-asr/cancel$", route
        )
        if m_audio_asr_cancel:
            ensure_audio_asr_worker()
            novel_id = int(m_audio_asr_cancel.group(1))
            chapter_num = int(m_audio_asr_cancel.group(2))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            ok, msg = cancel_chapter_audio_asr_task(novel_id, int(chapter_row["id"]))
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "cancelled"})
            return

        m_audio_asr_enqueue_batch = re.match(r"^/api/novels/(\d+)/audio-asr/enqueue-batch$", route)
        if m_audio_asr_enqueue_batch:
            ensure_audio_asr_worker()
            novel_id = int(m_audio_asr_enqueue_batch.group(1))
            body = self.read_json()
            raw_nums = body.get("chapterNums") or []
            force_extract = bool(body.get("forceExtract")) if isinstance(body, dict) else False
            chapter_nums: list[int] = []
            if isinstance(raw_nums, list):
                for item in raw_nums:
                    try:
                        value = int(item)
                    except (TypeError, ValueError):
                        continue
                    if value > 0:
                        chapter_nums.append(value)
            ok, msg, data = enqueue_batch_audio_asr_tasks(
                novel_id,
                chapter_nums or None,
                force_extract=force_extract,
            )
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "queued", **data})
            return

        m_nsfw_enqueue = re.match(r"^/api/novels/(\d+)/chapters/(\d+)/nsfw-review/enqueue$", route)
        if m_nsfw_enqueue:
            ensure_nsfw_review_worker()
            novel_id = int(m_nsfw_enqueue.group(1))
            chapter_num = int(m_nsfw_enqueue.group(2))
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            ok, msg = enqueue_chapter_nsfw_review_task(novel_id, int(chapter_row["id"]))
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "queued"})
            return

        m_nsfw_enqueue_batch = re.match(r"^/api/novels/(\d+)/nsfw-review/enqueue-batch$", route)
        if m_nsfw_enqueue_batch:
            ensure_nsfw_review_worker()
            novel_id = int(m_nsfw_enqueue_batch.group(1))
            body = self.read_json()
            raw_nums = body.get("chapterNums") or []
            chapter_nums: list[int] = []
            if isinstance(raw_nums, list):
                for item in raw_nums:
                    try:
                        value = int(item)
                    except (TypeError, ValueError):
                        continue
                    if value > 0:
                        chapter_nums.append(value)
            ok, msg, data = enqueue_batch_nsfw_review_tasks(novel_id, chapter_nums or None)
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": "queued", **data})
            return

        m_retry_line_task = re.match(r"^/api/line-audio-tasks/(\d+)/retry$", route)
        if m_retry_line_task:
            ensure_line_audio_worker()
            task_id = int(m_retry_line_task.group(1))
            ok, msg = retry_line_audio_task(task_id)
            if not ok:
                code = 404 if "不存在" in msg or "not found" in msg else 409
                self.send_json({"error": msg}, code)
                return
            kick_line_audio_queue_once()
            self.send_json({"status": msg})
            return

        m_prioritize_line_task = re.match(r"^/api/line-audio-tasks/(\d+)/prioritize$", route)
        if m_prioritize_line_task:
            ensure_line_audio_worker()
            task_id = int(m_prioritize_line_task.group(1))
            ok, msg = prioritize_line_audio_task(task_id)
            if not ok:
                code = 404 if "不存在" in msg or "not found" in msg else 409
                self.send_json({"error": msg}, code)
                return
            kick_line_audio_queue_once()
            self.send_json({"status": msg})
            return

        m_edit_line_audio = re.match(r"^/api/line-audio-tasks/(\d+)/edit-audio$", route)
        if m_edit_line_audio:
            task_id = int(m_edit_line_audio.group(1))
            body = self.read_json()
            try:
                start_seconds = float(body.get("startSeconds") or 0)
                end_seconds = float(body.get("endSeconds") or 0)
                volume_factor = float(body.get("volumeFactor") or 1)
                speed_factor = float(body.get("speedFactor") or 1)
            except (TypeError, ValueError):
                self.send_json({"error": "invalid audio range"}, 400)
                return
            ok, msg, data = edit_line_audio_task_audio(
                task_id,
                mode=str(body.get("mode") or "keep"),
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                volume_factor=volume_factor,
                speed_factor=speed_factor,
                segments=body.get("segments") if isinstance(body.get("segments"), list) else None,
                collect_training_samples=body.get("collectTrainingSamples") is not False,
            )
            if not ok:
                code = 404 if "不存在" in msg or "not found" in msg else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": msg, **data})
            return

        m_false_positive_noise = re.match(r"^/api/line-audio-tasks/(\d+)/noise/false-positive$", route)
        if m_false_positive_noise:
            task_id = int(m_false_positive_noise.group(1))
            body = self.read_json()
            ok, msg, data = record_line_audio_noise_false_positive(
                task_id,
                body.get("segments") if isinstance(body.get("segments"), list) else [],
            )
            if not ok:
                code = 404 if "不存在" in msg or "not found" in msg else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": msg, **data})
            return

        m_edit_noise_sample = re.match(r"^/api/line-audio-noise-samples/(manual-abnormal|abnormal|false-positive-normal)/files/(.+)/edit$", route)
        if m_edit_noise_sample:
            sample_path = _resolve_line_audio_noise_sample(m_edit_noise_sample.group(1), unquote(m_edit_noise_sample.group(2)))
            if sample_path is None or not sample_path.exists() or not sample_path.is_file():
                self.send_json({"error": "sample not found"}, 404)
                return
            body = self.read_json()
            ok, msg, data = _edit_manual_abnormal_sample(sample_path, body)
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            self.send_json({"status": msg, **data})
            return

        m_replace_line_audio = re.match(r"^/api/line-audio-tasks/(\d+)/replace-matching$", route)
        if m_replace_line_audio:
            task_id = int(m_replace_line_audio.group(1))
            ok, msg, data = replace_matching_line_audio_tasks(task_id)
            if not ok:
                code = 404 if "不存在" in msg or "not found" in msg else 409
                self.send_json({"error": msg}, code)
                return
            self.send_json({"status": msg, **data})
            return

        m_merge_audio = re.match(
            r"^/api/novels/(\d+)/chapters/(\d+)/merge-line-audio$", route
        )
        if m_merge_audio:
            novel_id = int(m_merge_audio.group(1))
            chapter_num = int(m_merge_audio.group(2))
            body = self.read_json()
            variant = _normalize_bundle_audio_variant((body or {}).get("variant") if isinstance(body, dict) else "ver")
            conn = db_conn()
            chapter_row = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            conn.close()
            if not chapter_row:
                self.send_json({"error": "chapter not found"}, 404)
                return
            ok, msg, path = merge_chapter_line_audio(
                novel_id,
                int(chapter_row["id"]),
                include_copyright=(variant == "ver"),
            )
            if not ok:
                self.send_json({"error": msg}, 409)
                return
            if path and variant == "ver":
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
            self.send_json({"status": "merged", "path": path, "variant": variant})
            return

        self.send_json({"error": "not found"}, 404)

    def do_PUT(self) -> None:
        self._reset_request_body_state()
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

        m_save_corrected_srt = re.match(r"^/api/novels/(\d+)/chapters/(\d+)/corrected-srt-file$", route)
        if m_save_corrected_srt:
            novel_id = int(m_save_corrected_srt.group(1))
            chapter_num = int(m_save_corrected_srt.group(2))
            srt_text = str(body.get("srtText") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            if not srt_text:
                self.send_json({"error": "srtText is required"}, 400)
                return
            conn = db_conn()
            chapter = conn.execute(
                "SELECT id FROM chapters WHERE novel_id=? AND chapter_num=?",
                (novel_id, chapter_num),
            ).fetchone()
            if not chapter:
                conn.close()
                self.send_json({"error": "chapter not found"}, 404)
                return
            task = conn.execute(
                "SELECT id,corrected_srt_file_path FROM chapter_asr_tasks WHERE novel_id=? AND chapter_id=? ORDER BY id DESC LIMIT 1",
                (novel_id, int(chapter["id"])),
            ).fetchone()
            rel = str(task["corrected_srt_file_path"] or "").strip() if task else ""
            file_path = (ROOT_DIR / rel).resolve() if rel else None
            root_dir = ROOT_DIR.resolve()
            if not task or not file_path or not file_path.exists() or not file_path.is_file():
                conn.close()
                self.send_json({"error": "corrected srt file not found"}, 404)
                return
            try:
                file_path.relative_to(root_dir)
            except ValueError:
                conn.close()
                self.send_json({"error": "invalid srt file path"}, 400)
                return
            file_path.write_text(srt_text + "\n", encoding="utf-8")
            conn.execute(
                "UPDATE chapter_asr_tasks SET subtitle_fixed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (int(task["id"]),),
            )
            conn.commit()
            conn.close()
            errors = inspect_srt_timing_errors(srt_text)
            self.send_json({
                "status": "saved",
                "errorCount": len(errors),
                "errorLines": [int(error["line"] or 0) for error in errors],
                "errors": errors,
            })
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
            visual_style = str(body.get("visualStyle") or DEFAULT_VISUAL_STYLE).strip() or DEFAULT_VISUAL_STYLE
            if visual_style not in VISUAL_STYLE_OPTIONS:
                visual_style = DEFAULT_VISUAL_STYLE
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
                    SET name=?,author=?,english_dir=?,intro=?,prompt_id=?,nsfw_prompt_id=?,illustration_scene_prompt_id=?,illustration_shot_prompt_id=?,illustration_prompt_prompt_id=?,visual_style=?,workflow_id=?,voice_sample_workflow_id=?,line_audio_workflow_id=?,voice_transcribe_workflow_id=?,audio_asr_workflow_id=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        str(body.get("name") or "").strip(),
                        str(body.get("author") or "").strip(),
                        english_dir,
                        str(body.get("intro") or "").strip(),
                        int(body.get("promptId")) if body.get("promptId") else None,
                        int(body.get("nsfwPromptId")) if body.get("nsfwPromptId") else None,
                        int(body.get("illustrationScenePromptId"))
                        if body.get("illustrationScenePromptId")
                        else None,
                        int(body.get("illustrationShotPromptId"))
                        if body.get("illustrationShotPromptId")
                        else None,
                        int(body.get("illustrationPromptPromptId"))
                        if body.get("illustrationPromptPromptId")
                        else None,
                        visual_style,
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
                        int(body.get("audioAsrWorkflowId"))
                        if body.get("audioAsrWorkflowId")
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
                "UPDATE json_prompts SET name=?,prompt_category=?,description=?,content=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (
                    str(body.get("name") or ""),
                    str(body.get("category") or "json_parse"),
                    str(body.get("description") or ""),
                    str(body.get("content") or ""),
                    prompt_id,
                ),
            )
            conn.commit()
            conn.close()
            self.send_json({"status": "ok"})
            return

        m_prompt_settings = re.match(r"^/api/prompts/(\d+)/settings$", route)
        if m_prompt_settings:
            prompt_id = int(m_prompt_settings.group(1))
            conn = db_conn()
            row = conn.execute(
                "SELECT id FROM json_prompts WHERE id=?", (prompt_id,)
            ).fetchone()
            if not row:
                conn.close()
                self.send_json({"error": "prompt not found"}, 404)
                return
            from .services import normalize_prompt_llm_settings
            settings = normalize_prompt_llm_settings(body)
            conn.execute(
                "UPDATE json_prompts SET llm_config_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(settings, ensure_ascii=False), prompt_id),
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
                "SELECT workflow_type, name, workflow_log_enabled FROM comfy_workflows WHERE id=?",
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
            if "workflowLogEnabled" in body:
                workflow_log_enabled = 1 if body.get("workflowLogEnabled") else 0
            else:
                workflow_log_enabled = int(row["workflow_log_enabled"] or 0)
            workflow_type = str(
                body.get("workflowType") or row["workflow_type"] or ""
            ).strip()
            if workflow_type not in {
                "voice_sample",
                "line_audio",
                "voice_transcribe",
                "audio_asr",
                "illustration",
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
            if batch_max_chars not in {0, 3500, 4000, 5000, 6000, 7000, 8000, 9000, 10000}:
                batch_max_chars = 3500
            raw_max_tokens = llm.get("maxTokens", 8192)
            if raw_max_tokens in (None, ""):
                raw_max_tokens = 8192
            try:
                llm_max_tokens = int(raw_max_tokens)
            except (TypeError, ValueError):
                llm_max_tokens = 8192
            llm_provider = str(llm.get("provider") or "grok")
            llm_max_tokens = normalize_llm_max_tokens(llm_provider, llm_max_tokens)
            raw_batch_timeout_minutes = llm.get("batchTimeoutMinutes", 15)
            if raw_batch_timeout_minutes in (None, ""):
                raw_batch_timeout_minutes = 15
            try:
                batch_timeout_minutes = int(raw_batch_timeout_minutes)
            except (TypeError, ValueError):
                batch_timeout_minutes = 15
            if batch_timeout_minutes not in {5, 10, 15, 20, 30, 40}:
                batch_timeout_minutes = 15
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

            video_cover_logo = body.get("videoCoverLogo") or {}
            if not isinstance(video_cover_logo, dict):
                video_cover_logo = {}
            video_cover_logo_path = str(video_cover_logo.get("path") or "").strip()
            if video_cover_logo_path:
                logo_abs = (ROOT_DIR / video_cover_logo_path).resolve()
                allowed_logo_dir = (ROOT_DIR / "temp" / "settings").resolve()
                try:
                    logo_abs.relative_to(allowed_logo_dir)
                except ValueError:
                    video_cover_logo_path = ""

            pairs = {
                "comfy_url": str(body.get("comfyUrl") or ""),
                "proxy_enabled": "1" if bool(body.get("proxyEnabled", False)) else "0",
                "proxy_url": str(body.get("proxyUrl") or ""),
                "llm_provider": llm_provider,
                "llm_base_url": str(llm.get("baseUrl") or ""),
                "llm_model": str(llm.get("model") or ""),
                "llm_api_key": str(llm.get("apiKey") or ""),
                "llm_temperature": str(llm.get("temperature") or 0.3),
                "llm_max_tokens": str(llm_max_tokens),
                "llm_num_ctx": str(llm.get("numCtx") or 65536),
                "llm_keep_alive": str(llm.get("keepAlive") or "30m"),
                "llm_unload_after_call": "1"
                if bool(llm.get("unloadAfterCall", False))
                else "0",
                "llm_batch_timeout_minutes": str(batch_timeout_minutes),
                "llm_think": "1" if bool(llm.get("think", True)) else "0",
                "llm_batch_max_chars": str(batch_max_chars),
                "ui_language": ui_language,
                "ui_timezone": ui_timezone,
                "line_audio_queue_mode": line_audio_queue_mode,
                "line_audio_queue_scheduled_at": line_audio_queue_scheduled_at,
                "copyright_audio_intro_enabled": "1"
                if (body.get("copyrightAudio") or {}).get("introEnabled")
                else "0",
                "copyright_audio_intro_path": str(
                    ((body.get("copyrightAudio") or {}).get("introPath") or "")
                ).strip(),
                "copyright_audio_outro_enabled": "1"
                if (body.get("copyrightAudio") or {}).get("outroEnabled")
                else "0",
                "copyright_audio_outro_path": str(
                    ((body.get("copyrightAudio") or {}).get("outroPath") or "")
                ).strip(),
                "live_ending_audio_items": json.dumps(
                    [
                        {
                            "label": str(item.get("label") or "直播结束语").strip() or "直播结束语",
                            "path": str(item.get("path") or "").strip(),
                        }
                        for item in ((body.get("liveEndingAudio") or {}).get("items") or [])
                        if str(item.get("path") or "").strip()
                    ],
                    ensure_ascii=False,
                ),
                "live_ending_audio_path": "",
                "video_cover_logo_enabled": "1" if bool(video_cover_logo.get("enabled")) else "0",
                "video_cover_logo_path": video_cover_logo_path,
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
        self._reset_request_body_state()
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
            ensure_line_audio_worker()
            task_id = int(m_delete_line_task.group(1))
            ok, msg = delete_line_audio_task(task_id)
            if not ok:
                code = 404 if "not found" in msg else 409
                self.send_json({"error": msg}, code)
                return
            kick_line_audio_queue_once()
            self.send_json({"status": "deleted"})
            return

        m_delete_noise_sample = re.match(r"^/api/line-audio-noise-samples/(manual-abnormal|abnormal|false-positive-normal)/files/(.+)$", route)
        if m_delete_noise_sample:
            sample_path = _resolve_line_audio_noise_sample(m_delete_noise_sample.group(1), unquote(m_delete_noise_sample.group(2)))
            if sample_path is None or not sample_path.exists() or not sample_path.is_file():
                self.send_json({"error": "sample not found"}, 404)
                return
            try:
                sample_path.unlink()
            except OSError as exc:
                self.send_json({"error": f"delete failed: {exc}"}, 409)
                return
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
