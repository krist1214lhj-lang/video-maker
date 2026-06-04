from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from video_providers.media_paths import normalize_web_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_BASE_URL_FILE = PROJECT_ROOT / "config" / "public_base_url.txt"

REPLICATE_NGROK_REQUIRED_MESSAGE = "Replicate 영상 생성을 위해 NGROK public URL이 필요합니다."

LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "0.0.0.0"}
PUBLIC_IMAGE_PATH_PREFIXES = ("/generated_images/", "/projects/", "/static/")


def get_public_base_url() -> str:
    env_value = (os.getenv("PUBLIC_BASE_URL") or os.getenv("NGROK_URL") or "").strip().rstrip("/")
    file_value = ""
    if PUBLIC_BASE_URL_FILE.exists():
        file_value = PUBLIC_BASE_URL_FILE.read_text(encoding="utf-8").strip().rstrip("/")
        if file_value.startswith("#"):
            file_value = ""

    if env_value and file_value and env_value != file_value:
        print(
            "[public-base-url-warning] PUBLIC_BASE_URL env and config/public_base_url.txt differ. "
            f"env={env_value} file={file_value} (using env)"
        )

    if env_value:
        return env_value
    if file_value:
        return file_value
    return ""


def normalize_image_web_path(image_path: str) -> str:
    normalized = normalize_web_path(image_path, keep_query=False)
    if not normalized:
        return ""

    parsed = urlparse(normalized)
    if parsed.scheme in {"http", "https"} and parsed.path:
        return parsed.path if parsed.path.startswith("/") else f"/{parsed.path}"

    return normalized if normalized.startswith("/") else f"/{normalized}"


def build_public_image_url(image_path: str) -> str:
    public_base_url = get_public_base_url()
    if not public_base_url:
        raise RuntimeError(REPLICATE_NGROK_REQUIRED_MESSAGE)

    if not public_base_url.startswith(("https://", "http://")):
        raise RuntimeError("PUBLIC_BASE_URL/NGROK_URL must include https:// or http://.")

    normalized_input = normalize_web_path(image_path, keep_query=True)
    parsed = urlparse(normalized_input.split("?", 1)[0])
    if parsed.scheme in {"http", "https"}:
        hostname = (parsed.hostname or "").lower()
        if hostname not in LOCAL_HOSTNAMES:
            return normalize_web_path(normalized_input, keep_query=True)

    web_path = normalize_image_web_path(image_path)
    if not web_path:
        raise RuntimeError(REPLICATE_NGROK_REQUIRED_MESSAGE)

    built = urljoin(public_base_url.rstrip("/") + "/", web_path.lstrip("/"))
    return normalize_web_path(built, keep_query=True)


def validate_replicate_start_image_url(url: str) -> tuple[bool, str]:
    if not url:
        return False, "empty url"

    if "\\" in url:
        return False, "path must use forward slashes"

    if not url.startswith("https://"):
        return False, "must start with https://"

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname in LOCAL_HOSTNAMES:
        return False, "localhost is not allowed"

    if not any(prefix in url for prefix in PUBLIC_IMAGE_PATH_PREFIXES):
        return False, "must include a public static image path"

    return True, ""


def verify_public_url_with_status(url: str, timeout: float = 12.0) -> tuple[bool, int | None, str]:
    url = normalize_web_path(url, keep_query=True)
    if not url:
        return False, None, "empty url"

    headers = {
        "User-Agent": "bposik-video-generator/1.0",
        "ngrok-skip-browser-warning": "69420",
    }
    last_status: int | None = None
    last_error = ""
    for method in ("HEAD", "GET"):
        try:
            request = Request(url, method=method, headers=headers)
            with urlopen(request, timeout=timeout) as response:
                last_status = int(response.status)
                if last_status in {200, 206}:
                    return True, last_status, ""
                last_error = f"{method} returned HTTP {last_status}"
        except Exception as exc:
            last_error = f"{method} failed: {exc.__class__.__name__}: {exc}"
            continue
    return False, last_status, last_error or "unreachable"


def verify_public_url_accessible(url: str, timeout: float = 12.0) -> bool:
    reachable, _status, _detail = verify_public_url_with_status(url, timeout=timeout)
    return reachable


def resolve_replicate_start_image_urls(image_path: str) -> tuple[str, str, str]:
    """Return local_image_url, public_start_image_url, replicate_start_image."""
    local_image_url = normalize_web_path(image_path, keep_query=True)
    public_start_image_url = build_public_image_url(local_image_url)
    is_valid, reason = validate_replicate_start_image_url(public_start_image_url)
    if not is_valid:
        raise RuntimeError(f"{REPLICATE_NGROK_REQUIRED_MESSAGE} ({reason})")
    return local_image_url, public_start_image_url, public_start_image_url
