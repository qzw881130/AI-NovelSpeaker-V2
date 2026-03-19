"""角色库管理模块"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from copy import deepcopy
from pathlib import Path

from .app_context import (
    DB_PATH,
    NOVEL_DIR,
    ROOT_DIR,
    WORKFLOWS_DIR,
    db_conn,
)
from .services import fetch_novels


ROLE_LEVEL_LABELS = {
    1: "一等角色",
    2: "二等角色",
    3: "三等角色",
}


def _normalize_role_level(level: object) -> int:
    try:
        value = int(str(level))
    except (TypeError, ValueError):
        return 3
    return value if value in ROLE_LEVEL_LABELS else 3


def _row_to_role(row) -> dict:
    if not row:
        return {}
    level = _normalize_role_level(row["role_level"])
    return {
        "id": int(row["id"]),
        "novelId": int(row["novel_id"]),
        "name": str(row["name"] or ""),
        "instruct": str(row["instruct"] or ""),
        "sampleText": str(row["sample_text"] or ""),
        "sampleAudioPath": str(row["sample_audio_path"] or ""),
        "sampleAudioSource": str(row["sample_audio_source"] or ""),
        "roleLevel": level,
        "roleLevelLabel": ROLE_LEVEL_LABELS[level],
        "createdAt": str(row["created_at"] or ""),
        "updatedAt": str(row["updated_at"] or ""),
    }


def list_roles(novel_id: int | None = None) -> dict:
    conn = db_conn()
    params = []
    where_clause = ""
    if novel_id is not None:
        where_clause = "WHERE novel_id = ?"
        params.append(novel_id)

    rows = conn.execute(
        f"""
        SELECT id, novel_id, name, instruct, sample_text, sample_audio_path, sample_audio_source, role_level,
               created_at, updated_at
        FROM roles
        {where_clause}
        ORDER BY role_level ASC, name COLLATE NOCASE ASC, id ASC
        """,
        params,
    ).fetchall()
    conn.close()

    roles = [_row_to_role(row) for row in rows]
    stats = {
        "total": len(roles),
        "level_1": sum(1 for role in roles if role["roleLevel"] == 1),
        "level_2": sum(1 for role in roles if role["roleLevel"] == 2),
        "level_3": sum(1 for role in roles if role["roleLevel"] == 3),
        "without_sample": sum(1 for role in roles if not role["sampleAudioPath"]),
    }
    return {"stats": stats, "roles": roles}


def get_role(role_id: int) -> dict | None:
    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    conn.close()
    return _row_to_role(row) if row else None


def get_role_by_name(novel_id: int, name: str) -> dict | None:
    conn = db_conn()
    row = conn.execute(
        "SELECT * FROM roles WHERE novel_id=? AND name=?",
        (novel_id, name),
    ).fetchone()
    conn.close()
    return _row_to_role(row) if row else None


def upsert_role_default(
    novel_id: int, name: str, instruct: str, sample_text: str
) -> tuple[bool, str, dict | None]:
    role_name = str(name or "").strip()
    role_instruct = str(instruct or "").strip()
    role_sample = str(sample_text or "").strip()
    if not role_name:
        return False, "role name cannot be empty", None

    conn = db_conn()
    row = conn.execute(
        "SELECT * FROM roles WHERE novel_id=? AND name=?",
        (novel_id, role_name),
    ).fetchone()
    if row:
        old_audio_path = str(row["sample_audio_path"] or "").strip()
        conn.execute(
            """
            UPDATE roles
            SET instruct=?, sample_text=?, sample_audio_path='', sample_audio_source='', updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (role_instruct, role_sample, int(row["id"])),
        )
        conn.commit()
        saved = conn.execute(
            "SELECT * FROM roles WHERE id=?", (int(row["id"]),)
        ).fetchone()
        conn.close()

        if old_audio_path:
            old_path = (ROOT_DIR / old_audio_path).resolve()
            root_path = ROOT_DIR.resolve()
            if root_path in old_path.parents and old_path != root_path:
                _remove_cached_playable_variants(old_path)

        return True, "saved", _row_to_role(saved)

    cur = conn.execute(
        """
        INSERT INTO roles(novel_id, name, instruct, sample_text, role_level)
        VALUES(?, ?, ?, ?, 3)
        """,
        (novel_id, role_name, role_instruct, role_sample),
    )
    conn.commit()
    role_id = int(cur.lastrowid or 0)
    saved = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    conn.close()
    if role_id <= 0 or not saved:
        return False, "failed to save role", None
    return True, "saved", _row_to_role(saved)


def update_role_fields(
    role_id: int, name: str, instruct: str, sample_text: str
) -> tuple[bool, str, dict | None]:
    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Role not found", None

    novel_id = int(row["novel_id"])
    role_name = str(name or "").strip()
    if not role_name:
        conn.close()
        return False, "Role name cannot be empty", None

    dup = conn.execute(
        "SELECT 1 FROM roles WHERE novel_id=? AND name=? AND id<>? LIMIT 1",
        (novel_id, role_name, role_id),
    ).fetchone()
    if dup:
        conn.close()
        return False, f"duplicate role name: {role_name}", None

    conn.execute(
        """
        UPDATE roles
        SET name=?, instruct=?, sample_text=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (
            role_name,
            str(instruct or "").strip(),
            str(sample_text or "").strip(),
            role_id,
        ),
    )
    conn.commit()
    saved = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    conn.close()
    return True, "saved", _row_to_role(saved)


def update_role_level(role_id: int, role_level: int) -> tuple[bool, str, dict | None]:
    level = _normalize_role_level(role_level)
    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Role not found", None
    conn.execute(
        "UPDATE roles SET role_level=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (level, role_id),
    )
    conn.commit()
    saved = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    conn.close()
    return True, "saved", _row_to_role(saved)


def duplicate_role(role_id: int) -> tuple[bool, str, dict | None]:
    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Role not found", None

    novel_id = int(row["novel_id"])
    base_name = str(row["name"] or "").strip() or f"role-{role_id}"
    next_name = f"{base_name}-副本"
    suffix = 2
    while conn.execute(
        "SELECT 1 FROM roles WHERE novel_id=? AND name=? LIMIT 1",
        (novel_id, next_name),
    ).fetchone():
        next_name = f"{base_name}-副本{suffix}"
        suffix += 1

    cur = conn.execute(
        """
        INSERT INTO roles(novel_id, name, instruct, sample_text, sample_audio_path, sample_audio_source, role_level)
        SELECT novel_id, ?, instruct, sample_text, '', '', role_level FROM roles WHERE id=?
        """,
        (next_name, role_id),
    )
    conn.commit()
    new_id = int(cur.lastrowid or 0)
    saved = conn.execute("SELECT * FROM roles WHERE id=?", (new_id,)).fetchone()
    conn.close()
    return True, "duplicated", _row_to_role(saved)


def delete_role(role_id: int) -> tuple[bool, str]:
    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Role not found"

    audio_path = str(row["sample_audio_path"] or "").strip()
    conn.execute("DELETE FROM roles WHERE id=?", (role_id,))
    conn.commit()
    conn.close()

    if audio_path:
        path = (ROOT_DIR / audio_path).resolve()
        root_path = ROOT_DIR.resolve()
        if root_path in path.parents and path != root_path:
            _remove_cached_playable_variants(path)

    return True, "deleted"


def save_role_sample_audio(
    role_id: int, audio_base64: str, source: str
) -> tuple[bool, str, dict | None]:
    audio_bytes = base64.b64decode(audio_base64)
    if len(audio_bytes) == 0:
        return False, "audio data is empty", None

    conn = db_conn()
    row = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if not row:
        conn.close()
        return False, "Role not found", None

    novel_id = int(row["novel_id"])
    role_name = str(row["name"] or "").strip()
    old_audio_path = str(row["sample_audio_path"] or "").strip()

    file_name = f"role-{role_id}-{int(time.time())}.flac"
    rel_dir = f"novel/{novel_id}/voices"
    abs_dir = ROOT_DIR / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    abs_path = abs_dir / file_name
    rel_path = f"{rel_dir}/{file_name}"

    with open(abs_path, "wb") as f:
        f.write(audio_bytes)

    conn.execute(
        """
        UPDATE roles
        SET sample_audio_path=?, sample_audio_source=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (rel_path, source, role_id),
    )
    conn.commit()
    saved = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    conn.close()

    if old_audio_path:
        old_full_path = (ROOT_DIR / old_audio_path).resolve()
        _remove_cached_playable_variants(old_full_path)

    return True, "saved", _row_to_role(saved)


def _remove_cached_playable_variants(path: Path):
    if not path.exists():
        return
    try:
        path.unlink()
    except Exception:
        pass


# For backwards compatibility with existing code
def list_roles_summary(novel_id: int | None = None) -> dict:
    return list_roles(novel_id)


def get_roles_by_novel(novel_id: int) -> list[dict]:
    result = list_roles(novel_id)
    return result.get("roles", [])


def get_role_library_map(novel_id: int) -> dict[str, dict]:
    conn = db_conn()
    rows = conn.execute(
        "SELECT id, name, sample_text, sample_audio_path FROM roles WHERE novel_id=?",
        (novel_id,),
    ).fetchall()
    conn.close()
    mapping: dict[str, dict] = {}
    for row in rows:
        name = str(row["name"] or "").strip()
        if name:
            mapping[name] = {
                "id": int(row["id"]),
                "name": name,
                "sample_text": str(row["sample_text"] or "").strip(),
                "sample_audio_path": str(row["sample_audio_path"] or "").strip(),
            }
    return mapping
