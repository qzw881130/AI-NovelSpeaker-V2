from __future__ import annotations

import json
import math
import os
import random
import re
import signal
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .app_context import NOVEL_DIR, ROOT_DIR, db_conn


DEFAULT_VIDEO_WIDTH = 1080
DEFAULT_VIDEO_HEIGHT = 1920
DEFAULT_VIDEO_FPS = 30
FADE_SECONDS = 1.0


@dataclass
class SubtitleSegment:
    start: float
    end: float
    text: str


@dataclass
class SceneItem:
    index: int
    start: float
    end: float
    image_path: Path
    title: str
    motion: dict


def _resolve_path(raw_path: str) -> Path | None:
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


def _safe_filename(value: str, fallback: str = "chapter") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return text or fallback


def _parse_timestamp(value: str) -> float | None:
    text = str(value or "").strip().replace(",", ".")
    match = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?$", text)
    if match:
        hh, mm, ss, ms = match.groups()
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int((ms or "0").ljust(3, "0")[:3]) / 1000
    try:
        return float(text)
    except ValueError:
        return None


def _parse_json_any(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    return parsed if isinstance(parsed, dict) else {}


def _load_scene_meta(conn, novel_id: int, chapter_id: int) -> dict[int, tuple[float, float]]:
    row = conn.execute(
        """
        SELECT result_json_text
        FROM chapter_illustration_tasks
        WHERE novel_id=? AND chapter_id=? AND stage='scene' AND status='completed'
        ORDER BY id DESC LIMIT 1
        """,
        (novel_id, chapter_id),
    ).fetchone()
    if not row or not str(row["result_json_text"] or "").strip():
        return {}
    try:
        parsed = _parse_json_any(str(row["result_json_text"] or ""))
    except Exception:
        return {}
    grid = parsed.get("grid") if isinstance(parsed, dict) else []
    if not isinstance(grid, list):
        return {}
    meta: dict[int, tuple[float, float]] = {}
    for pos, item in enumerate(grid, start=1):
        if not isinstance(item, dict):
            continue
        start = _parse_timestamp(str(item.get("start") or ""))
        end = _parse_timestamp(str(item.get("end") or ""))
        if start is None or end is None or end <= start:
            continue
        try:
            index = int(item.get("index") or pos)
        except (TypeError, ValueError):
            index = pos
        meta[index] = (start, end)
    return meta


def parse_asr_segments(raw_text: str) -> list[SubtitleSegment]:
    text = str(raw_text or "").replace("\r", "").strip()
    if not text:
        return []
    segments: list[SubtitleSegment] = []
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if len(lines) < 2:
            continue
        time_line = next((line for line in lines if "-->" in line), "")
        if not time_line:
            continue
        time_index = lines.index(time_line)
        match = re.match(r"^(.*?)\s*-->\s*(.*?)$", time_line)
        if not match:
            continue
        start = _parse_timestamp(match.group(1))
        end = _parse_timestamp(match.group(2))
        body = "".join(lines[time_index + 1 :]).strip()
        if start is None or end is None or end <= start or not body:
            continue
        segments.append(SubtitleSegment(start=start, end=end, text=body))
    if segments:
        return segments

    for line in text.split("\n"):
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) < 3:
            continue
        start = _parse_timestamp(parts[-2])
        end = _parse_timestamp(parts[-1])
        body = "".join(parts[:-2]).strip()
        if start is None or end is None or end <= start or not body:
            continue
        segments.append(SubtitleSegment(start=start, end=end, text=body))
    return _merge_subtitle_segments(segments)


def _merge_subtitle_segments(segments: list[SubtitleSegment]) -> list[SubtitleSegment]:
    merged: list[SubtitleSegment] = []
    current: SubtitleSegment | None = None
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        if current is None:
            current = SubtitleSegment(segment.start, segment.end, text)
            continue
        gap = max(0.0, segment.start - current.end)
        current_len = len(re.sub(r"\s+", "", current.text))
        duration = max(0.0, segment.end - segment.start)
        if gap <= 0.8 and (duration <= 1.4 or len(text) <= 8 or current_len <= 18):
            current.text = f"{current.text}{text}"
            current.end = segment.end
        else:
            merged.append(current)
            current = SubtitleSegment(segment.start, segment.end, text)
    if current:
        merged.append(current)
    return merged


def _read_text_file(path: Path | None) -> str:
    if not path or not path.exists() or not path.is_file():
        return ""
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="ignore")


def _load_font(size: int):
    from PIL import ImageFont

    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _text_width(draw, text: str, font) -> int:
    try:
        return int(draw.textlength(text, font=font))
    except Exception:
        bbox = draw.textbbox((0, 0), text, font=font)
        return int(bbox[2] - bbox[0])


def _wrap_text(draw, text: str, font, max_width: int, max_lines: int = 2) -> list[str]:
    chars = list(str(text or "").strip())
    lines: list[str] = []
    current = ""
    for char in chars:
        candidate = f"{current}{char}"
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines[:max_lines] or [""]


def _draw_text_with_shadow(draw, xy: tuple[int, int], text: str, font, fill: tuple[int, int, int], shadow=(0, 0, 0)) -> None:
    x, y = xy
    for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2), (0, 2), (2, 0)):
        draw.text((x + dx, y + dy), text, fill=shadow, font=font)
    draw.text((x, y), text, fill=fill, font=font)


def _motion_for_index(index: int) -> dict:
    rng = random.Random(index * 7919 + 17)
    templates = [
        (1.04, 1.10, -0.02, 0.02, -0.01, 0.01),
        (1.10, 1.04, 0.02, -0.02, 0.01, -0.01),
        (1.08, 1.08, -0.025, 0.025, -0.01, -0.01),
        (1.08, 1.08, 0.025, -0.025, 0.01, 0.01),
        (1.06, 1.11, -0.02, 0.01, 0.02, -0.015),
        (1.11, 1.06, 0.015, -0.02, -0.02, 0.015),
    ]
    tpl = templates[index % len(templates)]
    jitter = lambda value: value + rng.uniform(-0.004, 0.004)
    return {
        "scale_start": max(1.03, jitter(tpl[0])),
        "scale_end": min(1.12, jitter(tpl[1])),
        "x_start": jitter(tpl[2]),
        "x_end": jitter(tpl[3]),
        "y_start": jitter(tpl[4]),
        "y_end": jitter(tpl[5]),
    }


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _load_video_context(task_id: int) -> dict:
    conn = db_conn()
    task = conn.execute(
        """
        SELECT t.*, n.english_dir, n.name AS novel_name, c.text_file_path, c.audio_file_path, c.audio_duration_seconds
        FROM chapter_video_export_tasks t
        JOIN novels n ON n.id=t.novel_id
        JOIN chapters c ON c.id=t.chapter_id
        WHERE t.id=?
        """,
        (task_id,),
    ).fetchone()
    if not task:
        conn.close()
        raise RuntimeError("video export task not found")
    asr_row = conn.execute(
        """
        SELECT asr_file_path,timestamps_text,extracted_text,corrected_srt_file_path
        FROM chapter_asr_tasks
        WHERE novel_id=? AND chapter_id=? AND status='completed'
        ORDER BY id DESC LIMIT 1
        """,
        (int(task["novel_id"]), int(task["chapter_id"])),
    ).fetchone()
    image_rows = conn.execute(
        """
        SELECT item_index,scene_title,cn_summary,image_file_path
        FROM chapter_illustration_images
        WHERE novel_id=? AND chapter_id=? AND status='completed' AND COALESCE(image_file_path,'')<>''
        ORDER BY item_index ASC
        """,
        (int(task["novel_id"]), int(task["chapter_id"])),
    ).fetchall()
    scene_meta = _load_scene_meta(conn, int(task["novel_id"]), int(task["chapter_id"]))
    conn.close()

    audio_path = _resolve_path(str(task["audio_file_path"] or ""))
    if not audio_path or not audio_path.exists():
        raise RuntimeError("缺少章回音频")
    subtitle_mode = str(task["subtitle_mode"] or "srt").strip() or "srt"
    asr_text = ""
    if subtitle_mode != "none" and asr_row:
        corrected_srt_file = _resolve_path(str(asr_row["corrected_srt_file_path"] or ""))
        asr_text = _read_text_file(corrected_srt_file)
    subtitles = parse_asr_segments(asr_text)
    if subtitle_mode != "none" and not subtitles:
        raise RuntimeError("缺少SRT字幕文件")
    duration = float(task["duration_seconds"] or task["audio_duration_seconds"] or 0)
    if duration <= 0:
        duration = max((segment.end for segment in subtitles), default=0)
    if duration <= 0:
        raise RuntimeError("缺少音频时长")

    raw_images = []
    for row in image_rows:
        image_path = _resolve_path(str(row["image_file_path"] or ""))
        if not image_path or not image_path.exists():
            raise RuntimeError(f"插画文件不存在：第 {int(row['item_index'])} 张")
        raw_images.append(
            {
                "index": int(row["item_index"] or 0),
                "title": str(row["scene_title"] or row["cn_summary"] or "插画"),
                "image_path": image_path,
            }
        )
    if not raw_images:
        raise RuntimeError("缺少插画图片")

    scenes: list[SceneItem] = []
    count = len(raw_images)
    for pos, item in enumerate(raw_images):
        meta = scene_meta.get(int(item["index"]))
        if meta:
            start = max(0.0, min(duration, float(meta[0])))
            end = max(start + 0.1, min(duration, float(meta[1])))
        else:
            start = duration * pos / count
            end = duration * (pos + 1) / count
        scenes.append(
            SceneItem(
                index=int(item["index"]),
                start=start,
                end=end,
                image_path=item["image_path"],
                title=str(item["title"]),
                motion=_motion_for_index(pos + int(item["index"])),
            )
        )
    scenes.sort(key=lambda scene: (scene.start, scene.index))
    previous_end = 0.0
    for scene in scenes:
        if scene.start > previous_end:
            scene.start = previous_end
        if scene.end <= scene.start:
            scene.end = min(duration, scene.start + max(0.1, duration / count))
        previous_end = scene.end
    if scenes and scenes[-1].end < duration:
        scenes[-1].end = duration
    return {
        "task": task,
        "audio_path": audio_path,
        "subtitles": subtitles,
        "scenes": scenes,
        "duration": duration,
        "output_dir": NOVEL_DIR / str(task["english_dir"] or "") / "video",
    }


class FrameRenderer:
    def __init__(self, width: int, height: int, scenes: list[SceneItem], subtitles: list[SubtitleSegment]):
        from PIL import Image

        self.Image = Image
        self.width = width
        self.height = height
        self.is_landscape = width > height
        self.image_h = height if self.is_landscape else int(height * 0.72)
        self.subtitle_y = self.image_h
        self.scenes = scenes
        self.subtitles = subtitles
        self.font = _load_font(54)
        self.small_font = _load_font(34)
        self.image_cache: dict[str, object] = {}

    def _scene_at(self, t: float) -> tuple[int, SceneItem]:
        for idx, scene in enumerate(self.scenes):
            if scene.start <= t < scene.end:
                return idx, scene
        return len(self.scenes) - 1, self.scenes[-1]

    def _subtitle_at(self, t: float) -> SubtitleSegment | None:
        current = None
        for segment in self.subtitles:
            if segment.start <= t <= segment.end:
                return segment
            if segment.start <= t:
                current = segment
        return current

    def _image_for_scene(self, scene: SceneItem):
        key = str(scene.image_path)
        if key not in self.image_cache:
            img = self.Image.open(scene.image_path).convert("RGB")
            self.image_cache[key] = img
        return self.image_cache[key]

    def _render_scene_image(self, scene: SceneItem, t: float):
        src = self._image_for_scene(scene)
        duration = max(0.1, scene.end - scene.start)
        p = max(0.0, min(1.0, (t - scene.start) / duration))
        motion = scene.motion
        scale = _lerp(motion["scale_start"], motion["scale_end"], p)
        x_shift = _lerp(motion["x_start"], motion["x_end"], p)
        y_shift = _lerp(motion["y_start"], motion["y_end"], p)
        src_w, src_h = src.size
        target_w = int(self.width * scale)
        target_h = int(self.image_h * scale)
        cover_scale = max(target_w / src_w, target_h / src_h)
        resized = src.resize((max(1, int(src_w * cover_scale)), max(1, int(src_h * cover_scale))), self.Image.Resampling.LANCZOS)
        left = int((resized.width - self.width) / 2 + x_shift * self.width)
        top = int((resized.height - self.image_h) / 2 + y_shift * self.image_h)
        left = max(0, min(max(0, resized.width - self.width), left))
        top = max(0, min(max(0, resized.height - self.image_h), top))
        return resized.crop((left, top, left + self.width, top + self.image_h))

    def render(self, t: float):
        from PIL import ImageDraw

        frame = self.Image.new("RGB", (self.width, self.height), (18, 15, 12))
        idx, scene = self._scene_at(t)
        scene_img = self._render_scene_image(scene, t)
        if idx > 0 and t - scene.start < FADE_SECONDS:
            prev = self.scenes[idx - 1]
            prev_img = self._render_scene_image(prev, min(prev.end, t))
            alpha = max(0.0, min(1.0, (t - scene.start) / FADE_SECONDS))
            scene_img = self.Image.blend(prev_img, scene_img, alpha)
        frame.paste(scene_img, (0, 0))

        if self.is_landscape:
            draw = ImageDraw.Draw(frame)
            subtitle = self._subtitle_at(t)
            progress_y1 = self.height - 34
            progress_y2 = self.height - 26
            if subtitle:
                lines = _wrap_text(draw, subtitle.text, self.font, self.width - 96, max_lines=2)
                line_height = 72
                title_y = max(24, progress_y1 - len(lines) * line_height - 56)
                _draw_text_with_shadow(draw, (48, title_y), scene.title or f"第 {scene.index} 张", self.small_font, (248, 226, 190))
                y = title_y + 42
                for line in lines:
                    _draw_text_with_shadow(draw, (48, y), line, self.font, (255, 250, 238))
                    y += line_height
                if subtitle.end > subtitle.start:
                    p = max(0.0, min(1.0, (t - subtitle.start) / (subtitle.end - subtitle.start)))
                    bar_w = int((self.width - 96) * p)
                    draw.rounded_rectangle((48, progress_y1, 48 + bar_w, progress_y2), radius=4, fill=(229, 124, 48))
            else:
                _draw_text_with_shadow(draw, (48, self.height - 86), scene.title or f"第 {scene.index} 张", self.small_font, (248, 226, 190))
            return frame

        overlay = self.Image.new("RGBA", (self.width, self.height - self.subtitle_y), (20, 14, 10, 220))
        frame.paste(overlay.convert("RGB"), (0, self.subtitle_y))
        draw = ImageDraw.Draw(frame)
        subtitle = self._subtitle_at(t)
        title = scene.title or f"第 {scene.index} 张"
        draw.text((48, self.subtitle_y + 36), title, fill=(223, 199, 162), font=self.small_font)
        if subtitle:
            max_width = self.width - 96
            lines = _wrap_text(draw, subtitle.text, self.font, max_width, max_lines=2)
            y = self.subtitle_y + 118
            for line in lines:
                draw.text((48, y), line, fill=(255, 248, 232), font=self.font)
                y += 74
            if subtitle.end > subtitle.start:
                p = max(0.0, min(1.0, (t - subtitle.start) / (subtitle.end - subtitle.start)))
                bar_w = int((self.width - 96) * p)
                draw.rounded_rectangle((48, self.height - 88, 48 + bar_w, self.height - 76), radius=6, fill=(181, 92, 42))
        return frame


def process_video_export_task(task_id: int, progress_callback=None) -> None:
    conn = db_conn()
    row = conn.execute("SELECT status FROM chapter_video_export_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        conn.close()
        return
    conn.execute(
        "UPDATE chapter_video_export_tasks SET status='running',progress=1,error_message='',started_at=COALESCE(started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()

    try:
        ctx = _load_video_context(task_id)
        task = ctx["task"]
        width = int(task["width"] or DEFAULT_VIDEO_WIDTH)
        height = int(task["height"] or DEFAULT_VIDEO_HEIGHT)
        fps = int(task["fps"] or DEFAULT_VIDEO_FPS)
        duration = float(ctx["duration"])
        total_frames = max(1, int(math.ceil(duration * fps)))
        output_dir = Path(ctx["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        subtitle_suffix = "nosub" if str(task["subtitle_mode"] or "srt") == "none" else "srt"
        output_path = output_dir / f"chapter-{int(task['chapter_num']):03d}-{width}x{height}-{subtitle_suffix}.mp4"
        try:
            import PIL  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("缺少 Pillow，请安装 pillow") from exc
        renderer = FrameRenderer(width, height, ctx["scenes"], ctx["subtitles"])

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg not found")
        command = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-i",
            str(ctx["audio_path"]),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            assert proc.stdin is not None
            for frame_index in range(total_frames):
                t = frame_index / fps
                frame = renderer.render(t)
                proc.stdin.write(frame.tobytes())
                if frame_index % 5 == 0:
                    # Pillow rendering is CPU-heavy and runs in the server process;
                    # yield briefly so HTTP request threads remain responsive.
                    time.sleep(0.001)
                if frame_index % max(1, fps * 5) == 0:
                    if progress_callback:
                        progress_callback(made_progress=True)
                    progress = max(1, min(99, int(frame_index * 100 / total_frames)))
                    conn = db_conn()
                    conn.execute(
                        "UPDATE chapter_video_export_tasks SET progress=?,duration_seconds=?,current_frame=?,total_frames=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (progress, duration, frame_index, total_frames, task_id),
                    )
                    conn.commit()
                    conn.close()
            proc.stdin.close()
            stderr = proc.stderr.read().decode("utf-8", errors="ignore") if proc.stderr else ""
            code = proc.wait()
        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        if code != 0:
            raise RuntimeError(stderr.strip()[-1000:] or f"ffmpeg failed ({code})")
        rel_path = str(output_path.relative_to(ROOT_DIR))
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_video_export_tasks SET status='completed',progress=100,duration_seconds=?,current_frame=?,total_frames=?,output_file_path=?,error_message='',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (duration, total_frames, total_frames, rel_path, task_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_video_export_tasks SET status='failed',progress=0,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(exc), task_id),
        )
        conn.commit()
        conn.close()


def enqueue_video_export_task(novel_id: int, chapter_num: int, width: int = DEFAULT_VIDEO_WIDTH, height: int = DEFAULT_VIDEO_HEIGHT, fps: int = DEFAULT_VIDEO_FPS, subtitle_mode: str = "srt") -> tuple[bool, str, int | None]:
    try:
        width = int(width or DEFAULT_VIDEO_WIDTH)
        height = int(height or DEFAULT_VIDEO_HEIGHT)
        fps = int(fps or DEFAULT_VIDEO_FPS)
    except (TypeError, ValueError):
        return False, "invalid video size", None
    if (width, height) not in {(1080, 1920), (1920, 1080)}:
        return False, "unsupported video size", None
    subtitle_mode = str(subtitle_mode or "srt").strip().lower()
    if subtitle_mode not in {"srt", "none"}:
        return False, "unsupported subtitle mode", None
    conn = db_conn()
    row = conn.execute(
        "SELECT c.id,c.title,c.audio_duration_seconds FROM chapters c WHERE c.novel_id=? AND c.chapter_num=?",
        (novel_id, chapter_num),
    ).fetchone()
    if not row:
        conn.close()
        return False, "chapter not found", None
    existing = conn.execute(
        "SELECT id,status FROM chapter_video_export_tasks WHERE novel_id=? AND chapter_id=? AND width=? AND height=? AND fps=? AND subtitle_mode=?",
        (novel_id, int(row["id"]), width, height, fps, subtitle_mode),
    ).fetchone()
    if existing and str(existing["status"] or "") in {"pending", "running"}:
        task_id = int(existing["id"])
        conn.close()
        return True, "task already queued", task_id
    if existing:
        task_id = int(existing["id"])
        conn.execute(
            """
            UPDATE chapter_video_export_tasks
            SET status='pending',progress=0,width=?,height=?,fps=?,subtitle_mode=?,duration_seconds=?,current_frame=0,total_frames=0,
                process_id=0,output_file_path='',error_message='',started_at=NULL,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (width, height, fps, subtitle_mode, float(row["audio_duration_seconds"] or 0), task_id),
        )
    else:
        try:
            cursor = conn.execute(
                """
                INSERT INTO chapter_video_export_tasks(novel_id,chapter_id,chapter_num,chapter_title,status,progress,width,height,fps,subtitle_mode,duration_seconds)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (novel_id, int(row["id"]), chapter_num, str(row["title"] or ""), "pending", 0, width, height, fps, subtitle_mode, float(row["audio_duration_seconds"] or 0)),
            )
            task_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            existing = conn.execute(
                "SELECT id FROM chapter_video_export_tasks WHERE novel_id=? AND chapter_id=? AND width=? AND height=? AND fps=? AND subtitle_mode=?",
                (novel_id, int(row["id"]), width, height, fps, subtitle_mode),
            ).fetchone()
            if not existing:
                conn.close()
                return False, "video task already exists", None
            task_id = int(existing["id"])
    conn.commit()
    conn.close()
    return True, "queued", task_id


def list_video_export_tasks(novel_id: int | None = None) -> list[dict]:
    conn = db_conn()
    params: tuple = ()
    where = ""
    if novel_id:
        where = "WHERE t.novel_id=?"
        params = (novel_id,)
    rows = conn.execute(
        f"""
        SELECT t.*, n.name AS novel_name
        FROM chapter_video_export_tasks t
        JOIN novels n ON n.id=t.novel_id
        {where}
        ORDER BY t.updated_at DESC, t.id DESC
        LIMIT 500
        """,
        params,
    ).fetchall()
    conn.close()
    return [_task_to_dict(row) for row in rows]


def get_video_export_task(task_id: int) -> dict | None:
    conn = db_conn()
    row = conn.execute(
        """
        SELECT t.*, n.name AS novel_name
        FROM chapter_video_export_tasks t
        JOIN novels n ON n.id=t.novel_id
        WHERE t.id=?
        """,
        (task_id,),
    ).fetchone()
    conn.close()
    return _task_to_dict(row) if row else None


def retry_video_export_task(task_id: int) -> tuple[bool, str]:
    conn = db_conn()
    row = conn.execute("SELECT status FROM chapter_video_export_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        conn.close()
        return False, "task not found"
    if str(row["status"] or "") in {"pending", "running"}:
        conn.close()
        return False, "task is running"
    conn.execute(
        "UPDATE chapter_video_export_tasks SET status='pending',progress=0,current_frame=0,total_frames=0,process_id=0,error_message='',started_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()
    return True, "queued"


def cancel_video_export_task(task_id: int) -> tuple[bool, str]:
    conn = db_conn()
    row = conn.execute("SELECT status,process_id FROM chapter_video_export_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        conn.close()
        return False, "task not found"
    status = str(row["status"] or "")
    if status not in {"pending", "running"}:
        conn.close()
        return False, "only pending or running task can be cancelled"
    conn.execute(
        "UPDATE chapter_video_export_tasks SET status='cancelled',error_message='已终止',updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()
    pid = int(row["process_id"] or 0)
    if status == "running" and pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            pass
    return True, "cancelled"


def get_video_export_file_path(task_id: int) -> tuple[Path | None, str]:
    conn = db_conn()
    row = conn.execute(
        "SELECT output_file_path,chapter_num,chapter_title,width,height,subtitle_mode FROM chapter_video_export_tasks WHERE id=? AND status='completed'",
        (task_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None, ""
    path = _resolve_path(str(row["output_file_path"] or ""))
    if not path or not path.exists():
        return None, ""
    subtitle_suffix = "nosub" if str(row["subtitle_mode"] or "srt") == "none" else "srt"
    name = f"第{int(row['chapter_num']):03d}回-{_safe_filename(str(row['chapter_title'] or '视频'))}-{int(row['width'] or DEFAULT_VIDEO_WIDTH)}x{int(row['height'] or DEFAULT_VIDEO_HEIGHT)}-{subtitle_suffix}.mp4"
    return path, name


def _run_task_in_subprocess(task_id: int, progress_callback=None) -> None:
    command = [sys.executable, "-m", "server.video_export_runner", str(int(task_id))]
    proc = subprocess.Popen(command, cwd=str(ROOT_DIR))
    conn = db_conn()
    conn.execute(
        "UPDATE chapter_video_export_tasks SET process_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (int(proc.pid), task_id),
    )
    conn.commit()
    conn.close()
    while proc.poll() is None:
        if progress_callback:
            progress_callback(made_progress=True)
        conn = db_conn()
        row = conn.execute("SELECT status FROM chapter_video_export_tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()
        if row and str(row["status"] or "") == "cancelled":
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            except Exception:
                pass
            return
        time.sleep(1.0)
    conn = db_conn()
    row = conn.execute("SELECT status FROM chapter_video_export_tasks WHERE id=?", (task_id,)).fetchone()
    if row and str(row["status"] or "") == "cancelled":
        conn.execute("UPDATE chapter_video_export_tasks SET process_id=0,updated_at=CURRENT_TIMESTAMP WHERE id=?", (task_id,))
        conn.commit()
        conn.close()
        return
    conn.execute("UPDATE chapter_video_export_tasks SET process_id=0,updated_at=CURRENT_TIMESTAMP WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    if proc.returncode != 0:
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_video_export_tasks SET status='failed',progress=0,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status<>'failed'",
            (f"video export subprocess failed ({proc.returncode})", task_id),
        )
        conn.commit()
        conn.close()


def run_video_export_queue_once(progress_callback=None) -> bool:
    conn = db_conn()
    running = conn.execute("SELECT id FROM chapter_video_export_tasks WHERE status='running' ORDER BY id ASC LIMIT 1").fetchone()
    if running:
        task_id = int(running["id"])
        conn.close()
        _run_task_in_subprocess(task_id, progress_callback=progress_callback)
        return True
    pending = conn.execute("SELECT id FROM chapter_video_export_tasks WHERE status='pending' ORDER BY id ASC LIMIT 1").fetchone()
    if not pending:
        conn.close()
        return False
    task_id = int(pending["id"])
    conn.close()
    _run_task_in_subprocess(task_id, progress_callback=progress_callback)
    return True


def _task_to_dict(row) -> dict:
    output_path = _resolve_path(str(row["output_file_path"] or "")) if row["output_file_path"] else None
    size_bytes = output_path.stat().st_size if output_path and output_path.exists() else 0
    subtitle_mode = str(row["subtitle_mode"] or "srt") if "subtitle_mode" in row.keys() else "srt"
    srt_download_url = ""
    if subtitle_mode == "srt":
        conn = db_conn()
        asr_row = conn.execute(
            """
            SELECT corrected_srt_file_path
            FROM chapter_asr_tasks
            WHERE novel_id=? AND chapter_id=? AND COALESCE(corrected_srt_file_path,'')<>''
            ORDER BY id DESC LIMIT 1
            """,
            (int(row["novel_id"]), int(row["chapter_id"])),
        ).fetchone()
        conn.close()
        srt_path = _resolve_path(str(asr_row["corrected_srt_file_path"] or "")) if asr_row else None
        if srt_path and srt_path.exists() and srt_path.is_file():
            srt_download_url = f"/api/novels/{int(row['novel_id'])}/chapters/{int(row['chapter_num'])}/corrected-srt-file"
    return {
        "id": int(row["id"]),
        "novelId": int(row["novel_id"]),
        "novelName": str(row["novel_name"] or ""),
        "chapterId": int(row["chapter_id"]),
        "chapterNum": int(row["chapter_num"]),
        "chapterTitle": str(row["chapter_title"] or ""),
        "status": str(row["status"] or ""),
        "progress": int(row["progress"] or 0),
        "width": int(row["width"] or DEFAULT_VIDEO_WIDTH),
        "height": int(row["height"] or DEFAULT_VIDEO_HEIGHT),
        "fps": int(row["fps"] or DEFAULT_VIDEO_FPS),
        "subtitleMode": subtitle_mode,
        "durationSeconds": float(row["duration_seconds"] or 0),
        "currentFrame": int(row["current_frame"] or 0),
        "totalFrames": int(row["total_frames"] or 0),
        "outputFilePath": str(row["output_file_path"] or ""),
        "sizeBytes": int(size_bytes),
        "errorMessage": str(row["error_message"] or ""),
        "createdAt": str(row["created_at"] or ""),
        "startedAt": str(row["started_at"] or ""),
        "updatedAt": str(row["updated_at"] or ""),
        "downloadUrl": f"/api/video-export-tasks/{int(row['id'])}/file" if size_bytes else "",
        "srtDownloadUrl": srt_download_url,
    }
