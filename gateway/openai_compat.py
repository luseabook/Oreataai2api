"""Pure OpenAI-compatible media request/response mapping helpers.

This module deliberately performs no database or network access.  Keeping the
compatibility contract pure makes it possible to evolve provider execution
without changing the public API surface.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict, Mapping, Optional


VIDEO_ID_RE = re.compile(r"^video_([1-9][0-9]*)$")

IMAGE_SIZE_RATIOS: Dict[str, Optional[str]] = {
    "auto": None,
    "1024x1024": "1:1",
    "1536x1024": "3:2",
    "1024x1536": "2:3",
}

IMAGE_ASPECT_RATIOS = (
    ("21:9", 21 / 9),
    ("16:9", 16 / 9),
    ("3:2", 3 / 2),
    ("4:3", 4 / 3),
    ("5:4", 5 / 4),
    ("1:1", 1.0),
    ("4:5", 4 / 5),
    ("3:4", 3 / 4),
    ("2:3", 2 / 3),
    ("9:16", 9 / 16),
)
IMAGE_ASPECT_RATIO_TOLERANCE = 0.02
IMAGE_DIMENSION_MIN = 64
IMAGE_DIMENSION_MAX = 8192
IMAGE_SIZE_RE = re.compile(r"^([1-9][0-9]*)x([1-9][0-9]*)$")

VIDEO_SIZE_RATIOS: Dict[str, Optional[str]] = {
    "auto": None,
    "1024x1024": "1:1",
    "1280x720": "16:9",
    "720x1280": "9:16",
    "1920x1080": "16:9",
    "1080x1920": "9:16",
}

DEFAULT_MODEL_ALIASES = {
    "image": {
        "gpt-image-1",
        "gpt-image-1.5",
        "gpt-image-1-mini",
    },
    "video": {
        "sora-2",
        "sora-2-pro",
    },
}

VIDEO_STATUS_MAP = {
    "created": ("queued", 0),
    "queued": ("queued", 0),
    "running": ("in_progress", 25),
    "submitted": ("in_progress", 65),
    "hydrating": ("in_progress", 75),
    "completed": ("completed", 100),
    "failed": ("failed", 100),
    "expired": ("failed", 100),
    "cancelled": ("cancelled", 100),
}

IMAGE_REFERENCE_EXTENSIONS = {"jpg", "jpeg", "png", "bmp", "webp"}
VIDEO_REFERENCE_EXTENSIONS = {"mp4", "mov"}


class OpenAICompatError(ValueError):
    """A public-contract error that can be rendered in OpenAI's envelope."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "invalid_request_error",
        param: Optional[str] = None,
        code: Optional[str] = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.param = param
        self.code = code
        self.status_code = status_code


def encode_video_id(task_id: int) -> str:
    try:
        value = int(task_id)
    except (TypeError, ValueError) as exc:
        raise OpenAICompatError(
            "video task id must be a positive integer",
            param="video_id",
            code="invalid_video_id",
        ) from exc
    if value <= 0:
        raise OpenAICompatError(
            "video task id must be a positive integer",
            param="video_id",
            code="invalid_video_id",
        )
    return f"video_{value}"


def decode_video_id(video_id: str) -> int:
    match = VIDEO_ID_RE.fullmatch(str(video_id or ""))
    if not match:
        raise OpenAICompatError(
            "invalid video id",
            param="video_id",
            code="invalid_video_id",
            status_code=404,
        )
    return int(match.group(1))


def _size_to_ratio(
    size: Optional[str],
    mapping: Mapping[str, Optional[str]],
    *,
    media_kind: str,
) -> Optional[str]:
    normalized = str(size or "auto").strip().lower()
    if normalized not in mapping:
        raise OpenAICompatError(
            f"unsupported {media_kind} size: {size}",
            param="size",
            code="invalid_size",
        )
    return mapping[normalized]


def image_size_to_ratio(size: Optional[str]) -> Optional[str]:
    normalized = str(size or "auto").strip().lower()
    if normalized in IMAGE_SIZE_RATIOS:
        return IMAGE_SIZE_RATIOS[normalized]

    match = IMAGE_SIZE_RE.fullmatch(normalized)
    if match:
        width, height = (int(value) for value in match.groups())
        if (
            IMAGE_DIMENSION_MIN <= width <= IMAGE_DIMENSION_MAX
            and IMAGE_DIMENSION_MIN <= height <= IMAGE_DIMENSION_MAX
        ):
            requested_ratio = width / height
            ratio_name, ratio_value = min(
                IMAGE_ASPECT_RATIOS,
                key=lambda item: abs(requested_ratio - item[1]) / item[1],
            )
            relative_error = abs(requested_ratio - ratio_value) / ratio_value
            if relative_error <= IMAGE_ASPECT_RATIO_TOLERANCE:
                return ratio_name

    raise OpenAICompatError(
        f"unsupported image size: {size}",
        param="size",
        code="invalid_size",
    )


def video_size_to_ratio(size: Optional[str]) -> Optional[str]:
    return _size_to_ratio(size, VIDEO_SIZE_RATIOS, media_kind="video")


def video_size_to_resolution(size: Optional[str]) -> Optional[str]:
    normalized = str(size or "auto").strip().lower()
    if normalized == "auto":
        return None
    video_size_to_ratio(normalized)
    width, height = (int(part) for part in normalized.split("x", 1))
    return str(min(width, height))


def resolve_openai_model(kind: str, requested_model: Optional[str], config: Mapping[str, Any]) -> str:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in {"image", "video"}:
        raise OpenAICompatError(
            f"unsupported media kind: {kind}",
            param="model",
            code="invalid_model",
        )

    oreate = config.get("oreate") if isinstance(config.get("oreate"), Mapping) else {}
    compat = config.get("openai_compat") if isinstance(config.get("openai_compat"), Mapping) else {}
    alias_key = f"{normalized_kind}_model_aliases"
    aliases = compat.get(alias_key) if isinstance(compat.get(alias_key), Mapping) else {}
    default_key = f"default_{normalized_kind}_model"
    provider_default = str(oreate.get(default_key) or "").strip()
    model = str(requested_model or "").strip()

    if not model or model in DEFAULT_MODEL_ALIASES[normalized_kind]:
        if not provider_default:
            raise OpenAICompatError(
                f"default {normalized_kind} model is not configured",
                param="model",
                code="model_not_configured",
                status_code=503,
            )
        return provider_default
    mapped = aliases.get(model)
    return str(mapped).strip() if mapped not in (None, "") else model


def openai_model_name_for_provider(kind: str, provider_model: Any, config: Mapping[str, Any]) -> str:
    normalized_kind = str(kind or "").strip().lower()
    provider_name = str(provider_model or "").strip()
    if normalized_kind not in {"image", "video"} or not provider_name:
        return provider_name
    canonical = "gpt-image-1" if normalized_kind == "image" else "sora-2"
    try:
        if resolve_openai_model(normalized_kind, canonical, config) == provider_name:
            return canonical
    except OpenAICompatError:
        pass
    compat = config.get("openai_compat") if isinstance(config.get("openai_compat"), Mapping) else {}
    aliases = compat.get(f"{normalized_kind}_model_aliases") if isinstance(compat.get(f"{normalized_kind}_model_aliases"), Mapping) else {}
    for alias, target in aliases.items():
        if str(target or "").strip() == provider_name:
            return str(alias)
    return provider_name


def openai_error_payload(
    message: str,
    *,
    error_type: str = "invalid_request_error",
    param: Optional[str] = None,
    code: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "error": {
            "message": str(message),
            "type": str(error_type),
            "param": param,
            "code": code,
        }
    }


def _reference_object_path(item: Mapping[str, Any]) -> str:
    for key in ("object", "bosUrl", "bos_url", "bosObjectPath", "url"):
        candidate = item.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return ""


def _reference_extension(item: Mapping[str, Any]) -> str:
    explicit = item.get("fileExt") or item.get("doc_type")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lstrip(".").lower()
    content_type = item.get("contentType")
    if isinstance(content_type, str):
        lowered = content_type.strip().lower()
        if lowered.startswith("image/"):
            return lowered.partition("/")[2]
        if lowered.startswith("video/"):
            return lowered.partition("/")[2]
    return Path(_reference_object_path(item)).suffix.lstrip(".").lower()


def split_input_reference_attachments(input_reference: Any) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    if not isinstance(input_reference, list) or not input_reference:
        raise OpenAICompatError(
            "input_reference must contain at least one uploaded attachment",
            param="input_reference",
            code="invalid_input_reference",
        )
    reference_images: list[Dict[str, Any]] = []
    reference_videos: list[Dict[str, Any]] = []
    for item in input_reference:
        if not isinstance(item, Mapping):
            raise OpenAICompatError(
                "input_reference items must be uploaded attachment objects",
                param="input_reference",
                code="invalid_input_reference",
            )
        attachment = dict(item)
        if not _reference_object_path(attachment):
            raise OpenAICompatError(
                "input_reference items must contain an uploaded object path",
                param="input_reference",
                code="invalid_input_reference",
            )
        extension = _reference_extension(attachment)
        if extension in IMAGE_REFERENCE_EXTENSIONS:
            reference_images.append(attachment)
            continue
        if extension in VIDEO_REFERENCE_EXTENSIONS:
            reference_videos.append(attachment)
            continue
        raise OpenAICompatError(
            "input_reference contains an unsupported media type",
            param="input_reference",
            code="invalid_input_reference",
        )
    return reference_images, reference_videos


def video_status(status: Any) -> tuple[str, int]:
    return VIDEO_STATUS_MAP.get(str(status or "").strip().lower(), ("queued", 0))


def _size_from_task(task: Mapping[str, Any]) -> str:
    ratio = str(task.get("ratio") or "")
    resolution = str(task.get("resolution") or "")
    if ratio == "16:9":
        return "1920x1080" if resolution in {"1080", "1080p"} else "1280x720"
    if ratio == "9:16":
        return "1080x1920" if resolution in {"1080", "1080p"} else "720x1280"
    if ratio == "1:1":
        return "1024x1024"
    return "auto"


def task_to_video_object(
    task: Mapping[str, Any],
    *,
    requested_model: Optional[str] = None,
    requested_size: Optional[str] = None,
) -> Dict[str, Any]:
    public_status, progress = video_status(task.get("status"))
    result: Dict[str, Any] = {
        "id": encode_video_id(int(task.get("id") or 0)),
        "object": "video",
        "created_at": int(float(task.get("created_at") or 0)),
        "status": public_status,
        "progress": progress,
        "model": str(requested_model or task.get("model_name") or ""),
        "seconds": str(task.get("duration") or ""),
        "size": str(requested_size or _size_from_task(task)),
    }
    if public_status == "failed":
        # Keep the message short and free of internal details (paths, upstream
        # response bodies) that may have leaked into the task error text.
        result["error"] = {
            "code": str(task.get("error_code") or "video_generation_failed"),
            "message": str(task.get("error_message") or "video generation failed")[:200],
        }
    return result


def openai_model_list(caps: Mapping[str, Any], config: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the conservative OpenAI Model-list shape from visible capabilities."""

    records: list[Dict[str, Any]] = []
    seen: set[str] = set()

    def add(model_id: Any) -> None:
        value = str(model_id or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        records.append(
            {
                "id": value,
                "object": "model",
                "created": 0,
                "owned_by": "oreateai-gateway",
            }
        )

    compat = config.get("openai_compat") if isinstance(config.get("openai_compat"), Mapping) else {}
    for kind, canonical_alias in (("image", "gpt-image-1"), ("video", "sora-2")):
        models = caps.get(kind, {}).get("models") if isinstance(caps.get(kind), Mapping) else []
        visible_names = {
            str(item.get("name") or "").strip()
            for item in (models or [])
            if isinstance(item, Mapping) and bool(item.get("enabled", True)) and str(item.get("name") or "").strip()
        }
        if not visible_names:
            continue
        default_target = resolve_openai_model(kind, canonical_alias, config)
        if default_target in visible_names:
            add(canonical_alias)
        aliases = compat.get(f"{kind}_model_aliases") if isinstance(compat.get(f"{kind}_model_aliases"), Mapping) else {}
        for alias, target in aliases.items():
            if str(target or "").strip() in visible_names:
                add(alias)
        for name in sorted(visible_names):
            add(name)
    return {"object": "list", "data": records}
