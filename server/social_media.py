from __future__ import annotations

import json
import os
import time
import secrets
from pathlib import Path
from urllib import parse, request
from urllib.parse import urlparse

from .app_context import ROOT_DIR, db_conn
from .video_export import get_video_export_cover_path, get_video_export_file_path, _resolve_path


DEFAULT_YOUTUBE_PROXY_URL = "http://127.0.0.1:7897"
DEFAULT_YOUTUBE_TAGS = "四大名著,三国演义,有声小说,旺仔有声小说"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
DEFAULT_YOUTUBE_REDIRECT_URI = "http://localhost:8080/oauth"
RUNNING_UPLOAD_STALE_SECONDS = 300


def _get_channel_name() -> str:
    from .services import fetch_settings

    conn = db_conn()
    try:
        settings = fetch_settings(conn)
    finally:
        conn.close()
    return str(settings.get("channelName") or "旺仔有声小说").strip() or "旺仔有声小说"


def _normalize_redirect_uri(value: str) -> str:
    text = str(value or "").strip() or DEFAULT_YOUTUBE_REDIRECT_URI
    parsed = urlparse(text)
    if parsed.path != "/oauth":
        scheme = parsed.scheme or "http"
        netloc = parsed.netloc or "localhost:8080"
        text = f"{scheme}://{netloc}/oauth"
    return text


def _setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT setting_value FROM app_settings WHERE setting_key=?", (key,)).fetchone()
    return str(row["setting_value"] if row else default)


def _set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings(setting_key,setting_value) VALUES(?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
        (key, str(value or "")),
    )


def _resolve_config_path(value: str) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = ROOT_DIR / path
    try:
        return path.resolve()
    except Exception:
        return None


def _client_config_from_file(path_value: str) -> dict:
    path = _resolve_config_path(path_value)
    if not path or not path.exists() or not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    cfg = parsed.get("web") or parsed.get("installed") or {}
    return cfg if isinstance(cfg, dict) else {}


def get_youtube_settings() -> dict:
    conn = db_conn()
    data = {
        "clientSecretPath": _setting(conn, "youtube_client_secret_path", ""),
        "tokenPath": _setting(conn, "youtube_token_path", ""),
        "clientId": _setting(conn, "youtube_client_id", ""),
        "clientSecret": _setting(conn, "youtube_client_secret", ""),
        "redirectUri": _normalize_redirect_uri(_setting(conn, "youtube_redirect_uri", DEFAULT_YOUTUBE_REDIRECT_URI)),
        "proxyEnabled": _setting(conn, "youtube_proxy_enabled", "0") == "1",
        "proxyUrl": _setting(conn, "youtube_proxy_url", DEFAULT_YOUTUBE_PROXY_URL) or DEFAULT_YOUTUBE_PROXY_URL,
        "defaultTags": _setting(conn, "youtube_default_tags", DEFAULT_YOUTUBE_TAGS) or DEFAULT_YOUTUBE_TAGS,
        "channelName": _setting(conn, "channel_name", "旺仔有声小说") or "旺仔有声小说",
    }
    if not data["clientId"] or not data["clientSecret"]:
        cfg = _client_config_from_file(data["clientSecretPath"])
        data["clientId"] = data["clientId"] or str(cfg.get("client_id") or "").strip()
        data["clientSecret"] = data["clientSecret"] or str(cfg.get("client_secret") or "").strip()
        redirects = cfg.get("redirect_uris") if isinstance(cfg.get("redirect_uris"), list) else []
        if redirects and (not data["redirectUri"] or data["redirectUri"] == DEFAULT_YOUTUBE_REDIRECT_URI):
            data["redirectUri"] = _normalize_redirect_uri(str(redirects[0] or DEFAULT_YOUTUBE_REDIRECT_URI))
    conn.close()
    files = []
    for key, label in [("clientSecretPath", "OAuth client_secret.json"), ("tokenPath", "OAuth token.json")]:
        path = _resolve_config_path(str(data.get(key) or ""))
        files.append({
            "key": key,
            "label": label,
            "path": str(path or data.get(key) or ""),
            "exists": bool(path and path.exists() and path.is_file()),
        })
    data["files"] = files
    return data


def save_youtube_settings(payload: dict) -> dict:
    conn = db_conn()
    if "clientSecretPath" in payload:
        _set_setting(conn, "youtube_client_secret_path", str(payload.get("clientSecretPath") or "").strip())
    if "tokenPath" in payload:
        _set_setting(conn, "youtube_token_path", str(payload.get("tokenPath") or "").strip())
    _set_setting(conn, "youtube_client_id", str(payload.get("clientId") or "").strip())
    _set_setting(conn, "youtube_client_secret", str(payload.get("clientSecret") or "").strip())
    _set_setting(conn, "youtube_redirect_uri", _normalize_redirect_uri(str(payload.get("redirectUri") or DEFAULT_YOUTUBE_REDIRECT_URI)))
    _set_setting(conn, "youtube_proxy_enabled", "1" if bool(payload.get("proxyEnabled")) else "0")
    _set_setting(conn, "youtube_proxy_url", str(payload.get("proxyUrl") or DEFAULT_YOUTUBE_PROXY_URL).strip() or DEFAULT_YOUTUBE_PROXY_URL)
    _set_setting(conn, "youtube_default_tags", str(payload.get("defaultTags") or DEFAULT_YOUTUBE_TAGS).strip() or DEFAULT_YOUTUBE_TAGS)
    conn.commit()
    conn.close()
    return get_youtube_settings()


def save_youtube_config_file(kind: str, data: bytes) -> dict:
    key = str(kind or "").strip()
    if key != "client-secret":
        raise RuntimeError("unsupported youtube config file")
    if not data:
        raise RuntimeError("file is empty")
    try:
        json.loads(data.decode("utf-8-sig"))
    except Exception as exc:
        raise RuntimeError("上传文件不是有效 JSON") from exc
    target_dir = ROOT_DIR / "temp" / "settings"
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = "youtube-client-secret.json"
    target = target_dir / filename
    target.write_bytes(data)
    rel_path = target.relative_to(ROOT_DIR).as_posix()
    setting_key = "youtube_client_secret_path"
    conn = db_conn()
    _set_setting(conn, setting_key, rel_path)
    parsed = json.loads(data.decode("utf-8-sig"))
    cfg = parsed.get("web") or parsed.get("installed") or {}
    if isinstance(cfg, dict):
        _set_setting(conn, "youtube_client_id", str(cfg.get("client_id") or "").strip())
        _set_setting(conn, "youtube_client_secret", str(cfg.get("client_secret") or "").strip())
        redirects = cfg.get("redirect_uris") if isinstance(cfg.get("redirect_uris"), list) else []
        if redirects:
            _set_setting(conn, "youtube_redirect_uri", _normalize_redirect_uri(str(redirects[0] or DEFAULT_YOUTUBE_REDIRECT_URI)))
    conn.commit()
    conn.close()
    return get_youtube_settings()


def build_youtube_oauth_url() -> str:
    settings = get_youtube_settings()
    client_id = str(settings.get("clientId") or "").strip()
    if not client_id:
        raise RuntimeError("请先上传 client_secret.json 或填写 Client ID")
    state = secrets.token_urlsafe(24)
    conn = db_conn()
    _set_setting(conn, "youtube_oauth_state", state)
    conn.commit()
    conn.close()
    query = parse.urlencode({
        "client_id": client_id,
        "redirect_uri": _normalize_redirect_uri(str(settings.get("redirectUri") or DEFAULT_YOUTUBE_REDIRECT_URI)),
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


def finish_youtube_oauth(code: str, state: str) -> dict:
    settings = get_youtube_settings()
    client_id = str(settings.get("clientId") or "").strip()
    client_secret = str(settings.get("clientSecret") or "").strip()
    redirect_uri = _normalize_redirect_uri(str(settings.get("redirectUri") or DEFAULT_YOUTUBE_REDIRECT_URI))
    if not client_id or not client_secret:
        raise RuntimeError("缺少 Client ID 或 Client secret")
    conn = db_conn()
    expected_state = _setting(conn, "youtube_oauth_state", "")
    conn.close()
    if expected_state and str(state or "") != expected_state:
        raise RuntimeError("OAuth state 不匹配，请重新授权")
    payload = parse.urlencode({
        "code": str(code or ""),
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    req = request.Request("https://oauth2.googleapis.com/token", data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    opener = request.build_opener()
    if bool(settings.get("proxyEnabled")) and str(settings.get("proxyUrl") or "").strip():
        proxy_url = str(settings.get("proxyUrl") or "").strip()
        opener = request.build_opener(request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    with opener.open(req, timeout=60) as resp:
        token_data = json.loads(resp.read().decode("utf-8"))
    token_path = ROOT_DIR / "temp" / "settings" / "youtube-token.json"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_json = {
        "token": token_data.get("access_token", ""),
        "refresh_token": token_data.get("refresh_token", ""),
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": YOUTUBE_SCOPES,
    }
    if token_data.get("expires_in"):
        token_json["expiry"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + int(token_data.get("expires_in") or 0)))
    token_path.write_text(json.dumps(token_json, ensure_ascii=False, indent=2), encoding="utf-8")
    rel_path = token_path.relative_to(ROOT_DIR).as_posix()
    conn = db_conn()
    _set_setting(conn, "youtube_token_path", rel_path)
    _set_setting(conn, "youtube_oauth_state", "")
    conn.commit()
    conn.close()
    return get_youtube_settings()


def _task_to_dict(row) -> dict:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "platform": str(row["platform"] or "youtube"),
        "videoExportTaskId": int(row["video_export_task_id"]),
        "novelId": int(row["novel_id"]),
        "chapterId": int(row["chapter_id"]),
        "chapterNum": int(row["chapter_num"]),
        "title": str(row["title"] or ""),
        "playlistTitle": str(row["playlist_title"] or ""),
        "tags": str(row["tags"] or ""),
        "privacyStatus": str(row["privacy_status"] or "private"),
        "status": str(row["status"] or "pending"),
        "progress": int(row["progress"] or 0),
        "youtubeVideoId": str(row["youtube_video_id"] or ""),
        "youtubeUrl": str(row["youtube_url"] or ""),
        "errorMessage": str(row["error_message"] or ""),
        "createdAt": str(row["created_at"] or ""),
        "startedAt": str(row["started_at"] or ""),
        "updatedAt": str(row["updated_at"] or ""),
    }


def latest_youtube_upload_for_video_export(conn, video_export_task_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM social_media_upload_tasks WHERE platform='youtube' AND video_export_task_id=? ORDER BY id DESC LIMIT 1",
        (int(video_export_task_id),),
    ).fetchone()
    return _task_to_dict(row) if row else None


def list_youtube_upload_tasks(novel_id: int | None = None) -> list[dict]:
    conn = db_conn()
    where = ""
    params: tuple = ()
    if novel_id:
        where = "WHERE novel_id=?"
        params = (int(novel_id),)
    rows = conn.execute(f"SELECT * FROM social_media_upload_tasks {where} ORDER BY updated_at DESC,id DESC LIMIT 500", params).fetchall()
    conn.close()
    return [_task_to_dict(row) for row in rows]


def retry_youtube_upload(task_id: int) -> tuple[bool, str, dict | None]:
    conn = db_conn()
    row = conn.execute("SELECT * FROM social_media_upload_tasks WHERE id=?", (int(task_id),)).fetchone()
    if not row:
        conn.close()
        return False, "upload task not found", None
    conn.execute(
        """
        UPDATE social_media_upload_tasks
        SET status='pending',progress=0,youtube_video_id='',youtube_url='',error_message='',started_at=NULL,updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (int(task_id),),
    )
    conn.commit()
    task = conn.execute("SELECT * FROM social_media_upload_tasks WHERE id=?", (int(task_id),)).fetchone()
    conn.close()
    return True, "queued", _task_to_dict(task)


def enqueue_youtube_upload(video_export_task_id: int, payload: dict) -> tuple[bool, str, dict | None]:
    conn = db_conn()
    row = conn.execute(
        """
        SELECT t.*, n.name AS novel_name
        FROM chapter_video_export_tasks t
        JOIN novels n ON n.id=t.novel_id
        WHERE t.id=? AND t.status='completed' AND COALESCE(t.output_file_path,'')<>''
        """,
        (int(video_export_task_id),),
    ).fetchone()
    if not row:
        conn.close()
        return False, "video export task not completed", None
    running = conn.execute(
        "SELECT id,status FROM social_media_upload_tasks WHERE platform='youtube' AND video_export_task_id=? AND status IN ('pending','running') ORDER BY id DESC LIMIT 1",
        (int(video_export_task_id),),
    ).fetchone()
    if running:
        task = conn.execute("SELECT * FROM social_media_upload_tasks WHERE id=?", (int(running["id"]),)).fetchone()
        conn.close()
        return True, "already queued", _task_to_dict(task)
    title = str(payload.get("title") or f"{row['novel_name']}|{row['chapter_title']} | {_get_channel_name()}").strip()
    playlist_title = str(payload.get("playlistTitle") or f"有声《{row['novel_name']}》").strip()
    settings = get_youtube_settings()
    tags = str(payload.get("tags") or settings.get("defaultTags") or DEFAULT_YOUTUBE_TAGS).strip() or DEFAULT_YOUTUBE_TAGS
    privacy_status = str(payload.get("privacyStatus") or "private").strip() or "private"
    cur = conn.execute(
        """
        INSERT INTO social_media_upload_tasks(video_export_task_id,novel_id,chapter_id,chapter_num,title,playlist_title,tags,privacy_status,status,progress)
        VALUES(?,?,?,?,?,?,?,?, 'pending', 0)
        """,
        (int(row["id"]), int(row["novel_id"]), int(row["chapter_id"]), int(row["chapter_num"]), title, playlist_title, tags, privacy_status),
    )
    task_id = int(cur.lastrowid)
    conn.commit()
    task = conn.execute("SELECT * FROM social_media_upload_tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    return True, "queued", _task_to_dict(task)


def _get_authenticated_youtube(settings: dict):
    try:
        import httplib2
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from google_auth_httplib2 import AuthorizedHttp
    except ImportError as exc:
        raise RuntimeError("缺少 YouTube 上传依赖，请安装 google-api-python-client google-auth google-auth-oauthlib httplib2") from exc
    token_path = _resolve_config_path(str(settings.get("tokenPath") or ""))
    if not token_path or not token_path.exists():
        raise RuntimeError("未找到 YouTube token.json，请先在社交媒体配置中设置")
    creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
    if not creds.valid:
        raise RuntimeError("YouTube token.json 无效或已过期，请重新授权")
    proxy_info = None
    if bool(settings.get("proxyEnabled")):
        parsed = urlparse(str(settings.get("proxyUrl") or DEFAULT_YOUTUBE_PROXY_URL))
        if parsed.hostname and parsed.port:
            proxy_info = httplib2.ProxyInfo(
                proxy_type=httplib2.socks.PROXY_TYPE_HTTP,
                proxy_host=parsed.hostname,
                proxy_port=int(parsed.port),
            )
    http = httplib2.Http(proxy_info=proxy_info)
    # YouTube resumable uploads use HTTP 308 without a Location header to
    # acknowledge chunks. httplib2 treats 308 as a redirect unless disabled.
    http.follow_redirects = False
    authed_http = AuthorizedHttp(creds, http=http)
    return build("youtube", "v3", http=authed_http)


def _cached_youtube_playlists(conn) -> list[dict]:
    raw = _setting(conn, "youtube_playlists_cache_json", "[]")
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def get_cached_youtube_playlists() -> list[dict]:
    conn = db_conn()
    items = _cached_youtube_playlists(conn)
    conn.close()
    return items


def _find_playlist_id(title: str) -> str:
    wanted = str(title or "").strip()
    if not wanted:
        return ""
    for item in get_cached_youtube_playlists():
        if str(item.get("title") or "").strip() == wanted:
            return str(item.get("id") or "")
    return ""


def fetch_youtube_playlists() -> list[dict]:
    settings = get_youtube_settings()
    youtube = _get_authenticated_youtube(settings)
    playlists = []
    page_token = None
    while True:
        resp = youtube.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=page_token).execute()
        for item in resp.get("items", []):
            playlists.append({"id": str(item.get("id") or ""), "title": str(item.get("snippet", {}).get("title") or "")})
        page_token = resp.get("nextPageToken")
        if not page_token:
            conn = db_conn()
            _set_setting(conn, "youtube_playlists_cache_json", json.dumps(playlists, ensure_ascii=False))
            conn.commit()
            conn.close()
            return playlists


def _srt_path_for_task(conn, row) -> Path | None:
    asr_row = conn.execute(
        """
        SELECT corrected_srt_file_path FROM chapter_asr_tasks
        WHERE novel_id=? AND chapter_id=? AND COALESCE(corrected_srt_file_path,'')<>''
        ORDER BY id DESC LIMIT 1
        """,
        (int(row["novel_id"]), int(row["chapter_id"])),
    ).fetchone()
    return _resolve_path(str(asr_row["corrected_srt_file_path"] or "")) if asr_row else None


def _upload_task(task_id: int, progress_callback=None) -> None:
    conn = db_conn()
    row = conn.execute(
        """
        SELECT u.*, t.output_file_path,t.cover_image_index,t.subtitle_mode,n.name AS novel_name
        FROM social_media_upload_tasks u
        JOIN chapter_video_export_tasks t ON t.id=u.video_export_task_id
        JOIN novels n ON n.id=u.novel_id
        WHERE u.id=?
        """,
        (int(task_id),),
    ).fetchone()
    if not row:
        conn.close()
        return
    conn.execute("UPDATE social_media_upload_tasks SET status='running',progress=5,error_message='',started_at=COALESCE(started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(task_id),))
    conn.commit()
    settings = get_youtube_settings()
    video_path, _ = get_video_export_file_path(int(row["video_export_task_id"]))
    image_index = int(row["cover_image_index"] or 0) or None
    cover_path, _ = get_video_export_cover_path(int(row["video_export_task_id"]), image_index=image_index)
    srt_path = _srt_path_for_task(conn, row)
    conn.close()
    if not video_path or not video_path.exists():
        raise RuntimeError("视频文件不存在")
    youtube = _get_authenticated_youtube(settings)
    from googleapiclient.http import MediaFileUpload

    tags = [item.strip() for item in str(row["tags"] or "").split(",") if item.strip()]
    body = {
        "snippet": {
            "title": str(row["title"] or ""),
            "description": "",
            "tags": tags,
            "categoryId": "22",
            "defaultLanguage": "zh",
            "defaultAudioLanguage": "zh",
        },
        "status": {
            "privacyStatus": str(row["privacy_status"] or "private"),
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
        "recordingDetails": {"recordingDate": time.strftime("%Y-%m-%d")},
    }
    media = MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status,recordingDetails", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            progress = 10 + int(status.progress() * 70)
            conn = db_conn()
            conn.execute("UPDATE social_media_upload_tasks SET progress=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (progress, int(task_id)))
            conn.commit()
            conn.close()
            if progress_callback:
                progress_callback(made_progress=True)
    video_id = str(response.get("id") or "")
    if not video_id:
        raise RuntimeError("YouTube 未返回 video id")
    conn = db_conn()
    conn.execute("UPDATE social_media_upload_tasks SET progress=85,youtube_video_id=?,youtube_url=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (video_id, f"https://youtu.be/{video_id}", int(task_id)))
    conn.commit()
    conn.close()
    warnings = []
    if cover_path and cover_path.exists():
        try:
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(cover_path), mimetype="image/jpeg")).execute()
        except Exception as exc:
            warnings.append(f"封面上传失败：{exc}")
            print(f"[youtube-upload] set thumbnail failed: {exc}", flush=True)
    if srt_path and srt_path.exists() and str(row["subtitle_mode"] or "srt") == "srt":
        try:
            youtube.captions().insert(
                part="snippet",
                body={"snippet": {"videoId": video_id, "language": "zh", "name": "", "isDraft": False}},
                media_body=MediaFileUpload(str(srt_path), mimetype="application/x-subrip"),
            ).execute()
        except Exception as exc:
            warnings.append(f"字幕上传失败：{exc}")
            print(f"[youtube-upload] upload captions failed: {exc}", flush=True)
    playlist_id = _find_playlist_id(str(row["playlist_title"] or ""))
    if playlist_id:
        try:
            youtube.playlistItems().insert(
                part="snippet",
                body={"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
            ).execute()
        except Exception as exc:
            warnings.append(f"加入播放列表失败：{exc}")
            print(f"[youtube-upload] add playlist failed: {exc}", flush=True)
    conn = db_conn()
    conn.execute(
        "UPDATE social_media_upload_tasks SET status='completed',progress=100,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
        ("；".join(warnings), int(task_id)),
    )
    conn.commit()
    conn.close()


def run_social_upload_queue_once(progress_callback=None) -> bool:
    conn = db_conn()
    running = conn.execute(
        """
        SELECT id FROM social_media_upload_tasks
        WHERE status='running'
          AND (strftime('%s','now') - strftime('%s',updated_at)) <= ?
        ORDER BY id ASC LIMIT 1
        """,
        (RUNNING_UPLOAD_STALE_SECONDS,),
    ).fetchone()
    pending = None if running else conn.execute("SELECT id FROM social_media_upload_tasks WHERE status='pending' ORDER BY id ASC LIMIT 1").fetchone()
    row = running or pending
    conn.close()
    if not row:
        return False
    task_id = int(row["id"])
    try:
        _upload_task(task_id, progress_callback=progress_callback)
    except Exception as exc:
        conn = db_conn()
        conn.execute("UPDATE social_media_upload_tasks SET status='failed',progress=0,error_message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (str(exc), task_id))
        conn.commit()
        conn.close()
    return True
