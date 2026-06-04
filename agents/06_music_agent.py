"""
Agent 06 — music_agent (Phase 3A-3: design only)

역할: 스토리·감정·길이 기반 BGM 스타일·BPM·프롬프트 (Mock).

입력: story, emotion, duration
출력: music_style, bpm, music_prompt

연결 금지: main.py / API / UI
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

_char_mod = importlib.import_module("agents.03_character_agent")
_story_mod = importlib.import_module("agents.02_story_agent")

StoryContext = _char_mod.StoryContext
StoryTone = _story_mod.StoryTone


@dataclass
class MusicAgentInput:
    story: StoryContext
    emotion: str
    duration_seconds: int = 15


@dataclass
class MusicAgentResult:
    success: bool
    music_style: str
    bpm: int
    music_prompt: str
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meta: dict[str, Any] = field(default_factory=dict)


class MusicPlanner(Protocol):
    def plan(self, input_data: MusicAgentInput) -> tuple[str, int, str]:
        ...


@dataclass
class MockMusicPlanner:
    def plan(self, input_data: MusicAgentInput) -> tuple[str, int, str]:
        story = input_data.story
        emotion = (input_data.emotion or story.tone.label_ko).strip()
        duration = max(5, input_data.duration_seconds)

        tone = story.tone
        if tone == StoryTone.COMIC or "코믹" in emotion or "comic" in emotion.lower():
            style, bpm = "upbeat acoustic pop", 118
        elif tone == StoryTone.TWIST or "반전" in emotion:
            style, bpm = "cinematic tension pulse", 92
        elif tone == StoryTone.EMOTIONAL or "감성" in emotion:
            style, bpm = "soft piano ambient", 72
        else:
            style, bpm = "neutral lo-fi bed", 85

        if duration <= 20:
            bpm = min(bpm + 8, 128)
        elif duration >= 90:
            bpm = max(bpm - 10, 60)

        prompt = (
            f"Instrumental BGM for '{story.main_topic}', mood={emotion}, "
            f"style={style}, {bpm} BPM, duration≈{duration}s, "
            f"no vocals, loop-friendly ending."
        )
        return style, bpm, prompt


def run_music_agent(
    input_data: MusicAgentInput,
    *,
    planner: MusicPlanner | None = None,
) -> MusicAgentResult:
    if not input_data.story.main_topic.strip():
        return MusicAgentResult(
            success=False,
            music_style="",
            bpm=0,
            music_prompt="",
            meta={"error": "story is required"},
        )
    gen = planner or MockMusicPlanner()
    style, bpm, prompt = gen.plan(input_data)
    return MusicAgentResult(
        success=True,
        music_style=style,
        bpm=bpm,
        music_prompt=prompt,
        meta={"planner": type(gen).__name__},
    )


def example_music_result() -> dict[str, Any]:
    char = importlib.import_module("agents.03_character_agent")
    story_m = importlib.import_module("agents.02_story_agent")
    topic = importlib.import_module("agents.01_topic_agent")
    tr = topic.run_topic_agent(topic.TopicAgentInput(main_topic="창가의 비", style="감성"))
    sr = story_m.run_story_agent(
        story_m.StoryAgentInput(main_topic=tr.main_topic, subtopic=tr.subtopics[0], cut_count=5)
    )
    ctx = char.StoryContext.from_variant(
        main_topic=tr.main_topic,
        subtopic_id=tr.subtopics[0].id,
        subtopic_title=tr.subtopics[0].title,
        variant=sr.variants[1],
        cut_count=5,
    )
    r = run_music_agent(
        MusicAgentInput(story=ctx, emotion="감성", duration_seconds=tr.duration_seconds)
    )
    return {"success": r.success, "music_style": r.music_style, "bpm": r.bpm}
