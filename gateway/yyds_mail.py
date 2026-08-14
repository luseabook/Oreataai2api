"""YYDS temporary-mail client used by account registration."""

from __future__ import annotations

import html
import json
import re
import secrets
import time
from typing import Any, Callable, Dict, List, Mapping

import requests

from gateway.http_retry import mount_get_retry
from gateway.mail_identity import (
    generate_mailbox_local_part,
    rank_mail_domains,
    soft_order_mail_domains,
)

MailConfigFn = Callable[[], Mapping[str, Any]]


def _session() -> requests.Session:
    session = requests.Session()
    mount_get_retry(session)
    return session


class YydsClient:
    def __init__(self, mail_config: MailConfigFn):
        self._mail_config = mail_config

    def _cfg(self) -> Mapping[str, Any]:
        return self._mail_config()

    @property
    def base(self) -> str:
        return str(self._cfg().get("base_url") or "").rstrip("/")

    @property
    def api_key(self) -> str:
        return str(self._cfg().get("api_key") or "")

    def headers(self):
        if not self.api_key:
            raise RuntimeError("YYDS API key missing in config.json")
        return {"X-API-Key": self.api_key}

    def list_domains(self) -> List[str]:
        r = _session().get(f"{self.base}/domains", timeout=20)
        r.raise_for_status()
        data = r.json().get("data", [])
        out = []
        for item in data:
            if item.get("dnsRecords", {}).get("allPassed") and item.get("receivingReady", True):
                out.append(item["domain"])
        return out

    def probe_domain(self, domain: str) -> Dict[str, Any]:
        local_part = f"probe{secrets.token_hex(3)}"
        payload = {"localPart": local_part, "domain": domain}
        r = requests.post(f"{self.base}/accounts", json=payload, headers=self.headers(), timeout=30)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:500]}
        ok = r.status_code in (200, 201) and isinstance(body, dict) and body.get("data")
        return {"domain": domain, "status": r.status_code, "ok": bool(ok), "body": body}

    def create_mailbox(self) -> Dict[str, Any]:
        domains = self._cfg().get("preferred_domains") or self.list_domains()
        if not domains:
            raise RuntimeError("No YYDS domains available")
        domains = soft_order_mail_domains(
            rank_mail_domains([str(item) for item in domains if str(item or "").strip()])
        )
        errors = []
        for domain in domains:
            local_part = generate_mailbox_local_part()
            payload = {"localPart": local_part, "domain": domain}
            r = requests.post(f"{self.base}/accounts", json=payload, headers=self.headers(), timeout=30)
            try:
                body = r.json()
            except Exception:
                body = {"raw": r.text[:500]}
            if r.status_code in (200, 201) and isinstance(body, dict) and body.get("data"):
                return body["data"]
            errors.append({"domain": domain, "status": r.status_code, "body": body})
        raise RuntimeError(f"YYDS create mailbox failed for all candidate domains: {json.dumps(errors, ensure_ascii=False)[:2000]}")

    def fetch_messages(self, address: str, token: str) -> List[Dict[str, Any]]:
        headers = {"Authorization": f"Bearer {token}"}
        r = _session().get(f"{self.base}/messages", headers=headers, params={"address": address}, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", {})
        if isinstance(data, dict):
            return data.get("messages") or []
        if isinstance(data, list):
            return data
        return []

    def fetch_message_detail(self, address: str, token: str, message_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"}
        r = _session().get(f"{self.base}/messages/{message_id}", headers=headers, params={"address": address}, timeout=30)
        r.raise_for_status()
        return r.json().get("data", {})

    def extract_verify_link(self, message: Dict[str, Any]) -> str:
        blobs = []
        for key in ("subject", "html", "text", "body", "content", "bodyPreview"):
            value = message.get(key)
            if isinstance(value, list):
                blobs.extend([str(x) for x in value])
            elif isinstance(value, dict) and "content" in value:
                blobs.append(str(value.get("content") or ""))
            elif isinstance(value, str):
                blobs.append(value)
        joined = html.unescape("\n".join(blobs))
        patterns = (
            r'https://(?:www\.)?oreateai\.com[^\s"\'<>]*confirm[^\s"\'<>]*',
            r'https://(?:www\.)?oreateai\.com[^\s"\'<>]*verify[^\s"\'<>]*',
            r'https://(?:www\.)?oreateai\.com[^\s"\'<>]*[?&][^\s"\'<>]*tokenID=[^\s"\'<>]*',
            r'https://(?:www\.)?oreateai\.com[^\s"\'<>]*tokenID=[^\s"\'<>]*',
        )
        for pattern in patterns:
            m = re.search(pattern, joined, re.I)
            if m:
                return html.unescape(m.group(0)).rstrip(").,;]")
        return ""

    def extract_verify_code(self, message: Dict[str, Any]) -> str:
        blobs = []
        for key in ("subject", "html", "text", "body", "content", "bodyPreview"):
            value = message.get(key)
            if isinstance(value, list):
                blobs.extend([str(x) for x in value])
            elif isinstance(value, dict) and "content" in value:
                blobs.append(str(value.get("content") or ""))
            elif isinstance(value, str):
                blobs.append(value)
        joined = html.unescape("\n".join(blobs))
        m = re.search(r'\b(\d{6})\b', joined)
        return m.group(1) if m else ""

    def wait_verification_artifact(
        self,
        address: str,
        token: str,
        timeout_sec: int = 180,
        **kwargs: Any,
    ) -> Dict[str, str]:
        # kwargs (not_before/exclude_token_ids) are Outlook-specific and ignored here.
        seen = set()
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            msgs = self.fetch_messages(address, token)
            for msg in msgs:
                msg_id = msg.get("id")
                if not msg_id or msg_id in seen:
                    continue
                seen.add(msg_id)
                detail = self.fetch_message_detail(address, token, msg_id)
                link = self.extract_verify_link(detail)
                code = self.extract_verify_code(detail)
                if link or code:
                    return {"message_id": msg_id, "link": link, "code": code}
            time.sleep(5)
        raise RuntimeError("YYDS verification artifact timeout")

    def test_connectivity(self) -> Dict[str, Any]:
        domains = self.list_domains()[:20]
        preferred = self._cfg().get("preferred_domains") or domains[:5]
        results = []
        for domain in preferred:
            try:
                results.append(self.probe_domain(domain))
            except Exception as e:
                results.append({"domain": domain, "ok": False, "error": str(e)})
        return {"base_url": self.base, "preferred_domains": preferred, "results": results}


