"""에이전트 LLM 모드·모델 설정 (main.py 미연결)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class AgentLLMMode(str, Enum):
    MOCK = "mock"
    GPT = "gpt"


DEFAULT_TOPIC_MODEL = "gpt-4o-mini"
DEFAULT_STORY_MODEL = "gpt-4o-mini"


@dataclass(frozen=True)
class ResolvedLLMMode:
    """요청 모드 + 실제 사용 모드 (키 없으면 mock fallback)."""

    mode: AgentLLMMode
    requested: AgentLLMMode
    fallback_reason: str | None = None


def resolve_llm_mode(raw: str | None = None) -> AgentLLMMode:
    value = (raw or os.getenv("AGENT_LLM_MODE") or "mock").strip().lower()
    if value in ("gpt", "openai", "llm"):
        return AgentLLMMode.GPT
    return AgentLLMMode.MOCK


def resolve_effective_llm_mode(raw: str | None = None) -> ResolvedLLMMode:
    requested = resolve_llm_mode(raw)
    if requested == AgentLLMMode.GPT and not openai_api_key():
        return ResolvedLLMMode(
            mode=AgentLLMMode.MOCK,
            requested=requested,
            fallback_reason="missing_openai_api_key",
        )
    return ResolvedLLMMode(mode=requested, requested=requested)


def resolve_topic_model() -> str:
    return (
        os.getenv("AGENT_LLM_TOPIC_MODEL")
        or os.getenv("AGENT_LLM_MODEL")
        or DEFAULT_TOPIC_MODEL
    ).strip()


def resolve_story_model() -> str:
    return (
        os.getenv("AGENT_LLM_STORY_MODEL")
        or os.getenv("AGENT_LLM_MODEL")
        or DEFAULT_STORY_MODEL
    ).strip()


def openai_api_key() -> str:
    return (os.getenv("OPENAI_API_KEY") or "").strip()
