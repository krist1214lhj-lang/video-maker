#!/usr/bin/env python3
"""Phase 4A — 01·02 GPT 생성 스모크 테스트 (키·토큰만 출력, 키 값 미출력)."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from agents.llm.config import (
    resolve_effective_llm_mode,
    resolve_story_model,
    resolve_topic_model,
)

_topic = importlib.import_module("agents.01_topic_agent")
_story = importlib.import_module("agents.02_story_agent")

TopicAgentInput = _topic.TopicAgentInput
run_topic_agent = _topic.run_topic_agent
run_story_agent = _story.run_story_agent
StoryAgentInput = _story.StoryAgentInput


def _usage_cost_usd(usage: dict, *, model: str) -> float:
    """gpt-4o-mini 공개 단가 근사 (USD)."""
    inp = int(usage.get("prompt_tokens") or 0)
    out = int(usage.get("completion_tokens") or 0)
    rates = {
        "gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
        "gpt-4o": (2.50 / 1_000_000, 10.0 / 1_000_000),
    }
    rin, rout = rates.get(model, rates["gpt-4o-mini"])
    return inp * rin + out * rout


def main() -> int:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    resolved = resolve_effective_llm_mode("gpt")
    print("=== env ===")
    print(f".env exists: {(ROOT / '.env').is_file()}")
    print(f"OPENAI_API_KEY set: {bool(key)} (len={len(key)})")
    print(f"AGENT_LLM_MODE in .env: {bool(os.getenv('AGENT_LLM_MODE'))}")
    print(f"AGENT_LLM_MODE effective request=gpt -> mode={resolved.mode.value}")
    if resolved.fallback_reason:
        print(f"fallback_reason: {resolved.fallback_reason}")
        return 1

    topic_model = resolve_topic_model()
    story_model = resolve_story_model()
    print(f"topic_model: {topic_model}")
    print(f"story_model: {story_model}")

    inp = TopicAgentInput(
        main_topic="애견카페에서 신나게 노는 뽀식이",
        style="애니메이션",
        duration_seconds=15,
        cut_count=5,
        locale="ko",
    )
    print("\n=== 01_topic_agent (gpt) ===")
    tr = run_topic_agent(inp, llm_mode="gpt")
    print(f"success: {tr.success}")
    print(f"generator: {tr.meta.get('generator')}")
    print(f"llm_mode: {tr.meta.get('llm_mode')}")
    usage = tr.meta.get("llm_usage") or {}
    print(f"llm_usage: {usage}")
    if usage:
        print(f"est_cost_usd: ${_usage_cost_usd(usage, model=topic_model):.6f}")
    if not tr.success or len(tr.subtopics) != 3:
        print(f"error: {tr.meta.get('error')}")
        return 1
    for s in tr.subtopics:
        print(f"  - {s.id}: {s.title[:50]}")

    st = tr.subtopics[0]
    sinp = StoryAgentInput(
        main_topic=tr.main_topic,
        subtopic=st,
        style=tr.style,
        duration_seconds=tr.duration_seconds,
        cut_count=tr.cut_count,
        selected_subtopic_id=st.id,
    )
    print("\n=== 02_story_agent (gpt) ===")
    sr = run_story_agent(sinp, llm_mode="gpt")
    print(f"success: {sr.success}")
    print(f"generator: {sr.meta.get('generator')}")
    print(f"llm_mode: {sr.meta.get('llm_mode')}")
    usage2 = sr.meta.get("llm_usage") or {}
    print(f"llm_usage: {usage2}")
    if usage2:
        print(f"est_cost_usd: ${_usage_cost_usd(usage2, model=story_model):.6f}")
    if not sr.success or len(sr.variants) != 3:
        print(f"error: {sr.meta.get('error')}")
        return 1
    for v in sr.variants:
        print(f"  - {v.tone.value}: {v.title[:40]} ({len(v.cut_flow)} cuts)")

    total = 0.0
    if usage:
        total += _usage_cost_usd(usage, model=topic_model)
    if usage2:
        total += _usage_cost_usd(usage2, model=story_model)
    print(f"\n=== combined est (this run) ===")
    print(f"total_est_usd: ${total:.6f}")
    print("GPT_TESTS_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
