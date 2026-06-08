"""06_music_agent — Mock/GPT short-form BGM spec planner."""

from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from agents.llm.config import (
    AgentLLMMode,
    ResolvedLLMMode,
    resolve_effective_llm_mode,
    resolve_music_model,
)
from agents.llm.openai_client import (
    ChatCompletionRequest,
    LLMClient,
    LLMClientError,
    OpenAIChatClient,
    parse_json_object,
)
from agents.llm.pipeline_meta import estimate_llm_cost_usd

_music_mod = importlib.import_module("agents.06_music_agent")

GptInput = _music_mod.MusicAgentInput
MockMusicPlanner = _music_mod.MockMusicPlanner
MusicPlanner = _music_mod.MusicPlanner


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "value"):
        return getattr(value, "value")
    return str(value)


def _string(value: Any, fallback: str = "") -> str:
    text = str(value if value is not None else fallback).strip()
    return text or fallback


def _duration(input_data: GptInput) -> int:
    return max(5, int(input_data.duration_seconds or 15))


def _narration_text(input_data: GptInput) -> str:
    script = getattr(input_data, "narration_script", None)
    full_text = getattr(script, "full_text", None)
    if full_text:
        return str(full_text)[:700]
    lines = getattr(script, "lines", None)
    if isinstance(lines, list):
        return " ".join(str(getattr(line, "text", "")) for line in lines)[:700]
    return ""


def _music_system_prompt() -> str:
    return """You are a Korean short-form BGM music supervisor.
Return ONLY valid JSON.
Create a BGM specification for a short video. Do not create or reference any real audio file."""


def _music_user_prompt(input_data: GptInput) -> str:
    story = input_data.story
    duration = _duration(input_data)
    return f"""Main topic: {story.main_topic}
Selected subtopic: {story.subtopic_title}
Story title: {story.title}
Story tone: {story.tone.value} ({story.tone.label_ko})
Story summary: {story.summary}
Story arc: {story.story_arc}
Cut count: {story.cut_count}
Requested emotion: {input_data.emotion or story.tone.label_ko}
Target platform: {input_data.target_platform or "youtube_shorts"}
Target duration seconds: {duration}
Character profile: {_jsonable(input_data.character_profile)}
Narration excerpt: {_narration_text(input_data)}
Format plan: {_jsonable(input_data.format_plan)}

JSON:
{{
  "music_style": "upbeat acoustic pop",
  "mood": "playful, slightly cynical, cute",
  "bpm": 126,
  "instruments": ["ukulele", "muted percussion", "soft bass"],
  "music_prompt": "Instrumental BGM for a 15s Korean Shorts video, ...",
  "duration_seconds": {duration}
}}
Rules:
- Specify BGM only. No vocals, no lyrics, no actual music file generation.
- Fit {duration} seconds and keep the ending loop-friendly.
- For Bposik/뽀식이, reflect a 9-year-old poodle-mix: slightly cynical, chaotic, cute, senior-dog perspective if useful.
- BPM must be an integer between 60 and 160.
- music_prompt must be usable as a concise music-generation prompt later."""


def _parse_music_response(
    text: str,
    *,
    input_data: GptInput,
) -> tuple[str, int, str, dict[str, Any]]:
    payload = parse_json_object(text)
    duration = _duration(input_data)
    style = _string(payload.get("music_style"), "neutral lo-fi bed")[:80]
    mood = _string(payload.get("mood"), input_data.emotion or input_data.story.tone.label_ko)[:120]
    raw_bpm = payload.get("bpm")
    try:
        bpm = int(raw_bpm)
    except (TypeError, ValueError):
        raise ValueError("bpm must be an integer") from None
    bpm = min(max(bpm, 60), 160)

    raw_instruments = payload.get("instruments")
    instruments: list[str] = []
    if isinstance(raw_instruments, list):
        instruments = [
            _string(item)[:40]
            for item in raw_instruments
            if _string(item)
        ][:8]
    if not instruments:
        instruments = ["light percussion", "soft bass"]

    prompt = _string(payload.get("music_prompt"))
    if not prompt:
        raise ValueError("music_prompt is required")
    prompt = prompt[:900]

    meta = {
        "mood": mood,
        "instruments": instruments,
        "duration_seconds": duration,
    }
    return style, bpm, prompt, meta


@dataclass
class GptMusicPlanner:
    client: LLMClient
    model: str | None = None
    temperature: float = 0.55

    def plan(self, input_data: GptInput) -> tuple[str, int, str]:
        model = (self.model or resolve_music_model()).strip()
        result = self.client.complete_json(
            ChatCompletionRequest(
                system_prompt=_music_system_prompt(),
                user_prompt=_music_user_prompt(input_data),
                model=model,
                temperature=self.temperature,
                max_output_tokens=1536,
            )
        )
        try:
            style, bpm, prompt, meta = _parse_music_response(
                result.text,
                input_data=input_data,
            )
        except ValueError as exc:
            raise LLMClientError(f"music JSON parse failed: {exc}") from exc
        cost = estimate_llm_cost_usd(result.usage, model=result.model)
        self._last_meta = {
            **meta,
            "llm_model": result.model,
            "llm_usage": result.usage,
            "llm_provider": result.provider,
            "llm_est_cost_usd": round(cost, 6) if cost is not None else None,
        }
        return style, bpm, prompt

    @property
    def last_meta(self) -> dict[str, Any]:
        return getattr(self, "_last_meta", {})


def create_music_planner(mode: str | None = None) -> tuple[MusicPlanner, ResolvedLLMMode]:
    resolved = resolve_effective_llm_mode(mode)
    if resolved.mode == AgentLLMMode.MOCK:
        return MockMusicPlanner(), resolved
    return GptMusicPlanner(client=OpenAIChatClient()), resolved


def planner_mode_label(planner: Any, *, resolved: ResolvedLLMMode) -> str:
    name = type(planner).__name__
    if isinstance(planner, GptMusicPlanner):
        model = (getattr(planner, "last_meta", {}) or {}).get("llm_model") or ""
        label = f"{name}({model})" if model else name
    else:
        label = name
    if resolved.fallback_reason:
        return f"{label}[fallback:{resolved.fallback_reason}]"
    return label
