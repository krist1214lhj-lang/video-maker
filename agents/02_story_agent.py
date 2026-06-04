"""
Agent 02 — story_agent (Phase 4: Mock | GPT)

역할:
  - 소주제 1개당 스토리 3종 생성
  - 톤: 코믹(comic) / 감성(emotional) / 반전(twist)

연결 금지:
  - main.py / FastAPI / templates

향후 main.py 대응 (참고만):
  - ScenarioOption (tone, title, summary, cut_flow) 1개 = 스토리 1종
  - scenario_options.json 의 scenarios[3] 와 1:1 매핑 예정
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

# Phase 3A: 소주제 타입은 01과 동일 계약 — importlib로 로드 (파일명 숫자 접두)
import importlib

_topic_module = importlib.import_module("agents.01_topic_agent")
SubTopic = _topic_module.SubTopic


class StoryTone(str, Enum):
    """스토리 톤 (UI·scenario_options 와 매핑)."""

    COMIC = "comic"  # 코믹
    EMOTIONAL = "emotional"  # 감성
    TWIST = "twist"  # 반전

    @property
    def label_ko(self) -> str:
        return {
            StoryTone.COMIC: "코믹",
            StoryTone.EMOTIONAL: "감성",
            StoryTone.TWIST: "반전",
        }[self]


@dataclass(frozen=True)
class StoryCutBeat:
    """컷 단위 비트 (향후 ScenarioCutFlowItem / StorylineCutItem 으로 변환)."""

    cut: int
    scene: str
    emotion: str
    narration: str
    subtitle: str


@dataclass(frozen=True)
class StoryVariant:
    """소주제에 대한 스토리 1종."""

    id: str
    tone: StoryTone
    title: str
    logline: str
    narration_outline: str
    cut_flow: tuple[StoryCutBeat, ...]
    summary: str = ""
    story_arc: str = ""


@dataclass
class StoryAgentInput:
    """02_story_agent 입력."""

    main_topic: str
    subtopic: SubTopic
    style: str = ""
    duration_seconds: int = 15
    cut_count: int = 5
    project_slug: str = ""
    selected_subtopic_id: str | None = None
    selected_story_tone: StoryTone | None = None
    topic_result: Any | None = None

    @classmethod
    def from_topic_selection(
        cls,
        *,
        topic_result: Any,
        selected_subtopic_id: str | None = None,
        selected_story_tone: StoryTone | None = None,
        project_slug: str = "",
    ) -> StoryAgentInput:
        """Director·run-demo: topic_result + 선택 소주제로 입력 구성."""
        sid = (
            (selected_subtopic_id or "").strip()
            or (getattr(topic_result, "selected_subtopic_id", None) or "").strip()
            or "subtopic_1"
        )
        subtopic = next(
            (s for s in topic_result.subtopics if s.id == sid),
            topic_result.subtopics[0],
        )
        return cls(
            main_topic=topic_result.main_topic,
            subtopic=subtopic,
            style=topic_result.style,
            duration_seconds=topic_result.duration_seconds,
            cut_count=topic_result.cut_count,
            project_slug=project_slug,
            selected_subtopic_id=sid,
            selected_story_tone=selected_story_tone,
            topic_result=topic_result,
        )


@dataclass
class StoryAgentResult:
    """02_story_agent 출력 — 항상 3종 (comic, emotional, twist)."""

    success: bool
    main_topic: str
    subtopic_id: str
    subtopic_title: str
    variants: list[StoryVariant]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meta: dict[str, Any] = field(default_factory=dict)


class StoryGenerator(Protocol):
    def generate_variants(self, input_data: StoryAgentInput) -> list[StoryVariant]:
        ...


def _default_cut_beats(subtopic_title: str, tone: StoryTone, cut_count: int) -> tuple[StoryCutBeat, ...]:
    """톤별 목 컷 비트."""
    templates: dict[StoryTone, list[tuple[str, str]]] = {
        StoryTone.COMIC: [
            ("과하게 자신 있는 시작", "자신"),
            ("계획과 다른 순간", "당황"),
            ("어색한 수습", "코믹"),
            ("잘 된 줄 알았던 착각", "들뜸"),
            ("작은 실수로 웃음", "유쾌"),
        ],
        StoryTone.EMOTIONAL: [
            ("조용한 시작", "고요"),
            ("작은 디테일", "공감"),
            ("감정이 깊어짐", "몰입"),
            ("따뜻한 전환", "위로"),
            ("여운 있는 마무리", "감동"),
        ],
        StoryTone.TWIST: [
            ("평범한 일상", "평온"),
            ("이상 신호", "의심"),
            ("단서 수집", "긴장"),
            ("반전 공개", "충격"),
            ("새로운 해석", "깨달음"),
        ],
    }
    beats = templates[tone]
    out: list[StoryCutBeat] = []
    for i in range(cut_count):
        scene_label, emotion = beats[min(i, len(beats) - 1)]
        scene = f"{subtopic_title} — {scene_label}"
        out.append(
            StoryCutBeat(
                cut=i + 1,
                scene=scene,
                emotion=emotion,
                narration=scene[:40],
                subtitle=scene[:40],
            )
        )
    return tuple(out)


@dataclass
class MockStoryGenerator:
    def generate_variants(self, input_data: StoryAgentInput) -> list[StoryVariant]:
        st = input_data.subtopic
        topic = input_data.main_topic.strip()
        style = input_data.style.strip() or "기본"
        cut_count = max(1, min(20, input_data.cut_count))
        variants: list[StoryVariant] = []

        for tone in (StoryTone.COMIC, StoryTone.EMOTIONAL, StoryTone.TWIST):
            label = tone.label_ko
            cut_flow = _default_cut_beats(st.title, tone, cut_count)
            logline = {
                StoryTone.COMIC: "도입-가식-들킴-들뜸-코믹 마무리",
                StoryTone.EMOTIONAL: "고요-공감-몰입-위로-여운",
                StoryTone.TWIST: "평온-의심-단서-반전-재해석",
            }[tone]
            outline = (
                f"'{topic}'을(를) {label} 톤으로 푼 "
                f"({style}) {cut_count}컷 이야기."
            )
            variants.append(
                StoryVariant(
                    id=f"story_{st.id}_{tone.value}",
                    tone=tone,
                    title=f"{label}형 · {st.title}",
                    logline=logline,
                    narration_outline=outline,
                    summary=outline,
                    story_arc=logline,
                    cut_flow=cut_flow,
                )
            )
        return variants


def _coerce_story_input(input_data: StoryAgentInput) -> StoryAgentInput:
    if input_data.topic_result is None:
        return input_data
    return StoryAgentInput.from_topic_selection(
        topic_result=input_data.topic_result,
        selected_subtopic_id=input_data.selected_subtopic_id,
        selected_story_tone=input_data.selected_story_tone,
        project_slug=input_data.project_slug,
    )


def run_story_agent(
    input_data: StoryAgentInput,
    *,
    generator: StoryGenerator | None = None,
    llm_mode: str | None = None,
) -> StoryAgentResult:
    """소주제 1개에 대해 코믹·감성·반전 스토리 3종을 생성한다."""
    coerced = _coerce_story_input(input_data)
    if not coerced.main_topic.strip() or not coerced.subtopic.id:
        return StoryAgentResult(
            success=False,
            main_topic=coerced.main_topic,
            subtopic_id=coerced.subtopic.id,
            subtopic_title=coerced.subtopic.title,
            variants=[],
            meta={"error": "main_topic and subtopic are required"},
        )

    from agents.llm.openai_client import LLMClientError
    from agents.llm.story_generator import create_story_generator, generator_mode_label

    gen, resolved = create_story_generator(llm_mode)
    if generator is not None:
        gen = generator
    try:
        variants = gen.generate_variants(coerced)
    except LLMClientError as exc:
        return StoryAgentResult(
            success=False,
            main_topic=coerced.main_topic.strip(),
            subtopic_id=coerced.subtopic.id,
            subtopic_title=coerced.subtopic.title,
            variants=[],
            meta={
                "error": str(exc),
                "generator": generator_mode_label(gen, resolved=resolved),
                "llm_mode": resolved.mode.value,
                "llm_mode_requested": resolved.requested.value,
                "llm_fallback_reason": resolved.fallback_reason,
            },
        )

    expected_tones = {StoryTone.COMIC, StoryTone.EMOTIONAL, StoryTone.TWIST}
    if {v.tone for v in variants} != expected_tones:
        return StoryAgentResult(
            success=False,
            main_topic=coerced.main_topic.strip(),
            subtopic_id=coerced.subtopic.id,
            subtopic_title=coerced.subtopic.title,
            variants=variants,
            meta={
                "error": "variants must include comic, emotional, twist",
                "generator": generator_mode_label(gen, resolved=resolved),
                "llm_mode": resolved.mode.value,
                "llm_mode_requested": resolved.requested.value,
                "llm_fallback_reason": resolved.fallback_reason,
            },
        )

    meta: dict[str, Any] = {
        "generator": generator_mode_label(gen, resolved=resolved),
        "llm_mode": resolved.mode.value,
        "llm_mode_requested": resolved.requested.value,
        "llm_fallback_reason": resolved.fallback_reason,
        "selected_subtopic_id": coerced.selected_subtopic_id,
        "selected_story_tone": (
            coerced.selected_story_tone.value if coerced.selected_story_tone else None
        ),
    }
    if hasattr(gen, "last_meta"):
        meta.update(getattr(gen, "last_meta") or {})

    return StoryAgentResult(
        success=True,
        main_topic=coerced.main_topic.strip(),
        subtopic_id=coerced.subtopic.id,
        subtopic_title=coerced.subtopic.title,
        variants=variants,
        meta=meta,
    )


def example_story_result() -> dict[str, Any]:
    """문서·테스트용 예시 JSON."""
    topic_mod = importlib.import_module("agents.01_topic_agent")
    topic_result = topic_mod.run_topic_agent(
        topic_mod.TopicAgentInput(
            main_topic="애견카페에서 다른 친구들과 신나게 노는 뽀식이",
            style="애니메이션",
            duration_seconds=20,
            cut_count=5,
        )
    )
    story_result = run_story_agent(
        StoryAgentInput(
            main_topic=topic_result.main_topic,
            subtopic=topic_result.subtopics[0],
            style=topic_result.style,
            duration_seconds=topic_result.duration_seconds,
            cut_count=topic_result.cut_count,
        )
    )
    return {
        "success": story_result.success,
        "subtopic_id": story_result.subtopic_id,
        "variants": [
            {
                "id": v.id,
                "tone": v.tone.value,
                "tone_label_ko": v.tone.label_ko,
                "title": v.title,
                "logline": v.logline,
                "narration_outline": v.narration_outline,
                "summary": v.summary or v.narration_outline,
                "story_arc": v.story_arc or v.logline,
                "cut_flow": [beat.__dict__ for beat in v.cut_flow],
            }
            for v in story_result.variants
        ],
    }
