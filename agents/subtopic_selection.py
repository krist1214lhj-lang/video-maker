"""소주제 id 자동 선택 (Swagger placeholder · 오타)."""

from __future__ import annotations

from typing import Any, Protocol

DEFAULT_SUBTOPIC_ID = "subtopic_1"

# null / "" / Swagger 기본값 "string" / 흔한 오타 "subtopic1"
_AUTO_SELECT_SUBTOPIC_IDS = frozenset({"string", "subtopic1"})


class _HasId(Protocol):
    id: str


def is_auto_select_subtopic_id(requested: str | None) -> bool:
    trimmed = (requested or "").strip()
    if not trimmed:
        return True
    return trimmed.lower() in _AUTO_SELECT_SUBTOPIC_IDS


def resolve_selected_subtopic_id(
    requested: str | None,
    subtopics: list[_HasId],
    *,
    default_id: str = DEFAULT_SUBTOPIC_ID,
) -> tuple[str, bool]:
    """
    topic_result.subtopics 확보 후 호출.

    Returns:
        (effective_id, auto_selected)
    """
    if is_auto_select_subtopic_id(requested):
        if subtopics:
            return subtopics[0].id, True
        return default_id, True

    trimmed = (requested or "").strip()
    if subtopics:
        for item in subtopics:
            if item.id == trimmed:
                return trimmed, False
        return subtopics[0].id, False

    return trimmed or default_id, False


def subtopic_selection_meta(
    *,
    requested: str | None,
    effective_id: str,
    auto_selected: bool,
) -> dict[str, Any]:
    return {
        "auto_selected_subtopic_id": auto_selected,
        "selected_subtopic_id": effective_id,
        "selected_subtopic_id_requested": requested,
    }
