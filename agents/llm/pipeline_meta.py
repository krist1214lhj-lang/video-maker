"""파이프라인 응답용 LLM 모드 메타 (Director / run-demo)."""

from __future__ import annotations

from typing import Any

from agents.llm.config import resolve_llm_mode


def build_pipeline_llm_meta(
    *,
    topic_result: Any | None = None,
    story_bundles: list[Any] | None = None,
) -> dict[str, str | None]:
    """
    AGENT_LLM_MODE(요청)와 에이전트별 effective 모드를 meta에 넣는다.

    - llm_mode: 환경 변수 AGENT_LLM_MODE (요청값)
    - topic_llm_mode: 01_topic 실제 사용 모드
    - story_llm_mode: 02_story 실제 사용 모드
    """
    agent_llm_mode = resolve_llm_mode().value

    topic_llm_mode: str | None = None
    if topic_result is not None:
        topic_meta = getattr(topic_result, "meta", None) or {}
        if isinstance(topic_meta, dict):
            topic_llm_mode = topic_meta.get("llm_mode")

    story_llm_mode: str | None = None
    for bundle in story_bundles or []:
        story_result = getattr(bundle, "story_result", None)
        if story_result is None:
            continue
        story_meta = getattr(story_result, "meta", None) or {}
        if isinstance(story_meta, dict) and story_meta.get("llm_mode"):
            story_llm_mode = story_meta.get("llm_mode")
            break

    return {
        "llm_mode": agent_llm_mode,
        "topic_llm_mode": topic_llm_mode,
        "story_llm_mode": story_llm_mode,
    }
