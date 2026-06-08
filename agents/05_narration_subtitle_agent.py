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
CharacterProfile = _char_mod.CharacterProfile
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
class CharacterVoiceProfile:
    narrator_perspective: str
    speaking_style: str
    sarcasm_level: str
    energy_level: str
    age_tone: str
    catchphrase_style: str = ""
    sentence_length: str = "short"
    emotional_range: str = ""


@dataclass
class NarrationSubtitleAgentInput:
    story: StoryContext
    tone: StoryTone
    format: OutputFormat | FormatPlan
    locale: str = "ko"
    project_slug: str = ""
    character_profile: CharacterProfile | None = None
    character_prompt: str = ""
    character_voice_profile: CharacterVoiceProfile | None = None
    target_platform: str = ""
    duration_seconds: int | None = None


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


def _target_duration_seconds(input_data: NarrationSubtitleAgentInput) -> int:
    if isinstance(input_data.format, FormatPlan):
        return max(1, int(input_data.format.target_duration_seconds))
    if input_data.duration_seconds:
        return max(1, int(input_data.duration_seconds))
    return 15


def default_character_voice_profile(
    input_data: NarrationSubtitleAgentInput,
) -> CharacterVoiceProfile:
    profile = input_data.character_profile
    character_name = (getattr(profile, "character_name", "") or "").strip().lower()
    if input_data.character_voice_profile is not None:
        return input_data.character_voice_profile
    if character_name in {"bposik", "bposik_v2"} or "뽀식" in input_data.story.main_topic:
        return CharacterVoiceProfile(
            narrator_perspective="first_person",
            speaking_style="playful_dry",
            sarcasm_level="medium",
            energy_level="chaotic",
            age_tone="senior",
            catchphrase_style="짧게 투덜거리지만 귀엽게 마무리",
            sentence_length="very_short",
            emotional_range="시니컬하지만 따뜻함, 천방지축 행동과 노견 여유가 공존",
        )
    return CharacterVoiceProfile(
        narrator_perspective="third_person",
        speaking_style="playful" if input_data.tone == StoryTone.COMIC else "warm",
        sarcasm_level="low" if input_data.tone == StoryTone.COMIC else "none",
        energy_level="high" if input_data.tone == StoryTone.COMIC else "moderate",
        age_tone="adult",
        sentence_length="short",
        emotional_range=input_data.tone.label_ko,
    )


def estimate_voice_seconds(text: str) -> float:
    compact = "".join((text or "").split())
    if not compact:
        return 0.0
    # Korean short-form narration usually lands around 7-9 chars/sec.
    return round(max(1.0, len(compact) / 8.0), 2)


@dataclass
class MockNarrationSubtitleBuilder:
    def build(
        self, input_data: NarrationSubtitleAgentInput
    ) -> tuple[NarrationScript, SubtitleScript, VoiceStyle]:
        story = input_data.story
        tone = input_data.tone
        out_fmt = _resolve_format(input_data.format)
        cut_count = max(1, story.cut_count)
        target_duration = _target_duration_seconds(input_data)
        voice_profile = default_character_voice_profile(input_data)

        pace = "fast" if out_fmt == OutputFormat.SHORTS else "moderate"
        voice_map = {
            StoryTone.COMIC: ("ko_playful", "밝은 캐릭터 나레이션", "fast", "mid-high"),
            StoryTone.EMOTIONAL: ("ko_warm", "따뜻한 감성 나레이션", "slow", "mid"),
            StoryTone.TWIST: ("ko_dramatic", "반전 강조 나레이션", "moderate", "low-mid"),
        }
        vid, label, vp, pitch = voice_map.get(tone, voice_map[StoryTone.COMIC])

        lines: list[NarrationLine] = []
        cues: list[SubtitleCue] = []
        sec_per = max(1.0, float(target_duration) / cut_count)
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
        self._last_meta = {
            "character_voice_profile": voice_profile.__dict__,
            "estimated_voice_seconds": estimate_voice_seconds(full_text),
        }
        return narration, subtitle, voice

    @property
    def last_meta(self) -> dict[str, Any]:
        return getattr(self, "_last_meta", {})


def run_narration_subtitle_agent(
    input_data: NarrationSubtitleAgentInput,
    *,
    builder: NarrationSubtitleBuilder | None = None,
    llm_mode: str | None = None,
) -> NarrationSubtitleAgentResult:
    if not input_data.story.main_topic.strip():
        return NarrationSubtitleAgentResult(
            success=False,
            narration_script=None,
            subtitle_script=None,
            voice_style=None,
            meta={"error": "story is required"},
        )
    from agents.llm.config import AgentLLMMode, ResolvedLLMMode
    from agents.llm.openai_client import LLMClientError
    from agents.llm.narration_subtitle_generator import (
        builder_mode_label,
        create_narration_subtitle_builder,
    )

    resolved = ResolvedLLMMode(mode=AgentLLMMode.MOCK, requested=AgentLLMMode.MOCK)
    gen = builder
    if gen is None:
        gen, resolved = create_narration_subtitle_builder(llm_mode)

    fallback_error = None
    try:
        narration, subtitle, voice = gen.build(input_data)
    except LLMClientError as exc:
        fallback_error = str(exc)
        gen = MockNarrationSubtitleBuilder()
        narration, subtitle, voice = gen.build(input_data)
        resolved = ResolvedLLMMode(
            mode=AgentLLMMode.MOCK,
            requested=resolved.requested,
            fallback_reason="gpt_error",
        )
    meta: dict[str, Any] = {
        "builder": builder_mode_label(gen, resolved=resolved),
        "line_count": len(narration.lines),
        "cue_count": len(subtitle.cues),
        "llm_mode": resolved.mode.value,
        "llm_mode_requested": resolved.requested.value,
        "llm_fallback_reason": resolved.fallback_reason,
        "narration_llm_mode": resolved.mode.value,
        "narration_llm_mode_requested": resolved.requested.value,
        "narration_llm_fallback_reason": resolved.fallback_reason,
        "subtitle_llm_mode": resolved.mode.value,
        "subtitle_llm_mode_requested": resolved.requested.value,
        "subtitle_llm_fallback_reason": resolved.fallback_reason,
    }
    if fallback_error:
        meta["llm_error"] = fallback_error
    if hasattr(gen, "last_meta"):
        meta.update(getattr(gen, "last_meta") or {})
    return NarrationSubtitleAgentResult(
        success=True,
        narration_script=narration,
        subtitle_script=subtitle,
        voice_style=voice,
        meta=meta,
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
