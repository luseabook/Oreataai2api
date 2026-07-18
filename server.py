import asyncio
import io
import base64
import copy
import hashlib
import hmac
import html
import json
import math
import os
import secrets
import sqlite3
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
import zipfile
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
import re
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError
from urllib3.exceptions import InsecureRequestWarning

from gateway.openai_compat import (
    OpenAICompatError,
    decode_video_id,
    image_size_to_ratio,
    openai_error_payload,
    openai_model_list,
    openai_model_name_for_provider,
    resolve_openai_model,
    split_input_reference_attachments,
    task_to_video_object,
    video_size_to_ratio,
    video_size_to_resolution,
)
from gateway.runtime import (
    SingleWorkerLock,
    validate_single_worker_configuration,
    worker_lock_path,
)
from gateway.watermark import WatermarkImageError, watermark_free_image_bytes
from gateway.admin_html import ADMIN_HTML
from gateway.mail_identity import (
    MAIL_DOMAIN_STATS,
    MAIL_DOMAIN_STATS_LOCK,
    generate_mailbox_local_part,
    generate_registration_password,
    rank_mail_domains,
    record_mail_domain_outcome,
    soft_order_mail_domains,
)
from gateway.registration_events import (
    REGISTRATION_EVENT_STEP_MESSAGES,
    REGISTRATION_PIPELINE_STEPS,
    registration_event_message,
)
from gateway.yyds_mail import YydsClient
from gateway.outlook_mail import (
    MailRouter,
    OutlookMailClient,
    parse_outlook_import_text,
)
from gateway.media_utils import (
    IMAGE_UPLOAD_EXTENSIONS,
    MEDIA_UPLOAD_EXTENSIONS,
    VIDEO_UPLOAD_EXTENSIONS,
    first_upload_key_entry,
    is_media_upload_extension,
    normalized_file_extension,
    parse_mp4_video_metadata,
    response_data_object,
)
from gateway.oreate_client import (
    OreateClient,
    OreateSession,
    configure_oreate_client_defaults,
    extract_user_mirror_metadata,
)
from gateway.oreate_stream import (
    MEDIA_URL_RE,
    classify_history_error,
    classify_sse_error,
    extract_generation_assets,
    parse_sse_line,
    parse_sse_lines,
)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "accounts.db"
MIGRATIONS_DIR = BASE_DIR / "migrations"
CONFIG_LOCK = threading.RLock()
SECRET_PLACEHOLDER = "__redacted__"
ENCRYPTED_SECRET_PREFIX = "enc:v1:"
ACCOUNT_SECRET_FIELDS = ("password", "ouid", "ouss")
API_KEY_SCOPE_LIST_FIELDS = (
    "allowed_kinds",
    "allowed_models",
    "allowed_scenes",
    "allowed_resolutions",
    "allowed_durations",
)
API_KEY_SCOPE_BOOL_DEFAULTS = {
    "allow_uploads": True,
    "allow_experimental": True,
}
API_LIST_KINDS = {"image", "video", "upload"}
MEDIA_ADMIN_KINDS = {"image", "video"}
TASK_LIST_STATUSES = {"queued", "running", "submitted", "hydrating", "completed", "failed", "cancelled", "expired"}
MAX_LIST_LIMIT = 200
MAX_LIST_OFFSET = 10000
UNSAFE_ADMIN_PASSWORDS = {"", "admin123", "CHANGE_ME", "changeme", "password"}
MAX_CLEAN_ASSET_BYTES = 30 * 1024 * 1024
REGISTRATION_THREADS_LOCK = threading.Lock()
REGISTRATION_THREADS: Dict[int, threading.Thread] = {}
POOL_MAINTENANCE_THREADS_LOCK = threading.Lock()
POOL_MAINTENANCE_THREADS: Dict[int, threading.Thread] = {}
POOL_MAINTENANCE_SCHEDULER_LOCK = threading.Lock()
POOL_MAINTENANCE_SCHEDULER_THREAD: Optional[threading.Thread] = None
POOL_MAINTENANCE_SCHEDULER_STOP = threading.Event()
POOL_MAINTENANCE_SCHEDULER_WAKE = threading.Event()
GATEWAY_ENVIRONMENT_ERROR_CODES = {"212361"}

DEFAULT_CONFIG = {
    "database": {
        "busy_timeout_ms": 5000,
        "journal_mode": "WAL",
        "synchronous": "NORMAL",
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8890,
        "admin_username": "admin",
        "admin_password": "",
        "encryption_key": "",
        "admin_session_ttl_hours": 12,
    },
    "deployment": {
        "allow_public_bind": False,
        "trust_reverse_proxy": False,
        "tls_terminated_by_proxy": False,
    },
    "oreate": {
        "base_url": "https://www.oreateai.com",
        "default_fr": "main",
        "request_timeout": 30,
        "verify_tls": True,
        "default_image_model": "Google Nano Banana 2",
        "default_image_ratio": "16:9",
        "default_image_resolution": "4K",
        "default_video_scene": "text_or_image",
        "default_video_model": "Seedance 2.0 Mini",
        "default_video_duration": 5,
        "default_video_resolution": "480",
        "default_video_ratio": "16:9",
        "video_stream_wait_seconds": 60,
        "video_stream_read_timeout_seconds": 20,
        "video_hydration_timeout_seconds": 600,
        "video_hydration_poll_interval_seconds": 10,
        "browser_worker_enabled": False,
        "browser_worker_node": "node",
        "browser_worker_timeout_seconds": 150,
        "browser_worker_readiness_timeout_seconds": 60,
        "browser_worker_node_modules": "",
        "chromium_executable": "",
    },
    "mail": {
        "provider": "yyds",
        "base_url": "https://maliapi.215.im/v1",
        "api_key": "",
        "api_mode": "auto",
        "preferred_domains": [],
        "verification_timeout_sec": 300,
    },
    "pool": {
        "min_accounts": 3,
        "maintain_target": 5,
        "valid_threshold_pct": 1.0,
        "maintain_check_interval": 300,
        "registration_concurrency": 3,
        "auto_maintain_max_register": 5,
        "auto_checkin_enabled": True,
        "checkin_timezone": "Asia/Shanghai",
        "generation_probe_prompt": "账号健康检测：请生成白色背景上的一个蓝色圆点",
    },
    "gateway": {
        "default_rate_limit_per_minute": 60,
        "default_daily_request_limit": 0,
        "default_daily_point_limit": 0,
        "idempotency_ttl_hours": 24,
        "idempotency_key_max_length": 255,
        "account_cooldown_seconds": 300,
        "account_risk_quarantine_seconds": 3600,
        "account_failover_max_attempts": 5,
        "account_failover_error_codes": ["200001"],
        "account_daily_point_gain": 30,
        "capacity_point_tiers": [30, 50, 100, 150, 300, 455, 600, 1000],
        "prompt_max_length": 4000,
        "request_id_max_length": 128,
        "upload_max_bytes": 104857600,
        "upload_read_chunk_bytes": 1048576,
        "sync_wait_seconds": 0,
        "sync_wait_max_seconds": 120,
        "enable_background_worker": True,
        "task_worker_poll_interval_seconds": 1,
        "worker_shutdown_timeout_seconds": 30,
        "running_task_stale_seconds": 300,
        "task_hydration_attempt_timeout_seconds": 0,
        "submitted_task_retry_interval_seconds": 10,
        "hydrating_task_retry_interval_seconds": 10,
        "submitted_task_expire_seconds": 600,
        "hydrating_task_expire_seconds": 600,
        "scene_policies": {
            "text_or_image": {
                "enabled": True,
                "experimental": False,
                "verification_status": "live_verified",
                "risk_level": "low",
            },
            "reference": {
                "enabled": False,
                "experimental": True,
                "verification_status": "unverified",
                "risk_level": "high",
            },
            "frame_based": {
                "enabled": False,
                "experimental": True,
                "verification_status": "unverified",
                "risk_level": "high",
            },
            "motion": {
                "enabled": False,
                "experimental": True,
                "verification_status": "unverified",
                "risk_level": "high",
            },
        },
        "model_policies": {},
    },
    "openai_compat": {
        "image_sync_timeout_seconds": 120,
        "max_sync_timeout_seconds": 120,
        "image_model_aliases": {},
        "video_model_aliases": {},
        "public_base_url": "",
        "cors_allowed_origins": ["https://canvas.best"],
        "asset_host_allowlist": ["cdn.oreateai.com"],
        "asset_insecure_tls_fallback_hosts": ["cdn.oreateai.com"],
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
    with CONFIG_LOCK:
        serialized = json.dumps(cfg, ensure_ascii=False, indent=2)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{CONFIG_PATH.name}.",
            suffix=".tmp",
            dir=str(CONFIG_PATH.parent),
        )
        temp_path = Path(temp_name)
        try:
            handle = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
            fd = -1
            with handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, CONFIG_PATH)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def active_encryption_key() -> str:
    env_key = str(os.environ.get("OREATE_ENCRYPTION_KEY") or "").strip()
    if env_key:
        return env_key
    server_cfg = CFG.get("server", {}) if isinstance(CFG.get("server", {}), dict) else {}
    return str(server_cfg.get("encryption_key") or "").strip()


def secret_fernet(required: bool = False) -> Optional[Fernet]:
    key = active_encryption_key()
    if not key:
        if required:
            raise RuntimeError("server encryption key is not configured")
        return None
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        if required:
            raise RuntimeError("server encryption key is invalid") from exc
        return None


def is_encrypted_secret(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(ENCRYPTED_SECRET_PREFIX)


def encrypt_secret_value(value: Any) -> str:
    raw = "" if value in (None, "") else str(value)
    if raw == "":
        return ""
    if is_encrypted_secret(raw):
        return raw
    cipher = secret_fernet(required=True)
    token = cipher.encrypt(raw.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_SECRET_PREFIX}{token}"


def decrypt_secret_value(value: Any, required: bool = False) -> str:
    raw = "" if value in (None, "") else str(value)
    if raw == "":
        return ""
    if not is_encrypted_secret(raw):
        return raw
    cipher = secret_fernet(required=required)
    if not cipher:
        return ""
    token = raw[len(ENCRYPTED_SECRET_PREFIX) :]
    try:
        return cipher.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        if required:
            raise RuntimeError("server encryption key cannot decrypt stored account secrets") from exc
        return ""


def gateway_cfg() -> Dict[str, Any]:
    return CFG.get("gateway", {}) if isinstance(CFG.get("gateway", {}), dict) else {}


def database_cfg() -> Dict[str, Any]:
    return CFG.get("database", {}) if isinstance(CFG.get("database", {}), dict) else {}


def oreate_cfg() -> Dict[str, Any]:
    return CFG.get("oreate", {}) if isinstance(CFG.get("oreate", {}), dict) else {}


def deployment_cfg() -> Dict[str, Any]:
    return CFG.get("deployment", {}) if isinstance(CFG.get("deployment", {}), dict) else {}


def openai_compat_cfg() -> Dict[str, Any]:
    return CFG.get("openai_compat", {}) if isinstance(CFG.get("openai_compat", {}), dict) else {}


def tls_verify_enabled() -> bool:
    return bool(oreate_cfg().get("verify_tls", True))


def float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def int_or_default(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def default_model_verification_status(kind: str) -> str:
    return "live_verified" if kind == "image" else "unit_tested"


def policy_defaults_for_scene(scene_id: str) -> Dict[str, Any]:
    defaults = {
        "enabled": True,
        "experimental": False,
        "verification_status": "live_verified",
        "risk_level": "low",
    }
    if scene_id in {"reference", "frame_based", "motion"}:
        defaults.update(
            {
                "enabled": False,
                "experimental": True,
                "verification_status": "unverified",
                "risk_level": "high",
            }
        )
    return defaults


def policy_defaults_for_model(kind: str) -> Dict[str, Any]:
    return {
        "enabled": True,
        "experimental": False,
        "verification_status": default_model_verification_status(kind),
        "risk_level": "low",
    }


def resolve_policy(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return deep_merge(base, override or {})


def model_data(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)
    return model.dict(exclude_none=True)


def public_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(cfg))
    if out.get("server", {}).get("admin_password"):
        out["server"]["admin_password"] = SECRET_PLACEHOLDER
    if out.get("server", {}).get("encryption_key"):
        out["server"]["encryption_key"] = SECRET_PLACEHOLDER
    if out.get("mail", {}).get("api_key"):
        out["mail"]["api_key"] = SECRET_PLACEHOLDER
    return out


def clean_settings_update(data: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(data))
    server_cfg = out.get("server")
    if isinstance(server_cfg, dict):
        server_cfg.pop("admin_username", None)
        server_cfg.pop("admin_password", None)
        server_cfg.pop("encryption_key", None)
    mail_cfg = out.get("mail")
    if isinstance(mail_cfg, dict) and mail_cfg.get("api_key") in (None, "", SECRET_PLACEHOLDER):
        mail_cfg.pop("api_key", None)
    return out


def public_account(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["has_password"] = bool(decrypt_secret_value(item.get("password")))
    item["ouid_preview"] = decrypt_secret_value(item.get("ouid"))[:12]
    for key in ("password", "ouid", "ouss", "model_info_json", "video_info_json", "point_balance_json"):
        item.pop(key, None)
    now = time.time()
    item["cooling"] = account_cooldown_remaining_seconds(item, now) > 0
    item["cooldown_remaining_seconds"] = int(account_cooldown_remaining_seconds(item, now))
    item["balance_status"] = account_balance_status(item)
    item["risk_status"] = account_risk_status(item)
    item["health_status"] = account_health_status(item, now)
    item["active_reserved_points"] = account_active_reserved_points(item)
    item["available_points"] = account_available_points(item)
    item["unprotected_spendable_points"] = account_spendable_points(item, 1)
    return item


def public_api_key(row: sqlite3.Row, reveal: bool = False) -> Dict[str, Any]:
    item = dict(row)
    key = item.get("key", "")
    item["key_preview"] = f"{key[:16]}..." if key else ""
    item["deleted"] = bool(item.get("deleted_at"))
    expired = item.get("expires_at") not in (None, "") and float_or_default(item.get("expires_at"), 0) <= time.time()
    if item["deleted"]:
        item["status"] = "deleted"
    elif not item.get("enabled"):
        item["status"] = "disabled"
    elif expired:
        item["status"] = "expired"
    else:
        item["status"] = "enabled"
    if item.get("client_name") is None and item.get("client_id") is not None:
        item["client_name"] = ""
    item["client_name"] = item.get("client_name") or ""
    for field in API_KEY_SCOPE_LIST_FIELDS:
        value = json_value_from_db(item.get(field))
        if field == "allowed_durations":
            values = [int_or_default(v, 0) for v in value] if isinstance(value, list) else []
            item[field] = [v for v in values if v > 0]
        else:
            values = [str(v).strip() for v in value] if isinstance(value, list) else []
            item[field] = [v for v in values if v]
    item["allow_uploads"] = bool(item.get("allow_uploads")) if item.get("allow_uploads") is not None else API_KEY_SCOPE_BOOL_DEFAULTS["allow_uploads"]
    item["allow_experimental"] = bool(item.get("allow_experimental")) if item.get("allow_experimental") is not None else API_KEY_SCOPE_BOOL_DEFAULTS["allow_experimental"]
    if not reveal:
        item.pop("key", None)
    return item


def public_client(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["status"] = item.get("status") or "active"
    return item


def public_admin_audit(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["details"] = json_value_from_db(item.pop("details_json", None)) or {}
    return item


def redact_nested_fields(value: Any, keys: Iterable[str]) -> Any:
    sensitive = set(keys)
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if key in sensitive and item not in (None, ""):
                out[key] = SECRET_PLACEHOLDER
            else:
                out[key] = redact_nested_fields(item, sensitive)
        return out
    if isinstance(value, list):
        return [redact_nested_fields(item, sensitive) for item in value]
    return value


def public_registration_result(item: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(item))
    out.pop("password", None)
    out = redact_nested_fields(
        out,
        {
            "password",
            "token",
            "tokenID",
            "tokenId",
            "token_id",
            "jt",
            "cookies",
            "cookie",
            "OUID",
            "ouss",
            "session",
            "sessionkey",
            "accessToken",
        },
    )
    artifact = out.get("verification_artifact")
    if isinstance(artifact, dict):
        for key in ("code", "link", "token", "tokenID", "tokenId"):
            if artifact.get(key) not in (None, ""):
                artifact[key] = SECRET_PLACEHOLDER
    mailbox = out.get("mailbox")
    if isinstance(mailbox, dict):
        mailbox.pop("token", None)
    return out


def point_bucket_amount(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("amount", "point", "points", "balance", "value"):
        if value.get(key) not in (None, ""):
            return value.get(key)
    return None


def normalize_account_point_detail(raw: Any) -> Dict[str, Any]:
    source = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    source = source if isinstance(source, dict) else {}
    daily = source.get("daily")
    if daily in (None, ""):
        daily = source.get("dailyPoint")
    if daily in (None, ""):
        daily = source.get("daily_point")
    bonus = source.get("bonus")
    if bonus in (None, ""):
        bonus = source.get("bonusPoint")
    if bonus in (None, ""):
        bonus = source.get("bonus_point")
    pro = source.get("pro")
    if pro in (None, ""):
        pro = source.get("proPoint")
    if pro in (None, ""):
        pro = source.get("pro_point")
    rest = source.get("restPoint")
    if rest in (None, ""):
        rest = source.get("rest_point")
    if rest in (None, ""):
        rest = source.get("restpoint")
    daily = point_bucket_amount(daily)
    pro = point_bucket_amount(pro)
    bonus = point_bucket_amount(bonus)
    rest = point_bucket_amount(rest)
    if rest in (None, "") and any(value not in (None, "") for value in (daily, pro, bonus)):
        rest = sum(int_or_default(value, 0) for value in (daily, pro, bonus))
    return {
        "point_balance_json": {
            "daily_point": None if daily in (None, "") else int_or_default(daily, 0),
            "pro_point": None if pro in (None, "") else int_or_default(pro, 0),
            "bonus_point": None if bonus in (None, "") else int_or_default(bonus, 0),
            "rest_point": None if rest in (None, "") else int_or_default(rest, 0),
        },
        "daily_point": None if daily in (None, "") else int_or_default(daily, 0),
        "bonus_point": None if bonus in (None, "") else int_or_default(bonus, 0),
        "rest_point": None if rest in (None, "") else int_or_default(rest, 0),
    }


def account_balance_value(row: sqlite3.Row) -> Optional[int]:
    item = dict(row)
    if item.get("rest_point") not in (None, ""):
        return int_or_default(item.get("rest_point"), 0)
    if item.get("daily_point") not in (None, "") or item.get("bonus_point") not in (None, ""):
        return int_or_default(item.get("daily_point"), 0) + int_or_default(item.get("bonus_point"), 0)
    raw = json_value_from_db(item.get("point_balance_json")) or {}
    source = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    if isinstance(source, dict):
        if source.get("restPoint") not in (None, ""):
            return int_or_default(source.get("restPoint"), 0)
        if source.get("rest_point") not in (None, ""):
            return int_or_default(source.get("rest_point"), 0)
        if source.get("restpoint") not in (None, ""):
            return int_or_default(source.get("restpoint"), 0)
        if source.get("daily") not in (None, "") or source.get("bonus") not in (None, ""):
            return int_or_default(source.get("daily"), 0) + int_or_default(source.get("bonus"), 0)
        if source.get("dailyPoint") not in (None, "") or source.get("bonusPoint") not in (None, ""):
            return int_or_default(source.get("dailyPoint"), 0) + int_or_default(source.get("bonusPoint"), 0)
        if source.get("daily_point") not in (None, "") or source.get("bonus_point") not in (None, ""):
            return int_or_default(source.get("daily_point"), 0) + int_or_default(source.get("bonus_point"), 0)
    return None


def account_active_reserved_points(row: sqlite3.Row) -> int:
    item = dict(row)
    return max(0, int_or_default(item.get("active_reserved_points"), 0))


def account_reserve_target_points(row: sqlite3.Row) -> int:
    item = dict(row)
    return max(0, int_or_default(item.get("reserve_target_points"), 0))


def account_available_points(row: sqlite3.Row) -> Optional[int]:
    balance = account_balance_value(row)
    if balance is None:
        return None
    return max(0, balance - account_active_reserved_points(row))


def account_spendable_points(row: sqlite3.Row, estimated_point_cost: Optional[int]) -> Optional[int]:
    available = account_available_points(row)
    if available is None:
        return None
    cost = max(0, int_or_default(estimated_point_cost, 0))
    reserve_target = account_reserve_target_points(row)
    if reserve_target > 0 and cost < reserve_target:
        return max(0, available - reserve_target)
    return available


def account_has_sufficient_balance(row: sqlite3.Row, estimated_point_cost: Optional[int]) -> bool:
    if estimated_point_cost in (None, ""):
        return True
    try:
        cost = int(float(estimated_point_cost))
    except (TypeError, ValueError):
        return True
    if cost <= 0:
        return True
    spendable = account_spendable_points(row, cost)
    return spendable is not None and spendable >= cost


def account_cooldown_remaining_seconds(row: sqlite3.Row, now: Optional[float] = None) -> float:
    now = time.time() if now is None else now
    cooldown_until = row.get("cooldown_until") if isinstance(row, dict) else row["cooldown_until"]
    if cooldown_until in (None, ""):
        return 0.0
    try:
        remaining = float(cooldown_until) - float(now)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, remaining)


def account_balance_status(row: sqlite3.Row) -> str:
    balance = account_balance_value(row)
    if balance is None:
        return "unknown"
    if balance <= 0:
        return "empty"
    if balance < 10:
        return "low"
    return "ok"


def account_risk_status(row: sqlite3.Row) -> str:
    status = str(row["status"] or "")
    if status == "invalid":
        return "invalid"
    return "clean"


def account_health_status(row: sqlite3.Row, now: Optional[float] = None) -> str:
    status = str(row["status"] or "")
    if status == "disabled":
        return "disabled"
    if status == "invalid":
        return "invalid"
    if status not in {"verified", "active"}:
        return "pending"
    if account_cooldown_remaining_seconds(row, now) > 0:
        return "cooling"
    if account_risk_status(row) == "risk_control":
        return "risk_control"
    if account_balance_status(row) in {"empty", "low"}:
        return "low_balance"
    return "healthy"


def account_has_schedulable_capability(row: sqlite3.Row) -> bool:
    caps = capabilities_from_account(row)
    image_models = [model for model in caps.get("image", {}).get("models") or [] if model.get("enabled", True)]
    video_models = [model for model in caps.get("video", {}).get("models") or [] if model.get("enabled", True)]
    video_scenes = [scene for scene in caps.get("video", {}).get("scenes") or [] if scene.get("enabled", True)]
    return bool(image_models or (video_models and video_scenes))


def account_is_ready_schedulable(row: sqlite3.Row) -> bool:
    return str(row["status"] or "") in {"verified", "active"} and account_has_schedulable_capability(row)


def account_pool_summary(rows: List[sqlite3.Row], now: Optional[float] = None) -> Dict[str, int]:
    current = time.time() if now is None else now
    summary = {
        "total": 0,
        "verified": 0,
        "healthy": 0,
        "cooling": 0,
        "low_balance": 0,
        "invalid": 0,
        "risk_control": 0,
        "balance_known": 0,
    }
    for row in rows:
        summary["total"] += 1
        if str(row["status"] or "") in {"verified", "active"}:
            summary["verified"] += 1
        if account_balance_value(row) is not None:
            summary["balance_known"] += 1
        health = account_health_status(row, current)
        schedulable = account_is_ready_schedulable(row)
        if health == "risk_control":
            pass
        elif health == "healthy":
            if schedulable:
                summary["healthy"] += 1
        elif health == "cooling":
            if schedulable:
                summary["cooling"] += 1
        elif health in summary:
            summary[health] += 1
        if account_risk_status(row) == "risk_control":
            summary["risk_control"] += 1
    return summary


def task_metrics_summary(rows: List[sqlite3.Row]) -> Dict[str, Any]:
    statuses = {
        "queued": 0,
        "running": 0,
        "submitted": 0,
        "hydrating": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "expired": 0,
    }
    error_codes: Dict[str, int] = {}
    for row in rows:
        status = str(row["status"] or "")
        if status in statuses:
            statuses[status] += 1
        error_code = str(row["error_code"] or "")
        if error_code:
            error_codes[error_code] = error_codes.get(error_code, 0) + 1
    queue_length = sum(statuses[key] for key in ("queued", "running", "submitted", "hydrating"))
    completed = statuses["completed"]
    failed = statuses["failed"] + statuses["cancelled"] + statuses["expired"]
    success_base = completed + failed
    success_rate = round((completed / success_base) * 100, 2) if success_base else 100.0
    return {
        **statuses,
        "queue_length": queue_length,
        "success_rate": success_rate,
        "error_codes": error_codes,
        "total": len(rows),
    }


def usage_metrics_summary() -> Dict[str, Any]:
    start = day_start_timestamp(time.time())
    conn = db_conn()
    row = conn.execute(
        """
        SELECT
            COUNT(*) as request_count,
            COALESCE(SUM(COALESCE(estimated_point_cost, 0)), 0) as estimated_point_cost,
            COALESCE(SUM(COALESCE(actual_point_cost, 0)), 0) as actual_point_cost
        FROM usage_log
        WHERE created_at>=?
        """,
        (start,),
    ).fetchone()
    error_rows = conn.execute(
        """
        SELECT COALESCE(error_code, '') as error_code, COUNT(*) as count
        FROM usage_log
        WHERE created_at>=? AND COALESCE(error_code, '') != ''
        GROUP BY error_code
        ORDER BY count DESC, error_code ASC
        """,
        (start,),
    ).fetchall()
    conn.close()
    error_codes = {r["error_code"]: r["count"] for r in error_rows}
    return {
        "today_requests": row["request_count"] if row else 0,
        "today_estimated_point_cost": row["estimated_point_cost"] if row else 0,
        "today_actual_point_cost": row["actual_point_cost"] if row else 0,
        "error_codes": error_codes,
    }



def update_account_balance_snapshot(account_id: int, balance_detail: Any) -> sqlite3.Row:
    snapshot = normalize_account_point_detail(balance_detail)
    now = time.time()
    conn = db_conn()
    conn.execute(
        """
        UPDATE accounts
        SET point_balance_json=?, rest_point=?, daily_point=?, bonus_point=?, balance_updated_at=?, updated_at=?
        WHERE id=?
        """,
        (
            encode_json_value(snapshot["point_balance_json"]),
            snapshot["rest_point"],
            snapshot["daily_point"],
            snapshot["bonus_point"],
            now,
            now,
            account_id,
        ),
    )
    row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    conn.commit()
    conn.close()
    if not row:
        raise HTTPException(404, "account not found")
    return row


def capture_account_balance_snapshot(account: sqlite3.Row) -> Optional[Dict[str, Any]]:
    try:
        session = CLIENT.session_from_account(account)
        detail = CLIENT.fetch_account_point_detail(session, account)
    except Exception:
        return None
    return normalize_account_point_detail(detail)


def balance_snapshot_fields(snapshot: Optional[Dict[str, Any]], prefix: str) -> Dict[str, Any]:
    if not snapshot:
        return {}
    return {
        f"{prefix}_json": snapshot.get("point_balance_json"),
        f"{prefix}_rest_point": snapshot.get("rest_point"),
        f"{prefix}_daily_point": snapshot.get("daily_point"),
        f"{prefix}_bonus_point": snapshot.get("bonus_point"),
    }


def balance_snapshot_from_row(row: Optional[Any], prefix: str) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    raw_json = row.get(f"{prefix}_json") if isinstance(row, dict) else row[f"{prefix}_json"]
    rest = row.get(f"{prefix}_rest_point") if isinstance(row, dict) else row[f"{prefix}_rest_point"]
    daily = row.get(f"{prefix}_daily_point") if isinstance(row, dict) else row[f"{prefix}_daily_point"]
    bonus = row.get(f"{prefix}_bonus_point") if isinstance(row, dict) else row[f"{prefix}_bonus_point"]
    point_balance_json = json_value_from_db(raw_json)
    if not isinstance(point_balance_json, dict):
        if rest in (None, "") and daily in (None, "") and bonus in (None, ""):
            return None
        point_balance_json = {
            "rest_point": None if rest in (None, "") else int_or_default(rest, 0),
            "daily_point": None if daily in (None, "") else int_or_default(daily, 0),
            "bonus_point": None if bonus in (None, "") else int_or_default(bonus, 0),
        }
    return {
        "point_balance_json": point_balance_json,
        "rest_point": point_balance_json.get("rest_point"),
        "daily_point": point_balance_json.get("daily_point"),
        "bonus_point": point_balance_json.get("bonus_point"),
    }


def actual_point_cost_from_balance_snapshots(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> Optional[int]:
    if not before or not after:
        return None
    before_rest = before.get("rest_point")
    after_rest = after.get("rest_point")
    if before_rest in (None, "") or after_rest in (None, ""):
        return None
    return max(0, int_or_default(before_rest, 0) - int_or_default(after_rest, 0))


def fetch_account_row(account_id: int) -> Optional[sqlite3.Row]:
    conn = db_conn()
    row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    conn.close()
    return row


def build_failed_task_result_payload(task_id: int, account_id: Optional[int], error_code: str, message: str, status_code: int = 503) -> Dict[str, Any]:
    task_row = fetch_task_row(task_id)
    balance_before = balance_snapshot_from_row(task_row, "balance_before")
    balance_after = None
    if account_id:
        account_row = fetch_account_row(int(account_id))
        if account_row:
            balance_after = capture_account_balance_snapshot(account_row)
            if balance_after:
                update_account_balance_snapshot(account_row["id"], balance_after["point_balance_json"])
    payload = {
        "account_id": account_id,
        "error_code": error_code,
        "error_message": message,
        "response_summary": json.dumps({"code": error_code, "message": message}, ensure_ascii=False),
        "status_code": status_code,
        "actual_point_cost": actual_point_cost_from_balance_snapshots(balance_before, balance_after),
    }
    payload.update(balance_snapshot_fields(balance_before, "balance_before"))
    payload.update(balance_snapshot_fields(balance_after, "balance_after"))
    return payload


def extract_token_id_from_link(link: str) -> str:
    if not link:
        return ""
    cleaned = html.unescape(str(link)).replace("&amp;", "&")
    params = parse_qs(urlparse(cleaned).query)
    token = params.get("tokenID", [""])[0] or params.get("tokenId", [""])[0]
    if token:
        return str(token)
    match = re.search(r"[?&]tokenID=([^&#\s\"'<>]+)", cleaned, re.I)
    return unquote(match.group(1)) if match else ""


def is_unsafe_admin_password(password: str) -> bool:
    return password.strip() in UNSAFE_ADMIN_PASSWORDS


def localized_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("zh", "en", "zh-TW"):
            text = value.get(key)
            if isinstance(text, str) and text:
                return text
        for text in value.values():
            if isinstance(text, str) and text:
                return text
    return ""


def normalize_ratios(value: Any) -> List[str]:
    ratios = []
    if isinstance(value, list):
        for item in value:
            ratio = item.get("ratio") if isinstance(item, dict) else item
            if isinstance(ratio, str) and ratio and ratio not in ratios:
                ratios.append(ratio)
    return ratios


def normalize_option_values(value: Any, key: str = "value") -> List[Any]:
    values = []
    if isinstance(value, list):
        for item in value:
            option = item.get(key) if isinstance(item, dict) else item
            if option not in (None, "") and option not in values:
                values.append(option)
    return values


def model_policy_for(kind: str, model_name: str) -> Dict[str, Any]:
    policy_map = gateway_cfg().get("model_policies", {})
    default_policy = policy_defaults_for_model(kind)
    if not isinstance(policy_map, dict):
        return default_policy
    override = policy_map.get(model_name)
    if isinstance(override, dict):
        return resolve_policy(default_policy, override)
    return default_policy


def scene_policy_for(scene_id: str) -> Dict[str, Any]:
    policy_map = gateway_cfg().get("scene_policies", {})
    default_policy = policy_defaults_for_scene(scene_id)
    if not isinstance(policy_map, dict):
        return default_policy
    override = policy_map.get(scene_id)
    if isinstance(override, dict):
        return resolve_policy(default_policy, override)
    return default_policy


def normalize_image_models(image_info: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    models = []
    factories = (image_info or {}).get("data", {}).get("factory", [])
    if not isinstance(factories, list):
        return models
    for factory in factories:
        if not isinstance(factory, dict):
            continue
        factory_name = factory.get("modelFactoryName", "")
        for model in factory.get("models", []) or []:
            if not isinstance(model, dict):
                continue
            name = model.get("modelName", "")
            if not name:
                continue
            policy = model_policy_for("image", name)
            models.append({
                "name": name,
                "factory": factory_name,
                "description": localized_text(model.get("modelDesc")),
                "icon": model.get("modelIcon") or factory.get("modelIcon") or "",
                "resolutions": model.get("resolution") if isinstance(model.get("resolution"), list) else [],
                "ratios": normalize_ratios(model.get("size")),
                "point_cost": model.get("pointCost") if isinstance(model.get("pointCost"), list) else [],
                "enabled": bool(policy.get("enabled", True)),
                "experimental": bool(policy.get("experimental", False)),
                "verification_status": str(policy.get("verification_status") or default_model_verification_status("image")),
                "risk_level": str(policy.get("risk_level") or "low"),
            })
    return models


def normalize_video_models(video_info: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    models = []
    raw_models = (video_info or {}).get("models", {}).get("data", {}).get("models", [])
    if not isinstance(raw_models, list):
        return models
    for model in raw_models:
        if not isinstance(model, dict):
            continue
        name = model.get("modelName", "")
        if not name:
            continue
        resolutions = model.get("videoResolution")
        policy = model_policy_for("video", name)
        models.append({
            "name": name,
            "description": localized_text(model.get("description")),
            "icon": model.get("modelIcon") or "",
            "ai_type": model.get("aiType"),
            "durations": normalize_option_values(model.get("duration")),
            "resolutions": resolutions if isinstance(resolutions, list) else [],
            "ratios": normalize_ratios(model.get("videoSize")),
            "supports_audio": bool(model.get("supportAudio")),
            "supports_modify_size": bool(model.get("supportModifySize")),
            "point_cost_image": model.get("pointCostImage") if isinstance(model.get("pointCostImage"), list) else [],
            "point_cost_reference": model.get("pointCostReference") if isinstance(model.get("pointCostReference"), list) else [],
            "point_cost_motion": model.get("pointCostMotion") if isinstance(model.get("pointCostMotion"), list) else [],
            "enabled": bool(policy.get("enabled", True)),
            "experimental": bool(policy.get("experimental", False)),
            "verification_status": str(policy.get("verification_status") or default_model_verification_status("video")),
            "risk_level": str(policy.get("risk_level") or "low"),
        })
    return models


def normalize_video_scenes(video_info: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scenes = []
    raw_scenes = (video_info or {}).get("scenes", {}).get("data", {}).get("scenes", [])
    if not isinstance(raw_scenes, list):
        return scenes
    for scene in raw_scenes:
        if not isinstance(scene, dict):
            continue
        scene_id = scene.get("sceneId", "")
        if not scene_id:
            continue
        policy = scene_policy_for(scene_id)
        scenes.append({
            "scene_id": scene_id,
            "name": localized_text(scene.get("sceneName")),
            "description": localized_text(scene.get("description")),
            "icon": scene.get("sceneIcon") or "",
            "enabled": bool(policy.get("enabled", True)),
            "experimental": bool(policy.get("experimental", False)),
            "verification_status": str(policy.get("verification_status") or "live_verified"),
            "risk_level": str(policy.get("risk_level") or "low"),
        })
    return scenes


def normalize_capabilities(image_info: Optional[Dict[str, Any]], video_info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "image": {"models": normalize_image_models(image_info)},
        "video": {
            "models": normalize_video_models(video_info),
            "scenes": normalize_video_scenes(video_info),
        },
    }


def json_from_db(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def json_value_from_db(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def parse_boolean_flag(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    raise HTTPException(400, f"{field} must be a boolean")


def normalize_string_scope_values(value: Any, field: str) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        parts = re.split(r"[\r\n,]", value)
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        raise HTTPException(400, f"{field} must be a list or comma-separated string")
    out: List[str] = []
    for item in parts:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def normalize_int_scope_values(value: Any, field: str) -> List[int]:
    items = normalize_string_scope_values(value, field) if isinstance(value, str) else (list(value) if isinstance(value, (list, tuple, set)) else ([] if value in (None, "") else None))
    if items is None:
        raise HTTPException(400, f"{field} must be a list or comma-separated string")
    out: List[int] = []
    for item in items:
        try:
            number = int(item)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{field} must contain integers")
        if number > 0 and number not in out:
            out.append(number)
    return out


def encode_scope_values(values: List[Any]) -> Optional[str]:
    return json.dumps(values, ensure_ascii=False) if values else None


def scope_values_from_db(raw: Any, field: str) -> List[Any]:
    values = json_value_from_db(raw) if isinstance(raw, str) else raw
    if not isinstance(values, list):
        return []
    if field == "allowed_durations":
        return [v for v in (int_or_default(item, 0) for item in values) if v > 0]
    return [text for text in (str(item).strip() for item in values) if text]


def migrate_plaintext_account_secrets(conn: sqlite3.Connection) -> None:
    if not active_encryption_key():
        return
    rows = conn.execute("SELECT id,password,ouid,ouss FROM accounts").fetchall()
    for row in rows:
        updates: Dict[str, Any] = {}
        for field in ACCOUNT_SECRET_FIELDS:
            raw = row[field]
            if raw not in (None, "") and not is_encrypted_secret(raw):
                updates[field] = encrypt_secret_value(raw)
        if updates:
            updates["updated_at"] = time.time()
            set_clause = ", ".join(f"{name}=?" for name in updates.keys())
            conn.execute(
                f"UPDATE accounts SET {set_clause} WHERE id=?",
                tuple(updates.values()) + (row["id"],),
            )


def capabilities_from_account(account: sqlite3.Row) -> Dict[str, Any]:
    return normalize_capabilities(json_from_db(account["model_info_json"]), json_from_db(account["video_info_json"]))


def find_capability_model(models: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for model in models:
        if model.get("name") == name:
            return model
    return None


def find_capability_scene(scenes: List[Dict[str, Any]], scene_id: str) -> Optional[Dict[str, Any]]:
    for scene in scenes:
        if scene.get("scene_id") == scene_id:
            return scene
    return None


VIDEO_ATTACHMENT_OPTION_FIELDS = (
    "image",
    "first_frame",
    "last_frame",
    "reference_images",
    "reference_videos",
    "motion_video",
    "character_image",
    "ref_duration",
    "ref_total_duration",
    "motion_duration",
    "keep_original_sound",
    "is_audio",
    "ai_type",
)


def copy_optional_body_fields(options: Dict[str, Any], body: Any, fields: Iterable[str]) -> Dict[str, Any]:
    for field in fields:
        value = getattr(body, field, None)
        if value is not None:
            options[field] = value
    return options


def effective_generation_options(body: Any, caps: Dict[str, Any]) -> Dict[str, Any]:
    if body.kind == "image":
        options = {
            "model_name": body.model_name or CFG["oreate"]["default_image_model"],
            "ratio": body.ratio or CFG["oreate"]["default_image_ratio"],
            "resolution": body.resolution or CFG["oreate"]["default_image_resolution"],
        }
        return copy_optional_body_fields(options, body, ("image",))
    if body.kind == "video":
        options = {
            "model_name": body.model_name or CFG["oreate"]["default_video_model"],
            "ratio": body.ratio or CFG["oreate"]["default_video_ratio"],
            "resolution": body.resolution or CFG["oreate"]["default_video_resolution"],
            "duration": body.duration or CFG["oreate"]["default_video_duration"],
            "scene_id": body.scene_id or CFG["oreate"]["default_video_scene"],
        }
        return copy_optional_body_fields(options, body, VIDEO_ATTACHMENT_OPTION_FIELDS)
    return {}


def ensure_capability_value(field: str, value: Any, allowed: List[Any], code: str) -> None:
    if allowed and value not in allowed:
        raise GatewayAPIError(
            422,
            code,
            f"{field} is not supported",
            {"field": field, "value": value, "allowed": allowed},
        )


def validate_generation_options(kind: str, options: Dict[str, Any], caps: Dict[str, Any]) -> None:
    if kind not in ("image", "video"):
        raise GatewayAPIError(400, "UNSUPPORTED_KIND", f"unsupported kind: {kind}", {"field": "kind"})
    models = caps.get(kind, {}).get("models") or []
    if not models:
        raise GatewayAPIError(503, "CAPABILITIES_UNAVAILABLE", "model capabilities are unavailable; refresh model capabilities first")
    model = find_capability_model(models, options.get("model_name") or "")
    if not model:
        raise GatewayAPIError(
            422,
            "INVALID_MODEL",
            f"model_name is not supported for {kind} generation",
            {"field": "model_name", "value": options.get("model_name"), "allowed": [m.get("name") for m in models]},
        )
    if not bool(model.get("enabled", True)):
        raise GatewayAPIError(
            422,
            "MODEL_DISABLED",
            f"model_name is disabled by policy: {options.get('model_name')}",
            {"field": "model_name", "value": options.get("model_name"), "verification_status": model.get("verification_status"), "experimental": bool(model.get("experimental"))},
        )
    ensure_capability_value("resolution", options.get("resolution"), model.get("resolutions") or [], "INVALID_RESOLUTION")
    ensure_capability_value("ratio", options.get("ratio"), model.get("ratios") or [], "INVALID_RATIO")
    if kind == "video":
        scene_id = options.get("scene_id") or CFG["oreate"]["default_video_scene"]
        scenes = caps.get("video", {}).get("scenes") or []
        scene = find_capability_scene(scenes, scene_id)
        if not scene:
            raise GatewayAPIError(
                422,
                "INVALID_SCENE",
                "scene_id is not supported for video generation",
                {"field": "scene_id", "value": scene_id, "allowed": [s.get("scene_id") for s in scenes]},
            )
        if not bool(scene.get("enabled", True)):
            raise GatewayAPIError(
                422,
                "EXPERIMENTAL_SCENE_DISABLED",
                f"video scene is disabled by policy: {scene_id}",
                {
                    "field": "scene_id",
                    "value": scene_id,
                    "verification_status": scene.get("verification_status"),
                    "experimental": bool(scene.get("experimental")),
                },
            )
        ensure_capability_value("duration", options.get("duration"), model.get("durations") or [], "INVALID_DURATION")


def video_cost_table_for_scene(model: Dict[str, Any], scene_id: str) -> List[Dict[str, Any]]:
    if scene_id == "reference":
        return model.get("point_cost_reference") or []
    if scene_id == "motion":
        return model.get("point_cost_motion") or []
    return model.get("point_cost_image") or []


def cost_option_value(options: Dict[str, Any], key: str) -> Any:
    if key == "audio":
        return bool(options.get("is_audio"))
    if key == "motDuration":
        return options.get("motion_duration") or options.get("duration")
    if key == "refDuration":
        return options.get("ref_duration")
    return options.get(key)


def cost_item_matches_options(item: Dict[str, Any], options: Dict[str, Any]) -> bool:
    for key in ("duration", "resolution", "audio", "motDuration", "refDuration"):
        if key in item and item.get(key) not in (None, "", cost_option_value(options, key)):
            return False
    return True


def matched_video_cost_item(model: Dict[str, Any], options: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for item in video_cost_table_for_scene(model, options.get("scene_id") or ""):
        if isinstance(item, dict) and cost_item_matches_options(item, options):
            return item
    return None


def estimate_point_cost(kind: str, options: Dict[str, Any], caps: Dict[str, Any]) -> Optional[int]:
    models = caps.get(kind, {}).get("models") or []
    model = find_capability_model(models, options.get("model_name") or "")
    if not model:
        return None
    costs = model.get("point_cost") if kind == "image" else video_cost_table_for_scene(model, options.get("scene_id") or "")
    for item in costs or []:
        if not isinstance(item, dict):
            continue
        if kind == "image" and item.get("resolution") == options.get("resolution"):
            return item.get("point")
        if kind == "video" and cost_item_matches_options(item, options):
            return item.get("point")
    return None


def build_image_config(options: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "modelName": options.get("model_name") or "",
        "ratio": options.get("ratio") or "",
        "resolution": options.get("resolution") or "",
    }


def upload_attachment_data(value: Any) -> Dict[str, Any]:
    if isinstance(value, BaseModel):
        return model_data(value)
    return dict(value) if isinstance(value, dict) else {}


def upload_attachment_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [upload_attachment_data(item) for item in value if upload_attachment_data(item)]


def upload_object_value(value: Any) -> str:
    item = upload_attachment_data(value)
    for key in ("bosUrl", "bos_url", "object", "bosObjectPath", "url"):
        candidate = item.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return ""


def normalize_upload_attachment(value: Any) -> Dict[str, Any]:
    item = upload_attachment_data(value)
    bos_value = upload_object_value(item)
    normalized: Dict[str, Any] = {
        "bos_url": bos_value,
        "docId": item.get("docId") or item.get("docID") or "",
        "doc_title": item.get("fileName") or item.get("doc_title") or item.get("title") or item.get("name") or "",
        "doc_type": item.get("fileExt") or item.get("doc_type") or item.get("contentType") or "",
        "size": item.get("originSize") or item.get("fileSize") or item.get("size") or 0,
        "bosUrl": bos_value,
        "flag": "upload",
        "type": "file",
        "status": 1,
    }
    duration = item.get("videoDurationSec")
    if isinstance(duration, (int, float)) and duration > 0:
        normalized["videoDurationSec"] = duration
    return normalized


def require_upload_object(options: Dict[str, Any], field: str) -> Dict[str, Any]:
    item = upload_attachment_data(options.get(field))
    if not upload_object_value(item):
        raise GatewayAPIError(
            422,
            "MISSING_VIDEO_ATTACHMENT",
            f"{field} is required for {options.get('scene_id') or 'video'} scene",
            {"field": field},
        )
    return item


def optional_upload_object(options: Dict[str, Any], field: str) -> Dict[str, Any]:
    item = upload_attachment_data(options.get(field))
    if item and not upload_object_value(item):
        raise GatewayAPIError(
            422,
            "MISSING_VIDEO_ATTACHMENT",
            f"{field} must contain an uploaded object path",
            {"field": field},
        )
    return item


def require_upload_object_list(options: Dict[str, Any], field: str) -> List[Dict[str, Any]]:
    items = upload_attachment_list(options.get(field))
    invalid = [item for item in items if not upload_object_value(item)]
    if invalid:
        raise GatewayAPIError(
            422,
            "MISSING_VIDEO_ATTACHMENT",
            f"{field} contains an item without an uploaded object path",
            {"field": field},
        )
    return items


def first_positive_number(values: Iterable[Any], fallback: Any = "") -> Any:
    for value in values:
        if isinstance(value, (int, float)) and value > 0:
            return value
    return fallback


def resolve_video_ai_type(options: Dict[str, Any], model: Optional[Dict[str, Any]]) -> Optional[Any]:
    if options.get("ai_type") is not None:
        return options.get("ai_type")
    item = matched_video_cost_item(model or {}, options)
    if isinstance(item, dict) and item.get("aiType") is not None:
        return item.get("aiType")
    return (model or {}).get("ai_type")


def known_model_option_values(model: Optional[Dict[str, Any]], field: str) -> Optional[List[Any]]:
    if not isinstance(model, dict) or field not in model:
        return None
    value = model.get(field)
    return value if isinstance(value, list) else []


def should_send_model_option(model: Optional[Dict[str, Any]], field: str) -> bool:
    values = known_model_option_values(model, field)
    return values is None or bool(values)


def should_send_video_model_option(scene_id: str, model: Optional[Dict[str, Any]], field: str) -> bool:
    if scene_id == "motion" and field in {"ratios", "durations"}:
        return False
    return should_send_model_option(model, field)


def video_audio_enabled_for_scene(scene_id: str, options: Dict[str, Any], model: Optional[Dict[str, Any]]) -> bool:
    if scene_id == "motion":
        return False
    return bool(options.get("is_audio")) if (not isinstance(model, dict) or model.get("supports_audio")) else False


def build_video_message_attachments(options: Dict[str, Any]) -> List[Dict[str, Any]]:
    scene_id = options.get("scene_id") or CFG["oreate"]["default_video_scene"]
    raw: List[Dict[str, Any]] = []
    if scene_id == "text_or_image":
        image = optional_upload_object(options, "image")
        if image:
            raw.append(image)
    elif scene_id == "frame_based":
        raw.extend([require_upload_object(options, "first_frame"), require_upload_object(options, "last_frame")])
    elif scene_id == "reference":
        raw.extend(require_upload_object_list(options, "reference_images"))
        raw.extend(require_upload_object_list(options, "reference_videos"))
        if not raw:
            raise GatewayAPIError(
                422,
                "MISSING_VIDEO_ATTACHMENT",
                "at least one reference image or video is required for reference scene",
                {"field": "reference_images/reference_videos"},
            )
    elif scene_id == "motion":
        raw.extend([require_upload_object(options, "motion_video"), require_upload_object(options, "character_image")])
    return [normalize_upload_attachment(item) for item in raw]


def build_video_config(options: Dict[str, Any], model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    scene_id = options.get("scene_id") or CFG["oreate"]["default_video_scene"]
    config: Dict[str, Any] = {
        "modelName": options.get("model_name") or "",
        "ratio": (options.get("ratio") or "") if should_send_video_model_option(scene_id, model, "ratios") else "",
        "resolution": str(options.get("resolution") or "") if should_send_video_model_option(scene_id, model, "resolutions") else "",
        "isAudio": video_audio_enabled_for_scene(scene_id, options, model),
        "scene": scene_id,
    }
    if should_send_video_model_option(scene_id, model, "durations"):
        config["duration"] = options.get("duration") or CFG["oreate"]["default_video_duration"]
    ai_type = resolve_video_ai_type(options, model)
    config["aiType"] = ai_type if ai_type is not None else 0
    if scene_id == "text_or_image":
        config["textOrImage"] = {"image": upload_object_value(optional_upload_object(options, "image"))}
    elif scene_id == "frame_based":
        config["frameBased"] = {
            "firstFrame": upload_object_value(require_upload_object(options, "first_frame")),
            "lastFrame": upload_object_value(require_upload_object(options, "last_frame")),
        }
    elif scene_id == "reference":
        reference_images = require_upload_object_list(options, "reference_images")
        reference_videos = require_upload_object_list(options, "reference_videos")
        if not reference_images and not reference_videos:
            raise GatewayAPIError(
                422,
                "MISSING_VIDEO_ATTACHMENT",
                "at least one reference image or video is required for reference scene",
                {"field": "reference_images/reference_videos"},
            )
        ref_total_duration = options.get("ref_total_duration")
        if ref_total_duration is None:
            ref_total_duration = first_positive_number(
                (item.get("videoDurationSec") for item in reference_videos),
                config.get("duration") or CFG["oreate"]["default_video_duration"],
            )
        config["reference"] = {
            "referenceImages": [upload_object_value(item) for item in reference_images if upload_object_value(item)],
            "referenceVideos": [upload_object_value(item) for item in reference_videos if upload_object_value(item)],
            "refDuration": options.get("ref_duration") or "2-5",
            "refTotalDuration": ref_total_duration,
            "keepOriginalSound": bool(options.get("keep_original_sound")),
        }
    elif scene_id == "motion":
        motion_video = require_upload_object(options, "motion_video")
        character_image = require_upload_object(options, "character_image")
        config["motion"] = {
            "characterImage": upload_object_value(character_image),
            "motionVideo": upload_object_value(motion_video),
            "motDuration": options.get("motion_duration") or motion_video.get("videoDurationSec") or config.get("duration") or CFG["oreate"]["default_video_duration"],
            "keepOriginalSound": bool(options.get("keep_original_sound")),
        }
    return config


class UpstreamGenerationError(RuntimeError):
    def __init__(self, error: Dict[str, str]):
        self.error = error
        super().__init__(f"{error.get('code')}: {error.get('message')}")


class TaskCancelledError(RuntimeError):
    pass


def request_hash_for_generation(body: Any) -> str:
    data = model_data(body) if isinstance(body, BaseModel) else dict(body)
    stable = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def find_idempotency_record(api_key_id: int, idempotency_key: str) -> Optional[sqlite3.Row]:
    if not idempotency_key:
        return None
    conn = db_conn()
    row = conn.execute(
        "SELECT * FROM idempotency_keys WHERE api_key_id=? AND idempotency_key=?",
        (api_key_id, idempotency_key),
    ).fetchone()
    conn.close()
    return row


def reserve_idempotency_record(
    api_key_id: int,
    idempotency_key: str,
    request_hash: str,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    if not idempotency_key:
        return {"state": "disabled", "record": None}
    current = time.time() if now is None else float(now)
    ttl_hours = max(0.0, float_or_default(gateway_cfg().get("idempotency_ttl_hours"), 24.0))
    cutoff = current - (ttl_hours * 3600.0)
    conn = db_conn()
    try:
        row = conn.execute(
            "SELECT * FROM idempotency_keys WHERE api_key_id=? AND idempotency_key=?",
            (api_key_id, idempotency_key),
        ).fetchone()
        if row and (ttl_hours <= 0 or float_or_default(row["created_at"], 0) >= cutoff):
            record = dict(row)
            if record.get("request_hash") != request_hash:
                state = "conflict"
            elif int(record.get("status_code") or 0) > 0:
                state = "replay"
            else:
                state = "pending"
            return {"state": state, "record": record}

        conn.execute("BEGIN IMMEDIATE")
        if ttl_hours > 0:
            conn.execute(
                "DELETE FROM idempotency_keys WHERE api_key_id=? AND idempotency_key=? AND created_at<?",
                (api_key_id, idempotency_key, cutoff),
            )
        row = conn.execute(
            "SELECT * FROM idempotency_keys WHERE api_key_id=? AND idempotency_key=?",
            (api_key_id, idempotency_key),
        ).fetchone()
        if row:
            record = dict(row)
            if record.get("request_hash") != request_hash:
                state = "conflict"
            elif int(record.get("status_code") or 0) > 0:
                state = "replay"
            else:
                state = "pending"
            conn.commit()
            return {"state": state, "record": record}
        conn.execute(
            """
            INSERT INTO idempotency_keys(
                api_key_id,idempotency_key,request_hash,status_code,response_json,task_id,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (api_key_id, idempotency_key, request_hash, 0, "{}", None, current),
        )
        row = conn.execute(
            "SELECT * FROM idempotency_keys WHERE api_key_id=? AND idempotency_key=?",
            (api_key_id, idempotency_key),
        ).fetchone()
        conn.commit()
        return {"state": "reserved", "record": dict(row) if row else None}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_idempotency_reservation(api_key_id: int, idempotency_key: str, request_hash: str) -> None:
    if not idempotency_key:
        return
    conn = db_conn()
    conn.execute(
        """
        DELETE FROM idempotency_keys
        WHERE api_key_id=? AND idempotency_key=? AND request_hash=? AND status_code=0
        """,
        (api_key_id, idempotency_key, request_hash),
    )
    conn.commit()
    conn.close()


def save_idempotency_record(api_key_id: int, idempotency_key: str, request_hash: str, status_code: int, response: Dict[str, Any], task_id: Optional[int]) -> None:
    if not idempotency_key:
        return
    conn = db_conn()
    result = conn.execute(
        """
        UPDATE idempotency_keys
        SET status_code=?, response_json=?, task_id=?
        WHERE api_key_id=? AND idempotency_key=? AND request_hash=?
        """,
        (status_code, json.dumps(response, ensure_ascii=False), task_id, api_key_id, idempotency_key, request_hash),
    )
    if result.rowcount == 0:
        conn.execute(
            """
            INSERT INTO idempotency_keys(api_key_id,idempotency_key,request_hash,status_code,response_json,task_id,created_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (api_key_id, idempotency_key, request_hash, status_code, json.dumps(response, ensure_ascii=False), task_id, time.time()),
        )
    conn.commit()
    conn.close()


def get_api_key_record(api_key_id: int) -> sqlite3.Row:
    conn = db_conn()
    row = conn.execute("SELECT * FROM api_keys WHERE id=?", (api_key_id,)).fetchone()
    conn.close()
    if not row:
        raise GatewayAPIError(401, "UNAUTHORIZED", "valid API key required (header: Authorization: Bearer <key>)")
    return row


def resolve_api_key_policy(row: sqlite3.Row) -> Dict[str, Any]:
    gateway_cfg = CFG.get("gateway", {})
    return {
        "rate_limit_per_minute": int(
            row["rate_limit_per_minute"]
            if row["rate_limit_per_minute"] is not None
            else gateway_cfg.get("default_rate_limit_per_minute") or 0
        ),
        "daily_request_limit": int(
            row["daily_request_limit"]
            if row["daily_request_limit"] is not None
            else gateway_cfg.get("default_daily_request_limit") or 0
        ),
        "daily_point_limit": int(
            row["daily_point_limit"]
            if row["daily_point_limit"] is not None
            else gateway_cfg.get("default_daily_point_limit") or 0
        ),
        "allowed_kinds": scope_values_from_db(row["allowed_kinds"], "allowed_kinds"),
        "allowed_models": scope_values_from_db(row["allowed_models"], "allowed_models"),
        "allowed_scenes": scope_values_from_db(row["allowed_scenes"], "allowed_scenes"),
        "allowed_resolutions": scope_values_from_db(row["allowed_resolutions"], "allowed_resolutions"),
        "allowed_durations": scope_values_from_db(row["allowed_durations"], "allowed_durations"),
        "allow_uploads": bool(row["allow_uploads"]) if row["allow_uploads"] is not None else API_KEY_SCOPE_BOOL_DEFAULTS["allow_uploads"],
        "allow_experimental": bool(row["allow_experimental"]) if row["allow_experimental"] is not None else API_KEY_SCOPE_BOOL_DEFAULTS["allow_experimental"],
    }


def request_uses_uploaded_media(options: Dict[str, Any]) -> bool:
    for field in ("image", "first_frame", "last_frame", "motion_video", "character_image"):
        if isinstance(options.get(field), dict) and options.get(field):
            return True
    for field in ("reference_images", "reference_videos"):
        if isinstance(options.get(field), list) and options.get(field):
            return True
    return False


def enforce_api_key_scope(policy: Dict[str, Any], kind: str, options: Dict[str, Any], caps: Dict[str, Any], request_id: str) -> None:
    allowed_kinds = policy.get("allowed_kinds") or []
    if allowed_kinds and kind not in allowed_kinds:
        raise GatewayAPIError(403, "API_KEY_KIND_FORBIDDEN", "API key is not allowed to use this kind", {"field": "kind", "value": kind, "allowed": allowed_kinds}, request_id=request_id)

    model_name = str(options.get("model_name") or "")
    allowed_models = policy.get("allowed_models") or []
    if allowed_models and model_name not in allowed_models:
        raise GatewayAPIError(403, "API_KEY_MODEL_FORBIDDEN", "API key is not allowed to use this model", {"field": "model_name", "value": model_name, "allowed": allowed_models}, request_id=request_id)

    if kind in {"image", "video"}:
        resolution = str(options.get("resolution") or "")
        allowed_resolutions = policy.get("allowed_resolutions") or []
        if allowed_resolutions and resolution not in allowed_resolutions:
            raise GatewayAPIError(403, "API_KEY_RESOLUTION_FORBIDDEN", "API key is not allowed to use this resolution", {"field": "resolution", "value": resolution, "allowed": allowed_resolutions}, request_id=request_id)

    if not policy.get("allow_uploads", True) and request_uses_uploaded_media(options):
        raise GatewayAPIError(403, "API_KEY_UPLOAD_FORBIDDEN", "API key is not allowed to use uploaded media", request_id=request_id)

    model = find_capability_model(caps.get(kind, {}).get("models") or [], model_name)
    scene = None
    if kind == "video":
        scene_id = str(options.get("scene_id") or CFG["oreate"]["default_video_scene"])
        allowed_scenes = policy.get("allowed_scenes") or []
        if allowed_scenes and scene_id not in allowed_scenes:
            raise GatewayAPIError(403, "API_KEY_SCENE_FORBIDDEN", "API key is not allowed to use this scene", {"field": "scene_id", "value": scene_id, "allowed": allowed_scenes}, request_id=request_id)
        allowed_durations = policy.get("allowed_durations") or []
        duration = int_or_default(options.get("duration"), 0)
        if allowed_durations and duration not in allowed_durations:
            raise GatewayAPIError(403, "API_KEY_DURATION_FORBIDDEN", "API key is not allowed to use this duration", {"field": "duration", "value": duration, "allowed": allowed_durations}, request_id=request_id)
        scene = find_capability_scene(caps.get("video", {}).get("scenes") or [], scene_id)

    if not policy.get("allow_experimental", False):
        if model and bool(model.get("experimental")):
            raise GatewayAPIError(403, "API_KEY_EXPERIMENTAL_FORBIDDEN", "API key is not allowed to use experimental models", {"field": "model_name", "value": model_name}, request_id=request_id)
        if scene and bool(scene.get("experimental")):
            raise GatewayAPIError(403, "API_KEY_EXPERIMENTAL_FORBIDDEN", "API key is not allowed to use experimental scenes", {"field": "scene_id", "value": scene.get("scene_id")}, request_id=request_id)


def check_rate_limit(api_key_id: int, policy: Dict[str, int], now: float, request_id: str) -> None:
    limit = policy.get("rate_limit_per_minute") or 0
    if limit <= 0:
        return
    with RATE_BUCKETS_LOCK:
        window_start = now - 60
        bucket = [t for t in RATE_BUCKETS.get(api_key_id, []) if t >= window_start]
        if len(bucket) >= limit:
            RATE_BUCKETS[api_key_id] = bucket
            raise GatewayAPIError(
                429,
                "RATE_LIMITED",
                "API key rate limit exceeded",
                {"rate_limit_per_minute": limit},
                request_id=request_id,
            )
        bucket.append(now)
        RATE_BUCKETS[api_key_id] = bucket


def day_start_timestamp(now: float) -> float:
    start = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()


def check_daily_quota(
    api_key_id: int,
    estimated_point_cost: Optional[int],
    policy: Dict[str, int],
    now: float,
    request_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    start = day_start_timestamp(now)
    owns_connection = conn is None
    connection = conn or db_conn()
    row = connection.execute(
        """
        SELECT COUNT(*) as request_count, COALESCE(SUM(estimated_point_cost), 0) as point_count
        FROM usage_log
        WHERE api_key_id=? AND created_at>=?
        """,
        (api_key_id, start),
    ).fetchone()
    if owns_connection:
        connection.close()
    daily_request_limit = policy.get("daily_request_limit") or 0
    if daily_request_limit > 0 and row["request_count"] >= daily_request_limit:
        raise GatewayAPIError(
            429,
            "DAILY_REQUEST_LIMIT_EXCEEDED",
            "API key daily request limit exceeded",
            {"daily_request_limit": daily_request_limit},
            request_id=request_id,
        )
    daily_point_limit = policy.get("daily_point_limit") or 0
    current_cost = estimated_point_cost or 0
    if daily_point_limit > 0 and row["point_count"] + current_cost > daily_point_limit:
        raise GatewayAPIError(
            429,
            "DAILY_POINT_LIMIT_EXCEEDED",
            "API key daily point limit exceeded",
            {"daily_point_limit": daily_point_limit, "estimated_point_cost": estimated_point_cost},
            request_id=request_id,
        )


def candidate_accounts_for_generation(
    kind: str,
    requested_account_id: Optional[int] = None,
    excluded_account_ids: Optional[Iterable[int]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> List[sqlite3.Row]:
    now = time.time()
    capability_clause = "a.model_info_json IS NOT NULL AND a.model_info_json != ''" if kind == "image" else "a.video_info_json IS NOT NULL AND a.video_info_json != ''"
    params: List[Any] = [now]
    account_clause = ""
    if requested_account_id:
        account_clause = "AND a.id=?"
        params.append(requested_account_id)
    excluded_ids = sorted(
        {
            int(account_id)
            for account_id in (excluded_account_ids or [])
            if account_id not in (None, "")
        }
    )
    exclusion_clause = ""
    if excluded_ids:
        exclusion_clause = f"AND a.id NOT IN ({','.join('?' for _ in excluded_ids)})"
        params.extend(excluded_ids)
    owns_connection = conn is None
    connection = conn or db_conn()
    rows = connection.execute(
        f"""
        SELECT
            a.*,
            (
                SELECT COUNT(*)
                FROM tasks AS active_tasks
                WHERE active_tasks.account_id=a.id
                  AND active_tasks.status IN ('queued', 'running', 'submitted', 'hydrating')
            ) AS active_task_count,
            (
                SELECT COALESCE(SUM(COALESCE(active_reservations.estimated_point_cost, 0)), 0)
                FROM tasks AS active_reservations
                WHERE active_reservations.account_id=a.id
                  AND active_reservations.status IN ('queued', 'running', 'submitted', 'hydrating')
            ) AS active_reserved_points
        FROM accounts AS a
        WHERE a.status IN ('verified', 'active')
          AND a.ouid IS NOT NULL AND a.ouid != ''
          AND a.ouss IS NOT NULL AND a.ouss != ''
          AND ({capability_clause})
          AND (a.cooldown_until IS NULL OR a.cooldown_until <= ?)
          {account_clause}
          {exclusion_clause}
        ORDER BY active_task_count ASC,
        CASE WHEN a.last_used_at IS NULL THEN 1 ELSE 0 END ASC,
        COALESCE(a.failure_count, 0) ASC,
        COALESCE(a.last_used_at, 0) ASC,
        a.updated_at DESC,
        a.id ASC
        """,
        tuple(params),
    ).fetchall()
    if owns_connection:
        connection.close()
    return list(rows)


def pick_account_for_generation(
    kind: str,
    requested_account_id: Optional[int] = None,
    excluded_account_ids: Optional[Iterable[int]] = None,
) -> Optional[sqlite3.Row]:
    rows = candidate_accounts_for_generation(kind, requested_account_id, excluded_account_ids)
    return rows[0] if rows else None


def select_generation_account(
    body: Any,
    request_id: str = "",
    excluded_account_ids: Optional[Iterable[int]] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Tuple[sqlite3.Row, Dict[str, Any], Dict[str, Any], Optional[int]]:
    candidates = candidate_accounts_for_generation(
        body.kind,
        getattr(body, "account_id", None),
        excluded_account_ids,
        conn,
    )
    last_validation_error: Optional[GatewayAPIError] = None
    balance_misses: List[Dict[str, Any]] = []
    eligible: List[Tuple[int, int, int, sqlite3.Row, Dict[str, Any], Dict[str, Any], Optional[int]]] = []
    for candidate_index, account in enumerate(candidates):
        caps = capabilities_from_account(account)
        try:
            options = effective_generation_options(body, caps)
            validate_generation_options(body.kind, options, caps)
        except GatewayAPIError as exc:
            last_validation_error = exc
            continue
        estimated_point_cost = estimate_point_cost(body.kind, options, caps)
        if not account_has_sufficient_balance(account, estimated_point_cost):
            balance_misses.append(
                {
                    "required_points": max(0, int_or_default(estimated_point_cost, 0)),
                    "available_points": account_spendable_points(account, estimated_point_cost),
                    "reserved_points": account_active_reserved_points(account),
                    "balance_known": account_balance_value(account) is not None,
                }
            )
            continue
        spendable = account_spendable_points(account, estimated_point_cost)
        leftover = max(0, int_or_default(spendable, 0) - max(0, int_or_default(estimated_point_cost, 0)))
        active_task_count = max(0, int_or_default(dict(account).get("active_task_count"), 0))
        eligible.append((active_task_count, leftover, candidate_index, account, caps, options, estimated_point_cost))
    if eligible:
        _, _, _, account, caps, options, estimated_point_cost = min(
            eligible,
            key=lambda item: (item[0], item[1], item[2]),
        )
        return account, caps, options, estimated_point_cost
    if last_validation_error is not None and not balance_misses and candidates:
        if request_id:
            last_validation_error.request_id = request_id
        raise last_validation_error
    if balance_misses:
        required_points = min(
            (item["required_points"] for item in balance_misses if item["required_points"] > 0),
            default=0,
        )
        known_balances = [
            max(0, int_or_default(item.get("available_points"), 0))
            for item in balance_misses
            if item.get("balance_known")
        ]
        max_available_points = max(known_balances, default=0)
        daily_gain = max(0, int_or_default(gateway_cfg().get("account_daily_point_gain"), 30))
        estimated_ready_days = None
        if required_points > max_available_points and daily_gain > 0 and known_balances:
            estimated_ready_days = min(
                math.ceil(max(0, required_points - available_points) / daily_gain)
                for available_points in known_balances
            )
        raise GatewayAPIError(
            503,
            "INSUFFICIENT_POOL_CAPACITY",
            "account pool does not have enough available points",
            {
                "required_points": required_points,
                "max_available_points": max_available_points,
                "known_balance_accounts": len(known_balances),
                "candidate_accounts": len(candidates),
                "reserved_points": sum(int_or_default(item.get("reserved_points"), 0) for item in balance_misses),
                "estimated_ready_days": estimated_ready_days,
            },
            request_id=request_id,
        )
    raise GatewayAPIError(
        503,
        "NO_ACCOUNT_AVAILABLE",
        "no verified account available with enough balance",
        request_id=request_id,
    )


def mark_account_success(account_id: int) -> None:
    now = time.time()
    conn = db_conn()
    conn.execute(
        "UPDATE accounts SET last_used_at=?, failure_count=0, cooldown_until=NULL, last_error=NULL, updated_at=? WHERE id=?",
        (now, now, account_id),
    )
    conn.commit()
    conn.close()


def upstream_error_code(error: Exception) -> str:
    upstream = getattr(error, "error", None)
    if isinstance(upstream, dict) and upstream.get("code") is not None:
        return str(upstream.get("code"))
    message = str(error)
    structured_match = re.search(
        r"""(?ix)
        (?:
            ['"]?code['"]?
            | status\s+code
            | error\s+code
        )
        \s*[:=]?\s*['"]?(\d{5,6})
        """,
        message,
    )
    if structured_match:
        return structured_match.group(1)
    standalone_match = re.search(
        r"(?<![@\w])(\d{5,6})(?![\w.])",
        message,
    )
    return standalone_match.group(1) if standalone_match else ""


def account_failure_message(error: Exception, code: str) -> str:
    upstream = getattr(error, "error", None)
    if isinstance(upstream, dict):
        message = upstream.get("message") or upstream.get("msg") or str(error)
        return f"{code}: {message}" if code else str(message)
    return str(error)


def mark_account_failure(account_id: int, error: Exception) -> None:
    now = time.time()
    code = upstream_error_code(error)
    last_error = account_failure_message(error, code)[:500]
    conn = db_conn()
    row = conn.execute("SELECT COALESCE(failure_count, 0) as failure_count FROM accounts WHERE id=?", (account_id,)).fetchone()
    if code == "110012" or code in GATEWAY_ENVIRONMENT_ERROR_CODES:
        conn.execute(
            "UPDATE accounts SET last_error=?, updated_at=? WHERE id=?",
            (last_error, now, account_id),
        )
        conn.commit()
        conn.close()
        return
    next_failure_count = (row["failure_count"] if row else 0) + 1
    if code == "200001":
        conn.execute(
            "UPDATE accounts SET status='invalid', failure_count=?, cooldown_until=NULL, last_error=?, updated_at=? WHERE id=?",
            (next_failure_count, last_error, now, account_id),
        )
        conn.commit()
        conn.close()
        return
    if code in account_failover_error_codes():
        cooldown_seconds = max(
            1,
            int_or_default(
                gateway_cfg().get("account_risk_quarantine_seconds"),
                3600,
            ),
        )
    else:
        cooldown_seconds = max(
            1,
            int_or_default(gateway_cfg().get("account_cooldown_seconds"), 300),
        ) * min(next_failure_count, 6)
    conn.execute(
        "UPDATE accounts SET failure_count=?, cooldown_until=?, last_error=?, updated_at=? WHERE id=?",
        (next_failure_count, now + cooldown_seconds, last_error, now, account_id),
    )
    conn.commit()
    conn.close()


CFG = load_config()
ADMIN_TOKENS: Dict[str, str] = {}
WS_CLIENTS: List[WebSocket] = []
RATE_BUCKETS: Dict[int, List[float]] = {}
RATE_BUCKETS_LOCK = threading.Lock()
REQUEST_ADMISSION_LOCK = threading.Lock()
TASK_WORKER_LOCK = threading.Lock()
TASK_WORKER_THREAD: Optional[threading.Thread] = None
TASK_WORKER_STOP = threading.Event()
TASK_WORKER_WAKE = threading.Event()
APPLICATION_WORKER_LOCK: Optional[SingleWorkerLock] = None
APP_LIFECYCLE_STARTED = False


def db_conn():
    busy_timeout_ms = max(0, int_or_default(database_cfg().get("busy_timeout_ms"), 5000))
    conn = sqlite3.connect(DB_PATH, timeout=max(0.001, busy_timeout_ms / 1000.0))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    return conn


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def apply_sql_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    if not MIGRATIONS_DIR.exists():
        return
    for migration_path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        version_text, separator, name = migration_path.stem.partition("_")
        if not separator or not version_text.isdigit() or not name:
            continue
        version = int(version_text)
        applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?",
            (version,),
        ).fetchone()
        if applied:
            continue
        statements = [statement.strip() for statement in migration_path.read_text(encoding="utf-8").split(";") if statement.strip()]
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                (version, name, time.time()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def init_db():
    conn = db_conn()
    journal_mode = str(database_cfg().get("journal_mode") or "WAL").strip().upper()
    if journal_mode not in {"WAL", "DELETE"}:
        journal_mode = "WAL"
    synchronous = str(database_cfg().get("synchronous") or "NORMAL").strip().upper()
    if synchronous not in {"OFF", "NORMAL", "FULL", "EXTRA"}:
        synchronous = "NORMAL"
    conn.execute(f"PRAGMA journal_mode={journal_mode}")
    conn.execute(f"PRAGMA synchronous={synchronous}")
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
            point_balance_json TEXT,
            rest_point INTEGER,
            daily_point INTEGER,
            bonus_point INTEGER,
            reserve_target_points INTEGER NOT NULL DEFAULT 0,
            balance_updated_at REAL,
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
            api_key_id INTEGER,
            account_id INTEGER,
            kind TEXT NOT NULL,
            prompt TEXT,
            model_name TEXT,
            scene_id TEXT,
            resolution TEXT,
            ratio TEXT,
            duration INTEGER,
            estimated_point_cost INTEGER,
            actual_point_cost INTEGER,
            balance_before_json TEXT,
            balance_after_json TEXT,
            balance_before_rest_point INTEGER,
            balance_before_daily_point INTEGER,
            balance_before_bonus_point INTEGER,
            balance_after_rest_point INTEGER,
            balance_after_daily_point INTEGER,
            balance_after_bonus_point INTEGER,
            request_id TEXT,
            payload_json TEXT,
            response_json TEXT,
            assets_json TEXT,
            chat_id TEXT,
            focus_id TEXT,
            status TEXT NOT NULL DEFAULT 'created',
            error_code TEXT,
            error_message TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            cancel_requested_at REAL,
            next_attempt_at REAL,
            started_at REAL,
            finished_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(api_key_id) REFERENCES api_keys(id),
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            attempt_no INTEGER NOT NULL,
            phase TEXT NOT NULL,
            account_id INTEGER,
            status TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            request_payload_json TEXT,
            stream_summary_json TEXT,
            hydration_summary_json TEXT,
            assets_json TEXT,
            started_at REAL NOT NULL,
            finished_at REAL,
            FOREIGN KEY(task_id) REFERENCES tasks(id),
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            allowed_kinds TEXT,
            allowed_models TEXT,
            allowed_scenes TEXT,
            allow_uploads INTEGER NOT NULL DEFAULT 1,
            allow_experimental INTEGER NOT NULL DEFAULT 1,
            allowed_resolutions TEXT,
            allowed_durations TEXT,
            deleted_at REAL,
            disabled_reason TEXT,
            expires_at REAL,
            rotated_from_id INTEGER,
            rotation_note TEXT,
            created_at REAL NOT NULL,
            last_used_at REAL,
            FOREIGN KEY(rotated_from_id) REFERENCES api_keys(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            contact TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_id INTEGER,
            task_id INTEGER,
            kind TEXT NOT NULL,
            account_id INTEGER,
            prompt TEXT,
            status TEXT NOT NULL,
            response_summary TEXT,
            actual_point_cost INTEGER,
            created_at REAL NOT NULL,
            FOREIGN KEY(api_key_id) REFERENCES api_keys(id),
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            username TEXT NOT NULL,
            created_at REAL NOT NULL,
            last_used_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            revoked_at REAL,
            revoked_reason TEXT,
            remote_addr TEXT,
            user_agent TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_username TEXT NOT NULL,
            action TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            status_code INTEGER,
            entity_type TEXT,
            entity_id TEXT,
            details_json TEXT,
            remote_addr TEXT,
            user_agent TEXT,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS uploaded_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            object_path TEXT NOT NULL,
            attachment_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(api_key_id, object_path),
            FOREIGN KEY(api_key_id) REFERENCES api_keys(id),
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key_id INTEGER NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            response_json TEXT NOT NULL,
            task_id INTEGER,
            created_at REAL NOT NULL,
            UNIQUE(api_key_id, idempotency_key),
            FOREIGN KEY(api_key_id) REFERENCES api_keys(id),
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registration_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL DEFAULT 'queued',
            total INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            succeeded INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            current_index INTEGER NOT NULL DEFAULT 0,
            current_step TEXT NOT NULL DEFAULT 'queued',
            current_email TEXT NOT NULL DEFAULT '',
            items_json TEXT NOT NULL DEFAULT '[]',
            events_json TEXT NOT NULL DEFAULT '[]',
            error_message TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            finished_at REAL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_registration_jobs_status ON registration_jobs(status, id)"
    )
    add_column_if_missing(conn, "registration_jobs", "events_json", "TEXT NOT NULL DEFAULT '[]'")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outlook_mailboxes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            client_id TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'available',
            last_error TEXT NOT NULL DEFAULT '',
            leased_at REAL,
            used_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_outlook_mailboxes_status ON outlook_mailboxes(status, id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pool_maintenance_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL DEFAULT 'queued',
            total_accounts INTEGER NOT NULL DEFAULT 0,
            checked_accounts INTEGER NOT NULL DEFAULT 0,
            healthy_before INTEGER NOT NULL DEFAULT 0,
            healthy_after INTEGER NOT NULL DEFAULT 0,
            risk_found INTEGER NOT NULL DEFAULT 0,
            invalid_found INTEGER NOT NULL DEFAULT 0,
            isolated_accounts INTEGER NOT NULL DEFAULT 0,
            clean_risk INTEGER NOT NULL DEFAULT 1,
            supplement INTEGER NOT NULL DEFAULT 1,
            target_healthy INTEGER NOT NULL,
            max_register INTEGER NOT NULL DEFAULT 0,
            registration_target INTEGER NOT NULL DEFAULT 0,
            registered INTEGER NOT NULL DEFAULT 0,
            registration_failed INTEGER NOT NULL DEFAULT 0,
            current_account_id INTEGER,
            current_email TEXT NOT NULL DEFAULT '',
            current_step TEXT NOT NULL DEFAULT 'queued',
            items_json TEXT NOT NULL DEFAULT '[]',
            error_message TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            finished_at REAL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pool_maintenance_jobs_status ON pool_maintenance_jobs(status, id)"
    )
    add_column_if_missing(conn, "api_keys", "rate_limit_per_minute", "INTEGER")
    add_column_if_missing(conn, "api_keys", "daily_request_limit", "INTEGER")
    add_column_if_missing(conn, "api_keys", "daily_point_limit", "INTEGER")
    add_column_if_missing(conn, "api_keys", "client_id", "INTEGER")
    add_column_if_missing(conn, "api_keys", "allowed_kinds", "TEXT")
    add_column_if_missing(conn, "api_keys", "allowed_models", "TEXT")
    add_column_if_missing(conn, "api_keys", "allowed_scenes", "TEXT")
    add_column_if_missing(conn, "api_keys", "allow_uploads", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_missing(conn, "api_keys", "allow_experimental", "INTEGER NOT NULL DEFAULT 1")
    add_column_if_missing(conn, "api_keys", "allowed_resolutions", "TEXT")
    add_column_if_missing(conn, "api_keys", "allowed_durations", "TEXT")
    add_column_if_missing(conn, "api_keys", "deleted_at", "REAL")
    add_column_if_missing(conn, "api_keys", "disabled_reason", "TEXT")
    add_column_if_missing(conn, "api_keys", "expires_at", "REAL")
    add_column_if_missing(conn, "api_keys", "rotated_from_id", "INTEGER")
    add_column_if_missing(conn, "api_keys", "rotation_note", "TEXT")
    add_column_if_missing(conn, "clients", "contact", "TEXT NOT NULL DEFAULT ''")
    add_column_if_missing(conn, "clients", "status", "TEXT NOT NULL DEFAULT 'active'")
    add_column_if_missing(conn, "tasks", "api_key_id", "INTEGER")
    add_column_if_missing(conn, "accounts", "last_used_at", "REAL")
    add_column_if_missing(conn, "accounts", "failure_count", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "accounts", "cooldown_until", "REAL")
    add_column_if_missing(conn, "accounts", "point_balance_json", "TEXT")
    add_column_if_missing(conn, "accounts", "rest_point", "INTEGER")
    add_column_if_missing(conn, "accounts", "daily_point", "INTEGER")
    add_column_if_missing(conn, "accounts", "bonus_point", "INTEGER")
    add_column_if_missing(conn, "accounts", "reserve_target_points", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "accounts", "balance_updated_at", "REAL")
    add_column_if_missing(conn, "accounts", "last_checkin_at", "REAL")
    add_column_if_missing(conn, "pool_maintenance_jobs", "checked_in", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "tasks", "model_name", "TEXT")
    add_column_if_missing(conn, "tasks", "scene_id", "TEXT")
    add_column_if_missing(conn, "tasks", "resolution", "TEXT")
    add_column_if_missing(conn, "tasks", "ratio", "TEXT")
    add_column_if_missing(conn, "tasks", "duration", "INTEGER")
    add_column_if_missing(conn, "tasks", "estimated_point_cost", "INTEGER")
    add_column_if_missing(conn, "tasks", "actual_point_cost", "INTEGER")
    add_column_if_missing(conn, "tasks", "balance_before_json", "TEXT")
    add_column_if_missing(conn, "tasks", "balance_after_json", "TEXT")
    add_column_if_missing(conn, "tasks", "balance_before_rest_point", "INTEGER")
    add_column_if_missing(conn, "tasks", "balance_before_daily_point", "INTEGER")
    add_column_if_missing(conn, "tasks", "balance_before_bonus_point", "INTEGER")
    add_column_if_missing(conn, "tasks", "balance_after_rest_point", "INTEGER")
    add_column_if_missing(conn, "tasks", "balance_after_daily_point", "INTEGER")
    add_column_if_missing(conn, "tasks", "balance_after_bonus_point", "INTEGER")
    add_column_if_missing(conn, "tasks", "request_id", "TEXT")
    add_column_if_missing(conn, "tasks", "response_json", "TEXT")
    add_column_if_missing(conn, "tasks", "assets_json", "TEXT")
    add_column_if_missing(conn, "tasks", "focus_id", "TEXT")
    add_column_if_missing(conn, "tasks", "error_code", "TEXT")
    add_column_if_missing(conn, "tasks", "error_message", "TEXT")
    add_column_if_missing(conn, "tasks", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "tasks", "cancel_requested_at", "REAL")
    add_column_if_missing(conn, "tasks", "next_attempt_at", "REAL")
    add_column_if_missing(conn, "tasks", "started_at", "REAL")
    add_column_if_missing(conn, "tasks", "finished_at", "REAL")
    add_column_if_missing(conn, "task_attempts", "phase", "TEXT NOT NULL DEFAULT 'generation'")
    add_column_if_missing(conn, "task_attempts", "account_id", "INTEGER")
    add_column_if_missing(conn, "task_attempts", "status", "TEXT NOT NULL DEFAULT 'running'")
    add_column_if_missing(conn, "task_attempts", "error_code", "TEXT")
    add_column_if_missing(conn, "task_attempts", "error_message", "TEXT")
    add_column_if_missing(conn, "task_attempts", "request_payload_json", "TEXT")
    add_column_if_missing(conn, "task_attempts", "stream_summary_json", "TEXT")
    add_column_if_missing(conn, "task_attempts", "hydration_summary_json", "TEXT")
    add_column_if_missing(conn, "task_attempts", "assets_json", "TEXT")
    add_column_if_missing(conn, "task_attempts", "started_at", "REAL")
    add_column_if_missing(conn, "task_attempts", "finished_at", "REAL")
    add_column_if_missing(conn, "usage_log", "task_id", "INTEGER")
    add_column_if_missing(conn, "usage_log", "request_id", "TEXT")
    add_column_if_missing(conn, "usage_log", "idempotency_key", "TEXT")
    add_column_if_missing(conn, "usage_log", "model_name", "TEXT")
    add_column_if_missing(conn, "usage_log", "resolution", "TEXT")
    add_column_if_missing(conn, "usage_log", "ratio", "TEXT")
    add_column_if_missing(conn, "usage_log", "duration", "INTEGER")
    add_column_if_missing(conn, "usage_log", "scene_id", "TEXT")
    add_column_if_missing(conn, "usage_log", "estimated_point_cost", "INTEGER")
    add_column_if_missing(conn, "usage_log", "actual_point_cost", "INTEGER")
    add_column_if_missing(conn, "usage_log", "error_code", "TEXT")
    add_column_if_missing(conn, "usage_log", "status_code", "INTEGER")
    conn.execute(
        """
        UPDATE tasks
        SET next_attempt_at=COALESCE(next_attempt_at, updated_at, created_at)
        WHERE status IN ('submitted', 'hydrating')
          AND next_attempt_at IS NULL
        """
    )
    migrate_plaintext_account_secrets(conn)
    conn.commit()
    apply_sql_migrations(conn)
    conn.close()


def restore_gateway_risk_misclassified_accounts() -> int:
    now = time.time()
    conn = db_conn()
    try:
        table_exists = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table' AND name='accounts'
            """
        ).fetchone()
        if not table_exists:
            return 0
        cursor = conn.execute(
            """
            UPDATE accounts
            SET status='verified',
                failure_count=0,
                cooldown_until=NULL,
                last_error=NULL,
                updated_at=?
            WHERE status IN ('disabled', 'invalid')
              AND instr(COALESCE(last_error, ''), '212361') > 0
              AND COALESCE(ouid, '') <> ''
              AND COALESCE(ouss, '') <> ''
              AND COALESCE(model_info_json, '') NOT IN ('', '{}', 'null')
            """,
            (now,),
        )
        conn.commit()
        return max(0, int(cursor.rowcount or 0))
    finally:
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


class ServerSettingsIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    port: Optional[Annotated[int, Field(strict=True, ge=1, le=65535)]] = None

    @field_validator("port", mode="before")
    @classmethod
    def reject_null_port(cls, value: Any) -> Any:
        if value is None:
            raise PydanticCustomError("int_type", "Input should be a valid integer")
        return value


class PoolSettingsIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    min_accounts: Optional[Annotated[int, Field(strict=True, ge=0)]] = None
    maintain_target: Optional[Annotated[int, Field(strict=True, ge=0)]] = None
    maintain_check_interval: Optional[Annotated[int, Field(strict=True, ge=0)]] = None
    registration_concurrency: Optional[Annotated[int, Field(strict=True, ge=1, le=8)]] = None
    auto_maintain_max_register: Optional[Annotated[int, Field(strict=True, ge=0, le=50)]] = None
    auto_checkin_enabled: Optional[bool] = None
    checkin_timezone: Optional[str] = None

    @field_validator(
        "min_accounts",
        "maintain_target",
        "maintain_check_interval",
        "registration_concurrency",
        "auto_maintain_max_register",
        mode="before",
    )
    @classmethod
    def reject_null_pool_counts(cls, value: Any) -> Any:
        if value is None:
            raise PydanticCustomError("int_type", "Input should be a valid integer")
        return value

    @field_validator("auto_checkin_enabled", mode="before")
    @classmethod
    def coerce_auto_checkin_enabled(cls, value: Any) -> Any:
        if value is None:
            raise PydanticCustomError("bool_type", "Input should be a valid boolean")
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        raise PydanticCustomError("bool_type", "Input should be a valid boolean")

    @field_validator("checkin_timezone", mode="before")
    @classmethod
    def validate_checkin_timezone(cls, value: Any) -> Any:
        if value is None:
            raise PydanticCustomError("string_type", "Input should be a valid string")
        text = str(value).strip()
        if not text:
            raise PydanticCustomError("string_type", "Input should be a valid string")
        try:
            ZoneInfo(text)
        except Exception as exc:
            raise PydanticCustomError("timezone_type", "Input should be a valid IANA timezone") from exc
        return text


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    server: Optional[ServerSettingsIn] = None
    oreate: Optional[Dict[str, Any]] = None
    mail: Optional[Dict[str, Any]] = None
    pool: Optional[PoolSettingsIn] = None

    @field_validator("server", "oreate", "mail", "pool", mode="before")
    @classmethod
    def reject_null_sections(cls, value: Any) -> Any:
        if value is None:
            raise PydanticCustomError("dict_type", "Input should be a valid object")
        return value


class AdminCredentialsIn(BaseModel):
    current_password: str
    new_username: str
    new_password: str
    confirm_password: str


class LoginIn(BaseModel):
    username: str
    password: str


class MediaTaskIn(BaseModel):
    account_id: Optional[int] = None
    prompt: str
    kind: str
    model_name: Optional[str] = None
    ratio: Optional[str] = None
    resolution: Optional[str] = None
    duration: Optional[int] = None
    scene_id: Optional[str] = None
    image: Optional[Dict[str, Any]] = None
    first_frame: Optional[Dict[str, Any]] = None
    last_frame: Optional[Dict[str, Any]] = None
    reference_images: Optional[List[Dict[str, Any]]] = None
    reference_videos: Optional[List[Dict[str, Any]]] = None
    motion_video: Optional[Dict[str, Any]] = None
    character_image: Optional[Dict[str, Any]] = None
    ref_duration: Optional[Any] = None
    ref_total_duration: Optional[Any] = None
    motion_duration: Optional[Any] = None
    keep_original_sound: Optional[bool] = None
    is_audio: Optional[bool] = None
    ai_type: Optional[Any] = None
    jt: str = ""


class AutoRegisterIn(BaseModel):
    count: int = Field(default=1, ge=1, le=50)


class OutlookImportIn(BaseModel):
    text: str = Field(min_length=1)
    apply_detected_endpoint: bool = True


class OutlookPurgeIn(BaseModel):
    statuses: List[str] = Field(default_factory=lambda: ["used", "error", "disabled"])
    include_registered: bool = True


class MaintainIn(BaseModel):
    force_register: bool = False
    max_register: int = Field(default=3, ge=0, le=50)


class PoolMaintenanceIn(BaseModel):
    clean_risk: bool = True
    supplement: bool = True
    target_healthy: Optional[int] = Field(default=None, ge=1, le=500)
    max_register: int = Field(default=10, ge=0, le=50)


class AccountReserveTargetIn(BaseModel):
    reserve_target_points: Annotated[int, Field(strict=True, ge=0, le=1000000)]


class AccountZombiePurgeIn(BaseModel):
    confirm: bool = False


configure_oreate_client_defaults(
    lambda: CFG["oreate"],
    decrypt_secret=decrypt_secret_value,
    tls_verify=tls_verify_enabled,
)
CLIENT = OreateClient()


def quarantine_burned_outlook_mailboxes() -> int:
    """Mark Outlook pool rows that are already burned for Oreate as permanently disabled."""
    now = time.time()
    conn = db_conn()
    try:
        # Already present in accounts table => cannot re-register.
        cursor = conn.execute(
            """
            UPDATE outlook_mailboxes
            SET status='disabled',
                last_error=CASE
                    WHEN COALESCE(last_error,'')='' THEN 'burned: email already registered in accounts'
                    ELSE last_error
                END,
                updated_at=?
            WHERE status IN ('available', 'error', 'leased')
              AND EXISTS (
                  SELECT 1 FROM accounts a
                  WHERE lower(a.email)=lower(outlook_mailboxes.email)
              )
            """,
            (now,),
        )
        changed = int(cursor.rowcount or 0)
        # Prior failed registration markers => do not reclaim.
        cursor = conn.execute(
            """
            UPDATE outlook_mailboxes
            SET status='disabled', updated_at=?
            WHERE status IN ('available', 'error')
              AND (
                instr(lower(COALESCE(last_error,'')), 'signup_failed') > 0
                OR instr(lower(COALESCE(last_error,'')), 'confirm_failed') > 0
                OR instr(lower(COALESCE(last_error,'')), 'incorrect password') > 0
                OR instr(lower(COALESCE(last_error,'')), '600005') > 0
                OR instr(lower(COALESCE(last_error,'')), '600002') > 0
                OR instr(lower(COALESCE(last_error,'')), 'without ouss') > 0
                OR instr(lower(COALESCE(last_error,'')), 'link has expired') > 0
                OR instr(lower(COALESCE(last_error,'')), 'verify_error') > 0
                OR instr(lower(COALESCE(last_error,'')), 'verify_timeout') > 0
                OR instr(lower(COALESCE(last_error,'')), 'manual_release') > 0
              )
            """,
            (now,),
        )
        changed += int(cursor.rowcount or 0)
        conn.commit()
        return changed
    finally:
        conn.close()


def claim_outlook_mailbox() -> Dict[str, Any]:
    quarantine_burned_outlook_mailboxes()
    conn = db_conn()
    now = time.time()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, email, password, client_id, refresh_token
            FROM outlook_mailboxes
            WHERE status='available'
              AND NOT EXISTS (
                  SELECT 1 FROM accounts a
                  WHERE lower(a.email)=lower(outlook_mailboxes.email)
              )
              AND COALESCE(last_error,'') NOT LIKE '%signup_failed%'
              AND COALESCE(last_error,'') NOT LIKE '%confirm_failed%'
              AND COALESCE(last_error,'') NOT LIKE '%Incorrect password%'
              AND COALESCE(last_error,'') NOT LIKE '%600005%'
              AND COALESCE(last_error,'') NOT LIKE '%600002%'
              AND COALESCE(last_error,'') NOT LIKE '%without ouss%'
              AND COALESCE(last_error,'') NOT LIKE '%manual_release%'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            conn.rollback()
            raise RuntimeError("Outlook 邮箱池没有可用的未烧号邮箱，请导入新卡密")
        updated = conn.execute(
            """
            UPDATE outlook_mailboxes
            SET status='leased', leased_at=?, updated_at=?, last_error=''
            WHERE id=? AND status='available'
            """,
            (now, now, row["id"]),
        )
        if updated.rowcount != 1:
            conn.rollback()
            raise RuntimeError("领取 Outlook 邮箱失败，请重试")
        conn.commit()
        return {
            "id": int(row["id"]),
            "email": str(row["email"]),
            "password": decrypt_secret_value(row["password"], required=True),
            "client_id": str(row["client_id"]),
            "refresh_token": decrypt_secret_value(row["refresh_token"], required=True),
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def resolve_outlook_mailbox(token: str) -> Dict[str, Any]:
    mailbox_id = int(str(token or "").strip())
    conn = db_conn()
    try:
        row = conn.execute(
            """
            SELECT id, email, password, client_id, refresh_token, status
            FROM outlook_mailboxes
            WHERE id=?
            """,
            (mailbox_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"outlook mailbox not found: {mailbox_id}")
        return {
            "id": int(row["id"]),
            "email": str(row["email"]),
            "password": decrypt_secret_value(row["password"], required=True),
            "client_id": str(row["client_id"]),
            "refresh_token": decrypt_secret_value(row["refresh_token"], required=True),
            "status": str(row["status"] or ""),
        }
    finally:
        conn.close()


def finish_outlook_mailbox(token: str, status: str, error: str = "") -> None:
    mailbox_id = int(str(token or "").strip())
    normalized = str(status or "").strip().lower() or "error"
    if normalized not in {"available", "used", "error", "disabled", "leased"}:
        normalized = "error"
    now = time.time()
    used_at = now if normalized == "used" else None
    conn = db_conn()
    try:
        conn.execute(
            """
            UPDATE outlook_mailboxes
            SET status=?, last_error=?, used_at=COALESCE(?, used_at), updated_at=?
            WHERE id=?
            """,
            (normalized, str(error or "")[:1000], used_at, now, mailbox_id),
        )
        conn.commit()
    finally:
        conn.close()


def outlook_mailbox_stats() -> Dict[str, int]:
    conn = db_conn()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM outlook_mailboxes GROUP BY status"
        ).fetchall()
        stats = {"available": 0, "leased": 0, "used": 0, "error": 0, "disabled": 0, "total": 0}
        for row in rows:
            key = str(row["status"] or "")
            count = int(row["count"] or 0)
            stats["total"] += count
            if key in stats:
                stats[key] = count
        return stats
    finally:
        conn.close()


def import_outlook_mailboxes(
    text: str,
    *,
    apply_detected_endpoint: bool = True,
) -> Dict[str, Any]:
    parsed = parse_outlook_import_text(text)
    accounts = parsed.get("accounts") or []
    now = time.time()
    inserted = 0
    updated = 0
    skipped = 0
    conn = db_conn()
    try:
        for account in accounts:
            email = str(account.get("email") or "").strip().lower()
            if not email:
                skipped += 1
                continue
            password = encrypt_secret_value(account.get("password") or "")
            refresh_token = encrypt_secret_value(account.get("refresh_token") or "")
            client_id = str(account.get("client_id") or "").strip()
            existing = conn.execute(
                "SELECT id, status FROM outlook_mailboxes WHERE email=?",
                (email,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO outlook_mailboxes(
                        email, password, client_id, refresh_token, status,
                        last_error, leased_at, used_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'available', '', NULL, NULL, ?, ?)
                    """,
                    (email, password, client_id, refresh_token, now, now),
                )
                inserted += 1
                continue
            next_status = str(existing["status"] or "available")
            # Never revive permanently burned/disabled mailboxes on re-import.
            if next_status == "error":
                next_status = "available"
            conn.execute(
                """
                UPDATE outlook_mailboxes
                SET password=?, client_id=?, refresh_token=?, status=?,
                    last_error=CASE WHEN ?='disabled' THEN last_error ELSE '' END,
                    updated_at=?
                WHERE id=?
                """,
                (password, client_id, refresh_token, next_status, next_status, now, existing["id"]),
            )
            updated += 1
        conn.commit()
    finally:
        conn.close()

    config_updates: Dict[str, Any] = {}
    if apply_detected_endpoint and accounts:
        updates: Dict[str, Any] = {"provider": "outlook"}
        detected_base = str(parsed.get("detected_base_url") or "").strip()
        detected_key = str(parsed.get("detected_api_key") or "").strip()
        if detected_base:
            updates["base_url"] = detected_base
        if detected_key:
            updates["api_key"] = detected_key
        if not str((CFG.get("mail") or {}).get("api_mode") or "").strip():
            updates["api_mode"] = "auto"
        CFG["mail"] = deep_merge(CFG.get("mail") or {}, updates)
        save_config(CFG)
        config_updates = {
            "provider": "outlook",
            "base_url": updates.get("base_url") or str((CFG.get("mail") or {}).get("base_url") or ""),
        }
        if detected_key:
            config_updates["api_key"] = SECRET_PLACEHOLDER

    return {
        "ok": True,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "parse_errors": parsed.get("errors") or [],
        "detected_base_url": parsed.get("detected_base_url") or "",
        "config_updates": config_updates,
        "stats": outlook_mailbox_stats(),
    }


def public_outlook_mailbox(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "client_id": row["client_id"],
        "status": row["status"],
        "last_error": row["last_error"],
        "leased_at": row["leased_at"],
        "used_at": row["used_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "has_password": bool(row["password"]),
        "has_refresh_token": bool(row["refresh_token"]),
    }


YYDS_MAIL = YydsClient(lambda: CFG["mail"])
OUTLOOK_MAIL = OutlookMailClient(
    lambda: CFG["mail"],
    claim_mailbox=claim_outlook_mailbox,
    resolve_mailbox=resolve_outlook_mailbox,
    finish_mailbox=finish_outlook_mailbox,
)
MAIL = MailRouter(lambda: CFG["mail"], YYDS_MAIL, OUTLOOK_MAIL)


def save_account(email: str, password: str, session: OreateSession, model_info=None, video_info=None, status="verified", source="auto") -> int:
    now = time.time()
    encrypted_password = encrypt_secret_value(password)
    encrypted_ouid = encrypt_secret_value(session.cookies.get("OUID", ""))
    encrypted_ouss = encrypt_secret_value(session.cookies.get("ouss", ""))
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
            encrypted_password,
            status,
            source,
            encrypted_ouid,
            encrypted_ouss,
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
    rows = conn.execute(
        """
        SELECT
            a.*,
            (
                SELECT COUNT(*)
                FROM tasks AS active_tasks
                WHERE active_tasks.account_id=a.id
                  AND active_tasks.status IN ('queued', 'running', 'submitted', 'hydrating')
            ) AS active_task_count,
            (
                SELECT COALESCE(SUM(COALESCE(active_reservations.estimated_point_cost, 0)), 0)
                FROM tasks AS active_reservations
                WHERE active_reservations.account_id=a.id
                  AND active_reservations.status IN ('queued', 'running', 'submitted', 'hydrating')
            ) AS active_reserved_points
        FROM accounts AS a
        ORDER BY a.id DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def pool_capacity_rows() -> List[sqlite3.Row]:
    now = time.time()
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT
            a.*,
            (
                SELECT COUNT(*)
                FROM tasks AS active_tasks
                WHERE active_tasks.account_id=a.id
                  AND active_tasks.status IN ('queued', 'running', 'submitted', 'hydrating')
            ) AS active_task_count,
            (
                SELECT COALESCE(SUM(COALESCE(active_reservations.estimated_point_cost, 0)), 0)
                FROM tasks AS active_reservations
                WHERE active_reservations.account_id=a.id
                  AND active_reservations.status IN ('queued', 'running', 'submitted', 'hydrating')
            ) AS active_reserved_points
        FROM accounts AS a
        WHERE a.status IN ('verified', 'active')
          AND a.ouid IS NOT NULL AND a.ouid != ''
          AND a.ouss IS NOT NULL AND a.ouss != ''
          AND (a.cooldown_until IS NULL OR a.cooldown_until <= ?)
        ORDER BY a.id ASC
        """,
        (now,),
    ).fetchall()
    conn.close()
    return list(rows)


def capacity_point_tiers() -> List[int]:
    configured = gateway_cfg().get("capacity_point_tiers")
    values = configured if isinstance(configured, list) else []
    tiers = {
        int_or_default(value, 0)
        for value in values
        if int_or_default(value, 0) > 0
    }
    return sorted(tiers or {30, 50, 100, 150, 300, 455, 600, 1000})


def build_pool_capacity_snapshot() -> Dict[str, Any]:
    rows = pool_capacity_rows()
    known_rows = [row for row in rows if account_balance_value(row) is not None]
    daily_gain = max(0, int_or_default(gateway_cfg().get("account_daily_point_gain"), 30))
    total_points = sum(max(0, int_or_default(account_balance_value(row), 0)) for row in known_rows)
    reserved_points = sum(account_active_reserved_points(row) for row in known_rows)
    available_values = [max(0, int_or_default(account_available_points(row), 0)) for row in known_rows]
    tiers = []
    for point_cost in capacity_point_tiers():
        spendable_values = [
            max(0, int_or_default(account_spendable_points(row, point_cost), 0))
            for row in known_rows
        ]
        task_capacity = sum(value // point_cost for value in spendable_values)
        next_day_task_capacity = sum(
            (value + daily_gain) // point_cost
            for value in spendable_values
        )
        estimated_ready_days = None
        if spendable_values:
            estimated_ready_days = 0
            if not any(value >= point_cost for value in spendable_values):
                if daily_gain > 0:
                    estimated_ready_days = min(
                        math.ceil(max(0, point_cost - value) / daily_gain)
                        for value in spendable_values
                    )
                else:
                    estimated_ready_days = None
        tiers.append(
            {
                "point_cost": point_cost,
                "ready_accounts": sum(1 for value in spendable_values if value >= point_cost),
                "task_capacity": task_capacity,
                "daily_task_capacity": max(0, next_day_task_capacity - task_capacity),
                "estimated_ready_days": estimated_ready_days,
            }
        )
    return {
        "account_count": len(rows),
        "known_balance_accounts": len(known_rows),
        "total_points": total_points,
        "reserved_points": reserved_points,
        "available_points": sum(available_values),
        "max_available_points": max(available_values, default=0),
        "daily_point_gain_per_account": daily_gain,
        "daily_point_gain_total": len(rows) * daily_gain,
        "tiers": tiers,
    }


TASK_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "expired"}
TASK_CANCELLABLE_STATUSES = ("queued", "running", "submitted", "hydrating")


def encode_json_value(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def task_response_assets(response: Any) -> List[Any]:
    if not isinstance(response, dict):
        return []
    assets = response.get("assets")
    if isinstance(assets, list):
        return assets
    hydration = response.get("hydration")
    if isinstance(hydration, dict):
        nested_assets = hydration.get("assets")
        if isinstance(nested_assets, list):
            return nested_assets
    return []


def task_response_chat(response: Any) -> Dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    chat = response.get("chat")
    return chat if isinstance(chat, dict) else {}


def task_row_to_public(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    payload = json_value_from_db(item.get("payload_json")) or {}
    response = json_value_from_db(item.get("response_json")) or {}
    assets = json_value_from_db(item.get("assets_json"))
    if not isinstance(assets, list):
        assets = task_response_assets(response)
    item["payload"] = payload
    item["response"] = response
    item["assets"] = assets
    chat = task_response_chat(response)
    if not item.get("chat_id"):
        item["chat_id"] = chat.get("chatId") or ""
    if not item.get("focus_id"):
        item["focus_id"] = chat.get("focusId") or item.get("chat_id") or ""
    return item


def task_attempts_for_task(task_id: int) -> List[Dict[str, Any]]:
    conn = db_conn()
    rows = conn.execute("SELECT * FROM task_attempts WHERE task_id=? ORDER BY attempt_no ASC, id ASC", (task_id,)).fetchall()
    conn.close()
    attempts = []
    for row in rows:
        item = dict(row)
        item["request_payload"] = json_value_from_db(item.pop("request_payload_json", None))
        item["stream_summary"] = json_value_from_db(item.pop("stream_summary_json", None))
        item["hydration_summary"] = json_value_from_db(item.pop("hydration_summary_json", None))
        assets = json_value_from_db(item.pop("assets_json", None))
        item["assets"] = assets if isinstance(assets, list) else []
        attempts.append(item)
    return attempts


def task_detail_for_row(row: sqlite3.Row) -> Dict[str, Any]:
    task = task_row_to_public(row)
    task["attempts"] = task_attempts_for_task(task["id"])
    return task


def clean_asset_host_allowlist() -> set[str]:
    return {
        str(host).strip().lower()
        for host in openai_compat_cfg().get("asset_host_allowlist", ["cdn.oreateai.com"])
        if str(host).strip()
    }


def clean_asset_insecure_tls_fallback_hosts() -> set[str]:
    configured = openai_compat_cfg().get(
        "asset_insecure_tls_fallback_hosts",
        ["cdn.oreateai.com"],
    )
    allowed = clean_asset_host_allowlist()
    return {
        str(host).strip().lower()
        for host in configured
        if str(host).strip().lower() in allowed
    }


def validate_clean_asset_url(asset_url: str) -> str:
    url = str(asset_url or "").strip()
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.hostname.lower() not in clean_asset_host_allowlist()
    ):
        raise HTTPException(502, "image asset is unavailable")
    return url


def clean_asset_signature(task_id: int, asset_index: int, asset_url: str) -> str:
    signing_secret = active_encryption_key()
    if not signing_secret:
        raise RuntimeError("server encryption key is not configured")
    message = f"clean-asset:v1:{int(task_id)}:{int(asset_index)}:{asset_url}".encode("utf-8")
    key = hashlib.sha256(signing_secret.encode("utf-8")).digest()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def clean_asset_public_base_url(request: Request) -> str:
    configured = str(openai_compat_cfg().get("public_base_url") or "").strip()
    if configured:
        parsed = urlparse(configured)
        try:
            parsed_port = parsed.port
        except ValueError as exc:
            raise RuntimeError("openai_compat.public_base_url is invalid") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise RuntimeError("openai_compat.public_base_url is invalid")
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed_port}" if parsed_port is not None else ""
        return f"{parsed.scheme}://{host}{port}"
    return str(request.base_url).rstrip("/")


def public_clean_asset_url(
    request: Request,
    task_id: int,
    asset_index: int,
    asset_url: str,
) -> str:
    validated_url = validate_clean_asset_url(asset_url)
    signature = clean_asset_signature(task_id, asset_index, validated_url)
    return (
        f"{clean_asset_public_base_url(request)}/v1/tasks/{int(task_id)}"
        f"/assets/{int(asset_index)}/clean?signature={signature}"
    )


def public_task_for_request(task: Dict[str, Any], request: Request) -> Dict[str, Any]:
    public_task = copy.deepcopy(task)
    if (
        str(public_task.get("kind") or "").lower() != "image"
        or str(public_task.get("status") or "").lower() != "completed"
    ):
        return public_task
    source_assets = public_task.get("assets")
    if not isinstance(source_assets, list):
        return public_task

    task_id = int_or_default(public_task.get("id"), 0)
    if task_id <= 0:
        return public_task
    clean_assets = [
        public_clean_asset_url(request, task_id, index, str(asset_url or ""))
        for index, asset_url in enumerate(source_assets)
    ]
    clean_asset_by_source = {
        str(asset_url or ""): clean_assets[index]
        for index, asset_url in enumerate(source_assets)
    }
    public_task["assets"] = clean_assets

    response = public_task.get("response")
    if isinstance(response, dict):
        if isinstance(response.get("assets"), list):
            response["assets"] = list(clean_assets)
        hydration = response.get("hydration")
        if isinstance(hydration, dict) and isinstance(hydration.get("assets"), list):
            hydration["assets"] = list(clean_assets)

    attempts = public_task.get("attempts")
    if isinstance(attempts, list):
        for attempt in attempts:
            if isinstance(attempt, dict) and isinstance(attempt.get("assets"), list):
                attempt["assets"] = [
                    clean_asset_by_source[str(asset_url or "")]
                    for asset_url in attempt["assets"]
                    if str(asset_url or "") in clean_asset_by_source
                ]
    return public_task


def public_task_payload_for_request(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    result = copy.deepcopy(payload)
    task = result.get("task")
    if isinstance(task, dict):
        result["task"] = public_task_for_request(task, request)
    return result


def public_gateway_result_for_request(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    result = public_task_payload_for_request(payload, request)
    task = result.get("task")
    if isinstance(task, dict):
        result["assets"] = list(task.get("assets") or [])
        response = task.get("response")
        result["response"] = copy.deepcopy(response) if isinstance(response, dict) else {}
    return result


def fetch_remote_image_asset_with_requests(asset_url: str, *, verify_tls: bool) -> bytes:
    current_url = validate_clean_asset_url(asset_url)
    response = None
    try:
        for redirect_count in range(4):
            response = requests.get(
                current_url,
                stream=True,
                timeout=(5, 30),
                verify=verify_tls,
                allow_redirects=False,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = str(response.headers.get("location") or "").strip()
            response.close()
            response = None
            if not location or redirect_count == 3:
                raise HTTPException(502, "image asset redirect is invalid")
            current_url = validate_clean_asset_url(urljoin(current_url, location))
        if response is None:
            raise HTTPException(502, "image asset download failed")
        response.raise_for_status()
        validate_clean_asset_url(str(response.url or current_url))
        try:
            declared_length = int(response.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            declared_length = 0
        if declared_length > MAX_CLEAN_ASSET_BYTES:
            raise HTTPException(413, "image asset is too large")
        payload = bytearray()
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            payload.extend(chunk)
            if len(payload) > MAX_CLEAN_ASSET_BYTES:
                raise HTTPException(413, "image asset is too large")
        if not payload:
            raise HTTPException(502, "image asset is empty")
        return bytes(payload)
    except HTTPException:
        raise
    except requests.exceptions.SSLError:
        raise
    except requests.RequestException:
        raise HTTPException(502, "image asset download failed")
    finally:
        if response is not None:
            response.close()


def fetch_remote_image_asset(asset_url: str) -> bytes:
    validated_url = validate_clean_asset_url(asset_url)
    verify_tls = tls_verify_enabled()
    try:
        return fetch_remote_image_asset_with_requests(
            validated_url,
            verify_tls=verify_tls,
        )
    except requests.exceptions.SSLError:
        hostname = str(urlparse(validated_url).hostname or "").lower()
        if not verify_tls or hostname not in clean_asset_insecure_tls_fallback_hosts():
            raise HTTPException(502, "image asset download failed")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            try:
                return fetch_remote_image_asset_with_requests(
                    validated_url,
                    verify_tls=False,
                )
            except requests.exceptions.SSLError:
                raise HTTPException(502, "image asset download failed")


def cleaned_image_asset(asset_url: str) -> Tuple[bytes, str, bool]:
    source = fetch_remote_image_asset(validate_clean_asset_url(asset_url))
    try:
        return watermark_free_image_bytes(source, force_bottom_strip=True)
    except WatermarkImageError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


def completed_image_task_asset(task_id: int, asset_index: int) -> Tuple[Dict[str, Any], str]:
    row = fetch_task_row(task_id)
    if not row:
        raise HTTPException(404, "image asset not found")
    task = task_detail_for_row(row)
    assets = task.get("assets") if isinstance(task.get("assets"), list) else []
    if asset_index < 0 or asset_index >= len(assets):
        raise HTTPException(404, "image asset not found")
    if str(task.get("kind") or "").lower() != "image":
        raise HTTPException(409, "watermark removal only supports image tasks")
    if str(task.get("status") or "").lower() != "completed":
        raise HTTPException(409, "image task is not completed")
    return task, validate_clean_asset_url(str(assets[asset_index] or ""))


def clean_image_asset_response(
    task_id: int,
    asset_index: int,
    *,
    cache_control: str,
    asset_url: Optional[str] = None,
) -> Response:
    if asset_url is None:
        _task, asset_url = completed_image_task_asset(task_id, asset_index)
    cleaned, media_type, removed = cleaned_image_asset(asset_url)
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[media_type]
    return Response(
        content=cleaned,
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="task-{task_id}-clean-{asset_index + 1}.{extension}"',
            "Cache-Control": cache_control,
            "X-Watermark-Removed": "true" if removed else "false",
            "X-Content-Type-Options": "nosniff",
        },
    )


def update_task_record(task_id: int, **fields: Any) -> None:
    if not fields:
        return
    conn = db_conn()
    now = fields.pop("updated_at", time.time())
    payload = dict(fields)
    payload["updated_at"] = now
    for key in ("payload_json", "response_json", "assets_json", "balance_before_json", "balance_after_json"):
        if key in payload:
            payload[key] = encode_json_value(payload[key])
    assignments = ", ".join(f"{key}=?" for key in payload)
    values = list(payload.values()) + [task_id]
    conn.execute(f"UPDATE tasks SET {assignments} WHERE id=?", values)
    conn.commit()
    conn.close()


def update_task_status(task_id: int, status: str, response: Optional[Dict[str, Any]] = None) -> None:
    now = time.time()
    fields: Dict[str, Any] = {"status": status, "updated_at": now}
    if response is not None:
        fields["response_json"] = response
        fields["assets_json"] = task_response_assets(response)
        chat = task_response_chat(response)
        if chat:
            fields["chat_id"] = chat.get("chatId") or ""
            fields["focus_id"] = chat.get("focusId") or fields["chat_id"]
    if status in TASK_TERMINAL_STATUSES:
        fields["finished_at"] = now
        fields["next_attempt_at"] = None
    elif status in {"submitted", "hydrating"}:
        fields["next_attempt_at"] = now
    update_task_record(task_id, **fields)


def save_task(
    account_id: int,
    kind: str,
    prompt: str,
    payload: Dict[str, Any],
    response: Dict[str, Any],
    status: str = "created",
    *,
    api_key_id: Optional[int] = None,
    request_id: str = "",
    model_name: str = "",
    scene_id: str = "",
    resolution: str = "",
    ratio: str = "",
    duration: Optional[int] = None,
    estimated_point_cost: Optional[int] = None,
    actual_point_cost: Optional[int] = None,
    error_code: str = "",
    error_message: str = "",
    cancel_requested_at: Optional[float] = None,
    next_attempt_at: Optional[float] = None,
    started_at: Optional[float] = None,
    finished_at: Optional[float] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    now = time.time()
    if next_attempt_at is None and status in {"submitted", "hydrating"}:
        next_attempt_at = now
    chat = task_response_chat(response)
    assets = task_response_assets(response)
    owns_connection = conn is None
    connection = conn or db_conn()
    cursor = connection.execute(
        """
        INSERT INTO tasks(
            api_key_id, account_id, kind, prompt, model_name, scene_id, resolution, ratio, duration,
            estimated_point_cost, actual_point_cost, request_id, payload_json, response_json, assets_json,
            chat_id, focus_id, status, error_code, error_message, attempt_count, cancel_requested_at,
            next_attempt_at,
            started_at, finished_at, created_at, updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            api_key_id,
            account_id,
            kind,
            prompt,
            model_name or "",
            scene_id or "",
            resolution or "",
            ratio or "",
            duration,
            estimated_point_cost,
            actual_point_cost,
            request_id or "",
            encode_json_value(payload),
            encode_json_value(response),
            encode_json_value(assets),
            chat.get("chatId") or "",
            chat.get("focusId") or (chat.get("chatId") or ""),
            status,
            error_code or "",
            error_message or "",
            0,
            cancel_requested_at,
            next_attempt_at,
            started_at,
            finished_at,
            now,
            now,
        ),
    )
    task_id = int(cursor.lastrowid)
    if owns_connection:
        connection.commit()
        connection.close()
    return task_id


def update_usage_log_for_task(task_id: int, api_key_id: Optional[int] = None, **fields: Any) -> None:
    if not fields:
        return
    conn = db_conn()
    query = "SELECT id FROM usage_log WHERE task_id=?"
    params: List[Any] = [task_id]
    if api_key_id is not None:
        query += " AND api_key_id=?"
        params.append(api_key_id)
    query += " ORDER BY id DESC LIMIT 1"
    row = conn.execute(query, tuple(params)).fetchone()
    if not row:
        conn.close()
        return
    payload = dict(fields)
    for key in ("response_summary",):
        if key in payload and payload[key] is not None:
            payload[key] = str(payload[key])[:200]
    assignments = ", ".join(f"{key}=?" for key in payload)
    values = list(payload.values()) + [row["id"]]
    conn.execute(f"UPDATE usage_log SET {assignments} WHERE id=?", values)
    conn.commit()
    conn.close()


def fetch_task_row(task_id: int, api_key_id: Optional[int] = None) -> Optional[sqlite3.Row]:
    conn = db_conn()
    if api_key_id is None:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM tasks WHERE id=? AND api_key_id=?", (task_id, api_key_id)).fetchone()
        if not row:
            legacy = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if legacy and legacy["api_key_id"] is None:
                usage = conn.execute(
                    "SELECT 1 FROM usage_log WHERE task_id=? AND api_key_id=? LIMIT 1",
                    (task_id, api_key_id),
                ).fetchone()
                if usage:
                    row = legacy
    conn.close()
    return row


def fetch_task_attempt_row(task_id: int, attempt_no: int) -> Optional[sqlite3.Row]:
    conn = db_conn()
    row = conn.execute(
        "SELECT * FROM task_attempts WHERE task_id=? AND attempt_no=? ORDER BY id DESC LIMIT 1",
        (task_id, attempt_no),
    ).fetchone()
    conn.close()
    return row


def create_task_attempt(task: sqlite3.Row, phase: str, status: str = "running") -> int:
    conn = db_conn()
    next_no_row = conn.execute("SELECT COALESCE(MAX(attempt_no), 0) + 1 AS next_no FROM task_attempts WHERE task_id=?", (task["id"],)).fetchone()
    attempt_no = int(next_no_row["next_no"] or 1)
    now = time.time()
    conn.execute(
        """
        INSERT INTO task_attempts(
            task_id, attempt_no, phase, account_id, status, error_code, error_message,
            request_payload_json, stream_summary_json, hydration_summary_json, assets_json,
            started_at, finished_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            task["id"],
            attempt_no,
            phase,
            task.get("account_id"),
            status,
            "",
            "",
            encode_json_value(json_value_from_db(task.get("payload_json")) or {}),
            None,
            None,
            None,
            now,
            None,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    attempt_id = row[0]
    conn.close()
    return attempt_id


def update_task_attempt(attempt_id: int, **fields: Any) -> None:
    if not fields:
        return
    conn = db_conn()
    payload = dict(fields)
    for key in ("request_payload_json", "stream_summary_json", "hydration_summary_json", "assets_json"):
        if key in payload:
            payload[key] = encode_json_value(payload[key])
    assignments = ", ".join(f"{key}=?" for key in payload)
    values = list(payload.values()) + [attempt_id]
    conn.execute(f"UPDATE task_attempts SET {assignments} WHERE id=?", values)
    conn.commit()
    conn.close()


def claim_next_task() -> Optional[Dict[str, Any]]:
    conn = db_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = time.time()
        row = conn.execute(
            """
            SELECT *
            FROM tasks
            WHERE
              (
                status='queued'
                OR (
                  status IN ('submitted', 'hydrating')
                  AND cancel_requested_at IS NULL
                  AND next_attempt_at IS NOT NULL
                  AND next_attempt_at <= ?
                )
              )
            ORDER BY
                CASE status
                    WHEN 'queued' THEN 0
                    WHEN 'hydrating' THEN 1
                    WHEN 'submitted' THEN 2
                    ELSE 3
                END,
                COALESCE(next_attempt_at, updated_at) ASC,
                id ASC
            LIMIT 1
            """
            ,
            (now,),
        ).fetchone()
        if not row:
            conn.commit()
            return None
        task = dict(row)
        claimed_from_status = task.get("status") or ""
        next_status = "running" if task.get("status") == "queued" else "hydrating"
        result = conn.execute(
            """
            UPDATE tasks
            SET status=?, started_at=COALESCE(started_at, ?), updated_at=?, attempt_count=attempt_count+1, next_attempt_at=NULL
            WHERE id=?
            """,
            (next_status, now, now, task["id"]),
        )
        if result.rowcount != 1:
            conn.rollback()
            return None
        conn.commit()
        task["status"] = next_status
        task["started_at"] = task.get("started_at") or now
        task["claimed_from_status"] = claimed_from_status
        task["next_attempt_at"] = None
        return task
    finally:
        conn.close()


def recover_stale_running_tasks(
    *,
    now: Optional[float] = None,
    stale_after_seconds: Optional[float] = None,
) -> int:
    current = time.time() if now is None else float(now)
    stale_after = (
        float_or_default(stale_after_seconds, 300.0)
        if stale_after_seconds is not None
        else float_or_default(gateway_cfg().get("running_task_stale_seconds"), 300.0)
    )
    cutoff = current - max(0.0, stale_after)
    message = "task worker stopped before the generation attempt completed"
    conn = db_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT id
            FROM tasks
            WHERE status='running'
              AND COALESCE(updated_at, started_at, created_at) <= ?
            ORDER BY id ASC
            """,
            (cutoff,),
        ).fetchall()
        task_ids = [int(row["id"]) for row in rows]
        for task_id in task_ids:
            conn.execute(
                """
                UPDATE tasks
                SET status='expired', error_code='WORKER_LOST', error_message=?,
                    finished_at=?, next_attempt_at=NULL, updated_at=?
                WHERE id=? AND status='running'
                """,
                (message, current, current, task_id),
            )
            conn.execute(
                """
                UPDATE task_attempts
                SET status='expired', error_code='WORKER_LOST', error_message=?, finished_at=?
                WHERE task_id=? AND status='running'
                """,
                (message, current, task_id),
            )
            conn.execute(
                """
                UPDATE usage_log
                SET status='expired', response_summary=?, error_code='WORKER_LOST', status_code=503
                WHERE task_id=?
                """,
                (message[:200], task_id),
            )
        conn.commit()
        return len(task_ids)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def task_worker_enabled() -> bool:
    return bool(gateway_cfg().get("enable_background_worker", True))


def task_worker_poll_interval() -> float:
    try:
        return float(gateway_cfg().get("task_worker_poll_interval_seconds") or 1)
    except (TypeError, ValueError):
        return 1.0


def account_failover_max_attempts() -> int:
    return max(
        1,
        min(
            20,
            int_or_default(gateway_cfg().get("account_failover_max_attempts"), 5),
        ),
    )


def account_failover_error_codes() -> set[str]:
    configured = gateway_cfg().get(
        "account_failover_error_codes",
        ["200001"],
    )
    if isinstance(configured, str):
        values = re.split(r"[,;\s]+", configured)
    elif isinstance(configured, (list, tuple, set)):
        values = configured
    else:
        values = []
    return {
        str(value).strip()
        for value in values
        if str(value).strip()
        and str(value).strip() not in GATEWAY_ENVIRONMENT_ERROR_CODES
    }


def task_generation_attempt_account_ids(task_id: int) -> List[int]:
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT account_id
        FROM task_attempts
        WHERE task_id=? AND phase='generation' AND account_id IS NOT NULL
        ORDER BY attempt_no ASC, id ASC
        """,
        (task_id,),
    ).fetchall()
    conn.close()
    return [int(row["account_id"]) for row in rows]


def resolve_task_body(task: sqlite3.Row):
    payload = json_value_from_db(task["payload_json"]) or {}
    if not isinstance(payload, dict):
        raise RuntimeError("task payload is malformed")
    return GatewayGenerateIn(**payload)


def resolve_task_failover_body(task: Dict[str, Any]) -> Any:
    body = resolve_task_body(task)
    data = model_data(body)
    for field in ("model_name", "ratio", "resolution", "duration", "scene_id"):
        selected_value = task.get(field)
        if selected_value not in (None, ""):
            data[field] = selected_value
    return GatewayGenerateIn(**data)


def select_task_failover_account(
    task: Dict[str, Any],
    error: Exception,
) -> Optional[Tuple[sqlite3.Row, Dict[str, Any], Dict[str, Any], Optional[int]]]:
    if upstream_error_code(error) not in account_failover_error_codes():
        return None
    body = resolve_task_failover_body(task)
    if body.account_id is not None:
        return None
    attempted_account_ids = task_generation_attempt_account_ids(int(task["id"]))
    if len(attempted_account_ids) >= account_failover_max_attempts():
        return None
    try:
        account, caps, options, estimated_point_cost = select_generation_account(
            body,
            request_id=str(task.get("request_id") or ""),
            excluded_account_ids=attempted_account_ids,
        )
        if task.get("api_key_id"):
            policy = resolve_api_key_policy(get_api_key_record(int(task["api_key_id"])))
            enforce_api_key_scope(
                policy,
                body.kind,
                options,
                caps,
                str(task.get("request_id") or ""),
            )
    except (GatewayAPIError, HTTPException):
        return None
    return account, caps, options, estimated_point_cost


def task_retryable_status(status: str) -> bool:
    return status in {"failed", "expired"}


def task_hydratable_status(status: str) -> bool:
    return status in {"submitted", "hydrating"}


def task_cancellable_status(status: str) -> bool:
    return status in TASK_CANCELLABLE_STATUSES


def task_retry_interval_seconds(status: str) -> float:
    key = "hydrating_task_retry_interval_seconds" if status == "hydrating" else "submitted_task_retry_interval_seconds"
    default = float_or_default(oreate_cfg().get("video_hydration_poll_interval_seconds"), 10.0)
    return max(0.0, float_or_default(gateway_cfg().get(key), default))


def task_expire_seconds(status: str) -> float:
    key = "hydrating_task_expire_seconds" if status == "hydrating" else "submitted_task_expire_seconds"
    default = float_or_default(oreate_cfg().get("video_hydration_timeout_seconds"), 600.0)
    return max(0.0, float_or_default(gateway_cfg().get(key), default))


def task_hydration_attempt_timeout_seconds() -> float:
    return max(0.0, float_or_default(gateway_cfg().get("task_hydration_attempt_timeout_seconds"), 0.0))


def task_poll_interval_seconds() -> float:
    return max(0.0, float_or_default(oreate_cfg().get("video_hydration_poll_interval_seconds"), 10.0))


def task_live_snapshot(task_id: int) -> Optional[Dict[str, Any]]:
    row = fetch_task_row(task_id)
    return dict(row) if row else None


def task_cancel_requested(task_id: int) -> bool:
    row = task_live_snapshot(task_id)
    if not row:
        return True
    return row.get("status") == "cancelled" or bool(row.get("cancel_requested_at"))


def task_expired(task: Dict[str, Any]) -> bool:
    base_status = task.get("claimed_from_status") or task.get("status") or ""
    if base_status not in {"submitted", "hydrating"}:
        return False
    max_age = task_expire_seconds(base_status)
    if max_age <= 0:
        return False
    started_at = task.get("started_at") or task.get("created_at") or time.time()
    return (time.time() - float(started_at)) >= max_age


def task_next_attempt_at(task: Dict[str, Any], phase: str, status: str) -> Optional[float]:
    if status not in {"submitted", "hydrating"}:
        return None
    now = time.time()
    if phase == "generation":
        return now
    base_status = task.get("claimed_from_status") or status
    return now + task_retry_interval_seconds(base_status)


def cancel_task_attempt(task: Dict[str, Any], attempt_id: int, message: str = "task cancelled") -> None:
    now = time.time()
    task_id = int(task["id"])
    expected_status = str(task.get("status") or "")
    conn = db_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT status,api_key_id FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if not current:
            conn.rollback()
            return
        current_attempt = conn.execute(
            """
            SELECT id,attempt_no
            FROM task_attempts
            WHERE id=? AND task_id=? AND status='running'
              AND id=(
                  SELECT id FROM task_attempts
                  WHERE task_id=?
                  ORDER BY attempt_no DESC,id DESC
                  LIMIT 1
              )
            """,
            (attempt_id, task_id, task_id),
        ).fetchone()
        if not current_attempt:
            conn.rollback()
            return
        task_is_cancelled = bool(current and str(current["status"] or "") == "cancelled")
        if not task_is_cancelled and expected_status in {"running", "hydrating"}:
            result = conn.execute(
                """
                UPDATE tasks
                SET status='cancelled', error_code='TASK_CANCELLED', error_message=?,
                    cancel_requested_at=COALESCE(cancel_requested_at, ?),
                    finished_at=?, next_attempt_at=NULL, updated_at=?
                WHERE id=? AND status=?
                """,
                (
                    message,
                    now,
                    now,
                    now,
                    task_id,
                    expected_status,
                ),
            )
            if result.rowcount != 1:
                conn.rollback()
                return
            task_is_cancelled = True
        if not task_is_cancelled:
            conn.rollback()
            return
        attempt_update = conn.execute(
            """
            UPDATE task_attempts
            SET status='cancelled', error_code='TASK_CANCELLED', error_message=?,
                assets_json=?, finished_at=?
            WHERE id=? AND task_id=? AND status='running' AND attempt_no=?
            """,
            (
                message,
                encode_json_value([]),
                now,
                attempt_id,
                task_id,
                current_attempt["attempt_no"],
            ),
        )
        if attempt_update.rowcount != 1:
            conn.rollback()
            return
        tenant_api_key_id = current["api_key_id"] or task.get("api_key_id")
        if tenant_api_key_id:
            conn.execute(
                """
                UPDATE usage_log
                SET status='cancelled', response_summary='cancelled',
                    error_code='TASK_CANCELLED', status_code=499
                WHERE id=(
                    SELECT id FROM usage_log
                    WHERE task_id=? AND api_key_id=?
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (task_id, tenant_api_key_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def expire_task_attempt(task: Dict[str, Any], attempt_id: int, message: str = "task expired while waiting for upstream assets") -> None:
    now = time.time()
    update_task_attempt(
        attempt_id,
        status="expired",
        error_code="TASK_EXPIRED",
        error_message=message,
        assets_json=[],
        finished_at=now,
    )
    update_task_record(
        task["id"],
        status="expired",
        error_code="TASK_EXPIRED",
        error_message=message,
        finished_at=now,
        next_attempt_at=None,
    )
    if task.get("api_key_id"):
        update_usage_log_for_task(
            task["id"],
            task.get("api_key_id"),
            status="expired",
            response_summary=message,
            error_code="TASK_EXPIRED",
            status_code=504,
        )


def run_generation_attempt(task: sqlite3.Row, attempt_id: int) -> Dict[str, Any]:
    body = resolve_task_body(task)
    conn = db_conn()
    account_row = conn.execute("SELECT * FROM accounts WHERE id=?", (task["account_id"],)).fetchone()
    conn.close()
    if not account_row:
        raise HTTPException(503, "no verified account available")
    balance_before = capture_account_balance_snapshot(account_row)
    if balance_before:
        update_task_record(
            task["id"],
            **balance_snapshot_fields(balance_before, "balance_before"),
        )
        update_account_balance_snapshot(account_row["id"], balance_before["point_balance_json"])
    caps = capabilities_from_account(account_row)
    options = effective_generation_options(body, caps)
    validate_generation_options(body.kind, options, caps)
    generation = submit_generation_for_account(
        account_row,
        body.kind,
        body.prompt,
        options,
        hydration_timeout_sec=task_hydration_attempt_timeout_seconds(),
        hydration_poll_interval_sec=task_poll_interval_seconds(),
        should_stop=lambda: task_cancel_requested(task["id"]),
    )
    balance_after = capture_account_balance_snapshot(account_row)
    if balance_after:
        update_account_balance_snapshot(account_row["id"], balance_after["point_balance_json"])
    return {
        "account_id": account_row["id"],
        "body": body,
        "options": options,
        "generation": generation,
        "balance_before": balance_before,
        "balance_after": balance_after,
        "actual_point_cost": actual_point_cost_from_balance_snapshots(balance_before, balance_after)
        if generation.get("status") == "completed"
        else None,
    }


def run_hydration_attempt(task: sqlite3.Row, attempt_id: int) -> Dict[str, Any]:
    body = resolve_task_body(task)
    conn = db_conn()
    account_row = conn.execute("SELECT * FROM accounts WHERE id=?", (task["account_id"],)).fetchone()
    conn.close()
    if not account_row:
        raise HTTPException(503, "no verified account available")
    # An asynchronous task can spend points after the submit request returns but
    # before a later hydration pass observes the completed asset. Keep the
    # snapshot captured by the generation phase as the billing baseline instead
    # of replacing it with the already-deducted balance on every hydration poll.
    persisted_balance_before = balance_snapshot_from_row(task, "balance_before")
    balance_before = persisted_balance_before
    if balance_before is None:
        balance_before = capture_account_balance_snapshot(account_row)
    if balance_before and persisted_balance_before is None:
        update_task_record(
            task["id"],
            **balance_snapshot_fields(balance_before, "balance_before"),
        )
        update_account_balance_snapshot(account_row["id"], balance_before["point_balance_json"])
    session = CLIENT.session_from_account(account_row)
    response_data = json_value_from_db(task.get("response_json")) or {}
    chat = task_response_chat(response_data)
    chat_id = task.get("chat_id") or chat.get("chatId") or ""
    if not chat_id:
        raise RuntimeError("task chat_id missing for hydration")
    if body.kind == "video":
        hydration = CLIENT.hydrate_generation_result_until_assets(
            session,
            chat_id,
            timeout_sec=task_hydration_attempt_timeout_seconds(),
            poll_interval_sec=task_poll_interval_seconds(),
            chat_type="aiVideo",
            should_stop=lambda: task_cancel_requested(task["id"]),
        )
    else:
        hydration = CLIENT.hydrate_generation_result(session, chat_id)
    if hydration.get("error"):
        raise UpstreamGenerationError(hydration["error"])
    if hydration.get("status") == "cancelled":
        raise TaskCancelledError("task cancelled")
    assets = hydration.get("assets") or []
    result_status = "completed" if assets else "submitted"
    balance_after = capture_account_balance_snapshot(account_row)
    if balance_after:
        update_account_balance_snapshot(account_row["id"], balance_after["point_balance_json"])
    return {
        "account_id": account_row["id"],
        "body": body,
        "hydration": hydration,
        "assets": assets,
        "status": result_status,
        "chat_id": chat_id,
        "balance_before": balance_before,
        "balance_after": balance_after,
        "actual_point_cost": actual_point_cost_from_balance_snapshots(balance_before, balance_after)
        if result_status == "completed"
        else None,
    }


def finalize_task_attempt(task: sqlite3.Row, attempt_id: int, phase: str, result: Dict[str, Any], status: str) -> None:
    now = time.time()
    if task_cancel_requested(task["id"]):
        cancel_task_attempt(task, attempt_id, result.get("error_message") or "task cancelled")
        return
    next_attempt_at = task_next_attempt_at(task, phase, status)
    expected_status = {"generation": "running", "hydration": "hydrating"}.get(phase)
    if expected_status is None:
        raise ValueError(f"unsupported task attempt phase: {phase}")
    task_id = int(task["id"])
    task_fields = {
        "status": status,
        "account_id": result.get("account_id", task.get("account_id")),
        "chat_id": result.get("chat_id", task.get("chat_id") or ""),
        "focus_id": result.get("focus_id", task.get("focus_id") or ""),
        "response_json": result.get("response_json"),
        "assets_json": result.get("assets") or [],
        "error_code": result.get("error_code") or "",
        "error_message": result.get("error_message") or "",
        "actual_point_cost": result.get("actual_point_cost"),
        "balance_before_json": result.get("balance_before_json"),
        "balance_after_json": result.get("balance_after_json"),
        "balance_before_rest_point": result.get("balance_before_rest_point"),
        "balance_before_daily_point": result.get("balance_before_daily_point"),
        "balance_before_bonus_point": result.get("balance_before_bonus_point"),
        "balance_after_rest_point": result.get("balance_after_rest_point"),
        "balance_after_daily_point": result.get("balance_after_daily_point"),
        "balance_after_bonus_point": result.get("balance_after_bonus_point"),
        "next_attempt_at": next_attempt_at,
        "finished_at": now if status in TASK_TERMINAL_STATUSES else None,
        "updated_at": now,
    }
    for key in ("response_json", "assets_json", "balance_before_json", "balance_after_json"):
        task_fields[key] = encode_json_value(task_fields[key])
    task_assignments = ", ".join(f"{key}=?" for key in task_fields)
    conn = db_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        task_update = conn.execute(
            f"""
            UPDATE tasks
            SET {task_assignments}
            WHERE id=? AND status=? AND cancel_requested_at IS NULL
            """,
            tuple(task_fields.values()) + (task_id, expected_status),
        )
        if task_update.rowcount != 1:
            current = conn.execute(
                "SELECT status,cancel_requested_at FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            cancelled = bool(
                current
                and (
                    str(current["status"] or "") == "cancelled"
                    or current["cancel_requested_at"] is not None
                )
            )
            discarded_status = "cancelled" if cancelled else "expired"
            discarded_code = "TASK_CANCELLED" if cancelled else "TASK_ATTEMPT_STALE"
            discarded_message = (
                result.get("error_message") or "task cancelled"
                if cancelled
                else "task state changed before the attempt result could be finalized"
            )
            conn.execute(
                """
                UPDATE task_attempts
                SET status=?, error_code=?, error_message=?, assets_json=?, finished_at=?
                WHERE id=? AND task_id=? AND status='running'
                """,
                (
                    discarded_status,
                    discarded_code,
                    discarded_message,
                    encode_json_value([]),
                    now,
                    attempt_id,
                    task_id,
                ),
            )
            conn.commit()
            return
        attempt_update = conn.execute(
            """
            UPDATE task_attempts
            SET status=?, error_code=?, error_message=?, stream_summary_json=?,
                hydration_summary_json=?, assets_json=?, finished_at=?
            WHERE id=? AND task_id=? AND status='running'
            """,
            (
                status,
                result.get("error_code") or "",
                result.get("error_message") or "",
                encode_json_value(result.get("stream_summary")),
                encode_json_value(result.get("hydration_summary")),
                encode_json_value(result.get("assets") or []),
                now,
                attempt_id,
                task_id,
            ),
        )
        if attempt_update.rowcount != 1:
            raise RuntimeError("task attempt state changed before finalization")
        if task.get("api_key_id"):
            usage_status_code = result.get("status_code") or (
                200
                if status == "completed"
                else 202
                if status in {"queued", "submitted", "hydrating"}
                else 503
            )
            conn.execute(
                """
                UPDATE usage_log
                SET status=?, response_summary=?, error_code=?,
                    actual_point_cost=?, status_code=?
                WHERE id=(
                    SELECT id FROM usage_log
                    WHERE task_id=? AND api_key_id=?
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (
                    status,
                    str(result.get("response_summary") or status)[:200],
                    result.get("error_code") or "",
                    result.get("actual_point_cost"),
                    usage_status_code,
                    task_id,
                    task.get("api_key_id"),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def schedule_task_account_failover(
    task: Dict[str, Any],
    attempt_id: int,
    error: UpstreamGenerationError,
    account: sqlite3.Row,
    options: Dict[str, Any],
    estimated_point_cost: Optional[int],
) -> bool:
    now = time.time()
    task_id = int(task["id"])
    code = upstream_error_code(error) or "UPSTREAM_ERROR"
    message = account_failure_message(error, code)
    response = {
        "status": "queued",
        "account_failover_count": len(task_generation_attempt_account_ids(task_id)),
    }
    conn = db_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        task_update = conn.execute(
            """
            UPDATE tasks
            SET status='queued', account_id=?, model_name=?, scene_id=?, resolution=?, ratio=?,
                duration=?, estimated_point_cost=?, response_json=?, assets_json=?,
                chat_id='', focus_id='', error_code='', error_message='',
                balance_before_json=NULL, balance_after_json=NULL,
                balance_before_rest_point=NULL, balance_before_daily_point=NULL,
                balance_before_bonus_point=NULL, balance_after_rest_point=NULL,
                balance_after_daily_point=NULL, balance_after_bonus_point=NULL,
                actual_point_cost=NULL, next_attempt_at=NULL, finished_at=NULL, updated_at=?
            WHERE id=? AND status='running' AND cancel_requested_at IS NULL
            """,
            (
                account["id"],
                options.get("model_name") or "",
                options.get("scene_id") or "",
                options.get("resolution") or "",
                options.get("ratio") or "",
                options.get("duration"),
                estimated_point_cost,
                encode_json_value(response),
                encode_json_value([]),
                now,
                task_id,
            ),
        )
        if task_update.rowcount != 1:
            conn.rollback()
            return False
        attempt_update = conn.execute(
            """
            UPDATE task_attempts
            SET status='failed', error_code=?, error_message=?, assets_json=?, finished_at=?
            WHERE id=? AND task_id=? AND status='running'
            """,
            (
                code,
                message,
                encode_json_value([]),
                now,
                attempt_id,
                task_id,
            ),
        )
        if attempt_update.rowcount != 1:
            conn.rollback()
            return False
        if task.get("api_key_id"):
            conn.execute(
                """
                UPDATE usage_log
                SET account_id=?, status='queued', response_summary=?,
                    error_code='', estimated_point_cost=?, actual_point_cost=NULL, status_code=202
                WHERE id=(
                    SELECT id FROM usage_log
                    WHERE task_id=? AND api_key_id=?
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (
                    account["id"],
                    f"账号异常，正在切换账号重试（上游错误 {code}）"[:200],
                    estimated_point_cost,
                    task_id,
                    task.get("api_key_id"),
                ),
            )
        conn.commit()
        TASK_WORKER_WAKE.set()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute_task(task: sqlite3.Row) -> bool:
    phase = "generation" if task.get("status") == "running" else "hydration"
    attempt_id = create_task_attempt(task, phase, status="running")
    try:
        if task_cancel_requested(task["id"]):
            raise TaskCancelledError("task cancelled")
        if phase == "hydration" and task_expired(task):
            expire_task_attempt(task, attempt_id)
            return True
        if phase == "generation":
            result = run_generation_attempt(task, attempt_id)
            generation = result["generation"]
            if task_cancel_requested(task["id"]) or generation.get("status") == "cancelled":
                raise TaskCancelledError("task cancelled")
            assets = generation.get("assets") or []
            status = generation.get("status") or ("completed" if assets else "submitted")
            response = generation.get("response") or {}
            balance_before = result.get("balance_before")
            balance_after = result.get("balance_after")
            result_payload = {
                "account_id": result.get("account_id"),
                "response_json": response,
                "assets": assets,
                "chat_id": response.get("chat", {}).get("chatId") if isinstance(response.get("chat"), dict) else task.get("chat_id"),
                "focus_id": response.get("chat", {}).get("focusId") if isinstance(response.get("chat"), dict) else task.get("focus_id"),
                "stream_summary": generation.get("stream"),
                "hydration_summary": generation.get("hydration"),
                "response_summary": json.dumps({"status": status, "assets": len(assets)}, ensure_ascii=False),
                "status_code": 200 if status == "completed" else 202,
                "actual_point_cost": result.get("actual_point_cost"),
            }
            result_payload.update(balance_snapshot_fields(balance_before, "balance_before"))
            result_payload.update(balance_snapshot_fields(balance_after, "balance_after"))
            if status == "completed":
                mark_account_success(result["account_id"])
            else:
                mark_account_success(result["account_id"])
            finalize_task_attempt(task, attempt_id, phase, result_payload, status)
            return True

        hydration_result = run_hydration_attempt(task, attempt_id)
        if task_cancel_requested(task["id"]) or hydration_result.get("status") == "cancelled":
            raise TaskCancelledError("task cancelled")
        status = hydration_result.get("status") or ("completed" if hydration_result.get("assets") else "submitted")
        result_payload = {
            "account_id": hydration_result.get("account_id"),
            "response_json": json_value_from_db(task.get("response_json")) or {},
            "assets": hydration_result.get("assets") or [],
            "chat_id": hydration_result.get("chat_id") or task.get("chat_id") or "",
            "focus_id": task.get("focus_id") or task.get("chat_id") or "",
            "hydration_summary": hydration_result.get("hydration"),
            "response_summary": json.dumps({"status": status, "assets": len(hydration_result.get("assets") or [])}, ensure_ascii=False),
            "status_code": 200 if status == "completed" else 202,
            "actual_point_cost": hydration_result.get("actual_point_cost"),
        }
        result_payload.update(balance_snapshot_fields(hydration_result.get("balance_before"), "balance_before"))
        result_payload.update(balance_snapshot_fields(hydration_result.get("balance_after"), "balance_after"))
        mark_account_success(result_payload["account_id"])
        finalize_task_attempt(task, attempt_id, phase, result_payload, status)
        return True
    except TaskCancelledError as exc:
        cancel_task_attempt(task, attempt_id, str(exc) or "task cancelled")
        return True
    except UpstreamGenerationError as exc:
        if task_cancel_requested(task["id"]):
            cancel_task_attempt(task, attempt_id, "task cancelled")
            return True
        error = exc.error if isinstance(exc.error, dict) else {}
        code = error.get("code") or "UPSTREAM_ERROR"
        message = error.get("message") or str(exc)
        if task.get("account_id"):
            mark_account_failure(task["account_id"], exc)
        if phase == "generation":
            failover = select_task_failover_account(task, exc)
            if failover is not None:
                account, _caps, options, estimated_point_cost = failover
                if schedule_task_account_failover(
                    task,
                    attempt_id,
                    exc,
                    account,
                    options,
                    estimated_point_cost,
                ):
                    return True
        result_payload = build_failed_task_result_payload(task["id"], task.get("account_id"), code, message, status_code=503)
        finalize_task_attempt(task, attempt_id, phase, result_payload, "failed")
        return False
    except Exception as exc:
        if task_cancel_requested(task["id"]):
            cancel_task_attempt(task, attempt_id, "task cancelled")
            return True
        if task.get("account_id"):
            mark_account_failure(task["account_id"], exc)
        message = str(exc)
        result_payload = build_failed_task_result_payload(
            task["id"],
            task.get("account_id"),
            "UPSTREAM_ERROR",
            message,
            status_code=503,
        )
        finalize_task_attempt(task, attempt_id, phase, result_payload, "failed")
        return False


def process_task_queue(limit: int = 1) -> int:
    processed = 0
    with TASK_WORKER_LOCK:
        while processed < max(1, int(limit)):
            task = claim_next_task()
            if not task:
                break
            execute_task(task)
            processed += 1
    return processed


def task_worker_loop() -> None:
    while not TASK_WORKER_STOP.is_set():
        processed = process_task_queue(limit=1)
        if processed:
            continue
        TASK_WORKER_WAKE.wait(task_worker_poll_interval())
        TASK_WORKER_WAKE.clear()


def ensure_task_worker_started() -> None:
    global TASK_WORKER_THREAD
    if not task_worker_enabled():
        return
    if TASK_WORKER_THREAD and TASK_WORKER_THREAD.is_alive():
        return
    TASK_WORKER_STOP.clear()
    TASK_WORKER_THREAD = threading.Thread(target=task_worker_loop, name="task-worker", daemon=True)
    TASK_WORKER_THREAD.start()


def stop_task_worker(timeout: float) -> bool:
    """Request worker shutdown and wait for at most ``timeout`` seconds."""

    global TASK_WORKER_THREAD
    TASK_WORKER_STOP.set()
    TASK_WORKER_WAKE.set()
    worker = TASK_WORKER_THREAD
    if worker is None or not worker.is_alive():
        TASK_WORKER_THREAD = None
        return True

    join_timeout = float_or_default(timeout, 0.0)
    if not math.isfinite(join_timeout):
        join_timeout = 0.0
    worker.join(timeout=max(0.0, join_timeout))
    if worker.is_alive():
        return False

    TASK_WORKER_THREAD = None
    return True


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


def pick_account_for_capabilities() -> Optional[sqlite3.Row]:
    conn = db_conn()
    row = conn.execute(
        """
        SELECT * FROM accounts
        WHERE status IN ('verified', 'active')
          AND (
            (model_info_json IS NOT NULL AND model_info_json != '')
            OR
            (video_info_json IS NOT NULL AND video_info_json != '')
          )
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()
    return row


def capability_response_from_account(account: sqlite3.Row) -> Dict[str, Any]:
    caps = normalize_capabilities(json_from_db(account["model_info_json"]), json_from_db(account["video_info_json"]))
    return {"ok": True, "source_account_id": account["id"], **caps}


def filter_capabilities_for_api_key(caps: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(caps))
    allowed_kinds = policy.get("allowed_kinds") or []
    allowed_models = policy.get("allowed_models") or []
    allowed_scenes = policy.get("allowed_scenes") or []
    allowed_resolutions = policy.get("allowed_resolutions") or []
    allowed_durations = policy.get("allowed_durations") or []
    allow_experimental = bool(policy.get("allow_experimental", False))

    def filter_models(kind: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for model in out.get(kind, {}).get("models") or []:
            if allowed_models and model.get("name") not in allowed_models:
                continue
            if not allow_experimental and bool(model.get("experimental")):
                continue
            filtered = dict(model)
            if allowed_resolutions:
                filtered["resolutions"] = [value for value in filtered.get("resolutions") or [] if value in allowed_resolutions]
                if (model.get("resolutions") or []) and not filtered["resolutions"]:
                    continue
            if kind == "video" and allowed_durations:
                filtered["durations"] = [value for value in filtered.get("durations") or [] if value in allowed_durations]
                if (model.get("durations") or []) and not filtered["durations"]:
                    continue
            items.append(filtered)
        return items

    if allowed_kinds and "image" not in allowed_kinds:
        out.setdefault("image", {})["models"] = []
    else:
        out.setdefault("image", {})["models"] = filter_models("image")

    if allowed_kinds and "video" not in allowed_kinds:
        out.setdefault("video", {})["models"] = []
        out.setdefault("video", {})["scenes"] = []
    else:
        out.setdefault("video", {})["models"] = filter_models("video")
        scenes: List[Dict[str, Any]] = []
        for scene in out.get("video", {}).get("scenes") or []:
            if allowed_scenes and scene.get("scene_id") not in allowed_scenes:
                continue
            if not allow_experimental and bool(scene.get("experimental")):
                continue
            scenes.append(scene)
        out.setdefault("video", {})["scenes"] = scenes
    return out


def load_capabilities_from_pool(api_key_policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    account = pick_account_for_capabilities()
    if not account:
        raise HTTPException(503, "no account with model capabilities available")
    payload = capability_response_from_account(account)
    if api_key_policy:
        filtered = filter_capabilities_for_api_key({"image": payload.get("image", {}), "video": payload.get("video", {})}, api_key_policy)
        payload["image"] = filtered.get("image", {})
        payload["video"] = filtered.get("video", {})
    return payload


def refresh_capabilities_from_pool() -> Dict[str, Any]:
    conn = db_conn()
    account = conn.execute(
        """
        SELECT * FROM accounts
        WHERE status IN ('verified', 'active')
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()
    if not account:
        raise HTTPException(503, "no verified account available")
    session = CLIENT.session_from_account(account)
    image_info = CLIENT.fetch_image_models(session)
    video_info = {
        "models": CLIENT.fetch_video_models(session),
        "scenes": CLIENT.fetch_video_scenes(session),
    }
    now = time.time()
    conn = db_conn()
    conn.execute(
        "UPDATE accounts SET model_info_json=?, video_info_json=?, updated_at=? WHERE id=?",
        (json.dumps(image_info, ensure_ascii=False), json.dumps(video_info, ensure_ascii=False), now, account["id"]),
    )
    conn.commit()
    conn.close()
    caps = normalize_capabilities(image_info, video_info)
    return {"ok": True, "source_account_id": account["id"], **caps}


def browser_generation_enabled() -> bool:
    return bool(CFG.get("oreate", {}).get("browser_worker_enabled", False))


def browser_worker_script_path() -> Path:
    return BASE_DIR / "oreate_browser_worker.js"


def run_browser_generation(
    account: sqlite3.Row,
    kind: str,
    prompt: str,
    options: Dict[str, Any],
    *,
    image_config: Optional[Dict[str, Any]],
    video_config: Optional[Dict[str, Any]],
    attachments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    orete_config = CFG.get("oreate", {})
    script_path = browser_worker_script_path()
    if not script_path.is_file():
        raise RuntimeError(f"browser generation worker is missing: {script_path}")

    executable_path = str(orete_config.get("chromium_executable") or "").strip()
    node_modules_path = str(orete_config.get("browser_worker_node_modules") or "").strip()
    if not executable_path:
        raise RuntimeError("browser generation worker requires chromium_executable")
    if not node_modules_path:
        raise RuntimeError("browser generation worker requires browser_worker_node_modules")

    timeout_seconds = max(
        10,
        int_or_default(
            orete_config.get("browser_worker_timeout_seconds"),
            150,
        ),
    )
    readiness_timeout_seconds = max(
        5,
        min(
            int_or_default(
                orete_config.get("browser_worker_readiness_timeout_seconds"),
                60,
            ),
            max(5, timeout_seconds - 30),
        ),
    )
    # Leave enough time for Chromium startup, navigation and orderly shutdown.
    # Otherwise the outer subprocess timeout can terminate a healthy image
    # stream just before the worker returns its final JSON result.
    stream_wait_budget = max(5, timeout_seconds - 30)
    stream_wait_seconds = (
        min(
            float(orete_config.get("video_stream_wait_seconds") or 60),
            float(stream_wait_budget),
        )
        if kind == "video"
        else float(stream_wait_budget)
    )
    payload = {
        "baseUrl": str(orete_config.get("base_url") or "https://www.oreateai.com").rstrip("/"),
        "kind": kind,
        "chatType": "aiImage" if kind == "image" else "aiVideo",
        "prompt": prompt,
        "options": options,
        "imageConfig": image_config,
        "videoConfig": video_config,
        "attachments": attachments,
        "account": {
            "email": str(account["email"] or ""),
            "ouid": decrypt_secret_value(account["ouid"], required=True),
            "ouss": decrypt_secret_value(account["ouss"], required=True),
        },
        "runtime": {
            "chromiumExecutable": executable_path,
            "nodeModulesPath": node_modules_path,
            "streamWaitMs": max(5_000, int(stream_wait_seconds * 1000)),
            "navigationTimeoutMs": min(max(30_000, timeout_seconds * 1000 // 2), 90_000),
            "readinessTimeoutMs": int(readiness_timeout_seconds * 1000),
        },
    }
    command = [
        str(orete_config.get("browser_worker_node") or "node"),
        str(script_path),
    ]
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"browser generation worker timed out after {timeout_seconds} seconds"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown browser worker failure").strip()
        raise RuntimeError(f"browser generation worker failed: {detail[:1000]}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"browser generation worker returned malformed JSON: {completed.stdout[:500]}"
        ) from exc
    if not isinstance(result, dict):
        raise RuntimeError("browser generation worker returned an invalid result")
    if isinstance(result.get("error"), dict):
        raise UpstreamGenerationError(result["error"])
    chat = result.get("chat")
    stream = result.get("stream")
    if not isinstance(chat, dict) or not chat.get("chatId"):
        raise RuntimeError("browser generation worker did not return a chat id")
    if not isinstance(stream, dict):
        raise RuntimeError("browser generation worker did not return a stream result")
    events = stream.get("events") if isinstance(stream.get("events"), list) else []
    error = classify_sse_error(events)
    stream["error"] = error
    if error:
        stream["status"] = "failed"
    return {"chat": chat, "stream": stream}


def submit_generation_for_account(
    account: sqlite3.Row,
    kind: str,
    prompt: str,
    options: Dict[str, Any],
    hydration_timeout_sec: Optional[float] = None,
    hydration_poll_interval_sec: Optional[float] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    s = CLIENT.session_from_account(account)
    chat_type = "aiImage" if kind == "image" else "aiVideo"
    caps = capabilities_from_account(account)
    request_payload: Dict[str, Any] = {
        "chatType": chat_type,
        "messages": [{"role": "user", "content": prompt, "attachments": []}],
    }
    image_config = None
    video_config = None
    attachments: List[Dict[str, Any]] = []
    if kind == "image":
        image_config = build_image_config(options)
        request_payload["imageConfig"] = image_config
    else:
        model = find_capability_model(caps.get("video", {}).get("models") or [], options.get("model_name") or "") or {}
        video_config = build_video_config(options, model)
        attachments = build_video_message_attachments(options)
        request_payload["messages"][0]["attachments"] = attachments
        request_payload["videoConfig"] = video_config

    if browser_generation_enabled():
        if should_stop is not None and should_stop():
            raise TaskCancelledError("task cancelled before browser generation")
        browser_result = run_browser_generation(
            account,
            kind,
            prompt,
            options,
            image_config=image_config,
            video_config=video_config,
            attachments=attachments,
        )
        chat = browser_result["chat"]
        stream = browser_result["stream"]
    else:
        chat = CLIENT.create_chat_session(s, chat_type)
        stream = CLIENT.stream_generation(
            s,
            chat_id=chat["chatId"],
            focus_id=chat.get("focusId") or chat["chatId"],
            chat_type=chat_type,
            prompt=prompt,
            image_config=image_config,
            video_config=video_config,
            attachments=attachments,
            account=account,
            should_stop=should_stop,
        )
    if stream.get("error"):
        raise UpstreamGenerationError(stream["error"])
    if stream.get("status") == "cancelled":
        hydration = {"raw": {}, "assets": [], "status": "cancelled"}
    elif kind == "video" and stream.get("status") == "submitted":
        hydration = CLIENT.hydrate_generation_result_until_assets(
            s,
            chat["chatId"],
            timeout_sec=hydration_timeout_sec,
            poll_interval_sec=hydration_poll_interval_sec,
            chat_type=chat_type,
            should_stop=should_stop,
        )
    elif kind == "video":
        hydration = CLIENT.hydrate_generation_result(s, chat["chatId"], chat_type=chat_type)
    else:
        hydration = CLIENT.hydrate_generation_result(s, chat["chatId"])
    if hydration.get("error"):
        raise UpstreamGenerationError(hydration["error"])
    stream_assets = extract_generation_assets(stream.get("events") or [])
    assets = hydration.get("assets") or stream_assets
    if assets and not hydration.get("assets"):
        hydration = {**hydration, "assets": assets, "status": "completed"}
    response = {
        "chat": {"chatId": chat["chatId"], "focusId": chat.get("focusId") or chat["chatId"]},
        "stream": stream,
        "hydration": hydration,
    }
    return {
        "payload": request_payload,
        "response": response,
        "assets": assets,
        "stream": stream,
        "hydration": hydration,
        "status": "completed" if assets else hydration.get("status") or "submitted",
    }


def generation_probe_options(account: sqlite3.Row) -> Dict[str, Any]:
    caps = capabilities_from_account(account)
    models = [
        model
        for model in caps.get("image", {}).get("models") or []
        if bool(model.get("enabled", True))
    ]
    if not models:
        raise GatewayAPIError(
            503,
            "ACCOUNT_PROBE_UNSUPPORTED",
            "account has no enabled image model for generation health validation",
        )

    candidates: List[Tuple[float, int, int, Dict[str, Any]]] = []
    for model_index, model in enumerate(models):
        resolutions = list(model.get("resolutions") or [])
        ratios = list(model.get("ratios") or [])
        if not resolutions or not ratios:
            continue
        preferred_ratio = "1:1" if "1:1" in ratios else ratios[0]
        for resolution_index, resolution in enumerate(resolutions):
            options = {
                "model_name": str(model.get("name") or ""),
                "ratio": preferred_ratio,
                "resolution": resolution,
            }
            point_cost = estimate_point_cost("image", options, caps)
            sort_cost = float(point_cost) if point_cost is not None else math.inf
            candidates.append(
                (sort_cost, model_index, resolution_index, options)
            )

    if not candidates:
        raise GatewayAPIError(
            503,
            "ACCOUNT_PROBE_UNSUPPORTED",
            "account image capabilities do not contain a usable ratio and resolution",
        )
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    options = candidates[0][3]
    validate_generation_options("image", options, caps)
    return options


def probe_account_generation_health(account: sqlite3.Row) -> Dict[str, Any]:
    options = generation_probe_options(account)
    prompt = str(
        CFG.get("pool", {}).get("generation_probe_prompt")
        or DEFAULT_CONFIG["pool"]["generation_probe_prompt"]
    ).strip()
    result = submit_generation_for_account(
        account,
        "image",
        prompt,
        options,
    )
    assets = result.get("assets") or []
    if not assets:
        raise RuntimeError("generation health probe completed without an image asset")
    mark_account_success(int(account["id"]))
    return {
        "ok": True,
        "model_name": options["model_name"],
        "ratio": options["ratio"],
        "resolution": options["resolution"],
        "asset_count": len(assets),
    }


def validate_registered_account(account_id: int) -> Dict[str, Any]:
    account = account_row_by_id(account_id)
    if account is None:
        raise RuntimeError("registered account could not be loaded for validation")
    session = CLIENT.session_from_account(account)
    detail = CLIENT.fetch_account_point_detail(session, account)
    update_account_balance_snapshot(account_id, detail)
    result = probe_account_generation_health(account)
    now = time.time()
    conn = db_conn()
    conn.execute(
        """
        UPDATE accounts
        SET status='verified',
            cooldown_until=NULL,
            last_error=NULL,
            updated_at=?
        WHERE id=?
        """,
        (now, account_id),
    )
    conn.commit()
    conn.close()
    return result


def refresh_account_session_and_validate(account_id: int) -> Dict[str, Any]:
    account = account_row_by_id(account_id)
    if account is None:
        raise RuntimeError("account could not be loaded for session refresh")
    password = decrypt_secret_value(account["password"], required=True)
    now = time.time()
    conn = db_conn()
    conn.execute(
        "UPDATE accounts SET status='pending_validation', updated_at=? WHERE id=?",
        (now, account_id),
    )
    conn.commit()
    conn.close()
    refreshed_session = CLIENT.login(str(account["email"]), password)
    session = CLIENT.session_from_cookie_dict(refreshed_session.cookies)
    image_info = CLIENT.fetch_image_models(session)
    video_info = {
        "models": CLIENT.fetch_video_models(session),
        "scenes": CLIENT.fetch_video_scenes(session),
    }
    save_account(
        str(account["email"]),
        password,
        refreshed_session,
        model_info=image_info,
        video_info=video_info,
        status="pending_validation",
        source=str(account["source"] or "auto"),
    )
    result = validate_registered_account(account_id)
    mark_account_checkin_at(account_id)
    return result


def reactivate_account(account_id: int) -> Dict[str, Any]:
    """Re-login and re-validate an isolated/invalid account so it can rejoin the pool.

    Gateway ``disabled`` / ``invalid`` are local operational states (usually expired
    session ``200001``), not proof that Oreate banned the mailbox.
    """
    account = account_row_by_id(account_id)
    if account is None:
        raise RuntimeError("account not found")
    status = str(account["status"] or "")
    if status not in {"disabled", "invalid", "pending_validation"}:
        raise RuntimeError(f"account status '{status}' does not need reactivation")
    validation = refresh_account_session_and_validate(account_id)
    row = account_row_by_id(account_id)
    if row is None:
        raise RuntimeError("account disappeared after reactivation")
    return {
        "ok": str(row["status"] or "") in {"verified", "active"},
        "status": str(row["status"] or ""),
        "validation": validation,
        "item": public_account(row),
    }


def account_is_zombie(row: sqlite3.Row) -> bool:
    """Detect accounts that cannot form a usable Oreate session (no recoverable ouss)."""
    status = str(row["status"] or "").strip().lower()
    if status not in {"disabled", "invalid", "pending_validation"}:
        return False
    last_error = str(row["last_error"] or "").lower()
    ouss = decrypt_secret_value(row["ouss"] if "ouss" in row.keys() else "", required=False)
    if not str(ouss or "").strip():
        return True
    markers = (
        "200001",
        "user not login",
        "without ouss",
        "not fully activated",
        "link has expired",
        "600002",
    )
    return any(marker in last_error for marker in markers)


def purge_zombie_accounts() -> Dict[str, Any]:
    """Delete zombie pool accounts that are not usable for scheduling."""
    conn = db_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, email, status, ouss, last_error
            FROM accounts
            WHERE status IN ('disabled', 'invalid', 'pending_validation')
            ORDER BY id ASC
            """
        ).fetchall()
        candidates: List[sqlite3.Row] = [row for row in rows if account_is_zombie(row)]
        if not candidates:
            return {"ok": True, "deleted": 0, "skipped_active": 0, "emails": []}

        deleted_ids: List[int] = []
        deleted_emails: List[str] = []
        skipped_active = 0
        for row in candidates:
            account_id = int(row["id"])
            active = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM tasks
                WHERE account_id=?
                  AND status IN ('queued', 'running', 'submitted', 'hydrating')
                """,
                (account_id,),
            ).fetchone()
            if int((active["count"] if active else 0) or 0) > 0:
                skipped_active += 1
                continue
            conn.execute("UPDATE tasks SET account_id=NULL WHERE account_id=?", (account_id,))
            conn.execute("UPDATE task_attempts SET account_id=NULL WHERE account_id=?", (account_id,))
            conn.execute("UPDATE usage_log SET account_id=NULL WHERE account_id=?", (account_id,))
            conn.execute("DELETE FROM uploaded_media WHERE account_id=?", (account_id,))
            conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            deleted_ids.append(account_id)
            deleted_emails.append(str(row["email"] or ""))
        conn.commit()
        return {
            "ok": True,
            "deleted": len(deleted_ids),
            "skipped_active": skipped_active,
            "ids": deleted_ids,
            "emails": deleted_emails,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def save_and_validate_registered_account(
    email: str,
    password: str,
    session: OreateSession,
    image_info: Dict[str, Any],
    video_info: Dict[str, Any],
    trace: List[Dict[str, Any]],
) -> Tuple[int, str]:
    account_id = save_account(
        email,
        password,
        session,
        model_info=image_info,
        video_info=video_info,
        status="pending_validation",
        source="auto",
    )
    trace.append(
        {
            "step": "login_and_save",
            "account_id": account_id,
            "status": "pending_validation",
        }
    )
    try:
        validation = validate_registered_account(account_id)
    except Exception as exc:
        code = upstream_error_code(exc)
        mark_account_failure(account_id, exc)
        if code == "200001":
            isolate_account_from_pool(
                account_id,
                f"注册后真实生成检测失败：{account_failure_message(exc, code)}",
            )
        status = (
            "validation_deferred"
            if code in GATEWAY_ENVIRONMENT_ERROR_CODES
            else "invalid"
            if code == "200001"
            else "validation_failed"
        )
        trace.append(
            {
                "step": "generation_validation",
                "status": status,
                "error_code": code,
                "error": account_failure_message(exc, code),
            }
        )
        return account_id, status

    trace.append(
        {
            "step": "generation_validation",
            "status": "verified",
            "result": validation,
        }
    )
    mark_account_checkin_at(account_id)
    return account_id, "verified"



def registration_concurrency() -> int:
    raw = CFG.get("pool", {}).get("registration_concurrency", 3)
    value = int_or_default(raw, 3)
    return max(1, min(8, value))


def pool_auto_maintain_interval_seconds() -> float:
    raw = CFG.get("pool", {}).get("maintain_check_interval", 300)
    value = float_or_default(raw, 300.0)
    if not math.isfinite(value):
        return 300.0
    return max(0.0, value)


def pool_auto_maintain_enabled() -> bool:
    return pool_auto_maintain_interval_seconds() > 0


def pool_auto_maintain_max_register() -> int:
    raw = CFG.get("pool", {}).get("auto_maintain_max_register", 5)
    value = int_or_default(raw, 5)
    return max(0, min(50, value))


def pool_auto_checkin_enabled() -> bool:
    value = CFG.get("pool", {}).get("auto_checkin_enabled", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def pool_checkin_timezone_name() -> str:
    value = str(CFG.get("pool", {}).get("checkin_timezone") or "Asia/Shanghai").strip()
    return value or "Asia/Shanghai"


def pool_checkin_tzinfo() -> ZoneInfo:
    name = pool_checkin_timezone_name()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def account_checkin_day_key(ts: float, tz: Optional[ZoneInfo] = None) -> str:
    zone = tz or pool_checkin_tzinfo()
    return datetime.fromtimestamp(float(ts), tz=zone).date().isoformat()


def account_needs_daily_checkin(row: Any, now: Optional[float] = None) -> bool:
    if not pool_auto_checkin_enabled():
        return False
    if isinstance(row, sqlite3.Row):
        status = str(row["status"] or "")
        last_raw = row["last_checkin_at"] if "last_checkin_at" in row.keys() else None
    else:
        status = str((row or {}).get("status") or "")
        last_raw = (row or {}).get("last_checkin_at")
    if status not in {"verified", "active"}:
        return False
    current = time.time() if now is None else float(now)
    if last_raw in (None, ""):
        return True
    try:
        last_ts = float(last_raw)
    except (TypeError, ValueError):
        return True
    tz = pool_checkin_tzinfo()
    return account_checkin_day_key(last_ts, tz) != account_checkin_day_key(current, tz)


def mark_account_checkin_at(account_id: int, when: Optional[float] = None) -> None:
    now = time.time() if when is None else float(when)
    conn = db_conn()
    try:
        conn.execute(
            "UPDATE accounts SET last_checkin_at=?, updated_at=? WHERE id=?",
            (now, now, int(account_id)),
        )
        conn.commit()
    finally:
        conn.close()


def checkin_account_by_login(account_id: int) -> Dict[str, Any]:
    """Login once to trigger upstream daily check-in and refresh local session/balance."""
    account = account_row_by_id(account_id)
    if account is None:
        raise RuntimeError("account not found for daily check-in")
    status = str(account["status"] or "")
    if status not in {"verified", "active"}:
        raise RuntimeError(f"account status '{status}' is not eligible for daily check-in")
    password = decrypt_secret_value(account["password"], required=True)
    email = str(account["email"] or "")
    refreshed_session = CLIENT.login(email, password)
    session = CLIENT.session_from_cookie_dict(refreshed_session.cookies)
    model_info = json_value_from_db(account["model_info_json"])
    video_info = json_value_from_db(account["video_info_json"])
    save_account(
        email,
        password,
        refreshed_session,
        model_info=model_info if isinstance(model_info, dict) else None,
        video_info=video_info if isinstance(video_info, dict) else None,
        status=status,
        source=str(account["source"] or "auto"),
    )
    detail = CLIENT.fetch_account_point_detail(session, account)
    update_account_balance_snapshot(account_id, detail)
    now = time.time()
    mark_account_checkin_at(account_id, now)
    return {
        "ok": True,
        "account_id": int(account_id),
        "email": email,
        "checked_in_at": now,
        "balance": detail,
    }


def register_one_account(
    progress: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    def report(step: str, email: str = "") -> None:
        if progress is not None:
            progress(step, email)

    report("create_mailbox")
    mailbox = MAIL.create_mailbox()
    email = mailbox["address"]
    token = mailbox["token"]
    password = generate_registration_password()
    trace = []
    trace.append({"step": "create_mailbox", "email": email, "domain": mailbox.get("domain"), "mailbox_id": mailbox.get("mailbox_id")})
    report("signup_attempt", email)
    signup = CLIENT.signup_attempt(email, password)
    body = signup.get("response", {})
    status_code = signup.get("status_code")
    upstream_code = body.get("status", {}).get("code") if isinstance(body, dict) else None
    signup_ok = status_code == 200 and upstream_code == 0
    trace.append(
        {
            "step": "signup_attempt",
            "status_code": status_code,
            "response": body,
            "jt_coded": signup.get("jt_coded"),
        }
    )
    artifact = {}
    verification = {}
    account_id = None
    final_status = "signup_failed"
    if not signup_ok:
        # Upstream returns the same 100002 for bad jt and blocked disposable domains.
        # When the helper already supplied a CODED jt, treat it as domain rejection.
        if upstream_code == 100002 and signup.get("jt_coded"):
            final_status = "email_domain_rejected"

    if signup_ok:
        send_email_count = body.get("data", {}).get("sendEmailCount") or body.get("sendEmailCount")
        confirm_status = body.get("data", {}).get("confirmEmailStatus") or body.get("confirmEmailStatus")
        register_status = body.get("data", {}).get("registerStatus") or body.get("registerStatus")
        ticket_id = signup["ticket"]["ticketID"]
        trace.append({"step": "signup_flags", "sendEmailCount": send_email_count, "confirmEmailStatus": confirm_status, "registerStatus": register_status, "ticketID": ticket_id})
        report("email_verification", email)
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
                        report("login_and_save", email)
                        session = CLIENT.login(email, password)
                        sess = CLIENT.session_from_cookie_dict(session.cookies)
                        img = CLIENT.fetch_image_models(sess)
                        vid = {
                            "models": CLIENT.fetch_video_models(sess),
                            "scenes": CLIENT.fetch_video_scenes(sess),
                        }
                        report("generation_validation", email)
                        account_id, final_status = save_and_validate_registered_account(
                            email,
                            password,
                            session,
                            img,
                            vid,
                            trace,
                        )
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
                wait_started_at = time.time()
                send_count = int_or_default(send_email_count, 0)
                total_can_send = int_or_default(
                    body.get("data", {}).get("totalCanSendEmailCount") or body.get("totalCanSendEmailCount"),
                    3,
                )
                # Only resend when upstream still has quota; otherwise we would keep
                # matching stale inbox links from prior burned attempts.
                if send_count < total_can_send:
                    try:
                        resend = CLIENT.resend_confirm_email(email)
                        trace.append({"step": "resend_confirm_email", "response": resend})
                        wait_started_at = time.time()
                    except Exception as resend_error:
                        trace.append({"step": "resend_confirm_email_error", "error": str(resend_error)})
                else:
                    trace.append(
                        {
                            "step": "resend_confirm_email_skipped",
                            "sendEmailCount": send_count,
                            "totalCanSendEmailCount": total_can_send,
                        }
                    )

                excluded_token_ids: List[str] = []
                confirm_code = None
                verify_timeout_sec = max(
                    60,
                    int_or_default((CFG.get("mail") or {}).get("verification_timeout_sec"), 300),
                )
                for verify_attempt in range(2):
                    artifact = MAIL.wait_verification_artifact(
                        email,
                        token,
                        timeout_sec=verify_timeout_sec,
                        not_before=wait_started_at - 60,
                        exclude_token_ids=excluded_token_ids,
                    )
                    trace.append(
                        {
                            "step": "wait_verification_artifact",
                            "attempt": verify_attempt + 1,
                            "artifact": artifact,
                        }
                    )
                    if not (artifact.get("link") or artifact.get("code")):
                        final_status = "verify_timeout"
                        break

                    token_id = ""
                    link = artifact.get("link", "")
                    if link:
                        token_id = extract_token_id_from_link(link)
                        trace.append({"step": "extract_token_from_link", "tokenID": token_id, "link": link})
                        try:
                            vr = requests.get(link, verify=tls_verify_enabled(), timeout=10, allow_redirects=True)
                            trace.append({"step": "visit_verification_link", "status": vr.status_code})
                        except Exception as e:
                            trace.append({"step": "visit_verification_link", "error": str(e)})

                    code = artifact.get("code", "")
                    if not token_id and code:
                        token_id = code

                    if not token_id:
                        verification = CLIENT.check_email_verified(email, ticket_id)
                        trace.append({"step": "check_email_verified", "response": verification})
                        token_id = (
                            verification.get("tokenID")
                            or verification.get("data", {}).get("tokenID")
                            or verification.get("tokenId")
                        )

                    if not token_id:
                        final_status = "verify_pending"
                        break

                    confirm = CLIENT.confirm_email_register(email, token_id, ticket_id, password)
                    verification["confirm"] = confirm
                    trace.append({"step": "emailregisterconfirm", "response": confirm})
                    confirm_code = confirm.get("response", {}).get("status", {}).get("code")
                    if confirm.get("status_code") == 200 and confirm_code == 0:
                        report("login_and_save", email)
                        session = CLIENT.login(email, password)
                        sess = CLIENT.session_from_cookie_dict(session.cookies)
                        img = CLIENT.fetch_image_models(sess)
                        vid = {
                            "models": CLIENT.fetch_video_models(sess),
                            "scenes": CLIENT.fetch_video_scenes(sess),
                        }
                        report("generation_validation", email)
                        account_id, final_status = save_and_validate_registered_account(
                            email,
                            password,
                            session,
                            img,
                            vid,
                            trace,
                        )
                        break

                    # Expired/stale activation link: wait for a newer mail once.
                    if confirm_code in {600002, "600002"} and verify_attempt == 0:
                        excluded_token_ids.append(str(token_id))
                        wait_started_at = time.time()
                        if send_count < total_can_send:
                            try:
                                resend = CLIENT.resend_confirm_email(email)
                                trace.append({"step": "resend_confirm_email_retry", "response": resend})
                                wait_started_at = time.time()
                            except Exception as resend_error:
                                trace.append({"step": "resend_confirm_email_retry_error", "error": str(resend_error)})
                        continue

                    try:
                        report("login_and_save", email)
                        session = CLIENT.login(email, password)
                        sess = CLIENT.session_from_cookie_dict(session.cookies)
                        img = CLIENT.fetch_image_models(sess)
                        vid = {
                            "models": CLIENT.fetch_video_models(sess),
                            "scenes": CLIENT.fetch_video_scenes(sess),
                        }
                        report("generation_validation", email)
                        account_id, final_status = save_and_validate_registered_account(
                            email,
                            password,
                            session,
                            img,
                            vid,
                            trace,
                        )
                        trace.append(
                            {
                                "step": "login_after_confirm_fallback",
                                "account_id": account_id,
                                "confirm_code": confirm_code,
                            }
                        )
                    except Exception as login_error:
                        trace.append(
                            {
                                "step": "login_after_confirm_fallback_error",
                                "error": str(login_error),
                                "confirm_code": confirm_code,
                            }
                        )
                        final_status = "confirm_failed"
                    break
            except Exception as e:
                artifact = {"error": str(e)}
                trace.append({"step": "wait_verification_error", "error": str(e)})
                final_status = "verify_error"

    result = {
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
        "mailbox": {
            "address": email,
            "token": token,
            "provider": mailbox.get("provider") or "",
            "mailbox_id": mailbox.get("mailbox_id") or "",
        },
    }
    record_mail_domain_outcome(str(mailbox.get("domain") or ""), final_status)
    if str(mailbox.get("provider") or "").lower() == "outlook":
        if final_status == "verified":
            MAIL.finish_mailbox(str(token), "used")
        elif final_status == "email_domain_rejected":
            # Domain policy rejection is not mailbox-specific; return to pool.
            MAIL.finish_mailbox(str(token), "available", final_status)
        elif final_status in {
            "signup_failed",
            "confirm_failed",
            "verify_error",
            "verify_timeout",
            "verify_pending",
            "invalid",
            "validation_failed",
        }:
            # These addresses are burned for Oreate; keep them out of the claim pool.
            upstream_msg = ""
            if isinstance(body, dict):
                upstream_msg = str((body.get("status") or {}).get("msg") or "")
            confirm_msg = ""
            if isinstance(verification, dict):
                confirm_body = verification.get("confirm") or {}
                if isinstance(confirm_body, dict):
                    confirm_msg = str(
                        ((confirm_body.get("response") or {}).get("status") or {}).get("msg") or ""
                    )
            detail = " | ".join(
                part for part in (final_status, upstream_msg, confirm_msg, str(artifact.get("error") or "")) if part
            )
            MAIL.finish_mailbox(str(token), "disabled", detail[:1000])
        else:
            MAIL.finish_mailbox(str(token), "error", final_status)
    report("completed", email)
    return result


def auto_register_accounts(
    count: int = 1,
    progress: Optional[Callable[[str, str], None]] = None,
) -> List[Dict[str, Any]]:
    total = max(1, int(count))
    workers = min(registration_concurrency(), total)
    if workers <= 1 or total == 1:
        return [register_one_account(progress=progress) for _ in range(total)]

    progress_lock = threading.Lock()

    def safe_progress(step: str, email: str = "") -> None:
        if progress is None:
            return
        with progress_lock:
            progress(step, email)

    results: List[Optional[Dict[str, Any]]] = [None] * total
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(register_one_account, progress=safe_progress): index
            for index in range(total)
        }
        for future in as_completed(futures):
            index = futures[future]
            results[index] = future.result()
    return [
        item if item is not None else {"ok": False, "status": "registration_error"}
        for item in results
    ]


def registration_job_payload(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    try:
        results = json.loads(item.pop("items_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        results = []
    item["items"] = results if isinstance(results, list) else []
    try:
        events = json.loads(item.pop("events_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        events = []
    if not isinstance(events, list):
        events = []
    # Keep the newest events while bounding payload size for polling clients.
    item["events"] = events[-200:]
    return item


REGISTRATION_JOB_EVENTS_LOCK = threading.Lock()


def append_registration_job_events(job_id: int, new_events: List[Dict[str, Any]]) -> None:
    if not new_events:
        return
    with REGISTRATION_JOB_EVENTS_LOCK:
        conn = db_conn()
        try:
            row = conn.execute(
                "SELECT events_json FROM registration_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                return
            try:
                events = json.loads(row["events_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                events = []
            if not isinstance(events, list):
                events = []
            events.extend(new_events)
            events = events[-200:]
            conn.execute(
                """
                UPDATE registration_jobs
                SET events_json=?, updated_at=?
                WHERE id=?
                """,
                (json.dumps(events, ensure_ascii=False), time.time(), job_id),
            )
            conn.commit()
        finally:
            conn.close()


def append_registration_job_event(
    job_id: int,
    *,
    step: str,
    email: str = "",
    level: str = "info",
    message: str = "",
    status: str = "",
) -> None:
    event = {
        "ts": time.time(),
        "email": str(email or ""),
        "step": str(step or ""),
        "level": str(level or "info"),
        "message": str(message or registration_event_message(step, level=level, status=status)),
        "status": str(status or ""),
    }
    append_registration_job_events(job_id, [event])


def create_registration_job(count: int) -> Dict[str, Any]:
    total = int(count)
    if total < 1 or total > 50:
        raise HTTPException(422, "registration count must be between 1 and 50")
    now = time.time()
    conn = db_conn()
    cursor = conn.execute(
        """
        INSERT INTO registration_jobs(
            status,total,completed,succeeded,failed,current_index,current_step,
            current_email,items_json,events_json,error_message,created_at,updated_at
        )
        VALUES('queued',?,0,0,0,0,'queued','','[]','[]','',?,?)
        """,
        (total, now, now),
    )
    job_id = int(cursor.lastrowid)
    conn.commit()
    row = conn.execute("SELECT * FROM registration_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return registration_job_payload(row)


def get_registration_job(job_id: int) -> Dict[str, Any]:
    conn = db_conn()
    row = conn.execute("SELECT * FROM registration_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "registration job not found")
    return registration_job_payload(row)


def update_registration_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    payload = dict(fields)
    if "items" in payload:
        payload["items_json"] = json.dumps(payload.pop("items"), ensure_ascii=False)
    if "events" in payload:
        payload["events_json"] = json.dumps(payload.pop("events"), ensure_ascii=False)
    payload["updated_at"] = time.time()
    assignments = ", ".join(f"{key}=?" for key in payload)
    conn = db_conn()
    conn.execute(
        f"UPDATE registration_jobs SET {assignments} WHERE id=?",
        [*payload.values(), job_id],
    )
    conn.commit()
    conn.close()


def run_registration_job(job_id: int) -> None:
    job = get_registration_job(job_id)
    if job["status"] not in {"queued", "running"}:
        return
    total = int(job["total"])
    results = list(job.get("items") or [])
    succeeded = int(job.get("succeeded") or 0)
    failed = int(job.get("failed") or 0)
    completed = int(job.get("completed") or 0)
    update_registration_job(
        job_id,
        status="running",
        current_step="starting",
        error_message="",
    )
    append_registration_job_event(
        job_id,
        step="starting",
        level="info",
        message=f"开始注册任务，目标 {total} 个账号，并发 {registration_concurrency()}",
    )

    progress_lock = threading.Lock()
    while completed < total:
        batch_size = min(registration_concurrency(), total - completed)
        current_email = ""

        def progress(step: str, email: str = "") -> None:
            nonlocal current_email
            with progress_lock:
                if email:
                    current_email = email
                update_registration_job(
                    job_id,
                    current_index=min(total, completed + batch_size),
                    current_step=step,
                    current_email=current_email,
                )
                append_registration_job_event(
                    job_id,
                    step=step,
                    email=email or current_email,
                    level="info",
                )

        try:
            registered = auto_register_accounts(batch_size, progress=progress)
            if len(registered) != batch_size:
                raise RuntimeError("registration returned incomplete batch")
        except Exception as exc:
            registered = [
                {
                    "ok": False,
                    "status": "registration_error",
                    "email": current_email,
                    "error": str(exc),
                }
                for _ in range(batch_size)
            ]

        domain_rejected = False
        for raw in registered:
            result = public_registration_result(raw)
            results.append(result)
            ok = bool(result.get("ok") or result.get("status") == "verified")
            if ok:
                succeeded += 1
            else:
                failed += 1
            completed += 1
            email = str(result.get("email") or current_email)
            status = str(result.get("status") or "")
            if status == "email_domain_rejected":
                domain_rejected = True
            append_registration_job_event(
                job_id,
                step="account_done",
                email=email,
                level="success" if ok else "error",
                status=status,
                message=registration_event_message(
                    "account_done",
                    level="success" if ok else "error",
                    status=status,
                ),
            )
            update_registration_job(
                job_id,
                completed=completed,
                succeeded=succeeded,
                failed=failed,
                current_index=completed,
                current_step="completed",
                current_email=email,
                items=results,
            )

        if domain_rejected:
            message = (
                "上游已拒绝当前临时邮箱域名（100002）。"
                "Gmail 等常规邮箱仍可注册，但 YYDS 临时域名目前不可用；请更换可接收验证码的邮箱源后重试。"
            )
            append_registration_job_event(
                job_id,
                step="failed",
                level="error",
                status="email_domain_rejected",
                message=message,
            )
            update_registration_job(
                job_id,
                status="failed",
                current_step="failed",
                error_message=message,
                completed=completed,
                succeeded=succeeded,
                failed=failed,
                current_index=completed,
                items=results,
            )
            return

    final_status = "completed" if failed == 0 else "completed_with_errors"
    append_registration_job_event(
        job_id,
        step="completed",
        level="success" if failed == 0 else "error",
        message=f"注册任务结束：成功 {succeeded}，失败 {failed}",
    )
    update_registration_job(
        job_id,
        status=final_status,
        current_step="completed",
        finished_at=time.time(),
        items=results,
    )


def launch_registration_job(job_id: int) -> None:
    with REGISTRATION_THREADS_LOCK:
        existing = REGISTRATION_THREADS.get(job_id)
        if existing is not None and existing.is_alive():
            return

        def runner() -> None:
            try:
                run_registration_job(job_id)
            except Exception as exc:
                update_registration_job(
                    job_id,
                    status="failed",
                    current_step="failed",
                    error_message=str(exc)[:1000],
                    finished_at=time.time(),
                )
            finally:
                with REGISTRATION_THREADS_LOCK:
                    REGISTRATION_THREADS.pop(job_id, None)

        thread = threading.Thread(
            target=runner,
            name=f"registration-job-{job_id}",
            daemon=True,
        )
        REGISTRATION_THREADS[job_id] = thread
        thread.start()


def recover_interrupted_registration_jobs() -> int:
    now = time.time()
    conn = db_conn()
    cursor = conn.execute(
        """
        UPDATE registration_jobs
        SET status='failed',
            current_step='interrupted',
            error_message='服务重启，注册任务已中断，请重新提交',
            finished_at=?,
            updated_at=?
        WHERE status IN ('queued','running')
        """,
        (now, now),
    )
    conn.commit()
    count = int(cursor.rowcount or 0)
    conn.close()
    return count


POOL_MAINTENANCE_JOB_FIELDS = {
    "status",
    "total_accounts",
    "checked_accounts",
    "checked_in",
    "healthy_before",
    "healthy_after",
    "risk_found",
    "invalid_found",
    "isolated_accounts",
    "clean_risk",
    "supplement",
    "target_healthy",
    "max_register",
    "registration_target",
    "registered",
    "registration_failed",
    "current_account_id",
    "current_email",
    "current_step",
    "items_json",
    "error_message",
    "finished_at",
}


def pool_maintenance_job_payload(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    try:
        results = json.loads(item.pop("items_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        results = []
    item["items"] = results if isinstance(results, list) else []
    item["clean_risk"] = bool(item.get("clean_risk"))
    item["supplement"] = bool(item.get("supplement"))
    return item


def create_pool_maintenance_job(
    *,
    clean_risk: bool = True,
    supplement: bool = True,
    target_healthy: Optional[int] = None,
    max_register: int = 10,
) -> Dict[str, Any]:
    target = int(
        target_healthy
        if target_healthy is not None
        else CFG.get("pool", {}).get("maintain_target", 5)
    )
    register_limit = int(max_register)
    if target < 1 or target > 500:
        raise HTTPException(422, "healthy account target must be between 1 and 500")
    if register_limit < 0 or register_limit > 50:
        raise HTTPException(422, "max register must be between 0 and 50")

    rows = list_accounts()
    summary = account_pool_summary(rows)
    now = time.time()
    conn = db_conn()
    try:
        # Serialize the active-job check and insert. Without the immediate
        # transaction, two near-simultaneous requests can both observe an
        # empty queue and create duplicate maintenance workers.
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            """
            SELECT id FROM pool_maintenance_jobs
            WHERE status IN ('queued','running')
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if active:
            raise HTTPException(409, "已有号池维护任务正在执行")
        cursor = conn.execute(
            """
            INSERT INTO pool_maintenance_jobs(
                status,total_accounts,checked_accounts,checked_in,healthy_before,healthy_after,
                risk_found,invalid_found,isolated_accounts,clean_risk,supplement,
                target_healthy,max_register,registration_target,registered,
                registration_failed,current_account_id,current_email,current_step,
                items_json,error_message,created_at,updated_at
            )
            VALUES('queued',?,0,0,?,?,0,0,0,?,?,?, ?,0,0,0,NULL,'','queued','[]','',?,?)
            """,
            (
                len(rows),
                summary["healthy"],
                summary["healthy"],
                int(bool(clean_risk)),
                int(bool(supplement)),
                target,
                register_limit,
                now,
                now,
            ),
        )
        job_id = int(cursor.lastrowid)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM pool_maintenance_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if row is None:
        raise RuntimeError("created pool maintenance job could not be loaded")
    return pool_maintenance_job_payload(row)


def get_pool_maintenance_job(job_id: int) -> Dict[str, Any]:
    conn = db_conn()
    row = conn.execute(
        "SELECT * FROM pool_maintenance_jobs WHERE id=?",
        (job_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "pool maintenance job not found")
    return pool_maintenance_job_payload(row)


def update_pool_maintenance_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    payload = dict(fields)
    if "items" in payload:
        payload["items_json"] = json.dumps(
            payload.pop("items"),
            ensure_ascii=False,
        )
    unknown = set(payload) - POOL_MAINTENANCE_JOB_FIELDS
    if unknown:
        raise ValueError(
            f"unsupported pool maintenance job fields: {', '.join(sorted(unknown))}"
        )
    payload["updated_at"] = time.time()
    assignments = ", ".join(f"{key}=?" for key in payload)
    conn = db_conn()
    conn.execute(
        f"UPDATE pool_maintenance_jobs SET {assignments} WHERE id=?",
        [*payload.values(), job_id],
    )
    conn.commit()
    conn.close()


def account_row_by_id(account_id: int) -> Optional[sqlite3.Row]:
    conn = db_conn()
    row = conn.execute(
        "SELECT * FROM accounts WHERE id=?",
        (account_id,),
    ).fetchone()
    conn.close()
    return row


def isolate_account_from_pool(account_id: int, reason: str) -> None:
    now = time.time()
    conn = db_conn()
    conn.execute(
        """
        UPDATE accounts
        SET status='disabled',
            cooldown_until=NULL,
            last_error=CASE
                WHEN COALESCE(last_error, '')='' THEN ?
                ELSE last_error
            END,
            updated_at=?
        WHERE id=?
        """,
        (str(reason or "号池维护任务隔离")[:500], now, account_id),
    )
    conn.commit()
    conn.close()


def maintenance_account_item(
    row: sqlite3.Row,
    *,
    category: str,
    action: str,
    error_code: str = "",
) -> Dict[str, Any]:
    return {
        "account_id": int(row["id"]),
        "email": str(row["email"] or ""),
        "category": category,
        "action": action,
        "error_code": str(error_code or ""),
    }


def recoverable_expired_session_account(row: sqlite3.Row) -> bool:
    if str(row["status"] or "") != "disabled":
        return False
    return upstream_error_code(RuntimeError(str(row["last_error"] or ""))) == "200001"


def record_maintenance_account_failure(
    account_id: int,
    exc: Exception,
) -> Tuple[str, str]:
    code = upstream_error_code(exc)
    mark_account_failure(account_id, exc)
    refreshed = account_row_by_id(account_id)
    if code in GATEWAY_ENVIRONMENT_ERROR_CODES:
        return "gateway_risk", code
    if code == "200001" or (
        refreshed is not None
        and str(refreshed["status"] or "") == "invalid"
    ):
        return "invalid", code
    return "check_failed", code


def run_pool_maintenance_job(job_id: int) -> None:
    job = get_pool_maintenance_job(job_id)
    if job["status"] not in {"queued", "running"}:
        return

    clean_risk = bool(job["clean_risk"])
    supplement = bool(job["supplement"])
    target_healthy = int(job["target_healthy"])
    max_register = int(job["max_register"])
    rows = list_accounts()
    items = list(job.get("items") or [])
    checked_accounts = 0
    checked_in = 0
    risk_found = 0
    invalid_found = 0
    isolated_accounts = 0
    check_failures = 0
    gateway_risk_detected = False
    gateway_risk_message = ""

    update_pool_maintenance_job(
        job_id,
        status="running",
        total_accounts=len(rows),
        current_step="scanning",
        error_message="",
    )

    for account_data in rows:
        account_id = int(account_data["id"])
        row = account_row_by_id(account_id)
        if row is None:
            checked_accounts += 1
            continue
        update_pool_maintenance_job(
            job_id,
            current_account_id=account_id,
            current_email=str(row["email"] or ""),
            current_step="checking_account",
        )

        category = ""
        action = ""
        code = ""
        status = str(row["status"] or "")
        recover_expired_session = recoverable_expired_session_account(row)
        if status == "disabled" and not recover_expired_session:
            checked_accounts += 1
            update_pool_maintenance_job(
                job_id,
                checked_accounts=checked_accounts,
                checked_in=checked_in,
                risk_found=risk_found,
                invalid_found=invalid_found,
                isolated_accounts=isolated_accounts,
                items=items,
            )
            continue

        risk_status = account_risk_status(row)
        if recover_expired_session:
            try:
                update_pool_maintenance_job(
                    job_id,
                    current_step="refreshing_session",
                )
                refresh_account_session_and_validate(account_id)
                checked_in += 1
                items.append(
                    maintenance_account_item(
                        account_row_by_id(account_id) or row,
                        category="daily_checkin",
                        action="checked_in",
                    )
                )
            except Exception as exc:
                check_failures += 1
                category, code = record_maintenance_account_failure(
                    account_id,
                    exc,
                )
                if category == "invalid":
                    invalid_found += 1
                action = "cooling" if category == "check_failed" else ""
        elif risk_status == "risk_control":
            category = "risk_control"
            risk_found += 1
        elif status == "invalid":
            category = "invalid"
            invalid_found += 1
        elif status in {"verified", "active", "pending_validation"}:
            try:
                if status in {"verified", "active"} and account_needs_daily_checkin(row):
                    update_pool_maintenance_job(
                        job_id,
                        current_step="daily_checkin",
                    )
                    checkin_account_by_login(account_id)
                    checked_in += 1
                    items.append(
                        maintenance_account_item(
                            account_row_by_id(account_id) or row,
                            category="daily_checkin",
                            action="checked_in",
                        )
                    )
                    row = account_row_by_id(account_id) or row
                update_pool_maintenance_job(
                    job_id,
                    current_step="checking_generation",
                )
                if status == "pending_validation":
                    validate_registered_account(account_id)
                else:
                    session = CLIENT.session_from_account(row)
                    detail = CLIENT.fetch_account_point_detail(session, row)
                    update_account_balance_snapshot(account_id, detail)
                    probe_account_generation_health(row)
            except Exception as exc:
                if upstream_error_code(exc) == "200001":
                    try:
                        update_pool_maintenance_job(
                            job_id,
                            current_step="refreshing_session",
                        )
                        refresh_account_session_and_validate(account_id)
                        checked_in += 1
                        items.append(
                            maintenance_account_item(
                                account_row_by_id(account_id) or row,
                                category="daily_checkin",
                                action="checked_in",
                            )
                        )
                        exc = None
                    except Exception as refresh_exc:
                        exc = refresh_exc
                if exc is not None:
                    check_failures += 1
                    category, code = record_maintenance_account_failure(
                        account_id,
                        exc,
                    )
                    if category == "invalid":
                        invalid_found += 1
                    action = "cooling" if category == "check_failed" else ""

        if category == "gateway_risk":
            action = "aborted"
            gateway_risk_detected = True
            gateway_risk_message = (
                "生成环境触发上游风控（212361），已停止批量检测；"
                "账号未隔离、未冷却，请检查 Chromium 工作节点和出口网络。"
            )
            current = account_row_by_id(account_id) or row
            items.append(
                maintenance_account_item(
                    current,
                    category=category,
                    action=action,
                    error_code=code,
                )
            )
            checked_accounts += 1
            update_pool_maintenance_job(
                job_id,
                checked_accounts=checked_accounts,
                checked_in=checked_in,
                risk_found=risk_found,
                invalid_found=invalid_found,
                isolated_accounts=isolated_accounts,
                current_step="gateway_risk",
                error_message=gateway_risk_message,
                items=items,
            )
            break

        if category in {"risk_control", "invalid"}:
            if clean_risk:
                isolate_account_from_pool(
                    account_id,
                    f"号池维护检测到{code or category}",
                )
                isolated_accounts += 1
                action = "isolated"
            else:
                action = "detected"

        if category:
            current = account_row_by_id(account_id) or row
            items.append(
                maintenance_account_item(
                    current,
                    category=category,
                    action=action,
                    error_code=code,
                )
            )

        checked_accounts += 1
        update_pool_maintenance_job(
            job_id,
            checked_accounts=checked_accounts,
            checked_in=checked_in,
            risk_found=risk_found,
            invalid_found=invalid_found,
            isolated_accounts=isolated_accounts,
            items=items,
        )

    scanned_summary = account_pool_summary(list_accounts())
    registration_target = (
        min(max_register, max(0, target_healthy - scanned_summary["healthy"]))
        if supplement and not gateway_risk_detected
        else 0
    )
    update_pool_maintenance_job(
        job_id,
        healthy_after=scanned_summary["healthy"],
        registration_target=registration_target,
        current_account_id=None,
        current_email="",
        current_step=(
            "gateway_risk"
            if gateway_risk_detected
            else "supplementing"
            if registration_target
            else "finalizing"
        ),
        error_message=gateway_risk_message,
    )

    registered = 0
    registration_failed = 0
    remaining = registration_target
    progress_lock = threading.Lock()
    while remaining > 0:
        batch_size = min(registration_concurrency(), remaining)
        current_email = ""

        def progress(step: str, email: str = "") -> None:
            nonlocal current_email
            with progress_lock:
                if email:
                    current_email = email
                update_pool_maintenance_job(
                    job_id,
                    current_step=f"register_{step}",
                    current_email=current_email,
                )

        try:
            registered_items = auto_register_accounts(batch_size, progress=progress)
            if len(registered_items) != batch_size:
                raise RuntimeError("registration returned incomplete batch")
        except Exception:
            registered_items = [
                {
                    "ok": False,
                    "status": "registration_error",
                    "email": current_email,
                    "error": "账号注册失败",
                }
                for _ in range(batch_size)
            ]

        for raw in registered_items:
            result = public_registration_result(raw)
            ok = bool(result.get("ok") or result.get("status") == "verified")
            if ok:
                registered += 1
            else:
                registration_failed += 1
            items.append(
                {
                    **result,
                    "category": "registration",
                    "action": "registered" if ok else "registration_failed",
                }
            )
            remaining -= 1
            update_pool_maintenance_job(
                job_id,
                registered=registered,
                registration_failed=registration_failed,
                current_step="supplementing",
                current_email=str(result.get("email") or current_email),
                items=items,
            )

    final_summary = account_pool_summary(list_accounts())
    target_unmet = supplement and final_summary["healthy"] < target_healthy
    final_status = (
        "completed_with_errors"
        if gateway_risk_detected or check_failures or registration_failed or target_unmet
        else "completed"
    )
    update_pool_maintenance_job(
        job_id,
        status=final_status,
        healthy_after=final_summary["healthy"],
        checked_accounts=checked_accounts,
        checked_in=checked_in,
        risk_found=risk_found,
        invalid_found=invalid_found,
        isolated_accounts=isolated_accounts,
        registered=registered,
        registration_failed=registration_failed,
        current_account_id=None,
        current_email="",
        current_step="gateway_risk" if gateway_risk_detected else "completed",
        error_message=gateway_risk_message,
        items=items,
        finished_at=time.time(),
    )


def launch_pool_maintenance_job(job_id: int) -> None:
    with POOL_MAINTENANCE_THREADS_LOCK:
        existing = POOL_MAINTENANCE_THREADS.get(job_id)
        if existing is not None and existing.is_alive():
            return

        def runner() -> None:
            try:
                run_pool_maintenance_job(job_id)
            except Exception as exc:
                update_pool_maintenance_job(
                    job_id,
                    status="failed",
                    current_step="failed",
                    error_message=str(exc)[:1000],
                    finished_at=time.time(),
                )
            finally:
                with POOL_MAINTENANCE_THREADS_LOCK:
                    POOL_MAINTENANCE_THREADS.pop(job_id, None)

        thread = threading.Thread(
            target=runner,
            name=f"pool-maintenance-job-{job_id}",
            daemon=True,
        )
        POOL_MAINTENANCE_THREADS[job_id] = thread
        thread.start()


def recover_interrupted_pool_maintenance_jobs() -> int:
    now = time.time()
    conn = db_conn()
    try:
        table_exists = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='pool_maintenance_jobs'
            """
        ).fetchone()
        if not table_exists:
            return 0
        cursor = conn.execute(
            """
            UPDATE pool_maintenance_jobs
            SET status='failed',
                current_step='interrupted',
                error_message='服务重启，号池维护任务已中断，请重新提交',
                finished_at=?,
                updated_at=?
            WHERE status IN ('queued','running')
            """,
            (now, now),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def active_pool_maintenance_job_id() -> Optional[int]:
    conn = db_conn()
    row = conn.execute(
        """
        SELECT id FROM pool_maintenance_jobs
        WHERE status IN ('queued','running')
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return int(row["id"])


def maybe_launch_scheduled_pool_maintenance() -> Optional[int]:
    """Create and launch one maintenance job when auto-maintain is enabled and idle."""
    if not pool_auto_maintain_enabled():
        return None
    if active_pool_maintenance_job_id() is not None:
        return None
    try:
        job = create_pool_maintenance_job(
            clean_risk=True,
            supplement=True,
            target_healthy=None,
            max_register=pool_auto_maintain_max_register(),
        )
    except HTTPException as exc:
        if int(getattr(exc, "status_code", 0) or 0) == 409:
            return None
        raise
    launch_pool_maintenance_job(int(job["id"]))
    emit_log("info", f"已调度号池自动维护任务 #{job['id']}")
    return int(job["id"])


def pool_maintenance_scheduler_loop() -> None:
    while not POOL_MAINTENANCE_SCHEDULER_STOP.is_set():
        interval = pool_auto_maintain_interval_seconds()
        if interval <= 0:
            POOL_MAINTENANCE_SCHEDULER_WAKE.wait(60.0)
            POOL_MAINTENANCE_SCHEDULER_WAKE.clear()
            continue
        POOL_MAINTENANCE_SCHEDULER_WAKE.wait(interval)
        POOL_MAINTENANCE_SCHEDULER_WAKE.clear()
        if POOL_MAINTENANCE_SCHEDULER_STOP.is_set():
            break
        try:
            maybe_launch_scheduled_pool_maintenance()
        except Exception as exc:
            emit_log(
                "warning",
                f"号池自动维护调度失败：{type(exc).__name__}: {exc}",
            )


def ensure_pool_maintenance_scheduler_started() -> None:
    global POOL_MAINTENANCE_SCHEDULER_THREAD
    with POOL_MAINTENANCE_SCHEDULER_LOCK:
        if not pool_auto_maintain_enabled():
            return
        existing = POOL_MAINTENANCE_SCHEDULER_THREAD
        if existing is not None and existing.is_alive():
            return
        POOL_MAINTENANCE_SCHEDULER_STOP.clear()
        POOL_MAINTENANCE_SCHEDULER_THREAD = threading.Thread(
            target=pool_maintenance_scheduler_loop,
            name="pool-maintenance-scheduler",
            daemon=True,
        )
        POOL_MAINTENANCE_SCHEDULER_THREAD.start()


def stop_pool_maintenance_scheduler(timeout: float) -> bool:
    global POOL_MAINTENANCE_SCHEDULER_THREAD
    POOL_MAINTENANCE_SCHEDULER_STOP.set()
    POOL_MAINTENANCE_SCHEDULER_WAKE.set()
    with POOL_MAINTENANCE_SCHEDULER_LOCK:
        worker = POOL_MAINTENANCE_SCHEDULER_THREAD
        if worker is None or not worker.is_alive():
            POOL_MAINTENANCE_SCHEDULER_THREAD = None
            return True
        join_timeout = float_or_default(timeout, 0.0)
        if not math.isfinite(join_timeout):
            join_timeout = 0.0
        worker.join(timeout=max(0.0, join_timeout))
        if worker.is_alive():
            return False
        POOL_MAINTENANCE_SCHEDULER_THREAD = None
        return True



class OpenAIPathCORSMiddleware:
    """Apply browser CORS only to the public /v1 API surface."""

    def __init__(self, app: Any, **cors_options: Any):
        self.app = app
        self.cors_app = CORSMiddleware(app, **cors_options)

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and str(scope.get("path") or "").startswith("/v1"):
            await self.cors_app(scope, receive, send)
            return
        await self.app(scope, receive, send)


def normalize_cors_allowed_origins(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    origins: List[str] = []
    for raw_origin in value:
        if not isinstance(raw_origin, str):
            continue
        candidate = raw_origin.strip()
        parsed = urlparse(candidate)
        try:
            parsed_port = parsed.port
        except ValueError:
            continue
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            continue
        host = parsed.hostname.lower()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        normalized = f"{parsed.scheme.lower()}://{host}"
        if parsed_port is not None:
            normalized = f"{normalized}:{parsed_port}"
        if normalized not in origins:
            origins.append(normalized)
    return origins


app = FastAPI(title="OreateAI Gateway")
app.add_middleware(
    OpenAIPathCORSMiddleware,
    allow_origins=normalize_cors_allowed_origins(openai_compat_cfg().get("cors_allowed_origins")),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-Id"],
    expose_headers=["X-Request-Id", "X-Watermark-Removed"],
    max_age=600,
)


class GatewayAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Optional[Dict[str, Any]] = None, request_id: str = ""):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.request_id = request_id


def is_openai_compat_path(path: str) -> bool:
    return path == "/v1/models" or path.startswith("/v1/models/") or path.startswith("/v1/images/") or path == "/v1/videos" or path.startswith("/v1/videos/")


def openai_error_response(
    status_code: int,
    message: str,
    *,
    code: Optional[str] = None,
    param: Optional[str] = None,
) -> JSONResponse:
    if status_code == 401:
        error_type = "authentication_error"
    elif status_code == 429:
        error_type = "rate_limit_error"
    elif status_code >= 500:
        error_type = "api_error"
    else:
        error_type = "invalid_request_error"
    return JSONResponse(
        status_code=status_code,
        content=openai_error_payload(
            message,
            error_type=error_type,
            param=param,
            code=str(code).lower() if code else None,
        ),
    )


@app.middleware("http")
async def admin_audit_middleware(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception:
        admin_username = getattr(request.state, "admin_username", None)
        if admin_username and request.url.path.startswith("/api/"):
            try:
                write_admin_audit(
                    f"{request.method} {request.url.path}",
                    admin_username,
                    request,
                    status_code=500,
                )
            except Exception:
                pass
        raise
    admin_username = getattr(request.state, "admin_username", None)
    if admin_username and request.url.path.startswith("/api/"):
        try:
            write_admin_audit(
                f"{request.method} {request.url.path}",
                admin_username,
                request,
                status_code=response.status_code,
            )
        except Exception:
            pass
    return response


def gateway_request_id(request: Optional[Request] = None) -> str:
    max_length = max(8, int_or_default(gateway_cfg().get("request_id_max_length"), 128))
    if request is not None:
        incoming = str(request.headers.get("X-Request-ID") or "").strip()
        if incoming and len(incoming) <= max_length:
            return incoming
    random_length = max_length - len("req_")
    return "req_" + secrets.token_hex((random_length + 1) // 2)[:random_length]


def gateway_error_response(request_id: str, status_code: int, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            },
            "request_id": request_id,
        },
    )


@app.exception_handler(GatewayAPIError)
def handle_gateway_api_error(request: Request, exc: GatewayAPIError):
    if is_openai_compat_path(request.url.path):
        param = exc.details.get("field") if isinstance(exc.details, dict) else None
        return openai_error_response(
            exc.status_code,
            exc.message,
            code=exc.code,
            param=str(param) if param not in (None, "") else None,
        )
    return gateway_error_response(
        exc.request_id or gateway_request_id(request),
        exc.status_code,
        exc.code,
        exc.message,
        exc.details,
    )


@app.exception_handler(OpenAICompatError)
def handle_openai_compat_error(request: Request, exc: OpenAICompatError):
    return openai_error_response(
        exc.status_code,
        exc.message,
        code=exc.code,
        param=exc.param,
    )


@app.exception_handler(HTTPException)
def handle_http_exception(request: Request, exc: HTTPException):
    if is_openai_compat_path(request.url.path):
        return openai_error_response(exc.status_code, str(exc.detail))
    if request.url.path.startswith("/v1/"):
        code_by_status = {
            400: "BAD_REQUEST",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMITED",
            503: "SERVICE_UNAVAILABLE",
        }
        return gateway_error_response(
            gateway_request_id(request),
            exc.status_code,
            code_by_status.get(exc.status_code, "GATEWAY_ERROR"),
            str(exc.detail),
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
def handle_request_validation_error(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    first = errors[0] if errors else {}
    location = first.get("loc") if isinstance(first, dict) else ()
    field = None
    if isinstance(location, (list, tuple)):
        for item in reversed(location):
            if item not in {"body", "query", "path", "header"}:
                field = str(item)
                break
    message = str(first.get("msg") or "request validation failed") if isinstance(first, dict) else "request validation failed"
    if is_openai_compat_path(request.url.path):
        return openai_error_response(
            422,
            message,
            code="validation_error",
            param=field,
        )
    if request.url.path.startswith("/v1/"):
        return gateway_error_response(
            gateway_request_id(request),
            422,
            "VALIDATION_ERROR",
            message,
            {"field": field, "errors": errors},
        )
    return JSONResponse(status_code=422, content={"detail": errors})

# === API Key Auth ===
security = HTTPBearer(auto_error=False)

API_KEY_LAST_USED_TOUCH_INTERVAL_SECONDS = 60.0


def touch_api_key_last_used(conn: sqlite3.Connection, api_key_id: int, now: float) -> None:
    """Best-effort activity timestamp update that must never delay authentication."""
    try:
        conn.execute("PRAGMA busy_timeout=0")
        conn.execute(
            """
            UPDATE api_keys
            SET last_used_at=?
            WHERE id=? AND (last_used_at IS NULL OR last_used_at<?)
            """,
            (now, api_key_id, now - API_KEY_LAST_USED_TOUCH_INTERVAL_SECONDS),
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        conn.rollback()
        if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
            raise


def get_api_key(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Optional[int]:
    if credentials is None:
        return None
    conn = db_conn()
    row = conn.execute(
        "SELECT id, enabled, deleted_at, expires_at, last_used_at FROM api_keys WHERE key=?",
        (credentials.credentials,),
    ).fetchone()
    try:
        now = time.time()
        if row and row["enabled"] and row["deleted_at"] is None and (
            row["expires_at"] is None or float_or_default(row["expires_at"], 0) > now
        ):
            if now - float_or_default(row["last_used_at"], 0) >= API_KEY_LAST_USED_TOUCH_INTERVAL_SECONDS:
                touch_api_key_last_used(conn, row["id"], now)
            return row["id"]
        return None
    finally:
        conn.close()

def require_api_key(request: Request, api_key_id: Optional[int] = Depends(get_api_key)):
    if api_key_id is None:
        raise GatewayAPIError(
            401,
            "UNAUTHORIZED",
            "valid API key required (header: Authorization: Bearer <key>)",
            request_id=gateway_request_id(request),
        )
    return api_key_id

def insert_usage_log(
    conn: sqlite3.Connection,
    api_key_id: int,
    kind: str,
    account_id: int,
    prompt: str,
    status: str,
    summary: str = "",
    task_id: Optional[int] = None,
    request_id: str = "",
    idempotency_key: str = "",
    model_name: str = "",
    resolution: str = "",
    ratio: str = "",
    duration: Optional[int] = None,
    scene_id: str = "",
    estimated_point_cost: Optional[int] = None,
    error_code: str = "",
    status_code: Optional[int] = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO usage_log (
            api_key_id, task_id, kind, account_id, prompt, status, response_summary,
            request_id, idempotency_key, model_name, resolution, ratio, duration, scene_id,
            estimated_point_cost, error_code, status_code, created_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            api_key_id,
            task_id,
            kind,
            account_id,
            prompt[:200],
            status,
            summary[:200],
            request_id,
            idempotency_key,
            model_name,
            resolution,
            ratio,
            duration,
            scene_id,
            estimated_point_cost,
            error_code,
            status_code,
            time.time(),
        ),
    )
    return int(cursor.lastrowid)


def log_usage(
    api_key_id: int,
    kind: str,
    account_id: int,
    prompt: str,
    status: str,
    summary: str = "",
    task_id: Optional[int] = None,
    request_id: str = "",
    idempotency_key: str = "",
    model_name: str = "",
    resolution: str = "",
    ratio: str = "",
    duration: Optional[int] = None,
    scene_id: str = "",
    estimated_point_cost: Optional[int] = None,
    error_code: str = "",
    status_code: Optional[int] = None,
):
    conn = db_conn()
    try:
        usage_id = insert_usage_log(
            conn,
            api_key_id,
            kind,
            account_id,
            prompt,
            status,
            summary,
            task_id,
            request_id,
            idempotency_key,
            model_name,
            resolution,
            ratio,
            duration,
            scene_id,
            estimated_point_cost,
            error_code,
            status_code,
        )
        conn.commit()
        return usage_id
    finally:
        conn.close()


def save_uploaded_media_record(api_key_id: int, account_id: int, attachment: Dict[str, Any]) -> None:
    object_path = upload_object_value(attachment)
    if not object_path:
        raise RuntimeError("uploaded media record requires an object path")
    payload = encode_json_value(attachment)
    now = time.time()
    conn = db_conn()
    try:
        conn.execute(
            """
            INSERT INTO uploaded_media(api_key_id, account_id, object_path, attachment_json, status, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(api_key_id, object_path)
            DO UPDATE SET account_id=excluded.account_id, attachment_json=excluded.attachment_json,
                          status=excluded.status, updated_at=excluded.updated_at
            """,
            (api_key_id, account_id, object_path, payload, "completed", now, now),
        )
        conn.commit()
    finally:
        conn.close()


def upload_media_kind(attachment: Dict[str, Any], object_path: str = "") -> str:
    content_type = str(
        attachment.get("contentType")
        or attachment.get("content_type")
        or attachment.get("mimeType")
        or attachment.get("mime_type")
        or ""
    ).strip().lower()
    ext = normalized_file_extension(
        attachment.get("fileExt")
        or attachment.get("file_ext")
        or Path(urlparse(str(object_path or "")).path).suffix
    )
    if content_type.startswith("image/") or ext in IMAGE_UPLOAD_EXTENSIONS:
        return "image"
    if content_type.startswith("video/") or ext in VIDEO_UPLOAD_EXTENSIONS:
        return "video"
    return "unknown"


def sanitize_upload_attachment(value: Any) -> Any:
    sensitive_keys = {"sessionkey", "session_key", "cookies", "cookie", "ouid", "ouss", "authorization", "token", "access_token"}
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, nested in value.items():
            lowered = str(key).strip().lower()
            if lowered in sensitive_keys or lowered.endswith("cookie") or lowered.endswith("cookies"):
                continue
            sanitized[key] = sanitize_upload_attachment(nested)
        return sanitized
    if isinstance(value, list):
        return [sanitize_upload_attachment(item) for item in value]
    return value


def upload_kind_filter_clause(kind: str) -> tuple[str, List[Any]]:
    if kind not in MEDIA_ADMIN_KINDS:
        raise HTTPException(422, "kind is not supported")
    if kind == "image":
        content_prefix = "image/"
        extensions = IMAGE_UPLOAD_EXTENSIONS
    else:
        content_prefix = "video/"
        extensions = VIDEO_UPLOAD_EXTENSIONS
    clauses = [
        "LOWER(um.attachment_json) LIKE ?",
        "LOWER(um.attachment_json) LIKE ?",
    ]
    params: List[Any] = [
        f'%"contenttype": "{content_prefix}%',
        f'%"contenttype":"{content_prefix}%',
    ]
    for ext in sorted(extensions):
        clauses.extend(
            [
                "LOWER(um.object_path) LIKE ?",
                "LOWER(um.attachment_json) LIKE ?",
                "LOWER(um.attachment_json) LIKE ?",
            ]
        )
        params.extend(
            [
                f"%.{ext}%",
                f'%"fileext": "{ext}"%',
                f'%"fileext":"{ext}"%',
            ]
        )
    return f"({' OR '.join(clauses)})", params


def public_uploaded_media(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    attachment = json_value_from_db(item.pop("attachment_json", None)) or {}
    object_path = str(item.get("object_path") or upload_object_value(attachment) or "")
    sanitized_attachment = sanitize_upload_attachment(attachment)
    item["object_path"] = object_path
    item["attachment"] = sanitized_attachment
    item["kind"] = upload_media_kind(attachment, object_path)
    item["account_email"] = item.get("account_email") or ""
    item["api_key_name"] = item.get("api_key_name") or ""
    item["client_name"] = item.get("client_name") or ""
    item["related_task_count"] = int_or_default(item.get("related_task_count"), 0)
    item["file_name"] = sanitized_attachment.get("fileName") or sanitized_attachment.get("doc_title") or Path(urlparse(object_path).path).name
    item["content_type"] = sanitized_attachment.get("contentType") or sanitized_attachment.get("doc_type") or ""
    item["size"] = sanitized_attachment.get("originSize") or sanitized_attachment.get("fileSize") or sanitized_attachment.get("size") or 0
    return item


def resolve_uploaded_input_reference(
    api_key_id: int,
    input_reference: Any,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    attachments = upload_attachment_list(input_reference)
    if not attachments:
        raise OpenAICompatError(
            "input_reference must contain at least one uploaded attachment",
            param="input_reference",
            code="invalid_input_reference",
        )
    resolved: List[Dict[str, Any]] = []
    account_ids = set()
    conn = db_conn()
    try:
        for item in attachments:
            object_path = upload_object_value(item)
            if not object_path:
                raise OpenAICompatError(
                    "input_reference items must contain an uploaded object path",
                    param="input_reference",
                    code="invalid_input_reference",
                )
            row = conn.execute(
                "SELECT account_id, attachment_json, status FROM uploaded_media WHERE api_key_id=? AND object_path=?",
                (api_key_id, object_path),
            ).fetchone()
            if not row or str(row["status"] or "") != "completed":
                raise OpenAICompatError(
                    "input_reference must reference a completed upload owned by this API key",
                    param="input_reference",
                    code="invalid_input_reference",
                )
            account_ids.add(int(row["account_id"]))
            resolved.append(json_value_from_db(row["attachment_json"]) or {})
    finally:
        conn.close()
    if len(account_ids) != 1:
        raise OpenAICompatError(
            "input_reference attachments must originate from the same uploaded-media account",
            param="input_reference",
            code="invalid_input_reference",
        )
    reference_images, reference_videos = split_input_reference_attachments(resolved)
    return reference_images, reference_videos, next(iter(account_ids))


def update_usage_log(usage_id: int, **fields: Any) -> None:
    if not fields:
        return
    conn = db_conn()
    try:
        payload = dict(fields)
        for key in ("response_summary",):
            if key in payload and payload[key] is not None:
                payload[key] = str(payload[key])[:200]
        assignments = ", ".join(f"{key}=?" for key in payload)
        values = list(payload.values()) + [usage_id]
        conn.execute(f"UPDATE usage_log SET {assignments} WHERE id=?", values)
        conn.commit()
    finally:
        conn.close()


def admin_session_ttl_seconds() -> float:
    ttl_hours = float_or_default(CFG.get("server", {}).get("admin_session_ttl_hours"), 12.0)
    return max(0.0, ttl_hours * 3600.0)


def admin_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _admin_session_row(conn: sqlite3.Connection, token: str) -> Optional[sqlite3.Row]:
    token_hash = admin_token_hash(token)
    return conn.execute("SELECT * FROM admin_sessions WHERE token_hash=?", (token_hash,)).fetchone()


def create_admin_session(token: str, username: str, request: Optional[Request] = None) -> None:
    now = time.time()
    expires_at = now + admin_session_ttl_seconds()
    conn = db_conn()
    conn.execute(
        """
        INSERT INTO admin_sessions(
            token_hash, username, created_at, last_used_at, expires_at, revoked_at, revoked_reason, remote_addr, user_agent
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            admin_token_hash(token),
            username,
            now,
            now,
            expires_at,
            None,
            None,
            request.client.host if request and request.client else "",
            request.headers.get("user-agent", "") if request else "",
        ),
    )
    conn.commit()
    conn.close()
    ADMIN_TOKENS[token] = username


def revoke_admin_session(token: str, reason: str = "logout") -> None:
    now = time.time()
    conn = db_conn()
    conn.execute(
        "UPDATE admin_sessions SET revoked_at=?, revoked_reason=? WHERE token_hash=? AND revoked_at IS NULL",
        (now, reason, admin_token_hash(token)),
    )
    conn.commit()
    conn.close()
    ADMIN_TOKENS.pop(token, None)


def revoke_all_admin_sessions(reason: str = "credentials_updated") -> None:
    now = time.time()
    conn = db_conn()
    conn.execute(
        "UPDATE admin_sessions SET revoked_at=COALESCE(revoked_at, ?), revoked_reason=COALESCE(revoked_reason, ?) WHERE revoked_at IS NULL",
        (now, reason),
    )
    conn.commit()
    conn.close()
    ADMIN_TOKENS.clear()


def write_admin_audit(
    action: str,
    admin_username: str,
    request: Optional[Request] = None,
    *,
    status_code: Optional[int] = None,
    entity_type: str = "",
    entity_id: Optional[Any] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Any = details or {}
    if payload:
        payload = redact_nested_fields(
            json.loads(json.dumps(payload, ensure_ascii=False)),
            {"password", "current_password", "new_password", "confirm_password", "token", "token_id", "tokenID", "tokenId", "api_key", "secret", "cookie", "cookies"},
        )
    try:
        details_json = json.dumps(payload, ensure_ascii=False)
    except TypeError:
        details_json = json.dumps({"value": str(payload)}, ensure_ascii=False)
    conn = db_conn()
    conn.execute(
        """
        INSERT INTO admin_audit_log(
            admin_username, action, method, path, status_code, entity_type, entity_id,
            details_json, remote_addr, user_agent, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            admin_username,
            action,
            request.method if request else "",
            request.url.path if request else "",
            status_code,
            entity_type,
            None if entity_id is None else str(entity_id),
            details_json,
            request.client.host if request and request.client else "",
            request.headers.get("user-agent", "") if request else "",
            time.time(),
        ),
    )
    conn.commit()
    conn.close()


def list_admin_audit_rows(limit: int = 100) -> List[sqlite3.Row]:
    conn = db_conn()
    rows = conn.execute(
        "SELECT * FROM admin_audit_log ORDER BY id DESC LIMIT ?",
        (max(1, min(int(limit), 500)),),
    ).fetchall()
    conn.close()
    return rows


def build_backup_zip_bytes() -> bytes:
    temp_db_handle = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db_handle.close()
    temp_db_path = Path(temp_db_handle.name)
    source = db_conn()
    dest = sqlite3.connect(temp_db_path)
    try:
        source.backup(dest)
        dest.commit()
    finally:
        dest.close()
        source.close()
    manifest = {
        "created_at": time.time(),
        "db_filename": "accounts.db",
        "config_filename": "config.json",
        "server_host": CFG.get("server", {}).get("host", ""),
        "server_port": CFG.get("server", {}).get("port", 0),
    }
    try:
        db_bytes = temp_db_path.read_bytes()
    finally:
        try:
            temp_db_path.unlink()
        except FileNotFoundError:
            pass
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("accounts.db", db_bytes)
        archive.writestr("config.json", json.dumps(CFG, ensure_ascii=False, indent=2))
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return buffer.getvalue()


def restore_backup_zip_bytes(payload: bytes) -> Dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        if "accounts.db" not in names or "config.json" not in names:
            raise HTTPException(400, "backup archive missing required files")
        temp_dir = Path(tempfile.mkdtemp(prefix="oreate-restore-"))
        try:
            db_path = temp_dir / "accounts.db"
            config_path = temp_dir / "config.json"
            db_path.write_bytes(archive.read("accounts.db"))
            config_path.write_bytes(archive.read("config.json"))
            restored_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(restored_cfg, dict):
                raise HTTPException(400, "backup config is invalid")
            global CFG
            with CONFIG_LOCK:
                restored_candidate = deep_merge(DEFAULT_CONFIG, restored_cfg)
                save_config(restored_candidate)
                CFG = restored_candidate
            if DB_PATH.exists():
                DB_PATH.unlink()
            shutil.copyfile(db_path, DB_PATH)
            init_db()
            revoke_all_admin_sessions("backup_restored")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    return {"ok": True, "restored": True, "requires_relogin": True, "message": "恢复完成，请重新登录。"}


# === API Key Management (admin only) ===
def require_admin(request: Request):
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else auth
    if not token:
        raise HTTPException(401, "admin login required")
    conn = db_conn()
    row = _admin_session_row(conn, token)
    now = time.time()
    if row and (row["revoked_at"] is not None or float(row["expires_at"] or 0) <= now):
        if row["revoked_at"] is None:
            conn.execute(
                "UPDATE admin_sessions SET revoked_at=?, revoked_reason=? WHERE id=?",
                (now, "expired", row["id"]),
            )
            conn.commit()
        conn.close()
        ADMIN_TOKENS.pop(token, None)
        raise HTTPException(401, "admin login required")
    if not row:
        conn.close()
        raise HTTPException(401, "admin login required")
    conn.execute("UPDATE admin_sessions SET last_used_at=? WHERE id=?", (now, row["id"]))
    conn.commit()
    conn.close()
    request.state.admin_username = row["username"]
    request.state.admin_session_token = token
    ADMIN_TOKENS[token] = row["username"]
    return row["username"]


def update_policy_override(section: str, key: str, body: Dict[str, Any], allowed_keys: Iterable[str]) -> Dict[str, Any]:
    global CFG
    patch = {name: body[name] for name in allowed_keys if name in body}
    with CONFIG_LOCK:
        candidate = json.loads(json.dumps(CFG))
        gateway_cfg_section = candidate.setdefault("gateway", {})
        policies = gateway_cfg_section.setdefault(section, {})
        current = policies.get(key, {})
        if not isinstance(current, dict):
            current = {}
        merged = resolve_policy(current, patch)
        policies[key] = merged
        save_config(candidate)
        CFG = candidate
        return merged


def get_client_record(client_id: int) -> sqlite3.Row:
    conn = db_conn()
    row = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "client not found")
    return row


def api_key_scope_payload_values(payload: Dict[str, Any], current: Optional[sqlite3.Row] = None) -> Dict[str, Any]:
    current_data = dict(current) if current is not None else {}

    def list_value(field: str) -> Optional[str]:
        if field not in payload:
            return current_data.get(field)
        if field == "allowed_durations":
            return encode_scope_values(normalize_int_scope_values(payload.get(field), field))
        return encode_scope_values(normalize_string_scope_values(payload.get(field), field))

    def bool_value(field: str) -> int:
        if field not in payload:
            raw = current_data.get(field)
            default = API_KEY_SCOPE_BOOL_DEFAULTS[field]
            return int(bool(raw)) if raw is not None else int(default)
        return int(parse_boolean_flag(payload.get(field), field))

    return {
        "allowed_kinds": list_value("allowed_kinds"),
        "allowed_models": list_value("allowed_models"),
        "allowed_scenes": list_value("allowed_scenes"),
        "allowed_resolutions": list_value("allowed_resolutions"),
        "allowed_durations": list_value("allowed_durations"),
        "allow_uploads": bool_value("allow_uploads"),
        "allow_experimental": bool_value("allow_experimental"),
    }


def optional_timestamp_payload_value(payload: Dict[str, Any], field: str, current: Optional[sqlite3.Row] = None) -> Optional[float]:
    if field not in payload:
        return current[field] if current is not None else None
    value = payload.get(field)
    if value in (None, ""):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field} must be a unix timestamp")
    if timestamp <= 0:
        raise HTTPException(400, f"{field} must be a positive unix timestamp")
    return timestamp


def optional_api_key_id_payload_value(payload: Dict[str, Any], field: str, current: Optional[sqlite3.Row] = None) -> Optional[int]:
    if field not in payload:
        return current[field] if current is not None else None
    value = payload.get(field)
    if value in (None, ""):
        return None
    try:
        key_id = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field} must be an integer")
    if key_id <= 0:
        raise HTTPException(400, f"{field} must be positive")
    conn = db_conn()
    row = conn.execute("SELECT id FROM api_keys WHERE id=?", (key_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(400, f"{field} does not reference an existing API key")
    return key_id


def optional_text_payload_value(payload: Dict[str, Any], field: str, current: Optional[sqlite3.Row] = None) -> str:
    if field not in payload:
        return str(current[field] or "") if current is not None else ""
    return str(payload.get(field) or "").strip()


def optional_non_negative_integer_payload_value(
    payload: Dict[str, Any],
    field: str,
    current: Optional[sqlite3.Row] = None,
) -> Optional[int]:
    if field not in payload:
        return current[field] if current is not None else None
    value = payload.get(field)
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise HTTPException(400, f"{field} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field} must be a non-negative integer")
    if str(value).strip() != str(parsed) or parsed < 0:
        raise HTTPException(400, f"{field} must be a non-negative integer")
    return parsed


@app.get("/api/admin/apikeys")
def list_api_keys(_=Depends(require_admin)):
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT
            api_keys.*,
            clients.name AS client_name,
            clients.contact AS client_contact,
            clients.status AS client_status,
            COALESCE(today.request_count, 0) AS today_request_count,
            COALESCE(today.point_usage, 0) AS today_point_usage
        FROM api_keys
        LEFT JOIN clients ON clients.id=api_keys.client_id
        LEFT JOIN (
            SELECT
                api_key_id,
                COUNT(*) AS request_count,
                COALESCE(SUM(estimated_point_cost), 0) AS point_usage
            FROM usage_log
            WHERE created_at>=?
            GROUP BY api_key_id
        ) AS today ON today.api_key_id=api_keys.id
        ORDER BY api_keys.id DESC
        """,
        (day_start_timestamp(time.time()),),
    ).fetchall()
    conn.close()
    return {"items": [public_api_key(r) for r in rows]}


@app.get("/api/admin/apikeys/{key_id}/secret")
def reveal_api_key_secret(key_id: int, _=Depends(require_admin)):
    conn = db_conn()
    row = conn.execute("SELECT id, key FROM api_keys WHERE id=?", (key_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "api key not found")
    return {"id": row["id"], "key": row["key"]}


@app.get("/api/admin/clients")
def list_clients(_=Depends(require_admin)):
    conn = db_conn()
    rows = conn.execute("SELECT * FROM clients ORDER BY id DESC").fetchall()
    conn.close()
    return {"items": [public_client(r) for r in rows]}


@app.post("/api/admin/clients")
def create_client(body: Dict[str, Any] = None, _=Depends(require_admin)):
    payload = body or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "client name is required")
    contact = str(payload.get("contact") or "").strip()
    status = str(payload.get("status") or "active").strip() or "active"
    now = time.time()
    conn = db_conn()
    conn.execute(
        "INSERT INTO clients(name, contact, status, created_at) VALUES(?,?,?,?)",
        (name, contact, status, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM clients WHERE id=last_insert_rowid()").fetchone()
    conn.close()
    return {"ok": True, "item": public_client(row)}


@app.patch("/api/models/{model_name}/policy")
def patch_model_policy(model_name: str, body: Dict[str, Any], _=Depends(require_admin)):
    policy = update_policy_override(
        "model_policies",
        model_name,
        body or {},
        {"enabled", "experimental", "verification_status", "risk_level"},
    )
    return {"ok": True, "model_name": model_name, "policy": policy}


@app.patch("/api/video-scenes/{scene_id}/policy")
def patch_video_scene_policy(scene_id: str, body: Dict[str, Any], _=Depends(require_admin)):
    policy = update_policy_override(
        "scene_policies",
        scene_id,
        body or {},
        {"enabled", "experimental", "verification_status", "risk_level"},
    )
    return {"ok": True, "scene_id": scene_id, "policy": policy}


@app.post("/api/admin/apikeys")
def create_api_key(body: Dict[str, Any] = None, _=Depends(require_admin)):
    payload = body or {}
    name = str(payload.get("name") or "").strip()
    client_id = payload.get("client_id")
    client_id_value = None
    if client_id not in (None, ""):
        try:
            client_id_value = int(client_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "client_id must be an integer")
        get_client_record(client_id_value)
    key = "oreate_" + secrets.token_hex(24)
    scopes = api_key_scope_payload_values(payload)
    expires_at = optional_timestamp_payload_value(payload, "expires_at")
    rotated_from_id = optional_api_key_id_payload_value(payload, "rotated_from_id")
    rotation_note = optional_text_payload_value(payload, "rotation_note")
    disabled_reason = optional_text_payload_value(payload, "disabled_reason")
    enabled = int(parse_boolean_flag(payload.get("enabled"), "enabled")) if "enabled" in payload else 1
    if disabled_reason:
        enabled = 0
    conn = db_conn()
    conn.execute(
        """
        INSERT INTO api_keys (
            client_id, key, name, enabled, created_at,
            rate_limit_per_minute, daily_request_limit, daily_point_limit,
            allowed_kinds, allowed_models, allowed_scenes,
            allow_uploads, allow_experimental, allowed_resolutions, allowed_durations,
            expires_at, rotated_from_id, rotation_note, disabled_reason
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            client_id_value,
            key,
            name,
            enabled,
            time.time(),
            optional_non_negative_integer_payload_value(payload, "rate_limit_per_minute"),
            optional_non_negative_integer_payload_value(payload, "daily_request_limit"),
            optional_non_negative_integer_payload_value(payload, "daily_point_limit"),
            scopes["allowed_kinds"],
            scopes["allowed_models"],
            scopes["allowed_scenes"],
            scopes["allow_uploads"],
            scopes["allow_experimental"],
            scopes["allowed_resolutions"],
            scopes["allowed_durations"],
            expires_at,
            rotated_from_id,
            rotation_note,
            disabled_reason,
        ),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT api_keys.*, clients.name AS client_name, clients.contact AS client_contact, clients.status AS client_status
        FROM api_keys
        LEFT JOIN clients ON clients.id=api_keys.client_id
        WHERE api_keys.key=?
        """,
        (key,),
    ).fetchone()
    conn.close()
    return {"ok": True, "item": public_api_key(row, reveal=True) if row else None}


@app.patch("/api/admin/apikeys/{key_id}")
def update_api_key_policy(key_id: int, body: Dict[str, Any], _=Depends(require_admin)):
    payload = body or {}

    conn = db_conn()
    current = conn.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
    if not current:
        conn.close()
        raise HTTPException(404, "api key not found")
    client_id = payload.get("client_id")
    client_id_value: Optional[int]
    if client_id in (None, ""):
        client_id_value = current["client_id"]
    else:
        try:
            client_id_value = int(client_id)
        except (TypeError, ValueError):
            conn.close()
            raise HTTPException(400, "client_id must be an integer")
        get_client_record(client_id_value)
    scopes = api_key_scope_payload_values(payload, current)
    expires_at = optional_timestamp_payload_value(payload, "expires_at", current)
    rotated_from_id = optional_api_key_id_payload_value(payload, "rotated_from_id", current)
    rotation_note = optional_text_payload_value(payload, "rotation_note", current)
    disabled_reason = optional_text_payload_value(payload, "disabled_reason", current)
    if "enabled" in payload:
        enabled = int(parse_boolean_flag(payload.get("enabled"), "enabled"))
    elif "disabled_reason" in payload and disabled_reason:
        enabled = 0
    else:
        enabled = int(current["enabled"])
    name = optional_text_payload_value(payload, "name", current)
    conn.execute(
        """
        UPDATE api_keys
        SET client_id=?, name=?, rate_limit_per_minute=?, daily_request_limit=?, daily_point_limit=?,
            allowed_kinds=?, allowed_models=?, allowed_scenes=?,
            allow_uploads=?, allow_experimental=?, allowed_resolutions=?, allowed_durations=?,
            expires_at=?, rotated_from_id=?, rotation_note=?, disabled_reason=?, enabled=?
        WHERE id=?
        """,
        (
            client_id_value,
            name,
            optional_non_negative_integer_payload_value(payload, "rate_limit_per_minute", current),
            optional_non_negative_integer_payload_value(payload, "daily_request_limit", current),
            optional_non_negative_integer_payload_value(payload, "daily_point_limit", current),
            scopes["allowed_kinds"],
            scopes["allowed_models"],
            scopes["allowed_scenes"],
            scopes["allow_uploads"],
            scopes["allow_experimental"],
            scopes["allowed_resolutions"],
            scopes["allowed_durations"],
            expires_at,
            rotated_from_id,
            rotation_note,
            disabled_reason,
            enabled,
            key_id,
        ),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT api_keys.*, clients.name AS client_name, clients.contact AS client_contact, clients.status AS client_status
        FROM api_keys
        LEFT JOIN clients ON clients.id=api_keys.client_id
        WHERE api_keys.id=?
        """,
        (key_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "api key not found")
    return {"ok": True, "item": public_api_key(row)}


@app.delete("/api/admin/apikeys/{key_id}")
def delete_api_key(key_id: int, _=Depends(require_admin)):
    conn = db_conn()
    conn.execute(
        """
        UPDATE api_keys
        SET enabled=0, deleted_at=?, disabled_reason=?
        WHERE id=?
        """,
        (time.time(), "deleted", key_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


def execute_paginated_admin_query(
    count_sql: str,
    select_sql: str,
    params: List[Any],
    limit: int,
    offset: int,
) -> Tuple[int, List[Any]]:
    """Read a page and its filtered total from the same SQLite snapshot."""
    conn = db_conn()
    try:
        conn.execute("BEGIN")
        total = int(conn.execute(count_sql, tuple(params)).fetchone()[0])
        rows = conn.execute(select_sql, tuple(params + [limit + 1, offset])).fetchall()
        conn.commit()
        return total, rows
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/admin/usage")
def get_usage(
    limit: int = 200,
    offset: int = 0,
    client_id: Optional[int] = None,
    api_key_id: Optional[int] = None,
    account_id: Optional[int] = None,
    kind: Optional[str] = None,
    model_name: Optional[str] = None,
    status: Optional[str] = None,
    error_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    _=Depends(require_admin),
):
    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise HTTPException(422, f"limit must be between 1 and {MAX_LIST_LIMIT}")
    if offset < 0 or offset > MAX_LIST_OFFSET:
        raise HTTPException(422, f"offset must be between 0 and {MAX_LIST_OFFSET}")
    if kind and kind not in API_LIST_KINDS:
        raise HTTPException(422, "kind is not supported")
    if status and status not in TASK_LIST_STATUSES:
        raise HTTPException(422, "status is not supported")
    where = ["1=1"]
    params: List[Any] = []
    start = parse_report_date_boundary(date_from, end_of_day=False)
    end = parse_report_date_boundary(date_to, end_of_day=True)
    if start is not None:
        where.append("u.created_at>=?")
        params.append(start)
    if end is not None:
        where.append("u.created_at<=?")
        params.append(end)
    if client_id is not None:
        where.append("k.client_id=?")
        params.append(client_id)
    if api_key_id is not None:
        where.append("u.api_key_id=?")
        params.append(api_key_id)
    if account_id is not None:
        where.append("u.account_id=?")
        params.append(account_id)
    if kind:
        where.append("u.kind=?")
        params.append(kind)
    if model_name:
        where.append("u.model_name=?")
        params.append(model_name)
    if status:
        where.append("u.status=?")
        params.append(status)
    if error_code:
        where.append("u.error_code=?")
        params.append(error_code)
    where_sql = " AND ".join(where)
    from_sql = """
        FROM usage_log u
        LEFT JOIN accounts a ON u.account_id=a.id
        LEFT JOIN api_keys k ON u.api_key_id=k.id
        LEFT JOIN clients c ON k.client_id=c.id
    """
    total, rows = execute_paginated_admin_query(
        f"SELECT COUNT(*) {from_sql} WHERE {where_sql}",
        f"""
        SELECT
            u.*,
            a.email AS account_email,
            k.name AS api_key_name,
            c.name AS client_name
        {from_sql}
        WHERE {where_sql}
        ORDER BY u.id DESC
        LIMIT ? OFFSET ?
        """,
        params,
        limit,
        offset,
    )
    items = [dict(r) for r in rows[:limit]]
    return {"items": items, "limit": limit, "offset": offset, "total": total, "has_more": len(rows) > limit}


def parse_report_date_boundary(value: Optional[str], end_of_day: bool = False) -> Optional[float]:
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "date must use YYYY-MM-DD")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt.timestamp()


@app.get("/api/admin/uploads")
def list_admin_uploads(
    limit: int = 200,
    offset: int = 0,
    api_key_id: Optional[int] = None,
    account_id: Optional[int] = None,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    _=Depends(require_admin),
):
    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise HTTPException(422, f"limit must be between 1 and {MAX_LIST_LIMIT}")
    if offset < 0 or offset > MAX_LIST_OFFSET:
        raise HTTPException(422, f"offset must be between 0 and {MAX_LIST_OFFSET}")
    where = ["1=1"]
    params: List[Any] = []
    start = parse_report_date_boundary(date_from, end_of_day=False)
    end = parse_report_date_boundary(date_to, end_of_day=True)
    if start is not None:
        where.append("um.created_at>=?")
        params.append(start)
    if end is not None:
        where.append("um.created_at<=?")
        params.append(end)
    if api_key_id is not None:
        where.append("um.api_key_id=?")
        params.append(api_key_id)
    if account_id is not None:
        where.append("um.account_id=?")
        params.append(account_id)
    if status:
        where.append("um.status=?")
        params.append(status)
    if kind:
        clause, kind_params = upload_kind_filter_clause(kind)
        where.append(clause)
        params.extend(kind_params)
    where_sql = " AND ".join(where)
    from_sql = """
        FROM uploaded_media um
        LEFT JOIN accounts a ON um.account_id=a.id
        LEFT JOIN api_keys k ON um.api_key_id=k.id
        LEFT JOIN clients c ON k.client_id=c.id
    """
    total, rows = execute_paginated_admin_query(
        f"SELECT COUNT(*) {from_sql} WHERE {where_sql}",
        f"""
        SELECT
            um.*,
            a.email AS account_email,
            k.name AS api_key_name,
            c.name AS client_name,
            (
                SELECT COUNT(*)
                FROM tasks t
                WHERE t.api_key_id=um.api_key_id
                  AND t.account_id=um.account_id
                  AND INSTR(COALESCE(t.payload_json, ''), um.object_path) > 0
            ) AS related_task_count
        {from_sql}
        WHERE {where_sql}
        ORDER BY um.id DESC
        LIMIT ? OFFSET ?
        """,
        params,
        limit,
        offset,
    )
    items = [public_uploaded_media(row) for row in rows[:limit]]
    return {"items": items, "limit": limit, "offset": offset, "total": total, "has_more": len(rows) > limit}


@app.get("/api/admin/cost-report")
def get_cost_report(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    client_id: Optional[int] = None,
    api_key_id: Optional[int] = None,
    account_id: Optional[int] = None,
    model_name: Optional[str] = None,
    _=Depends(require_admin),
):
    where = ["1=1"]
    params: List[Any] = []
    start = parse_report_date_boundary(date_from, end_of_day=False)
    end = parse_report_date_boundary(date_to, end_of_day=True)
    if start is not None:
        where.append("u.created_at>=?")
        params.append(start)
    if end is not None:
        where.append("u.created_at<=?")
        params.append(end)
    if client_id is not None:
        where.append("k.client_id=?")
        params.append(client_id)
    if api_key_id is not None:
        where.append("u.api_key_id=?")
        params.append(api_key_id)
    if account_id is not None:
        where.append("u.account_id=?")
        params.append(account_id)
    if model_name:
        where.append("u.model_name=?")
        params.append(model_name)
    conn = db_conn()
    rows = conn.execute(
        f"""
        SELECT
            date(datetime(u.created_at, 'unixepoch', 'localtime')) AS report_date,
            k.client_id AS client_id,
            COALESCE(NULLIF(c.name, ''), NULLIF(k.name, ''), '') AS client_name,
            u.api_key_id AS api_key_id,
            COALESCE(k.name, '') AS api_key_name,
            u.account_id AS account_id,
            COALESCE(a.email, '') AS account_email,
            COALESCE(u.model_name, '') AS model_name,
            COUNT(*) AS request_count,
            COALESCE(SUM(COALESCE(u.estimated_point_cost, 0)), 0) AS estimated_point_cost,
            COALESCE(SUM(COALESCE(u.actual_point_cost, 0)), 0) AS actual_point_cost,
            COALESCE(SUM(CASE WHEN u.status='completed' THEN 1 ELSE 0 END), 0) AS success_request_count,
            COALESCE(SUM(CASE WHEN u.status!='completed' THEN 1 ELSE 0 END), 0) AS failed_request_count,
            COALESCE(SUM(CASE WHEN u.status='completed' THEN COALESCE(u.actual_point_cost, 0) ELSE 0 END), 0) AS success_actual_point_cost,
            COALESCE(SUM(CASE WHEN u.status!='completed' THEN COALESCE(u.actual_point_cost, 0) ELSE 0 END), 0) AS failed_actual_point_cost
        FROM usage_log u
        LEFT JOIN api_keys k ON k.id=u.api_key_id
        LEFT JOIN clients c ON c.id=k.client_id
        LEFT JOIN accounts a ON a.id=u.account_id
        WHERE {' AND '.join(where)}
        GROUP BY report_date, k.client_id, c.name, u.api_key_id, k.name, u.account_id, a.email, u.model_name
        ORDER BY report_date DESC, actual_point_cost DESC, request_count DESC, api_key_id DESC
        LIMIT 500
        """,
        tuple(params),
    ).fetchall()
    conn.close()
    items = [dict(row) for row in rows]
    summary = {
        "request_count": sum(int_or_default(item.get("request_count"), 0) for item in items),
        "estimated_point_cost": sum(int_or_default(item.get("estimated_point_cost"), 0) for item in items),
        "actual_point_cost": sum(int_or_default(item.get("actual_point_cost"), 0) for item in items),
        "success_actual_point_cost": sum(int_or_default(item.get("success_actual_point_cost"), 0) for item in items),
        "failed_actual_point_cost": sum(int_or_default(item.get("failed_actual_point_cost"), 0) for item in items),
    }
    return {"items": items, "summary": summary}


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
    image: Optional[Dict[str, Any]] = None
    first_frame: Optional[Dict[str, Any]] = None
    last_frame: Optional[Dict[str, Any]] = None
    reference_images: Optional[List[Dict[str, Any]]] = None
    reference_videos: Optional[List[Dict[str, Any]]] = None
    motion_video: Optional[Dict[str, Any]] = None
    character_image: Optional[Dict[str, Any]] = None
    ref_duration: Optional[Any] = None
    ref_total_duration: Optional[Any] = None
    motion_duration: Optional[Any] = None
    keep_original_sound: Optional[bool] = None
    is_audio: Optional[bool] = None
    ai_type: Optional[Any] = None
    sync_wait_seconds: Optional[float] = None


class OpenAIImageGenerationIn(BaseModel):
    model: Optional[str] = "gpt-image-1"
    prompt: str
    n: int = 1
    size: Optional[str] = "auto"
    quality: Optional[str] = "auto"
    response_format: Optional[str] = "url"
    output_format: Optional[str] = None
    user: Optional[str] = None
    ratio: Optional[str] = None
    resolution: Optional[str] = None
    timeout: Optional[float] = None
    stream: bool = False


class OpenAIVideoGenerationIn(BaseModel):
    model: Optional[str] = "sora-2"
    prompt: str
    seconds: Optional[Any] = None
    size: Optional[str] = "auto"
    user: Optional[str] = None
    scene_id: Optional[str] = None
    ratio: Optional[str] = None
    resolution: Optional[str] = None
    image: Optional[Dict[str, Any]] = None
    input_reference: Optional[List[Dict[str, Any]]] = None


def validate_generation_request_boundaries(
    body: GatewayGenerateIn,
    idempotency_key: str,
    request_id: str,
) -> None:
    prompt_max_length = max(1, int_or_default(gateway_cfg().get("prompt_max_length"), 4000))
    if len(body.prompt) > prompt_max_length:
        raise GatewayAPIError(
            400,
            "PROMPT_TOO_LONG",
            "prompt exceeds the configured length limit",
            {"field": "prompt", "max_length": prompt_max_length},
            request_id=request_id,
        )
    idempotency_key_max_length = max(
        1,
        int_or_default(gateway_cfg().get("idempotency_key_max_length"), 255),
    )
    if len(idempotency_key) > idempotency_key_max_length:
        raise GatewayAPIError(
            400,
            "IDEMPOTENCY_KEY_TOO_LONG",
            "Idempotency-Key exceeds the configured length limit",
            {"field": "Idempotency-Key", "max_length": idempotency_key_max_length},
            request_id=request_id,
        )
    if body.sync_wait_seconds is None:
        return
    sync_wait_seconds = float(body.sync_wait_seconds)
    sync_wait_max_seconds = max(
        0.0,
        float_or_default(gateway_cfg().get("sync_wait_max_seconds"), 120.0),
    )
    if not math.isfinite(sync_wait_seconds) or not 0.0 <= sync_wait_seconds <= sync_wait_max_seconds:
        raise GatewayAPIError(
            400,
            "SYNC_WAIT_OUT_OF_RANGE",
            "sync_wait_seconds is outside the configured range",
            {"field": "sync_wait_seconds", "min": 0, "max": sync_wait_max_seconds},
            request_id=request_id,
        )


def request_body_from_generation(body: GatewayGenerateIn) -> Dict[str, Any]:
    data = model_data(body)
    data.pop("sync_wait_seconds", None)
    return data


def queue_generation_task(
    api_key_id: Optional[int],
    request_id: str,
    account: sqlite3.Row,
    body: GatewayGenerateIn,
    options: Dict[str, Any],
    estimated_point_cost: Optional[int],
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    payload = request_body_from_generation(body)
    response = {"status": "queued"}
    task_id = save_task(
        account["id"],
        body.kind,
        body.prompt,
        payload,
        response,
        status="queued",
        api_key_id=api_key_id,
        request_id=request_id,
        model_name=options.get("model_name") or "",
        scene_id=options.get("scene_id") or "",
        resolution=options.get("resolution") or "",
        ratio=options.get("ratio") or "",
        duration=options.get("duration"),
        estimated_point_cost=estimated_point_cost,
        conn=conn,
    )
    if api_key_id is not None:
        usage_args = (
            api_key_id,
            body.kind,
            account["id"],
            body.prompt,
            "queued",
        )
        usage_kwargs = {
            "task_id": task_id,
            "request_id": request_id,
            "model_name": options.get("model_name") or "",
            "resolution": options.get("resolution") or "",
            "ratio": options.get("ratio") or "",
            "duration": options.get("duration"),
            "scene_id": options.get("scene_id") or "",
            "estimated_point_cost": estimated_point_cost,
            "status_code": 202,
        }
        if conn is not None:
            insert_usage_log(conn, *usage_args, **usage_kwargs)
        else:
            log_usage(*usage_args, **usage_kwargs)
    if conn is None and task_worker_enabled():
        TASK_WORKER_WAKE.set()
    return task_id


def admit_generation_task(
    api_key_id: Optional[int],
    request_id: str,
    body: GatewayGenerateIn,
    policy: Optional[Dict[str, Any]] = None,
) -> Tuple[int, sqlite3.Row, Dict[str, Any], Dict[str, Any], Optional[int]]:
    """Atomically reserve account capacity and persist the queued task."""
    conn = db_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        account, caps, options, estimated_point_cost = select_generation_account(
            body,
            request_id=request_id,
            conn=conn,
        )
        if api_key_id is not None:
            effective_policy = policy or resolve_api_key_policy(get_api_key_record(api_key_id))
            enforce_api_key_scope(effective_policy, body.kind, options, caps, request_id)
            check_daily_quota(
                api_key_id,
                estimated_point_cost,
                effective_policy,
                time.time(),
                request_id,
                conn=conn,
            )
        task_id = queue_generation_task(
            api_key_id,
            request_id,
            account,
            body,
            options,
            estimated_point_cost,
            conn=conn,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if task_worker_enabled():
        TASK_WORKER_WAKE.set()
    return task_id, account, caps, options, estimated_point_cost


def wait_for_task_snapshot(task_id: int, api_key_id: int, timeout_sec: Optional[float]) -> Dict[str, Any]:
    timeout = float(timeout_sec if timeout_sec is not None else gateway_cfg().get("sync_wait_seconds") or 0)
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        row = fetch_task_row(task_id, api_key_id)
        if not row:
            raise GatewayAPIError(404, "TASK_NOT_FOUND", "task not found")
        task = task_detail_for_row(row)
        if task.get("status") in TASK_TERMINAL_STATUSES:
            return task
        if time.monotonic() >= deadline:
            return task
        if not task_worker_enabled():
            process_task_queue(limit=1)
            continue
        TASK_WORKER_WAKE.set()
        time.sleep(min(0.2, max(0.01, deadline - time.monotonic())))


@app.post("/v1/generate")
def gateway_generate(body: GatewayGenerateIn, request: Request, api_key_id: int = Depends(require_api_key)):
    """Generate image or video via the account pool. Auto-selects account if not specified."""
    request_id = gateway_request_id(request)
    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    validate_generation_request_boundaries(body, idempotency_key, request_id)
    request_hash = request_hash_for_generation(body)
    idempotency_reserved = False
    if idempotency_key:
        reservation = reserve_idempotency_record(api_key_id, idempotency_key, request_hash)
        reservation_state = reservation["state"]
        existing_idempotency = reservation.get("record") or {}
        if reservation_state == "conflict":
            raise GatewayAPIError(
                409,
                "IDEMPOTENCY_KEY_CONFLICT",
                "Idempotency-Key was already used with a different request body",
                {"field": "Idempotency-Key"},
                request_id=request_id,
            )
        if reservation_state == "pending":
            raise GatewayAPIError(
                409,
                "IDEMPOTENCY_KEY_IN_PROGRESS",
                "a request with this Idempotency-Key is still being processed",
                {"field": "Idempotency-Key", "retryable": True},
                request_id=request_id,
            )
        if reservation_state == "replay":
            replay = json.loads(existing_idempotency.get("response_json") or "{}")
            replay["idempotent_replay"] = True
            replay["request_id"] = request_id
            replay = public_gateway_result_for_request(replay, request)
            return JSONResponse(status_code=int(existing_idempotency["status_code"]), content=replay)
        idempotency_reserved = reservation_state == "reserved"

    try:
        with REQUEST_ADMISSION_LOCK:
            policy = resolve_api_key_policy(get_api_key_record(api_key_id))
            now = time.time()
            check_rate_limit(api_key_id, policy, now, request_id)
            if body.kind not in ("image", "video"):
                raise GatewayAPIError(400, "UNSUPPORTED_KIND", f"unsupported kind: {body.kind}", {"field": "kind"}, request_id=request_id)
            task_id, account, caps, options, estimated_point_cost = admit_generation_task(
                api_key_id,
                request_id,
                body,
                policy,
            )
    except Exception:
        if idempotency_reserved:
            release_idempotency_reservation(api_key_id, idempotency_key, request_hash)
        raise

    result: Dict[str, Any] = {
        "ok": True,
        "task_id": task_id,
        "account_id": account["id"],
        "request_id": request_id,
        "idempotent_replay": False,
        "estimated_point_cost": estimated_point_cost,
        "status": "queued",
    }
    status_code = 202
    if idempotency_key:
        save_idempotency_record(api_key_id, idempotency_key, request_hash, status_code, result, task_id)
    sync_wait_seconds = body.sync_wait_seconds if body.sync_wait_seconds is not None else gateway_cfg().get("sync_wait_seconds") or 0
    if sync_wait_seconds and float(sync_wait_seconds) > 0:
        snapshot = wait_for_task_snapshot(task_id, api_key_id, float(sync_wait_seconds))
        result["status"] = snapshot.get("status") or result["status"]
        result["account_id"] = snapshot.get("account_id") or result["account_id"]
        result["task"] = snapshot
        result["assets"] = snapshot.get("assets") or []
        result["response"] = snapshot.get("response") or {}
        if snapshot.get("status") == "completed":
            status_code = 200
        elif snapshot.get("status") in {"failed", "expired"}:
            error_code = snapshot.get("error_code") or "UPSTREAM_ERROR"
            error_message = snapshot.get("error_message") or "generation failed"
            error_content = {
                "ok": False,
                "error": {
                    "code": error_code,
                    "message": error_message,
                    "details": {
                        "task_id": task_id,
                        "status": snapshot.get("status"),
                    },
                },
                "request_id": request_id,
            }
            if idempotency_key:
                save_idempotency_record(api_key_id, idempotency_key, request_hash, 503, error_content, task_id)
            return JSONResponse(status_code=503, content=error_content)
        elif snapshot.get("status") == "cancelled":
            error_content = {
                "ok": False,
                "error": {
                    "code": "TASK_CANCELLED",
                    "message": snapshot.get("error_message") or "task cancelled",
                    "details": {
                        "task_id": task_id,
                        "status": snapshot.get("status"),
                    },
                },
                "request_id": request_id,
            }
            if idempotency_key:
                save_idempotency_record(api_key_id, idempotency_key, request_hash, 409, error_content, task_id)
            return JSONResponse(status_code=409, content=error_content)
        else:
            status_code = 202
    if idempotency_key:
        save_idempotency_record(api_key_id, idempotency_key, request_hash, status_code, result, task_id)
    return JSONResponse(
        status_code=status_code,
        content=public_gateway_result_for_request(result, request),
    )


def json_response_content(response: JSONResponse) -> Dict[str, Any]:
    try:
        body = json.loads(bytes(response.body).decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def validate_openai_image_request(body: OpenAIImageGenerationIn) -> str:
    if body.n != 1:
        raise OpenAICompatError(
            "only n=1 is supported",
            param="n",
            code="unsupported_n",
        )
    if body.stream:
        raise OpenAICompatError(
            "stream=true is not supported for image generation",
            param="stream",
            code="unsupported_stream",
        )
    response_format = str(body.response_format or "url").lower()
    if response_format not in {"url", "b64_json"}:
        raise OpenAICompatError(
            "response_format must be url or b64_json",
            param="response_format",
            code="unsupported_response_format",
        )
    return response_format


def openai_image_body_from_request(
    body: OpenAIImageGenerationIn,
    *,
    reference_images: Optional[List[Dict[str, Any]]] = None,
    account_id: Optional[int] = None,
) -> GatewayGenerateIn:
    provider_model = resolve_openai_model("image", body.model, CFG)
    mapped_ratio = image_size_to_ratio(body.size)
    compat = openai_compat_cfg()
    configured_timeout = float_or_default(
        body.timeout if body.timeout is not None else compat.get("image_sync_timeout_seconds"),
        120.0,
    )
    max_timeout = max(0.01, float_or_default(compat.get("max_sync_timeout_seconds"), 120.0))
    sync_timeout = min(max(0.01, configured_timeout), max_timeout)
    native_body = GatewayGenerateIn(
        kind="image",
        prompt=body.prompt,
        model_name=provider_model,
        ratio=body.ratio or mapped_ratio or CFG["oreate"]["default_image_ratio"],
        resolution=body.resolution or CFG["oreate"]["default_image_resolution"],
        reference_images=reference_images or None,
        account_id=account_id,
        sync_wait_seconds=sync_timeout,
    )
    return native_body


def execute_openai_image_request(
    body: OpenAIImageGenerationIn,
    native_body: GatewayGenerateIn,
    request: Request,
    api_key_id: int,
    response_format: str,
):
    native_response = gateway_generate(native_body, request, api_key_id)
    content = json_response_content(native_response)
    if native_response.status_code >= 400:
        error = content.get("error") if isinstance(content.get("error"), dict) else {}
        return openai_error_response(
            native_response.status_code,
            str(error.get("message") or "image generation failed"),
            code=str(error.get("code") or "image_generation_failed"),
        )

    assets = content.get("assets") if isinstance(content.get("assets"), list) else []
    if native_response.status_code == 202 or content.get("status") != "completed":
        return openai_error_response(
            504,
            "image generation did not complete before the compatibility timeout",
            code="image_generation_timeout",
        )
    if not assets:
        return openai_error_response(
            502,
            "image generation completed without an image asset",
            code="image_asset_missing",
        )
    task = content.get("task") if isinstance(content.get("task"), dict) else {}
    created = int(float(task.get("created_at") or time.time()))
    task_id = int_or_default(content.get("task_id") or task.get("id"), 0)
    if response_format == "b64_json":
        try:
            _stored_task, original_asset_url = completed_image_task_asset(task_id, 0)
            image_bytes, _media_type, _removed = cleaned_image_asset(original_asset_url)
        except HTTPException as exc:
            return openai_error_response(
                exc.status_code,
                str(exc.detail or "image asset processing failed"),
                code="image_asset_processing_failed",
            )
        return {
            "created": created,
            "data": [
                {
                    "b64_json": base64.b64encode(image_bytes).decode("ascii"),
                    "revised_prompt": body.prompt,
                }
            ],
        }
    return {
        "created": created,
        "data": [
            {
                "url": str(assets[0]),
                "revised_prompt": body.prompt,
            }
        ],
    }


@app.post("/v1/images/generations")
def gateway_openai_image_generation(
    body: OpenAIImageGenerationIn,
    request: Request,
    api_key_id: int = Depends(require_api_key),
):
    response_format = validate_openai_image_request(body)
    native_body = openai_image_body_from_request(body)
    return execute_openai_image_request(body, native_body, request, api_key_id, response_format)


def openai_video_task(task_id: int, api_key_id: int) -> Dict[str, Any]:
    row = fetch_task_row(task_id, api_key_id)
    if not row or str(row["kind"] or "") != "video":
        raise OpenAICompatError(
            "video not found",
            param="video_id",
            code="video_not_found",
            status_code=404,
        )
    return task_detail_for_row(row)


async def upload_openai_input_reference_files(
    files: List[UploadFile],
    api_key_id: int,
    account: sqlite3.Row,
    request_id: str,
    *,
    allowed_extensions: Optional[Iterable[str]] = None,
    error_param: str = "input_reference",
    error_code: str = "invalid_input_reference",
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not files:
        return [], []
    max_bytes = max(1, int_or_default(gateway_cfg().get("upload_max_bytes"), 104857600))
    chunk_bytes = max(1, int_or_default(gateway_cfg().get("upload_read_chunk_bytes"), 1048576))
    accepted_extensions = {
        normalized_file_extension(item)
        for item in (allowed_extensions or MEDIA_UPLOAD_EXTENSIONS)
        if normalized_file_extension(item)
    }
    attachments: List[Dict[str, Any]] = []
    try:
        session = CLIENT.session_from_account(account)
        for file in files:
            try:
                filename = Path(file.filename or "upload.bin").name
                extension = normalized_file_extension(Path(filename).suffix)
                if extension not in accepted_extensions:
                    raise OpenAICompatError(
                        f"{error_param} contains an unsupported media type",
                        param=error_param,
                        code=error_code,
                    )
                chunks: List[bytes] = []
                total_bytes = 0
                while True:
                    chunk = await file.read(chunk_bytes)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise OpenAICompatError(
                            f"{error_param} file exceeds the configured size limit",
                            param=error_param,
                            code=error_code,
                            status_code=413,
                        )
                    chunks.append(chunk)
                data = b"".join(chunks)
                if not data:
                    raise OpenAICompatError(
                        f"{error_param} file is empty",
                        param=error_param,
                        code=error_code,
                    )
                attachment = CLIENT.upload_file_bytes(
                    session,
                    filename,
                    data,
                    file.content_type or "application/octet-stream",
                )
                save_uploaded_media_record(api_key_id, int(account["id"]), attachment)
                attachments.append(attachment)
            finally:
                await file.close()
    except OpenAICompatError:
        raise
    except Exception as exc:
        mark_account_failure(account["id"], exc)
        raise GatewayAPIError(
            503,
            "UPLOAD_FAILED",
            "upstream upload failed",
            request_id=request_id,
        ) from exc
    mark_account_success(account["id"])
    return split_input_reference_attachments(attachments)


def openai_image_generation_payload_from_form(form: Any) -> Dict[str, Any]:
    payload = {
        "model": form.get("model"),
        "prompt": form.get("prompt"),
        "n": form.get("n"),
        "size": form.get("size"),
        "quality": form.get("quality"),
        "response_format": form.get("response_format"),
        "output_format": form.get("output_format"),
        "user": form.get("user"),
        "ratio": form.get("ratio"),
        "resolution": form.get("resolution"),
        "timeout": form.get("timeout"),
        "stream": form.get("stream"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def multipart_upload_files(form: Any, field: str) -> List[UploadFile]:
    field_pattern = re.compile(rf"^{re.escape(field)}(?:\[\d*\])?$")
    return [
        item
        for field_name, item in form.multi_items()
        if field_pattern.fullmatch(str(field_name))
        and hasattr(item, "read")
        and hasattr(item, "filename")
    ]


def multipart_field_present(form: Any, field: str) -> bool:
    field_pattern = re.compile(rf"^{re.escape(field)}(?:\[\d*\])?$")
    return any(field_pattern.fullmatch(str(field_name)) for field_name, _ in form.multi_items())


@app.post("/v1/images/edits")
async def gateway_openai_image_edit(
    request: Request,
    api_key_id: int = Depends(require_api_key),
):
    content_type = str(request.headers.get("content-type") or "").lower()
    if "multipart/form-data" not in content_type:
        raise OpenAICompatError(
            "image edits require multipart/form-data",
            param="image",
            code="unsupported_content_type",
            status_code=415,
        )

    request_id = gateway_request_id(request)
    async with request.form() as form:
        if multipart_field_present(form, "mask"):
            raise OpenAICompatError(
                "mask is not supported for image edits",
                param="mask",
                code="unsupported_mask",
            )
        image_files = multipart_upload_files(form, "image")
        if not image_files:
            raise OpenAICompatError(
                "image is required",
                param="image",
                code="missing_image",
            )
        try:
            body = OpenAIImageGenerationIn(**openai_image_generation_payload_from_form(form))
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc

        response_format = validate_openai_image_request(body)
        preflight_body = openai_image_body_from_request(body)
        policy = resolve_api_key_policy(get_api_key_record(api_key_id))
        if not policy.get("allow_uploads", True):
            raise GatewayAPIError(
                403,
                "API_KEY_UPLOAD_FORBIDDEN",
                "API key is not allowed to upload files",
                request_id=request_id,
            )
        account, caps, options, _ = select_generation_account(preflight_body, request_id=request_id)
        enforce_api_key_scope(policy, "image", options, caps, request_id)
        reference_images, reference_videos = await upload_openai_input_reference_files(
            image_files,
            api_key_id,
            account,
            request_id,
            allowed_extensions=IMAGE_UPLOAD_EXTENSIONS,
            error_param="image",
            error_code="invalid_image",
        )
        if reference_videos or not reference_images:
            raise OpenAICompatError(
                "image must contain a supported image file",
                param="image",
                code="invalid_image",
            )
        native_body = openai_image_body_from_request(
            body,
            reference_images=reference_images,
            account_id=int(account["id"]),
        )
        return execute_openai_image_request(body, native_body, request, api_key_id, response_format)


def openai_video_body_from_request(
    body: OpenAIVideoGenerationIn,
    *,
    reference_images: Optional[List[Dict[str, Any]]] = None,
    reference_videos: Optional[List[Dict[str, Any]]] = None,
    account_id: Optional[int] = None,
) -> GatewayGenerateIn:
    provider_model = resolve_openai_model("video", body.model, CFG)
    try:
        duration = int(body.seconds if body.seconds not in (None, "") else CFG["oreate"]["default_video_duration"])
    except (TypeError, ValueError) as exc:
        raise OpenAICompatError(
            "seconds must be an integer",
            param="seconds",
            code="invalid_seconds",
        ) from exc
    if duration <= 0:
        raise OpenAICompatError(
            "seconds must be positive",
            param="seconds",
            code="invalid_seconds",
        )
    mapped_ratio = video_size_to_ratio(body.size)
    mapped_resolution = video_size_to_resolution(body.size)
    has_reference_inputs = bool(reference_images or reference_videos or body.input_reference)
    scene_id = body.scene_id or ("reference" if has_reference_inputs else CFG["oreate"]["default_video_scene"])
    if has_reference_inputs and scene_id != "reference":
        raise OpenAICompatError(
            "input_reference requires the reference scene",
            param="input_reference",
            code="invalid_input_reference",
        )
    return GatewayGenerateIn(
        kind="video",
        prompt=body.prompt,
        model_name=provider_model,
        ratio=body.ratio or mapped_ratio or CFG["oreate"]["default_video_ratio"],
        resolution=body.resolution or mapped_resolution or CFG["oreate"]["default_video_resolution"],
        duration=duration,
        scene_id=scene_id,
        account_id=account_id,
        image=body.image,
        reference_images=reference_images or None,
        reference_videos=reference_videos or None,
    )


async def parse_openai_video_generation_request(request: Request) -> OpenAIVideoGenerationIn:
    return OpenAIVideoGenerationIn(**(await request.json()))


def openai_video_generation_payload_from_form(form: Any) -> Dict[str, Any]:
    payload = {
        "model": form.get("model"),
        "prompt": form.get("prompt"),
        "seconds": form.get("seconds"),
        "size": form.get("size"),
        "user": form.get("user"),
        "scene_id": form.get("scene_id"),
        "ratio": form.get("ratio"),
        "resolution": form.get("resolution"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def openai_video_object(task: Dict[str, Any]) -> Dict[str, Any]:
    return task_to_video_object(
        task,
        requested_model=openai_model_name_for_provider("video", task.get("model_name"), CFG),
    )


@app.post("/v1/videos/generations")
@app.post("/v1/videos")
async def gateway_openai_video_generation(
    request: Request,
    api_key_id: int = Depends(require_api_key),
):
    reference_images: List[Dict[str, Any]] = []
    reference_videos: List[Dict[str, Any]] = []
    content_type = str(request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        async with request.form() as form:
            try:
                body = OpenAIVideoGenerationIn(**openai_video_generation_payload_from_form(form))
            except ValidationError as exc:
                raise RequestValidationError(exc.errors()) from exc
            requested_model = str(body.model or "sora-2")
            requested_size = str(body.size or "auto")
            multipart_references = [
                item
                for field_name in ("input_reference", "input_reference[]")
                for item in form.getlist(field_name)
                if hasattr(item, "read") and hasattr(item, "filename")
            ]
            if multipart_references:
                policy = resolve_api_key_policy(get_api_key_record(api_key_id))
                if not policy.get("allow_uploads", True):
                    raise GatewayAPIError(
                        403,
                        "API_KEY_UPLOAD_FORBIDDEN",
                        "API key is not allowed to upload files",
                        request_id=gateway_request_id(request),
                    )
                account = pick_account_for_generation("video") or pick_account_for_generation("image")
                if not account:
                    raise GatewayAPIError(503, "NO_ACCOUNT_AVAILABLE", "no verified account available", request_id=gateway_request_id(request))
                reference_images, reference_videos = await upload_openai_input_reference_files(
                    multipart_references,
                    api_key_id,
                    account,
                    gateway_request_id(request),
                )
            native_body = openai_video_body_from_request(
                body,
                reference_images=reference_images,
                reference_videos=reference_videos,
                account_id=int(account["id"]) if multipart_references else None,
            )
    else:
        try:
            body = await parse_openai_video_generation_request(request)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc
        requested_model = str(body.model or "sora-2")
        requested_size = str(body.size or "auto")
        if body.input_reference:
            reference_images, reference_videos, upload_account_id = resolve_uploaded_input_reference(
                api_key_id,
                body.input_reference,
            )
            native_body = openai_video_body_from_request(
                body,
                reference_images=reference_images,
                reference_videos=reference_videos,
                account_id=upload_account_id,
            )
        else:
            native_body = openai_video_body_from_request(body)
    native_response = gateway_generate(native_body, request, api_key_id)
    content = json_response_content(native_response)
    if native_response.status_code >= 400:
        error = content.get("error") if isinstance(content.get("error"), dict) else {}
        return openai_error_response(
            native_response.status_code,
            str(error.get("message") or "video generation failed"),
            code=str(error.get("code") or "video_generation_failed"),
        )
    task_id = int(content.get("task_id") or 0)
    task = openai_video_task(task_id, api_key_id)
    return task_to_video_object(
        task,
        requested_model=requested_model,
        requested_size=requested_size,
    )


@app.get("/v1/videos/{video_id}/content")
def gateway_openai_video_content(video_id: str, api_key_id: int = Depends(require_api_key)):
    task = openai_video_task(decode_video_id(video_id), api_key_id)
    if task.get("status") != "completed":
        raise OpenAICompatError(
            "video is not completed",
            param="video_id",
            code="video_not_completed",
            status_code=409,
        )
    assets = task.get("assets") if isinstance(task.get("assets"), list) else []
    asset = str(assets[0] if assets else "")
    parsed = urlparse(asset)
    allowlist = {
        str(item).strip().lower()
        for item in openai_compat_cfg().get("asset_host_allowlist", ["cdn.oreateai.com"])
        if str(item).strip()
    }
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() not in allowlist:
        raise OpenAICompatError(
            "video asset is unavailable",
            param="video_id",
            code="invalid_video_asset",
            status_code=502,
        )
    return RedirectResponse(asset, status_code=307)


@app.get("/v1/videos/{video_id}")
def gateway_openai_video_detail(video_id: str, api_key_id: int = Depends(require_api_key)):
    task = openai_video_task(decode_video_id(video_id), api_key_id)
    return openai_video_object(task)


@app.delete("/v1/videos/{video_id}")
def gateway_openai_video_delete(video_id: str, api_key_id: int = Depends(require_api_key)):
    task_id = decode_video_id(video_id)
    task = openai_video_task(task_id, api_key_id)
    if task.get("status") in TASK_TERMINAL_STATUSES:
        raise OpenAICompatError(
            "terminal video jobs cannot be cancelled",
            param="video_id",
            code="video_not_cancellable",
            status_code=409,
        )
    cancel_task_record(task_id, api_key_id)
    return openai_video_object(openai_video_task(task_id, api_key_id))


@app.post("/v1/uploads")
async def gateway_upload(
    request: Request,
    file: UploadFile = File(...),
    account_id: Optional[int] = Form(None),
    api_key_id: int = Depends(require_api_key),
):
    """Upload a local file to the same BOS object path format used by web video scenes."""
    request_id = gateway_request_id(request)
    policy = resolve_api_key_policy(get_api_key_record(api_key_id))
    if not policy.get("allow_uploads", True):
        raise GatewayAPIError(403, "API_KEY_UPLOAD_FORBIDDEN", "API key is not allowed to upload files", request_id=request_id)
    now = time.time()
    filename = Path(file.filename or "upload.bin").name
    extension = normalized_file_extension(Path(filename).suffix)
    if extension not in MEDIA_UPLOAD_EXTENSIONS:
        raise GatewayAPIError(
            415,
            "UNSUPPORTED_UPLOAD_TYPE",
            "only supported image and video files can be uploaded",
            {"field": "file"},
            request_id=request_id,
        )
    with REQUEST_ADMISSION_LOCK:
        check_rate_limit(api_key_id, policy, now, request_id)
        check_daily_quota(api_key_id, 0, policy, now, request_id)
        account = pick_account_for_generation("video", account_id) or pick_account_for_generation("image", account_id)
        if not account:
            raise GatewayAPIError(503, "NO_ACCOUNT_AVAILABLE", "no verified account available", request_id=request_id)
        usage_id = log_usage(
            api_key_id,
            "upload",
            account["id"],
            filename,
            "uploading",
            "upload admitted",
            request_id=request_id,
            estimated_point_cost=0,
        )
    max_bytes = max(1, int_or_default(gateway_cfg().get("upload_max_bytes"), 104857600))
    chunk_bytes = max(1, int_or_default(gateway_cfg().get("upload_read_chunk_bytes"), 1048576))
    chunks: List[bytes] = []
    total_bytes = 0
    while True:
        chunk = await file.read(chunk_bytes)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            update_usage_log(
                usage_id,
                status="failed",
                response_summary="uploaded file exceeds the configured size limit",
                error_code="UPLOAD_TOO_LARGE",
                status_code=413,
            )
            raise GatewayAPIError(
                413,
                "UPLOAD_TOO_LARGE",
                "uploaded file exceeds the configured size limit",
                {"field": "file", "max_bytes": max_bytes},
                request_id=request_id,
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        update_usage_log(
            usage_id,
            status="failed",
            response_summary="uploaded file is empty",
            error_code="EMPTY_UPLOAD",
            status_code=400,
        )
        raise GatewayAPIError(400, "EMPTY_UPLOAD", "uploaded file is empty", {"field": "file"}, request_id=request_id)
    try:
        session = CLIENT.session_from_account(account)
        attachment = CLIENT.upload_file_bytes(
            session,
            filename,
            data,
            file.content_type or "application/octet-stream",
        )
    except Exception as e:
        mark_account_failure(account["id"], e)
        update_usage_log(
            usage_id,
            status="failed",
            response_summary="upstream upload failed",
            error_code="UPLOAD_FAILED",
            status_code=503,
        )
        raise GatewayAPIError(503, "UPLOAD_FAILED", "upstream upload failed", request_id=request_id)
    mark_account_success(account["id"])
    save_uploaded_media_record(api_key_id, int(account["id"]), attachment)
    update_usage_log(
        usage_id,
        status="completed",
        response_summary="upload completed",
        status_code=200,
    )
    return {
        "ok": True,
        "request_id": request_id,
        "account_id": account["id"],
        "attachment": attachment,
        "message_attachment": normalize_upload_attachment(attachment),
    }


@app.get("/v1/tasks")
def gateway_tasks(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    api_key_id: int = Depends(require_api_key),
):
    """List tasks created by this API key."""
    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise HTTPException(422, f"limit must be between 1 and {MAX_LIST_LIMIT}")
    if offset < 0 or offset > MAX_LIST_OFFSET:
        raise HTTPException(422, f"offset must be between 0 and {MAX_LIST_OFFSET}")
    if kind and kind not in API_LIST_KINDS:
        raise HTTPException(422, "kind is not supported")
    if status and status not in TASK_LIST_STATUSES:
        raise HTTPException(422, "status is not supported")
    where = ["api_key_id=?"]
    params: List[Any] = [api_key_id]
    if status:
        where.append("status=?")
        params.append(status)
    if kind:
        where.append("kind=?")
        params.append(kind)
    conn = db_conn()
    rows = conn.execute(
        f"SELECT * FROM tasks WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params + [limit + 1, offset]),
    ).fetchall()
    conn.close()
    items = [public_task_for_request(task_row_to_public(r), request) for r in rows[:limit]]
    return {"items": items, "limit": limit, "offset": offset, "has_more": len(rows) > limit}


@app.get("/v1/accounts/status")
def gateway_account_status(api_key_id: int = Depends(require_api_key)):
    """Get pool status."""
    rows = list_accounts()
    summary = account_pool_summary(rows)
    return {
        "ok": True,
        "total_accounts": summary["total"],
        "verified_accounts": summary["verified"],
        "healthy_accounts": summary["healthy"],
        "cooling_accounts": summary["cooling"],
        "low_balance_accounts": summary["low_balance"],
        "invalid_accounts": summary["invalid"],
        "risk_control_accounts": summary["risk_control"],
        "balance_known_accounts": summary["balance_known"],
    }


@app.get("/healthz")
def healthz():
    return {"ok": True, "status": "ok"}


def validate_account_secret_storage_readiness() -> None:
    conn = db_conn()
    try:
        rows = conn.execute("SELECT password,ouid,ouss FROM accounts").fetchall()
    finally:
        conn.close()
    stored_values = [
        value
        for row in rows
        for value in (row["password"], row["ouid"], row["ouss"])
        if value not in (None, "")
    ]
    if not stored_values:
        return
    if not active_encryption_key():
        raise HTTPException(503, "account secrets exist but the server encryption key is not configured")
    try:
        secret_fernet(required=True)
    except RuntimeError as exc:
        raise HTTPException(503, "server encryption key is invalid") from exc
    if any(not is_encrypted_secret(value) for value in stored_values):
        raise HTTPException(503, "plaintext account secrets remain; run the secret migration before serving traffic")
    try:
        for value in stored_values:
            decrypt_secret_value(value, required=True)
    except RuntimeError as exc:
        raise HTTPException(503, "server encryption key cannot decrypt stored account secrets") from exc


def server_host_requires_public_deployment_acknowledgement(host: Any) -> bool:
    normalized = str(host or "").strip().lower()
    if not normalized:
        return False
    if normalized in {"localhost", "::1"}:
        return False
    return not normalized.startswith("127.")


def validate_public_bind_readiness() -> None:
    if not server_host_requires_public_deployment_acknowledgement(CFG.get("server", {}).get("host")):
        return
    deployment = deployment_cfg()
    if (
        bool(deployment.get("allow_public_bind"))
        and bool(deployment.get("trust_reverse_proxy"))
        and bool(deployment.get("tls_terminated_by_proxy"))
    ):
        return
    raise HTTPException(
        503,
        "public bind requires explicit reverse-proxy and TLS acknowledgement before serving traffic",
    )


@app.get("/readyz")
def readyz():
    application_lock = APPLICATION_WORKER_LOCK
    if application_lock is not None and not APP_LIFECYCLE_STARTED:
        raise HTTPException(503, "application worker lifecycle is not in a ready state")
    if APP_LIFECYCLE_STARTED:
        if application_lock is None or not application_lock.is_held:
            raise HTTPException(503, "single application worker lock is not held")
    try:
        conn = db_conn()
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        conn.close()
    except Exception as exc:
        raise HTTPException(503, "database not ready") from exc
    if not isinstance(CFG, dict) or not isinstance(gateway_cfg(), dict):
        raise HTTPException(503, "config not ready")
    admin_password = str(CFG.get("server", {}).get("admin_password") or "")
    if is_unsafe_admin_password(admin_password):
        raise HTTPException(503, "administrator credentials are not production-safe")
    validate_public_bind_readiness()
    validate_account_secret_storage_readiness()
    if task_worker_enabled():
        worker = TASK_WORKER_THREAD
        if worker is None or not worker.is_alive():
            raise HTTPException(503, "task worker not ready")
    rows = list_accounts()
    summary = account_pool_summary(rows)
    if summary["healthy"] <= 0:
        raise HTTPException(503, "no healthy account available")
    return {
        "ok": True,
        "status": "ready",
        "db": True,
        "config": True,
        "healthy_accounts": summary["healthy"],
        "usable_accounts": summary["healthy"] + summary["cooling"],
        "total_accounts": summary["total"],
    }


@app.get("/metrics")
def metrics():
    rows = list_accounts()
    account_summary = account_pool_summary(rows)
    conn = db_conn()
    task_rows = conn.execute("SELECT status,error_code FROM tasks").fetchall()
    conn.close()
    task_summary = task_metrics_summary(task_rows)
    usage_summary = usage_metrics_summary()
    return {
        "ok": True,
        "accounts": account_summary,
        "tasks": task_summary,
        "usage": usage_summary,
    }


@app.get("/v1/capabilities")
def gateway_capabilities(api_key_id: int = Depends(require_api_key)):
    policy = resolve_api_key_policy(get_api_key_record(api_key_id))
    return load_capabilities_from_pool(policy)


def openai_models_for_api_key(api_key_id: int) -> Dict[str, Any]:
    policy = resolve_api_key_policy(get_api_key_record(api_key_id))
    capabilities = load_capabilities_from_pool(policy)
    return openai_model_list(capabilities, CFG)


@app.get("/v1/models")
def gateway_openai_models(api_key_id: int = Depends(require_api_key)):
    return openai_models_for_api_key(api_key_id)


@app.get("/v1/models/{model_id}")
def gateway_openai_model_detail(model_id: str, api_key_id: int = Depends(require_api_key)):
    model_list = openai_models_for_api_key(api_key_id)
    for model in model_list.get("data") or []:
        if str(model.get("id") or "") == model_id:
            return model
    raise OpenAICompatError(
        f"The model '{model_id}' does not exist or you do not have access to it.",
        param="model",
        code="model_not_found",
        status_code=404,
    )


def gateway_task_detail_payload(task_id: int, api_key_id: Optional[int] = None) -> Dict[str, Any]:
    row = fetch_task_row(task_id, api_key_id)
    if not row:
        raise GatewayAPIError(404, "TASK_NOT_FOUND", "task not found")
    task = task_detail_for_row(row)
    conn = db_conn()
    usage_query = "SELECT * FROM usage_log WHERE task_id=?"
    params: List[Any] = [task_id]
    if api_key_id is not None:
        usage_query += " AND api_key_id=?"
        params.append(api_key_id)
    usage_query += " ORDER BY id DESC LIMIT 1"
    usage_row = conn.execute(usage_query, tuple(params)).fetchone()
    conn.close()
    payload = task.get("payload") or {}
    task["model_name"] = task.get("model_name") or (usage_row["model_name"] if usage_row else "") or payload.get("model_name") or payload.get("modelName") or ""
    task["resolution"] = task.get("resolution") or (usage_row["resolution"] if usage_row else "") or payload.get("resolution") or ""
    task["ratio"] = task.get("ratio") or (usage_row["ratio"] if usage_row else "") or payload.get("ratio") or ""
    task["duration"] = task.get("duration") or (usage_row["duration"] if usage_row else None) or payload.get("duration")
    task["scene_id"] = task.get("scene_id") or (usage_row["scene_id"] if usage_row else "") or payload.get("scene_id") or payload.get("sceneId") or ""
    task["estimated_point_cost"] = task.get("estimated_point_cost") or (usage_row["estimated_point_cost"] if usage_row else None)
    task["request_id"] = task.get("request_id") or (usage_row["request_id"] if usage_row else "")
    task["idempotency_key"] = usage_row["idempotency_key"] if usage_row else ""
    task["error_code"] = task.get("error_code") or (usage_row["error_code"] if usage_row else "")
    task["status_code"] = usage_row["status_code"] if usage_row else None
    return {"ok": True, "task": task}


def retry_task_record(
    task_id: int,
    api_key_id: Optional[int] = None,
    request_id: str = "",
) -> Dict[str, Any]:
    retry_request_id = request_id or gateway_request_id()
    with REQUEST_ADMISSION_LOCK:
        row = fetch_task_row(task_id, api_key_id)
        if not row:
            raise GatewayAPIError(404, "TASK_NOT_FOUND", "task not found")
        task = dict(row)
        if not task_retryable_status(task.get("status") or ""):
            raise GatewayAPIError(409, "TASK_NOT_RETRYABLE", "only failed or expired tasks can be retried")
        body_data = model_data(resolve_task_body(row))
        body_data["account_id"] = None
        body = GatewayGenerateIn(**body_data)
        tenant_api_key_id = int_or_default(task.get("api_key_id"), 0) or None
        policy: Optional[Dict[str, Any]] = None
        now = time.time()
        if tenant_api_key_id is not None:
            api_key_row = get_api_key_record(tenant_api_key_id)
            if not bool(api_key_row["enabled"]) or api_key_row["deleted_at"] is not None:
                raise GatewayAPIError(
                    403,
                    "API_KEY_DISABLED",
                    "the API key associated with this task is disabled",
                    request_id=retry_request_id,
                )
            policy = resolve_api_key_policy(api_key_row)
            check_rate_limit(tenant_api_key_id, policy, now, retry_request_id)

        conn = db_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            account, caps, options, estimated_point_cost = select_generation_account(
                body,
                request_id=retry_request_id,
                conn=conn,
            )
            if tenant_api_key_id is not None and policy is not None:
                enforce_api_key_scope(policy, body.kind, options, caps, retry_request_id)
                check_daily_quota(
                    tenant_api_key_id,
                    estimated_point_cost,
                    policy,
                    now,
                    retry_request_id,
                    conn=conn,
                )

            queued_response = {
                "ok": True,
                "task_id": task_id,
                "account_id": account["id"],
                "request_id": retry_request_id,
                "idempotent_replay": False,
                "estimated_point_cost": estimated_point_cost,
                "status": "queued",
            }
            source_usage = conn.execute(
                "SELECT idempotency_key FROM usage_log WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            result = conn.execute(
                """
                UPDATE tasks
                SET status='queued', account_id=?, estimated_point_cost=?, actual_point_cost=NULL,
                    error_code='', error_message='', response_json=?, assets_json=?, chat_id='', focus_id='',
                    cancel_requested_at=NULL, next_attempt_at=NULL, started_at=NULL, finished_at=NULL,
                    balance_before_json=NULL, balance_after_json=NULL,
                    balance_before_rest_point=NULL, balance_before_daily_point=NULL, balance_before_bonus_point=NULL,
                    balance_after_rest_point=NULL, balance_after_daily_point=NULL, balance_after_bonus_point=NULL,
                    updated_at=?
                WHERE id=? AND status IN ('failed', 'expired')
                """,
                (
                    account["id"],
                    estimated_point_cost,
                    encode_json_value({}),
                    encode_json_value([]),
                    now,
                    task_id,
                ),
            )
            if result.rowcount != 1:
                raise GatewayAPIError(
                    409,
                    "TASK_NOT_RETRYABLE",
                    "task state changed before the retry could be reserved",
                    request_id=retry_request_id,
                )
            if tenant_api_key_id is not None:
                insert_usage_log(
                    conn,
                    tenant_api_key_id,
                    body.kind,
                    account["id"],
                    body.prompt,
                    "queued",
                    "retry requested",
                    task_id,
                    retry_request_id,
                    str(source_usage["idempotency_key"] or "") if source_usage else "",
                    str(options.get("model_name") or ""),
                    str(options.get("resolution") or ""),
                    str(options.get("ratio") or ""),
                    int_or_default(options.get("duration"), 0) or None,
                    str(options.get("scene_id") or ""),
                    estimated_point_cost,
                    "",
                    202,
                )
            conn.execute(
                "UPDATE idempotency_keys SET status_code=202, response_json=? WHERE task_id=?",
                (json.dumps(queued_response, ensure_ascii=False), task_id),
            )
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
    TASK_WORKER_WAKE.set()
    return gateway_task_detail_payload(task_id, api_key_id)


def cancel_task_record(task_id: int, api_key_id: Optional[int] = None) -> Dict[str, Any]:
    row = fetch_task_row(task_id, api_key_id)
    if not row:
        raise GatewayAPIError(404, "TASK_NOT_FOUND", "task not found")
    task = dict(row)
    if task.get("status") == "cancelled":
        return gateway_task_detail_payload(task_id, api_key_id)
    if not task_cancellable_status(task.get("status") or ""):
        raise GatewayAPIError(
            409,
            "TASK_NOT_CANCELLABLE",
            "only active tasks can be cancelled",
            {
                "status": task.get("status") or "",
                "allowed": list(TASK_CANCELLABLE_STATUSES),
            },
        )
    now = time.time()
    conn = db_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT status,api_key_id FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if not current:
            raise GatewayAPIError(404, "TASK_NOT_FOUND", "task not found")
        current_api_key_id = current["api_key_id"]
        if api_key_id is not None:
            if current_api_key_id is None:
                legacy_owner = conn.execute(
                    "SELECT 1 FROM usage_log WHERE task_id=? AND api_key_id=? LIMIT 1",
                    (task_id, api_key_id),
                ).fetchone()
                if not legacy_owner:
                    raise GatewayAPIError(404, "TASK_NOT_FOUND", "task not found")
            elif int(current_api_key_id) != int(api_key_id):
                raise GatewayAPIError(404, "TASK_NOT_FOUND", "task not found")
        current_status = str(current["status"] or "")
        if current_status == "cancelled":
            conn.rollback()
            return gateway_task_detail_payload(task_id, api_key_id)
        if not task_cancellable_status(current_status):
            raise GatewayAPIError(
                409,
                "TASK_NOT_CANCELLABLE",
                "only active tasks can be cancelled",
                {
                    "status": current_status,
                    "allowed": list(TASK_CANCELLABLE_STATUSES),
                },
            )
        current_attempt = conn.execute(
            """
            SELECT id,attempt_no
            FROM task_attempts
            WHERE task_id=? AND status='running'
            ORDER BY attempt_no DESC,id DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        result = conn.execute(
            f"""
            UPDATE tasks
            SET status='cancelled', error_code='TASK_CANCELLED', error_message='task cancelled',
                cancel_requested_at=?, next_attempt_at=NULL, finished_at=?, updated_at=?
            WHERE id=?
              AND status IN ('queued', 'running', 'submitted', 'hydrating')
            """,
            (now, now, now, task_id),
        )
        if result.rowcount != 1:
            raise GatewayAPIError(
                409,
                "TASK_NOT_CANCELLABLE",
                "task state changed before cancellation could be reserved",
                {"status": current_status},
            )
        tenant_api_key_id = current_api_key_id or api_key_id
        if current_attempt:
            attempt_update = conn.execute(
                """
                UPDATE task_attempts
                SET status='cancelled', error_code='TASK_CANCELLED',
                    error_message='task cancelled', assets_json=?, finished_at=?
                WHERE id=? AND task_id=? AND status='running' AND attempt_no=?
                """,
                (
                    encode_json_value([]),
                    now,
                    current_attempt["id"],
                    task_id,
                    current_attempt["attempt_no"],
                ),
            )
            if attempt_update.rowcount != 1:
                raise RuntimeError("current task attempt changed during cancellation")
        usage_scope = "task_id=?"
        usage_params: List[Any] = [task_id]
        if tenant_api_key_id is not None:
            usage_scope += " AND api_key_id=?"
            usage_params.append(tenant_api_key_id)
        conn.execute(
            f"""
            UPDATE usage_log
            SET status='cancelled', response_summary='cancelled',
                error_code='TASK_CANCELLED', status_code=499
            WHERE id=(
                SELECT id FROM usage_log
                WHERE {usage_scope}
                ORDER BY id DESC LIMIT 1
            )
            """,
            tuple(usage_params),
        )
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()
    return gateway_task_detail_payload(task_id, api_key_id)


def hydrate_task_record(task_id: int, api_key_id: Optional[int] = None) -> Dict[str, Any]:
    row = fetch_task_row(task_id, api_key_id)
    if not row:
        raise GatewayAPIError(404, "TASK_NOT_FOUND", "task not found")
    task = dict(row)
    if not task_hydratable_status(task.get("status") or ""):
        raise GatewayAPIError(409, "TASK_NOT_HYDRATABLE", "only submitted tasks can be rehydrated")
    update_task_record(
        task_id,
        status="hydrating",
        error_code="",
        error_message="",
        cancel_requested_at=None,
        next_attempt_at=time.time(),
        finished_at=None,
    )
    if task.get("api_key_id"):
        update_usage_log_for_task(
            task_id,
            task.get("api_key_id"),
            status="hydrating",
            response_summary="hydrate requested",
            error_code="",
            status_code=202,
        )
    TASK_WORKER_WAKE.set()
    return gateway_task_detail_payload(task_id, api_key_id)


@app.get("/v1/tasks/{task_id}/assets/{asset_index}/clean")
def gateway_public_clean_asset(
    task_id: int,
    asset_index: int,
    signature: str = "",
):
    _task, asset_url = completed_image_task_asset(task_id, asset_index)
    expected_signature = clean_asset_signature(task_id, asset_index, asset_url)
    if not signature or not secrets.compare_digest(signature, expected_signature):
        raise HTTPException(404, "image asset not found")
    return clean_image_asset_response(
        task_id,
        asset_index,
        asset_url=asset_url,
        cache_control="public, max-age=86400, immutable",
    )


@app.get("/v1/task/{task_id}")
def gateway_task_detail(
    task_id: int,
    request: Request,
    api_key_id: int = Depends(require_api_key),
):
    return public_task_payload_for_request(
        gateway_task_detail_payload(task_id, api_key_id),
        request,
    )


@app.get("/v1/tasks/{task_id}")
def gateway_task_detail_alias(
    task_id: int,
    request: Request,
    api_key_id: int = Depends(require_api_key),
):
    return public_task_payload_for_request(
        gateway_task_detail_payload(task_id, api_key_id),
        request,
    )


@app.post("/v1/tasks/{task_id}/retry")
def gateway_task_retry(task_id: int, request: Request, api_key_id: int = Depends(require_api_key)):
    return public_task_payload_for_request(
        retry_task_record(task_id, api_key_id, gateway_request_id(request)),
        request,
    )


@app.post("/v1/tasks/{task_id}/cancel")
def gateway_task_cancel(
    task_id: int,
    request: Request,
    api_key_id: int = Depends(require_api_key),
):
    return public_task_payload_for_request(
        cancel_task_record(task_id, api_key_id),
        request,
    )


@app.post("/v1/tasks/{task_id}/hydrate")
def gateway_task_hydrate(
    task_id: int,
    request: Request,
    api_key_id: int = Depends(require_api_key),
):
    return public_task_payload_for_request(
        hydrate_task_record(task_id, api_key_id),
        request,
    )


@app.on_event("startup")
def on_startup():
    global APPLICATION_WORKER_LOCK, APP_LIFECYCLE_STARTED
    validate_single_worker_configuration(os.environ)
    application_lock = APPLICATION_WORKER_LOCK
    if application_lock is None:
        application_lock = SingleWorkerLock(worker_lock_path(DB_PATH, os.environ))
        APPLICATION_WORKER_LOCK = application_lock

    try:
        application_lock.acquire()
        if APP_LIFECYCLE_STARTED:
            return
        try:
            validate_public_bind_readiness()
        except HTTPException as exc:
            raise RuntimeError(str(exc.detail)) from exc
        init_db()
        restored_gateway_risk_accounts = restore_gateway_risk_misclassified_accounts()
        if restored_gateway_risk_accounts:
            emit_log(
                "info",
                f"已恢复 {restored_gateway_risk_accounts} 个被生成环境风控误判的账号",
            )
        if not CONFIG_PATH.exists():
            save_config(CFG)
        recover_stale_running_tasks(stale_after_seconds=0.0)
        recover_interrupted_registration_jobs()
        recover_interrupted_pool_maintenance_jobs()
        ensure_task_worker_started()
        ensure_pool_maintenance_scheduler_started()
    except BaseException:
        APP_LIFECYCLE_STARTED = False
        application_lock.release()
        if APPLICATION_WORKER_LOCK is application_lock:
            APPLICATION_WORKER_LOCK = None
        raise

    APP_LIFECYCLE_STARTED = True


@app.on_event("shutdown")
def on_shutdown() -> bool:
    global APPLICATION_WORKER_LOCK, APP_LIFECYCLE_STARTED
    shutdown_timeout = float_or_default(
        gateway_cfg().get("worker_shutdown_timeout_seconds"), 30.0
    )
    if not math.isfinite(shutdown_timeout):
        shutdown_timeout = 30.0
    task_stopped = stop_task_worker(max(0.0, shutdown_timeout))
    scheduler_stopped = stop_pool_maintenance_scheduler(max(0.0, min(5.0, shutdown_timeout)))
    worker_stopped = task_stopped and scheduler_stopped
    APP_LIFECYCLE_STARTED = False
    if not worker_stopped:
        return False

    application_lock = APPLICATION_WORKER_LOCK
    if application_lock is not None:
        application_lock.release()
        APPLICATION_WORKER_LOCK = None
    return True


@app.get("/")
def root():
    return {
        "ok": True,
        "service": "oreateai",
        "cwd": str(BASE_DIR),
        "accounts": len(list_accounts()),
    }


@app.post("/api/admin/login")
def admin_login(body: LoginIn, request: Request):
    expected_user = str(CFG["server"].get("admin_username") or "")
    expected_password = str(CFG["server"].get("admin_password") or "")
    if is_unsafe_admin_password(expected_password):
        raise HTTPException(500, "admin password must be changed before login")
    if not secrets.compare_digest(body.username, expected_user) or not secrets.compare_digest(body.password, expected_password):
        raise HTTPException(401, "invalid admin credentials")
    token = secrets.token_hex(24)
    create_admin_session(token, body.username, request)
    write_admin_audit("login", body.username, request, status_code=200, details={"username": body.username})
    return {"ok": True, "token": token}


@app.post("/api/admin/credentials")
def update_admin_credentials(body: AdminCredentialsIn, _=Depends(require_admin)):
    global CFG
    with CONFIG_LOCK:
        current_password = str(CFG["server"].get("admin_password") or "")
        if not secrets.compare_digest(body.current_password, current_password):
            raise HTTPException(401, "current password is incorrect")
        new_username = body.new_username.strip()
        if not new_username:
            raise HTTPException(400, "new username is required")
        if body.new_password != body.confirm_password:
            raise HTTPException(400, "new passwords do not match")
        if len(body.new_password) < 8 or is_unsafe_admin_password(body.new_password):
            raise HTTPException(400, "new password is too weak")
        candidate = deep_merge(
            CFG,
            {"server": {"admin_username": new_username, "admin_password": body.new_password}},
        )
        save_config(candidate)
        CFG = candidate
    revoke_all_admin_sessions("credentials_updated")
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(request: Request, _=Depends(require_admin)):
    token = getattr(request.state, "admin_session_token", "")
    if token:
        revoke_admin_session(token, "logout")
    return {"ok": True}


@app.get("/api/admin/audit-logs")
def list_admin_audit_logs(limit: int = 100, _=Depends(require_admin)):
    return {"items": [public_admin_audit(row) for row in list_admin_audit_rows(limit)]}


@app.get("/api/admin/backup")
def download_admin_backup(_=Depends(require_admin)):
    payload = build_backup_zip_bytes()
    headers = {"Content-Disposition": f'attachment; filename="oreate-backup-{int(time.time())}.zip"'}
    return StreamingResponse(io.BytesIO(payload), media_type="application/zip", headers=headers)


@app.post("/api/admin/restore")
def upload_admin_restore(
    confirm: bool = Form(False),
    file: UploadFile = File(...),
    _=Depends(require_admin),
):
    if not confirm:
        raise HTTPException(400, "restore confirmation is required")
    payload = file.file.read()
    if not payload:
        raise HTTPException(400, "backup file is empty")
    return restore_backup_zip_bytes(payload)


@app.get("/api/admin/settings")
def get_settings(_=Depends(require_admin)):
    return public_config(CFG)


@app.put("/api/admin/settings")
def put_settings(body: SettingsIn, _=Depends(require_admin)):
    global CFG
    with CONFIG_LOCK:
        if hasattr(body, "model_dump"):
            update = body.model_dump(exclude_unset=True)
        else:
            update = body.dict(exclude_unset=True)
        data = clean_settings_update(update)
        candidate = deep_merge(CFG, data)
        pool_cfg = candidate.get("pool", {})
        min_accounts = pool_cfg.get("min_accounts", 0)
        maintain_target = pool_cfg.get("maintain_target", 0)
        if maintain_target < min_accounts:
            raise HTTPException(
                status_code=422,
                detail=[
                    {
                        "type": "value_error",
                        "loc": ["body", "pool", "maintain_target"],
                        "msg": "Value error, maintain_target must be greater than or equal to min_accounts",
                        "input": maintain_target,
                    }
                ],
            )
        restart_required = candidate.get("server", {}).get("port") != CFG.get("server", {}).get("port")
        save_config(candidate)
        CFG = candidate
    # Apply pool auto-maintain changes without requiring a process restart.
    # Only touch the live scheduler when the app lifecycle is running; API-only
    # unit tests must not spawn background workers that keep the temp DB locked.
    if APP_LIFECYCLE_STARTED:
        if pool_auto_maintain_enabled():
            ensure_pool_maintenance_scheduler_started()
        else:
            stop_pool_maintenance_scheduler(0.5)
    return {
        "ok": True,
        "config": public_config(CFG),
        "restart_required": restart_required,
    }


@app.get("/api/accounts")
def api_accounts(_=Depends(require_admin)):
    return {"items": [public_account(row) for row in list_accounts()]}


@app.get("/api/pool/capacity")
def pool_capacity(_=Depends(require_admin)):
    return build_pool_capacity_snapshot()


@app.put("/api/accounts/{account_id}/reserve-target")
def update_account_reserve_target(
    account_id: int,
    body: AccountReserveTargetIn,
    _=Depends(require_admin),
):
    conn = db_conn()
    cursor = conn.execute(
        """
        UPDATE accounts
        SET reserve_target_points=?, updated_at=?
        WHERE id=?
        """,
        (body.reserve_target_points, time.time(), account_id),
    )
    updated_count = cursor.rowcount
    conn.commit()
    conn.close()
    if updated_count != 1:
        raise HTTPException(404, "account not found")
    row = next((item for item in list_accounts() if int(item["id"]) == account_id), None)
    if row is None:
        raise HTTPException(404, "account not found")
    return {"ok": True, "item": public_account(row)}


@app.get("/api/accounts/{account_id}/credentials")
def account_credentials(account_id: int, _=Depends(require_admin)):
    conn = db_conn()
    row = conn.execute(
        "SELECT id,email,password FROM accounts WHERE id=?",
        (account_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "account not found")
    return {
        "id": int(row["id"]),
        "email": str(row["email"] or ""),
        "password": decrypt_secret_value(row["password"]),
    }


@app.post("/api/accounts/{account_id}/refresh-balance")
def refresh_account_balance(account_id: int, _=Depends(require_admin)):
    conn = db_conn()
    account = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    conn.close()
    if not account:
        raise HTTPException(404, "account not found")
    try:
        session = CLIENT.session_from_account(account)
        detail = CLIENT.fetch_account_point_detail(session, account)
    except Exception as exc:
        raise HTTPException(503, str(exc))
    row = update_account_balance_snapshot(account_id, detail)
    return {"ok": True, "item": public_account(row)}


@app.post("/api/accounts/{account_id}/reactivate")
def reactivate_account_endpoint(account_id: int, _=Depends(require_admin)):
    account = account_row_by_id(account_id)
    if account is None:
        raise HTTPException(404, "account not found")
    try:
        return reactivate_account(account_id)
    except RuntimeError as exc:
        message = str(exc)
        if "does not need reactivation" in message:
            raise HTTPException(409, message) from exc
        raise HTTPException(503, message) from exc
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/accounts/purge-zombies")
def purge_zombie_accounts_endpoint(body: AccountZombiePurgeIn, _admin: str = Depends(require_admin)):
    if not body.confirm:
        raise HTTPException(400, "请确认清理僵尸号（confirm=true）")
    return purge_zombie_accounts()


@app.get("/api/models/capabilities")
def admin_model_capabilities(_=Depends(require_admin)):
    return load_capabilities_from_pool()


@app.post("/api/models/refresh")
def admin_models_refresh(_=Depends(require_admin)):
    return refresh_capabilities_from_pool()


@app.get("/api/mail/test")
def mail_test(_=Depends(require_admin)):
    result = MAIL.test_connectivity()
    if isinstance(result, dict):
        result = dict(result)
        result["outlook_pool"] = outlook_mailbox_stats()
    return result


@app.get("/api/mail/outlook")
def list_outlook_mailboxes_endpoint(
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 200,
    _=Depends(require_admin),
):
    limit = max(1, min(int(limit or 200), 1000))
    clauses = []
    params: List[Any] = []
    status_value = str(status or "").strip().lower()
    if status_value and status_value != "all":
        clauses.append("status=?")
        params.append(status_value)
    query = str(q or "").strip()
    if query:
        clauses.append("email LIKE ?")
        params.append(f"%{query}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = db_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT id, email, password, client_id, refresh_token, status, last_error,
                   leased_at, used_at, created_at, updated_at
            FROM outlook_mailboxes
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        items = [public_outlook_mailbox(row) for row in rows]
    finally:
        conn.close()
    return {
        "stats": outlook_mailbox_stats(),
        "items": items,
        "provider": str((CFG.get("mail") or {}).get("provider") or "yyds"),
        "base_url": str((CFG.get("mail") or {}).get("base_url") or ""),
        "api_mode": str((CFG.get("mail") or {}).get("api_mode") or "auto"),
    }


@app.post("/api/mail/outlook/import")
def import_outlook_mailboxes_endpoint(body: OutlookImportIn, _=Depends(require_admin)):
    return import_outlook_mailboxes(body.text, apply_detected_endpoint=body.apply_detected_endpoint)


@app.post("/api/mail/outlook/import-file")
async def import_outlook_mailboxes_file_endpoint(
    file: UploadFile = File(...),
    apply_detected_endpoint: bool = Form(True),
    _=Depends(require_admin),
):
    filename = str(file.filename or "").strip()
    suffix = Path(filename).suffix.lower() if filename else ""
    if suffix and suffix not in {".txt", ".csv", ".log", ".text"}:
        raise HTTPException(400, "仅支持导入 .txt / .csv 文本卡密文件")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(400, "卡密文件过大（上限 8MB）")
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            text = ""
    else:
        raise HTTPException(400, "无法识别文件编码，请另存为 UTF-8 文本后重试")
    result = import_outlook_mailboxes(text, apply_detected_endpoint=apply_detected_endpoint)
    result["filename"] = filename
    if int(result.get("inserted") or 0) == 0 and int(result.get("updated") or 0) == 0:
        raise HTTPException(
            400,
            "未识别到有效 Outlook 卡密行（需要 邮箱----密码----client_id----refresh_token、邮箱----密码----mail-new链接，或完整 /get 链接）",
        )
    return result


@app.post("/api/mail/outlook/purge")
def purge_outlook_mailboxes_endpoint(body: OutlookPurgeIn, _=Depends(require_admin)):
    allowed = {"available", "leased", "used", "error", "disabled"}
    statuses = [str(item).strip().lower() for item in (body.statuses or []) if str(item).strip()]
    statuses = [item for item in statuses if item in allowed]
    include_registered = bool(body.include_registered)
    if include_registered and "disabled" not in statuses:
        statuses.append("disabled")
    if not statuses and not include_registered:
        raise HTTPException(400, "请指定要清理的状态，例如 used / error")
    if include_registered:
        # Mark already-registered pool rows so status-based cleanup can catch them too.
        quarantine_burned_outlook_mailboxes()
    placeholders = ",".join("?" for _ in statuses) if statuses else ""
    conn = db_conn()
    try:
        registered_count = 0
        if include_registered:
            registered_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM outlook_mailboxes
                    WHERE EXISTS (
                        SELECT 1 FROM accounts a
                        WHERE lower(a.email)=lower(outlook_mailboxes.email)
                    )
                    """
                ).fetchone()["c"]
                or 0
            )
        if include_registered and statuses:
            deleted = conn.execute(
                f"""
                DELETE FROM outlook_mailboxes
                WHERE status IN ({placeholders})
                   OR EXISTS (
                        SELECT 1 FROM accounts a
                        WHERE lower(a.email)=lower(outlook_mailboxes.email)
                   )
                """,
                statuses,
            )
        elif include_registered:
            deleted = conn.execute(
                """
                DELETE FROM outlook_mailboxes
                WHERE EXISTS (
                    SELECT 1 FROM accounts a
                    WHERE lower(a.email)=lower(outlook_mailboxes.email)
                )
                """
            )
        else:
            deleted = conn.execute(
                f"DELETE FROM outlook_mailboxes WHERE status IN ({placeholders})",
                statuses,
            )
        count = int(deleted.rowcount or 0)
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "deleted": count,
        "deleted_registered": registered_count,
        "statuses": statuses,
        "include_registered": include_registered,
        "stats": outlook_mailbox_stats(),
    }


@app.post("/api/mail/outlook/use-for-registration")
def use_outlook_for_registration_endpoint(_=Depends(require_admin)):
    mail_cfg = deep_merge(
        CFG.get("mail") or {},
        {
            "provider": "outlook",
            "api_mode": str((CFG.get("mail") or {}).get("api_mode") or "auto"),
        },
    )
    CFG["mail"] = mail_cfg
    save_config(CFG)
    return {
        "ok": True,
        "provider": "outlook",
        "stats": outlook_mailbox_stats(),
        "base_url": str(mail_cfg.get("base_url") or ""),
    }


@app.get("/api/mail/outlook/{mailbox_id}/credentials")
def outlook_mailbox_credentials(mailbox_id: int, _=Depends(require_admin)):
    conn = db_conn()
    try:
        row = conn.execute(
            """
            SELECT id, email, password, client_id, refresh_token
            FROM outlook_mailboxes
            WHERE id=?
            """,
            (mailbox_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "outlook mailbox not found")
    return {
        "id": int(row["id"]),
        "email": str(row["email"] or ""),
        "password": decrypt_secret_value(row["password"]),
        "client_id": str(row["client_id"] or ""),
        "refresh_token": decrypt_secret_value(row["refresh_token"]),
    }


@app.post("/api/mail/outlook/{mailbox_id}/release")
def release_outlook_mailbox_endpoint(mailbox_id: int, _=Depends(require_admin)):
    conn = db_conn()
    try:
        row = conn.execute("SELECT id, status FROM outlook_mailboxes WHERE id=?", (mailbox_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, "outlook mailbox not found")
    finish_outlook_mailbox(str(mailbox_id), "available", "manual_release")
    return {"ok": True, "stats": outlook_mailbox_stats()}


@app.delete("/api/mail/outlook/{mailbox_id}")
def delete_outlook_mailbox_endpoint(mailbox_id: int, _=Depends(require_admin)):
    conn = db_conn()
    try:
        deleted = conn.execute("DELETE FROM outlook_mailboxes WHERE id=?", (mailbox_id,))
        conn.commit()
        rowcount = deleted.rowcount
    finally:
        conn.close()
    if rowcount != 1:
        raise HTTPException(404, "outlook mailbox not found")
    return {"ok": True, "stats": outlook_mailbox_stats()}


@app.post("/api/register/one")
def register_one(_=Depends(require_admin)):
    return {"items": [public_registration_result(item) for item in auto_register_accounts(1)]}


@app.post("/api/register/batch")
def register_batch(body: AutoRegisterIn, _=Depends(require_admin)):
    return {"items": [public_registration_result(item) for item in auto_register_accounts(body.count)]}


@app.post("/api/register/jobs", status_code=202)
def create_registration_job_endpoint(body: AutoRegisterIn, _=Depends(require_admin)):
    provider = str((CFG.get("mail") or {}).get("provider") or "yyds").strip().lower()
    if provider in {"outlook", "out", "hotmail", "msoauth2", "oauth2"}:
        available = int(outlook_mailbox_stats().get("available") or 0)
        if available < int(body.count):
            raise HTTPException(
                400,
                f"Outlook 可用邮箱不足：需要 {body.count}，当前可用 {available}。请先导入卡密。",
            )
    job = create_registration_job(body.count)
    launch_registration_job(job["id"])
    return {"ok": True, "job": job}


@app.get("/api/register/jobs/{job_id}")
def registration_job_detail(job_id: int, _=Depends(require_admin)):
    return {"job": get_registration_job(job_id)}


@app.post("/api/pool/maintenance/jobs", status_code=202)
def create_pool_maintenance_job_endpoint(
    body: PoolMaintenanceIn,
    _=Depends(require_admin),
):
    job = create_pool_maintenance_job(
        clean_risk=body.clean_risk,
        supplement=body.supplement,
        target_healthy=body.target_healthy,
        max_register=body.max_register,
    )
    launch_pool_maintenance_job(job["id"])
    return {"ok": True, "job": job}


@app.get("/api/pool/maintenance/jobs/{job_id}")
def pool_maintenance_job_detail(job_id: int, _=Depends(require_admin)):
    return {"job": get_pool_maintenance_job(job_id)}


@app.post("/api/accounts/import")
def import_account(body: Dict[str, str], _=Depends(require_admin)):
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
def generate_media(body: MediaTaskIn, request: Request, _=Depends(require_admin)):
    if body.kind not in ("image", "video"):
        raise HTTPException(400, f"unsupported kind: {body.kind}")
    gateway_body = GatewayGenerateIn(
        kind=body.kind,
        prompt=body.prompt,
        model_name=body.model_name,
        ratio=body.ratio,
        resolution=body.resolution,
        duration=body.duration,
        scene_id=body.scene_id,
        account_id=body.account_id,
        image=body.image,
        first_frame=body.first_frame,
        last_frame=body.last_frame,
        reference_images=body.reference_images,
        reference_videos=body.reference_videos,
        motion_video=body.motion_video,
        character_image=body.character_image,
        ref_duration=body.ref_duration,
        ref_total_duration=body.ref_total_duration,
        motion_duration=body.motion_duration,
        keep_original_sound=body.keep_original_sound,
        is_audio=body.is_audio,
        ai_type=body.ai_type,
    )
    task_id, account, _, _, estimated_point_cost = admit_generation_task(
        None,
        gateway_request_id(request),
        gateway_body,
    )
    return {"ok": True, "task_id": task_id, "status": "queued", "account_id": account["id"], "estimated_point_cost": estimated_point_cost}


@app.get("/api/tasks")
def list_tasks(
    limit: int = 200,
    offset: int = 0,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    model_name: Optional[str] = None,
    scene_id: Optional[str] = None,
    api_key_id: Optional[int] = None,
    client_id: Optional[int] = None,
    account_id: Optional[int] = None,
    error_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    _=Depends(require_admin),
):
    if limit < 1 or limit > MAX_LIST_LIMIT:
        raise HTTPException(422, f"limit must be between 1 and {MAX_LIST_LIMIT}")
    if offset < 0 or offset > MAX_LIST_OFFSET:
        raise HTTPException(422, f"offset must be between 0 and {MAX_LIST_OFFSET}")
    if kind and kind not in API_LIST_KINDS:
        raise HTTPException(422, "kind is not supported")
    if status and status not in TASK_LIST_STATUSES:
        raise HTTPException(422, "status is not supported")
    where = ["1=1"]
    params: List[Any] = []
    start = parse_report_date_boundary(date_from, end_of_day=False)
    end = parse_report_date_boundary(date_to, end_of_day=True)
    if start is not None:
        where.append("t.created_at>=?")
        params.append(start)
    if end is not None:
        where.append("t.created_at<=?")
        params.append(end)
    if status:
        where.append("t.status=?")
        params.append(status)
    if kind:
        where.append("t.kind=?")
        params.append(kind)
    if model_name:
        where.append("t.model_name=?")
        params.append(model_name)
    if scene_id:
        where.append("t.scene_id=?")
        params.append(scene_id)
    if api_key_id is not None:
        where.append("t.api_key_id=?")
        params.append(api_key_id)
    if client_id is not None:
        where.append("k.client_id=?")
        params.append(client_id)
    if account_id is not None:
        where.append("t.account_id=?")
        params.append(account_id)
    if error_code:
        where.append("t.error_code=?")
        params.append(error_code)
    where_sql = " AND ".join(where)
    from_sql = """
        FROM tasks t
        LEFT JOIN accounts a ON t.account_id=a.id
        LEFT JOIN api_keys k ON t.api_key_id=k.id
        LEFT JOIN clients c ON k.client_id=c.id
    """
    total, rows = execute_paginated_admin_query(
        f"SELECT COUNT(*) {from_sql} WHERE {where_sql}",
        f"""
        SELECT
            t.*,
            a.email AS account_email,
            k.name AS api_key_name,
            c.name AS client_name
        {from_sql}
        WHERE {where_sql}
        ORDER BY t.id DESC
        LIMIT ? OFFSET ?
        """,
        params,
        limit,
        offset,
    )
    items = [task_row_to_public(r) for r in rows[:limit]]
    return {"items": items, "limit": limit, "offset": offset, "total": total, "has_more": len(rows) > limit}


@app.get("/api/tasks/{task_id}")
def admin_task_detail(task_id: int, _=Depends(require_admin)):
    return gateway_task_detail_payload(task_id)


@app.get("/api/tasks/{task_id}/assets/{asset_index}/clean")
def admin_task_clean_asset(task_id: int, asset_index: int, _=Depends(require_admin)):
    return clean_image_asset_response(
        task_id,
        asset_index,
        cache_control="private, max-age=3600",
    )


@app.post("/api/tasks/{task_id}/retry")
def admin_task_retry(task_id: int, request: Request, _=Depends(require_admin)):
    return retry_task_record(task_id, request_id=gateway_request_id(request))


@app.post("/api/tasks/{task_id}/cancel")
def admin_task_cancel(task_id: int, _=Depends(require_admin)):
    return cancel_task_record(task_id)


@app.post("/api/tasks/{task_id}/hydrate")
def admin_task_hydrate(task_id: int, _=Depends(require_admin)):
    return hydrate_task_record(task_id)


@app.post("/api/tasks/{task_id}/mark")
def mark_task(task_id: int, body: Dict[str, Any], _=Depends(require_admin)):
    status = str(body.get("status", "")).strip()
    if not status:
        raise HTTPException(400, "status required")
    update_task_status(task_id, status, body.get("response"))
    return {"ok": True, "task_id": task_id, "status": status}


@app.post("/api/pool/maintain")
def pool_maintain(body: MaintainIn, _=Depends(require_admin)):
    accounts = list_accounts()
    summary = account_pool_summary(accounts)
    created = []
    target = max(0, int_or_default(CFG["pool"].get("maintain_target"), 5))
    deficit = max(0, target - summary["healthy"])
    if body.force_register and deficit == 0 and body.max_register > 0:
        deficit = 1
    need = min(body.max_register, deficit)
    if need > 0:
        created = auto_register_accounts(need)
    return {
        "ok": True,
        "accounts_total": len(accounts),
        "verified_total": summary["verified"],
        "healthy_total": summary["healthy"],
        "created": [public_registration_result(item) for item in created],
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



@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return HTMLResponse(
        ADMIN_HTML,
        headers={
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https://cdn.oreateai.com; "
                "media-src 'self' https://cdn.oreateai.com; "
                "connect-src 'self' ws: wss:; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=CFG["server"]["host"],
        port=int(CFG["server"]["port"]),
        workers=1,
    )
