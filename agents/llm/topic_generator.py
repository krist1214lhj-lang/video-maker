"""01_topic_agent — Mock·GPT 소주제 생성기."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from agents.llm.config import (
    AgentLLMMode,
    ResolvedLLMMode,
    resolve_effective_llm_mode,
    resolve_topic_model,
)
from agents.llm.openai_client import (
    ChatCompletionRequest,
    LLMClient,
    LLMClientError,
    OpenAIChatClient,
    parse_json_object,
)

_topic_mod = importlib.import_module("agents.01_topic_agent")
TopicAgentInput = _topic_mod.TopicAgentInput
TopicGenerator = _topic_mod.TopicGenerator
SubTopic = _topic_mod.SubTopic
SubTopicAngle = _topic_mod.SubTopicAngle
MockTopicGenerator = _topic_mod.MockTopicGenerator

_EXPECTED_IDS = ("subtopic_1", "subtopic_2", "subtopic_3")
_ANGLE_MAP = {
    "relationship": SubTopicAngle.RELATIONSHIP,
    "place": SubTopicAngle.PLACE,
    "emotion": SubTopicAngle.EMOTION,
}


def _topic_system_prompt(locale: str) -> str:
    lang = "한국어" if (locale or "ko").startswith("ko") else "the user's locale"
    return f"""You are a short-form video planning assistant.
Return ONLY valid JSON. User-facing text in {lang}.
Produce exactly 3 subtopics with ids subtopic_1, subtopic_2, subtopic_3."""


def _topic_user_prompt(inp: TopicAgentInput) -> str:
    return f"""Main topic: {inp.main_topic.strip()}
Style: {inp.style or "(unspecified)"}
Duration (sec): {inp.duration_seconds}
Cut count: {inp.cut_count}

JSON:
{{
  "subtopics": [
    {{
      "id": "subtopic_1",
      "title": "...",
      "angle": "relationship|place|emotion",
      "hook": "...",
      "one_line_pitch": "..."
    }},
    {{ "id": "subtopic_2", ... }},
    {{ "id": "subtopic_3", ... }}
  ]
}}"""


def _parse_subtopics(text: str, *, inp: TopicAgentInput) -> list[SubTopic]:
    payload = parse_json_object(text)
    raw_list = payload.get("subtopics")
    if not isinstance(raw_list, list):
        raise ValueError("subtopics must be a list")
    by_id: dict[str, SubTopic] = {}
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        if sid not in _EXPECTED_IDS:
            continue
        angle = _ANGLE_MAP.get(
            str(item.get("angle") or "emotion").strip().lower(),
            SubTopicAngle.EMOTION,
        )
        title = str(item.get("title") or "").strip() or f"{inp.main_topic} — {sid}"
        hook = str(item.get("hook") or "").strip() or title[:60]
        pitch = str(item.get("one_line_pitch") or "").strip() or hook
        by_id[sid] = SubTopic(id=sid, title=title, angle=angle, hook=hook, one_line_pitch=pitch)
    ordered = [by_id[i] for i in _EXPECTED_IDS if i in by_id]
    if len(ordered) != 3:
        raise ValueError(f"expected 3 subtopics, got {len(ordered)}")
    return ordered


@dataclass
class GptTopicGenerator:
    client: LLMClient
    model: str | None = None
    temperature: float = 0.7

    def generate_subtopics(self, input_data: TopicAgentInput) -> list[SubTopic]:
        model = (self.model or resolve_topic_model()).strip()
        result = self.client.complete_json(
            ChatCompletionRequest(
                system_prompt=_topic_system_prompt(input_data.locale),
                user_prompt=_topic_user_prompt(input_data),
                model=model,
                temperature=self.temperature,
                max_output_tokens=2048,
            )
        )
        try:
            subtopics = _parse_subtopics(result.text, inp=input_data)
        except ValueError as exc:
            raise LLMClientError(f"topic JSON parse failed: {exc}") from exc
        object.__setattr__(
            self,
            "_last_meta",
            {
                "llm_model": result.model,
                "llm_usage": result.usage,
                "llm_provider": result.provider,
            },
        )
        return subtopics

    @property
    def last_meta(self) -> dict[str, Any]:
        return getattr(self, "_last_meta", {})


def create_topic_generator(mode: str | None = None) -> tuple[TopicGenerator, ResolvedLLMMode]:
    resolved = resolve_effective_llm_mode(mode)
    if resolved.mode == AgentLLMMode.MOCK:
        return MockTopicGenerator(), resolved
    return GptTopicGenerator(client=OpenAIChatClient()), resolved


def generator_mode_label(generator: Any, *, resolved: ResolvedLLMMode) -> str:
    name = type(generator).__name__
    if isinstance(generator, GptTopicGenerator):
        model = (getattr(generator, "last_meta", {}) or {}).get("llm_model") or ""
        label = f"{name}({model})" if model else name
    else:
        label = name
    if resolved.fallback_reason:
        return f"{label}[fallback:{resolved.fallback_reason}]"
    return label
