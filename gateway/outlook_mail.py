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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from urllib.parse import parse_qs, unquote, urlparse

import requests

from gateway.yyds_mail import YydsClient

MailConfigFn = Callable[[], Mapping[str, Any]]
ClaimMailboxFn = Callable[[], Dict[str, Any]]
ResolveMailboxFn = Callable[[str], Dict[str, Any]]
FinishMailboxFn = Callable[[str, str, str], None]

_GRAPH_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default offline_access"
_SHOP_ERROR_RE = re.compile(r"(账号不存在|取件失败|密钥错误|缺少email|not\s*found|unauthorized)", re.I)


def _mail_session() -> requests.Session:
    session = requests.Session()
    # Local HTTP_PROXY can break shop-card hosts / Graph; talk to endpoints directly.
    session.trust_env = False
    return session


def _mail_http_get(url: str, params: Mapping[str, Any], timeout: int = 45) -> requests.Response:
    return _mail_session().get(url, params=dict(params), timeout=timeout)


def parse_outlook_import_line(line: str) -> Optional[Dict[str, str]]:
    raw = str(line or "").strip()
    if not raw or raw.startswith("#"):
        return None

    detected_base = ""
    detected_key = ""
    payload = raw

    if "://" in raw and ("email=" in raw or "/get" in raw):
        parsed = urlparse(raw)
        if parsed.scheme and parsed.netloc:
            detected_base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        qs = parse_qs(parsed.query)
        if qs.get("key"):
            detected_key = unquote(str(qs["key"][0] or "")).strip()
        if qs.get("email"):
            payload = unquote(str(qs["email"][0] or "")).strip()

    parts = payload.split("----")
    if len(parts) < 4:
        return None
    email, password, client_id = parts[0].strip(), parts[1].strip(), parts[2].strip()
    refresh_token = "----".join(parts[3:]).strip().rstrip("&?")
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

    def _fetch_via_graph(self, account: Mapping[str, Any], folders: Sequence[str] = ("inbox", "junkemail")) -> List[Dict[str, Any]]:
        access = self._graph_access_token(account)
        headers = {"Authorization": f"Bearer {access}"}
        messages: List[Dict[str, Any]] = []
        errors: List[str] = []
        for folder in folders:
            folder_key = str(folder or "").strip().lower()
            if folder_key in {"inbox", "mailinbox", ""}:
                url = (
                    "https://graph.microsoft.com/v1.0/me/messages"
                    "?$top=12&$select=id,subject,bodyPreview,body,receivedDateTime"
                    "&$orderby=receivedDateTime desc"
                )
            elif folder_key in {"junk", "junkemail", "junkemailmessages"}:
                url = (
                    "https://graph.microsoft.com/v1.0/me/mailFolders/junkemail/messages"
                    "?$top=12&$select=id,subject,bodyPreview,body,receivedDateTime"
                    "&$orderby=receivedDateTime desc"
                )
            else:
                url = (
                    f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
                    "?$top=12&$select=id,subject,bodyPreview,body,receivedDateTime"
                    "&$orderby=receivedDateTime desc"
                )
            r = _mail_session().get(url, headers=headers, timeout=45)
            try:
                body = r.json()
            except Exception:
                body = {}
            if r.status_code >= 400:
                err = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else (r.text or "")[:160]
                errors.append(f"{folder}: HTTP {r.status_code} {err}")
                continue
            for item in body.get("value") or []:
                if isinstance(item, dict):
                    messages.append(graph_message_to_payload(item))
        if not messages and errors:
            raise RuntimeError("outlook graph fetch failed: " + " | ".join(errors[:4]))
        return messages

    def fetch_latest_message(self, account: Mapping[str, Any], folders: Sequence[str] = ("INBOX", "Junk")) -> Dict[str, Any]:
        mode = self.api_mode
        errors: List[str] = []
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
            if mode not in ("auto",) and mode.startswith("mso"):
                raise RuntimeError("outlook msoauth2 fetch failed: " + " | ".join(errors[:6]))
        if mode in ("auto", "graph", "microsoft", "msgraph"):
            try:
                messages = self._fetch_via_graph(account)
                if messages:
                    # Prefer Oreate verification mail over unrelated latest mail.
                    for message in messages:
                        blob = _message_blobs(message)
                        if "oreateai.com" in blob.lower() or "oreate" in str(message.get("subject") or "").lower():
                            return message
                    return messages[0]
            except Exception as exc:
                errors.append(f"graph:{exc}")
                if mode != "auto":
                    raise
        if errors:
            raise RuntimeError("outlook mail fetch failed: " + " | ".join(errors[:6]))
        return {}

    def fetch_candidate_messages(self, account: Mapping[str, Any]) -> List[Dict[str, Any]]:
        mode = self.api_mode
        if mode in ("auto", "graph", "microsoft", "msgraph"):
            try:
                messages = self._fetch_via_graph(account)
                if messages:
                    return messages
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

    def wait_verification_artifact(
        self,
        address: str,
        token: str,
        timeout_sec: int = 180,
        *,
        not_before: Optional[float] = None,
        exclude_token_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, str]:
        account = self._resolve_mailbox(str(token))
        if str(account.get("email") or "").lower() != str(address or "").lower():
            raise RuntimeError("outlook mailbox token/email mismatch")
        deadline = time.time() + timeout_sec
        last_error = ""
        seen_fingerprints = set()
        excluded = {str(item).strip().lower() for item in (exclude_token_ids or []) if str(item).strip()}
        # Default: ignore mail that already existed before this wait cycle.
        min_ts = float(not_before) if not_before is not None else (time.time() - 15.0)
        while time.time() < deadline:
            try:
                messages = self.fetch_candidate_messages(account)
                for message in messages:
                    received_ts = self._message_received_ts(message)
                    if received_ts and received_ts < min_ts:
                        continue
                    blob = _message_blobs(message)
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
                        from urllib.parse import parse_qs, urlparse

                        token_id = (parse_qs(urlparse(html.unescape(link).replace("&amp;", "&")).query).get("tokenID") or [""])[0]
                    if token_id and token_id.lower() in excluded:
                        continue
                    # If Graph omitted receivedDateTime, only accept after first poll cycle
                    # when fingerprint is newly observed during this wait.
                    return artifact
            except Exception as exc:
                last_error = str(exc)
            time.sleep(5)
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
