"""GPT 기반 스토리 3톤 생성기."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from agents.llm.client import ChatCompletionRequest, LLMClient, LLMClientError
from agents.llm.config import resolve_story_model
from agents.llm.parsers import parse_variants_from_llm
from agents.llm.prompts import story_system_prompt, story_user_prompt

_story_mod = importlib.import_module("agents.02_story_agent")
StoryAgentInput = _story_mod.StoryAgentInput
StoryVariant = _story_mod.StoryVariant


@dataclass
class GptStoryGenerator:
    """02_story_agent — OpenAI 1회 호출로 comic / emotional / twist."""

    client: LLMClient
    model: str | None = None
    temperature: float = 0.8

    def generate_variants(self, input_data: StoryAgentInput) -> list[StoryVariant]:
        st = input_data.subtopic
        model = (self.model or resolve_story_model()).strip()
        request = ChatCompletionRequest(
            system_prompt=story_system_prompt(),
            user_prompt=story_user_prompt(
                main_topic=input_data.main_topic.strip(),
                subtopic_title=st.title,
                subtopic_hook=st.hook,
                subtopic_pitch=st.one_line_pitch,
                style=input_data.style,
                duration_seconds=input_data.duration_seconds,
                cut_count=input_data.cut_count,
            ),
            model=model,
            temperature=self.temperature,
            max_output_tokens=4096,
        )
        result = self.client.complete_json(request)
        try:
            variants = parse_variants_from_llm(result.text, input_data=input_data)
        except ValueError as exc:
            raise LLMClientError(f"story JSON parse failed: {exc}") from exc

        self._last_meta = {
            "llm_model": result.model,
            "llm_usage": result.usage,
            "llm_provider": result.provider,
        }
        return variants

    @property
    def last_meta(self) -> dict[str, Any]:
        return getattr(self, "_last_meta", {})
