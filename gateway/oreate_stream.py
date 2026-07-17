"""SSE parsing and upstream generation result helpers."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional


def parse_sse_line(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        parsed = {"event": "message", "data": data}
    if isinstance(parsed, dict):
        return parsed
    return {"event": "message", "data": parsed}


def parse_sse_lines(lines: Iterable[Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for raw in lines:
        event = parse_sse_line(raw)
        if event is not None:
            events.append(event)
    return events


def classify_sse_error(events: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    for event in events:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        code = data.get("code") or event.get("code") or event.get("err")
        if event.get("event") == "error" or code:
            message = data.get("msg") or data.get("message") or event.get("msg") or event.get("message") or "upstream error"
            return {"code": str(code or "UPSTREAM_ERROR"), "message": str(message)}
    return None


def classify_history_error(body: Any, ignored_codes: Optional[List[str]] = None) -> Optional[Dict[str, str]]:
    ignored = set(ignored_codes or [])
    if isinstance(body, dict):
        status = body.get("status") if isinstance(body.get("status"), dict) else {}
        code = status.get("code")
        if code not in (None, "", 0, "0") and str(code) not in ignored:
            message = status.get("msg") or status.get("message") or body.get("message") or "history hydration failed"
            return {"code": str(code), "message": str(message)}

    def walk(value: Any) -> Optional[Dict[str, str]]:
        if isinstance(value, list):
            for item in value:
                error = walk(item)
                if error:
                    return error
            return None
        if not isinstance(value, dict):
            return None
        explicit_error = value.get("error") or value.get("err")
        if isinstance(explicit_error, dict):
            code = explicit_error.get("code") or explicit_error.get("errCode") or explicit_error.get("errorCode")
            message = explicit_error.get("msg") or explicit_error.get("message") or explicit_error.get("errorMessage")
            if code not in (None, "", 0, "0") and str(code) not in ignored:
                return {"code": str(code), "message": str(message or "history hydration failed")}
        for code_key in ("errorCode", "errCode"):
            code = value.get(code_key)
            if code not in (None, "", 0, "0") and str(code) not in ignored:
                message = value.get("errorMessage") or value.get("errMsg") or value.get("msg") or value.get("message")
                return {"code": str(code), "message": str(message or "history hydration failed")}
        for message_key in ("failReason",):
            message = value.get(message_key)
            if isinstance(message, str) and message.strip():
                return {"code": "UPSTREAM_ERROR", "message": message.strip()}
        for item in value.values():
            error = walk(item)
            if error:
                return error
        return None

    return walk(body)


MEDIA_URL_RE = re.compile(
    r"https?://(?:"
    r"cdn\.oreateai\.com/(?:aiimage|aivideo|static/result)[^\s)\"']+"
    r"|[^\s)\"']+\.(?:jpg|jpeg|png|webp|gif|mp4|mov|webm)(?:\?[^\s)\"']*)?"
    r")",
    re.IGNORECASE,
)


def extract_generation_assets(body: Any) -> List[str]:
    assets: List[str] = []

    def add(value: Any) -> None:
        if not isinstance(value, str) or not value:
            return
        for url in MEDIA_URL_RE.findall(value):
            if url not in assets:
                assets.append(url)

    def walk(value: Any) -> None:
        if isinstance(value, str):
            add(value)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("role") == "user":
            return
        for key in ("url", "bosUrl", "bos_url", "object", "src", "downloadUrl"):
            add(value.get(key))
        files = value.get("files")
        if isinstance(files, list):
            for item in files:
                walk(item)
        for item in value.values():
            walk(item)

    walk(body)
    return assets

