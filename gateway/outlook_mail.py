"""Outlook / Hotmail mail backend via shop-card, msOauth2api, or Microsoft Graph.

Supports:
1. Shop-card `/get?key=...&email=addr----pass----client_id----refresh_token`
2. Standard msOauth2api `/api/mail-new?...`
3. Direct Microsoft Graph fallback using refresh_token (when shop APIs are broken)
"""

from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import requests

from gateway.yyds_mail import YydsClient

MailConfigFn = Callable[[], Mapping[str, Any]]
ClaimMailboxFn = Callable[[], Dict[str, Any]]
ResolveMailboxFn = Callable[[str], Dict[str, Any]]
FinishMailboxFn = Callable[[str, str, str], None]

_GRAPH_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default offline_access"
_GRAPH_DEFAULT_FOLDERS = ("inbox", "junkemail", "clutter")
_GRAPH_FOLDER_ALIASES = {
    "inbox": {"inbox", "mailinbox", ""},
    "junkemail": {"junk", "junkemail", "junkemailmessages", "spam"},
    "clutter": {"clutter", "focused"},
}
_GRAPH_FOLDER_NAME_HINTS = (
    "junk",
    "spam",
    "clutter",
    "junkemail",
    "垃圾",
    "垃圾邮件",
    "垃圾郵件",
    "杂乱",
    "雜亂",
)
_SHOP_ERROR_RE = re.compile(r"(账号不存在|取件失败|密钥错误|缺少email|not\s*found|unauthorized)", re.I)


def _mail_session() -> requests.Session:
    session = requests.Session()
    # Local HTTP_PROXY can break shop-card hosts / Graph; talk to endpoints directly.
    session.trust_env = False
    return session


def _mail_http_get(url: str, params: Mapping[str, Any], timeout: int = 45) -> requests.Response:
    return _mail_session().get(url, params=dict(params), timeout=timeout)


def _extract_query_value(query: str, key: str) -> str:
    """Extract one query value without parse_qs (+ → space) rewriting token chars."""
    match = re.search(rf"(?:^|&){re.escape(key)}=([^&]*)", str(query or ""))
    if not match:
        return ""
    return unquote(match.group(1)).strip()


def _account_from_parts(
    email: str,
    password: str,
    client_id: str,
    refresh_token: str,
    *,
    detected_base: str = "",
    detected_key: str = "",
) -> Optional[Dict[str, str]]:
    email = str(email or "").strip()
    password = str(password or "").strip()
    client_id = str(client_id or "").strip()
    refresh_token = str(refresh_token or "").strip().rstrip("&?")
    if "@" not in email or not password or not client_id or not refresh_token:
        return None
    return {
        "email": email,
        "password": password,
        "client_id": client_id,
        "refresh_token": refresh_token,
        "detected_base_url": detected_base,
        "detected_api_key": detected_key,
    }


def _parse_mail_new_url(url: str) -> Tuple[str, str, str, str]:
    """Return (email, client_id, refresh_token, base_url) from a mail-new style URL."""
    parsed = urlparse(str(url or "").strip())
    base = ""
    if parsed.scheme and parsed.netloc:
        base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    query = parsed.query or ""
    return (
        _extract_query_value(query, "email"),
        _extract_query_value(query, "client_id"),
        _extract_query_value(query, "refresh_token"),
        base,
    )


def parse_outlook_import_line(line: str) -> Optional[Dict[str, str]]:
    raw = str(line or "").strip()
    if not raw or raw.startswith("#"):
        return None

    # Format A: whole-line shop /get URL
    # email=addr----pass----client_id----refresh_token
    if re.match(r"^https?://", raw, re.I) and ("/get" in raw or "email=" in raw):
        parsed = urlparse(raw)
        detected_base = ""
        detected_key = ""
        if parsed.scheme and parsed.netloc:
            detected_base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        query = parsed.query or ""
        detected_key = _extract_query_value(query, "key")
        payload = _extract_query_value(query, "email")
        parts = payload.split("----")
        if len(parts) >= 4:
            return _account_from_parts(
                parts[0],
                parts[1],
                parts[2],
                "----".join(parts[3:]),
                detected_base=detected_base,
                detected_key=detected_key,
            )

    parts = raw.split("----")

    # Format B: email----password----https://host/api/mail-new?refresh_token=...&client_id=...
    if len(parts) >= 3 and re.match(r"^https?://", parts[2].strip(), re.I):
        email = parts[0].strip()
        password = parts[1].strip()
        url = "----".join(parts[2:]).strip()
        url_email, client_id, refresh_token, base = _parse_mail_new_url(url)
        return _account_from_parts(
            email or url_email,
            password,
            client_id,
            refresh_token,
            detected_base=base,
        )

    # Format C: email----password----client_id----refresh_token
    if len(parts) < 4:
        return None
    return _account_from_parts(
        parts[0],
        parts[1],
        parts[2],
        "----".join(parts[3:]),
    )


def parse_outlook_import_text(text: str) -> Dict[str, Any]:
    accounts: List[Dict[str, str]] = []
    errors: List[Dict[str, Any]] = []
    detected_base = ""
    detected_key = ""
    for index, line in enumerate(str(text or "").splitlines(), 1):
        try:
            parsed = parse_outlook_import_line(line)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append({"line": index, "error": str(exc)})
            continue
        if parsed is None:
            if str(line or "").strip():
                errors.append({"line": index, "error": "unrecognized outlook account line"})
            continue
        if parsed.get("detected_base_url") and not detected_base:
            detected_base = parsed["detected_base_url"]
        if parsed.get("detected_api_key") and not detected_key:
            detected_key = parsed["detected_api_key"]
        accounts.append(
            {
                "email": parsed["email"],
                "password": parsed["password"],
                "client_id": parsed["client_id"],
                "refresh_token": parsed["refresh_token"],
            }
        )
    return {
        "accounts": accounts,
        "errors": errors,
        "detected_base_url": detected_base,
        "detected_api_key": detected_key,
    }


def _message_blobs(message: Mapping[str, Any]) -> str:
    blobs: List[str] = []
    for key in (
        "subject",
        "title",
        "html",
        "text",
        "body",
        "bodyPreview",
        "content",
        "send",
        "message",
        "mail",
        "raw",
    ):
        value = message.get(key)
        if isinstance(value, list):
            blobs.extend(str(item) for item in value)
        elif isinstance(value, dict):
            # Graph body: {"contentType":"html","content":"..."}
            if "content" in value:
                blobs.append(str(value.get("content") or ""))
            else:
                blobs.append(json.dumps(value, ensure_ascii=False))
        elif value not in (None, ""):
            blobs.append(str(value))
    return html.unescape("\n".join(blobs))


def is_shop_api_error_payload(payload: Mapping[str, Any]) -> bool:
    """Shop /get often returns HTTP 200 with {"code":404,"msg":"账号不存在"}."""
    if not isinstance(payload, Mapping):
        return False
    msg = str(payload.get("msg") or payload.get("message") or payload.get("error") or "").strip()
    code = payload.get("code")
    if msg and _SHOP_ERROR_RE.search(msg):
        return True
    # Bare status payloads with no mail body fields.
    mailish = any(payload.get(k) not in (None, "") for k in ("subject", "title", "html", "text", "body", "content", "mail"))
    if not mailish and code not in (None, "", 0, "0", 200, "200") and not str(payload.get("verification_code") or "").strip():
        if isinstance(code, int) and code >= 400:
            return True
        if str(code).isdigit() and int(str(code)) >= 400:
            return True
    return False


def normalize_mail_payload(payload: Any) -> Dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return {}
        try:
            return normalize_mail_payload(json.loads(text))
        except Exception:
            return {"text": text, "raw": text}
    if not isinstance(payload, dict):
        return {"raw": str(payload)}

    if is_shop_api_error_payload(payload):
        raise RuntimeError(
            f"outlook shop API error: code={payload.get('code')} msg={payload.get('msg') or payload.get('message') or ''}"
        )

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if isinstance(data.get("mail"), dict):
        data = data["mail"]
    out = dict(data)
    # Only keep verification codes that look like OTP, not HTTP-ish status codes.
    for candidate in (payload.get("verification_code"), payload.get("code"), out.get("code")):
        text = str(candidate or "").strip()
        if re.fullmatch(r"\d{4,8}", text):
            out["code"] = text
            break
    else:
        if "code" in out and not re.fullmatch(r"\d{4,8}", str(out.get("code") or "").strip()):
            out.pop("code", None)
    return out


def graph_message_to_payload(message: Mapping[str, Any]) -> Dict[str, Any]:
    body = message.get("body") if isinstance(message.get("body"), dict) else {}
    content = str(body.get("content") or "")
    return {
        "id": str(message.get("id") or ""),
        "subject": str(message.get("subject") or ""),
        "bodyPreview": str(message.get("bodyPreview") or ""),
        "html": content,
        "text": content,
        "body": content,
        "receivedDateTime": str(message.get("receivedDateTime") or ""),
    }


class OutlookMailClient:
    """Pool-backed Outlook mailbox client used by registration."""

    def __init__(
        self,
        mail_config: MailConfigFn,
        *,
        claim_mailbox: ClaimMailboxFn,
        resolve_mailbox: ResolveMailboxFn,
        finish_mailbox: Optional[FinishMailboxFn] = None,
    ):
        self._mail_config = mail_config
        self._claim_mailbox = claim_mailbox
        self._resolve_mailbox = resolve_mailbox
        self._finish_mailbox = finish_mailbox
        self._extract = YydsClient(mail_config)

    def _cfg(self) -> Mapping[str, Any]:
        return self._mail_config()

    @property
    def base(self) -> str:
        return str(self._cfg().get("base_url") or "").rstrip("/")

    @property
    def api_key(self) -> str:
        return str(self._cfg().get("api_key") or "")

    @property
    def api_mode(self) -> str:
        mode = str(self._cfg().get("api_mode") or "auto").strip().lower()
        return mode or "auto"

    def create_mailbox(self) -> Dict[str, Any]:
        row = self._claim_mailbox()
        email = str(row.get("email") or "").strip()
        mailbox_id = str(row.get("id") or "").strip()
        if not email or not mailbox_id:
            raise RuntimeError("outlook mailbox pool returned an invalid account")
        domain = email.split("@")[-1] if "@" in email else "outlook.com"
        return {
            "address": email,
            "token": mailbox_id,
            "domain": domain,
            "mailbox_id": mailbox_id,
            "provider": "outlook",
        }

    def _credential_blob(self, account: Mapping[str, Any]) -> str:
        return "----".join(
            [
                str(account.get("email") or ""),
                str(account.get("password") or ""),
                str(account.get("client_id") or ""),
                str(account.get("refresh_token") or ""),
            ]
        )

    def _fetch_via_get(self, account: Mapping[str, Any]) -> Dict[str, Any]:
        if not self.base:
            raise RuntimeError("outlook mail base_url missing in config")
        params = {"email": self._credential_blob(account)}
        if self.api_key:
            params["key"] = self.api_key
        r = _mail_http_get(f"{self.base}/get", params, timeout=45)
        if r.status_code >= 400:
            raise RuntimeError(f"outlook /get failed: HTTP {r.status_code} {(r.text or '')[:300]}")
        try:
            return normalize_mail_payload(r.json())
        except RuntimeError:
            raise
        except Exception:
            return normalize_mail_payload(r.text)

    def _fetch_via_msoauth2(self, account: Mapping[str, Any], mailbox: str) -> Dict[str, Any]:
        if not self.base:
            raise RuntimeError("outlook mail base_url missing in config")
        params = {
            "email": str(account.get("email") or ""),
            "client_id": str(account.get("client_id") or ""),
            "refresh_token": str(account.get("refresh_token") or ""),
            "mailbox": mailbox,
            "response_type": "json",
        }
        if self.api_key:
            params["password"] = self.api_key
        paths = ("/api/mail-new", "/mail-new")
        errors: List[str] = []
        for path in paths:
            r = _mail_http_get(f"{self.base}{path}", params, timeout=45)
            if r.status_code >= 400:
                errors.append(f"{path}: HTTP {r.status_code} {(r.text or '')[:160]}")
                continue
            try:
                return normalize_mail_payload(r.json())
            except RuntimeError as exc:
                errors.append(f"{path}:{exc}")
                continue
            except Exception:
                return normalize_mail_payload(r.text)
        raise RuntimeError("outlook msoauth2 fetch failed: " + " | ".join(errors[:4]))

    def _graph_access_token(self, account: Mapping[str, Any]) -> str:
        client_id = str(account.get("client_id") or "").strip()
        refresh_token = str(account.get("refresh_token") or "").strip()
        if not client_id or not refresh_token:
            raise RuntimeError("outlook graph credentials missing client_id/refresh_token")
        r = _mail_session().post(
            _GRAPH_TOKEN_URL,
            data={
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": _GRAPH_SCOPE,
            },
            timeout=45,
        )
        try:
            body = r.json()
        except Exception:
            body = {}
        token = str(body.get("access_token") or "").strip()
        if r.status_code >= 400 or not token:
            err = body.get("error_description") or body.get("error") or (r.text or "")[:200]
            raise RuntimeError(f"outlook graph token failed: HTTP {r.status_code} {err}")
        return token

    def _graph_folder_urls(self, access_token: str, folders: Sequence[str]) -> List[Tuple[str, str]]:
        """Return (folder_label, messages_url) pairs, including discovered junk/clutter folders."""
        headers = {"Authorization": f"Bearer {access_token}"}
        urls: List[Tuple[str, str]] = []
        seen_urls: set[str] = set()

        def add(label: str, url: str) -> None:
            if url in seen_urls:
                return
            seen_urls.add(url)
            urls.append((label, url))

        for folder in folders:
            folder_key = str(folder or "").strip().lower()
            select = (
                "?$top=25&$select=id,subject,bodyPreview,body,receivedDateTime"
                "&$orderby=receivedDateTime desc"
            )
            if folder_key in _GRAPH_FOLDER_ALIASES["inbox"]:
                add("inbox", "https://graph.microsoft.com/v1.0/me/messages" + select)
            elif folder_key in _GRAPH_FOLDER_ALIASES["junkemail"]:
                add(
                    "junkemail",
                    "https://graph.microsoft.com/v1.0/me/mailFolders/junkemail/messages" + select,
                )
            elif folder_key in _GRAPH_FOLDER_ALIASES["clutter"]:
                add(
                    "clutter",
                    "https://graph.microsoft.com/v1.0/me/mailFolders/clutter/messages" + select,
                )
            elif folder_key:
                add(
                    folder_key,
                    f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages" + select,
                )

        # Discover localized folder names (e.g. 垃圾邮件) and scan them too.
        try:
            r = _mail_session().get(
                "https://graph.microsoft.com/v1.0/me/mailFolders"
                "?$top=50&$select=id,displayName,wellKnownName",
                headers=headers,
                timeout=45,
            )
            body = r.json() if r.content else {}
            if r.status_code < 400:
                for item in body.get("value") or []:
                    if not isinstance(item, dict):
                        continue
                    folder_id = str(item.get("id") or "").strip()
                    if not folder_id:
                        continue
                    well_known = str(item.get("wellKnownName") or "").strip().lower()
                    display = str(item.get("displayName") or "").strip().lower()
                    interesting = well_known in {"inbox", "junkemail", "clutter"} or any(
                        hint in display for hint in _GRAPH_FOLDER_NAME_HINTS
                    )
                    if not interesting:
                        continue
                    label = well_known or display or folder_id[:12]
                    add(
                        f"discovered:{label}",
                        (
                            f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder_id}/messages"
                            "?$top=25&$select=id,subject,bodyPreview,body,receivedDateTime"
                            "&$orderby=receivedDateTime desc"
                        ),
                    )
        except Exception:
            pass
        return urls

    def _fetch_via_graph(
        self,
        account: Mapping[str, Any],
        folders: Sequence[str] = _GRAPH_DEFAULT_FOLDERS,
    ) -> List[Dict[str, Any]]:
        access = self._graph_access_token(account)
        headers = {"Authorization": f"Bearer {access}"}
        messages: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        errors: List[str] = []
        for folder_label, url in self._graph_folder_urls(access, folders):
            r = _mail_session().get(url, headers=headers, timeout=45)
            try:
                body = r.json()
            except Exception:
                body = {}
            if r.status_code >= 400:
                err = (
                    (body.get("error") or {}).get("message")
                    if isinstance(body.get("error"), dict)
                    else (r.text or "")[:160]
                )
                errors.append(f"{folder_label}: HTTP {r.status_code} {err}")
                continue
            for item in body.get("value") or []:
                if not isinstance(item, dict):
                    continue
                message_id = str(item.get("id") or "").strip()
                if message_id and message_id in seen_ids:
                    continue
                if message_id:
                    seen_ids.add(message_id)
                messages.append(graph_message_to_payload(item))
        if not messages and errors:
            raise RuntimeError("outlook graph fetch failed: " + " | ".join(errors[:4]))
        return messages

    def probe_graph_mailbox(self, account: Mapping[str, Any]) -> Dict[str, Any]:
        """Classify whether Graph credentials work and whether the mailbox has any mail."""
        try:
            messages = self._fetch_via_graph(account)
            return {
                "ok": True,
                "messages": messages,
                "message_count": len(messages),
                "oreate_candidates": sum(
                    1
                    for message in messages
                    if "oreate" in _message_blobs(message).lower()
                    or "oreate" in str(message.get("subject") or "").lower()
                ),
                "error": "",
            }
        except Exception as exc:
            return {
                "ok": False,
                "messages": [],
                "message_count": 0,
                "oreate_candidates": 0,
                "error": str(exc),
            }

    def fetch_latest_message(self, account: Mapping[str, Any], folders: Sequence[str] = ("INBOX", "Junk")) -> Dict[str, Any]:
        mode = self.api_mode
        errors: List[str] = []
        # Prefer Graph in auto mode: shop APIs are frequently missing for card pools.
        if mode in ("auto", "graph", "microsoft", "msgraph"):
            try:
                messages = self._fetch_via_graph(account)
                if messages:
                    for message in messages:
                        blob = _message_blobs(message)
                        if "oreateai.com" in blob.lower() or "oreate" in str(message.get("subject") or "").lower():
                            return message
                    return messages[0]
            except Exception as exc:
                errors.append(f"graph:{exc}")
                if mode != "auto":
                    raise
        if mode in ("auto", "get"):
            try:
                message = self._fetch_via_get(account)
                if message:
                    return message
            except Exception as exc:
                errors.append(f"get:{exc}")
                if mode == "get":
                    raise
        if mode in ("auto", "msoauth2", "msOauth2", "oauth2"):
            for folder in folders:
                try:
                    message = self._fetch_via_msoauth2(account, folder)
                    if message:
                        return message
                except Exception as exc:
                    errors.append(f"{folder}:{exc}")
            if mode not in ("auto",) and str(mode).lower().startswith("mso"):
                raise RuntimeError("outlook msoauth2 fetch failed: " + " | ".join(errors[:6]))
        if errors:
            raise RuntimeError("outlook mail fetch failed: " + " | ".join(errors[:6]))
        return {}

    def fetch_candidate_messages(self, account: Mapping[str, Any]) -> List[Dict[str, Any]]:
        mode = self.api_mode
        if mode in ("auto", "graph", "microsoft", "msgraph"):
            # Graph success with an empty mailbox must NOT fall back to shop APIs.
            # Shop 404s previously masked the real "mail not arrived" condition.
            try:
                return self._fetch_via_graph(account)
            except Exception:
                if mode != "auto":
                    raise
        latest = self.fetch_latest_message(account)
        return [latest] if latest else []

    def _extract_artifact(self, message: Mapping[str, Any]) -> Dict[str, str]:
        blob = _message_blobs(message)
        subject = str(message.get("subject") or message.get("title") or "")
        link = self._extract.extract_verify_link({"text": blob, "html": blob, "subject": subject})
        code = str(message.get("code") or "").strip()
        if code and not re.fullmatch(r"\d{4,8}", code):
            code = ""
        # Prefer Oreate activation links; ignore unrelated OTP codes (e.g. ChatGPT).
        if not code and ("oreate" in blob.lower() or "oreate" in subject.lower()):
            code = self._extract.extract_verify_code({"text": blob, "html": blob})
        if link or code:
            return {
                "message_id": str(message.get("id") or message.get("message_id") or "latest"),
                "link": link,
                "code": code,
                "receivedDateTime": str(message.get("receivedDateTime") or ""),
            }
        return {}

    @staticmethod
    def _message_received_ts(message: Mapping[str, Any]) -> float:
        raw = str(message.get("receivedDateTime") or "").strip()
        if not raw:
            return 0.0
        try:
            normalized = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return 0.0

    def _int_cfg(self, key: str, default: int) -> int:
        try:
            return max(1, int(self._cfg().get(key, default) or default))
        except Exception:
            return default

    def wait_verification_artifact(
        self,
        address: str,
        token: str,
        timeout_sec: int = 300,
        *,
        not_before: Optional[float] = None,
        exclude_token_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, str]:
        account = self._resolve_mailbox(str(token))
        if str(account.get("email") or "").lower() != str(address or "").lower():
            raise RuntimeError("outlook mailbox token/email mismatch")
        started = time.time()
        deadline = started + max(1, int(timeout_sec))
        # Empty inboxes rarely receive Oreate mail later; fail fast instead of sitting on "验邮".
        empty_fail_after = min(
            max(15, self._int_cfg("empty_mailbox_fail_after_sec", 75)),
            max(1, int(timeout_sec)),
        )
        # Mailbox has old mail / unrelated mail but no fresh Oreate activation letter.
        no_fresh_fail_after = min(
            max(20, self._int_cfg("no_fresh_mail_fail_after_sec", 90)),
            max(1, int(timeout_sec)),
        )
        # Bad refresh_token/client_id should not burn the full verification window.
        credential_fail_after = min(
            max(5, self._int_cfg("credential_fail_after_sec", 20)),
            max(1, int(timeout_sec)),
        )
        last_error = ""
        seen_fingerprints = set()
        excluded = {str(item).strip().lower() for item in (exclude_token_ids or []) if str(item).strip()}
        # Default: ignore mail that already existed before this wait cycle.
        # Allow modest clock skew between local host and Graph timestamps.
        min_ts = float(not_before) if not_before is not None else (started - 60.0)
        graph_ok_seen = False
        max_message_count = 0
        stale_oreate_count = 0
        credential_error = ""
        credential_fail_since: Optional[float] = None
        while time.time() < deadline:
            messages: List[Dict[str, Any]] = []
            try:
                mode = self.api_mode
                if mode in ("auto", "graph", "microsoft", "msgraph"):
                    probe = self.probe_graph_mailbox(account)
                    if probe.get("ok"):
                        graph_ok_seen = True
                        credential_fail_since = None
                        messages = list(probe.get("messages") or [])
                        max_message_count = max(max_message_count, len(messages))
                        credential_error = ""
                    else:
                        credential_error = str(probe.get("error") or "graph probe failed")
                        last_error = credential_error
                        if credential_fail_since is None:
                            credential_fail_since = time.time()
                        # Shop fallback only when Graph credentials themselves failed.
                        if mode == "auto":
                            messages = self.fetch_candidate_messages(account)
                        elif time.time() - credential_fail_since >= credential_fail_after:
                            raise RuntimeError(
                                "outlook verification timeout: graph credentials invalid "
                                f"({credential_error[:240]})"
                            )
                else:
                    messages = self.fetch_candidate_messages(account)
                    max_message_count = max(max_message_count, len(messages))

                for message in messages:
                    received_ts = self._message_received_ts(message)
                    blob = _message_blobs(message)
                    subject = str(message.get("subject") or message.get("title") or "")
                    is_oreate = "oreate" in blob.lower() or "oreate" in subject.lower()
                    if received_ts and received_ts < min_ts:
                        if is_oreate:
                            stale_oreate_count += 1
                        continue
                    fingerprint = blob[:1000]
                    if not fingerprint or fingerprint in seen_fingerprints:
                        continue
                    seen_fingerprints.add(fingerprint)
                    artifact = self._extract_artifact(message)
                    if not artifact:
                        continue
                    link = str(artifact.get("link") or "")
                    token_id = ""
                    if link:
                        token_id = (
                            parse_qs(urlparse(html.unescape(link).replace("&amp;", "&")).query).get("tokenID") or [""]
                        )[0]
                    if token_id and token_id.lower() in excluded:
                        continue
                    return artifact

                elapsed = time.time() - started
                # Graph can read the mailbox, but it stays empty => card won't get activation mail.
                if graph_ok_seen and max_message_count == 0 and elapsed >= empty_fail_after:
                    raise RuntimeError(
                        "outlook verification timeout: graph ok but mailbox empty "
                        f"(no mail after {int(elapsed)}s; activation mail not arrived)"
                    )
                # Graph works and inbox has mail, but nothing fresh/usable after signup+resend.
                if graph_ok_seen and max_message_count > 0 and elapsed >= no_fresh_fail_after:
                    raise RuntimeError(
                        "outlook verification timeout: graph ok but no fresh Oreate activation mail "
                        f"(scanned {max_message_count} messages, stale_oreate={stale_oreate_count}, "
                        f"waited {int(elapsed)}s)"
                    )
                # Graph credentials keep failing in auto mode (shop fallback also empty).
                if (
                    credential_fail_since is not None
                    and not graph_ok_seen
                    and not messages
                    and time.time() - credential_fail_since >= credential_fail_after
                ):
                    raise RuntimeError(
                        "outlook verification timeout: graph credentials invalid "
                        f"({credential_error[:240]})"
                    )
            except RuntimeError:
                raise
            except Exception as exc:
                last_error = str(exc)
                lowered = last_error.lower()
                if "graph token failed" in lowered or "credentials missing" in lowered:
                    credential_error = last_error
                    if credential_fail_since is None:
                        credential_fail_since = time.time()
            elapsed = time.time() - started
            time.sleep(8.0 if elapsed >= 60 else 5.0)

        if credential_error and not graph_ok_seen:
            raise RuntimeError(
                "outlook verification timeout: graph credentials invalid "
                f"({credential_error[:240]})"
            )
        if graph_ok_seen and max_message_count == 0:
            raise RuntimeError(
                "outlook verification timeout: graph ok but mailbox empty "
                "(activation mail not arrived or delayed)"
            )
        if graph_ok_seen:
            raise RuntimeError(
                "outlook verification timeout: graph ok but no Oreate activation mail "
                f"(scanned {max_message_count} messages across inbox/junk/clutter)"
            )
        suffix = f" ({last_error})" if last_error else ""
        raise RuntimeError(f"outlook verification artifact timeout{suffix}")

    def finish_mailbox(self, token: str, status: str, error: str = "") -> None:
        if self._finish_mailbox is None:
            return
        self._finish_mailbox(str(token), str(status), str(error or ""))

    def test_connectivity(self) -> Dict[str, Any]:
        return {
            "provider": "outlook",
            "base_url": self.base,
            "api_mode": self.api_mode,
            "has_api_key": bool(self.api_key),
            "ok": bool(self.base) or self.api_mode in {"graph", "microsoft", "msgraph", "auto"},
        }


class MailRouter:
    """Dispatch mail operations to YYDS or Outlook based on config.provider."""

    def __init__(self, mail_config: MailConfigFn, yyds: YydsClient, outlook: OutlookMailClient):
        self._mail_config = mail_config
        self.yyds = yyds
        self.outlook = outlook

    def _provider(self) -> str:
        value = str(self._mail_config().get("provider") or "yyds").strip().lower()
        if value in {"outlook", "out", "hotmail", "msoauth2", "oauth2"}:
            return "outlook"
        return "yyds"

    def active(self) -> Any:
        return self.outlook if self._provider() == "outlook" else self.yyds

    def create_mailbox(self) -> Dict[str, Any]:
        return self.active().create_mailbox()

    def wait_verification_artifact(
        self,
        address: str,
        token: str,
        timeout_sec: int = 180,
        **kwargs: Any,
    ) -> Dict[str, str]:
        return self.active().wait_verification_artifact(address, token, timeout_sec=timeout_sec, **kwargs)

    def test_connectivity(self) -> Dict[str, Any]:
        provider = self._provider()
        result = self.active().test_connectivity()
        if isinstance(result, dict):
            result = dict(result)
            result["provider"] = provider
        return result

    def finish_mailbox(self, token: str, status: str, error: str = "") -> None:
        if self._provider() != "outlook":
            return
        self.outlook.finish_mailbox(token, status, error)
