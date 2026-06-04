"""
Agent 05 — narration_subtitle_agent (Phase 3A-3: design only)

역할: 스토리·톤·포맷 기반 나레이션·자막 스크립트·보이스 스타일 (Mock).

입력: story, tone, format
출력: narration_script, subtitle_script, voice_style

연결 금지: main.py / API / UI
(post_production/narration_subtitle.py 는 Phase 1 실구현 — 별개)
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

_char_mod = importlib.import_module("agents.03_character_agent")
_format_mod = importlib.import_module("agents.04_format_agent")
_story_mod = importlib.import_module("agents.02_story_agent")

StoryContext = _char_mod.StoryContext
StoryTone = _story_mod.StoryTone
OutputFormat = _format_mod.OutputFormat
FormatPlan = _format_mod.FormatPlan


@dataclass
class NarrationLine:
    cut: int
    text: str
    emotion: str = ""


@dataclass
class SubtitleCue:
    cut: int
    text: str
    start_sec: float = 0.0
    end_sec: float = 0.0


@dataclass
class NarrationScript:
    locale: str
    lines: list[NarrationLine]
    full_text: str


@dataclass
class SubtitleScript:
    locale: str
    cues: list[SubtitleCue]
    srt_preview: str


@dataclass
class VoiceStyle:
    voice_id: str
    label_ko: str
    pace: str
    pitch: str
    force_short_dialogue: bool = True


@dataclass
class NarrationSubtitleAgentInput:
    story: StoryContext
    tone: StoryTone
    format: OutputFormat | FormatPlan
    locale: str = "ko"
    project_slug: str = ""


@dataclass
class NarrationSubtitleAgentResult:
    success: bool
    narration_script: NarrationScript | None
    subtitle_script: SubtitleScript | None
    voice_style: VoiceStyle | None
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meta: dict[str, Any] = field(default_factory=dict)


class NarrationSubtitleBuilder(Protocol):
    def build(self, input_data: NarrationSubtitleAgentInput) -> tuple[
        NarrationScript, SubtitleScript, VoiceStyle
    ]:
        ...


def _resolve_format(fmt: OutputFormat | FormatPlan) -> OutputFormat:
    if isinstance(fmt, FormatPlan):
        return fmt.format
    return fmt


@dataclass
class MockNarrationSubtitleBuilder:
    def build(
        self, input_data: NarrationSubtitleAgentInput
    ) -> tuple[NarrationScript, SubtitleScript, VoiceStyle]:
        story = input_data.story
        tone = input_data.tone
        out_fmt = _resolve_format(input_data.format)
        cut_count = max(1, story.cut_count)

        pace = "fast" if out_fmt == OutputFormat.SHORTS else "moderate"
        voice_map = {
            StoryTone.COMIC: ("ko_playful", "밝은 캐릭터 나레이션", "fast", "mid-high"),
            StoryTone.EMOTIONAL: ("ko_warm", "따뜻한 감성 나레이션", "slow", "mid"),
            StoryTone.TWIST: ("ko_dramatic", "반전 강조 나레이션", "moderate", "low-mid"),
        }
        vid, label, vp, pitch = voice_map.get(tone, voice_map[StoryTone.COMIC])

        lines: list[NarrationLine] = []
        cues: list[SubtitleCue] = []
        sec_per = max(2.0, 15.0 / cut_count)
        for i in range(1, cut_count + 1):
            narr = f"[컷{i}] {story.subtopic_title}: {story.summary[:40]}…"
            lines.append(NarrationLine(cut=i, text=narr, emotion=tone.label_ko))
            start = (i - 1) * sec_per
            cues.append(
                SubtitleCue(
                    cut=i,
                    text=narr.split("] ", 1)[-1][:42],
                    start_sec=start,
                    end_sec=start + sec_per * 0.9,
                )
            )

        full_text = "\n".join(ln.text for ln in lines)
        srt_lines = []
        for cue in cues:
            srt_lines.append(f"{cue.cut}\n00:00:{int(cue.start_sec):02d},000 --> 00:00:{int(cue.end_sec):02d},000\n{cue.text}\n")
        narration = NarrationScript(locale=input_data.locale, lines=lines, full_text=full_text)
        subtitle = SubtitleScript(
            locale=input_data.locale,
            cues=cues,
            srt_preview="\n".join(srt_lines[:6]),
        )
        voice = VoiceStyle(
            voice_id=vid,
            label_ko=label,
            pace=vp,
            pitch=pitch,
            force_short_dialogue=out_fmt in {OutputFormat.SHORTS, OutputFormat.CARTOON},
        )
        return narration, subtitle, voice


def run_narration_subtitle_agent(
    input_data: NarrationSubtitleAgentInput,
    *,
    builder: NarrationSubtitleBuilder | None = None,
) -> NarrationSubtitleAgentResult:
    if not input_data.story.main_topic.strip():
        return NarrationSubtitleAgentResult(
            success=False,
            narration_script=None,
            subtitle_script=None,
            voice_style=None,
            meta={"error": "story is required"},
        )
    gen = builder or MockNarrationSubtitleBuilder()
    narration, subtitle, voice = gen.build(input_data)
    return NarrationSubtitleAgentResult(
        success=True,
        narration_script=narration,
        subtitle_script=subtitle,
        voice_style=voice,
        meta={"builder": type(gen).__name__, "line_count": len(narration.lines)},
    )


def example_narration_subtitle_result() -> dict[str, Any]:
    char = importlib.import_module("agents.03_character_agent")
    fmt = importlib.import_module("agents.04_format_agent")
    topic = importlib.import_module("agents.01_topic_agent")
    story_m = importlib.import_module("agents.02_story_agent")
    tr = topic.run_topic_agent(topic.TopicAgentInput(main_topic="비 오는 날", style="감성"))
    sr = story_m.run_story_agent(
        story_m.StoryAgentInput(main_topic=tr.main_topic, subtopic=tr.subtopics[0], cut_count=4)
    )
    ctx = char.StoryContext.from_variant(
        main_topic=tr.main_topic,
        subtopic_id=tr.subtopics[0].id,
        subtopic_title=tr.subtopics[0].title,
        variant=sr.variants[1],
        cut_count=4,
    )
    r = run_narration_subtitle_agent(
        NarrationSubtitleAgentInput(
            story=ctx,
            tone=story_m.StoryTone.EMOTIONAL,
            format=fmt.OutputFormat.SHORTS,
        )
    )
    return {
        "success": r.success,
        "narration_lines": len(r.narration_script.lines) if r.narration_script else 0,
        "subtitle_cues": len(r.subtitle_script.cues) if r.subtitle_script else 0,
        "voice_style": r.voice_style.voice_id if r.voice_style else None,
    }
