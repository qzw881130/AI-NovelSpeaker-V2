from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.app_context import ROOT_DIR, db_conn


TARGET_ENGLISH_DIR = "hlm"


def int_to_cn(num: int) -> str:
    digits = "零一二三四五六七八九"
    units = ["", "十", "百", "千"]
    if num == 0:
        return "零"
    parts: list[str] = []
    unit_index = 0
    need_zero = False
    while num > 0:
        num, remainder = divmod(num, 10)
        if remainder == 0:
            need_zero = bool(parts)
        else:
            segment = digits[remainder] + units[unit_index]
            if need_zero:
                parts.append("零")
                need_zero = False
            parts.append(segment)
        unit_index += 1
    text = "".join(reversed(parts))
    text = re.sub(r"^一十", "十", text)
    text = re.sub(r"零+", "零", text).rstrip("零")
    return text


def convert_title(title: str) -> str:
    m = re.match(r"^第(\d+)章(.*)$", str(title or "").strip())
    if not m:
        return str(title or "").strip()
    num = int(m.group(1))
    suffix = m.group(2)
    return f"第{int_to_cn(num)}章{suffix}"


def normalize_text_files(text_dir: Path) -> int:
    changed = 0
    for path in sorted(text_dir.glob("*.txt")):
        m = re.match(r"^(\d{3})_(.+)$", path.stem)
        prefix = m.group(1) if m else ""
        title_part = m.group(2) if m else path.stem
        new_title_part = convert_title(title_part).replace(" ", "_")
        lines = (
            path.read_text(encoding="utf-8", errors="ignore")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .split("\n")
        )
        if lines:
            lines[0] = convert_title(lines[0])
        new_content = "\n".join(lines).rstrip() + "\n"
        new_name = (
            f"{prefix}_{new_title_part}.txt" if prefix else f"{new_title_part}.txt"
        )
        new_path = path.with_name(new_name)
        if new_path != path:
            path.rename(new_path)
            path = new_path
            changed += 1
        path.write_text(new_content, encoding="utf-8")
    return changed


def normalize_database(conn) -> tuple[int, int]:
    novel = conn.execute(
        "SELECT id FROM novels WHERE english_dir=?", (TARGET_ENGLISH_DIR,)
    ).fetchone()
    if not novel:
        raise SystemExit(f"novel not found: {TARGET_ENGLISH_DIR}")
    novel_id = int(novel["id"])
    chapters = conn.execute(
        "SELECT id, title, text_file_path FROM chapters WHERE novel_id=? ORDER BY chapter_num ASC",
        (novel_id,),
    ).fetchall()
    chapter_updates = 0
    for row in chapters:
        old_title = str(row["title"] or "")
        new_title = convert_title(old_title)
        old_path = str(row["text_file_path"] or "")
        new_path = old_path
        if old_path:
            rel = Path(old_path)
            stem_match = re.match(r"^(\d{3})_(.+)$", rel.stem)
            prefix = stem_match.group(1) if stem_match else ""
            title_part = stem_match.group(2) if stem_match else rel.stem
            new_title_part = convert_title(title_part).replace(" ", "_")
            new_path = str(
                rel.with_name(
                    f"{prefix}_{new_title_part}.txt"
                    if prefix
                    else f"{new_title_part}.txt"
                )
            ).replace("\\", "/")
        if new_title != old_title or new_path != old_path:
            conn.execute(
                "UPDATE chapters SET title=?, text_file_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_title, new_path, int(row["id"])),
            )
            chapter_updates += 1

    json_rows = conn.execute(
        "SELECT id, chapter_title, merged_result_json FROM json_tasks WHERE novel_id=?",
        (novel_id,),
    ).fetchall()
    json_updates = 0
    for row in json_rows:
        old_title = str(row["chapter_title"] or "")
        new_title = convert_title(old_title)
        merged = str(row["merged_result_json"] or "")
        updated = merged
        for num in range(1, 121):
            updated = updated.replace(f"第{num}章", f"第{int_to_cn(num)}章")
        if new_title != old_title or updated != merged:
            conn.execute(
                "UPDATE json_tasks SET chapter_title=?, merged_result_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_title, updated, int(row["id"])),
            )
            json_updates += 1
    return chapter_updates, json_updates


def main() -> None:
    text_dir = ROOT_DIR / "novel" / TARGET_ENGLISH_DIR / "text"
    if not text_dir.exists():
        raise SystemExit(f"text dir not found: {text_dir}")
    file_changes = normalize_text_files(text_dir)
    conn = db_conn()
    chapter_updates, json_updates = normalize_database(conn)
    conn.commit()
    conn.close()
    print(
        f"normalized hlm titles: files={file_changes}, chapters={chapter_updates}, json_tasks={json_updates}"
    )


if __name__ == "__main__":
    main()
