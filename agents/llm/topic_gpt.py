"""GPT 기반 소주제 생성기."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from agents.llm.client import ChatCompletionRequest, LLMClient, LLMClientError
from agents.llm.config import resolve_topic_model
from agents.llm.parsers import parse_subtopics_from_llm
from agents.llm.prompts import topic_system_prompt, topic_user_prompt

_topic_mod = importlib.import_module("agents.01_topic_agent")
TopicAgentInput = _topic_mod.TopicAgentInput
SubTopic = _topic_mod.SubTopic


@dataclass
class GptTopicGenerator:
    """01_topic_agent — OpenAI 1회 호출로 소주제 3개."""

    client: LLMClient
    model: str | None = None
    temperature: float = 0.7

    def generate_subtopics(self, input_data: TopicAgentInput) -> list[SubTopic]:
        model = (self.model or resolve_topic_model()).strip()
        request = ChatCompletionRequest(
            system_prompt=topic_system_prompt(input_data.locale),
            user_prompt=topic_user_prompt(
                main_topic=input_data.main_topic.strip(),
                style=input_data.style,
                duration_seconds=input_data.duration_seconds,
                cut_count=input_data.cut_count,
            ),
            model=model,
            temperature=self.temperature,
            max_output_tokens=2048,
        )
        result = self.client.complete_json(request)
        try:
            subtopics = parse_subtopics_from_llm(result.text, input_data=input_data)
        except ValueError as exc:
            raise LLMClientError(f"topic JSON parse failed: {exc}") from exc

        self._last_meta = {
            "llm_model": result.model,
            "llm_usage": result.usage,
            "llm_provider": result.provider,
        }
        return subtopics

    @property
    def last_meta(self) -> dict[str, Any]:
        return getattr(self, "_last_meta", {})
