import asyncio
import base64
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import re
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from banti_token_generator import generate_jt_token

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "accounts.db"

DEFAULT_CONFIG = {
    "server": {
        "host": "0.0.0.0",
        "port": 8890,
        "admin_username": "admin",
        "admin_password": "admin123",
    },
    "oreate": {
        "base_url": "https://www.oreateai.com",
        "default_fr": "main",
        "request_timeout": 30,
        "default_image_model": "Google Nano Banana 2",
        "default_image_ratio": "16:9",
        "default_image_resolution": "4K",
        "default_video_scene": "text_or_image",
        "default_video_model": "Seedance 2.0 Mini",
        "default_video_duration": 5,
        "default_video_resolution": "480",
        "default_video_ratio": "16:9",
    },
    "mail": {
        "provider": "yyds",
        "base_url": "https://maliapi.215.im/v1",
        "api_key": "",
        "preferred_domains": [],
    },
    "pool": {
        "min_accounts": 3,
        "maintain_target": 5,
        "valid_threshold_pct": 1.0,
        "maintain_check_interval": 300,
    },
}


def deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        user_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return deep_merge(DEFAULT_CONFIG, user_cfg)
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg: Dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


CFG = load_config()
ADMIN_TOKENS: Dict[str, str] = {}
WS_CLIENTS: List[WebSocket] = []


def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            password TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            source TEXT NOT NULL DEFAULT 'auto',
            ouid TEXT,
            ouss TEXT,
            model_info_json TEXT,
            video_info_json TEXT,
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            kind TEXT NOT NULL,
            prompt TEXT,
            payload_json TEXT,
            chat_id TEXT,
            status TEXT NOT NULL DEFAULT 'created',
            response_json TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            last_used_at REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_id INTEGER,
            kind TEXT NOT NULL,
            account_id INTEGER,
            prompt TEXT,
            status TEXT NOT NULL,
            response_summary TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY(api_key_id) REFERENCES api_keys(id)
        )
        """
    )
    conn.commit()
    conn.close()


async def broadcast(msg: Dict[str, Any]):
    dead = []
    for ws in WS_CLIENTS:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        try:
            WS_CLIENTS.remove(ws)
        except ValueError:
            pass


def emit_log(level: str, message: str):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast({"type": "log", "time": time.strftime("%H:%M:%S"), "level": level, "message": message}))
    except RuntimeError:
        pass


class SettingsIn(BaseModel):
    server: Optional[Dict[str, Any]] = None
    oreate: Optional[Dict[str, Any]] = None
    mail: Optional[Dict[str, Any]] = None
    pool: Optional[Dict[str, Any]] = None


class LoginIn(BaseModel):
    username: str
    password: str


class MediaTaskIn(BaseModel):
    account_id: int
    prompt: str
    kind: str
    model_name: Optional[str] = None
    ratio: Optional[str] = None
    resolution: Optional[str] = None
    duration: Optional[int] = None
    scene_id: Optional[str] = None
    jt: str = ""


class AutoRegisterIn(BaseModel):
    count: int = 1


class MaintainIn(BaseModel):
    force_register: bool = False
    max_register: int = 3


@dataclass
class OreateSession:
    email: str
    password: str
    cookies: Dict[str, str]
    ticket_id: str = ""
    fr: str = "main"
    signup_response: Optional[Dict[str, Any]] = None
    signup_payload: Optional[Dict[str, Any]] = None


class YydsClient:
    def __init__(self):
        self.base = CFG["mail"]["base_url"].rstrip("/")
        self.api_key = CFG["mail"]["api_key"]

    def headers(self):
        if not self.api_key:
            raise RuntimeError("YYDS API key missing in config.json")
        return {"X-API-Key": self.api_key}

    def list_domains(self) -> List[str]:
        r = requests.get(f"{self.base}/domains", timeout=20)
        r.raise_for_status()
        data = r.json().get("data", [])
        out = []
        for item in data:
            if item.get("dnsRecords", {}).get("allPassed") and item.get("receivingReady", True):
                out.append(item["domain"])
        return out

    def probe_domain(self, domain: str) -> Dict[str, Any]:
        local_part = f"probe-{secrets.token_hex(3)}"
        payload = {"localPart": local_part, "domain": domain}
        r = requests.post(f"{self.base}/accounts", json=payload, headers=self.headers(), timeout=30)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:500]}
        ok = r.status_code in (200, 201) and isinstance(body, dict) and body.get("data")
        return {"domain": domain, "status": r.status_code, "ok": bool(ok), "body": body}

    def create_mailbox(self) -> Dict[str, Any]:
        domains = CFG["mail"].get("preferred_domains") or self.list_domains()
        if not domains:
            raise RuntimeError("No YYDS domains available")
        errors = []
        for domain in domains:
            local_part = f"oreate-{secrets.token_hex(4)}"
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
        r = requests.get(f"{self.base}/messages", headers=headers, params={"address": address}, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", {})
        if isinstance(data, dict):
            return data.get("messages") or []
        if isinstance(data, list):
            return data
        return []

    def fetch_message_detail(self, address: str, token: str, message_id: str) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.get(f"{self.base}/messages/{message_id}", headers=headers, params={"address": address}, timeout=30)
        r.raise_for_status()
        return r.json().get("data", {})

    def extract_verify_link(self, message: Dict[str, Any]) -> str:
        blobs = []
        for key in ("subject", "html", "text", "body", "content"):
            value = message.get(key)
            if isinstance(value, list):
                blobs.extend([str(x) for x in value])
            elif isinstance(value, str):
                blobs.append(value)
        joined = "\n".join(blobs)
        m = re.search(r'https://www\.oreateai\.com[^\s"\'<>]*confirm[^\s"\'<>]*', joined, re.I)
        if m:
            return m.group(0)
        m = re.search(r'https://www\.oreateai\.com[^\s"\'<>]*verify[^\s"\'<>]*', joined, re.I)
        if m:
            return m.group(0)
        m = re.search(r'https://www\.oreateai\.com[^\s"\'<>]*\?[^\s"\'<>]*tokenID=[^\s"\'<>]*', joined, re.I)
        if m:
            return m.group(0)
        return ""

    def extract_verify_code(self, message: Dict[str, Any]) -> str:
        blobs = []
        for key in ("subject", "html", "text", "body", "content"):
            value = message.get(key)
            if isinstance(value, list):
                blobs.extend([str(x) for x in value])
            elif isinstance(value, str):
                blobs.append(value)
        joined = "\n".join(blobs)
        m = re.search(r'\\b(\\d{6})\\b', joined)
        return m.group(1) if m else ""

    def wait_verification_artifact(self, address: str, token: str, timeout_sec: int = 180) -> Dict[str, str]:
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
        preferred = CFG["mail"].get("preferred_domains") or domains[:5]
        results = []
        for domain in preferred:
            try:
                results.append(self.probe_domain(domain))
            except Exception as e:
                results.append({"domain": domain, "ok": False, "error": str(e)})
        return {"base_url": self.base, "preferred_domains": preferred, "results": results}


class OreateClient:
    def __init__(self):
        self.base = CFG["oreate"]["base_url"].rstrip("/")
        self.timeout = CFG["oreate"].get("request_timeout", 30)
        self.headers = {
            "accept": "application/json, text/plain, */*",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            "origin": self.base,
            "referer": f"{self.base}/home/vertical/aiImage",
            "locale": "zh-CN",
            "client-type": "pc",
            "pragma": "no-cache",
            "cache-control": "no-cache, no-store",
        }

    def new_session(self) -> requests.Session:
        s = requests.Session()
        s.verify = False
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
            "fr": CFG["oreate"]["default_fr"],
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
            json={"email": email, "fr": CFG["oreate"]["default_fr"]},
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
                "fr": CFG["oreate"]["default_fr"],
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
            "fr": CFG["oreate"]["default_fr"],
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
        if account["ouid"]:
            s.cookies.set("OUID", account["ouid"])
        if account["ouss"]:
            s.cookies.set("ouss", account["ouss"])
        return s

    def session_from_cookie_dict(self, cookies: Dict[str, str]) -> requests.Session:
        s = self.new_session()
        if cookies.get("OUID"):
            s.cookies.set("OUID", cookies["OUID"])
        if cookies.get("ouss"):
            s.cookies.set("ouss", cookies["ouss"])
        return s

    def fetch_image_models(self, s: requests.Session) -> Dict[str, Any]:
        r = s.get(self.base + "/oreate/img/getmodelconfig", headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def fetch_video_models(self, s: requests.Session) -> Dict[str, Any]:
        r = s.get(self.base + "/oreate/aivideo/getmodelconfigv3", headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def fetch_video_scenes(self, s: requests.Session) -> Dict[str, Any]:
        r = s.get(self.base + "/oreate/aivideo/getsceneconfig", headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def create_chat(self, s: requests.Session, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = s.post(
            self.base + "/oreate/create/chat",
            headers={**self.headers, "content-type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()


CLIENT = OreateClient()
MAIL = YydsClient()


def save_account(email: str, password: str, session: OreateSession, model_info=None, video_info=None, status="verified", source="auto") -> int:
    now = time.time()
    conn = db_conn()
    conn.execute(
        """
        INSERT INTO accounts(email,password,status,source,ouid,ouss,model_info_json,video_info_json,last_error,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(email) DO UPDATE SET
            password=excluded.password,
            status=excluded.status,
            source=excluded.source,
            ouid=excluded.ouid,
            ouss=excluded.ouss,
            model_info_json=excluded.model_info_json,
            video_info_json=excluded.video_info_json,
            updated_at=excluded.updated_at
        """,
        (
            email,
            password,
            status,
            source,
            session.cookies.get("OUID", ""),
            session.cookies.get("ouss", ""),
            json.dumps(model_info, ensure_ascii=False) if model_info else None,
            json.dumps(video_info, ensure_ascii=False) if video_info else None,
            None,
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM accounts WHERE email=?", (email,)).fetchone()
    account_id = row[0]
    conn.close()
    return account_id


def list_accounts() -> List[Dict[str, Any]]:
    conn = db_conn()
    rows = conn.execute("SELECT * FROM accounts ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_task(account_id: int, kind: str, prompt: str, payload: Dict[str, Any], response: Dict[str, Any]) -> int:
    now = time.time()
    chat_id = response.get("data", {}).get("chatId", "")
    conn = db_conn()
    conn.execute(
        """
        INSERT INTO tasks(account_id,kind,prompt,payload_json,chat_id,status,response_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            account_id,
            kind,
            prompt,
            json.dumps(payload, ensure_ascii=False),
            chat_id,
            "created",
            json.dumps(response, ensure_ascii=False),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    task_id = row[0]
    conn.close()
    return task_id


def update_task_status(task_id: int, status: str, response: Optional[Dict[str, Any]] = None) -> None:
    now = time.time()
    conn = db_conn()
    conn.execute(
        "UPDATE tasks SET status=?, response_json=?, updated_at=? WHERE id=?",
        (status, json.dumps(response, ensure_ascii=False) if response is not None else None, now, task_id),
    )
    conn.commit()
    conn.close()


def pick_account_for_task(kind: str) -> Optional[sqlite3.Row]:
    conn = db_conn()
    row = conn.execute(
        """
        SELECT * FROM accounts
        WHERE status IN ('verified', 'active')
          AND (
            (? = 'image' AND (model_info_json IS NOT NULL AND model_info_json != ''))
            OR
            (? = 'video' AND (video_info_json IS NOT NULL AND video_info_json != ''))
          )
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (kind, kind),
    ).fetchone()
    conn.close()
    return row


def auto_register_accounts(count: int = 1) -> List[Dict[str, Any]]:
    results = []
    for _ in range(max(1, count)):
        mailbox = MAIL.create_mailbox()
        email = mailbox["address"]
        token = mailbox["token"]
        password = "Aa1@" + secrets.token_hex(6)[:8]
        trace = []
        trace.append({"step": "create_mailbox", "email": email, "domain": mailbox.get("domain"), "mailbox_id": mailbox.get("mailbox_id")})
        signup = CLIENT.signup_attempt(email, password)
        body = signup.get("response", {})
        status_code = signup.get("status_code")
        signup_ok = status_code == 200 and body.get("status", {}).get("code") == 0
        trace.append({"step": "signup_attempt", "status_code": status_code, "response": body})
        artifact = {}
        verification = {}
        account_id = None
        final_status = "signup_failed"

        if signup_ok:
            send_email_count = body.get("data", {}).get("sendEmailCount") or body.get("sendEmailCount")
            confirm_status = body.get("data", {}).get("confirmEmailStatus") or body.get("confirmEmailStatus")
            register_status = body.get("data", {}).get("registerStatus") or body.get("registerStatus")
            ticket_id = signup["ticket"]["ticketID"]
            trace.append({"step": "signup_flags", "sendEmailCount": send_email_count, "confirmEmailStatus": confirm_status, "registerStatus": register_status, "ticketID": ticket_id})
            if register_status == 2:
                try:
                    verification = CLIENT.check_email_verified(email, ticket_id)
                    trace.append({"step": "check_email_verified", "response": verification})
                    token_id = verification.get("tokenID") or verification.get("data", {}).get("tokenID") or verification.get("tokenId")
                    if token_id:
                        confirm = CLIENT.confirm_email_register(email, token_id, ticket_id, password)
                        verification["confirm"] = confirm
                        trace.append({"step": "emailregisterconfirm", "response": confirm})
                        if confirm.get("status_code") == 200 and confirm.get("response", {}).get("status", {}).get("code") == 0:
                            session = CLIENT.login(email, password)
                            sess = CLIENT.session_from_cookie_dict(session.cookies)
                            img = CLIENT.fetch_image_models(sess)
                            vid = {
                                "models": CLIENT.fetch_video_models(sess),
                                "scenes": CLIENT.fetch_video_scenes(sess),
                            }
                            account_id = save_account(email, password, session, model_info=img, video_info=vid, status="verified", source="auto")
                            final_status = "verified"
                            trace.append({"step": "login_and_save", "account_id": account_id})
                        else:
                            final_status = "confirm_failed"
                    else:
                        final_status = "verify_pending"
                except Exception as e:
                    verification = {"error": str(e), "sendEmailCount": send_email_count, "confirmEmailStatus": confirm_status}
                    trace.append({"step": "verify_error", "error": str(e)})
                    final_status = "verify_error"
            else:
                try:
                    artifact = MAIL.wait_verification_artifact(email, token, timeout_sec=180)
                    trace.append({"step": "wait_verification_artifact", "artifact": artifact})
                    if artifact.get("link") or artifact.get("code"):
                        # Extract tokenID from the verification link and visit it
                        token_id = ""
                        link = artifact.get("link", "")
                        if link:
                            parsed = urllib.parse.urlparse(link)
                            params = urllib.parse.parse_qs(parsed.query)
                            token_id = params.get("tokenID", [""])[0]
                            trace.append({"step": "extract_token_from_link", "tokenID": token_id, "link": link})
                            # Visit the verification link (click it) - REQUIRED for email to be marked verified
                            try:
                                vr = requests.get(link, verify=False, timeout=10, allow_redirects=True)
                                trace.append({"step": "visit_verification_link", "status": vr.status_code})
                            except Exception as e:
                                trace.append({"step": "visit_verification_link", "error": str(e)})
                        
                        code = artifact.get("code", "")
                        if not token_id and code:
                            token_id = code
                        
                        if not token_id:
                            # Fallback: try check_email_verified
                            verification = CLIENT.check_email_verified(email, ticket_id)
                            trace.append({"step": "check_email_verified", "response": verification})
                            token_id = verification.get("tokenID") or verification.get("data", {}).get("tokenID") or verification.get("tokenId")
                        
                        if token_id:
                            confirm = CLIENT.confirm_email_register(email, token_id, ticket_id, password)
                            verification["confirm"] = confirm
                            trace.append({"step": "emailregisterconfirm", "response": confirm})
                            if confirm.get("status_code") == 200 and confirm.get("response", {}).get("status", {}).get("code") == 0:
                                session = CLIENT.login(email, password)
                                sess = CLIENT.session_from_cookie_dict(session.cookies)
                                img = CLIENT.fetch_image_models(sess)
                                vid = {
                                    "models": CLIENT.fetch_video_models(sess),
                                    "scenes": CLIENT.fetch_video_scenes(sess),
                                }
                                account_id = save_account(email, password, session, model_info=img, video_info=vid, status="verified", source="auto")
                                final_status = "verified"
                                trace.append({"step": "login_and_save", "account_id": account_id})
                            else:
                                final_status = "confirm_failed"
                        else:
                            final_status = "verify_pending"
                    else:
                        final_status = "verify_timeout"
                except Exception as e:
                    artifact = {"error": str(e)}
                    trace.append({"step": "wait_verification_error", "error": str(e)})
                    final_status = "verify_error"

        results.append({
            "ok": final_status == "verified",
            "status": final_status,
            "account_id": account_id,
            "email": email,
            "password": password,
            "signup_status": status_code,
            "signup_response": body,
            "verification": verification,
            "verification_artifact": artifact,
            "trace": trace,
            "mailbox": {"address": email, "token": token},
        })
    return results

app = FastAPI(title="OreateAI Gateway")

# === API Key Auth ===
security = HTTPBearer(auto_error=False)

def get_api_key(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Optional[int]:
    if credentials is None:
        return None
    conn = db_conn()
    row = conn.execute("SELECT id, enabled FROM api_keys WHERE key=?", (credentials.credentials,)).fetchone()
    conn.close()
    if row and row["enabled"]:
        conn = db_conn()
        conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (time.time(), row["id"]))
        conn.commit()
        conn.close()
        return row["id"]
    return None

def require_api_key(api_key_id: Optional[int] = Depends(get_api_key)):
    if api_key_id is None:
        raise HTTPException(401, "valid API key required (header: Authorization: Bearer <key>)")
    return api_key_id

def log_usage(api_key_id: int, kind: str, account_id: int, prompt: str, status: str, summary: str = ""):
    conn = db_conn()
    conn.execute(
        "INSERT INTO usage_log (api_key_id, kind, account_id, prompt, status, response_summary, created_at) VALUES (?,?,?,?,?,?,?)",
        (api_key_id, kind, account_id, prompt[:200], status, summary[:200], time.time()),
    )
    conn.commit()
    conn.close()


# === API Key Management (admin only) ===
def require_admin(request: Request):
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else auth
    if token not in ADMIN_TOKENS:
        raise HTTPException(401, "admin login required")
    return token


@app.get("/api/admin/apikeys")
def list_api_keys(_=Depends(require_admin)):
    conn = db_conn()
    rows = conn.execute("SELECT * FROM api_keys ORDER BY id DESC").fetchall()
    conn.close()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/admin/apikeys")
def create_api_key(name: str = "", _=Depends(require_admin)):
    key = "oreate_" + secrets.token_hex(24)
    conn = db_conn()
    conn.execute("INSERT INTO api_keys (key, name, enabled, created_at) VALUES (?,?,1,?)", (key, name, time.time()))
    conn.commit()
    row = conn.execute("SELECT * FROM api_keys WHERE key=?", (key,)).fetchone()
    conn.close()
    return {"ok": True, "item": dict(row) if row else None}


@app.delete("/api/admin/apikeys/{key_id}")
def delete_api_key(key_id: int, _=Depends(require_admin)):
    conn = db_conn()
    conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
    conn.execute("DELETE FROM usage_log WHERE api_key_id=?", (key_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/admin/usage")
def get_usage(_=Depends(require_admin)):
    conn = db_conn()
    rows = conn.execute(
        "SELECT u.*, a.email as account_email FROM usage_log u LEFT JOIN accounts a ON u.account_id=a.id ORDER BY u.id DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return {"items": [dict(r) for r in rows]}


# === Gateway Endpoints (API Key protected) ===
class GatewayGenerateIn(BaseModel):
    kind: str = "image"  # "image" or "video"
    prompt: str
    model_name: Optional[str] = None
    ratio: Optional[str] = None
    resolution: Optional[str] = None
    duration: Optional[int] = None
    scene_id: Optional[str] = None
    account_id: Optional[int] = None


@app.post("/v1/generate")
def gateway_generate(body: GatewayGenerateIn, api_key_id: int = Depends(require_api_key)):
    """Generate image or video via the account pool. Auto-selects account if not specified."""
    conn = db_conn()
    
    # Pick account
    if body.account_id:
        account = conn.execute("SELECT * FROM accounts WHERE id=? AND status='verified'", (body.account_id,)).fetchone()
    else:
        account = conn.execute(
            "SELECT * FROM accounts WHERE status='verified' ORDER BY last_error IS NULL DESC, id ASC LIMIT 1"
        ).fetchone()
    conn.close()
    
    if not account:
        raise HTTPException(503, "no verified account available")
    
    s = CLIENT.session_from_account(account)
    
    if body.kind == "image":
        payload = {
            "docId": "",
            "content": body.prompt,
            "chatMode": "aiImage",
            "modelName": body.model_name or CFG["oreate"]["default_image_model"],
            "ratio": body.ratio or CFG["oreate"]["default_image_ratio"],
            "resolution": body.resolution or CFG["oreate"]["default_image_resolution"],
        }
        response = CLIENT.create_chat(s, payload)
        task_id = save_task(account["id"], "image", body.prompt, payload, response)
        log_usage(api_key_id, "image", account["id"], body.prompt, "created")
        return {"ok": True, "task_id": task_id, "account_id": account["id"], "response": response}
    
    elif body.kind == "video":
        payload = {
            "docId": "",
            "content": body.prompt,
            "chatMode": "aiVideo",
            "sceneId": body.scene_id or CFG["oreate"]["default_video_scene"],
            "modelName": body.model_name or CFG["oreate"]["default_video_model"],
            "duration": body.duration or CFG["oreate"]["default_video_duration"],
            "resolution": body.resolution or CFG["oreate"]["default_video_resolution"],
            "ratio": body.ratio or CFG["oreate"]["default_video_ratio"],
        }
        response = CLIENT.create_chat(s, payload)
        task_id = save_task(account["id"], "video", body.prompt, payload, response)
        log_usage(api_key_id, "video", account["id"], body.prompt, "created")
        return {"ok": True, "task_id": task_id, "account_id": account["id"], "response": response}
    
    raise HTTPException(400, f"unsupported kind: {body.kind}")


@app.get("/v1/tasks")
def gateway_tasks(api_key_id: int = Depends(require_api_key)):
    """List tasks created by this API key."""
    conn = db_conn()
    rows = conn.execute(
        "SELECT * FROM usage_log WHERE api_key_id=? ORDER BY id DESC LIMIT 50", (api_key_id,)
    ).fetchall()
    conn.close()
    return {"items": [dict(r) for r in rows]}


@app.get("/v1/accounts/status")
def gateway_account_status(api_key_id: int = Depends(require_api_key)):
    """Get pool status."""
    conn = db_conn()
    total = conn.execute("SELECT COUNT(*) as c FROM accounts").fetchone()["c"]
    verified = conn.execute("SELECT COUNT(*) as c FROM accounts WHERE status='verified'").fetchone()["c"]
    conn.close()
    return {"ok": True, "total_accounts": total, "verified_accounts": verified}


@app.get("/v1/task/{task_id}")
def gateway_task_detail(task_id: int, api_key_id: int = Depends(require_api_key)):
    conn = db_conn()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    conn.close()
    if not task:
        raise HTTPException(404, "task not found")
    return {"ok": True, "task": dict(task)}


@app.on_event("startup")
def on_startup():
    init_db()
    if not CONFIG_PATH.exists():
        save_config(CFG)


@app.get("/")
def root():
    return {
        "ok": True,
        "service": "oreateai",
        "cwd": str(BASE_DIR),
        "accounts": len(list_accounts()),
    }


@app.post("/api/admin/login")
def admin_login(body: LoginIn):
    if body.username != CFG["server"]["admin_username"] or body.password != CFG["server"]["admin_password"]:
        raise HTTPException(401, "invalid admin credentials")
    token = secrets.token_hex(24)
    ADMIN_TOKENS[token] = body.username
    return {"ok": True, "token": token}


@app.get("/api/admin/settings")
def get_settings():
    cfg = load_config()
    return cfg


@app.put("/api/admin/settings")
def put_settings(body: SettingsIn):
    global CFG
    data = body.dict(exclude_none=True)
    CFG = deep_merge(CFG, data)
    save_config(CFG)
    return {"ok": True, "config": CFG}


@app.get("/api/accounts")
def api_accounts():
    return {"items": list_accounts()}


@app.get("/api/mail/test")
def mail_test():
    return MAIL.test_connectivity()


@app.post("/api/register/one")
def register_one():
    return {"items": auto_register_accounts(1)}


@app.post("/api/register/batch")
def register_batch(body: AutoRegisterIn):
    return {"items": auto_register_accounts(body.count)}


@app.post("/api/accounts/import")
def import_account(body: Dict[str, str]):
    email = body.get("email", "").strip()
    password = body.get("password", "")
    if not email or not password:
        raise HTTPException(400, "email/password required")
    session = CLIENT.login(email, password)
    sess = CLIENT.session_from_cookie_dict(session.cookies)
    img = CLIENT.fetch_image_models(sess)
    vid = {
        "models": CLIENT.fetch_video_models(sess),
        "scenes": CLIENT.fetch_video_scenes(sess),
    }
    account_id = save_account(email, password, session, model_info=img, video_info=vid, status="verified", source="manual")
    return {"ok": True, "account_id": account_id, "email": email}


@app.post("/api/media/generate")
def generate_media(body: MediaTaskIn):
    conn = db_conn()
    account = conn.execute("SELECT * FROM accounts WHERE id=?", (body.account_id,)).fetchone()
    conn.close()
    if not account:
        raise HTTPException(404, "account not found")
    s = CLIENT.session_from_account(account)
    payload = {
        "docId": "",
        "content": body.prompt,
        "chatMode": "aiImage" if body.kind == "image" else "aiVideo",
    }
    if body.kind == "image":
        payload.update({
            "modelName": body.model_name or CFG["oreate"]["default_image_model"],
            "ratio": body.ratio or CFG["oreate"]["default_image_ratio"],
            "resolution": body.resolution or CFG["oreate"]["default_image_resolution"],
        })
    elif body.kind == "video":
        payload.update({
            "sceneId": body.scene_id or CFG["oreate"]["default_video_scene"],
            "modelName": body.model_name or CFG["oreate"]["default_video_model"],
            "duration": body.duration or CFG["oreate"]["default_video_duration"],
            "resolution": body.resolution or CFG["oreate"]["default_video_resolution"],
            "ratio": body.ratio or CFG["oreate"]["default_video_ratio"],
        })
    if body.jt is not None:
        payload["jt"] = body.jt
    response = CLIENT.create_chat(s, payload)
    task_id = save_task(body.account_id, body.kind, body.prompt, payload, response)
    return {"ok": True, "task_id": task_id, "response": response}


@app.get("/api/tasks")
def list_tasks():
    conn = db_conn()
    rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
    conn.close()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/tasks/{task_id}/mark")
def mark_task(task_id: int, body: Dict[str, Any]):
    status = str(body.get("status", "")).strip()
    if not status:
        raise HTTPException(400, "status required")
    update_task_status(task_id, status, body.get("response"))
    return {"ok": True, "task_id": task_id, "status": status}


@app.post("/api/pool/maintain")
def pool_maintain(body: MaintainIn):
    accounts = list_accounts()
    verified = [a for a in accounts if a.get("status") in ("verified", "active")]
    created = []
    if len(verified) < CFG["pool"].get("min_accounts", 3) or body.force_register:
        need = max(1, min(body.max_register, CFG["pool"].get("maintain_target", 5) - len(verified)))
        created = auto_register_accounts(need)
    return {
        "ok": True,
        "accounts_total": len(accounts),
        "verified_total": len(verified),
        "created": created,
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    WS_CLIENTS.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            WS_CLIENTS.remove(ws)
        except ValueError:
            pass


ADMIN_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OreateAI Gateway</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f5f5f7;color:#1d1d1f;padding:0}
.nav{background:#fff;border-bottom:1px solid #e5e5e5;padding:16px 32px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100;animation:slideDown .5s cubic-bezier(.22,1,.36,1)}
.nav h1{font-size:18px;font-weight:600;letter-spacing:-.3px}
.nav a{color:#1d1d1f;text-decoration:none;font-size:14px;padding:6px 16px;border-radius:8px;transition:.2s}
.nav a:hover{background:#f0f0f0}
.nav .badge{background:#1d1d1f;color:#fff;font-size:11px;padding:2px 8px;border-radius:12px;margin-left:4px}
.container{max-width:1200px;margin:0 auto;padding:24px 32px}
.section{background:#fff;border-radius:16px;padding:24px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.04);animation:fadeUp .6s cubic-bezier(.22,1,.36,1)}
.section h2{font-size:15px;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:end}
.col{flex:1;min-width:200px}
label{display:block;font-size:12px;color:#86868b;margin-bottom:4px}
input,select,textarea{width:100%;font-size:14px;padding:10px 12px;border:1px solid #d2d2d7;border-radius:10px;background:#fff;transition:.2s;outline:none}
input:focus,select:focus,textarea:focus{border-color:#1d1d1f;box-shadow:0 0 0 3px rgba(0,0,0,.06)}
textarea{min-height:80px;resize:vertical;font-family:inherit}
button{font-size:14px;padding:10px 20px;border:none;border-radius:10px;cursor:pointer;transition:all .25s cubic-bezier(.22,1,.36,1);font-weight:500}
button:active{transform:scale(.96)}
.btn-primary{background:#1d1d1f;color:#fff}
.btn-primary:hover{background:#000}
.btn-secondary{background:#f0f0f0;color:#1d1d1f}
.btn-secondary:hover{background:#e5e5e5}
.btn-danger{background:#ff3b30;color:#fff}
.btn-danger:hover{background:#d62d20}
.btn-sm{padding:6px 14px;font-size:12px;border-radius:8px}
.table-wrap{overflow-x:auto;border-radius:10px;border:1px solid #e5e5e5}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#f5f5f7;padding:10px 12px;text-align:left;font-weight:500;border-bottom:1px solid #e5e5e5}
td{padding:10px 12px;border-bottom:1px solid #f0f0f0;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#fafafa}
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:6px;font-weight:500}
.tag-green{background:#e8f5e9;color:#2e7d32}
.tag-red{background:#ffebee;color:#c62828}
.tag-gray{background:#f5f5f5;color:#616161}
.tag-blue{background:#e3f2fd;color:#1565c0}
.copy-btn{cursor:pointer;font-size:11px;padding:2px 8px;border-radius:6px;background:#f0f0f0;border:none;margin-left:4px}
.copy-btn:hover{background:#e0e0e0}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:0}
.stat-card{background:#f5f5f7;border-radius:12px;padding:16px;text-align:center}
.stat-card .num{font-size:28px;font-weight:700;letter-spacing:-.5px}
.stat-card .label{font-size:12px;color:#86868b;margin-top:2px}
.tabs{display:flex;gap:4px;margin-bottom:16px;background:#f5f5f7;padding:4px;border-radius:10px}
.tab{flex:1;text-align:center;padding:8px 12px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:500;transition:.2s;border:none;background:transparent}
.tab.active{background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.tab:hover:not(.active){background:rgba(0,0,0,.03)}
.hidden{display:none!important}
.loading{text-align:center;padding:32px;color:#86868b}
@keyframes fadeUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
@keyframes slideDown{from{opacity:0;transform:translateY(-12px)}to{opacity:1;transform:translateY(0)}}
pre{background:#fafafa;border:1px solid #eee;padding:12px;border-radius:10px;overflow:auto;font-size:12px;max-height:300px}
</style>
</head>
<body>

<div class="nav">
  <h1>OreateAI Gateway</h1>
  <a href="#" onclick="switchTab('pool')" id="tab-pool-btn">号池 <span class="badge" id="pool-count">0</span></a>
  <a href="#" onclick="switchTab('generate')">生成</a>
  <a href="#" onclick="switchTab('tasks')">任务</a>
  <a href="#" onclick="switchTab('apikeys')">API Keys</a>
  <a href="#" onclick="switchTab('settings')">设置</a>
  <span style="flex:1"></span>
  <span style="font-size:12px;color:#86868b" id="status-text">就绪</span>
</div>

<div class="container">

<!-- Stats -->
<div class="stats" id="stats-row">
  <div class="stat-card"><div class="num" id="st-total">-</div><div class="label">总账号</div></div>
  <div class="stat-card"><div class="num" id="st-verified">-</div><div class="label">可用</div></div>
  <div class="stat-card"><div class="num" id="st-tasks">-</div><div class="label">任务数</div></div>
  <div class="stat-card"><div class="num" id="st-apikeys">-</div><div class="label">API Keys</div></div>
</div>

<!-- Tab: 号池 -->
<div id="tab-pool" class="section">
  <h2>📋 号池管理</h2>
  <div class="row" style="margin-bottom:16px">
    <div class="col"><label>注册数量</label><input id="reg_count" value="1"></div>
    <div><button class="btn-primary" onclick="registerOne()">注册 1 个</button></div>
    <div><button class="btn-primary" onclick="registerBatch()">批量注册</button></div>
    <div><button class="btn-secondary" onclick="maintainPool()">补号</button></div>
    <div><button class="btn-secondary" onclick="importDialog()">导入账号</button></div>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID</th><th>邮箱</th><th>状态</th><th>来源</th><th>OUID</th><th>创建时间</th><th>操作</th></tr></thead>
      <tbody id="accounts-tbody"></tbody>
    </table>
  </div>
  <div id="import-area" class="hidden" style="margin-top:12px">
    <div class="row">
      <div class="col"><input id="imp-email" placeholder="邮箱"></div>
      <div class="col"><input id="imp-pwd" placeholder="密码"></div>
      <div><button class="btn-primary" onclick="doImport()">导入</button></div>
      <div><button class="btn-secondary" onclick="document.getElementById('import-area').classList.add('hidden')">取消</button></div>
    </div>
  </div>
</div>

<!-- Tab: 生成 -->
<div id="tab-generate" class="section hidden">
  <h2>🎨 图片 / 🎬 视频 生成</h2>
  <div class="row">
    <div class="col">
      <label>类型</label>
      <select id="g-kind"><option value="image">图片</option><option value="video">视频</option></select>
    </div>
    <div class="col"><label>账号ID（留空自动分配）</label><input id="g-account" placeholder="auto"></div>
    <div class="col"><label>模型（可选）</label><input id="g-model" placeholder="默认"></div>
    <div class="col"><label>比例</label><input id="g-ratio" placeholder="16:9"></div>
  </div>
  <div class="row">
    <div class="col"><label>分辨率</label><input id="g-res" placeholder="4K / 480"></div>
    <div class="col"><label>时长（视频）</label><input id="g-dur" placeholder="5"></div>
    <div class="col"><label>场景（视频）</label><input id="g-scene" placeholder="text_or_image"></div>
  </div>
  <div style="margin-top:12px"><label>描述词</label><textarea id="g-prompt" placeholder="请输入描述词..."></textarea></div>
  <div style="margin-top:12px;display:flex;gap:8px">
    <button class="btn-primary" onclick="gatewayGenerate()">提交生成</button>
    <button class="btn-secondary" onclick="document.getElementById('g-result').textContent=''">清空</button>
  </div>
  <pre id="g-result" style="margin-top:12px"></pre>
</div>

<!-- Tab: 任务 -->
<div id="tab-tasks" class="section hidden">
  <h2>📦 任务列表</h2>
  <button class="btn-secondary btn-sm" onclick="loadTasks()" style="margin-bottom:12px">刷新</button>
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID</th><th>类型</th><th>账号</th><th>状态</th><th>提示词</th><th>chatId</th><th>时间</th></tr></thead>
      <tbody id="tasks-tbody"></tbody>
    </table>
  </div>
</div>

<!-- Tab: API Keys -->
<div id="tab-apikeys" class="section hidden">
  <h2>🔑 API Keys</h2>
  <div class="row" style="margin-bottom:16px">
    <div class="col"><input id="ak-name" placeholder="名称（可选）"></div>
    <div><button class="btn-primary" onclick="createApiKey()">创建 Key</button></div>
  </div>
  <div id="ak-new" class="hidden" style="background:#e8f5e9;padding:12px;border-radius:10px;margin-bottom:12px">
    <strong>新 Key:</strong> <code id="ak-new-value"></code>
    <button class="copy-btn" onclick="copyKey()">复制</button>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID</th><th>Key</th><th>名称</th><th>状态</th><th>创建时间</th><th>最后使用</th><th>操作</th></tr></thead>
      <tbody id="apikeys-tbody"></tbody>
    </table>
  </div>
  <h2 style="margin-top:24px">📊 用量日志</h2>
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID</th><th>类型</th><th>账号</th><th>状态</th><th>提示词</th><th>时间</th></tr></thead>
      <tbody id="usage-tbody"></tbody>
    </table>
  </div>
</div>

<!-- Tab: 设置 -->
<div id="tab-settings" class="section hidden">
  <h2>⚙️ 系统设置</h2>
  <div id="settings-form"></div>
  <div style="margin-top:12px"><button class="btn-primary" onclick="saveSettings()">保存设置</button></div>
  <pre id="settings-raw" style="margin-top:12px"></pre>
</div>

</div>

<script>
let state = {accounts:[],tasks:[],apikeys:[],usage:[],settings:{}};
const BASE = '';

async function api(method, url, body) {
  const opts = {method, headers:{'Content-Type':'application/json'}};
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(BASE + url, opts);
  return r.json();
}

async function init() {
  document.getElementById('status-text').textContent = '加载中...';
  await Promise.all([loadAccounts(), loadTasks(), loadApiKeys(), loadUsage(), loadSettings()]);
  document.getElementById('status-text').textContent = `就绪 — ${state.accounts.filter(a=>a.status==='verified').length} 可用账号`;
}

function switchTab(name) {
  document.querySelectorAll('.tab-pool,.tab-generate,.tab-tasks,.tab-apikeys,.tab-settings'.split(',').map(n=>'#tab-'+n)).forEach(el => {
    const e = document.getElementById('tab-'+el.id.replace('tab-',''));
    if(e) e.classList.add('hidden');
  });
  // Simple version:
  ['pool','generate','tasks','apikeys','settings'].forEach(t => {
    const el = document.getElementById('tab-'+t);
    if (el) el.classList.toggle('hidden', t !== name);
  });
}

// Accounts
async function loadAccounts() {
  const r = await api('GET','/api/accounts');
  state.accounts = r.items || [];
  renderAccounts();
  updateStats();
}

function renderAccounts() {
  const tbody = document.getElementById('accounts-tbody');
  tbody.innerHTML = state.accounts.map(a => {
    const statusClass = a.status === 'verified' ? 'tag-green' : a.status === 'new' ? 'tag-blue' : 'tag-gray';
    const ouid = (a.ouid||'').substring(0,12);
    return `<tr>
      <td>${a.id}</td>
      <td style="max-width:240px;overflow:hidden;text-overflow:ellipsis">${a.email}</td>
      <td><span class="tag ${statusClass}">${a.status}</span></td>
      <td>${a.source||'-'}</td>
      <td style="font-family:monospace;font-size:11px">${ouid}...</td>
      <td>${new Date((a.created_at||0)*1000).toLocaleString()}</td>
      <td><button class="btn-sm btn-secondary" onclick="generateWith('${a.id}')">生成</button></td>
    </tr>`;
  }).join('');
  document.getElementById('pool-count').textContent = state.accounts.filter(a=>a.status==='verified').length;
}

async function registerOne() { document.getElementById('status-text').textContent = '注册中...'; const r=await api('POST','/api/register/one'); await loadAccounts(); document.getElementById('status-text').textContent='完成'; alert(JSON.stringify(r)); }
async function registerBatch() { document.getElementById('status-text').textContent='批量注册中...'; const r=await api('POST','/api/register/batch',{count:Number(document.getElementById('reg_count').value||1)}); await loadAccounts(); document.getElementById('status-text').textContent='完成'; alert('成功: '+((r.items||[]).filter(i=>i.status==='verified').length)+'/'+((r.items||[]).length)); }
async function maintainPool() { const r=await api('POST','/api/pool/maintain',{force_register:true,max_register:Number(document.getElementById('reg_count').value||1)}); await loadAccounts(); alert(JSON.stringify(r.created)); }
function importDialog() { document.getElementById('import-area').classList.remove('hidden'); }
async function doImport() { const r=await api('POST','/api/accounts/import',{email:document.getElementById('imp-email').value,password:document.getElementById('imp-pwd').value}); await loadAccounts(); alert(r.ok?'✅ 导入成功':'❌ 失败'); }

function generateWith(aid) { switchTab('generate'); document.getElementById('g-account').value=aid; }

// Generate
async function gatewayGenerate() {
  const payload = {
    kind: document.getElementById('g-kind').value,
    prompt: document.getElementById('g-prompt').value,
    model_name: document.getElementById('g-model').value || null,
    ratio: document.getElementById('g-ratio').value || null,
    resolution: document.getElementById('g-res').value || null,
    duration: document.getElementById('g-dur').value ? Number(document.getElementById('g-dur').value) : null,
    scene_id: document.getElementById('g-scene').value || null,
    account_id: document.getElementById('g-account').value ? Number(document.getElementById('g-account').value) : null,
  };
  document.getElementById('g-result').textContent = '提交中...';
  // Use internal admin endpoint (no API key needed)
  const r = await api('POST','/api/media/generate',payload);
  document.getElementById('g-result').textContent = JSON.stringify(r, null, 2);
  await loadTasks();
}

// Tasks
async function loadTasks() {
  const r = await api('GET','/api/tasks');
  state.tasks = r.items || [];
  renderTasks();
  updateStats();
}

function renderTasks() {
  const tbody = document.getElementById('tasks-tbody');
  tbody.innerHTML = state.tasks.slice(0,50).map(t => {
    const sClass = t.status==='created'?'tag-blue':t.status==='completed'?'tag-green':'tag-gray';
    return `<tr>
      <td>${t.id}</td>
      <td>${t.kind}</td>
      <td>${t.account_id||'-'}</td>
      <td><span class="tag ${sClass}">${t.status}</span></td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${(t.prompt||'').substring(0,40)}</td>
      <td style="font-family:monospace;font-size:11px">${(t.chat_id||'').substring(0,12)}</td>
      <td>${new Date((t.created_at||0)*1000).toLocaleString()}</td>
    </tr>`;
  }).join('');
}

// API Keys
async function loadApiKeys() {
  const r = await api('GET','/api/admin/apikeys');
  state.apikeys = r.items || [];
  renderApiKeys();
  updateStats();
}

function renderApiKeys() {
  const tbody = document.getElementById('apikeys-tbody');
  tbody.innerHTML = state.apikeys.map(k => {
    const keyShort = (k.key||'').substring(0,20)+'...';
    return `<tr>
      <td>${k.id}</td>
      <td style="font-family:monospace;font-size:11px" title="${k.key}">${keyShort} <button class="copy-btn" onclick="navigator.clipboard.writeText('${k.key}')">复制</button></td>
      <td>${k.name||'-'}</td>
      <td><span class="tag ${k.enabled?'tag-green':'tag-gray'}">${k.enabled?'启用':'停用'}</span></td>
      <td>${new Date((k.created_at||0)*1000).toLocaleString()}</td>
      <td>${k.last_used_at?new Date(k.last_used_at*1000).toLocaleString():'-'}</td>
      <td><button class="btn-sm btn-danger" onclick="deleteKey(${k.id})">删除</button></td>
    </tr>`;
  }).join('');
}

async function createApiKey() {
  const name = document.getElementById('ak-name').value;
  const r = await api('POST','/api/admin/apikeys?name='+encodeURIComponent(name));
  if (r.item) {
    const el = document.getElementById('ak-new');
    el.classList.remove('hidden');
    document.getElementById('ak-new-value').textContent = r.item.key;
    await loadApiKeys();
  }
}

function copyKey() {
  const val = document.getElementById('ak-new-value').textContent;
  navigator.clipboard.writeText(val);
  alert('已复制');
}

async function deleteKey(id) {
  if (!confirm('确认删除此 API Key？')) return;
  await api('DELETE','/api/admin/apikeys/'+id);
  await loadApiKeys();
}

// Usage
async function loadUsage() {
  const r = await api('GET','/api/admin/usage');
  state.usage = r.items || [];
  renderUsage();
}

function renderUsage() {
  const tbody = document.getElementById('usage-tbody');
  tbody.innerHTML = state.usage.slice(0,50).map(u => {
    return `<tr>
      <td>${u.id}</td>
      <td><span class="tag ${u.kind==='image'?'tag-blue':'tag-green'}">${u.kind}</span></td>
      <td>${u.account_email||u.account_id||'-'}</td>
      <td>${u.status}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${(u.prompt||'').substring(0,40)}</td>
      <td>${new Date((u.created_at||0)*1000).toLocaleString()}</td>
    </tr>`;
  }).join('');
}

// Settings
async function loadSettings() {
  state.settings = await api('GET','/api/admin/settings');
  document.getElementById('settings-raw').textContent = JSON.stringify(state.settings, null, 2);
  renderSettings();
}

function renderSettings() {
  const s = state.settings;
  document.getElementById('settings-form').innerHTML = `
    <div class="row">
      <div class="col"><label>服务端口</label><input id="s-port" value="${s.server?.port||8890}"></div>
      <div class="col"><label>管理员密码</label><input id="s-admin-pwd" placeholder="留空不修改"></div>
      <div class="col"><label>API基础URL</label><input id="s-base" value="${s.oreate?.base_url||''}"></div>
    </div>
    <div class="row" style="margin-top:12px">
      <div class="col"><label>默认图片模型</label><input id="s-img-model" value="${s.oreate?.default_image_model||''}"></div>
      <div class="col"><label>默认视频模型</label><input id="s-vid-model" value="${s.oreate?.default_video_model||''}"></div>
      <div class="col"><label>号池最低数</label><input id="s-min" value="${s.pool?.min_accounts||3}"></div>
    </div>
  `;
}

async function saveSettings() {
  const body = {
    server: { port: Number(document.getElementById('s-port').value) },
    oreate: {
      base_url: document.getElementById('s-base').value,
      default_image_model: document.getElementById('s-img-model').value,
      default_video_model: document.getElementById('s-vid-model').value,
    },
    pool: { min_accounts: Number(document.getElementById('s-min').value) },
  };
  const pwd = document.getElementById('s-admin-pwd').value;
  if (pwd) body.server.admin_password = pwd;
  const r = await api('PUT','/api/admin/settings', body);
  if (r.ok) { await loadSettings(); alert('✅ 已保存'); }
}

function updateStats() {
  const accounts = state.accounts || [];
  document.getElementById('st-total').textContent = accounts.length;
  document.getElementById('st-verified').textContent = accounts.filter(a=>a.status==='verified').length;
  document.getElementById('st-tasks').textContent = (state.tasks||[]).length;
  document.getElementById('st-apikeys').textContent = (state.apikeys||[]).length;
}

init();
</script>
</body>
</html>"""


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return HTMLResponse(ADMIN_HTML)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=CFG["server"]["host"], port=int(CFG["server"]["port"]))
