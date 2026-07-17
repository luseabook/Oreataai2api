"""Shared media upload and MP4 metadata helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

MEDIA_UPLOAD_EXTENSIONS = {"jpg", "jpeg", "png", "bmp", "webp", "mp4", "mov"}
IMAGE_UPLOAD_EXTENSIONS = {"jpg", "jpeg", "png", "bmp", "webp"}
VIDEO_UPLOAD_EXTENSIONS = {"mp4", "mov"}


def normalized_file_extension(value: Any) -> str:
    return str(value or "").lstrip(".").lower()


def is_media_upload_extension(value: Any) -> bool:
    return normalized_file_extension(value) in MEDIA_UPLOAD_EXTENSIONS


def response_data_object(body: Any) -> Dict[str, Any]:
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


def parse_mp4_video_metadata(data: bytes) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    if len(data) < 8:
        return metadata

    def read_uint(offset: int, size: int) -> Optional[int]:
        if offset < 0 or offset + size > len(data):
            return None
        return int.from_bytes(data[offset:offset + size], "big")

    def fixed_16_16(offset: int) -> Optional[int]:
        raw = read_uint(offset, 4)
        if raw is None:
            return None
        value = raw / 65536
        return int(round(value)) if value > 0 else None

    containers = {"moov", "trak", "mdia", "minf", "stbl", "edts", "udta"}
    stack: List[Tuple[int, int]] = [(0, len(data))]
    while stack:
        start, end = stack.pop()
        pos = start
        while pos + 8 <= end:
            box_size = read_uint(pos, 4)
            if box_size is None:
                break
            try:
                box_type = data[pos + 4:pos + 8].decode("latin1")
            except Exception:
                break
            header_size = 8
            if box_size == 1:
                large_size = read_uint(pos + 8, 8)
                if large_size is None:
                    break
                box_size = large_size
                header_size = 16
            elif box_size == 0:
                box_size = end - pos
            if box_size < header_size:
                break
            content_start = pos + header_size
            box_end = pos + box_size
            if box_end > end or box_end <= pos:
                break

            if box_type == "mvhd" and "videoDurationSec" not in metadata and content_start + 20 <= box_end:
                version = data[content_start]
                if version == 0:
                    timescale = read_uint(content_start + 12, 4)
                    duration = read_uint(content_start + 16, 4)
                elif version == 1 and content_start + 32 <= box_end:
                    timescale = read_uint(content_start + 20, 4)
                    duration = read_uint(content_start + 24, 8)
                else:
                    timescale = None
                    duration = None
                if timescale and duration:
                    metadata["videoDurationSec"] = round(duration / timescale, 3)
            elif box_type == "tkhd":
                version = data[content_start] if content_start < box_end else 0
                size_offset = content_start + (88 if version == 1 else 76)
                width = fixed_16_16(size_offset)
                height = fixed_16_16(size_offset + 4)
                if width and height:
                    metadata["videoWidth"] = max(int(metadata.get("videoWidth") or 0), width)
                    metadata["videoHeight"] = max(int(metadata.get("videoHeight") or 0), height)
            elif box_type in containers:
                stack.append((content_start, box_end))
            pos = box_end
    return metadata


def first_upload_key_entry(key_list: Any) -> Dict[str, Any]:
    if isinstance(key_list, list) and key_list:
        first = key_list[0]
        return first if isinstance(first, dict) else {}
    if isinstance(key_list, dict) and key_list:
        for key in sorted(key_list.keys(), key=str):
            first = key_list[key]
            return first if isinstance(first, dict) else {}
    return {}
