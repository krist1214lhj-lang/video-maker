"""
Agent 09 — director_agent (Phase 3A-2: design only, Planning pipeline)

역할:
  - 전반부 기획 오케스트레이션 (HTTP·main.py 미연결)
  - 호출 순서: 09 Director → 01 Topic → 02 Story → 03 Character → 04 Format

주의 — 이름 충돌:
  - `agents/post_production/director.py` = 후반작업(05→07), `pipeline_director` shim.
  - 이 모듈 = **기획 Director** (01→04).

상세 다이어그램: docs/agents/DIRECTOR_PIPELINE.md
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

_topic_mod = importlib.import_module("agents.01_topic_agent")
_story_mod = importlib.import_module("agents.02_story_agent")
_character_mod = importlib.import_module("agents.03_character_agent")
_format_mod = importlib.import_module("agents.04_format_agent")

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
SubTopic = _topic_mod.SubTopic


class DirectorStep(str, Enum):
    DIRECTOR = "09_director"
    TOPIC = "01_topic"
    STORY = "02_story"
    CHARACTER = "03_character"
    FORMAT = "04_format"


@dataclass
class PlanningDirectorInput:
    """09_director_agent — 전체 기획 파이프라인 입력."""

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
    """09_director_agent — 전체 기획 파이프라인 출력."""

    success: bool
    main_topic: str
    topic_result: TopicAgentResult | None
    selected_subtopic: SubTopic | None
    selected_story: StoryContext | None
    story_bundles: list[SubTopicStoryBundle]
    character_result: CharacterAgentResult | None
    format_result: FormatAgentResult | None
    steps: list[str]
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


def _run_topic_story_phase(input_data: PlanningDirectorInput) -> tuple[_TopicStoryPhase | None, PlanningDirectorResult | None]:
    """01 Topic → 02 Story (공통 전단). 실패 시 PlanningDirectorResult 반환."""
    steps: list[str] = [DirectorStep.DIRECTOR.value]

    topic_input = TopicAgentInput(
        main_topic=input_data.main_topic,
        style=input_data.style,
        duration_seconds=input_data.duration_seconds,
        cut_count=input_data.cut_count,
        project_slug=input_data.project_slug,
        locale=input_data.locale,
    )
    topic_result = _topic_mod.run_topic_agent(topic_input)
    steps.append(DirectorStep.TOPIC.value)

    if not topic_result.success:
        return None, PlanningDirectorResult(
            success=False,
            main_topic=input_data.main_topic,
            topic_result=topic_result,
            selected_subtopic=None,
            selected_story=None,
            story_bundles=[],
            character_result=None,
            format_result=None,
            steps=steps,
            meta={"failed_at": DirectorStep.TOPIC.value},
        )

    subtopics = list(topic_result.subtopics)
    if input_data.selected_subtopic_id:
        subtopics = [s for s in subtopics if s.id == input_data.selected_subtopic_id]
        if not subtopics:
            return None, PlanningDirectorResult(
                success=False,
                main_topic=topic_result.main_topic,
                topic_result=topic_result,
                selected_subtopic=None,
                selected_story=None,
                story_bundles=[],
                character_result=None,
                format_result=None,
                steps=steps,
                meta={"failed_at": DirectorStep.TOPIC.value, "error": "unknown subtopic"},
            )

    bundles: list[SubTopicStoryBundle] = []
    for subtopic in subtopics:
        story_input = StoryAgentInput(
            main_topic=topic_result.main_topic,
            subtopic=subtopic,
            style=topic_result.style,
            duration_seconds=topic_result.duration_seconds,
            cut_count=topic_result.cut_count,
            project_slug=input_data.project_slug,
        )
        story_result = _story_mod.run_story_agent(story_input)
        if not story_result.success:
            return None, PlanningDirectorResult(
                success=False,
                main_topic=topic_result.main_topic,
                topic_result=topic_result,
                selected_subtopic=None,
                selected_story=None,
                story_bundles=bundles,
                character_result=None,
                format_result=None,
                steps=steps + [DirectorStep.STORY.value],
                meta={"failed_at": DirectorStep.STORY.value, "subtopic_id": subtopic.id},
            )
        bundles.append(SubTopicStoryBundle(subtopic=subtopic, story_result=story_result))
        steps.append(f"{DirectorStep.STORY.value}:{subtopic.id}")

    active_subtopic = subtopics[0]
    active_bundle = bundles[0]
    variant = _select_story_variant(active_bundle.story_result, input_data.selected_story_tone)
    if variant is None:
        return None, PlanningDirectorResult(
            success=False,
            main_topic=topic_result.main_topic,
            topic_result=topic_result,
            selected_subtopic=active_subtopic,
            selected_story=None,
            story_bundles=bundles,
            character_result=None,
            format_result=None,
            steps=steps,
            meta={"failed_at": DirectorStep.STORY.value, "error": "no story variant"},
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


def run_planning_pipeline(input_data: PlanningDirectorInput) -> PlanningDirectorResult:
    """
    호출 순서 (in-process):

      09 Director — 입력 검증·단계 조율 (이 함수)
      01 Topic    — 소주제 3개
      02 Story    — 소주제당 스토리 3종 (코믹/감성/반전)
      03 Character — 선택 스토리 + reference_characters 기반 prompt/config
      04 Format   — 출력 방식 format_plan

    개별 에이전트는 다른 에이전트 HTTP를 호출하지 않는다.
    """
    phase, early = _run_topic_story_phase(input_data)
    if early is not None:
        return early
    assert phase is not None

    steps = phase.steps
    topic_result = phase.topic_result
    active_subtopic = phase.active_subtopic
    story_ctx = phase.story_ctx
    bundles = phase.bundles

    ref = input_data.reference_character or ReferenceCharacter(
        name="bposik_v2",
        identity_lock_strength="high",
    )
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
        return PlanningDirectorResult(
            success=False,
            main_topic=topic_result.main_topic,
            topic_result=topic_result,
            selected_subtopic=active_subtopic,
            selected_story=story_ctx,
            story_bundles=bundles,
            character_result=character_result,
            format_result=None,
            steps=steps,
            meta={"failed_at": DirectorStep.CHARACTER.value},
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
        return PlanningDirectorResult(
            success=False,
            main_topic=topic_result.main_topic,
            topic_result=topic_result,
            selected_subtopic=active_subtopic,
            selected_story=story_ctx,
            story_bundles=bundles,
            character_result=character_result,
            format_result=format_result,
            steps=steps,
            meta={"failed_at": DirectorStep.FORMAT.value},
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
            "selected_story_tone": input_data.selected_story_tone.value,
            "format": format_result.format_plan.format.value if format_result.format_plan else None,
            "recommended_cut_count": format_result.recommended_cut_count,
            "aspect_ratio": format_result.aspect_ratio,
        },
    )


# --- Phase 3A 호환 (01→02 만) ---

TopicStoryDirectorInput = PlanningDirectorInput
TopicStoryDirectorResult = PlanningDirectorResult


def run_topic_story_pipeline(input_data: PlanningDirectorInput) -> PlanningDirectorResult:
    """Phase 3A: Topic → Story 까지만 (03·04 생략)."""
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
        character_result=None,
        format_result=None,
        steps=phase.steps,
        meta={"mode": "topic_story_only", "selected_story_tone": input_data.selected_story_tone.value},
    )


def example_director_result() -> dict[str, Any]:
    result = run_planning_pipeline(
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
    return {
        "success": result.success,
        "main_topic": result.main_topic,
        "steps": result.steps,
        "selected_story_tone": result.meta.get("selected_story_tone"),
        "format": plan.format.value if plan else None,
        "character_name": (
            result.character_result.character_profile.character_name
            if result.character_result and result.character_result.character_profile
            else None
        ),
        "recommended_cut_count": (
            result.format_result.recommended_cut_count if result.format_result else None
        ),
        "aspect_ratio": (
            result.format_result.aspect_ratio if result.format_result else None
        ),
    }


def example_planning_result() -> dict[str, Any]:
    return example_director_result()
