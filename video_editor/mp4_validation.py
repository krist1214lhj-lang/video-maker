from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .project_final_export import is_valid_media_file, probe_video_metadata

MIN_MP4_BYTES = 2048
INVALID_MP4_PREFIXES = (b"MOCK", b"MOCK_STILL_HOLD")


def compute_prompt_hash(prompt: str) -> str:
    normalized = re.sub(r"\s+", " ", (prompt or "").strip())
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def cache_bust_video_url(base_url: str, *, version: int | str | None = None) -> str:
    if not base_url:
        return base_url
    if "?" in base_url and re.search(r"[?&]v=", base_url):
        return base_url
    token = str(version if version is not None else int(datetime.now(timezone.utc).timestamp()))
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}v={token}"


def validate_playable_mp4(path: Path) -> dict:
    errors: list[str] = []
    resolved = path.resolve() if path.exists() else path

    if not path.exists():
        return {
            "valid": False,
            "errors": ["파일이 존재하지 않습니다."],
            "file_size_bytes": 0,
            "duration_seconds": None,
            "container_ok": False,
        }

    try:
        file_size_bytes = path.stat().st_size
    except OSError as error:
        return {
            "valid": False,
            "errors": [f"파일 크기를 읽을 수 없습니다: {error}"],
            "file_size_bytes": 0,
            "duration_seconds": None,
            "container_ok": False,
        }

    if file_size_bytes <= 0:
        errors.append("파일 크기가 0바이트입니다.")
    elif file_size_bytes < MIN_MP4_BYTES:
        errors.append(f"파일 크기가 너무 작습니다 ({file_size_bytes} bytes).")

    try:
        prefix = path.read_bytes()[:16]
    except OSError as error:
        errors.append(f"파일을 읽을 수 없습니다: {error}")
        prefix = b""

    if prefix.startswith(INVALID_MP4_PREFIXES):
        errors.append("MOCK/placeholder mp4 파일입니다.")

    probed = probe_video_metadata(path)
    duration_seconds = probed.get("duration_seconds")
    container_ok = is_valid_media_file(path)

    if not container_ok:
        errors.append("ffprobe가 mp4 container를 읽지 못했습니다.")
    if duration_seconds is None:
        errors.append("ffprobe duration을 읽을 수 없습니다.")
    elif float(duration_seconds) <= 0:
        errors.append("duration이 0초입니다.")

    return {
        "valid": not errors,
        "errors": errors,
        "file_size_bytes": file_size_bytes,
        "duration_seconds": duration_seconds,
        "duration": probed.get("duration") or "unknown",
        "file_size": probed.get("file_size") or str(file_size_bytes),
        "container_ok": container_ok,
        "path": str(resolved),
    }


def quarantine_invalid_mp4(path: Path, *, reason: str | list[str] | None = None) -> Path | None:
    if not path.exists():
        return None

    quarantine_dir = path.parent / ".quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = quarantine_dir / f"{path.stem}_{timestamp}{path.suffix}"
    counter = 0
    while target.exists():
        counter += 1
        target = quarantine_dir / f"{path.stem}_{timestamp}_{counter}{path.suffix}"

    shutil.move(str(path), str(target))
    sidecar = path.with_suffix(".json")
    if sidecar.exists():
        try:
            shutil.move(str(sidecar), str(target.with_suffix(".json")))
        except OSError:
            pass

    reason_text = reason
    if isinstance(reason, list):
        reason_text = "; ".join(reason)
    note_path = target.with_suffix(".quarantine.txt")
    note_path.write_text(
        "\n".join(
            [
                f"quarantined_at: {datetime.now(timezone.utc).isoformat()}",
                f"source: {path}",
                f"reason: {reason_text or 'invalid mp4'}",
            ]
        ),
        encoding="utf-8",
    )
    return target


def delete_invalid_mp4(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    sidecar = path.with_suffix(".json")
    if sidecar.exists():
        try:
            sidecar.unlink()
        except OSError:
            pass
    return True
