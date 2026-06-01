from __future__ import annotations

import hashlib
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[misc, assignment]

OPENAI_REFERENCE_MAX_EDGE = 1024
OPENAI_REFERENCE_MAX_BYTES = 20 * 1024 * 1024
SUPPORTED_REFERENCE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def inspect_reference_image_file(src_path: Path) -> dict | None:
    if Image is None:
        print("[reference] Pillow is not installed; cannot inspect reference images.")
        return None
    if not src_path.is_file():
        return None
    if src_path.suffix.lower() not in SUPPORTED_REFERENCE_EXTENSIONS:
        return None

    try:
        stat = src_path.stat()
    except OSError as exc:
        print(f"[reference] skip unreadable file {src_path.name}: {exc}")
        return None

    if stat.st_size <= 0 or stat.st_size > OPENAI_REFERENCE_MAX_BYTES:
        print(
            f"[reference] skip {src_path.name}: file size out of range "
            f"({stat.st_size} bytes)"
        )
        return None

    try:
        with Image.open(src_path) as image:
            image.verify()
        with Image.open(src_path) as image:
            width, height = image.size
            if width <= 0 or height <= 0:
                return None
            return {
                "path": src_path,
                "extension": src_path.suffix.lower(),
                "mode": image.mode,
                "size": (width, height),
                "file_size": stat.st_size,
            }
    except Exception as exc:
        print(f"[reference] skip invalid image {src_path.name}: {exc}")
        return None


def openai_reference_cache_path(src_path: Path) -> Path:
    cache_dir = src_path.parent / ".openai_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    stat = src_path.stat()
    cache_key = f"{src_path.name}:{stat.st_mtime_ns}:{stat.st_size}"
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{src_path.stem}_{digest}_openai.png"


def log_openai_reference_conversion(
    *,
    selected: Path,
    original_mode: str,
    converted_path: Path,
    converted_mode: str,
    converted_size: tuple[int, int],
    character_name: str = "",
) -> None:
    print(f"[reference] selected: {selected}")
    print(f"[reference] original mode: {original_mode}")
    print(f"[reference] converted for OpenAI: {converted_path}")
    print(f"[reference] converted mode: {converted_mode}")
    print(f"[reference] converted size: {converted_size[0]}x{converted_size[1]}")
    if character_name:
        print(f"[image] active image provider: OpenAI")
        print(f"[image] reference character: {character_name}")


def prepare_openai_reference_image(
    src_path: Path,
    *,
    character_name: str = "",
) -> tuple[Path | None, str | None, dict]:
    meta = inspect_reference_image_file(src_path)
    if not meta:
        return None, "reference image could not be opened or validated", meta or {}

    cache_path = openai_reference_cache_path(src_path)
    try:
        source_mtime = src_path.stat().st_mtime_ns
        if cache_path.is_file() and cache_path.stat().st_mtime_ns >= source_mtime:
            with Image.open(cache_path) as cached:
                log_openai_reference_conversion(
                    selected=src_path,
                    original_mode=str(meta.get("mode", "")),
                    converted_path=cache_path,
                    converted_mode=cached.mode,
                    converted_size=cached.size,
                    character_name=character_name,
                )
            return cache_path, None, meta

        with Image.open(src_path) as image:
            converted = image.convert("RGB")
            converted.thumbnail(
                (OPENAI_REFERENCE_MAX_EDGE, OPENAI_REFERENCE_MAX_EDGE),
                Image.Resampling.LANCZOS,
            )
            converted.save(cache_path, format="PNG", optimize=True)

        with Image.open(cache_path) as cached:
            log_openai_reference_conversion(
                selected=src_path,
                original_mode=str(meta.get("mode", "")),
                converted_path=cache_path,
                converted_mode=cached.mode,
                converted_size=cached.size,
                character_name=character_name,
            )
        return cache_path, None, meta
    except Exception as exc:
        print(f"[reference] conversion failed for {src_path.name}: {exc}")
        return None, str(exc), meta


def is_openai_reference_image_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "invalid_image",
            "invalid_image_file",
            "unsupported image",
            "unsupported file",
            "image file",
        )
    )
