"""Upstream Oreate HTTP client and session model."""

from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional
from urllib.parse import quote

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from banti_token_generator import generate_banti_artifacts, generate_jt_token
from gateway.media_utils import (
    IMAGE_UPLOAD_EXTENSIONS,
    VIDEO_UPLOAD_EXTENSIONS,
    first_upload_key_entry,
    is_media_upload_extension,
    normalized_file_extension,
    parse_mp4_video_metadata,
    response_data_object,
)
from gateway.oreate_stream import (
    classify_history_error,
    classify_sse_error,
    extract_generation_assets,
    parse_sse_line,
)

OreateConfigFn = Callable[[], Mapping[str, Any]]
DecryptSecretFn = Callable[..., str]
TlsVerifyFn = Callable[[], bool]

_DEFAULT_OREATE_CONFIG: Optional[OreateConfigFn] = None
_DEFAULT_DECRYPT_SECRET: Optional[DecryptSecretFn] = None
_DEFAULT_TLS_VERIFY: Optional[TlsVerifyFn] = None


def configure_oreate_client_defaults(
    oreate_config: OreateConfigFn,
    *,
    decrypt_secret: DecryptSecretFn,
    tls_verify: TlsVerifyFn,
) -> None:
    global _DEFAULT_OREATE_CONFIG, _DEFAULT_DECRYPT_SECRET, _DEFAULT_TLS_VERIFY
    _DEFAULT_OREATE_CONFIG = oreate_config
    _DEFAULT_DECRYPT_SECRET = decrypt_secret
    _DEFAULT_TLS_VERIFY = tls_verify


@dataclass
class OreateSession:
    email: str
    password: str
    cookies: Dict[str, str]
    ticket_id: str = ""
    fr: str = "main"
    signup_response: Optional[Dict[str, Any]] = None
    signup_payload: Optional[Dict[str, Any]] = None


class OreateClient:
    def __init__(
        self,
        oreate_config: Optional[OreateConfigFn] = None,
        *,
        decrypt_secret: Optional[DecryptSecretFn] = None,
        tls_verify: Optional[TlsVerifyFn] = None,
    ):
        resolved_config = oreate_config or _DEFAULT_OREATE_CONFIG
        resolved_decrypt = decrypt_secret or _DEFAULT_DECRYPT_SECRET
        resolved_tls = tls_verify or _DEFAULT_TLS_VERIFY
        if resolved_config is None or resolved_decrypt is None or resolved_tls is None:
            raise TypeError(
                "OreateClient requires oreate_config/decrypt_secret/tls_verify "
                "or configure_oreate_client_defaults() first"
            )
        self._oreate_config = resolved_config
        self._decrypt_secret = resolved_decrypt
        self._tls_verify = resolved_tls

    def _cfg(self) -> Mapping[str, Any]:
        return self._oreate_config()

    @property
    def base(self) -> str:
        return str(self._cfg().get("base_url") or "").rstrip("/")

    @property
    def timeout(self) -> Any:
        return self._cfg().get("request_timeout", 30)

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "accept": "application/json, text/plain, */*",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "origin": self.base,
            "referer": f"{self.base}/home/vertical/aiImage",
            "locale": "zh-CN",
            "client-type": "pc",
            "pragma": "no-cache",
            "cache-control": "no-cache, no-store",
        }

    def _headers_for(
        self,
        chat_type: str = "",
        accept: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> Dict[str, str]:
        headers = dict(self.headers)
        if chat_type == "aiVideo":
            headers["referer"] = f"{self.base}/home/vertical/aiVideo/zh"
        elif chat_type == "aiImage":
            headers["referer"] = f"{self.base}/home/vertical/aiImage"
        if accept:
            headers["accept"] = accept
        if content_type:
            headers["content-type"] = content_type
        headers.pop("client-type", None)
        headers["Client-Type"] = "pc"
        return headers

    def _stream_timeout(self, is_video: bool) -> Any:
        if not is_video:
            return self.timeout
        try:
            base_timeout = float(self.timeout)
        except (TypeError, ValueError):
            base_timeout = 30.0
        read_timeout = float(self._cfg().get("video_stream_read_timeout_seconds") or min(base_timeout, 20.0))
        return (self.timeout, read_timeout)

    def new_session(self) -> requests.Session:
        s = requests.Session()
        s.verify = self._tls_verify()
        s.get(self.base + "/", headers=self.headers, timeout=self.timeout)
        return s

    def get_ticket(self, s: requests.Session) -> Dict[str, Any]:
        r = s.get(self.base + "/passport/api/getticket", headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        body = r.json()
        if body.get("status", {}).get("code") != 0:
            raise RuntimeError(f"getticket failed: {body}")
        return body["data"]

    def get_jt_probe(self, s: requests.Session, subid: str = "") -> Dict[str, Any]:
        payload = {
            "subid": subid,
            "ts": f"{int(time.time() * 1000)}_{secrets.randbelow(10**10)}",
            "r": secrets.token_hex(3),
            "v": "1.0",
            "d": "",
        }
        results = []
        for path in ("/cdr", "/dr"):
            url = "https://banti.oreateai.com" + path + "?_o=https%3A%2F%2Fwww.oreateai.com"
            try:
                r = s.post(url, json=payload, headers={**self.headers, "content-type": "application/json"}, timeout=self.timeout)
                text = r.text[:2000]
                try:
                    body = r.json()
                except Exception:
                    body = {"raw": text}
                results.append({"path": path, "status": r.status_code, "body": body, "text": text})
            except Exception as e:
                results.append({"path": path, "error": str(e)})
        return {"payload": payload, "results": results}

    def encrypt_password(self, pk_pem: str, password: str) -> str:
        pub = serialization.load_pem_public_key(pk_pem.encode())
        enc = pub.encrypt(password.encode(), padding.PKCS1v15())
        return base64.b64encode(enc).decode()

    def signup_attempt(self, email: str, password: str, jt: Any = None) -> Dict[str, Any]:
        s = self.new_session()
        ticket = self.get_ticket(s)
        enc_password = self.encrypt_password(ticket["pk"], password)
        jt_token = jt if jt is not None else generate_jt_token()
        payload = {
            "email": email,
            "password": enc_password,
            "ticketID": ticket["ticketID"],
            "fr": self._cfg()["default_fr"],
            "jt": jt_token,
        }
        r = s.post(
            self.base + "/passport/api/emailsignupin",
            headers={**self.headers, "content-type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:2000]}
        return {
            "status_code": r.status_code,
            "ticket": ticket,
            "payload": payload,
            "response": body,
            "cookies": s.cookies.get_dict(),
        }

    def check_email_verified(self, email: str, ticket_id: str) -> Dict[str, Any]:
        r = requests.post(
            self.base + "/passport/api/checkemailverified",
            headers={**self.headers, "content-type": "application/json"},
            json={"email": email, "ticketID": ticket_id},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def resend_confirm_email(self, email: str) -> Dict[str, Any]:
        r = requests.post(
            self.base + "/passport/api/resendconfirmemail",
            headers={**self.headers, "content-type": "application/json"},
            json={"email": email, "fr": self._cfg()["default_fr"]},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def confirm_email_register(self, email: str, token_id: str, ticket_id: str, password: str) -> Dict[str, Any]:
        s = self.new_session()
        ticket = self.get_ticket(s)
        enc_password = self.encrypt_password(ticket["pk"], password)
        r = s.post(
            self.base + "/passport/api/emailregisterconfirm",
            headers={**self.headers, "content-type": "application/json"},
            json={
                "email": email,
                "tokenID": token_id,
                "ticketID": ticket_id,
                "password": enc_password,
                "jt": generate_jt_token(),
                "fr": self._cfg()["default_fr"],
            },
            timeout=self.timeout,
        )
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:2000]}
        return {"status_code": r.status_code, "response": body, "cookies": s.cookies.get_dict()}

    def login(self, email: str, password: str) -> OreateSession:
        s = self.new_session()
        ticket = self.get_ticket(s)
        enc_password = self.encrypt_password(ticket["pk"], password)
        payload = {
            "email": email,
            "password": enc_password,
            "ticketID": ticket["ticketID"],
            "fr": self._cfg()["default_fr"],
            "jt": generate_jt_token(),
        }
        r = s.post(
            self.base + "/passport/api/emaillogin",
            headers={**self.headers, "content-type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("status", {}).get("code") != 0:
            raise RuntimeError(f"emaillogin failed: {body}")
        return OreateSession(email=email, password=password, cookies=s.cookies.get_dict())

    def session_from_account(self, account: sqlite3.Row) -> requests.Session:
        s = self.new_session()
        ouid = self._decrypt_secret(account["ouid"], required=True)
        ouss = self._decrypt_secret(account["ouss"], required=True)
        if ouid:
            self._set_cookie_unique(s, "OUID", ouid)
        if ouss:
            self._set_cookie_unique(s, "ouss", ouss)
        return s

    def session_from_cookie_dict(self, cookies: Dict[str, str]) -> requests.Session:
        s = self.new_session()
        if cookies.get("OUID"):
            self._set_cookie_unique(s, "OUID", cookies["OUID"])
        if cookies.get("ouss"):
            self._set_cookie_unique(s, "ouss", cookies["ouss"])
        return s

    def fetch_image_models(self, s: requests.Session) -> Dict[str, Any]:
        r = s.get(self.base + "/oreate/img/getmodelconfig", headers=self._headers_for("aiImage"), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def fetch_video_models(self, s: requests.Session) -> Dict[str, Any]:
        r = s.get(self.base + "/oreate/aivideo/getmodelconfigv3", headers=self._headers_for("aiVideo"), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def fetch_video_scenes(self, s: requests.Session) -> Dict[str, Any]:
        r = s.get(self.base + "/oreate/aivideo/getsceneconfig", headers=self._headers_for("aiVideo"), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def fetch_user_mirror_metadata(self, s: requests.Session, account: Optional[sqlite3.Row] = None) -> Dict[str, Any]:
        fallback_email = account["email"] if account is not None and "email" in account.keys() else ""
        try:
            r = s.get(self.base + "/oreate/user/getuserinfo", headers=self.headers, timeout=self.timeout)
            r.raise_for_status()
            return extract_user_mirror_metadata(r.json(), fallback_email)
        except Exception:
            return {"email": fallback_email, "vip": "", "reg_ts": ""}

    def fetch_account_point_detail(self, s: requests.Session, account: Optional[sqlite3.Row] = None) -> Dict[str, Any]:
        try:
            r = s.get(self.base + "/oreate/account/getpointdetail", headers=self.headers, timeout=self.timeout)
            r.raise_for_status()
            body = r.json()
            status = body.get("status") if isinstance(body, dict) else None
            if isinstance(status, dict) and status.get("code") not in (None, 0):
                raise RuntimeError(f"getpointdetail failed: {body}")
            return body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else body
        except Exception as exc:
            fallback = account["email"] if account is not None and "email" in account.keys() else ""
            raise RuntimeError(f"getpointdetail failed for {fallback or 'account'}: {exc}") from exc

    def upload_file_bytes(
        self,
        s: requests.Session,
        filename: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> Dict[str, Any]:
        safe_name = Path(filename or "upload.bin").name
        path = Path(safe_name)
        file_ext = normalized_file_extension(path.suffix)
        file_name = path.stem or "upload"
        upload_payload = {
            "mFileList": [
                {
                    "filename": file_name,
                    "fileExt": file_ext,
                    "size": len(data),
                }
            ]
        }
        is_media_upload = is_media_upload_extension(file_ext)
        if is_media_upload:
            upload_payload["source"] = "aiImage"
        token_response = s.post(
            self.base + "/oreate/convert/getuploadbostoken",
            headers={**self.headers, "content-type": "application/json"},
            json=upload_payload,
            timeout=self.timeout,
        )
        token_response.raise_for_status()
        token_body = token_response.json()
        token_data = response_data_object(token_body)
        key_list = (token_data or {}).get("KeyList") or (token_data or {}).get("keyList") or []
        key = first_upload_key_entry(key_list)
        if not key:
            raise RuntimeError(f"upload token response missing KeyList: {token_body}")
        bucket = key.get("bucket") or key.get("Bucket") or ""
        object_path = key.get("objectPath") or key.get("object") or key.get("bosObjectPath") or key.get("key") or ""
        session_key = key.get("sessionkey") or key.get("sessionKey") or key.get("accessToken") or key.get("token") or ""
        if not bucket or not object_path or not session_key:
            raise RuntimeError("upload token response missing bucket, objectPath, or sessionkey")

        upload_type = content_type or "application/octet-stream"
        init_url = (
            "https://storage.googleapis.com/upload/storage/v1/b/"
            f"{quote(str(bucket), safe='')}/o?uploadType=resumable&name={quote(str(object_path), safe='')}"
        )
        init_response = requests.post(
            init_url,
            headers={
                "authorization": f"Bearer {session_key}",
                "x-upload-content-type": upload_type,
                "x-upload-content-length": str(len(data)),
                "content-length": "0",
            },
            timeout=self.timeout,
        )
        init_response.raise_for_status()
        upload_location = init_response.headers.get("Location") or init_response.headers.get("location")
        if not upload_location:
            raise RuntimeError("resumable upload did not return Location")
        put_response = requests.put(
            upload_location,
            headers={
                "authorization": f"Bearer {session_key}",
                "content-type": upload_type,
                "content-length": str(len(data)),
            },
            data=data,
            timeout=self.timeout,
        )
        put_response.raise_for_status()
        attachment = {
            "fileName": file_name,
            "fileExt": file_ext,
            "originSize": len(data),
            "contentType": upload_type,
            "bucket": bucket,
            "object": object_path,
            "bosUrl": object_path,
            "bosObjectPath": object_path,
            "status": "completed",
        }
        if file_ext in VIDEO_UPLOAD_EXTENSIONS:
            attachment.update(parse_mp4_video_metadata(data))
        if file_ext in IMAGE_UPLOAD_EXTENSIONS:
            convert_payload = {
                "fileName": f"{file_name}.{file_ext}" if file_ext else file_name,
                "fileExt": file_ext,
                "fileSize": len(data),
                "needEdit": False,
                "bucket": bucket,
                "object": object_path,
            }
            convert_response = s.post(
                self.base + "/oreate/convert/submit",
                headers={**self.headers, "content-type": "application/json"},
                json=convert_payload,
                timeout=self.timeout,
            )
            convert_response.raise_for_status()
            convert_body = convert_response.json()
            status = convert_body.get("status") if isinstance(convert_body, dict) else None
            if isinstance(status, dict) and status.get("code") not in (None, 0):
                raise RuntimeError(f"convert submit failed: {status}")
            convert_data = response_data_object(convert_body)
            doc_id = convert_data.get("docId") or convert_data.get("docID")
            if doc_id:
                attachment["docId"] = doc_id
            if "parseInfo" in convert_data:
                attachment["parseInfo"] = convert_data.get("parseInfo")
        return attachment

    def _set_cookie_unique(self, s: requests.Session, name: str, value: str) -> None:
        cookies = getattr(s, "cookies", {})
        if isinstance(cookies, dict):
            cookies[name] = value
            return
        for cookie in list(cookies):
            if cookie.name == name:
                cookies.clear(cookie.domain, cookie.path, cookie.name)
        cookies.set(name, value)

    def _cookie_value(self, s: requests.Session, name: str) -> str:
        cookies = getattr(s, "cookies", {})
        if hasattr(cookies, "get"):
            try:
                return cookies.get(name) or ""
            except Exception:
                for cookie in reversed(list(cookies)):
                    if getattr(cookie, "name", "") == name:
                        return getattr(cookie, "value", "") or ""
        return ""

    def create_chat_session(self, s: requests.Session, chat_type: str) -> Dict[str, Any]:
        r = s.post(
            self.base + "/oreate/create/chat",
            headers=self._headers_for(chat_type, content_type="application/json"),
            json={"type": chat_type, "docId": ""},
            timeout=self.timeout,
        )
        r.raise_for_status()
        body = r.json()
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        chat_id = (data or {}).get("chatId") or ""
        focus_id = (data or {}).get("focusId") or chat_id
        if not chat_id:
            raise RuntimeError(f"create_chat_session missing chatId: {body}")
        return {"chatId": chat_id, "focusId": focus_id, "raw": body}

    def stream_generation(
        self,
        s: requests.Session,
        chat_id: str,
        focus_id: str,
        chat_type: str,
        prompt: str,
        image_config: Optional[Dict[str, Any]] = None,
        video_config: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        account: Optional[sqlite3.Row] = None,
        jt: Optional[str] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        banti_artifacts: Dict[str, Any] = {"jt": jt or "", "cookies": {}}
        if jt is None:
            bid = ""
            for _ in range(3):
                banti_artifacts = generate_banti_artifacts()
                helper_cookies = banti_artifacts.get("cookies") if isinstance(banti_artifacts.get("cookies"), dict) else {}
                bid = helper_cookies.get("__bid_n") or ""
                if banti_artifacts.get("jt") and bid:
                    break
            if not banti_artifacts.get("jt") or not bid:
                raise RuntimeError("banti mirror artifacts unavailable for generation")
            self._set_cookie_unique(s, "__bid_n", str(bid))
        mirror = self.fetch_user_mirror_metadata(s, account) if account is not None else {"email": "", "vip": "", "reg_ts": ""}
        extra: Dict[str, Any] = {
            "doc_name": "",
            "module_name": "gpt4o",
            "email": mirror.get("email") or "",
            "vip": mirror.get("vip") or "",
            "reg_ts": mirror.get("reg_ts") or "",
            "deviceID": self._cookie_value(s, "OUID"),
            "bid": self._cookie_value(s, "__bid_n"),
        }
        body: Dict[str, Any] = {
            "type": "chat",
            "focusId": focus_id or chat_id,
            "chatId": chat_id,
            "chatType": chat_type,
            "from": "home",
            "chatTitle": "Unnamed Session",
            "messages": [{"role": "user", "content": prompt, "attachments": attachments or []}],
            "isFirst": True,
            "extra": extra,
            "clientType": "pc",
            "jt": banti_artifacts["jt"],
            "ua": self.headers["user-agent"],
            "js_env": "h5",
        }
        if image_config is not None:
            body["imageConfig"] = image_config
        if video_config is not None:
            body["videoConfig"] = video_config
        is_video = chat_type == "aiVideo" or video_config is not None
        events: List[Dict[str, Any]] = []
        completion_reason = "eof"
        response = None
        stream_wait = float(self._cfg().get("video_stream_wait_seconds") or 60)
        deadline = time.monotonic() + max(0.0, stream_wait) if is_video else None
        try:
            response = s.post(
                self.base + "/oreate/sse/stream",
                headers=self._headers_for(chat_type, accept="text/event-stream", content_type="application/json"),
                json=body,
                timeout=self._stream_timeout(is_video),
                stream=True,
            )
            response.raise_for_status()
            for raw in response.iter_lines(decode_unicode=True):
                if should_stop is not None and should_stop():
                    completion_reason = "cancelled"
                    break
                event = parse_sse_line(raw)
                if event is None:
                    continue
                events.append(event)
                if event.get("event") == "end":
                    completion_reason = "end"
                    break
                if classify_sse_error([event]):
                    completion_reason = "error"
                    break
                if is_video and deadline is not None and time.monotonic() >= deadline:
                    completion_reason = "video_stream_wait_elapsed"
                    break
        except (requests.exceptions.ReadTimeout, requests.exceptions.Timeout):
            if not (is_video and events and not classify_sse_error(events)):
                raise
            completion_reason = "read_timeout"
        except requests.exceptions.ConnectionError as exc:
            if not (is_video and events and "read timed out" in str(exc).lower() and not classify_sse_error(events)):
                raise
            completion_reason = "read_timeout"
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()
        error = classify_sse_error(events)
        if error:
            status = "failed"
        elif completion_reason == "cancelled":
            status = "cancelled"
        elif is_video and events and completion_reason in ("read_timeout", "video_stream_wait_elapsed", "eof"):
            status = "submitted"
        else:
            status = "streamed"
        return {
            "events": events,
            "error": error,
            "status": status,
            "completion_reason": completion_reason,
        }

    def hydrate_generation_result(self, s: requests.Session, chat_id: str, chat_type: str = "") -> Dict[str, Any]:
        r = s.get(
            self.base + "/oreate/memory/getmessagelist",
            headers=self._headers_for(chat_type),
            params={"pn": 1, "rn": 30, "chatID": chat_id},
            timeout=self.timeout,
        )
        r.raise_for_status()
        body = r.json()
        return {"raw": body, "assets": extract_generation_assets(body)}

    def hydrate_generation_result_until_assets(
        self,
        s: requests.Session,
        chat_id: str,
        timeout_sec: Optional[float] = None,
        poll_interval_sec: Optional[float] = None,
        chat_type: str = "aiVideo",
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        timeout = float(
            self._cfg().get("video_hydration_timeout_seconds") or 600
            if timeout_sec is None
            else timeout_sec
        )
        interval = float(
            self._cfg().get("video_hydration_poll_interval_seconds") or 10
            if poll_interval_sec is None
            else poll_interval_sec
        )
        deadline = time.monotonic() + max(0.0, timeout)
        attempts = 0
        last_result: Dict[str, Any] = {"raw": {}, "assets": []}
        while True:
            if should_stop is not None and should_stop():
                last_result["status"] = "cancelled"
                last_result["attempts"] = attempts
                return last_result
            attempts += 1
            last_result = self.hydrate_generation_result(s, chat_id, chat_type=chat_type)
            assets = last_result.get("assets") or []
            last_result["attempts"] = attempts
            if assets:
                last_result["status"] = "completed"
                return last_result
            if should_stop is not None and should_stop():
                last_result["status"] = "cancelled"
                return last_result
            history_error = classify_history_error(last_result.get("raw"), ignored_codes=["110012"])
            if history_error:
                last_result["status"] = "failed"
                last_result["error"] = history_error
                return last_result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_result["status"] = "submitted"
                return last_result
            sleep_for = min(max(interval, 0.0), remaining)
            if should_stop is None or sleep_for <= 0:
                time.sleep(sleep_for)
                continue
            sleep_deadline = time.monotonic() + sleep_for
            while True:
                if should_stop():
                    last_result["status"] = "cancelled"
                    return last_result
                remaining_sleep = sleep_deadline - time.monotonic()
                if remaining_sleep <= 0:
                    break
                time.sleep(min(0.5, remaining_sleep))

    def create_chat(self, s: requests.Session, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = s.post(
            self.base + "/oreate/create/chat",
            headers={**self.headers, "content-type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()


