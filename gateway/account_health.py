"""Pure account pool health evaluation helpers.

No database, HTTP, or network access. Callers supply account field views
and optional capability flags.

Three readiness dimensions (Slice 1):

  auth_ready
      status in {verified, active} and ouid/ouss both non-empty.

  points_ready
      Display/isolation dimension aligned with existing balance semantics:
      balance_status ok -> True; empty/low -> False; unknown -> True.
      Unknown stays True so an unrefreshed balance is not treated as a
      bad account (matches health_status, which only flags empty/low as
      low_balance, and account_has_sufficient_balance when cost is
      unknown).

  generate_ready
      auth_ready AND not cooling AND health_status would be healthy AND
      has_schedulable_capability (capability flag supplied by caller).

Compatible health_status strings are unchanged:
  disabled | invalid | pending | cooling | risk_control | low_balance | healthy
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

LOW_BALANCE_THRESHOLD = 10

HEALTH_STATUS_VALUES = (
    "disabled",
    "invalid",
    "pending",
    "cooling",
    "risk_control",
    "low_balance",
    "healthy",
)


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _int_or_default(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _json_value(raw: Any) -> Any:
    if raw in (None, ""):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def account_balance_value(row: Any) -> Optional[int]:
    """Return known rest-point balance, or None when balance is unknown."""
    if _get(row, "rest_point") not in (None, ""):
        return _int_or_default(_get(row, "rest_point"), 0)
    if _get(row, "daily_point") not in (None, "") or _get(row, "bonus_point") not in (None, ""):
        return _int_or_default(_get(row, "daily_point"), 0) + _int_or_default(_get(row, "bonus_point"), 0)
    raw = _json_value(_get(row, "point_balance_json")) or {}
    source = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    if isinstance(source, dict):
        if source.get("restPoint") not in (None, ""):
            return _int_or_default(source.get("restPoint"), 0)
        if source.get("rest_point") not in (None, ""):
            return _int_or_default(source.get("rest_point"), 0)
        if source.get("restpoint") not in (None, ""):
            return _int_or_default(source.get("restpoint"), 0)
        if source.get("daily") not in (None, "") or source.get("bonus") not in (None, ""):
            return _int_or_default(source.get("daily"), 0) + _int_or_default(source.get("bonus"), 0)
        if source.get("dailyPoint") not in (None, "") or source.get("bonusPoint") not in (None, ""):
            return _int_or_default(source.get("dailyPoint"), 0) + _int_or_default(source.get("bonusPoint"), 0)
        if source.get("daily_point") not in (None, "") or source.get("bonus_point") not in (None, ""):
            return _int_or_default(source.get("daily_point"), 0) + _int_or_default(source.get("bonus_point"), 0)
    return None


def account_cooldown_remaining_seconds(row: Any, now: Optional[float] = None) -> float:
    current = time.time() if now is None else now
    cooldown_until = _get(row, "cooldown_until")
    if cooldown_until in (None, ""):
        return 0.0
    try:
        remaining = float(cooldown_until) - float(current)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, remaining)


def account_is_cooling(row: Any, now: Optional[float] = None) -> bool:
    return account_cooldown_remaining_seconds(row, now) > 0


def account_balance_status(row: Any) -> str:
    balance = account_balance_value(row)
    if balance is None:
        return "unknown"
    if balance <= 0:
        return "empty"
    if balance < LOW_BALANCE_THRESHOLD:
        return "low"
    return "ok"


def account_risk_status(row: Any) -> str:
    status = str(_get(row, "status") or "")
    if status == "invalid":
        return "invalid"
    if status == "risk_control":
        # Accounts can be placed in this status by upstream risk-control
        # detection; the branch must be reachable so pool maintenance isolates
        # them instead of scheduling them blindly (P2-2 fix).
        return "risk_control"
    return "clean"


def account_health_status(row: Any, now: Optional[float] = None) -> str:
    """Composite display health. Priority and enum strings are stable API."""
    status = str(_get(row, "status") or "")
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


def account_auth_ready(row: Any) -> bool:
    status = str(_get(row, "status") or "")
    if status not in {"verified", "active"}:
        return False
    ouid = str(_get(row, "ouid") or "").strip()
    ouss = str(_get(row, "ouss") or "").strip()
    return bool(ouid and ouss)


def account_points_ready(row: Any) -> bool:
    """True when balance is not empty/low; unknown counts as ready for display."""
    return account_balance_status(row) not in {"empty", "low"}


def account_generate_ready(
    row: Any,
    *,
    has_schedulable_capability: bool = False,
    now: Optional[float] = None,
) -> bool:
    """True when the account can enter generation selection (capability flag required)."""
    if not account_auth_ready(row):
        return False
    if account_is_cooling(row, now):
        return False
    if account_health_status(row, now) != "healthy":
        return False
    return bool(has_schedulable_capability)


def account_health_fields(
    row: Any,
    *,
    has_schedulable_capability: bool = False,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Serialize-ready health snapshot for one account."""
    current = time.time() if now is None else now
    remaining = account_cooldown_remaining_seconds(row, current)
    caps = bool(has_schedulable_capability)
    return {
        "cooling": remaining > 0,
        "cooldown_remaining_seconds": int(remaining),
        "balance_status": account_balance_status(row),
        "risk_status": account_risk_status(row),
        "health_status": account_health_status(row, current),
        "auth_ready": account_auth_ready(row),
        "points_ready": account_points_ready(row),
        "generate_ready": account_generate_ready(
            row,
            has_schedulable_capability=caps,
            now=current,
        ),
    }


def account_pool_summary(
    rows: Iterable[Any],
    now: Optional[float] = None,
    *,
    has_schedulable_capability: Optional[Callable[[Any], bool]] = None,
) -> Dict[str, int]:
    """Aggregate pool counters, including the three readiness dimensions.

    ``has_schedulable_capability`` is provided by the caller (capability JSON
    lives outside this pure module). When omitted, capability-gated counts
    (healthy/cooling/generate_ready) treat every row as non-schedulable.
    """
    current = time.time() if now is None else now
    caps_fn = has_schedulable_capability or (lambda _row: False)
    summary = {
        "total": 0,
        "verified": 0,
        "healthy": 0,
        "cooling": 0,
        "low_balance": 0,
        "invalid": 0,
        "disabled": 0,
        "risk_control": 0,
        "balance_known": 0,
        "auth_ready": 0,
        "points_ready": 0,
        "generate_ready": 0,
    }
    for row in rows:
        summary["total"] += 1
        status = str(_get(row, "status") or "")
        if status in {"verified", "active"}:
            summary["verified"] += 1
        if account_balance_value(row) is not None:
            summary["balance_known"] += 1
        health = account_health_status(row, current)
        caps = bool(caps_fn(row))
        # Match legacy: healthy/cooling pool buckets require verified|active + caps.
        schedulable = status in {"verified", "active"} and caps
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
        if account_auth_ready(row):
            summary["auth_ready"] += 1
        if account_points_ready(row):
            summary["points_ready"] += 1
        if account_generate_ready(row, has_schedulable_capability=caps, now=current):
            summary["generate_ready"] += 1
    return summary
