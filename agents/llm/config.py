"""에이전트 LLM 모드·모델 설정 (main.py 미연결)."""

from __future__ import annotations

import os
from enum import Enum


class AgentLLMMode(str, Enum):
    MOCK = "mock"
    GPT = "gpt"


DEFAULT_TOPIC_MODEL = "gpt-4o-mini"
DEFAULT_STORY_MODEL = "gpt-4o-mini"


def resolve_llm_mode(raw: str | None = None) -> AgentLLMMode:
    """
    AGENT_LLM_MODE 환경 변수: mock | gpt (기본 mock).

    raw 인자가 있으면 env보다 우선 (테스트·주입용).
    """
    value = (raw or os.getenv("AGENT_LLM_MODE") or "mock").strip().lower()
    if value in ("gpt", "openai", "llm"):
        return AgentLLMMode.GPT
    return AgentLLMMode.MOCK


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
