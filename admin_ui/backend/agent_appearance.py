"""UI-only avatars, saved atomically with the Agent and its SQLite backups."""
import base64
import binascii
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_STORED_BYTES = 256 * 1024


def normalize_avatar(value: str | None) -> str | None:
    """Accept raster uploads only; discard metadata/animation and bound DB growth."""
    if value is None:
        return None
    if len(value) > (MAX_UPLOAD_BYTES * 4 // 3) + 128:
        raise ValueError("Avatar must be 2 MB or smaller")
    header, separator, encoded = value.partition(",")
    formats = {"data:image/png;base64": "PNG", "data:image/jpeg;base64": "JPEG",
               "data:image/webp;base64": "WEBP"}
    if not separator or header not in formats:
        raise ValueError("Choose a PNG, JPEG, or WebP image")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise ValueError("Avatar must be 2 MB or smaller")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=[formats[header]]) as source:
                if source.format != formats[header]:
                    raise ValueError("Avatar type does not match its contents")
                if source.width * source.height > MAX_PIXELS:
                    raise ValueError("Avatar must contain at most 16 million pixels")
                source.load()
                thumbnail = ImageOps.exif_transpose(source).convert("RGBA")
                thumbnail.thumbnail((256, 256), Image.Resampling.LANCZOS)
                # Fresh pixels remove EXIF, comments, ICC profiles and other metadata.
                clean = Image.new("RGBA", thumbnail.size)
                clean.paste(thumbnail)
                output = io.BytesIO()
                clean.save(output, format="PNG", optimize=True)
        if output.tell() > MAX_STORED_BYTES:
            raise ValueError("Avatar is too complex; choose a smaller image")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except (binascii.Error, UnidentifiedImageError, OSError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Avatar is not a valid supported image") from exc
