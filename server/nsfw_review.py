"""章节 NSFW 审查服务模块"""

from __future__ import annotations

import json
import time
from typing import Any

from .app_context import db_conn
from .services import (
    LlmRequestTimeoutError,
    apply_prompt_llm_settings,
    clear_local_llama_context,
    extract_chat_content,
    fetch_settings,
    http_json_request,
    normalize_ollama_keep_alive,
    normalize_llm_max_tokens,
    load_prompt_llm_settings,
    parse_model_json,
    read_chapter_text,
    split_text_batches,
    unload_ollama_model,
    build_ollama_chat_url,
)

NSFW_PROMPT_NAME = "NSFW审查提示词"
NSFW_SEVERITY_ORDER = [
    "强暴/非自愿性行为",
    "露出/公共性行为",
    "性行为描写",
    "口交/手交/足交等具体性行为",
    "性器官描写",
    "色情幻想/春梦",
    "性玩具/道具使用",
    "性暗示",
    "脏话/低俗言语（与性相关的）",
    "其他严重色情内容",
]


def _lookup_nsfw_prompt(conn) -> tuple[int | None, str]:
    row = conn.execute(
        "SELECT id, content FROM json_prompts WHERE name=? LIMIT 1",
        (NSFW_PROMPT_NAME,),
    ).fetchone()
    if not row:
        return None, ""
    return int(row["id"] or 0), str(row["content"] or "").strip()


def _call_llm_nsfw_review(
    *, llm: dict, proxy_url: str, system_prompt: str, chapter_title: str, chapter_text: str, batch_index: int = 1, batch_total: int = 1
) -> str:
    base_url = str(llm.get("baseUrl") or "").strip()
    provider = str(llm.get("provider") or "").strip()
    model = str(llm.get("model") or "").strip()
    api_key = str(llm.get("apiKey") or "").strip()
    temperature = float(llm.get("temperature") or 0.1)
    top_p = float(llm.get("topP") if llm.get("topP") not in (None, "") else 0.85)
    max_tokens = normalize_llm_max_tokens(provider, llm.get("maxTokens") or 8192)
    num_ctx = int(llm.get("numCtx") or 65536)
    keep_alive = str(llm.get("keepAlive") or "30m").strip() or "30m"
    unload_after_call = bool(llm.get("unloadAfterCall", False))
    batch_timeout_minutes = int(llm.get("batchTimeoutMinutes") or 15)
    think = bool(llm.get("think", True))

    if not base_url:
        raise RuntimeError("LLM baseUrl is empty")
    if not model:
        raise RuntimeError("LLM model is empty")

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    batch_note = ""
    if batch_total > 1:
        batch_note = (
            f"补充说明：当前是拆分批次 {batch_index}/{batch_total}，"
            "请只审查本批次文本，并返回该批次的违规结构。\n"
        )
    user_prompt = (
        "请严格按系统提示词要求审查以下小说章回文本。\n"
        "必须只返回一个 JSON 对象，不要返回解释文字。\n"
        f"{batch_note}"
        f"章回名：{chapter_title}\n"
        f"原文：\n{chapter_text}\n"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
    }
    if provider == "local_llama":
        payload["chat_template_kwargs"] = {"enable_thinking": think}
    request_timeout = float(max(60, batch_timeout_minutes * 60))
    url = f"{base_url.rstrip('/')}/chat/completions"
    if provider == "ollama":
        url = build_ollama_chat_url(base_url)
        request_keep_alive = normalize_ollama_keep_alive(keep_alive)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "think": think,
            "keep_alive": request_keep_alive,
            "options": {
                "num_ctx": num_ctx,
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": max_tokens,
            },
        }
    try:
        code, body = http_json_request(
            "POST",
            url,
            payload=payload,
            headers=headers,
            timeout=request_timeout,
            proxy_url=proxy_url,
        )
    finally:
        if provider == "ollama" and unload_after_call:
            try:
                unload_ollama_model(
                    base_url=base_url,
                    model=model,
                    proxy_url=proxy_url,
                    timeout=30.0,
                )
            except Exception:
                pass
        if provider == "local_llama" and unload_after_call:
            try:
                clear_local_llama_context(
                    base_url=base_url,
                    proxy_url=proxy_url,
                    timeout=10.0,
                )
            except Exception:
                pass

    if not (200 <= code < 300):
        detail = ""
        try:
            parsed = json.loads(body or "{}")
            if isinstance(parsed, dict):
                err = parsed.get("error")
                if isinstance(err, dict):
                    detail = str(err.get("message") or err.get("msg") or "").strip()
                elif isinstance(err, str):
                    detail = err.strip()
                if not detail:
                    detail = str(parsed.get("message") or parsed.get("msg") or "").strip()
        except Exception:
            detail = ""
        raise RuntimeError(f"LLM request failed (HTTP {code})" + (f": {detail[:200]}" if detail else ""))
    parsed_body = json.loads(body or "{}")
    if not isinstance(parsed_body, dict):
        raise RuntimeError("LLM response is not object")
    content = extract_chat_content(parsed_body)
    if not content:
        raise RuntimeError("LLM response content is empty")
    return content


def _normalize_review_result(payload: dict) -> dict:
    has_nsfw = bool(payload.get("has_nsfw", False))
    violations = payload.get("violations")
    normalized: list[dict[str, Any]] = []
    if isinstance(violations, list):
        for item in violations:
            if not isinstance(item, dict):
                continue
            nsfw_type = str(item.get("type") or "").strip()
            if not nsfw_type:
                continue
            raw_sentences = item.get("sentences")
            sentences: list[str] = []
            if isinstance(raw_sentences, list):
                for sentence in raw_sentences:
                    text = str(sentence or "").strip()
                    if text and text not in sentences:
                        sentences.append(text)
            normalized.append({"type": nsfw_type, "sentences": sentences})
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        if not has_nsfw or not normalized:
            summary = "未发现NSFW内容"
        else:
            count = sum(len(item["sentences"]) for item in normalized)
            severity = next((name for name in NSFW_SEVERITY_ORDER if any(item["type"] == name for item in normalized)), normalized[0]["type"])
            summary = f"共发现{count}处违规，包含{len(normalized)}种类型，最严重为【{severity}】"
    return {
        "has_nsfw": has_nsfw and bool(normalized),
        "violations": normalized,
        "summary": summary,
    }


def _merge_review_results(results: list[dict]) -> dict:
    type_map: dict[str, list[str]] = {}
    has_nsfw = False
    for result in results:
        normalized = _normalize_review_result(result)
        if normalized["has_nsfw"]:
            has_nsfw = True
        for item in normalized["violations"]:
            bucket = type_map.setdefault(item["type"], [])
            for sentence in item["sentences"]:
                if sentence not in bucket:
                    bucket.append(sentence)
    violations = [{"type": key, "sentences": value} for key, value in type_map.items()]
    if not violations:
        return {
            "has_nsfw": False,
            "violations": [],
            "summary": "未发现NSFW内容",
        }
    severity = next((name for name in NSFW_SEVERITY_ORDER if name in type_map), violations[0]["type"])
    count = sum(len(item["sentences"]) for item in violations)
    return {
        "has_nsfw": has_nsfw or bool(violations),
        "violations": violations,
        "summary": f"共发现{count}处违规，包含{len(violations)}种类型，最严重为【{severity}】",
    }


def list_nsfw_review_chapters(novel_id: int) -> list[dict]:
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT c.id, c.chapter_num, c.title, c.word_count,
               t.status, t.progress, t.result_json_text, t.error_message, t.updated_at
        FROM chapters c
        LEFT JOIN chapter_nsfw_tasks t ON t.chapter_id = c.id AND t.novel_id = c.novel_id
        WHERE c.novel_id=?
        ORDER BY c.chapter_num ASC
        """,
        (novel_id,),
    ).fetchall()
    conn.close()
    items = []
    for row in rows:
        raw_result = str(row["result_json_text"] or "").strip()
        summary = ""
        has_nsfw = False
        if raw_result:
            try:
                parsed = json.loads(raw_result)
                if isinstance(parsed, dict):
                    normalized = _normalize_review_result(parsed)
                    summary = normalized["summary"]
                    has_nsfw = bool(normalized["has_nsfw"])
            except Exception:
                summary = ""
        status = str(row["status"] or "").strip() or "idle"
        items.append(
            {
                "chapterId": int(row["id"]),
                "chapterNum": int(row["chapter_num"] or 0),
                "title": str(row["title"] or ""),
                "wordCount": int(row["word_count"] or 0),
                "status": status,
                "progress": int(row["progress"] or 0),
                "hasReview": bool(raw_result),
                "hasNsfw": has_nsfw,
                "summary": summary,
                "resultJsonText": raw_result,
                "errorMessage": str(row["error_message"] or ""),
                "updatedAt": str(row["updated_at"] or ""),
            }
        )
    return items


def enqueue_chapter_nsfw_review_task(novel_id: int, chapter_id: int) -> tuple[bool, str]:
    conn = db_conn()
    chapter = conn.execute(
        "SELECT id, chapter_num, title FROM chapters WHERE novel_id=? AND id=?",
        (novel_id, chapter_id),
    ).fetchone()
    if not chapter:
        conn.close()
        return False, "chapter not found"
    novel = conn.execute(
        "SELECT nsfw_prompt_id FROM novels WHERE id=?",
        (novel_id,),
    ).fetchone()
    prompt_id = int(novel["nsfw_prompt_id"] or 0) if novel and novel["nsfw_prompt_id"] is not None else None
    if not prompt_id:
        prompt_id, _ = _lookup_nsfw_prompt(conn)
    if not prompt_id:
        conn.close()
        return False, "nsfw review prompt not found"
    settings = fetch_settings(conn)
    llm = settings.get("llm") or {}
    existing = conn.execute(
        "SELECT status FROM chapter_nsfw_tasks WHERE novel_id=? AND chapter_id=?",
        (novel_id, chapter_id),
    ).fetchone()
    if existing and str(existing["status"] or "") in {"pending", "running", "processing"}:
        conn.close()
        return False, "nsfw review task already queued"
    conn.execute(
        """
        INSERT INTO chapter_nsfw_tasks(
            novel_id, chapter_id, chapter_num, chapter_title, prompt_id,
            status, progress, model_name, think_enabled, result_json_text, error_message, started_at, updated_at
        ) VALUES(?,?,?,?,?, 'pending',0,?,?, '', '', NULL, CURRENT_TIMESTAMP)
        ON CONFLICT(novel_id, chapter_id) DO UPDATE SET
            chapter_num=excluded.chapter_num,
            chapter_title=excluded.chapter_title,
            prompt_id=excluded.prompt_id,
            status='pending',
            progress=0,
            model_name=excluded.model_name,
            think_enabled=excluded.think_enabled,
            result_json_text='',
            error_message='',
            started_at=NULL,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            novel_id,
            int(chapter["id"]),
            int(chapter["chapter_num"] or 0),
            str(chapter["title"] or ""),
            prompt_id,
            str(llm.get("model") or ""),
            1 if bool(llm.get("think", True)) else 0,
        ),
    )
    conn.commit()
    conn.close()
    return True, "queued"


def enqueue_batch_nsfw_review_tasks(novel_id: int, chapter_nums: list[int] | None = None) -> tuple[bool, str, dict]:
    conn = db_conn()
    query = "SELECT id, chapter_num FROM chapters WHERE novel_id=?"
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
        ok, _ = enqueue_chapter_nsfw_review_task(novel_id, int(row["id"]))
        if ok:
            queued += 1
        else:
            skipped += 1
    return True, "ok", {"queued": queued, "skipped": skipped, "total": len(rows)}


def process_chapter_nsfw_review_task(task_id: int) -> None:
    conn = db_conn()
    row = conn.execute(
        """
        SELECT t.*, c.text_file_path, n.prompt_id AS novel_prompt_id
        FROM chapter_nsfw_tasks t
        JOIN chapters c ON c.id=t.chapter_id
        JOIN novels n ON n.id=t.novel_id
        WHERE t.id=?
        """,
        (task_id,),
    ).fetchone()
    if not row:
        conn.close()
        return
    try:
        prompt_id = int(row["prompt_id"] or 0)
        prompt_row = conn.execute(
            "SELECT content FROM json_prompts WHERE id=?",
            (prompt_id,),
        ).fetchone()
        if not prompt_row:
            raise RuntimeError("nsfw review prompt not found")
        system_prompt = str(prompt_row["content"] or "").strip()
        if not system_prompt:
            raise RuntimeError("nsfw review prompt content is empty")

        settings = fetch_settings(conn)
        llm = apply_prompt_llm_settings(settings.get("llm") or {}, load_prompt_llm_settings(conn, prompt_id))
        proxy_url = str(settings.get("proxyUrl") or "")
        model_name = str(llm.get("model") or "")
        think_enabled = 1 if bool(llm.get("think", True)) else 0
        chapter_title = str(row["chapter_title"] or f"第{int(row['chapter_num'])}回")
        chapter_text = read_chapter_text(str(row["text_file_path"] or ""))
        if not chapter_text:
            raise RuntimeError("chapter text is empty or missing")

        raw_batch_max_chars = llm.get("batchMaxChars", 3500)
        if raw_batch_max_chars in (None, ""):
            raw_batch_max_chars = 3500
        batch_max_chars = int(raw_batch_max_chars)
        if batch_max_chars not in {0, 3500, 4000, 5000, 6000, 7000, 8000, 9000, 10000}:
            batch_max_chars = 3500
        batches = split_text_batches(chapter_text, max_chars=batch_max_chars)

        conn.execute(
            "UPDATE chapter_nsfw_tasks SET progress=10, model_name=?, think_enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (model_name, think_enabled, task_id),
        )
        conn.commit()
        conn.close()

        parsed_outputs: list[dict] = []
        for idx, batch_text in enumerate(batches, start=1):
            conn = db_conn()
            conn.execute(
                "UPDATE chapter_nsfw_tasks SET status='processing', progress=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (min(92, 10 + int(78 * (idx - 1) / max(1, len(batches)))), task_id),
            )
            conn.commit()
            conn.close()
            raw = _call_llm_nsfw_review(
                llm=llm,
                proxy_url=proxy_url,
                system_prompt=system_prompt,
                chapter_title=chapter_title,
                chapter_text=batch_text,
                batch_index=idx,
                batch_total=len(batches),
            )
            parsed_outputs.append(parse_model_json(raw))

        merged = _merge_review_results(parsed_outputs)
        conn = db_conn()
        conn.execute(
            "UPDATE chapter_nsfw_tasks SET status='completed', progress=100, result_json_text=?, error_message='', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (json.dumps(merged, ensure_ascii=False), task_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        conn = db_conn()
        status = "timeout" if isinstance(exc, LlmRequestTimeoutError) else "failed"
        conn.execute(
            "UPDATE chapter_nsfw_tasks SET status=?, progress=0, error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, str(exc), task_id),
        )
        conn.commit()
        conn.close()


def run_nsfw_review_queue_once() -> bool:
    conn = db_conn()
    running = conn.execute(
        "SELECT id FROM chapter_nsfw_tasks WHERE status IN ('running','processing') ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if running:
        task_id = int(running["id"])
        conn.close()
        process_chapter_nsfw_review_task(task_id)
        return True

    pending = conn.execute(
        "SELECT id FROM chapter_nsfw_tasks WHERE status='pending' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if not pending:
        conn.close()
        return False
    task_id = int(pending["id"])
    conn.execute(
        "UPDATE chapter_nsfw_tasks SET status='running', progress=5, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()
    process_chapter_nsfw_review_task(task_id)
    return True
