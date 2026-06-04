from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = PROJECT_ROOT / "projects"
GENERATED_IMAGE_DIR = PROJECT_ROOT / "generated_images"
REFERENCE_CHARACTERS_DIR = PROJECT_ROOT / "reference_characters"
STATIC_DIR = PROJECT_ROOT / "static"


def _collapse_slashes(path: str) -> str:
    if not path:
        return path
    collapsed = path.replace("\\", "/")
    while len(collapsed) > 1 and "//" in collapsed[1:]:
        collapsed = collapsed[0] + collapsed[1:].replace("//", "/")
    return collapsed


def normalize_web_path(value: str, *, keep_query: bool = True) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    query = ""
    base = raw
    if "?" in raw:
        base, query_part = raw.split("?", 1)
        query = f"?{query_part}" if keep_query and query_part else ""

    if "://" in base:
        scheme, rest = base.split("://", 1)
        rest = _collapse_slashes(rest)
        base = f"{scheme}://{rest}"

    parsed = urlparse(base)
    if parsed.scheme in {"http", "https"}:
        web_path = _collapse_slashes(parsed.path or "")
        if not web_path.startswith("/"):
            web_path = f"/{web_path}"
        return f"{parsed.scheme}://{parsed.netloc}{web_path}{query}"

    web_path = _collapse_slashes(base)
    if not web_path.startswith("/"):
        web_path = f"/{web_path}"
    return f"{web_path}{query}"


def resolve_local_image_path(image_url: str) -> Path | None:
    if not image_url:
        return None

    normalized = normalize_web_path(image_url, keep_query=False)
    parsed = urlparse(normalized)
    if parsed.scheme in {"http", "https"} and parsed.path:
        normalized = parsed.path

    if normalized.startswith("/generated_images/"):
        return GENERATED_IMAGE_DIR / Path(normalized).name
    if normalized.startswith("/projects/"):
        relative = normalized.removeprefix("/projects/").lstrip("/").replace("\\", "/")
        candidate = PROJECTS_DIR / relative
        try:
            candidate.resolve().relative_to(PROJECTS_DIR.resolve())
        except ValueError:
            return None
        return candidate
    if normalized.startswith("/reference_characters/"):
        relative = normalized.removeprefix("/reference_characters/").lstrip("/")
        return REFERENCE_CHARACTERS_DIR / relative
    if normalized.startswith("/static/"):
        relative = normalized.removeprefix("/static/").lstrip("/")
        return STATIC_DIR / relative

    candidate = Path(normalized)
    if candidate.is_file():
        return candidate

    project_candidate = PROJECT_ROOT / normalized.lstrip("/")
    if project_candidate.is_file():
        return project_candidate

    return None
