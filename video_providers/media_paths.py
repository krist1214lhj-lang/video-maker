from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = PROJECT_ROOT / "projects"
GENERATED_IMAGE_DIR = PROJECT_ROOT / "generated_images"
REFERENCE_CHARACTERS_DIR = PROJECT_ROOT / "reference_characters"
STATIC_DIR = PROJECT_ROOT / "static"


def resolve_local_image_path(image_url: str) -> Path | None:
    if not image_url:
        return None

    normalized = image_url.split("?", 1)[0].strip()
    parsed = urlparse(normalized)
    if parsed.scheme in {"http", "https"} and parsed.path:
        normalized = parsed.path

    if normalized.startswith("/generated_images/"):
        return GENERATED_IMAGE_DIR / Path(normalized).name
    if normalized.startswith("/projects/"):
        candidate = PROJECTS_DIR / normalized.removeprefix("/projects/").lstrip("/")
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
