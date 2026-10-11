"""OCS-JS 2026 image optimize payload -> OpenAI Responses API.

OCS supplies ordered Base64 data URLs and corresponding [图片N] references in
suggestion_title / suggestion_options. Never fetch user-provided URLs server-side.
"""
import base64
import binascii
import hashlib
import re

MAX_IMAGES = 10
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024
DATA_URL_RE = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,([A-Za-z0-9+/=\r\n]+)$", re.I)


class ImagePayloadError(ValueError):
    pass


def normalize_images(images):
    """Validate, limit and return OpenAI-compatible data URLs in OCS order."""
    if images is None or images == "":
        return []
    if not isinstance(images, list):
        raise ImagePayloadError("images 必须是 Base64 图片数组")
    if len(images) > MAX_IMAGES:
        raise ImagePayloadError(f"每题最多允许 {MAX_IMAGES} 张图片")
    normalized = []
    total = 0
    for index, item in enumerate(images, 1):
        if not isinstance(item, str):
            raise ImagePayloadError(f"第 {index} 张图片不是 Data URL")
        match = DATA_URL_RE.fullmatch(item.strip())
        if not match:
            raise ImagePayloadError(f"第 {index} 张图片格式不正确（需要 Base64 Data URL）")
        encoded = re.sub(r"\s+", "", match.group(2))
        if len(encoded) > (MAX_IMAGE_BYTES * 4 // 3 + 16):
            raise ImagePayloadError(f"第 {index} 张图片太大")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImagePayloadError(f"第 {index} 张图片 Base64 无效") from exc
        total += len(raw)
        if len(raw) > MAX_IMAGE_BYTES or total > MAX_TOTAL_IMAGE_BYTES:
            raise ImagePayloadError("图片总大小或单张大小超出限制")
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif raw.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif raw.startswith((b"GIF87a", b"GIF89a")):
            mime = "image/gif"
        elif raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            raise ImagePayloadError(f"第 {index} 张图片不是支持的 PNG/JPEG/GIF/WebP 文件")
        # Some OCS GM_xmlhttpRequest fallbacks label JPEG data as PNG.
        normalized.append(f"data:{mime};base64,{encoded}")
    return normalized


def build_responses_input(prompt, images):
    """Preserve old text-only payload; use multimodal content for image questions."""
    if not images:
        return [{"role": "user", "content": prompt}]
    content = [{"type": "input_text", "text": prompt}]
    content.extend({"type": "input_image", "image_url": image, "detail": "auto"} for image in images)
    return [{"role": "user", "content": content}]


def image_fingerprint(images, suggested_title="", suggested_options=""):
    digest = hashlib.sha256()
    for data_url in images:
        digest.update(data_url.encode("ascii"))
        digest.update(b"\0")
    digest.update(suggested_title.encode("utf-8"))
    digest.update(b"\0")
    digest.update(suggested_options.encode("utf-8"))
    return digest.hexdigest()
