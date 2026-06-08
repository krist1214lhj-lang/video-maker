"""파이프라인 응답용 LLM 모드·토큰·비용 메타 (Director / run-demo)."""

from __future__ import annotations

from typing import Any

from agents.llm.config import (
    resolve_llm_mode,
    resolve_music_model,
    resolve_narration_subtitle_model,
    resolve_review_model,
    resolve_story_model,
    resolve_topic_model,
)

# OpenAI 공개 단가 근사 (USD per token)
_MODEL_RATES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
    "gpt-4o": (2.50 / 1_000_000, 10.0 / 1_000_000),
}


def estimate_llm_cost_usd(usage: dict[str, Any] | None, *, model: str | None) -> float | None:
    if not usage:
        return None
    inp = int(usage.get("prompt_tokens") or 0)
    out = int(usage.get("completion_tokens") or 0)
    key = (model or "gpt-4o-mini").strip()
    rin, rout = _MODEL_RATES.get(key, _MODEL_RATES["gpt-4o-mini"])
    return inp * rin + out * rout


def build_pipeline_llm_meta(
    *,
    topic_result: Any | None = None,
    story_bundles: list[Any] | None = None,
    narration_result: Any | None = None,
    music_result: Any | None = None,
    review_result: Any | None = None,
) -> dict[str, Any]:
    """
    AGENT_LLM_MODE(요청)와 에이전트별 effective 모드·토큰·추정 비용.

    - llm_mode: 환경 변수 AGENT_LLM_MODE (요청값)
    - topic_llm_mode / story_llm_mode / narration_subtitle_llm_mode / music_llm_mode / review_llm_mode: 실제 사용 모드
    - topic_llm_usage / story_llm_usage / narration_subtitle_llm_usage / music_llm_usage / review_llm_usage: OpenAI usage dict
    - *_llm_est_cost_usd / llm_est_cost_usd_total: gpt-4o-mini 단가 근사
    """
    agent_llm_mode = resolve_llm_mode().value

    topic_meta: dict[str, Any] = {}
    if topic_result is not None:
        raw = getattr(topic_result, "meta", None) or {}
        if isinstance(raw, dict):
            topic_meta = raw

    topic_llm_mode = topic_meta.get("llm_mode")
    topic_llm_usage = topic_meta.get("llm_usage")
    topic_model = topic_meta.get("llm_model") or resolve_topic_model()
    topic_cost = estimate_llm_cost_usd(
        topic_llm_usage if isinstance(topic_llm_usage, dict) else None,
        model=str(topic_model) if topic_model else None,
    )

    story_llm_mode: str | None = None
    story_meta: dict[str, Any] = {}
    for bundle in story_bundles or []:
        story_result = getattr(bundle, "story_result", None)
        if story_result is None:
            continue
        raw = getattr(story_result, "meta", None) or {}
        if isinstance(raw, dict) and raw.get("llm_mode"):
            story_llm_mode = raw.get("llm_mode")
            story_meta = raw
            break

    story_llm_usage = story_meta.get("llm_usage")
    story_model = story_meta.get("llm_model") or resolve_story_model()
    story_cost = estimate_llm_cost_usd(
        story_llm_usage if isinstance(story_llm_usage, dict) else None,
        model=str(story_model) if story_model else None,
    )

    narration_meta: dict[str, Any] = {}
    if narration_result is not None:
        raw = getattr(narration_result, "meta", None) or {}
        if isinstance(raw, dict):
            narration_meta = raw
    narration_llm_mode = narration_meta.get("narration_llm_mode") or narration_meta.get("llm_mode")
    narration_llm_usage = narration_meta.get("llm_usage")
    narration_model = narration_meta.get("llm_model") or resolve_narration_subtitle_model()
    narration_cost = estimate_llm_cost_usd(
        narration_llm_usage if isinstance(narration_llm_usage, dict) else None,
        model=str(narration_model) if narration_model else None,
    )

    music_meta: dict[str, Any] = {}
    if music_result is not None:
        raw = getattr(music_result, "meta", None) or {}
        if isinstance(raw, dict):
            music_meta = raw
    music_llm_mode = music_meta.get("music_llm_mode") or music_meta.get("llm_mode")
    music_llm_usage = music_meta.get("llm_usage")
    music_model = music_meta.get("llm_model") or resolve_music_model()
    music_cost = estimate_llm_cost_usd(
        music_llm_usage if isinstance(music_llm_usage, dict) else None,
        model=str(music_model) if music_model else None,
    )

    review_meta: dict[str, Any] = {}
    if review_result is not None:
        raw = getattr(review_result, "meta", None) or {}
        if isinstance(raw, dict):
            review_meta = raw
    review_llm_mode = review_meta.get("review_llm_mode") or review_meta.get("llm_mode")
    review_llm_usage = review_meta.get("llm_usage")
    review_model = review_meta.get("llm_model") or resolve_review_model()
    review_cost = estimate_llm_cost_usd(
        review_llm_usage if isinstance(review_llm_usage, dict) else None,
        model=str(review_model) if review_model else None,
    )

    total_cost: float | None = None
    if (
        topic_cost is not None
        or story_cost is not None
        or narration_cost is not None
        or music_cost is not None
        or review_cost is not None
    ):
        total_cost = (
            (topic_cost or 0.0)
            + (story_cost or 0.0)
            + (narration_cost or 0.0)
            + (music_cost or 0.0)
            + (review_cost or 0.0)
        )

    return {
        "llm_mode": agent_llm_mode,
        "topic_llm_mode": topic_llm_mode,
        "story_llm_mode": story_llm_mode,
        "narration_subtitle_llm_mode": narration_llm_mode,
        "music_llm_mode": music_llm_mode,
        "review_llm_mode": review_llm_mode,
        "topic_llm_usage": topic_llm_usage,
        "story_llm_usage": story_llm_usage,
        "narration_subtitle_llm_usage": narration_llm_usage,
        "music_llm_usage": music_llm_usage,
        "review_llm_usage": review_llm_usage,
        "topic_llm_model": topic_model,
        "story_llm_model": story_model,
        "narration_subtitle_llm_model": narration_model,
        "music_llm_model": music_model,
        "review_llm_model": review_model,
        "topic_llm_est_cost_usd": round(topic_cost, 6) if topic_cost is not None else None,
        "story_llm_est_cost_usd": round(story_cost, 6) if story_cost is not None else None,
        "narration_subtitle_llm_est_cost_usd": round(narration_cost, 6) if narration_cost is not None else None,
        "music_llm_est_cost_usd": round(music_cost, 6) if music_cost is not None else None,
        "review_llm_est_cost_usd": round(review_cost, 6) if review_cost is not None else None,
        "llm_est_cost_usd_total": round(total_cost, 6) if total_cost is not None else None,
    }
