"""OpenAI Chat 호출 인터페이스 (Phase 4)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from agents.llm.config import openai_api_key


class LLMClientError(RuntimeError):
    """GPT 호출·파싱 실패."""


@dataclass(frozen=True)
class ChatCompletionRequest:
    system_prompt: str
    user_prompt: str
    model: str
    temperature: float = 0.7
    max_output_tokens: int = 4096
    json_mode: bool = True


@dataclass(frozen=True)
class ChatCompletionResult:
    text: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    provider: str = "openai"


class LLMClient(Protocol):
    """Mock·GPT 공통 계약."""

    def complete_json(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        ...


@dataclass
class OpenAIChatClient:
    """OpenAI Chat Completions — agents 전용 (이미지·TTS와 분리)."""

    api_key: str | None = None

    def complete_json(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        key = (self.api_key or openai_api_key()).strip()
        if not key:
            raise LLMClientError("OPENAI_API_KEY is not set (AGENT_LLM_MODE=gpt)")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMClientError("openai package is not installed") from exc

        client = OpenAI(api_key=key)
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — 상위에서 meta.error 로 변환
            raise LLMClientError(f"OpenAI chat completion failed: {exc}") from exc

        choice = response.choices[0].message.content if response.choices else None
        if not choice or not str(choice).strip():
            raise LLMClientError("OpenAI returned empty content")

        usage: dict[str, int] = {}
        if response.usage:
            usage = {
                "prompt_tokens": int(response.usage.prompt_tokens or 0),
                "completion_tokens": int(response.usage.completion_tokens or 0),
                "total_tokens": int(response.usage.total_tokens or 0),
            }

        return ChatCompletionResult(
            text=str(choice).strip(),
            model=request.model,
            usage=usage,
            provider="openai",
        )


def get_llm_client(*, mode: str | None = None) -> LLMClient | None:
    """
    GPT 모드일 때만 OpenAI 클라이언트 반환.
    Mock 모드에서는 None (생성기가 Mock 구현을 직접 사용).
    """
    from agents.llm.config import AgentLLMMode, resolve_llm_mode

    if resolve_llm_mode(mode) != AgentLLMMode.GPT:
        return None
    return OpenAIChatClient()
