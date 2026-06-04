"""
Agent 09 — director_agent (Phase 3A-3: design only)

역할: 전체 콘텐츠 파이프라인 오케스트레이션 (HTTP·main.py 미연결)

호출 순서 (전체):
  09 Director → 01 Topic → 02 Story → 03 Character → 04 Format
            → 05 NarrationSubtitle → 06 Music → 07 Production → 08 Review

주의 — 이름 충돌:
  - `agents/post_production/director.py` = 후반 실구현 shim (main 연결)
  - 이 모듈 = **기획·제작 Director** (01→08 Mock)

상세: docs/agents/DIRECTOR_PIPELINE.md
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any

_topic_mod = importlib.import_module("agents.01_topic_agent")
_story_mod = importlib.import_module("agents.02_story_agent")
_character_mod = importlib.import_module("agents.03_character_agent")
_format_mod = importlib.import_module("agents.04_format_agent")
_narration_mod = importlib.import_module("agents.05_narration_subtitle_agent")
_music_mod = importlib.import_module("agents.06_music_agent")
_production_mod = importlib.import_module("agents.07_production_agent")
_review_mod = importlib.import_module("agents.08_review_agent")

TopicAgentInput = _topic_mod.TopicAgentInput
TopicAgentResult = _topic_mod.TopicAgentResult
StoryAgentInput = _story_mod.StoryAgentInput
StoryAgentResult = _story_mod.StoryAgentResult
StoryTone = _story_mod.StoryTone
StoryContext = _character_mod.StoryContext
ReferenceCharacter = _character_mod.ReferenceCharacter
CharacterAgentInput = _character_mod.CharacterAgentInput
CharacterAgentResult = _character_mod.CharacterAgentResult
FormatAgentInput = _format_mod.FormatAgentInput
FormatAgentResult = _format_mod.FormatAgentResult
NarrationSubtitleAgentInput = _narration_mod.NarrationSubtitleAgentInput
NarrationSubtitleAgentResult = _narration_mod.NarrationSubtitleAgentResult
MusicAgentInput = _music_mod.MusicAgentInput
MusicAgentResult = _music_mod.MusicAgentResult
ProductionAgentInput = _production_mod.ProductionAgentInput
ProductionAgentResult = _production_mod.ProductionAgentResult
StoryboardPlan = _production_mod.StoryboardPlan
StoryboardCut = _production_mod.StoryboardCut
ReviewAgentInput = _review_mod.ReviewAgentInput
ReviewAgentResult = _review_mod.ReviewAgentResult
ProductionResultSnapshot = _review_mod.ProductionResultSnapshot
SubTopic = _topic_mod.SubTopic

DEFAULT_SUBTOPIC_ID = "subtopic_1"


def _resolve_selected_subtopic_id(requested: str | None) -> str:
    """요청값이 없거나 공백이면 subtopic_1."""
    trimmed = (requested or "").strip()
    return trimmed or DEFAULT_SUBTOPIC_ID


def _find_subtopic(subtopics: list[SubTopic], subtopic_id: str) -> SubTopic | None:
    return next((s for s in subtopics if s.id == subtopic_id), None)


class DirectorStep(str, Enum):
    DIRECTOR = "09_director"
    TOPIC = "01_topic"
    STORY = "02_story"
    CHARACTER = "03_character"
    FORMAT = "04_format"
    NARRATION_SUBTITLE = "05_narration_subtitle"
    MUSIC = "06_music"
    PRODUCTION = "07_production"
    REVIEW = "08_review"


@dataclass
class PlanningDirectorInput:
    main_topic: str
    style: str = ""
    duration_seconds: int = 15
    cut_count: int = 5
    project_slug: str = ""
    locale: str = "ko"
    selected_subtopic_id: str | None = None
    selected_story_tone: StoryTone = StoryTone.COMIC
    reference_character: ReferenceCharacter | None = None
    target_platform: str = "youtube_shorts"
    preferred_format: _format_mod.OutputFormat | None = None


@dataclass
class SubTopicStoryBundle:
    subtopic: SubTopic
    story_result: StoryAgentResult


@dataclass
class PlanningDirectorResult:
    success: bool
    main_topic: str
    topic_result: TopicAgentResult | None
    selected_subtopic: SubTopic | None
    selected_story: StoryContext | None
    story_bundles: list[SubTopicStoryBundle]
    character_result: CharacterAgentResult | None
    format_result: FormatAgentResult | None
    narration_result: NarrationSubtitleAgentResult | None = None
    music_result: MusicAgentResult | None = None
    production_result: ProductionAgentResult | None = None
    review_result: ReviewAgentResult | None = None
    steps: list[str] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meta: dict[str, Any] = field(default_factory=dict)


def _select_story_variant(
    story_result: StoryAgentResult,
    tone: StoryTone,
) -> Any | None:
    for variant in story_result.variants:
        if variant.tone == tone:
            return variant
    return story_result.variants[0] if story_result.variants else None


@dataclass
class _TopicStoryPhase:
    steps: list[str]
    topic_result: TopicAgentResult
    active_subtopic: SubTopic
    story_ctx: StoryContext
    bundles: list[SubTopicStoryBundle]


def _fail(
    *,
    main_topic: str,
    steps: list[str],
    failed_at: DirectorStep,
    topic_result: TopicAgentResult | None = None,
    selected_subtopic: SubTopic | None = None,
    selected_story: StoryContext | None = None,
    bundles: list[SubTopicStoryBundle] | None = None,
    character_result: CharacterAgentResult | None = None,
    format_result: FormatAgentResult | None = None,
    narration_result: NarrationSubtitleAgentResult | None = None,
    music_result: MusicAgentResult | None = None,
    production_result: ProductionAgentResult | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> PlanningDirectorResult:
    meta = {"failed_at": failed_at.value}
    if extra_meta:
        meta.update(extra_meta)
    return PlanningDirectorResult(
        success=False,
        main_topic=main_topic,
        topic_result=topic_result,
        selected_subtopic=selected_subtopic,
        selected_story=selected_story,
        story_bundles=bundles or [],
        character_result=character_result,
        format_result=format_result,
        narration_result=narration_result,
        music_result=music_result,
        production_result=production_result,
        review_result=None,
        steps=steps,
        meta=meta,
    )


def _run_topic_story_phase(
    input_data: PlanningDirectorInput,
) -> tuple[_TopicStoryPhase | None, PlanningDirectorResult | None]:
    steps: list[str] = [DirectorStep.DIRECTOR.value]

    topic_result = _topic_mod.run_topic_agent(
        TopicAgentInput(
            main_topic=input_data.main_topic,
            style=input_data.style,
            duration_seconds=input_data.duration_seconds,
            cut_count=input_data.cut_count,
            project_slug=input_data.project_slug,
            locale=input_data.locale,
        )
    )
    steps.append(DirectorStep.TOPIC.value)
    if not topic_result.success:
        return None, _fail(main_topic=input_data.main_topic, steps=steps, failed_at=DirectorStep.TOPIC, topic_result=topic_result)

    effective_subtopic_id = _resolve_selected_subtopic_id(input_data.selected_subtopic_id)
    input_data.selected_subtopic_id = effective_subtopic_id

    active_subtopic = _find_subtopic(topic_result.subtopics, effective_subtopic_id)
    topic_result = replace(topic_result, selected_subtopic_id=effective_subtopic_id)

    if active_subtopic is None:
        return None, _fail(
            main_topic=topic_result.main_topic,
            steps=steps,
            failed_at=DirectorStep.TOPIC,
            topic_result=topic_result,
            extra_meta={
                "error": "unknown subtopic",
                "selected_subtopic_id": effective_subtopic_id,
            },
        )

    if not topic_result.selected_subtopic_id:
        return None, _fail(
            main_topic=topic_result.main_topic,
            steps=steps,
            failed_at=DirectorStep.TOPIC,
            topic_result=topic_result,
            selected_subtopic=active_subtopic,
            extra_meta={"error": "missing selected_subtopic_id on topic"},
        )

    subtopics = [active_subtopic]
    bundles: list[SubTopicStoryBundle] = []
    for subtopic in subtopics:
        story_result = _story_mod.run_story_agent(
            StoryAgentInput(
                main_topic=topic_result.main_topic,
                subtopic=subtopic,
                style=topic_result.style,
                duration_seconds=topic_result.duration_seconds,
                cut_count=topic_result.cut_count,
                project_slug=input_data.project_slug,
            )
        )
        if not story_result.success:
            return None, _fail(
                main_topic=topic_result.main_topic,
                steps=steps + [DirectorStep.STORY.value],
                failed_at=DirectorStep.STORY,
                topic_result=topic_result,
                selected_subtopic=active_subtopic,
                bundles=bundles,
                extra_meta={"subtopic_id": subtopic.id},
            )
        bundles.append(SubTopicStoryBundle(subtopic=subtopic, story_result=story_result))
        steps.append(f"{DirectorStep.STORY.value}:{subtopic.id}")

    active_subtopic = subtopics[0]
    variant = _select_story_variant(bundles[0].story_result, input_data.selected_story_tone)
    if variant is None:
        return None, _fail(
            main_topic=topic_result.main_topic,
            steps=steps,
            failed_at=DirectorStep.STORY,
            topic_result=topic_result,
            selected_subtopic=active_subtopic,
            bundles=bundles,
            extra_meta={"error": "no story variant"},
        )

    story_ctx = StoryContext.from_variant(
        main_topic=topic_result.main_topic,
        subtopic_id=active_subtopic.id,
        subtopic_title=active_subtopic.title,
        variant=variant,
        cut_count=topic_result.cut_count,
    )
    return (
        _TopicStoryPhase(
            steps=steps,
            topic_result=topic_result,
            active_subtopic=active_subtopic,
            story_ctx=story_ctx,
            bundles=bundles,
        ),
        None,
    )


def _build_storyboard(
    *,
    story_ctx: StoryContext,
    character_result: CharacterAgentResult,
    format_result: FormatAgentResult,
) -> StoryboardPlan:
    plan = format_result.format_plan
    cut_count = plan.recommended_cut_count if plan else story_ctx.cut_count
    aspect = format_result.aspect_ratio or (plan.aspect_ratio if plan else "9:16")
    total = float(plan.target_duration_seconds if plan else 15)
    per_cut = total / max(1, cut_count)
    visual_base = (character_result.character_prompt or "")[:160]
    cuts = [
        StoryboardCut(
            cut=i,
            scene=f"{story_ctx.subtopic_title} — beat {i}",
            visual_prompt=f"{visual_base} | cut {i}/{cut_count}",
            duration_sec=per_cut,
        )
        for i in range(1, cut_count + 1)
    ]
    return StoryboardPlan(cuts=cuts, aspect_ratio=aspect, total_duration_sec=total)


def run_planning_pipeline(input_data: PlanningDirectorInput) -> PlanningDirectorResult:
    """Phase 3A-2: 01 Topic → 04 Format (05~08 생략)."""
    phase, early = _run_topic_story_phase(input_data)
    if early is not None:
        return early
    assert phase is not None

    steps = list(phase.steps)
    topic_result = phase.topic_result
    story_ctx = phase.story_ctx
    bundles = phase.bundles
    active_subtopic = phase.active_subtopic

    ref = input_data.reference_character or ReferenceCharacter(name="bposik_v2", identity_lock_strength="high")
    character_result = _character_mod.run_character_agent(
        CharacterAgentInput(
            topic=topic_result.main_topic,
            story=story_ctx,
            reference_character=ref,
            project_slug=input_data.project_slug,
        )
    )
    steps.append(DirectorStep.CHARACTER.value)
    if not character_result.success:
        return _fail(
            main_topic=topic_result.main_topic,
            steps=steps,
            failed_at=DirectorStep.CHARACTER,
            topic_result=topic_result,
            selected_subtopic=active_subtopic,
            selected_story=story_ctx,
            bundles=bundles,
            character_result=character_result,
        )

    format_result = _format_mod.run_format_agent(
        FormatAgentInput(
            story=story_ctx,
            target_platform=input_data.target_platform,
            duration_seconds=topic_result.duration_seconds,
            style=topic_result.style,
            preferred_format=input_data.preferred_format,
        )
    )
    steps.append(DirectorStep.FORMAT.value)
    if not format_result.success:
        return _fail(
            main_topic=topic_result.main_topic,
            steps=steps,
            failed_at=DirectorStep.FORMAT,
            topic_result=topic_result,
            selected_subtopic=active_subtopic,
            selected_story=story_ctx,
            bundles=bundles,
            character_result=character_result,
            format_result=format_result,
        )

    return PlanningDirectorResult(
        success=True,
        main_topic=topic_result.main_topic,
        topic_result=topic_result,
        selected_subtopic=active_subtopic,
        selected_story=story_ctx,
        story_bundles=bundles,
        character_result=character_result,
        format_result=format_result,
        steps=steps,
        meta={
            "mode": "planning_only",
            "selected_story_tone": input_data.selected_story_tone.value,
            "format": format_result.format_plan.format.value if format_result.format_plan else None,
            "recommended_cut_count": format_result.recommended_cut_count,
            "aspect_ratio": format_result.aspect_ratio,
        },
    )


def run_full_pipeline(input_data: PlanningDirectorInput) -> PlanningDirectorResult:
    """Phase 3A-3: 01 → 08 전체 파이프라인."""
    pre = run_planning_pipeline(input_data)
    if not pre.success:
        return pre

    steps = list(pre.steps)
    topic_result = pre.topic_result
    story_ctx = pre.selected_story
    format_result = pre.format_result
    character_result = pre.character_result
    assert topic_result and story_ctx and format_result and character_result
    assert format_result.format_plan is not None

    narration_result = _narration_mod.run_narration_subtitle_agent(
        NarrationSubtitleAgentInput(
            story=story_ctx,
            tone=input_data.selected_story_tone,
            format=format_result.format_plan,
            locale=input_data.locale,
            project_slug=input_data.project_slug,
        )
    )
    steps.append(DirectorStep.NARRATION_SUBTITLE.value)
    if not narration_result.success or not narration_result.narration_script or not narration_result.subtitle_script:
        return _fail(
            main_topic=pre.main_topic,
            steps=steps,
            failed_at=DirectorStep.NARRATION_SUBTITLE,
            topic_result=topic_result,
            selected_subtopic=pre.selected_subtopic,
            selected_story=story_ctx,
            bundles=pre.story_bundles,
            character_result=character_result,
            format_result=format_result,
            narration_result=narration_result,
        )

    music_result = _music_mod.run_music_agent(
        MusicAgentInput(
            story=story_ctx,
            emotion=input_data.selected_story_tone.label_ko,
            duration_seconds=topic_result.duration_seconds,
        )
    )
    steps.append(DirectorStep.MUSIC.value)
    if not music_result.success:
        return _fail(
            main_topic=pre.main_topic,
            steps=steps,
            failed_at=DirectorStep.MUSIC,
            topic_result=topic_result,
            selected_subtopic=pre.selected_subtopic,
            selected_story=story_ctx,
            bundles=pre.story_bundles,
            character_result=character_result,
            format_result=format_result,
            narration_result=narration_result,
            music_result=music_result,
        )

    storyboard = _build_storyboard(
        story_ctx=story_ctx,
        character_result=character_result,
        format_result=format_result,
    )
    production_result = _production_mod.run_production_agent(
        ProductionAgentInput(
            storyboard=storyboard,
            narration=narration_result.narration_script,
            subtitle=narration_result.subtitle_script,
            music=music_result,
            format=format_result.format_plan,
            project_slug=input_data.project_slug,
        )
    )
    steps.append(DirectorStep.PRODUCTION.value)
    if not production_result.success:
        return _fail(
            main_topic=pre.main_topic,
            steps=steps,
            failed_at=DirectorStep.PRODUCTION,
            topic_result=topic_result,
            selected_subtopic=pre.selected_subtopic,
            selected_story=story_ctx,
            bundles=pre.story_bundles,
            character_result=character_result,
            format_result=format_result,
            narration_result=narration_result,
            music_result=music_result,
            production_result=production_result,
        )

    plan = format_result.format_plan
    review_result = _review_mod.run_review_agent(
        ReviewAgentInput(
            production_result=ProductionResultSnapshot(
                project_slug=input_data.project_slug or "mock_project",
                format=plan.format,
                target_duration_seconds=plan.target_duration_seconds,
                actual_duration_seconds=storyboard.total_duration_sec,
                character_consistency_score=0.92 if character_result.success else 0.5,
                has_subtitle_track=bool(narration_result.subtitle_script.cues),
                has_voice_track=bool(narration_result.narration_script.lines),
                subtitle_cue_count=len(narration_result.subtitle_script.cues),
                narration_line_count=len(narration_result.narration_script.lines),
                render_success=True,
                aspect_ratio=format_result.aspect_ratio,
            )
        )
    )
    steps.append(DirectorStep.REVIEW.value)

    review_passed = (
        review_result.review_report.passed if review_result.review_report else False
    )
    return PlanningDirectorResult(
        success=review_passed,
        main_topic=pre.main_topic,
        topic_result=topic_result,
        selected_subtopic=pre.selected_subtopic,
        selected_story=story_ctx,
        story_bundles=pre.story_bundles,
        character_result=character_result,
        format_result=format_result,
        narration_result=narration_result,
        music_result=music_result,
        production_result=production_result,
        review_result=review_result,
        steps=steps,
        meta={
            "mode": "full",
            "selected_story_tone": input_data.selected_story_tone.value,
            "format": plan.format.value,
            "review_passed": review_passed,
            "retry_target_agent": review_result.retry_target_agent.value,
            "recommended_cut_count": format_result.recommended_cut_count,
            "aspect_ratio": format_result.aspect_ratio,
        },
    )


TopicStoryDirectorInput = PlanningDirectorInput
TopicStoryDirectorResult = PlanningDirectorResult


def run_topic_story_pipeline(input_data: PlanningDirectorInput) -> PlanningDirectorResult:
    """Phase 3A: 01 → 02 만."""
    phase, early = _run_topic_story_phase(input_data)
    if early is not None:
        return early
    assert phase is not None
    return PlanningDirectorResult(
        success=True,
        main_topic=phase.topic_result.main_topic,
        topic_result=phase.topic_result,
        selected_subtopic=phase.active_subtopic,
        selected_story=phase.story_ctx,
        story_bundles=phase.bundles,
        steps=phase.steps,
        meta={"mode": "topic_story_only", "selected_story_tone": input_data.selected_story_tone.value},
    )


def example_director_result() -> dict[str, Any]:
    return example_full_pipeline_result()


def example_planning_result() -> dict[str, Any]:
    result = run_planning_pipeline(
        PlanningDirectorInput(
            main_topic="애견카페에서 다른 친구들과 신나게 노는 뽀식이",
            style="애니메이션",
            duration_seconds=20,
            cut_count=5,
            selected_subtopic_id="subtopic_1",
            selected_story_tone=StoryTone.COMIC,
            target_platform="youtube_shorts",
        )
    )
    plan = result.format_result.format_plan if result.format_result and result.format_result.format_plan else None
    return {
        "success": result.success,
        "mode": result.meta.get("mode"),
        "steps": result.steps,
        "format": plan.format.value if plan else None,
    }


def example_full_pipeline_result() -> dict[str, Any]:
    result = run_full_pipeline(
        PlanningDirectorInput(
            main_topic="애견카페에서 다른 친구들과 신나게 노는 뽀식이",
            style="애니메이션",
            duration_seconds=20,
            cut_count=5,
            project_slug="bposik_rainy_home",
            selected_subtopic_id="subtopic_1",
            selected_story_tone=StoryTone.COMIC,
            target_platform="youtube_shorts",
        )
    )
    plan = result.format_result.format_plan if result.format_result and result.format_result.format_plan else None
    report = result.review_result.review_report if result.review_result else None
    return {
        "success": result.success,
        "main_topic": result.main_topic,
        "steps": result.steps,
        "format": plan.format.value if plan else None,
        "character_name": (
            result.character_result.character_profile.character_name
            if result.character_result and result.character_result.character_profile
            else None
        ),
        "voice_style": (
            result.narration_result.voice_style.voice_id
            if result.narration_result and result.narration_result.voice_style
            else None
        ),
        "bpm": result.music_result.bpm if result.music_result else None,
        "review_passed": report.passed if report else None,
        "retry_target_agent": result.meta.get("retry_target_agent"),
    }
