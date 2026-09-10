import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import video_export


class OrganizeNovelVideosTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="video-organization-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.novel_dir = self.root / "novel"
        self.output = self.novel_dir / "test-novel" / "video_final"
        self.database = self.root / "test.sqlite3"
        self.conn = self.connect()
        self.addCleanup(self.conn.close)
        # Only the columns queried by organization, plus task uniqueness.
        self.conn.executescript("""
            CREATE TABLE novels (id INTEGER PRIMARY KEY, english_dir TEXT, name TEXT);
            CREATE TABLE chapters (
                id INTEGER PRIMARY KEY, novel_id INTEGER,
                chapter_num INTEGER, title TEXT
            );
            CREATE TABLE chapter_video_export_tasks (
                id INTEGER PRIMARY KEY, novel_id INTEGER, chapter_id INTEGER,
                chapter_num INTEGER, chapter_title TEXT, status TEXT,
                width INTEGER, height INTEGER, fps INTEGER,
                subtitle_mode TEXT, output_file_path TEXT,
                UNIQUE(novel_id, chapter_id, width, height, fps, subtitle_mode)
            );
            CREATE TABLE chapter_asr_tasks (
                id INTEGER PRIMARY KEY, novel_id INTEGER, chapter_id INTEGER,
                corrected_srt_file_path TEXT,
                UNIQUE(novel_id, chapter_id)
            );
            INSERT INTO novels VALUES (1, 'test-novel', '\u7ea2\u697c\u68a6');
            INSERT INTO chapters VALUES (1, 1, 7, 'Title');
        """)
        for name, value in (("ROOT_DIR", self.root), ("NOVEL_DIR", self.novel_dir)):
            self.enterContext(patch.object(video_export, name, value))
        self.db_mock = self.enterContext(
            patch.object(video_export, "db_conn", side_effect=self.connect)
        )

    def connect(self):
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def source(self, name, content):
        path = self.root / "sources" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def task(self, path, *, chapter_id=1, novel_id=1, width=1920,
             height=1080, fps=30, mode="srt", status="completed"):
        self.conn.execute(
            """INSERT INTO chapter_video_export_tasks
               (novel_id, chapter_id, chapter_num, chapter_title, status,
                width, height, fps, subtitle_mode, output_file_path)
               VALUES (?, ?, 999, 'Stale title', ?, ?, ?, ?, ?, ?)""",
            (novel_id, chapter_id, status, width, height, fps, mode, str(path)),
        )
        self.conn.commit()

    def subtitle(self, path, chapter_id=1):
        self.conn.execute(
            "INSERT INTO chapter_asr_tasks "
            "(novel_id, chapter_id, corrected_srt_file_path) VALUES (1, ?, ?)",
            (chapter_id, str(path)),
        )
        self.conn.commit()

    def assert_output(self, result, expected):
        expected = {f"\u7ea2\u697c\u68a6_{name}": content for name, content in expected.items()}
        self.assertTrue(self.output.is_dir())
        actual = {
            path.relative_to(self.output).as_posix(): path.read_bytes()
            for path in self.output.rglob("*") if path.is_file()
        }
        self.assertEqual(actual, expected)
        self.assertEqual(result, {
            "path": str(self.output),
            "videoCount": sum(name.endswith(".mp4") for name in expected),
            "subtitleCount": sum(name.endswith(".srt") for name in expected),
            "sizeBytes": sum(len(content) for content in expected.values()),
        })

    def test_both_ratios_copy_matching_srt_count_bytes_and_preserve_sources(self):
        portrait = self.source("portrait.mp4", b"portrait-video")
        landscape = self.source("landscape.mp4", b"landscape-video")
        srt_content = "1\n00:00:00,000 --> 00:00:01,000\n\u5b57\u5e55\n".encode()
        srt = self.source("unrelated-name.srt", srt_content)
        self.task(portrait.relative_to(self.root), width=1080, height=1920)
        self.task(landscape)
        self.subtitle(srt.relative_to(self.root))
        originals = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                     for p in (portrait, landscape, srt)}

        result = video_export.organize_novel_videos(1)

        self.assert_output(result, {
            "9x16/\u7b2c007\u56de Title.mp4": b"portrait-video",
            "9x16/\u7b2c007\u56de Title.srt": srt_content,
            "16x9/\u7b2c007\u56de Title.mp4": b"landscape-video",
            "16x9/\u7b2c007\u56de Title.srt": srt_content,
        })
        for path, original in originals.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)
        self.db_mock.assert_called_once_with()

    def test_current_title_fullwidth_and_ascii_spaces_and_safe_filename(self):
        cases = [
            ("  \u98ce\u96e8\u3000\u3000\u5c71\u6cb3  ", "\u98ce\u96e8 \u5c71\u6cb3"),
            ("  Alpha   Beta  ", "Alpha Beta"),
            ('Alpha/:*?"<>|\\Beta', "Alpha_Beta"),
            (" \u3000 ._ ", ""),
        ]
        expected = {}
        for number, (title, safe_title) in enumerate(cases, 1):
            self.conn.execute("INSERT INTO chapters VALUES (?, 1, ?, ?)",
                              (number + 1, number, title))
            content = str(number).encode()
            self.task(self.source(f"title-{number}.mp4", content), chapter_id=number + 1)
            stem = f"\u7b2c{number:03d}\u56de" + (f" {safe_title}" if safe_title else "")
            expected[f"16x9/{stem}.mp4"] = content
        self.assert_output(video_export.organize_novel_videos(1), expected)

    def test_missing_or_empty_video_and_directory_are_skipped_with_subtitles(self):
        srt = self.source("existing.srt", b"subtitle")
        self.subtitle(srt)
        for fps, path in enumerate((self.root / "missing.mp4", "", srt.parent), 30):
            self.task(path, fps=fps)
        self.assert_output(video_export.organize_novel_videos(1), {})
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(srt.read_bytes(), b"subtitle")

    def test_missing_empty_or_absent_subtitle_does_not_skip_video(self):
        expected = {}
        for chapter_id, subtitle in enumerate((None, "", self.root / "missing.srt"), 2):
            self.conn.execute("INSERT INTO chapters VALUES (?, 1, ?, 'Title')",
                              (chapter_id, chapter_id))
            self.task(self.source(f"chapter-{chapter_id}.mp4", b"video"),
                      chapter_id=chapter_id)
            if subtitle is not None:
                self.subtitle(subtitle, chapter_id)
            expected[f"16x9/\u7b2c{chapter_id:03d}\u56de Title.mp4"] = b"video"
        self.assert_output(video_export.organize_novel_videos(1), expected)

    def test_empty_novel_creates_output_directory(self):
        self.assertFalse(self.novel_dir.exists())
        self.assert_output(video_export.organize_novel_videos(1), {})
        self.assertEqual(list(self.output.iterdir()), [])

    def test_only_completed_tasks_of_requested_novel_are_copied(self):
        video = self.source("video.mp4", b"video")
        for fps, status in enumerate(("pending", "running", "failed", "cancelled"), 30):
            self.task(video, fps=fps, status=status)
        self.conn.execute("INSERT INTO novels VALUES (2, 'other-novel', 'Other')")
        self.conn.execute("INSERT INTO chapters VALUES (2, 2, 1, 'Other')")
        self.task(video, novel_id=2, chapter_id=2)
        self.assert_output(video_export.organize_novel_videos(1), {})
        self.assertFalse((self.novel_dir / "other-novel").exists())

    def test_subtitle_modes_do_not_overwrite_and_repeated_run_is_stable(self):
        subtitled = self.source("srt.mp4", b"subtitled-video")
        plain = self.source("none.mp4", b"plain-video")
        subtitle = self.source("chapter.srt", b"subtitle")
        self.task(subtitled)
        self.task(plain, mode="none")
        self.subtitle(subtitle)
        expected = {
            "16x9/\u7b2c007\u56de Title.mp4": b"subtitled-video",
            "16x9/\u7b2c007\u56de Title.srt": b"subtitle",
            "16x9/\u7b2c007\u56de Title-1920x1080-nosub-30fps.mp4": b"plain-video",
            "16x9/\u7b2c007\u56de Title-1920x1080-nosub-30fps.srt": b"subtitle",
        }
        for run in range(2):
            with self.subTest(run=run):
                self.assert_output(video_export.organize_novel_videos(1), expected)
                self.assertEqual(subtitled.read_bytes(), b"subtitled-video")
                self.assertEqual(plain.read_bytes(), b"plain-video")
                self.assertEqual(subtitle.read_bytes(), b"subtitle")

    def test_same_source_absolute_relative_and_symlink_paths_are_deduplicated(self):
        video = self.source("video.mp4", b"one-video")
        alias = video.with_name("alias.mp4")
        alias.symlink_to(video)
        self.task(video)
        self.task(video.relative_to(self.root), mode="none")
        self.task(alias, fps=60)
        self.subtitle(self.source("chapter.srt", b"subtitle"))
        self.assert_output(video_export.organize_novel_videos(1), {
            "16x9/\u7b2c007\u56de Title.mp4": b"one-video",
            "16x9/\u7b2c007\u56de Title.srt": b"subtitle",
        })

    def test_hardlinks_to_same_physical_video_are_deduplicated(self):
        video = self.source("video.mp4", b"one-video")
        alias = video.with_name("hardlink.mp4")
        alias.hardlink_to(video)
        self.assertTrue(video.samefile(alias))
        self.task(video)
        self.task(alias, mode="none")
        self.assert_output(video_export.organize_novel_videos(1), {
            "16x9/\u7b2c007\u56de Title.mp4": b"one-video",
        })

    def test_distinct_physical_files_with_identical_bytes_are_not_deduplicated(self):
        self.task(self.source("first.mp4", b"identical"))
        self.task(self.source("second.mp4", b"identical"), mode="none")
        self.assert_output(video_export.organize_novel_videos(1), {
            "16x9/\u7b2c007\u56de Title.mp4": b"identical",
            "16x9/\u7b2c007\u56de Title-1920x1080-nosub-30fps.mp4": b"identical",
        })

    def test_nonexistent_novel_raises_without_creating_output(self):
        with self.assertRaisesRegex(ValueError, "^novel not found$"):
            video_export.organize_novel_videos(999)
        self.assertFalse(self.novel_dir.exists())


if __name__ == "__main__":
    unittest.main()
