"""05_narration_subtitle_agent — Mock/GPT narration and subtitle builder."""

from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from agents.llm.config import (
    AgentLLMMode,
    ResolvedLLMMode,
    resolve_effective_llm_mode,
    resolve_narration_subtitle_model,
)
from agents.llm.openai_client import (
    ChatCompletionRequest,
    LLMClient,
    LLMClientError,
    OpenAIChatClient,
    parse_json_object,
)
from agents.llm.pipeline_meta import estimate_llm_cost_usd

_narr_mod = importlib.import_module("agents.05_narration_subtitle_agent")
_format_mod = importlib.import_module("agents.04_format_agent")

CharacterVoiceProfile = _narr_mod.CharacterVoiceProfile
FormatPlan = _format_mod.FormatPlan
GptInput = _narr_mod.NarrationSubtitleAgentInput
MockNarrationSubtitleBuilder = _narr_mod.MockNarrationSubtitleBuilder
NarrationLine = _narr_mod.NarrationLine
NarrationScript = _narr_mod.NarrationScript
NarrationSubtitleBuilder = _narr_mod.NarrationSubtitleBuilder
SubtitleCue = _narr_mod.SubtitleCue
SubtitleScript = _narr_mod.SubtitleScript
VoiceStyle = _narr_mod.VoiceStyle
default_character_voice_profile = _narr_mod.default_character_voice_profile
estimate_voice_seconds = _narr_mod.estimate_voice_seconds


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


def _format_payload(inp: GptInput) -> dict[str, Any]:
    fmt = inp.format
    if isinstance(fmt, FormatPlan):
        return _jsonable(fmt)
    return {"format": fmt.value, "target_duration_seconds": inp.duration_seconds or 15}


def _target_duration(inp: GptInput) -> int:
    if isinstance(inp.format, FormatPlan):
        return max(1, int(inp.format.target_duration_seconds))
    return max(1, int(inp.duration_seconds or 15))


def _narration_system_prompt() -> str:
    return """You are a Korean short-form narration and subtitle writer.
Return ONLY valid JSON.
Create narration, subtitles, voice style, and a reusable character_voice_profile.
Keep the output suitable for fast 15-second Shorts unless another duration is given."""


def _narration_user_prompt(inp: GptInput) -> str:
    story = inp.story
    voice_profile = default_character_voice_profile(inp)
    target_duration = _target_duration(inp)
    return f"""Main topic: {story.main_topic}
Selected subtopic: {story.subtopic_title}
Story title: {story.title}
Story tone: {inp.tone.value} ({inp.tone.label_ko})
Story summary: {story.summary}
Story arc: {story.story_arc}
Cut count: {story.cut_count}
Locale: {inp.locale}
Project slug: {inp.project_slug or "(unspecified)"}
Target platform: {inp.target_platform or "youtube_shorts"}
Target duration seconds: {target_duration}
Format: {_format_payload(inp)}

Character profile: {_jsonable(inp.character_profile)}
Character prompt excerpt: {(inp.character_prompt or "")[:800]}
Requested character_voice_profile: {_jsonable(voice_profile)}

JSON:
{{
  "narration_script": {{
    "locale": "{inp.locale}",
    "lines": [
      {{ "cut": 1, "text": "short Korean narration", "emotion": "{inp.tone.label_ko}" }}
    ],
    "full_text": "all narration lines joined"
  }},
  "subtitle_script": {{
    "locale": "{inp.locale}",
    "cues": [
      {{ "cut": 1, "text": "short screen subtitle", "start_sec": 0.0, "end_sec": 2.7 }}
    ],
    "srt_preview": "optional SRT preview"
  }},
  "voice_style": {{
    "voice_id": "ko_bposik_playful_dry",
    "label_ko": "살짝 시니컬한 뽀식이 목소리",
    "pace": "fast",
    "pitch": "mid",
    "force_short_dialogue": true
  }},
  "character_voice_profile": {{
    "narrator_perspective": "first_person",
    "speaking_style": "playful_dry",
    "sarcasm_level": "medium",
    "energy_level": "chaotic",
    "age_tone": "senior",
    "catchphrase_style": "짧게 투덜거리지만 귀엽게 마무리",
    "sentence_length": "very_short",
    "emotional_range": "시니컬하지만 따뜻함"
  }},
  "estimated_voice_seconds": 14.0
}}
Rules:
- Return exactly {story.cut_count} narration lines and exactly {story.cut_count} subtitle cues.
- For 15-second Shorts, keep Korean narration very short: about 90-130 Korean characters total.
- Subtitles must be shorter than narration and easy to read on a phone.
- If the character is bposik_v2 or 뽀식이, use a 9-year-old poodle-mix voice: slightly cynical, chaotic, cute, optionally first-person senior-dog perspective.
- Avoid long exposition. Make every line usable as voiceover.
- Cue times must stay within {target_duration} seconds and must not overlap."""


def _srt_timestamp(seconds: float) -> str:
    safe = max(0.0, float(seconds))
    whole = int(safe)
    millis = int(round((safe - whole) * 1000))
    return f"00:00:{whole:02d},{millis:03d}"


def _build_srt_preview(cues: list[SubtitleCue]) -> str:
    lines: list[str] = []
    for cue in cues[:6]:
        lines.append(
            f"{cue.cut}\n{_srt_timestamp(cue.start_sec)} --> {_srt_timestamp(cue.end_sec)}\n{cue.text}\n"
        )
    return "\n".join(lines)


def _string(value: Any, fallback: str = "") -> str:
    text = str(value if value is not None else fallback).strip()
    return text or fallback


def _profile_from_payload(value: Any, fallback: CharacterVoiceProfile) -> CharacterVoiceProfile:
    raw = value if isinstance(value, dict) else {}
    return CharacterVoiceProfile(
        narrator_perspective=_string(raw.get("narrator_perspective"), fallback.narrator_perspective),
        speaking_style=_string(raw.get("speaking_style"), fallback.speaking_style),
        sarcasm_level=_string(raw.get("sarcasm_level"), fallback.sarcasm_level),
        energy_level=_string(raw.get("energy_level"), fallback.energy_level),
        age_tone=_string(raw.get("age_tone"), fallback.age_tone),
        catchphrase_style=_string(raw.get("catchphrase_style"), fallback.catchphrase_style),
        sentence_length=_string(raw.get("sentence_length"), fallback.sentence_length),
        emotional_range=_string(raw.get("emotional_range"), fallback.emotional_range),
    )


def _parse_narration_response(
    text: str,
    *,
    inp: GptInput,
) -> tuple[NarrationScript, SubtitleScript, VoiceStyle, CharacterVoiceProfile, float]:
    payload = parse_json_object(text)
    narr_payload = payload.get("narration_script")
    sub_payload = payload.get("subtitle_script")
    voice_payload = payload.get("voice_style")
    if not isinstance(narr_payload, dict):
        raise ValueError("narration_script must be an object")
    if not isinstance(sub_payload, dict):
        raise ValueError("subtitle_script must be an object")
    if not isinstance(voice_payload, dict):
        raise ValueError("voice_style must be an object")

    cut_count = max(1, int(inp.story.cut_count))
    target_duration = float(_target_duration(inp))
    sec_per = target_duration / cut_count

    lines: list[NarrationLine] = []
    raw_lines = narr_payload.get("lines")
    if isinstance(raw_lines, list):
        for idx, item in enumerate(raw_lines[:cut_count], start=1):
            if not isinstance(item, dict):
                continue
            line_text = _string(item.get("text"), f"{inp.story.subtopic_title} 컷 {idx}")[:120]
            lines.append(
                NarrationLine(
                    cut=int(item.get("cut") or idx),
                    text=line_text,
                    emotion=_string(item.get("emotion"), inp.tone.label_ko),
                )
            )
    while len(lines) < cut_count:
        idx = len(lines) + 1
        lines.append(
            NarrationLine(
                cut=idx,
                text=f"{inp.story.subtopic_title} 컷 {idx}",
                emotion=inp.tone.label_ko,
            )
        )
    lines = [
        NarrationLine(cut=idx, text=line.text, emotion=line.emotion)
        for idx, line in enumerate(lines[:cut_count], start=1)
    ]
    full_text = _string(narr_payload.get("full_text"), "\n".join(line.text for line in lines))

    cues: list[SubtitleCue] = []
    raw_cues = sub_payload.get("cues")
    if isinstance(raw_cues, list):
        for idx, item in enumerate(raw_cues[:cut_count], start=1):
            if not isinstance(item, dict):
                continue
            start = float(item.get("start_sec") if item.get("start_sec") is not None else (idx - 1) * sec_per)
            end = float(item.get("end_sec") if item.get("end_sec") is not None else start + sec_per * 0.9)
            start = min(max(0.0, start), target_duration)
            end = min(max(start + 0.2, end), target_duration)
            cues.append(
                SubtitleCue(
                    cut=int(item.get("cut") or idx),
                    text=_string(item.get("text"), lines[idx - 1].text)[:42],
                    start_sec=round(start, 2),
                    end_sec=round(end, 2),
                )
            )
    while len(cues) < cut_count:
        idx = len(cues) + 1
        start = (idx - 1) * sec_per
        cues.append(
            SubtitleCue(
                cut=idx,
                text=lines[idx - 1].text[:42],
                start_sec=round(start, 2),
                end_sec=round(min(target_duration, start + sec_per * 0.9), 2),
            )
        )
    cues = [
        SubtitleCue(cut=idx, text=cue.text, start_sec=cue.start_sec, end_sec=cue.end_sec)
        for idx, cue in enumerate(cues[:cut_count], start=1)
    ]

    fallback_profile = default_character_voice_profile(inp)
    voice_profile = _profile_from_payload(payload.get("character_voice_profile"), fallback_profile)
    voice = VoiceStyle(
        voice_id=_string(voice_payload.get("voice_id"), "ko_bposik_playful_dry"),
        label_ko=_string(voice_payload.get("label_ko"), "살짝 시니컬한 캐릭터 나레이션"),
        pace=_string(voice_payload.get("pace"), "fast"),
        pitch=_string(voice_payload.get("pitch"), "mid"),
        force_short_dialogue=bool(voice_payload.get("force_short_dialogue", True)),
    )
    estimated = payload.get("estimated_voice_seconds")
    estimated_seconds = float(estimated) if isinstance(estimated, (int, float)) else estimate_voice_seconds(full_text)
    estimated_seconds = round(min(max(0.0, estimated_seconds), target_duration), 2)
    narration = NarrationScript(
        locale=_string(narr_payload.get("locale"), inp.locale),
        lines=lines,
        full_text=full_text,
    )
    subtitle = SubtitleScript(
        locale=_string(sub_payload.get("locale"), inp.locale),
        cues=cues,
        srt_preview=_string(sub_payload.get("srt_preview"), _build_srt_preview(cues)),
    )
    return narration, subtitle, voice, voice_profile, estimated_seconds


@dataclass
class GptNarrationSubtitleBuilder:
    client: LLMClient
    model: str | None = None
    temperature: float = 0.7

    def build(self, input_data: GptInput) -> tuple[NarrationScript, SubtitleScript, VoiceStyle]:
        model = (self.model or resolve_narration_subtitle_model()).strip()
        result = self.client.complete_json(
            ChatCompletionRequest(
                system_prompt=_narration_system_prompt(),
                user_prompt=_narration_user_prompt(input_data),
                model=model,
                temperature=self.temperature,
                max_output_tokens=3072,
            )
        )
        try:
            narration, subtitle, voice, profile, estimated = _parse_narration_response(
                result.text,
                inp=input_data,
            )
        except ValueError as exc:
            raise LLMClientError(f"narration/subtitle JSON parse failed: {exc}") from exc
        cost = estimate_llm_cost_usd(result.usage, model=result.model)
        self._last_meta = {
            "character_voice_profile": asdict(profile),
            "estimated_voice_seconds": estimated,
            "llm_model": result.model,
            "llm_usage": result.usage,
            "llm_provider": result.provider,
            "llm_est_cost_usd": round(cost, 6) if cost is not None else None,
        }
        return narration, subtitle, voice

    @property
    def last_meta(self) -> dict[str, Any]:
        return getattr(self, "_last_meta", {})


def create_narration_subtitle_builder(
    mode: str | None = None,
) -> tuple[NarrationSubtitleBuilder, ResolvedLLMMode]:
    resolved = resolve_effective_llm_mode(mode)
    if resolved.mode == AgentLLMMode.MOCK:
        return MockNarrationSubtitleBuilder(), resolved
    return GptNarrationSubtitleBuilder(client=OpenAIChatClient()), resolved


def builder_mode_label(builder: Any, *, resolved: ResolvedLLMMode) -> str:
    name = type(builder).__name__
    if isinstance(builder, GptNarrationSubtitleBuilder):
        model = (getattr(builder, "last_meta", {}) or {}).get("llm_model") or ""
        label = f"{name}({model})" if model else name
    else:
        label = name
    if resolved.fallback_reason:
        return f"{label}[fallback:{resolved.fallback_reason}]"
    return label
