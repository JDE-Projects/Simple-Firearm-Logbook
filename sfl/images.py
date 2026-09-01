"""Photo handling: decompression-bomb size guards, mime sniffing, the
import-time re-encode to JPEG, and the shared wording for photos refused
during import."""
import os

from PIL import Image, ImageOps

from sfl import config

# Pillow only raises DecompressionBombError above 2x this value (it just
# warns between 1x and 2x), so setting this to 32,000,000 is what makes the
# hard refusal actually land at the documented ~64 megapixels instead of
# quietly doubling to ~128. Applies to every Image.open() call, so it covers
# photo import everywhere in the app.
Image.MAX_IMAGE_PIXELS = 32_000_000


def _photo_source_too_large(path: str) -> bool:
    """Decompression-bomb guard: refuse a photo source bigger than
    MAX_IMAGE_BYTES before it is ever opened."""
    return os.path.getsize(path) > config.MAX_IMAGE_BYTES


def _photo_bytes_too_large(data: bytes) -> bool:
    """Decompression-bomb guard for raw bytes: refuse a photo source bigger
    than MAX_IMAGE_BYTES before it is ever opened. Same ceiling as
    _photo_source_too_large, for the drag-and-drop import path where the
    source is already-decoded bytes rather than a file on disk."""
    return len(data) > config.MAX_IMAGE_BYTES


def _photo_b64_too_large(raw: str) -> bool:
    """Decompression-bomb guard for a still-encoded drag-and-drop payload:
    estimate the decoded size from the base64 string length (a safe upper
    bound, never smaller than the real decoded size) so an oversized drop is
    refused before it is ever decoded into memory."""
    return (len(raw) * 3) // 4 > config.MAX_IMAGE_BYTES


def _sniff_image_mime(data: bytes) -> str:
    """Guess an image's mime type from its file signature, independent of
    whatever extension it's stored under. Used for embedding photos as
    base64 data URIs and for in-app previews."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    return "image/jpeg"


def optimize_image_to_jpeg(src: str, target: str, max_edge: int = config.PHOTO_MAX_EDGE,
                           quality: int = config.PHOTO_JPEG_QUALITY) -> None:
    """Read an image, apply its stored EXIF rotation, scale the long edge down
    to max_edge if it is larger, and write the result to target as a JPEG.

    src may be a path or a file-like object (e.g. io.BytesIO), since PIL's
    Image.open accepts either. target is always a path.

    Never reads or writes anything but src (read) and target (write), so the
    user's original is always safe. Raises on any failure (unreadable file,
    unsupported data, write error) so the caller can refuse to store a broken
    or oversized photo rather than pretend the import worked.
    """
    with Image.open(src) as opened:
        # Honor the orientation a phone camera records in EXIF, so a photo that
        # looks upright in the gallery isn't stored sideways.
        img = ImageOps.exif_transpose(opened)
        # JPEG has no transparency, so flatten any alpha channel onto white
        # instead of letting it turn black.
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, (255, 255, 255))
            flattened.paste(rgba, mask=rgba.split()[-1])
            img = flattened
        elif img.mode != "RGB":
            img = img.convert("RGB")
        long_edge = max(img.size)
        if long_edge > max_edge:
            scale = max_edge / long_edge
            new_size = (max(1, round(img.size[0] * scale)),
                        max(1, round(img.size[1] * scale)))
            img = img.resize(new_size, Image.LANCZOS)
        img.save(target, "JPEG", quality=quality)


# Wording for each refusal reason, used by photo_failure_warning below.
# "single"/"plural" are the full sentence used when only one reason applies;
# "clause_single"/"clause_plural" are the short fragment used when a warning
# has to list more than one reason.
_PHOTO_FAILURE_TEMPLATES = {
    "not_image": {
        "single": "1 file wasn't an image and wasn't added.",
        "plural": "{n} files weren't images and weren't added.",
        "clause_single": "1 wasn't an image",
        "clause_plural": "{n} weren't images",
    },
    "damaged": {
        "single": "1 image was damaged and wasn't added.",
        "plural": "{n} images were damaged and weren't added.",
        "clause_single": "1 was damaged",
        "clause_plural": "{n} were damaged",
    },
    "too_large": {
        "single": "1 image was too large and wasn't added.",
        "plural": "{n} images were too large and weren't added.",
        "clause_single": "1 was too large",
        "clause_plural": "{n} were too large",
    },
}


def photo_failure_warning(fail_counts):
    """Builds the user-facing warning for refused photos out of a
    {"not_image": n, "damaged": n, "too_large": n} count dict, so add_photos
    and add_photos_from_data always word it the same way. Returns None if
    nothing was refused."""
    not_image = fail_counts.get("not_image", 0)
    damaged = fail_counts.get("damaged", 0)
    too_large = fail_counts.get("too_large", 0)
    total = not_image + damaged + too_large
    if total == 0:
        return None

    buckets = [b for b in (("not_image", not_image), ("damaged", damaged), ("too_large", too_large)) if b[1]]
    if len(buckets) == 1:
        kind, n = buckets[0]
        template = _PHOTO_FAILURE_TEMPLATES[kind]
        return template["single"] if n == 1 else template["plural"].format(n=n)

    clauses = []
    for kind, n in buckets:
        template = _PHOTO_FAILURE_TEMPLATES[kind]
        clauses.append(template["clause_single"] if n == 1 else template["clause_plural"].format(n=n))
    return f"{total} files weren't added: " + ", ".join(clauses) + "."
