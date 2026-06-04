"""OpenAI Chat JSON 호출 (Phase 4A)."""

from __future__ import annotations

import json
import re
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


@dataclass(frozen=True)
class ChatCompletionResult:
    text: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    provider: str = "openai"


class LLMClient(Protocol):
    def complete_json(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        ...


def parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty LLM response")
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fenced:
        data = json.loads(fenced.group(1).strip())
        if isinstance(data, dict):
            return data
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(raw[start : end + 1])
        if isinstance(data, dict):
            return data
    raise ValueError("response is not a JSON object")


@dataclass
class OpenAIChatClient:
    api_key: str | None = None

    def complete_json(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        key = (self.api_key or openai_api_key()).strip()
        if not key:
            raise LLMClientError("OPENAI_API_KEY is not set")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMClientError("openai package is not installed") from exc

        client = OpenAI(api_key=key)
        try:
            response = client.chat.completions.create(
                model=request.model,
                messages=[
                    {"role": "system", "content": request.system_prompt},
                    {"role": "user", "content": request.user_prompt},
                ],
                temperature=request.temperature,
                max_tokens=request.max_output_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001
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
        )
