"""Build model×cost-combo catalogs and attach pool availability."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


def availability_status(ready_accounts: int) -> str:
    if ready_accounts >= 2:
        return "available"
    if ready_accounts == 1:
        return "tight"
    return "unavailable"


def video_cost_table_for_scene(model: Mapping[str, Any], scene_id: str) -> List[Dict[str, Any]]:
    if scene_id == "reference":
        raw = model.get("point_cost_reference") or []
    elif scene_id == "motion":
        raw = model.get("point_cost_motion") or []
    else:
        raw = model.get("point_cost_image") or []
    return [item for item in raw if isinstance(item, dict)]


def _int_or_none(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _point_cost(item: Mapping[str, Any]) -> Optional[int]:
    point = _int_or_none(item.get("point"))
    if point is None or point < 0:
        return None
    return point


def expand_capability_cost_rows(
    capabilities: Mapping[str, Any],
    *,
    include_disabled: bool = False,
) -> List[Dict[str, Any]]:
    """Expand upstream point-cost tables into flat catalog rows (no pool metrics)."""
    rows: List[Dict[str, Any]] = []
    image_models = ((capabilities.get("image") or {}).get("models") or [])
    for model in image_models:
        if not isinstance(model, dict):
            continue
        if not include_disabled and not bool(model.get("enabled", True)):
            continue
        model_name = str(model.get("name") or "").strip()
        if not model_name:
            continue
        for item in model.get("point_cost") or []:
            if not isinstance(item, dict):
                continue
            point = _point_cost(item)
            if point is None:
                continue
            rows.append(
                {
                    "kind": "image",
                    "model_name": model_name,
                    "scene_id": "",
                    "scene_name": "",
                    "resolution": str(item.get("resolution") or "") or None,
                    "duration": None,
                    "is_audio": None,
                    "point_cost": point,
                    "enabled": bool(model.get("enabled", True)),
                    "experimental": bool(model.get("experimental", False)),
                    "verification_status": str(model.get("verification_status") or "live_verified"),
                    "risk_level": str(model.get("risk_level") or "low"),
                }
            )

    video = capabilities.get("video") or {}
    video_models = video.get("models") or []
    scenes = video.get("scenes") or []
    for model in video_models:
        if not isinstance(model, dict):
            continue
        if not include_disabled and not bool(model.get("enabled", True)):
            continue
        model_name = str(model.get("name") or "").strip()
        if not model_name:
            continue
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            scene_id = str(scene.get("scene_id") or "").strip()
            if not scene_id:
                continue
            if not include_disabled and not bool(scene.get("enabled", True)):
                continue
            for item in video_cost_table_for_scene(model, scene_id):
                point = _point_cost(item)
                if point is None:
                    continue
                audio = item.get("audio")
                is_audio = None if audio in (None, "") else bool(audio)
                rows.append(
                    {
                        "kind": "video",
                        "model_name": model_name,
                        "scene_id": scene_id,
                        "scene_name": str(scene.get("name") or scene_id),
                        "resolution": str(item.get("resolution") or "") or None,
                        "duration": _int_or_none(item.get("duration")),
                        "is_audio": is_audio,
                        "point_cost": point,
                        "enabled": bool(model.get("enabled", True)) and bool(scene.get("enabled", True)),
                        "experimental": bool(model.get("experimental", False))
                        or bool(scene.get("experimental", False)),
                        "verification_status": str(
                            scene.get("verification_status")
                            or model.get("verification_status")
                            or "unverified"
                        ),
                        "risk_level": str(
                            scene.get("risk_level") or model.get("risk_level") or "low"
                        ),
                    }
                )
    return rows


def attach_spendable_availability(
    rows: Sequence[Mapping[str, Any]],
    spendable_for_cost: Mapping[int, Sequence[int]],
) -> List[Dict[str, Any]]:
    """Attach ready_accounts / task_capacity / status using precomputed spendable lists."""
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        cost = max(0, int(item.get("point_cost") or 0))
        values = [max(0, int(v)) for v in (spendable_for_cost.get(cost) or [])]
        ready = sum(1 for value in values if value >= cost) if cost > 0 else len(values)
        capacity = sum(value // cost for value in values) if cost > 0 else 0
        item["ready_accounts"] = ready
        item["task_capacity"] = capacity
        item["status"] = availability_status(ready)
        out.append(item)
    return out


def sort_availability_items(items: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    rank = {"available": 0, "tight": 1, "unavailable": 2}

    def key(item: Mapping[str, Any]):
        return (
            rank.get(str(item.get("status") or ""), 9),
            0 if str(item.get("kind")) == "image" else 1,
            str(item.get("model_name") or ""),
            str(item.get("scene_id") or ""),
            int(item.get("point_cost") or 0),
            str(item.get("resolution") or ""),
            int(item.get("duration") or 0),
        )

    return [dict(item) for item in sorted(items, key=key)]


def public_availability_items(items: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Strip account/pool-facing fields for the user-facing catalog."""
    public_rows: List[Dict[str, Any]] = []
    for item in items:
        public_rows.append(
            {
                "kind": item.get("kind"),
                "model_name": item.get("model_name"),
                "scene_id": item.get("scene_id") or "",
                "scene_name": item.get("scene_name") or "",
                "resolution": item.get("resolution"),
                "duration": item.get("duration"),
                "is_audio": item.get("is_audio"),
                "point_cost": item.get("point_cost"),
                "status": item.get("status"),
                "verification_status": item.get("verification_status"),
                "experimental": bool(item.get("experimental")),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return public_rows


def group_items_by_model(items: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []
    for item in items:
        key = (str(item.get("kind") or ""), str(item.get("model_name") or ""))
        if key not in groups:
            groups[key] = {
                "kind": item.get("kind"),
                "model_name": item.get("model_name"),
                "experimental": bool(item.get("experimental")),
                "verification_status": item.get("verification_status"),
                "combos": [],
                "available_combos": 0,
                "total_combos": 0,
            }
            order.append(key)
        group = groups[key]
        group["combos"].append(dict(item))
        group["total_combos"] += 1
        if item.get("status") in {"available", "tight"}:
            group["available_combos"] += 1
        if bool(item.get("experimental")):
            group["experimental"] = True
    return [groups[key] for key in order]
