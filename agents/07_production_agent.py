"""
Agent 07 — production_agent (Phase 3A-3: design only)

역할: 스토리보드·오디오·포맷을 묶어 production_plan · render_plan (Mock).

입력: storyboard, narration, subtitle, music, format
출력: production_plan, render_plan

연결 금지: main.py / API / UI
(post_production/production.py 는 Phase 1 final_export — 별개)
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

_format_mod = importlib.import_module("agents.04_format_agent")
_narr_mod = importlib.import_module("agents.05_narration_subtitle_agent")
_music_mod = importlib.import_module("agents.06_music_agent")

OutputFormat = _format_mod.OutputFormat
FormatPlan = _format_mod.FormatPlan
NarrationScript = _narr_mod.NarrationScript
SubtitleScript = _narr_mod.SubtitleScript
MusicAgentResult = _music_mod.MusicAgentResult


class RenderStage(str, Enum):
    ASSEMBLE_TIMELINE = "assemble_timeline"
    MIX_AUDIO = "mix_audio"
    BURN_SUBTITLES = "burn_subtitles"
    ENCODE = "encode"
    PUBLISH_MANIFEST = "publish_manifest"


@dataclass
class StoryboardCut:
    cut: int
    scene: str
    visual_prompt: str
    duration_sec: float


@dataclass
class StoryboardPlan:
    cuts: list[StoryboardCut]
    aspect_ratio: str
    total_duration_sec: float


@dataclass
class ProductionPlan:
    project_slug: str
    format: OutputFormat
    cut_count: int
    timeline_steps: list[str]
    asset_manifest: dict[str, Any]


@dataclass
class RenderPlan:
    stages: list[RenderStage]
    output_container: str
    output_resolution: str
    estimated_render_seconds: int


@dataclass
class ProductionAgentInput:
    storyboard: StoryboardPlan
    narration: NarrationScript
    subtitle: SubtitleScript
    music: MusicAgentResult
    format: OutputFormat | FormatPlan
    project_slug: str = ""


@dataclass
class ProductionAgentResult:
    success: bool
    production_plan: ProductionPlan | None
    render_plan: RenderPlan | None
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meta: dict[str, Any] = field(default_factory=dict)


class ProductionPlanner(Protocol):
    def plan(self, input_data: ProductionAgentInput) -> tuple[ProductionPlan, RenderPlan]:
        ...


def _resolve_format(fmt: OutputFormat | FormatPlan) -> tuple[OutputFormat, str, int]:
    if isinstance(fmt, FormatPlan):
        return fmt.format, fmt.aspect_ratio, fmt.recommended_cut_count
    return fmt, "9:16" if fmt == OutputFormat.SHORTS else "16:9", 5


@dataclass
class MockProductionPlanner:
    def plan(self, input_data: ProductionAgentInput) -> tuple[ProductionPlan, RenderPlan]:
        out_fmt, aspect, _ = _resolve_format(input_data.format)
        sb = input_data.storyboard
        narr = input_data.narration
        sub = input_data.subtitle
        music = input_data.music

        container = "mp4" if out_fmt != _format_mod.OutputFormat.CARD_NEWS else "zip_cards"
        res = "1080x1920" if aspect == "9:16" else "1920x1080" if aspect == "16:9" else "1080x1350"

        prod = ProductionPlan(
            project_slug=input_data.project_slug or "mock_project",
            format=out_fmt,
            cut_count=len(sb.cuts),
            timeline_steps=[
                "bind_storyboard_cuts",
                "attach_narration_tracks",
                "attach_subtitle_track",
                "mix_bgm_under_dialogue",
                "validate_timeline_duration",
            ],
            asset_manifest={
                "storyboard_cuts": len(sb.cuts),
                "narration_lines": len(narr.lines),
                "subtitle_cues": len(sub.cues),
                "bgm_bpm": music.bpm,
                "bgm_style": music.music_style,
            },
        )
        stages = [
            RenderStage.ASSEMBLE_TIMELINE,
            RenderStage.MIX_AUDIO,
            RenderStage.BURN_SUBTITLES,
            RenderStage.ENCODE,
        ]
        if out_fmt == _format_mod.OutputFormat.SLIDESHOW:
            stages = [RenderStage.ASSEMBLE_TIMELINE, RenderStage.ENCODE, RenderStage.PUBLISH_MANIFEST]
        render = RenderPlan(
            stages=stages,
            output_container=container,
            output_resolution=res,
            estimated_render_seconds=max(30, int(sb.total_duration_sec * 2)),
        )
        return prod, render


def run_production_agent(
    input_data: ProductionAgentInput,
    *,
    planner: ProductionPlanner | None = None,
) -> ProductionAgentResult:
    if not input_data.storyboard.cuts:
        return ProductionAgentResult(
            success=False,
            production_plan=None,
            render_plan=None,
            meta={"error": "storyboard must have at least one cut"},
        )
    gen = planner or MockProductionPlanner()
    prod, render = gen.plan(input_data)
    return ProductionAgentResult(
        success=True,
        production_plan=prod,
        render_plan=render,
        meta={"planner": type(gen).__name__},
    )


def example_production_result() -> dict[str, Any]:
    narr = importlib.import_module("agents.05_narration_subtitle_agent")
    music = importlib.import_module("agents.06_music_agent")
    fmt = importlib.import_module("agents.04_format_agent")
    char = importlib.import_module("agents.03_character_agent")
    story_m = importlib.import_module("agents.02_story_agent")
    topic = importlib.import_module("agents.01_topic_agent")
    tr = topic.run_topic_agent(topic.TopicAgentInput(main_topic="테스트", cut_count=3))
    sr = story_m.run_story_agent(
        story_m.StoryAgentInput(main_topic=tr.main_topic, subtopic=tr.subtopics[0], cut_count=3)
    )
    ctx = char.StoryContext.from_variant(
        main_topic=tr.main_topic,
        subtopic_id=tr.subtopics[0].id,
        subtopic_title=tr.subtopics[0].title,
        variant=sr.variants[0],
        cut_count=3,
    )
    nr = narr.run_narration_subtitle_agent(
        narr.NarrationSubtitleAgentInput(
            story=ctx, tone=story_m.StoryTone.COMIC, format=fmt.OutputFormat.SHORTS
        )
    )
    mr = music.run_music_agent(music.MusicAgentInput(story=ctx, emotion="코믹", duration_seconds=15))
    sb = StoryboardPlan(
        cuts=[
            StoryboardCut(cut=i, scene=f"scene_{i}", visual_prompt=f"cut {i}", duration_sec=3.0)
            for i in range(1, 4)
        ],
        aspect_ratio="9:16",
        total_duration_sec=9.0,
    )
    assert nr.narration_script and nr.subtitle_script
    r = run_production_agent(
        ProductionAgentInput(
            storyboard=sb,
            narration=nr.narration_script,
            subtitle=nr.subtitle_script,
            music=mr,
            format=fmt.OutputFormat.SHORTS,
        )
    )
    assert r.production_plan and r.render_plan
    return {
        "success": r.success,
        "format": r.production_plan.format.value,
        "render_stages": [s.value for s in r.render_plan.stages],
    }
