import asyncio
import io
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import shutil
import tempfile
import threading
import time
import zipfile
from urllib.parse import parse_qs, quote, urlparse
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import requests
import re
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from banti_token_generator import generate_banti_artifacts, generate_jt_token

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "accounts.db"
SECRET_PLACEHOLDER = "__redacted__"
UNSAFE_ADMIN_PASSWORDS = {"", "admin123", "CHANGE_ME", "changeme", "password"}

DEFAULT_CONFIG = {
    "server": {
        "host": "127.0.0.1",
        "port": 8890,
        "admin_username": "admin",
        "admin_password": "",
        "admin_session_ttl_hours": 12,
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
    "gateway": {
        "default_rate_limit_per_minute": 60,
        "default_daily_request_limit": 0,
        "default_daily_point_limit": 0,
        "idempotency_ttl_hours": 24,
        "account_cooldown_seconds": 300,
        "prompt_max_length": 4000,
        "sync_wait_seconds": 0,
        "enable_background_worker": True,
        "task_worker_poll_interval_seconds": 1,
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


def gateway_cfg() -> Dict[str, Any]:
    return CFG.get("gateway", {}) if isinstance(CFG.get("gateway", {}), dict) else {}


def oreate_cfg() -> Dict[str, Any]:
    return CFG.get("oreate", {}) if isinstance(CFG.get("oreate", {}), dict) else {}


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
    if out.get("mail", {}).get("api_key"):
        out["mail"]["api_key"] = SECRET_PLACEHOLDER
    return out


def clean_settings_update(data: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(data))
    server_cfg = out.get("server")
    if isinstance(server_cfg, dict):
        server_cfg.pop("admin_username", None)
        server_cfg.pop("admin_password", None)
    mail_cfg = out.get("mail")
    if isinstance(mail_cfg, dict) and mail_cfg.get("api_key") in (None, "", SECRET_PLACEHOLDER):
        mail_cfg.pop("api_key", None)
    return out


def public_account(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["has_password"] = bool(item.get("password"))
    item["ouid_preview"] = (item.get("ouid") or "")[:12]
    for key in ("password", "ouid", "ouss", "model_info_json", "video_info_json", "point_balance_json"):
        item.pop(key, None)
    now = time.time()
    item["cooling"] = account_cooldown_remaining_seconds(item, now) > 0
    item["cooldown_remaining_seconds"] = int(account_cooldown_remaining_seconds(item, now))
    item["balance_status"] = account_balance_status(item)
    item["risk_status"] = account_risk_status(item)
    item["health_status"] = account_health_status(item, now)
    return item


def public_api_key(row: sqlite3.Row, reveal: bool = False) -> Dict[str, Any]:
    item = dict(row)
    key = item.get("key", "")
    item["key_preview"] = f"{key[:16]}..." if key else ""
    item["deleted"] = bool(item.get("deleted_at"))
    item["status"] = "deleted" if item["deleted"] else ("enabled" if item.get("enabled") else "disabled")
    if item.get("client_name") is None and item.get("client_id") is not None:
        item["client_name"] = ""
    item["client_name"] = item.get("client_name") or ""
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
    rest = source.get("restPoint")
    if rest in (None, ""):
        rest = source.get("rest_point")
    if rest in (None, ""):
        rest = source.get("restpoint")
    if rest in (None, "") and (daily not in (None, "") or bonus not in (None, "")):
        rest = int_or_default(daily, 0) + int_or_default(bonus, 0)
    return {
        "point_balance_json": {
            "daily_point": None if daily in (None, "") else int_or_default(daily, 0),
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


def account_has_sufficient_balance(row: sqlite3.Row, estimated_point_cost: Optional[int]) -> bool:
    if estimated_point_cost in (None, ""):
        return True
    try:
        cost = int(float(estimated_point_cost))
    except (TypeError, ValueError):
        return True
    if cost <= 0:
        return True
    balance = account_balance_value(row)
    if balance is None:
        return True
    return balance >= cost


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
        return "risk_control" if "212361" in str(row["last_error"] or "") else "invalid"
    if str(row["last_error"] or "").find("212361") >= 0:
        return "risk_control"
    return "clean"


def account_health_status(row: sqlite3.Row, now: Optional[float] = None) -> str:
    if str(row["status"] or "") == "invalid":
        return "invalid"
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
    params = parse_qs(urlparse(link).query)
    return params.get("tokenID", [""])[0]


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
MEDIA_UPLOAD_EXTENSIONS = {"jpg", "jpeg", "png", "bmp", "webp", "mp4", "mov"}


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


def normalized_file_extension(value: Any) -> str:
    return str(value or "").lstrip(".").lower()


def is_media_upload_extension(value: Any) -> bool:
    return normalized_file_extension(value) in MEDIA_UPLOAD_EXTENSIONS


def response_data_object(body: Any) -> Dict[str, Any]:
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


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


def first_upload_key_entry(key_list: Any) -> Dict[str, Any]:
    if isinstance(key_list, list) and key_list:
        first = key_list[0]
        return first if isinstance(first, dict) else {}
    if isinstance(key_list, dict) and key_list:
        for key in sorted(key_list.keys(), key=str):
            first = key_list[key]
            return first if isinstance(first, dict) else {}
    return {}


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
        "ratio": (options.get("ratio") or "") if should_send_model_option(model, "ratios") else "",
        "resolution": str(options.get("resolution") or "") if should_send_model_option(model, "resolutions") else "",
        "isAudio": bool(options.get("is_audio")) if (not isinstance(model, dict) or model.get("supports_audio")) else False,
        "scene": scene_id,
    }
    if should_send_model_option(model, "durations"):
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


def extract_user_mirror_metadata(body: Any, fallback_email: str = "") -> Dict[str, Any]:
    source = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else body
    source = source if isinstance(source, dict) else {}
    basic = source.get("basicInfo") or source.get("userInfo") or {}
    vip = source.get("vipInfo") or {}
    if not isinstance(basic, dict):
        basic = {}
    if not isinstance(vip, dict):
        vip = {}
    vip_type = vip.get("vipType")
    return {
        "email": basic.get("email") or fallback_email or "",
        "vip": "" if vip_type is None else str(vip_type),
        "reg_ts": basic.get("createTime") or "",
    }


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


def save_idempotency_record(api_key_id: int, idempotency_key: str, request_hash: str, status_code: int, response: Dict[str, Any], task_id: Optional[int]) -> None:
    if not idempotency_key:
        return
    conn = db_conn()
    conn.execute(
        """
        INSERT OR IGNORE INTO idempotency_keys(api_key_id,idempotency_key,request_hash,status_code,response_json,task_id,created_at)
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


def resolve_api_key_policy(row: sqlite3.Row) -> Dict[str, int]:
    gateway_cfg = CFG.get("gateway", {})
    return {
        "rate_limit_per_minute": int(row["rate_limit_per_minute"] or gateway_cfg.get("default_rate_limit_per_minute") or 0),
        "daily_request_limit": int(row["daily_request_limit"] or gateway_cfg.get("default_daily_request_limit") or 0),
        "daily_point_limit": int(row["daily_point_limit"] or gateway_cfg.get("default_daily_point_limit") or 0),
    }


def check_rate_limit(api_key_id: int, policy: Dict[str, int], now: float, request_id: str) -> None:
    limit = policy.get("rate_limit_per_minute") or 0
    if limit <= 0:
        return
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


def check_daily_quota(api_key_id: int, estimated_point_cost: Optional[int], policy: Dict[str, int], now: float, request_id: str) -> None:
    start = day_start_timestamp(now)
    conn = db_conn()
    row = conn.execute(
        """
        SELECT COUNT(*) as request_count, COALESCE(SUM(estimated_point_cost), 0) as point_count
        FROM usage_log
        WHERE api_key_id=? AND created_at>=?
        """,
        (api_key_id, start),
    ).fetchone()
    conn.close()
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


def candidate_accounts_for_generation(kind: str, requested_account_id: Optional[int] = None) -> List[sqlite3.Row]:
    now = time.time()
    capability_clause = "model_info_json IS NOT NULL AND model_info_json != ''" if kind == "image" else "video_info_json IS NOT NULL AND video_info_json != ''"
    params: List[Any] = [now]
    account_clause = ""
    if requested_account_id:
        account_clause = "AND id=?"
        params.append(requested_account_id)
    conn = db_conn()
    rows = conn.execute(
        f"""
        SELECT * FROM accounts
        WHERE status IN ('verified', 'active')
          AND ({capability_clause})
          AND (cooldown_until IS NULL OR cooldown_until <= ?)
          {account_clause}
        ORDER BY COALESCE(failure_count, 0) ASC, COALESCE(last_used_at, 0) ASC, updated_at DESC, id ASC
        """,
        tuple(params),
    ).fetchall()
    conn.close()
    return list(rows)


def pick_account_for_generation(kind: str, requested_account_id: Optional[int] = None) -> Optional[sqlite3.Row]:
    rows = candidate_accounts_for_generation(kind, requested_account_id)
    return rows[0] if rows else None


def select_generation_account(body: Any, request_id: str = "") -> Tuple[sqlite3.Row, Dict[str, Any], Dict[str, Any], Optional[int]]:
    candidates = candidate_accounts_for_generation(body.kind, getattr(body, "account_id", None))
    last_validation_error: Optional[GatewayAPIError] = None
    had_balance_miss = False
    for account in candidates:
        caps = capabilities_from_account(account)
        try:
            options = effective_generation_options(body, caps)
            validate_generation_options(body.kind, options, caps)
        except GatewayAPIError as exc:
            last_validation_error = exc
            continue
        estimated_point_cost = estimate_point_cost(body.kind, options, caps)
        if not account_has_sufficient_balance(account, estimated_point_cost):
            had_balance_miss = True
            continue
        return account, caps, options, estimated_point_cost
    if last_validation_error is not None and not had_balance_miss and candidates:
        if request_id:
            last_validation_error.request_id = request_id
        raise last_validation_error
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
    match = re.search(r"\b\d{5,6}\b", str(error))
    return match.group(0) if match else ""


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
    if code == "110012":
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
    cooldown_seconds = int(CFG.get("gateway", {}).get("account_cooldown_seconds") or 300) * min(next_failure_count, 6)
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
TASK_WORKER_LOCK = threading.Lock()
TASK_WORKER_THREAD: Optional[threading.Thread] = None
TASK_WORKER_STOP = threading.Event()
TASK_WORKER_WAKE = threading.Event()


def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
            point_balance_json TEXT,
            rest_point INTEGER,
            daily_point INTEGER,
            bonus_point INTEGER,
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
            deleted_at REAL,
            disabled_reason TEXT,
            created_at REAL NOT NULL,
            last_used_at REAL
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
    add_column_if_missing(conn, "api_keys", "rate_limit_per_minute", "INTEGER")
    add_column_if_missing(conn, "api_keys", "daily_request_limit", "INTEGER")
    add_column_if_missing(conn, "api_keys", "daily_point_limit", "INTEGER")
    add_column_if_missing(conn, "api_keys", "client_id", "INTEGER")
    add_column_if_missing(conn, "api_keys", "deleted_at", "REAL")
    add_column_if_missing(conn, "api_keys", "disabled_reason", "TEXT")
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
    add_column_if_missing(conn, "accounts", "balance_updated_at", "REAL")
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
        read_timeout = float(CFG["oreate"].get("video_stream_read_timeout_seconds") or min(base_timeout, 20.0))
        return (self.timeout, read_timeout)

    def new_session(self) -> requests.Session:
        s = requests.Session()
        s.verify = tls_verify_enabled()
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
            self._set_cookie_unique(s, "OUID", account["ouid"])
        if account["ouss"]:
            self._set_cookie_unique(s, "ouss", account["ouss"])
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
        if is_media_upload:
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
            banti_artifacts = generate_banti_artifacts()
            helper_cookies = banti_artifacts.get("cookies") if isinstance(banti_artifacts.get("cookies"), dict) else {}
            bid = helper_cookies.get("__bid_n")
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
        stream_wait = float(CFG["oreate"].get("video_stream_wait_seconds") or 60)
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
            CFG["oreate"].get("video_hydration_timeout_seconds") or 600
            if timeout_sec is None
            else timeout_sec
        )
        interval = float(
            CFG["oreate"].get("video_hydration_poll_interval_seconds") or 10
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


TASK_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "expired"}


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
) -> int:
    now = time.time()
    if next_attempt_at is None and status in {"submitted", "hydrating"}:
        next_attempt_at = now
    chat = task_response_chat(response)
    assets = task_response_assets(response)
    conn = db_conn()
    conn.execute(
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
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    task_id = row[0]
    conn.close()
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


def task_worker_enabled() -> bool:
    return bool(gateway_cfg().get("enable_background_worker", True))


def task_worker_poll_interval() -> float:
    try:
        return float(gateway_cfg().get("task_worker_poll_interval_seconds") or 1)
    except (TypeError, ValueError):
        return 1.0


def resolve_task_body(task: sqlite3.Row):
    payload = json_value_from_db(task["payload_json"]) or {}
    if not isinstance(payload, dict):
        raise RuntimeError("task payload is malformed")
    return GatewayGenerateIn(**payload)


def task_retryable_status(status: str) -> bool:
    return status in {"failed", "expired"}


def task_hydratable_status(status: str) -> bool:
    return status in {"submitted", "hydrating"}


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
    update_task_attempt(
        attempt_id,
        status="cancelled",
        error_code="TASK_CANCELLED",
        error_message=message,
        assets_json=[],
        finished_at=now,
    )
    update_task_record(
        task["id"],
        status="cancelled",
        error_code="TASK_CANCELLED",
        error_message=message,
        finished_at=now,
        next_attempt_at=None,
    )
    if task.get("api_key_id"):
        update_usage_log_for_task(
            task["id"],
            task.get("api_key_id"),
            status="cancelled",
            response_summary="cancelled",
            error_code="TASK_CANCELLED",
            status_code=499,
        )


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
    balance_before = capture_account_balance_snapshot(account_row)
    if balance_before:
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
    update_task_attempt(
        attempt_id,
        status=status,
        error_code=result.get("error_code") or "",
        error_message=result.get("error_message") or "",
        stream_summary_json=result.get("stream_summary"),
        hydration_summary_json=result.get("hydration_summary"),
        assets_json=result.get("assets") or [],
        finished_at=now,
    )
    update_task_record(
        task["id"],
        status=status,
        account_id=result.get("account_id", task.get("account_id")),
        chat_id=result.get("chat_id", task.get("chat_id") or ""),
        focus_id=result.get("focus_id", task.get("focus_id") or ""),
        response_json=result.get("response_json"),
        assets_json=result.get("assets") or [],
        error_code=result.get("error_code") or "",
        error_message=result.get("error_message") or "",
        actual_point_cost=result.get("actual_point_cost"),
        balance_before_json=result.get("balance_before_json"),
        balance_after_json=result.get("balance_after_json"),
        balance_before_rest_point=result.get("balance_before_rest_point"),
        balance_before_daily_point=result.get("balance_before_daily_point"),
        balance_before_bonus_point=result.get("balance_before_bonus_point"),
        balance_after_rest_point=result.get("balance_after_rest_point"),
        balance_after_daily_point=result.get("balance_after_daily_point"),
        balance_after_bonus_point=result.get("balance_after_bonus_point"),
        next_attempt_at=next_attempt_at,
        finished_at=now if status in TASK_TERMINAL_STATUSES else None,
    )
    if task.get("api_key_id"):
        update_usage_log_for_task(
            task["id"],
            task.get("api_key_id"),
            status=status,
            response_summary=result.get("response_summary") or status,
            error_code=result.get("error_code") or "",
            actual_point_cost=result.get("actual_point_cost"),
            status_code=result.get("status_code") or (200 if status == "completed" else 202 if status in {"queued", "submitted", "hydrating"} else 503),
        )


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
        result_payload = build_failed_task_result_payload(task["id"], task.get("account_id"), code, message, status_code=503)
        if task.get("account_id"):
            mark_account_failure(task["account_id"], exc)
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


def load_capabilities_from_pool() -> Dict[str, Any]:
    account = pick_account_for_capabilities()
    if not account:
        raise HTTPException(503, "no account with model capabilities available")
    return capability_response_from_account(account)


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
    assets = hydration.get("assets") or []
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
                            token_id = extract_token_id_from_link(link)
                            trace.append({"step": "extract_token_from_link", "tokenID": token_id, "link": link})
                            # Visit the verification link (click it) - REQUIRED for email to be marked verified
                            try:
                                vr = requests.get(link, verify=tls_verify_enabled(), timeout=10, allow_redirects=True)
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


class GatewayAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Optional[Dict[str, Any]] = None, request_id: str = ""):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.request_id = request_id


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
    if request is not None:
        incoming = request.headers.get("X-Request-ID")
        if incoming:
            return incoming
    return "req_" + secrets.token_hex(8)


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
    return gateway_error_response(
        exc.request_id or gateway_request_id(request),
        exc.status_code,
        exc.code,
        exc.message,
        exc.details,
    )


@app.exception_handler(HTTPException)
def handle_http_exception(request: Request, exc: HTTPException):
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

def require_api_key(request: Request, api_key_id: Optional[int] = Depends(get_api_key)):
    if api_key_id is None:
        raise GatewayAPIError(
            401,
            "UNAUTHORIZED",
            "valid API key required (header: Authorization: Bearer <key>)",
            request_id=gateway_request_id(request),
        )
    return api_key_id

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
    conn.execute(
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
    conn.commit()
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    conn.close()
    return row[0] if row else None


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
            CFG = deep_merge(DEFAULT_CONFIG, restored_cfg)
            save_config(CFG)
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
    gateway_cfg_section = CFG.setdefault("gateway", {})
    policies = gateway_cfg_section.setdefault(section, {})
    current = policies.get(key, {})
    if not isinstance(current, dict):
        current = {}
    merged = resolve_policy(current, patch)
    policies[key] = merged
    save_config(CFG)
    return merged


def get_client_record(client_id: int) -> sqlite3.Row:
    conn = db_conn()
    row = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "client not found")
    return row


@app.get("/api/admin/apikeys")
def list_api_keys(_=Depends(require_admin)):
    conn = db_conn()
    rows = conn.execute(
        """
        SELECT api_keys.*, clients.name AS client_name, clients.contact AS client_contact, clients.status AS client_status
        FROM api_keys
        LEFT JOIN clients ON clients.id=api_keys.client_id
        ORDER BY api_keys.id DESC
        """
    ).fetchall()
    conn.close()
    return {"items": [public_api_key(r) for r in rows]}


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
    name = payload.get("name", "")
    client_id = payload.get("client_id")
    client_id_value = None
    if client_id not in (None, ""):
        try:
            client_id_value = int(client_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "client_id must be an integer")
        get_client_record(client_id_value)
    key = "oreate_" + secrets.token_hex(24)
    conn = db_conn()
    conn.execute(
        "INSERT INTO api_keys (client_id, key, name, enabled, created_at) VALUES (?,?,?,?,?)",
        (client_id_value, key, name, 1, time.time()),
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
    def limit_value(name: str) -> Optional[int]:
        value = body.get(name)
        if value in (None, ""):
            return None
        value = int(value)
        if value < 0:
            raise HTTPException(400, f"{name} must be non-negative")
        return value

    conn = db_conn()
    current = conn.execute("SELECT client_id FROM api_keys WHERE id=?", (key_id,)).fetchone()
    if not current:
        conn.close()
        raise HTTPException(404, "api key not found")
    client_id = body.get("client_id") if isinstance(body, dict) else None
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
    conn.execute(
        """
        UPDATE api_keys
        SET client_id=?, rate_limit_per_minute=?, daily_request_limit=?, daily_point_limit=?
        WHERE id=?
        """,
        (
            client_id_value,
            limit_value("rate_limit_per_minute"),
            limit_value("daily_request_limit"),
            limit_value("daily_point_limit"),
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
    )
    if api_key_id is not None:
        log_usage(
            api_key_id,
            body.kind,
            account["id"],
            body.prompt,
            "queued",
            task_id=task_id,
            request_id=request_id,
            model_name=options.get("model_name") or "",
            resolution=options.get("resolution") or "",
            ratio=options.get("ratio") or "",
            duration=options.get("duration"),
            scene_id=options.get("scene_id") or "",
            estimated_point_cost=estimated_point_cost,
            status_code=202,
        )
    if task_worker_enabled():
        TASK_WORKER_WAKE.set()
    return task_id


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
    request_hash = request_hash_for_generation(body)
    if idempotency_key:
        existing_idempotency = find_idempotency_record(api_key_id, idempotency_key)
        if existing_idempotency:
            if existing_idempotency["request_hash"] != request_hash:
                raise GatewayAPIError(
                    409,
                    "IDEMPOTENCY_KEY_CONFLICT",
                    "Idempotency-Key was already used with a different request body",
                    {"field": "Idempotency-Key"},
                    request_id=request_id,
                )
            replay = json.loads(existing_idempotency["response_json"])
            replay["idempotent_replay"] = True
            replay["request_id"] = request_id
            return JSONResponse(status_code=existing_idempotency["status_code"], content=replay)
    policy = resolve_api_key_policy(get_api_key_record(api_key_id))
    now = time.time()
    check_rate_limit(api_key_id, policy, now, request_id)
    if body.kind not in ("image", "video"):
        raise GatewayAPIError(400, "UNSUPPORTED_KIND", f"unsupported kind: {body.kind}", {"field": "kind"}, request_id=request_id)
    account, caps, options, estimated_point_cost = select_generation_account(body, request_id=request_id)
    check_daily_quota(api_key_id, estimated_point_cost, policy, now, request_id)

    task_id = queue_generation_task(api_key_id, request_id, account, body, options, estimated_point_cost)
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
    sync_wait_seconds = body.sync_wait_seconds if body.sync_wait_seconds is not None else gateway_cfg().get("sync_wait_seconds") or 0
    if sync_wait_seconds and float(sync_wait_seconds) > 0:
        snapshot = wait_for_task_snapshot(task_id, api_key_id, float(sync_wait_seconds))
        result["status"] = snapshot.get("status") or result["status"]
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
    return JSONResponse(status_code=status_code, content=result)


@app.post("/v1/uploads")
async def gateway_upload(
    request: Request,
    file: UploadFile = File(...),
    account_id: Optional[int] = Form(None),
    api_key_id: int = Depends(require_api_key),
):
    """Upload a local file to the same BOS object path format used by web video scenes."""
    request_id = gateway_request_id(request)
    account = pick_account_for_generation("video", account_id) or pick_account_for_generation("image", account_id)
    if not account:
        raise GatewayAPIError(503, "NO_ACCOUNT_AVAILABLE", "no verified account available", request_id=request_id)
    data = await file.read()
    if not data:
        raise GatewayAPIError(400, "EMPTY_UPLOAD", "uploaded file is empty", {"field": "file"}, request_id=request_id)
    try:
        session = CLIENT.session_from_account(account)
        attachment = CLIENT.upload_file_bytes(
            session,
            file.filename or "upload.bin",
            data,
            file.content_type or "application/octet-stream",
        )
    except Exception as e:
        mark_account_failure(account["id"], e)
        raise GatewayAPIError(503, "UPLOAD_FAILED", str(e), request_id=request_id)
    mark_account_success(account["id"])
    return {
        "ok": True,
        "request_id": request_id,
        "account_id": account["id"],
        "attachment": attachment,
        "message_attachment": normalize_upload_attachment(attachment),
    }


@app.get("/v1/tasks")
def gateway_tasks(api_key_id: int = Depends(require_api_key)):
    """List tasks created by this API key."""
    conn = db_conn()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE api_key_id=? ORDER BY id DESC LIMIT 50",
        (api_key_id,),
    ).fetchall()
    conn.close()
    return {"items": [task_row_to_public(r) for r in rows]}


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


@app.get("/readyz")
def readyz():
    try:
        conn = db_conn()
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
        conn.close()
    except Exception as exc:
        raise HTTPException(503, f"database not ready: {exc}")
    if not isinstance(CFG, dict) or not isinstance(gateway_cfg(), dict):
        raise HTTPException(503, "config not ready")
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
    return load_capabilities_from_pool()


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


def retry_task_record(task_id: int, api_key_id: Optional[int] = None) -> Dict[str, Any]:
    row = fetch_task_row(task_id, api_key_id)
    if not row:
        raise GatewayAPIError(404, "TASK_NOT_FOUND", "task not found")
    task = dict(row)
    if not task_retryable_status(task.get("status") or ""):
        raise GatewayAPIError(409, "TASK_NOT_RETRYABLE", "only failed or expired tasks can be retried")
    update_task_record(
        task_id,
        status="queued",
        error_code="",
        error_message="",
        response_json={},
        assets_json=[],
        chat_id="",
        focus_id="",
        cancel_requested_at=None,
        next_attempt_at=None,
        started_at=None,
        finished_at=None,
    )
    if task.get("api_key_id"):
        update_usage_log_for_task(
            task_id,
            task.get("api_key_id"),
            status="queued",
            response_summary="retry requested",
            error_code="",
            status_code=202,
        )
    TASK_WORKER_WAKE.set()
    return gateway_task_detail_payload(task_id, api_key_id)


def cancel_task_record(task_id: int, api_key_id: Optional[int] = None) -> Dict[str, Any]:
    row = fetch_task_row(task_id, api_key_id)
    if not row:
        raise GatewayAPIError(404, "TASK_NOT_FOUND", "task not found")
    task = dict(row)
    if task.get("status") == "cancelled":
        return gateway_task_detail_payload(task_id, api_key_id)
    now = time.time()
    update_task_record(
        task_id,
        status="cancelled",
        error_code="TASK_CANCELLED",
        error_message="task cancelled",
        cancel_requested_at=now,
        next_attempt_at=None,
        finished_at=now,
    )
    if task.get("api_key_id"):
        update_usage_log_for_task(
            task_id,
            task.get("api_key_id"),
            status="cancelled",
            response_summary="cancelled",
            error_code="TASK_CANCELLED",
            status_code=499,
        )
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


@app.get("/v1/task/{task_id}")
def gateway_task_detail(task_id: int, api_key_id: int = Depends(require_api_key)):
    return gateway_task_detail_payload(task_id, api_key_id)


@app.get("/v1/tasks/{task_id}")
def gateway_task_detail_alias(task_id: int, api_key_id: int = Depends(require_api_key)):
    return gateway_task_detail_payload(task_id, api_key_id)


@app.post("/v1/tasks/{task_id}/retry")
def gateway_task_retry(task_id: int, api_key_id: int = Depends(require_api_key)):
    return retry_task_record(task_id, api_key_id)


@app.post("/v1/tasks/{task_id}/cancel")
def gateway_task_cancel(task_id: int, api_key_id: int = Depends(require_api_key)):
    return cancel_task_record(task_id, api_key_id)


@app.post("/v1/tasks/{task_id}/hydrate")
def gateway_task_hydrate(task_id: int, api_key_id: int = Depends(require_api_key)):
    return hydrate_task_record(task_id, api_key_id)


@app.on_event("startup")
def on_startup():
    init_db()
    if not CONFIG_PATH.exists():
        save_config(CFG)
    ensure_task_worker_started()


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
    CFG = deep_merge(CFG, {"server": {"admin_username": new_username, "admin_password": body.new_password}})
    save_config(CFG)
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
    data = clean_settings_update(model_data(body))
    CFG = deep_merge(CFG, data)
    save_config(CFG)
    return {"ok": True, "config": public_config(CFG)}


@app.get("/api/accounts")
def api_accounts(_=Depends(require_admin)):
    return {"items": [public_account(row) for row in list_accounts()]}


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


@app.get("/api/models/capabilities")
def admin_model_capabilities(_=Depends(require_admin)):
    return load_capabilities_from_pool()


@app.post("/api/models/refresh")
def admin_models_refresh(_=Depends(require_admin)):
    return refresh_capabilities_from_pool()


@app.get("/api/mail/test")
def mail_test(_=Depends(require_admin)):
    return MAIL.test_connectivity()


@app.post("/api/register/one")
def register_one(_=Depends(require_admin)):
    return {"items": [public_registration_result(item) for item in auto_register_accounts(1)]}


@app.post("/api/register/batch")
def register_batch(body: AutoRegisterIn, _=Depends(require_admin)):
    return {"items": [public_registration_result(item) for item in auto_register_accounts(body.count)]}


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
    try:
        account, caps, options, estimated_point_cost = select_generation_account(gateway_body)
    except GatewayAPIError as exc:
        raise HTTPException(exc.status_code, exc.message)
    task_id = queue_generation_task(None, gateway_request_id(request), account, gateway_body, options, estimated_point_cost)
    return {"ok": True, "task_id": task_id, "status": "queued", "account_id": account["id"], "estimated_point_cost": estimated_point_cost}


@app.get("/api/tasks")
def list_tasks(_=Depends(require_admin)):
    conn = db_conn()
    rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
    conn.close()
    return {"items": [task_row_to_public(r) for r in rows]}


@app.get("/api/tasks/{task_id}")
def admin_task_detail(task_id: int, _=Depends(require_admin)):
    return gateway_task_detail_payload(task_id)


@app.post("/api/tasks/{task_id}/retry")
def admin_task_retry(task_id: int, _=Depends(require_admin)):
    return retry_task_record(task_id)


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
    verified = [a for a in accounts if a.get("status") in ("verified", "active")]
    created = []
    if len(verified) < CFG["pool"].get("min_accounts", 3) or body.force_register:
        need = max(1, min(body.max_register, CFG["pool"].get("maintain_target", 5) - len(verified)))
        created = auto_register_accounts(need)
    return {
        "ok": True,
        "accounts_total": len(accounts),
        "verified_total": len(verified),
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
.nav a{color:#1d1d1f;text-decoration:none;font-size:14px;padding:6px 16px;border-radius:8px;transition:.2s;cursor:pointer}
.nav a:hover{background:#f0f0f0}
.nav .badge{background:#1d1d1f;color:#fff;font-size:11px;padding:2px 8px;border-radius:12px;margin-left:4px}
.container{max-width:1200px;margin:0 auto;padding:24px 32px}
.login-panel{max-width:420px;margin:80px auto 0}
.login-error{color:#c62828;font-size:12px;margin-top:8px;min-height:16px}
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
.btn-primary{background:#1d1d1f;color:#fff}.btn-primary:hover{background:#000}
.btn-secondary{background:#f0f0f0;color:#1d1d1f}.btn-secondary:hover{background:#e5e5e5}
.btn-danger{background:#ff3b30;color:#fff}.btn-danger:hover{background:#d62d20}
.btn-sm{padding:6px 14px;font-size:12px;border-radius:8px}
.table-wrap{overflow-x:auto;border-radius:10px;border:1px solid #e5e5e5}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#f5f5f7;padding:10px 12px;text-align:left;font-weight:500;border-bottom:1px solid #e5e5e5;white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid #f0f0f0;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#fafafa}
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:6px;font-weight:500}
.tag-green{background:#e8f5e9;color:#2e7d32}
.tag-red{background:#ffebee;color:#c62828}
.tag-gray{background:#f5f5f5;color:#616161}
.tag-blue{background:#e3f2fd;color:#1565c0}
.copy-btn{display:inline-flex;align-items:center;gap:4px;cursor:pointer;font-size:11px;padding:3px 10px;border-radius:6px;background:#f0f0f0;border:none;transition:.15s}
.copy-btn:hover{background:#e0e0e0}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:0}
.stat-card{background:#f5f5f7;border-radius:12px;padding:16px;text-align:center}
.stat-card .num{font-size:28px;font-weight:700;letter-spacing:-.5px}
.stat-card .label{font-size:12px;color:#86868b;margin-top:2px}
.endpoint-box{background:#f5f5f7;border-radius:10px;padding:12px 16px;margin-bottom:12px;font-family:monospace;font-size:13px}
.endpoint-box .url{font-weight:600;color:#1d1d1f}
.endpoint-box .desc{font-size:12px;color:#86868b;margin-top:2px}
.task-preview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.task-preview-card{background:#f5f5f7;border-radius:12px;padding:12px}
.task-preview-card h3{font-size:13px;font-weight:600;margin-bottom:8px}
.task-preview-meta{font-size:12px;line-height:1.65;color:#3a3a3c;word-break:break-word}
.task-preview-assets{display:flex;flex-direction:column;gap:8px}
.task-preview-media{max-width:100%;border-radius:10px;border:1px solid #e5e5e5;background:#000}
.task-actions{display:flex;flex-wrap:wrap;gap:6px}
.hidden{display:none!important}
@keyframes fadeUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
@keyframes slideDown{from{opacity:0;transform:translateY(-12px)}to{opacity:1;transform:translateY(0)}}
pre{background:#fafafa;border:1px solid #eee;padding:12px;border-radius:10px;overflow:auto;font-size:12px;max-height:300px}
</style>
</head>
<body>

<div id="login-panel" class="section login-panel hidden">
  <h2>管理员登录</h2>
  <div style="margin-top:12px"><label>用户名</label><input id="login-user" autocomplete="username" value="admin"></div>
  <div style="margin-top:12px"><label>密码</label><input id="login-pass" type="password" autocomplete="current-password"></div>
  <div style="margin-top:16px"><button class="btn-primary" onclick="adminLogin()">登录</button></div>
  <div id="login-error" class="login-error"></div>
</div>

<div id="app-shell" class="hidden">
<div class="nav">
  <h1>OreateAI Gateway</h1>
  <a onclick="switchTab('pool')">号池 <span class="badge" id="pool-count">0</span></a>
  <a onclick="switchTab('generate')">生成</a>
  <a onclick="switchTab('tasks')">任务</a>
  <a onclick="switchTab('apikeys')">API Keys</a>
  <a onclick="switchTab('settings')">设置</a>
  <span style="flex:1"></span>
  <span style="font-size:12px;color:#86868b" id="status-text">就绪</span>
  <button class="btn-secondary btn-sm" onclick="logout()">退出</button>
</div>

<div class="container">

<div class="stats">
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
    <div><button class="btn-secondary" onclick="toggleImport()">导入账号</button></div>
  </div>
  <div id="import-area" class="hidden" style="margin-bottom:12px">
    <div class="row">
      <div class="col"><input id="imp-email" placeholder="邮箱"></div>
      <div class="col"><input id="imp-pwd" placeholder="密码"></div>
      <div><button class="btn-primary" onclick="doImport()">导入</button></div>
      <div><button class="btn-secondary" onclick="document.getElementById('import-area').classList.add('hidden')">取消</button></div>
    </div>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr>
        <th>ID</th><th>邮箱</th><th>状态</th><th>健康</th><th>来源</th><th>OUID</th><th>余额</th><th>更新时间</th><th>创建时间</th><th>操作</th>
      </tr></thead>
      <tbody id="accounts-tbody"></tbody>
    </table>
  </div>
</div>

<!-- Tab: 生成 -->
<div id="tab-generate" class="section hidden">
  <h2>🎨 图片 / 🎬 视频 生成</h2>
  <div style="margin-bottom:16px">
    <div class="endpoint-box">
      <div class="url">POST <span id="gw-url">/v1/generate</span></div>
      <div class="desc">Authorization: Bearer &lt;API Key&gt; &nbsp;|&nbsp; Content-Type: application/json</div>
    </div>
    <div style="font-size:12px;color:#86868b;margin-top:4px">
      示例: <code id="gw-example">curl -H "Authorization: Bearer &lt;key&gt;" -H "Content-Type: application/json" -d '{"kind":"image","prompt":"hello"}' http://localhost:8894/v1/generate</code>
      <button class="copy-btn" onclick="copyExample()" style="margin-left:4px">复制</button>
    </div>
  </div>
  <div class="row">
    <div class="col"><label>类型</label><select id="g-kind" onchange="applyGenerateOptions()"><option value="image">图片</option><option value="video">视频</option></select></div>
    <div class="col"><label>账号ID（留空自动分配）</label><input id="g-account" placeholder="auto"></div>
    <div class="col"><label>模型</label><select id="g-model" onchange="applyModelOptions()"></select></div>
    <div class="col"><label>比例</label><select id="g-ratio"></select></div>
  </div>
  <div class="row">
    <div class="col"><label>分辨率</label><select id="g-res"></select></div>
    <div class="col"><label>视频时长</label><select id="g-dur"></select></div>
    <div class="col"><label>视频场景</label><select id="g-scene"></select></div>
  </div>
  <div id="g-model-desc" style="font-size:12px;color:#6e6e73;margin-top:8px"></div>
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
      <thead><tr><th>ID</th><th>类型</th><th>账号</th><th>状态</th><th>提示词</th><th>chatId</th><th>时间</th><th>操作</th></tr></thead>
      <tbody id="tasks-tbody"></tbody>
    </table>
  </div>
  <div id="task-preview" class="section hidden" style="margin-top:16px">
    <h2>🔎 任务详情</h2>
    <div id="task-preview-body" class="task-preview-grid"></div>
  </div>
</div>

<!-- Tab: API Keys -->
<div id="tab-apikeys" class="section hidden">
  <h2>🔑 API Keys</h2>
  <h3 style="margin-top:0;font-size:14px">客户</h3>
  <div class="row" style="margin-bottom:16px">
    <div class="col"><input id="client-name" placeholder="客户名称"></div>
    <div class="col"><input id="client-contact" placeholder="联系方式"></div>
    <div><button class="btn-primary" onclick="createClient()">创建客户</button></div>
  </div>
  <div style="margin-bottom:16px">
    <div class="endpoint-box">
      <div class="url">POST /v1/generate</div>
      <div class="desc">请求头: <code>Authorization: Bearer &lt;你的 API Key&gt;</code></div>
    </div>
  </div>
  <div class="row" style="margin-bottom:16px">
    <div class="col"><input id="ak-name" placeholder="名称（可选）"></div>
    <div class="col"><select id="ak-client"></select></div>
    <div><button class="btn-primary" onclick="createApiKey()">创建 Key</button></div>
  </div>
  <div id="ak-new" class="hidden" style="background:#e8f5e9;padding:12px;border-radius:10px;margin-bottom:12px">
    <strong>新 Key:</strong> <code id="ak-new-value"></code>
    <button class="copy-btn" onclick="copyKey()">📋 复制</button>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID</th><th>Key</th><th>名称</th><th>客户</th><th>状态</th><th>每分钟</th><th>每日请求</th><th>每日点数</th><th>创建时间</th><th>最后使用</th><th>操作</th></tr></thead>
      <tbody id="apikeys-tbody"></tbody>
    </table>
  </div>
  <h2 style="margin-top:24px">👥 客户列表</h2>
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID</th><th>名称</th><th>联系方式</th><th>状态</th><th>创建时间</th></tr></thead>
      <tbody id="clients-tbody"></tbody>
    </table>
  </div>
  <h2 style="margin-top:24px">📊 用量日志</h2>
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID</th><th>类型</th><th>账号</th><th>模型</th><th>点数</th><th>错误码</th><th>状态</th><th>提示词</th><th>时间</th></tr></thead>
      <tbody id="usage-tbody"></tbody>
    </table>
  </div>
</div>

<!-- Tab: 设置 -->
<div id="tab-settings" class="section hidden">
  <h2>⚙️ 系统设置</h2>
  <div class="row">
    <div class="col"><label>服务端口</label><input id="s-port" value="8894"></div>
  </div>
  <div class="row" style="margin-top:12px">
    <div class="col"><label>OreateAI 基础 URL</label><input id="s-base" value="https://www.oreateai.com"></div>
    <div class="col"><label>默认图片模型</label><input id="s-img-model" value=""></div>
    <div class="col"><label>默认视频模型</label><input id="s-vid-model" value=""></div>
  </div>
  <h3 style="margin-top:20px;font-size:14px">📧 YYDS 邮箱配置</h3>
  <div class="row" style="margin-top:8px">
    <div class="col"><label>API 地址</label><input id="s-mail-url" value="https://maliapi.215.im/v1"></div>
    <div class="col" style="flex:2"><label>API Key</label><input id="s-mail-key" placeholder="mail api key"></div>
  </div>
  <div class="row" style="margin-top:8px">
    <div class="col" style="flex:3"><label>首选域名（逗号分隔）</label><input id="s-mail-domains" placeholder="domain1.xyz,domain2.xyz"></div>
  </div>
  <h3 style="margin-top:20px;font-size:14px">📦 号池配置</h3>
  <div class="row" style="margin-top:8px">
    <div class="col"><label>最低账号数</label><input id="s-min" value="3"></div>
    <div class="col"><label>维护目标数</label><input id="s-target" value="5"></div>
  </div>
  <div style="margin-top:16px"><button class="btn-primary" onclick="saveSettings()">保存设置</button></div>
  <pre id="settings-raw" style="margin-top:12px"></pre>

  <h3 style="margin-top:20px;font-size:14px">管理员账号</h3>
  <div class="row" style="margin-top:8px">
    <div class="col"><label>当前密码</label><input id="cred-current" type="password" autocomplete="current-password"></div>
    <div class="col"><label>新用户名</label><input id="cred-user" autocomplete="username"></div>
    <div class="col"><label>新密码</label><input id="cred-pass" type="password" autocomplete="new-password"></div>
    <div class="col"><label>确认新密码</label><input id="cred-confirm" type="password" autocomplete="new-password"></div>
  </div>
  <div style="margin-top:16px"><button class="btn-secondary" onclick="changeCredentials()">修改账号密码</button></div>

  <h3 style="margin-top:20px;font-size:14px">模型能力</h3>
  <div style="margin-top:8px;display:flex;gap:8px;align-items:center">
    <button class="btn-secondary" onclick="refreshCapabilities()">刷新模型能力</button>
    <span style="font-size:12px;color:#86868b" id="cap-status">未加载</span>
  </div>

  <h3 style="margin-top:20px;font-size:14px">🧾 审计日志</h3>
  <div class="table-wrap" style="margin-top:8px">
    <table>
      <thead><tr><th>时间</th><th>用户</th><th>动作</th><th>路径</th><th>状态</th><th>详情</th></tr></thead>
      <tbody id="audit-tbody"></tbody>
    </table>
  </div>

  <h3 style="margin-top:20px;font-size:14px">🗄 备份与恢复</h3>
  <div class="row" style="margin-top:8px">
    <div class="col"><button class="btn-secondary" onclick="downloadBackup()">下载备份</button></div>
    <div class="col"><label>恢复包</label><input id="restore-file" type="file" accept=".zip,application/zip"></div>
    <div class="col" style="align-self:end"><button class="btn-danger" onclick="restoreBackup()">恢复备份</button></div>
  </div>
</div>

</div>
</div>

<script>
const BASE = location.origin;
let adminToken = localStorage.getItem('oreate_admin_token') || '';

function copyText(t) { navigator.clipboard.writeText(t).catch(()=>{}); }
function authHeaders(){
  const headers = {'Content-Type':'application/json'};
  if (adminToken) headers.Authorization = 'Bearer ' + adminToken;
  return headers;
}
function showLogin(message=''){
  document.getElementById('login-panel').classList.remove('hidden');
  document.getElementById('app-shell').classList.add('hidden');
  document.getElementById('login-error').textContent = message;
}
function showApp(){
  document.getElementById('login-panel').classList.add('hidden');
  document.getElementById('app-shell').classList.remove('hidden');
}
async function adminLogin(){
  const r = await fetch(BASE + '/api/admin/login', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      username:document.getElementById('login-user').value,
      password:document.getElementById('login-pass').value
    })
  });
  const data = await r.json().catch(()=>({}));
  if (!r.ok || !data.token) {
    showLogin(data.detail || '登录失败');
    return;
  }
  adminToken = data.token;
  localStorage.setItem('oreate_admin_token', adminToken);
  await init();
}
function logout(){
  if (adminToken) {
    fetch(BASE + '/api/admin/logout', {
      method:'POST',
      headers: authHeaders()
    }).catch(()=>{});
  }
  adminToken = '';
  localStorage.removeItem('oreate_admin_token');
  showLogin();
}
async function downloadBackup(){
  const r=await fetch(BASE + '/api/admin/backup', {headers: authHeaders()});
  if(!r.ok) throw new Error('backup failed');
  const blob=await r.blob();
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;
  a.download='oreate-backup.zip';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(()=>URL.revokeObjectURL(url), 1000);
}
async function restoreBackup(){
  const input=document.getElementById('restore-file');
  const file=input?.files?.[0];
  if(!file){ alert('请选择备份文件'); return; }
  if(!confirm('确认恢复备份？当前数据库和配置将被替换。')) return;
  const form=new FormData();
  form.append('confirm', 'true');
  form.append('file', file);
  const r=await fetch(BASE + '/api/admin/restore', {
    method:'POST',
    headers: adminToken ? {Authorization:'Bearer ' + adminToken} : {},
    body: form
  });
  const data=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(data.detail || 'restore failed');
  alert('恢复完成，请重新登录');
  adminToken='';
  localStorage.removeItem('oreate_admin_token');
  showLogin('恢复完成，请重新登录');
}
function switchTab(name) {
  document.querySelectorAll('#tab-pool,#tab-generate,#tab-tasks,#tab-apikeys,#tab-settings').forEach(el => {
    el.classList.toggle('hidden', el.id !== 'tab-'+name);
  });
}

// Init
async function init() {
  if (!adminToken) {
    showLogin();
    return;
  }
  showApp();
  document.getElementById('status-text').textContent = '加载中...';
  try {
    await loadClients();
    await Promise.all([loadAccounts(), loadTasks(), loadApiKeys(), loadUsage(), loadAuditLogs(), loadSettings()]);
    await loadCapabilities();
  } catch (e) {
    document.getElementById('status-text').textContent = '未授权';
    showLogin('登录已失效');
    return;
  }
  const v = state.accounts.filter(a=>a.status==='verified').length;
  document.getElementById('status-text').textContent = `就绪 — ${v} 可用账号`;
  document.getElementById('gw-url').textContent = location.origin + '/v1/generate';
  document.getElementById('gw-example').textContent =
    'curl -H "Authorization: Bearer <key>" -H "Content-Type: application/json" -d \'{"kind":"image","prompt":"hello"}\' ' + location.origin + '/v1/generate';
}
function copyExample() { copyText(document.getElementById('gw-example').textContent); }

// === Accounts ===
let state = {accounts:[],tasks:[],apikeys:[],clients:[],usage:[],auditLogs:[],settings:{},capabilities:{image:{models:[]},video:{models:[],scenes:[]}}};
async function api(m,u,b){
  const o={method:m,headers:authHeaders()};
  if(b) o.body=JSON.stringify(b);
  const r = await fetch(BASE+u,o);
  const data = await r.json().catch(()=>({}));
  if (r.status === 401) throw new Error(data.detail || 'unauthorized');
  if (!r.ok) throw new Error(data.detail || 'request failed');
  return data;
}
function escapeHtml(value){
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function normalizedOptionValues(values){
  const out=[];
  (Array.isArray(values)?values:[]).forEach(v => {
    const s=String(v ?? '').trim();
    if(s && !out.includes(s)) out.push(s);
  });
  return out;
}
function setSelectOptions(id, items, selectedValue='', emptyLabel='默认'){
  const el=document.getElementById(id);
  if(!el) return;
  const selected=String(selectedValue ?? '');
  const options=[];
  if(emptyLabel !== null) options.push(`<option value="">${escapeHtml(emptyLabel)}</option>`);
  (Array.isArray(items)?items:[]).forEach(item => {
    const value=String((item && typeof item === 'object' ? item.value : item) ?? '');
    if(!value) return;
    const label=String((item && typeof item === 'object' ? item.label : item) ?? value);
    options.push(`<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`);
  });
  el.innerHTML=options.join('');
  if([...el.options].some(o=>o.value===selected)) {
    el.value=selected;
  } else if(el.options.length > (emptyLabel === null ? 0 : 1)) {
    el.selectedIndex=emptyLabel === null ? 0 : 1;
  } else {
    el.value='';
  }
}
function valueOptions(values){
  return normalizedOptionValues(values).map(v => ({value:v,label:v}));
}
function capabilityModels(kind){
  return (((state.capabilities || {})[kind] || {}).models || []);
}
function capabilityScenes(){
  return (((state.capabilities || {}).video || {}).scenes || []);
}
function policyBadge(item){
  const verification = item?.verification_status || 'unverified';
  return `${verification}${item?.experimental ? ' · experimental' : ''}`;
}
function modelOptionLabel(model){
  const title = model.description ? `${model.name} - ${model.description}` : model.name;
  return `${title} · ${policyBadge(model)}`;
}
function sceneOptionLabel(scene){
  const title = scene.name ? `${scene.name} - ${scene.scene_id}` : scene.scene_id;
  return `${title} · ${policyBadge(scene)}`;
}
function defaultModel(kind){
  return kind === 'video' ? (state.settings.oreate?.default_video_model || '') : (state.settings.oreate?.default_image_model || '');
}
function defaultRatio(kind){
  return kind === 'video' ? (state.settings.oreate?.default_video_ratio || '') : (state.settings.oreate?.default_image_ratio || '');
}
function defaultResolution(kind){
  return kind === 'video' ? (state.settings.oreate?.default_video_resolution || '') : (state.settings.oreate?.default_image_resolution || '');
}
function setVideoFieldsVisible(visible){
  ['g-dur','g-scene'].forEach(id => {
    const wrap=document.getElementById(id)?.closest('.col');
    if(wrap) wrap.style.display = visible ? '' : 'none';
  });
}
function setCapabilityState(payload){
  state.capabilities={
    image: payload?.image || {models:[]},
    video: payload?.video || {models:[],scenes:[]},
    source_account_id: payload?.source_account_id || null,
  };
}
async function loadAccounts(){
  const r=await api('GET','/api/accounts');
  state.accounts=r.items||[]; renderAccounts(); updateStats();
}
async function loadClients(){
  const r=await api('GET','/api/admin/clients');
  state.clients=r.items||[];
  renderClients();
  renderClientSelect();
}
function renderClientSelect(){
  setSelectOptions(
    'ak-client',
    (state.clients||[]).map(c => ({value:String(c.id), label:`${c.name}${c.contact ? ` · ${c.contact}` : ''}`})),
    '',
    '未绑定'
  );
}
function renderAccounts(){
  const tbody=document.getElementById('accounts-tbody');
  tbody.innerHTML = state.accounts.map(a => {
    const sc = a.status==='verified'?'tag-green':a.status==='new'?'tag-blue':'tag-gray';
    const hc = a.health_status==='healthy'?'tag-green':a.health_status==='cooling'?'tag-blue':a.health_status==='low_balance'?'tag-red':a.health_status==='invalid'?'tag-gray':'tag-blue';
    const em = a.email||'';
    const restPoint = a.rest_point ?? '-';
    const balanceUpdatedAt = a.balance_updated_at ? new Date((a.balance_updated_at||0)*1000).toLocaleString() : '-';
    const healthMeta = `${a.risk_status||'clean'}${a.cooling ? ` · ${a.cooldown_remaining_seconds || 0}s` : ''}`;
    return `<tr>
      <td>${a.id}</td>
      <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis">${em}<button class="copy-btn" onclick="copyText('${em.replace(/'/g,"\\'")}')">📋</button></td>
      <td><span class="tag ${sc}">${a.status}</span></td>
      <td><span class="tag ${hc}">${a.health_status || '-'}</span><div style="font-size:11px;color:#86868b">${healthMeta}</div></td>
      <td>${a.source||'-'}</td>
      <td style="font-family:monospace;font-size:11px">${a.ouid_preview||''}</td>
      <td>${restPoint}</td>
      <td style="font-size:11px">${balanceUpdatedAt}</td>
      <td style="font-size:11px">${new Date((a.created_at||0)*1000).toLocaleString()}</td>
      <td><button class="btn-sm btn-secondary" onclick="generateWith(${a.id})">生成</button> <button class="btn-sm btn-secondary" onclick="refreshAccountBalance(${a.id})">刷新余额</button></td>
    </tr>`;
  }).join('');
  document.getElementById('pool-count').textContent = state.accounts.filter(a=>a.status==='verified').length;
}
async function registerOne(){document.getElementById('status-text').textContent='注册中...';const r=await api('POST','/api/register/one');await loadAccounts();document.getElementById('status-text').textContent='完成';alert(JSON.stringify(r));}
async function registerBatch(){document.getElementById('status-text').textContent='批量注册中...';const n=Number(document.getElementById('reg_count').value||1);const r=await api('POST','/api/register/batch',{count:n});await loadAccounts();document.getElementById('status-text').textContent='完成';const ok=(r.items||[]).filter(i=>i.status==='verified').length;alert(`成功: ${ok}/${n}`);}
async function maintainPool(){const r=await api('POST','/api/pool/maintain',{force_register:true,max_register:Number(document.getElementById('reg_count').value||1)});await loadAccounts();alert(JSON.stringify(r.created));}
function toggleImport(){document.getElementById('import-area').classList.toggle('hidden');}
async function doImport(){const r=await api('POST','/api/accounts/import',{email:document.getElementById('imp-email').value,password:document.getElementById('imp-pwd').value});await loadAccounts();alert(r.ok?'✅ 导入成功':'❌ 失败');}
function generateWith(aid){switchTab('generate');document.getElementById('g-account').value=aid;}
async function refreshAccountBalance(aid){await api('POST',`/api/accounts/${aid}/refresh-balance`);await loadAccounts();}

// === Generate ===
async function loadCapabilities(){
  const status=document.getElementById('cap-status');
  if(status) status.textContent='加载中...';
  try {
    const r=await api('GET','/api/models/capabilities');
    setCapabilityState(r);
    const imageCount=capabilityModels('image').length;
    const videoCount=capabilityModels('video').length;
    const sceneSummary=capabilityScenes().map(s => `${s.scene_id}:${policyBadge(s)}`).join(' | ');
    if(status) status.textContent=`账号 ${r.source_account_id || '-'} · 图片模型 ${imageCount} · 视频模型 ${videoCount}${sceneSummary ? ` · 场景 ${sceneSummary}` : ''}`;
  } catch(e) {
    setCapabilityState({});
    if(status) status.textContent='未加载：' + e.message;
  }
  applyGenerateOptions();
}
async function refreshCapabilities(){
  const status=document.getElementById('cap-status');
  if(status) status.textContent='刷新中...';
  try {
    const r=await api('POST','/api/models/refresh');
    setCapabilityState(r);
    const imageCount=capabilityModels('image').length;
    const videoCount=capabilityModels('video').length;
    if(status) status.textContent=`账号 ${r.source_account_id || '-'} · 图片模型 ${imageCount} · 视频模型 ${videoCount}`;
    applyGenerateOptions();
    alert('已刷新模型能力');
  } catch(e) {
    if(status) status.textContent='刷新失败：' + e.message;
    alert('刷新失败：' + e.message);
  }
}
function selectedModel(kind){
  const name=document.getElementById('g-model').value;
  return capabilityModels(kind).find(m => m.name === name) || null;
}
function applyGenerateOptions(){
  const kind=document.getElementById('g-kind').value;
  const models=capabilityModels(kind);
  const current=document.getElementById('g-model').value;
  const configured=defaultModel(kind);
  const selected=models.some(m=>m.name===current) ? current : (models.some(m=>m.name===configured) ? configured : (models[0]?.name || ''));
  setVideoFieldsVisible(kind === 'video');
  setSelectOptions(
    'g-model',
    models.map(m => ({value:m.name,label:modelOptionLabel(m)})),
    selected,
    models.length ? null : '使用默认模型'
  );
  applyModelOptions();
}
function applyModelOptions(){
  const kind=document.getElementById('g-kind').value;
  const model=selectedModel(kind);
  const desc=document.getElementById('g-model-desc');
  if(model) {
    desc.textContent = model.description || '';
    setSelectOptions('g-ratio', valueOptions(model.ratios), defaultRatio(kind), model.ratios?.length ? null : '默认比例');
    setSelectOptions('g-res', valueOptions(model.resolutions), defaultResolution(kind), model.resolutions?.length ? null : '默认分辨率');
  } else {
    desc.textContent = capabilityModels(kind).length ? '' : '模型能力未加载，可在设置页刷新模型能力。';
    setSelectOptions('g-ratio', valueOptions([defaultRatio(kind)]), defaultRatio(kind), '默认比例');
    setSelectOptions('g-res', valueOptions([defaultResolution(kind)]), defaultResolution(kind), '默认分辨率');
  }
  if(kind === 'video') {
    setSelectOptions('g-dur', valueOptions(model?.durations || [state.settings.oreate?.default_video_duration]), state.settings.oreate?.default_video_duration || '', model?.durations?.length ? null : '默认时长');
    setSelectOptions(
      'g-scene',
      capabilityScenes().map(s => ({value:s.scene_id,label:sceneOptionLabel(s)})),
      state.settings.oreate?.default_video_scene || '',
      capabilityScenes().length ? null : '默认场景'
    );
  } else {
    setSelectOptions('g-dur', [], '', '默认时长');
    setSelectOptions('g-scene', [], '', '默认场景');
  }
}
async function gatewayGenerate(){
  const payload={
    kind: document.getElementById('g-kind').value,
    prompt: document.getElementById('g-prompt').value,
    model_name: document.getElementById('g-model').value||null,
    ratio: document.getElementById('g-ratio').value||null,
    resolution: document.getElementById('g-res').value||null,
    duration: document.getElementById('g-dur').value?Number(document.getElementById('g-dur').value):null,
    scene_id: document.getElementById('g-scene').value||null,
    account_id: document.getElementById('g-account').value?Number(document.getElementById('g-account').value):null,
  };
  document.getElementById('g-result').textContent='提交中...';
  const r=await api('POST','/api/media/generate',payload);
  document.getElementById('g-result').textContent=JSON.stringify(r,null,2);
  await loadTasks();
}

// === Tasks ===
async function loadTasks(){const r=await api('GET','/api/tasks');state.tasks=r.items||[];renderTasks();updateStats();}
function renderTasks(){
  document.getElementById('tasks-tbody').innerHTML = state.tasks.slice(0,50).map(t => {
    const statusClass=t.status==='completed'?'tag-green':t.status==='failed'?'tag-red':t.status==='cancelled'?'tag-gray':t.status==='submitted'?'tag-blue':'tag-gray';
    const kindClass=t.kind==='image'?'tag-blue':'tag-green';
    return `<tr>
      <td>${t.id}</td>
      <td><span class="tag ${kindClass}">${t.kind}</span></td>
      <td>${t.account_id||'-'}</td>
      <td><span class="tag ${statusClass}">${t.status}${t.cancel_requested_at ? ' · cancel' : ''}</span></td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${escapeHtml((t.prompt||'').substring(0,40))}</td>
      <td style="font-family:monospace;font-size:11px">${escapeHtml((t.chat_id||'').substring(0,12))}</td>
      <td style="font-size:11px">${new Date((t.created_at||0)*1000).toLocaleString()}</td>
      <td>
        <div class="task-actions">
          <button class="btn-sm btn-secondary" onclick="loadTaskDetail(${t.id})">详情</button>
          <button class="btn-sm btn-secondary" onclick="hydrateTask(${t.id})">重水合</button>
          <button class="btn-sm btn-secondary" onclick="retryTask(${t.id})">重试</button>
          <button class="btn-sm btn-danger" onclick="cancelTask(${t.id})">取消</button>
        </div>
      </td>
    </tr>`;
  }).join('');
}
function renderTaskAsset(asset){
  const url = String(asset || '');
  if(!url) return '';
  if(/\.(mp4|mov|webm)(\?|$)/i.test(url)) {
    return `<video class="task-preview-media" controls src="${escapeHtml(url)}"></video>`;
  }
  return `<img class="task-preview-media" src="${escapeHtml(url)}" alt="task result">`;
}
function renderTaskPreview(task){
  const panel=document.getElementById('task-preview');
  const body=document.getElementById('task-preview-body');
  if(!panel || !body) return;
  const assets=Array.isArray(task?.assets) ? task.assets : [];
  const attempts=Array.isArray(task?.attempts) ? task.attempts : [];
  const scene=capabilityScenes().find(s => s.scene_id === (task?.scene_id || task?.payload?.scene_id)) || null;
  const model=capabilityModels(task?.kind || 'image').find(m => m.name === (task?.model_name || task?.payload?.model_name)) || null;
  const assetHtml=assets.length ? assets.map(renderTaskAsset).join('') : '<div class="task-preview-meta">暂无结果</div>';
  body.innerHTML = `
    <div class="task-preview-card">
      <h3>基础信息</h3>
      <div class="task-preview-meta">
        <div><strong>ID</strong> ${task?.id || '-'}</div>
        <div><strong>状态</strong> ${escapeHtml(task?.status || '-')}</div>
        <div><strong>模型</strong> ${escapeHtml(task?.model_name || '-')}</div>
        <div><strong>场景</strong> ${escapeHtml(task?.scene_id || '-')}</div>
        <div><strong>验证</strong> ${escapeHtml(scene?.verification_status || model?.verification_status || task?.verification_status || '-')}</div>
        <div><strong>实验性</strong> ${(scene?.experimental ?? model?.experimental ?? task?.experimental) ? '是' : '否'}</div>
        <div><strong>点数</strong> ${task?.estimated_point_cost ?? '-'}</div>
        <div><strong>实际</strong> ${task?.actual_point_cost ?? '-'}</div>
        <div><strong>前后余额</strong> ${task?.balance_before_rest_point ?? '-'} → ${task?.balance_after_rest_point ?? '-'}</div>
        <div><strong>错误码</strong> ${escapeHtml(task?.error_code || '-')}</div>
      </div>
      <div style="margin-top:12px" class="task-actions">
        <button class="btn-sm btn-secondary" onclick="retryTask(${task?.id || 0})">retryTask</button>
        <button class="btn-sm btn-secondary" onclick="cancelTask(${task?.id || 0})">cancelTask</button>
        <button class="btn-sm btn-secondary" onclick="hydrateTask(${task?.id || 0})">hydrateTask</button>
      </div>
    </div>
    <div class="task-preview-card">
      <h3>结果预览</h3>
      <div class="task-preview-assets">${assetHtml}</div>
      <div style="margin-top:8px;font-size:12px;color:#86868b">${assets.map(a => `<div><a href="${escapeHtml(String(a))}" target="_blank" rel="noreferrer">${escapeHtml(String(a))}</a></div>`).join('')}</div>
    </div>
    <div class="task-preview-card">
      <h3>参数与尝试</h3>
      <div class="task-preview-meta"><pre style="white-space:pre-wrap">${escapeHtml(JSON.stringify(task?.payload || {}, null, 2))}</pre></div>
      <div class="task-preview-meta" style="margin-top:8px"><pre style="white-space:pre-wrap">${escapeHtml(JSON.stringify(attempts, null, 2))}</pre></div>
    </div>
  `;
  panel.classList.remove('hidden');
}
async function loadTaskDetail(id){
  const r=await api('GET',`/api/tasks/${id}`);
  renderTaskPreview(r.task);
  return r.task;
}
async function retryTask(id){
  const r=await api('POST',`/api/tasks/${id}/retry`);
  await loadTasks();
  renderTaskPreview(r.task);
}
async function cancelTask(id){
  const r=await api('POST',`/api/tasks/${id}/cancel`);
  await loadTasks();
  renderTaskPreview(r.task);
}
async function hydrateTask(id){
  const r=await api('POST',`/api/tasks/${id}/hydrate`);
  await loadTasks();
  renderTaskPreview(r.task);
}

// === API Keys ===
async function loadApiKeys(){const r=await api('GET','/api/admin/apikeys');state.apikeys=r.items||[];renderApiKeys();updateStats();}
function renderApiKeys(){
  document.getElementById('apikeys-tbody').innerHTML = state.apikeys.map(k => {
    const kp=k.key_preview||'';
    return `<tr><td>${k.id}</td><td style="font-family:monospace;font-size:11px">${kp}</td><td>${k.name||'-'}</td><td>${k.client_name||'-'}</td><td><span class="tag ${k.enabled?'tag-green':'tag-gray'}">${k.enabled?'启用':'停用'}</span></td><td><input id="ak-rate-${k.id}" data-field="rate_limit_per_minute" value="${k.rate_limit_per_minute||''}" style="width:84px"></td><td><input id="ak-req-${k.id}" data-field="daily_request_limit" value="${k.daily_request_limit||''}" style="width:84px"></td><td><input id="ak-point-${k.id}" data-field="daily_point_limit" value="${k.daily_point_limit||''}" style="width:84px"></td><td style="font-size:11px">${new Date((k.created_at||0)*1000).toLocaleString()}</td><td style="font-size:11px">${k.last_used_at?new Date(k.last_used_at*1000).toLocaleString():'-'}</td><td><button class="btn-sm btn-secondary" onclick="updateApiKeyPolicy(${k.id})">保存</button> <button class="btn-sm btn-danger" onclick="deleteKey(${k.id})">删除</button></td></tr>`;
  }).join('');
}
function renderClients(){
  const tbody=document.getElementById('clients-tbody');
  if(!tbody) return;
  tbody.innerHTML = (state.clients||[]).map(c => {
    return `<tr><td>${c.id}</td><td>${c.name||'-'}</td><td>${c.contact||'-'}</td><td><span class="tag ${c.status==='active'?'tag-green':'tag-gray'}">${c.status||'active'}</span></td><td style="font-size:11px">${new Date((c.created_at||0)*1000).toLocaleString()}</td></tr>`;
  }).join('');
}
async function createClient(){
  const r=await api('POST','/api/admin/clients',{name:document.getElementById('client-name').value,contact:document.getElementById('client-contact').value});
  document.getElementById('client-name').value='';
  document.getElementById('client-contact').value='';
  await loadClients();
  alert(r.ok?'✅ 客户已创建':'❌ 创建失败');
}
async function createApiKey(){
  const name=document.getElementById('ak-name').value;
  const clientValue=document.getElementById('ak-client').value;
  const body={name:name};
  if(clientValue) body.client_id=Number(clientValue);
  const r=await api('POST','/api/admin/apikeys',body);
  if(r.item){
    document.getElementById('ak-new').classList.remove('hidden');
    document.getElementById('ak-new-value').textContent=r.item.key;
    await loadClients();
    await loadApiKeys();
  } else alert('创建失败: '+JSON.stringify(r));
}
function copyKey(){const v=document.getElementById('ak-new-value').textContent;copyText(v);alert('已复制');}
async function updateApiKeyPolicy(id){
  const body={
    rate_limit_per_minute:document.getElementById('ak-rate-'+id).value || null,
    daily_request_limit:document.getElementById('ak-req-'+id).value || null,
    daily_point_limit:document.getElementById('ak-point-'+id).value || null,
  };
  await api('PATCH','/api/admin/apikeys/'+id,body);
  await loadClients();
  await loadApiKeys();
}
async function deleteKey(id){if(!confirm('确认删除此 API Key？')) return; await api('DELETE','/api/admin/apikeys/'+id);await loadApiKeys();}

// === Usage ===
async function loadUsage(){const r=await api('GET','/api/admin/usage');state.usage=r.items||[];renderUsage();}
function renderUsage(){
  document.getElementById('usage-tbody').innerHTML = state.usage.slice(0,50).map(u => {
    return `<tr><td>${u.id}</td><td><span class="tag ${u.kind==='image'?'tag-blue':'tag-green'}">${u.kind}</span></td><td>${u.account_email||u.account_id||'-'}</td><td>${u.model_name||'-'}</td><td>${u.estimated_point_cost ?? '-'}</td><td>${u.error_code||'-'}</td><td>${u.status}</td><td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${(u.prompt||'').substring(0,40)}</td><td style="font-size:11px">${new Date((u.created_at||0)*1000).toLocaleString()}</td></tr>`;
  }).join('');
}

async function loadAuditLogs(){const r=await api('GET','/api/admin/audit-logs');state.auditLogs=r.items||[];renderAuditLogs();}
function renderAuditLogs(){
  const tbody=document.getElementById('audit-tbody');
  if(!tbody) return;
  tbody.innerHTML = (state.auditLogs||[]).slice(0,50).map(a => {
    return `<tr><td style="font-size:11px">${new Date((a.created_at||0)*1000).toLocaleString()}</td><td>${escapeHtml(a.admin_username||'-')}</td><td>${escapeHtml(a.action||'-')}</td><td style="font-size:11px">${escapeHtml(a.path||'-')}</td><td>${a.status_code ?? '-'}</td><td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;font-size:11px">${escapeHtml(JSON.stringify(a.details || {}))}</td></tr>`;
  }).join('');
}

// === Settings ===
async function loadSettings(){
  state.settings=await api('GET','/api/admin/settings');
  const s=state.settings;
  document.getElementById('s-port').value=s.server?.port||8894;
  document.getElementById('s-base').value=s.oreate?.base_url||'';
  document.getElementById('s-img-model').value=s.oreate?.default_image_model||'';
  document.getElementById('s-vid-model').value=s.oreate?.default_video_model||'';
  document.getElementById('s-min').value=s.pool?.min_accounts||3;
  document.getElementById('s-target').value=s.pool?.maintain_target||5;
  document.getElementById('s-mail-url').value=s.mail?.base_url||'';
  document.getElementById('s-mail-key').value='';
  document.getElementById('s-mail-key').placeholder=s.mail?.api_key==='__redacted__'?'留空不修改':'mail api key';
  document.getElementById('s-mail-domains').value=(s.mail?.preferred_domains||[]).join(',');
  document.getElementById('cred-user').value=s.server?.admin_username||'';
  document.getElementById('settings-raw').textContent=JSON.stringify(s,null,2);
}
async function saveSettings(){
  const doms = document.getElementById('s-mail-domains').value.split(',').map(s=>s.trim()).filter(Boolean);
  const body={
    server:{port:Number(document.getElementById('s-port').value)},
    oreate:{
      base_url:document.getElementById('s-base').value,
      default_image_model:document.getElementById('s-img-model').value,
      default_video_model:document.getElementById('s-vid-model').value,
    },
    mail:{
      base_url:document.getElementById('s-mail-url').value,
      preferred_domains:doms,
    },
    pool:{
      min_accounts:Number(document.getElementById('s-min').value),
      maintain_target:Number(document.getElementById('s-target').value),
    },
  };
  const mailKey=document.getElementById('s-mail-key').value;
  if(mailKey) body.mail.api_key=mailKey;
  const r=await api('PUT','/api/admin/settings',body);
  if(r.ok){await loadSettings();alert('✅ 已保存');} else alert('❌ 保存失败');
}
async function changeCredentials(){
  const body={
    current_password:document.getElementById('cred-current').value,
    new_username:document.getElementById('cred-user').value,
    new_password:document.getElementById('cred-pass').value,
    confirm_password:document.getElementById('cred-confirm').value,
  };
  if(!body.current_password || !body.new_username || !body.new_password || !body.confirm_password){
    alert('请填写当前密码、新用户名、新密码和确认密码');
    return;
  }
  const r=await api('POST','/api/admin/credentials',body);
  if(r.ok){
    document.getElementById('cred-current').value='';
    document.getElementById('cred-pass').value='';
    document.getElementById('cred-confirm').value='';
    adminToken='';
    localStorage.removeItem('oreate_admin_token');
    document.getElementById('login-user').value=body.new_username;
    showLogin('账号密码已修改，请重新登录');
  }
}
function updateStats(){
  const a=state.accounts||[];
  document.getElementById('st-total').textContent=a.length;
  document.getElementById('st-verified').textContent=a.filter(x=>x.status==='verified').length;
  document.getElementById('st-tasks').textContent=(state.tasks||[]).length;
  document.getElementById('st-apikeys').textContent=(state.apikeys||[]).length;
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
