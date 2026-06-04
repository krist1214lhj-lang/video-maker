"""
Agent 04 — format_agent (Phase 3A-2: design only)

역할:
  - 스토리·플랫폼·길이를 바탕으로 출력 방식(format) 결정
  - format_plan 생성 (컷 수, 비율, 페이싱, 제작 노트)

지원 format:
  - shorts
  - long_video
  - slideshow
  - cartoon
  - card_news

연결 금지:
  - main.py / FastAPI / templates
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

_char_mod = importlib.import_module("agents.03_character_agent")
_story_mod = importlib.import_module("agents.02_story_agent")
StoryContext = _char_mod.StoryContext


class OutputFormat(str, Enum):
    SHORTS = "shorts"
    LONG_VIDEO = "long_video"
    SLIDESHOW = "slideshow"
    CARTOON = "cartoon"
    CARD_NEWS = "card_news"

    @property
    def label_ko(self) -> str:
        return {
            OutputFormat.SHORTS: "숏츠",
            OutputFormat.LONG_VIDEO: "롱폼 영상",
            OutputFormat.SLIDESHOW: "슬라이드쇼",
            OutputFormat.CARTOON: "카툰",
            OutputFormat.CARD_NEWS: "카드뉴스",
        }[self]


@dataclass
class FormatPlan:
    """04_format_agent 출력 본체."""

    format: OutputFormat
    label_ko: str
    aspect_ratio: str
    target_duration_seconds: int
    recommended_cut_count: int
    pacing: str
    platform_notes: str
    production_hints: list[str]


@dataclass
class FormatAgentInput:
    """04_format_agent 입력."""

    story: StoryContext
    target_platform: str = "youtube_shorts"
    duration_seconds: int = 15
    style: str = ""
    preferred_format: OutputFormat | None = None


@dataclass
class FormatAgentResult:
    """출력: format_plan + recommended_cut_count + aspect_ratio (편의 필드)."""

    success: bool
    format_plan: FormatPlan | None
    recommended_cut_count: int = 0
    aspect_ratio: str = ""
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meta: dict[str, Any] = field(default_factory=dict)


class FormatPlanner(Protocol):
    def plan(self, input_data: FormatAgentInput) -> FormatPlan:
        ...


def _normalize_platform(value: str) -> str:
    return (value or "").strip().lower().replace(" ", "_")


@dataclass
class MockFormatPlanner:
    def plan(self, input_data: FormatAgentInput) -> FormatPlan:
        if input_data.preferred_format:
            fmt = input_data.preferred_format
        else:
            fmt = self._infer_format(input_data)
        duration = max(5, input_data.duration_seconds)
        platform = _normalize_platform(input_data.target_platform)

        specs: dict[OutputFormat, dict[str, Any]] = {
            OutputFormat.SHORTS: {
                "aspect_ratio": "9:16",
                "pacing": "fast",
                "cuts": max(3, min(12, duration // 3)),
                "hints": ["Hook in first 1.5s", "One beat per cut", "Vertical safe zone"],
            },
            OutputFormat.LONG_VIDEO: {
                "aspect_ratio": "16:9",
                "pacing": "moderate",
                "cuts": max(5, min(30, duration // 5)),
                "hints": ["Act structure", "B-roll allowance", "Narration-led transitions"],
            },
            OutputFormat.SLIDESHOW: {
                "aspect_ratio": "16:9",
                "pacing": "slow",
                "cuts": max(4, min(15, duration // 4)),
                "hints": ["Static key visuals", "Ken Burns optional", "Caption-first"],
            },
            OutputFormat.CARTOON: {
                "aspect_ratio": "16:9",
                "pacing": "moderate",
                "cuts": max(5, min(20, input_data.story.cut_count)),
                "hints": ["Exaggerated acting", "Consistent line art style", "Panel-like framing"],
            },
            OutputFormat.CARD_NEWS: {
                "aspect_ratio": "4:5",
                "pacing": "static",
                "cuts": max(3, min(10, duration // 2)),
                "hints": ["One message per card", "Bold typography", "Square-safe layout"],
            },
        }
        spec = specs[fmt]
        platform_notes = f"Target platform: {platform or 'generic'}."
        if platform in {"youtube_shorts", "tiktok", "reels", "instagram"} and fmt != OutputFormat.SHORTS:
            platform_notes += " Consider shorts for vertical discovery."

        return FormatPlan(
            format=fmt,
            label_ko=fmt.label_ko,
            aspect_ratio=spec["aspect_ratio"],
            target_duration_seconds=duration,
            recommended_cut_count=int(spec["cuts"]),
            pacing=spec["pacing"],
            platform_notes=platform_notes,
            production_hints=list(spec["hints"]),
        )

    def _infer_format(self, input_data: FormatAgentInput) -> OutputFormat:
        duration = input_data.duration_seconds
        platform = _normalize_platform(input_data.target_platform)
        tone = input_data.story.tone

        if platform in {"card", "card_news", "instagram_card"}:
            return OutputFormat.CARD_NEWS
        if platform in {"slideshow", "slides", "ppt"}:
            return OutputFormat.SLIDESHOW
        if "cartoon" in _normalize_platform(platform):
            return OutputFormat.CARTOON
        if duration >= 90:
            return OutputFormat.LONG_VIDEO
        if tone == _story_mod.StoryTone.COMIC and duration <= 30:
            return OutputFormat.CARTOON
        if duration <= 45 and platform in {"youtube_shorts", "tiktok", "reels", "shorts"}:
            return OutputFormat.SHORTS
        if duration <= 25:
            return OutputFormat.SHORTS
        return OutputFormat.LONG_VIDEO


def run_format_agent(
    input_data: FormatAgentInput,
    *,
    planner: FormatPlanner | None = None,
) -> FormatAgentResult:
    """출력 방식 format_plan 을 결정한다."""
    if not input_data.story.main_topic.strip():
        return FormatAgentResult(
            success=False,
            format_plan=None,
            recommended_cut_count=0,
            aspect_ratio="",
            meta={"error": "story context is required"},
        )

    gen = planner or MockFormatPlanner()
    plan = gen.plan(input_data)
    return FormatAgentResult(
        success=True,
        format_plan=plan,
        recommended_cut_count=plan.recommended_cut_count,
        aspect_ratio=plan.aspect_ratio,
        meta={"planner": type(gen).__name__},
    )


def example_format_result() -> dict[str, Any]:
    topic_mod = importlib.import_module("agents.01_topic_agent")
    story_mod = importlib.import_module("agents.02_story_agent")
    tr = topic_mod.run_topic_agent(topic_mod.TopicAgentInput(main_topic="비 오는 날 창가", style="감성"))
    sr = story_mod.run_story_agent(
        story_mod.StoryAgentInput(main_topic=tr.main_topic, subtopic=tr.subtopics[0], cut_count=5)
    )
    ctx = StoryContext.from_variant(
        main_topic=tr.main_topic,
        subtopic_id=tr.subtopics[0].id,
        subtopic_title=tr.subtopics[0].title,
        variant=sr.variants[1],
        cut_count=5,
    )
    result = run_format_agent(
        FormatAgentInput(
            story=ctx,
            target_platform="youtube_shorts",
            duration_seconds=20,
        )
    )
    plan = result.format_plan
    assert plan is not None
    return {
        "success": result.success,
        "format_plan": {
            "format": plan.format.value,
            "label_ko": plan.label_ko,
            "pacing": plan.pacing,
        },
        "recommended_cut_count": result.recommended_cut_count,
        "aspect_ratio": result.aspect_ratio,
    }
