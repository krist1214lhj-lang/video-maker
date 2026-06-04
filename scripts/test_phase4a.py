"""Phase 4A 검증 (로컬)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.dev_api.run_demo import AgentRunDemoRequest, run_demo_pipeline

BODY = {
    "main_topic": "애견카페에서 신나게 노는 뽀식이",
    "style": "애니메이션",
    "duration_seconds": 20,
    "cut_count": 5,
    "project_slug": "",
    "locale": "ko",
    "selected_subtopic_id": "subtopic_1",
    "selected_story_tone": "comic",
    "reference_character_name": "bposik_v2",
    "target_platform": "youtube_shorts",
}


def check(label: str, r: dict) -> bool:
    steps = r.get("steps") or []
    ok = (
        r.get("success") is True
        and "01_topic" in steps
        and any(s.startswith("02_story") for s in steps)
    )
    print(f"[{label}] success={r.get('success')} steps_ok={ok}")
    print(f"  steps={steps[:8]}")
    print(f"  topic_sid={r.get('topic', {}).get('selected_subtopic_id')}")
    return ok


def main() -> int:
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["AGENT_LLM_MODE"] = "mock"
    if not check("mock", run_demo_pipeline(AgentRunDemoRequest(**BODY))):
        return 1

    os.environ["AGENT_LLM_MODE"] = "gpt"
    os.environ.pop("OPENAI_API_KEY", None)
    import importlib

    topic = importlib.import_module("agents.01_topic_agent")
    tr = topic.run_topic_agent(topic.TopicAgentInput(main_topic="테스트", locale="ko"))
    print(
        f"[gpt-no-key-topic] success={tr.success} "
        f"llm_mode={tr.meta.get('llm_mode')} "
        f"fallback={tr.meta.get('llm_fallback_reason')}"
    )
    if tr.meta.get("llm_mode") != "mock" or tr.meta.get("llm_fallback_reason") != "missing_openai_api_key":
        return 1

    if not check("run-demo-gpt-no-key", run_demo_pipeline(AgentRunDemoRequest(**BODY))):
        return 1

    key = os.getenv("OPENAI_API_KEY", "").strip()
    if key:
        os.environ["AGENT_LLM_MODE"] = "gpt"
        if not check("gpt-with-key", run_demo_pipeline(AgentRunDemoRequest(**BODY))):
            return 1
        print("[gpt-with-key] skipped=0")
    else:
        print("[gpt-with-key] skipped (no OPENAI_API_KEY)")

    print("ALL_CHECKS_PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
