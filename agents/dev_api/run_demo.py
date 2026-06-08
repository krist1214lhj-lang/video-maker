"""
POST /agent/run-demo — Mock Director 파이프라인 (01→08).

- OpenAI / Replicate 호출 없음
- agents/09_director_agent.run_full_pipeline() 만 사용
- main.py 수정 없음 → register_run_demo_routes(app) 로 마운트
"""

from __future__ import annotations

import importlib
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from agents.subtopic_selection import is_auto_select_subtopic_id

_director_mod = importlib.import_module("agents.09_director_agent")
_story_mod = importlib.import_module("agents.02_story_agent")
_char_mod = importlib.import_module("agents.03_character_agent")

PlanningDirectorInput = _director_mod.PlanningDirectorInput
run_full_pipeline = _director_mod.run_full_pipeline
StoryTone = _story_mod.StoryTone
ReferenceCharacter = _char_mod.ReferenceCharacter


class AgentRunDemoRequest(BaseModel):
    main_topic: str = Field(..., min_length=1, description="대주제")
    style: str = ""
    duration_seconds: int = Field(15, ge=5, le=600)
    cut_count: int = Field(5, ge=1, le=30)
    project_slug: str = ""
    locale: str = "ko"
    selected_subtopic_id: str | None = Field(
        default=None,
        description="비우거나 string/subtopic1 이면 첫 소주제(subtopic_1) 자동 선택",
    )
    selected_story_tone: str = Field("comic", description="comic | emotional | twist")
    reference_character_name: str = "bposik_v2"
    target_platform: str = "youtube_shorts"


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return str(value)


def _parse_story_tone(raw: str) -> StoryTone:
    key = (raw or "comic").strip().lower()
    for tone in StoryTone:
        if tone.value == key or tone.name.lower() == key:
            return tone
    return StoryTone.COMIC


def _topic_payload(result: Any) -> dict[str, Any]:
    tr = result.topic_result
    if tr is None:
        return {}
    return {
        "success": tr.success,
        "main_topic": tr.main_topic,
        "style": tr.style,
        "duration_seconds": tr.duration_seconds,
        "cut_count": tr.cut_count,
        "subtopics": [
            {
                "id": s.id,
                "title": s.title,
                "angle": s.angle.value,
                "hook": s.hook,
                "one_line_pitch": s.one_line_pitch,
            }
            for s in tr.subtopics
        ],
        "selected_subtopic_id": (
            tr.selected_subtopic_id
            or (result.selected_subtopic.id if result.selected_subtopic else None)
        ),
    }


def _story_payload(result: Any) -> dict[str, Any]:
    ctx = result.selected_story
    if ctx is None:
        return {}
    return {
        "main_topic": ctx.main_topic,
        "subtopic_id": ctx.subtopic_id,
        "subtopic_title": ctx.subtopic_title,
        "tone": ctx.tone.value,
        "title": ctx.title,
        "logline": getattr(ctx, "logline", None) or ctx.story_arc,
        "narration_outline": getattr(ctx, "narration_outline", None) or ctx.summary,
        "summary": ctx.summary,
        "story_arc": ctx.story_arc,
        "cut_count": ctx.cut_count,
        "variant_count_per_subtopic": 3,
    }


def _character_payload(result: Any) -> dict[str, Any]:
    cr = result.character_result
    if cr is None or not cr.success:
        return {"success": False}
    profile = cr.character_profile
    constraints = cr.visual_constraints
    return {
        "success": cr.success,
        "character_prompt": cr.character_prompt,
        "character_profile": _to_jsonable(profile) if profile else None,
        "visual_constraints": _to_jsonable(constraints) if constraints else None,
        "meta": _to_jsonable(cr.meta),
    }


def _format_payload(result: Any) -> dict[str, Any]:
    fr = result.format_result
    if fr is None or not fr.success:
        return {"success": False}
    plan = fr.format_plan
    return {
        "success": fr.success,
        "recommended_cut_count": fr.recommended_cut_count,
        "aspect_ratio": fr.aspect_ratio,
        "format_plan": _to_jsonable(plan) if plan else None,
    }


def _narration_payload(result: Any) -> dict[str, Any]:
    nr = result.narration_result
    if nr is None or not nr.success:
        return {"success": False}
    return {
        "success": nr.success,
        "narration_script": _to_jsonable(nr.narration_script),
        "subtitle_script": _to_jsonable(nr.subtitle_script),
        "voice_style": _to_jsonable(nr.voice_style),
        "meta": _to_jsonable(nr.meta),
    }


def _music_payload(result: Any) -> dict[str, Any]:
    mr = result.music_result
    if mr is None or not mr.success:
        return {"success": False}
    return {
        "success": mr.success,
        "music_style": mr.music_style,
        "bpm": mr.bpm,
        "music_prompt": mr.music_prompt,
        "meta": _to_jsonable(mr.meta),
    }


def _review_payload(result: Any) -> dict[str, Any]:
    rr = result.review_result
    if rr is None or not rr.success:
        return {"success": False}
    report = rr.review_report
    return {
        "success": rr.success,
        "review_passed": report.passed if report else None,
        "quality_score": report.score if report else None,
        "strengths": _to_jsonable(report.strengths) if report else [],
        "weaknesses": _to_jsonable(report.weaknesses) if report else [],
        "revision_required": report.revision_required if report else None,
        "retry_target_agent": rr.retry_target_agent.value,
        "retry_action": report.retry_action if report else "",
        "revision_reason": report.revision_reason if report else "",
        "review_summary": report.summary_ko if report else "",
        "checks": _to_jsonable(report.checks) if report else [],
        "meta": _to_jsonable(rr.meta),
    }


def _demo_meta(
    pipeline_result: Any,
    *,
    requested_subtopic_id: str | None = None,
) -> dict[str, Any]:
    from agents.llm.pipeline_meta import build_pipeline_llm_meta

    base: dict[str, Any] = {
        "mode": "mock_full_pipeline",
        "failed_at": pipeline_result.meta.get("failed_at"),
        "review_passed": (
            pipeline_result.review_result.review_report.passed
            if pipeline_result.review_result and pipeline_result.review_result.review_report
            else None
        ),
        "retry_target_agent": (
            pipeline_result.review_result.retry_target_agent.value
            if pipeline_result.review_result
            else None
        ),
    }
    base.update(
        build_pipeline_llm_meta(
            topic_result=pipeline_result.topic_result,
            story_bundles=pipeline_result.story_bundles,
            narration_result=pipeline_result.narration_result,
            music_result=pipeline_result.music_result,
            review_result=pipeline_result.review_result,
        )
    )
    character_meta = (
        pipeline_result.character_result.meta
        if pipeline_result.character_result
        else {}
    )
    if isinstance(character_meta, dict):
        base["character_llm_mode"] = character_meta.get("character_llm_mode")
        base["character_llm_mode_requested"] = character_meta.get(
            "character_llm_mode_requested"
        )
        base["character_llm_fallback_reason"] = character_meta.get(
            "character_llm_fallback_reason"
        )
    narration_meta = (
        pipeline_result.narration_result.meta
        if pipeline_result.narration_result
        else {}
    )
    if isinstance(narration_meta, dict):
        base["narration_llm_mode"] = narration_meta.get("narration_llm_mode")
        base["narration_llm_mode_requested"] = narration_meta.get(
            "narration_llm_mode_requested"
        )
        base["narration_llm_fallback_reason"] = narration_meta.get(
            "narration_llm_fallback_reason"
        )
        base["subtitle_llm_mode"] = narration_meta.get("subtitle_llm_mode")
        base["subtitle_llm_mode_requested"] = narration_meta.get(
            "subtitle_llm_mode_requested"
        )
        base["subtitle_llm_fallback_reason"] = narration_meta.get(
            "subtitle_llm_fallback_reason"
        )
    music_meta = (
        pipeline_result.music_result.meta
        if pipeline_result.music_result
        else {}
    )
    if isinstance(music_meta, dict):
        base["music_llm_mode"] = music_meta.get("music_llm_mode")
        base["music_llm_mode_requested"] = music_meta.get(
            "music_llm_mode_requested"
        )
        base["music_llm_fallback_reason"] = music_meta.get(
            "music_llm_fallback_reason"
        )
    review_meta = (
        pipeline_result.review_result.meta
        if pipeline_result.review_result
        else {}
    )
    if isinstance(review_meta, dict):
        base["review_llm_mode"] = review_meta.get("review_llm_mode")
        base["review_llm_mode_requested"] = review_meta.get(
            "review_llm_mode_requested"
        )
        base["review_llm_fallback_reason"] = review_meta.get(
            "review_llm_fallback_reason"
        )
        base["retry_action"] = review_meta.get("retry_action")
        base["revision_reason"] = review_meta.get("revision_reason")
    director_meta = pipeline_result.meta or {}
    effective = director_meta.get("selected_subtopic_id")
    if effective is None and pipeline_result.topic_result:
        tr = pipeline_result.topic_result
        effective = tr.selected_subtopic_id or (
            pipeline_result.selected_subtopic.id
            if pipeline_result.selected_subtopic
            else None
        )
    auto = director_meta.get("auto_selected_subtopic_id")
    if auto is None:
        auto = is_auto_select_subtopic_id(requested_subtopic_id)
    if effective:
        base["selected_subtopic_id"] = effective
    base["auto_selected_subtopic_id"] = bool(auto)
    return base


def run_demo_pipeline(body: AgentRunDemoRequest) -> dict[str, Any]:
    """Director 09 → 01…08 (Mock only)."""
    requested_subtopic_id = body.selected_subtopic_id
    pipeline_result = run_full_pipeline(
        PlanningDirectorInput(
            main_topic=body.main_topic.strip(),
            style=body.style,
            duration_seconds=body.duration_seconds,
            cut_count=body.cut_count,
            project_slug=body.project_slug,
            locale=body.locale,
            selected_subtopic_id=requested_subtopic_id,
            selected_story_tone=_parse_story_tone(body.selected_story_tone),
            reference_character=ReferenceCharacter(name=body.reference_character_name.strip()),
            target_platform=body.target_platform,
        )
    )
    return {
        "success": pipeline_result.success,
        "steps": list(pipeline_result.steps),
        "topic": _topic_payload(pipeline_result),
        "story": _story_payload(pipeline_result),
        "character": _character_payload(pipeline_result),
        "format": _format_payload(pipeline_result),
        "narration_subtitle": _narration_payload(pipeline_result),
        "music": _music_payload(pipeline_result),
        "review": _review_payload(pipeline_result),
        "meta": _demo_meta(
            pipeline_result,
            requested_subtopic_id=requested_subtopic_id,
        ),
    }


def register_run_demo_routes(app: FastAPI) -> None:
    """main.py 를 수정하지 않고 FastAPI app 에 개발용 라우트를 붙인다."""

    @app.post("/agent/run-demo")
    def agent_run_demo(request: AgentRunDemoRequest) -> dict[str, Any]:
        return run_demo_pipeline(request)
