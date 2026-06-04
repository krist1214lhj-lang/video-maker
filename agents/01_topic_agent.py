"""
Agent 01 — topic_agent (Phase 3A: design only)

역할:
  - 대주제(메인 토픽) 확정·검증
  - 소주제 3개 생성 (서로 다른 각도)

연결 금지 (Phase 3A):
  - main.py / FastAPI / templates
  - 다른 에이전트 HTTP 호출

향후 main.py 대응 API (참고만):
  - POST /generate-scenario-options 의 topic·style·duration 입력과 유사
  - 소주제 3개는 scenario_options.json 의 scenarios[] 3안으로 매핑 예정
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol


class SubTopicAngle(str, Enum):
    """소주제가 바라보는 서사 각도 (예시 분류)."""

    RELATIONSHIP = "relationship"
    PLACE = "place"
    EMOTION = "emotion"


@dataclass(frozen=True)
class SubTopic:
    """소주제 1개."""

    id: str
    title: str
    angle: SubTopicAngle
    hook: str
    one_line_pitch: str


@dataclass
class TopicAgentInput:
    """01_topic_agent 입력."""

    main_topic: str
    style: str = ""
    duration_seconds: int = 15
    cut_count: int = 5
    project_slug: str = ""
    locale: str = "ko"


@dataclass
class TopicAgentResult:
    """01_topic_agent 출력."""

    success: bool
    main_topic: str
    style: str
    duration_seconds: int
    cut_count: int
    subtopics: list[SubTopic]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meta: dict[str, Any] = field(default_factory=dict)


class TopicGenerator(Protocol):
    """향후 LLM·규칙 엔진 주입용."""

    def generate_subtopics(self, input_data: TopicAgentInput) -> list[SubTopic]:
        ...


@dataclass
class MockTopicGenerator:
    """Phase 3A 목 구현 — main.py 미사용."""

    def generate_subtopics(self, input_data: TopicAgentInput) -> list[SubTopic]:
        topic = input_data.main_topic.strip()
        return [
            SubTopic(
                id="subtopic_1",
                title=f"{topic} — 첫 만남의 어색함",
                angle=SubTopicAngle.RELATIONSHIP,
                hook="낯선 공간에서 눈이 마주치는 순간",
                one_line_pitch="관계의 시작을 가볍게 여는 소주제",
            ),
            SubTopic(
                id="subtopic_2",
                title=f"{topic} — 공간이 주는 분위기",
                angle=SubTopicAngle.PLACE,
                hook="비 오는 창가, 따뜻한 조명",
                one_line_pitch="장소·분위기로 감정선을 깔는 소주제",
            ),
            SubTopic(
                id="subtopic_3",
                title=f"{topic} — 예상 못 한 반전",
                angle=SubTopicAngle.EMOTION,
                hook="마지막 컷에서 드러나는 작은 진실",
                one_line_pitch="반전 여지를 남기는 소주제",
            ),
        ]


def run_topic_agent(
    input_data: TopicAgentInput,
    *,
    generator: TopicGenerator | None = None,
) -> TopicAgentResult:
    """
    소주제 3개를 생성한다.

    Phase 3A: MockTopicGenerator만 사용. main import 금지.
    """
    if not input_data.main_topic.strip():
        return TopicAgentResult(
            success=False,
            main_topic="",
            style=input_data.style,
            duration_seconds=input_data.duration_seconds,
            cut_count=input_data.cut_count,
            subtopics=[],
            meta={"error": "main_topic is required"},
        )

    gen = generator or MockTopicGenerator()
    subtopics = gen.generate_subtopics(input_data)
    if len(subtopics) != 3:
        return TopicAgentResult(
            success=False,
            main_topic=input_data.main_topic.strip(),
            style=input_data.style,
            duration_seconds=input_data.duration_seconds,
            cut_count=input_data.cut_count,
            subtopics=subtopics,
            meta={"error": f"expected 3 subtopics, got {len(subtopics)}"},
        )

    return TopicAgentResult(
        success=True,
        main_topic=input_data.main_topic.strip(),
        style=input_data.style.strip(),
        duration_seconds=input_data.duration_seconds,
        cut_count=input_data.cut_count,
        subtopics=subtopics,
        meta={"generator": type(gen).__name__},
    )


def example_topic_result() -> dict[str, Any]:
    """문서·테스트용 예시 JSON (직렬화 형태)."""
    result = run_topic_agent(
        TopicAgentInput(
            main_topic="애견카페에서 다른 친구들과 신나게 노는 뽀식이",
            style="애니메이션",
            duration_seconds=20,
            cut_count=5,
            project_slug="bposik_rainy_home",
        )
    )
    return {
        "success": result.success,
        "main_topic": result.main_topic,
        "style": result.style,
        "duration_seconds": result.duration_seconds,
        "cut_count": result.cut_count,
        "subtopics": [
            {
                "id": s.id,
                "title": s.title,
                "angle": s.angle.value,
                "hook": s.hook,
                "one_line_pitch": s.one_line_pitch,
            }
            for s in result.subtopics
        ],
        "generated_at": result.generated_at,
    }
