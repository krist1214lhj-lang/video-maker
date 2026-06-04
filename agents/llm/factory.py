"""Mock / GPT 생성기 팩토리."""

from __future__ import annotations

import importlib
from typing import Any

from agents.llm.client import LLMClient, get_llm_client
from agents.llm.config import AgentLLMMode, resolve_llm_mode
from agents.llm.story_gpt import GptStoryGenerator
from agents.llm.topic_gpt import GptTopicGenerator

_topic_mod = importlib.import_module("agents.01_topic_agent")
_story_mod = importlib.import_module("agents.02_story_agent")

MockTopicGenerator = _topic_mod.MockTopicGenerator
MockStoryGenerator = _story_mod.MockStoryGenerator
TopicGenerator = _topic_mod.TopicGenerator
StoryGenerator = _story_mod.StoryGenerator


def create_topic_generator(mode: AgentLLMMode | str | None = None) -> TopicGenerator:
    resolved = resolve_llm_mode(mode.value if isinstance(mode, AgentLLMMode) else mode)
    if resolved == AgentLLMMode.MOCK:
        return MockTopicGenerator()
    client = get_llm_client(mode="gpt")
    assert client is not None
    return GptTopicGenerator(client=client)


def create_story_generator(mode: AgentLLMMode | str | None = None) -> StoryGenerator:
    resolved = resolve_llm_mode(mode.value if isinstance(mode, AgentLLMMode) else mode)
    if resolved == AgentLLMMode.MOCK:
        return MockStoryGenerator()
    client = get_llm_client(mode="gpt")
    assert client is not None
    return GptStoryGenerator(client=client)


def generator_mode_label(generator: Any) -> str:
    name = type(generator).__name__
    if isinstance(generator, GptTopicGenerator | GptStoryGenerator):
        meta = getattr(generator, "last_meta", {}) or {}
        model = meta.get("llm_model") or ""
        return f"{name}({model})" if model else name
    return name
