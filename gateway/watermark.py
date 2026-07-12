import io
from typing import List, Optional, Tuple

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_IMAGE_PIXELS = 40_000_000


class WatermarkImageError(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def image_media_type(image_format: str) -> Tuple[str, str]:
    normalized = str(image_format or "").upper()
    if normalized in {"JPEG", "JPG"}:
        return "image/jpeg", "jpg"
    if normalized == "PNG":
        return "image/png", "png"
    if normalized == "WEBP":
        return "image/webp", "webp"
    raise WatermarkImageError(415, "unsupported image format")


def detect_oreate_watermark_bbox(image: Image.Image) -> Optional[Tuple[int, int, int, int]]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    if width < 160 or height < 160:
        return None

    region_left = int(width * 0.45)
    region_top = int(height * 0.84)
    region_width = width - region_left
    region_height = height - region_top
    pixels = rgb.load()
    mask = bytearray(region_width * region_height)
    for y in range(region_top, height):
        row_offset = (y - region_top) * region_width
        for x in range(region_left, width):
            red, green, blue = pixels[x, y]
            if min(red, green, blue) >= 170 and max(red, green, blue) - min(red, green, blue) <= 70:
                mask[row_offset + x - region_left] = 1

    visited = bytearray(len(mask))
    scale = max(0.75, min(width, height) / 360)
    minimum_area = max(12, round(20 * scale * scale))
    minimum_height = max(7, round(height * 0.014))
    maximum_height = max(minimum_height, round(height * 0.06))
    maximum_width = max(24, round(width * 0.08))
    components: List[Tuple[int, int, int, int, int]] = []

    for start, present in enumerate(mask):
        if not present or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        area = 0
        min_x = region_width
        max_x = 0
        min_y = region_height
        max_y = 0
        while stack:
            current = stack.pop()
            local_y, local_x = divmod(current, region_width)
            area += 1
            min_x = min(min_x, local_x)
            max_x = max(max_x, local_x)
            min_y = min(min_y, local_y)
            max_y = max(max_y, local_y)
            for delta_y in (-1, 0, 1):
                neighbor_y = local_y + delta_y
                if neighbor_y < 0 or neighbor_y >= region_height:
                    continue
                for delta_x in (-1, 0, 1):
                    if delta_x == 0 and delta_y == 0:
                        continue
                    neighbor_x = local_x + delta_x
                    if neighbor_x < 0 or neighbor_x >= region_width:
                        continue
                    neighbor = neighbor_y * region_width + neighbor_x
                    if mask[neighbor] and not visited[neighbor]:
                        visited[neighbor] = 1
                        stack.append(neighbor)

        component_width = max_x - min_x + 1
        component_height = max_y - min_y + 1
        absolute_left = min_x + region_left
        absolute_top = min_y + region_top
        if (
            area >= minimum_area
            and 2 <= component_width <= maximum_width
            and minimum_height <= component_height <= maximum_height
            and absolute_left >= width * 0.5
            and absolute_top >= height * 0.88
        ):
            components.append(
                (
                    absolute_left,
                    absolute_top,
                    max_x + region_left + 1,
                    max_y + region_top + 1,
                    area,
                )
            )

    if len(components) < 7:
        return None
    components.sort(key=lambda item: item[0])
    candidates: List[Tuple[float, Tuple[int, int, int, int]]] = []
    for group_size in range(7, min(10, len(components)) + 1):
        for offset in range(0, len(components) - group_size + 1):
            group = components[offset : offset + group_size]
            left = min(item[0] for item in group)
            top = min(item[1] for item in group)
            right = max(item[2] for item in group)
            bottom = max(item[3] for item in group)
            group_width = right - left
            group_height = bottom - top
            centers_y = [(item[1] + item[3]) / 2 for item in group]
            gaps = [max(0, group[index + 1][0] - group[index][2]) for index in range(len(group) - 1)]
            if not (
                width * 0.22 <= group_width <= width * 0.5
                and height * 0.018 <= group_height <= height * 0.065
                and left >= width * 0.5
                and right >= width * 0.82
                and top >= height * 0.88
                and bottom <= height * 0.985
                and max(centers_y) - min(centers_y) <= height * 0.03
                and (not gaps or max(gaps) <= width * 0.055)
            ):
                continue
            score = abs(group_size - 8) * 10 + abs(group_width / width - 0.34) * 10
            candidates.append((score, (left, top, right, bottom)))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def watermark_free_image_bytes(payload: bytes) -> Tuple[bytes, str, bool]:
    try:
        with Image.open(io.BytesIO(payload)) as source:
            image_format = str(source.format or "")
            media_type, _extension = image_media_type(image_format)
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise WatermarkImageError(413, "image dimensions are too large")
            source.load()
            image = ImageOps.exif_transpose(source)
            watermark_bbox = detect_oreate_watermark_bbox(image)
            if watermark_bbox is None:
                return payload, media_type, False

            width, height = image.size
            crop_bottom = watermark_bbox[1] - max(4, round(height * 0.01))
            if crop_bottom < round(height * 0.75):
                return payload, media_type, False
            crop_width = round(crop_bottom * width / height)
            if crop_width <= 0 or crop_width > width:
                return payload, media_type, False
            crop_left = (width - crop_width) // 2
            cropped = image.crop((crop_left, 0, crop_left + crop_width, crop_bottom))
            cleaned = cropped.resize((width, height), Image.Resampling.LANCZOS)

            output = io.BytesIO()
            if image_format.upper() in {"JPEG", "JPG"}:
                cleaned.convert("RGB").save(output, format="JPEG", quality=95, optimize=True)
            elif image_format.upper() == "PNG":
                cleaned.save(output, format="PNG", optimize=True)
            elif image_format.upper() == "WEBP":
                cleaned.save(output, format="WEBP", quality=95, method=6)
            else:
                raise WatermarkImageError(415, "unsupported image format")
            return output.getvalue(), media_type, True
    except WatermarkImageError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        raise WatermarkImageError(415, "invalid image asset")
