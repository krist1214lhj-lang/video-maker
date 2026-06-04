"""02_story_agent — Mock·GPT 스토리 3톤 생성기."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from agents.llm.config import (
    AgentLLMMode,
    ResolvedLLMMode,
    resolve_effective_llm_mode,
    resolve_story_model,
)
from agents.llm.openai_client import (
    ChatCompletionRequest,
    LLMClient,
    LLMClientError,
    OpenAIChatClient,
    parse_json_object,
)

_story_mod = importlib.import_module("agents.02_story_agent")
StoryAgentInput = _story_mod.StoryAgentInput
StoryGenerator = _story_mod.StoryGenerator
StoryTone = _story_mod.StoryTone
StoryVariant = _story_mod.StoryVariant
StoryCutBeat = _story_mod.StoryCutBeat
MockStoryGenerator = _story_mod.MockStoryGenerator

_TONE_MAP = {
    "comic": StoryTone.COMIC,
    "emotional": StoryTone.EMOTIONAL,
    "twist": StoryTone.TWIST,
}


def _story_system_prompt() -> str:
    return """You are a short-form video storywriter.
Return ONLY valid JSON.
Provide comic, emotional, twist variants; each with title, logline, narration_outline, cut_flow."""


def _story_user_prompt(inp: StoryAgentInput) -> str:
    st = inp.subtopic
    return f"""Main topic: {inp.main_topic.strip()}
Subtopic: {st.title}
Hook: {st.hook}
Pitch: {st.one_line_pitch}
Style: {inp.style or "(unspecified)"}
Duration (sec): {inp.duration_seconds}
Cut count: {inp.cut_count}

JSON:
{{
  "variants": [
    {{
      "tone": "comic",
      "title": "...",
      "logline": "one-line hook",
      "narration_outline": "2-3 sentence narration plan",
      "cut_flow": [
        {{
          "cut": 1,
          "scene": "...",
          "emotion": "...",
          "narration": "...",
          "subtitle": "..."
        }}
      ]
    }},
    {{ "tone": "emotional", ... }},
    {{ "tone": "twist", ... }}
  ]
}}
Rules: exactly {inp.cut_count} cuts per variant; tones comic, emotional, twist."""


def _build_variant(
    *,
    tone: StoryTone,
    subtopic_id: str,
    title: str,
    logline: str,
    narration_outline: str,
    cut_flow: tuple[StoryCutBeat, ...],
) -> StoryVariant:
    return StoryVariant(
        id=f"story_{subtopic_id}_{tone.value}",
        tone=tone,
        title=title,
        logline=logline,
        narration_outline=narration_outline,
        summary=narration_outline,
        story_arc=logline,
        cut_flow=cut_flow,
    )


def _parse_variants(text: str, *, inp: StoryAgentInput) -> list[StoryVariant]:
    payload = parse_json_object(text)
    raw_list = payload.get("variants")
    if not isinstance(raw_list, list):
        raise ValueError("variants must be a list")
    cut_count = max(1, min(20, inp.cut_count))
    st = inp.subtopic
    by_tone: dict[StoryTone, StoryVariant] = {}

    for item in raw_list:
        if not isinstance(item, dict):
            continue
        tone = _TONE_MAP.get(str(item.get("tone") or "").strip().lower())
        if tone is None:
            continue
        title = str(item.get("title") or "").strip() or f"{tone.label_ko} · {st.title}"
        logline = str(item.get("logline") or item.get("story_arc") or "").strip() or title
        outline = str(
            item.get("narration_outline") or item.get("summary") or ""
        ).strip() or logline

        beats: list[StoryCutBeat] = []
        raw_flow = item.get("cut_flow")
        if isinstance(raw_flow, list):
            for idx, beat in enumerate(raw_flow[:cut_count], start=1):
                if not isinstance(beat, dict):
                    continue
                scene = str(beat.get("scene") or "").strip() or f"{st.title} — 컷 {idx}"
                emotion = str(beat.get("emotion") or "").strip() or "중립"
                narration = str(beat.get("narration") or scene).strip()[:200]
                subtitle = str(beat.get("subtitle") or narration).strip()[:80]
                beats.append(
                    StoryCutBeat(
                        cut=int(beat.get("cut") or idx),
                        scene=scene,
                        emotion=emotion,
                        narration=narration,
                        subtitle=subtitle,
                    )
                )
        while len(beats) < cut_count:
            n = len(beats) + 1
            beats.append(
                StoryCutBeat(
                    cut=n,
                    scene=f"{st.title} — 컷 {n}",
                    emotion="중립",
                    narration=f"{st.title} 컷 {n}",
                    subtitle=f"컷 {n}",
                )
            )
        by_tone[tone] = _build_variant(
            tone=tone,
            subtopic_id=st.id,
            title=title,
            logline=logline,
            narration_outline=outline,
            cut_flow=tuple(beats[:cut_count]),
        )

    expected = {StoryTone.COMIC, StoryTone.EMOTIONAL, StoryTone.TWIST}
    if set(by_tone.keys()) != expected:
        missing = expected - set(by_tone.keys())
        raise ValueError(f"missing tones: {[t.value for t in missing]}")
    return [by_tone[StoryTone.COMIC], by_tone[StoryTone.EMOTIONAL], by_tone[StoryTone.TWIST]]


@dataclass
class GptStoryGenerator:
    client: LLMClient
    model: str | None = None
    temperature: float = 0.8

    def generate_variants(self, input_data: StoryAgentInput) -> list[StoryVariant]:
        model = (self.model or resolve_story_model()).strip()
        result = self.client.complete_json(
            ChatCompletionRequest(
                system_prompt=_story_system_prompt(),
                user_prompt=_story_user_prompt(input_data),
                model=model,
                temperature=self.temperature,
                max_output_tokens=4096,
            )
        )
        try:
            variants = _parse_variants(result.text, inp=input_data)
        except ValueError as exc:
            raise LLMClientError(f"story JSON parse failed: {exc}") from exc
        object.__setattr__(
            self,
            "_last_meta",
            {
                "llm_model": result.model,
                "llm_usage": result.usage,
                "llm_provider": result.provider,
            },
        )
        return variants

    @property
    def last_meta(self) -> dict[str, Any]:
        return getattr(self, "_last_meta", {})


def create_story_generator(mode: str | None = None) -> tuple[StoryGenerator, ResolvedLLMMode]:
    resolved = resolve_effective_llm_mode(mode)
    if resolved.mode == AgentLLMMode.MOCK:
        return MockStoryGenerator(), resolved
    return GptStoryGenerator(client=OpenAIChatClient()), resolved


def generator_mode_label(generator: Any, *, resolved: ResolvedLLMMode) -> str:
    name = type(generator).__name__
    if isinstance(generator, GptStoryGenerator):
        model = (getattr(generator, "last_meta", {}) or {}).get("llm_model") or ""
        label = f"{name}({model})" if model else name
    else:
        label = name
    if resolved.fallback_reason:
        return f"{label}[fallback:{resolved.fallback_reason}]"
    return label
