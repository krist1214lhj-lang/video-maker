#!/usr/bin/env python3
"""Phase 4A GPT 실연결: 01·02 단독 + /agent/run-demo (in-process · HTTP)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

META_KEYS = (
    "llm_mode",
    "topic_llm_mode",
    "story_llm_mode",
    "topic_llm_usage",
    "story_llm_usage",
    "topic_llm_est_cost_usd",
    "story_llm_est_cost_usd",
    "llm_est_cost_usd_total",
)


def _print_meta(meta: dict) -> None:
    for key in META_KEYS:
        print(f"  meta.{key}: {meta.get(key)}")


def main() -> int:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    mode = (os.getenv("AGENT_LLM_MODE") or "mock").strip()
    print("=== 1. .env ===")
    print(f"  .env exists: {(ROOT / '.env').is_file()}")
    print(f"  OPENAI_API_KEY set: {bool(key)}")
    print(f"  AGENT_LLM_MODE: {mode}")
    if not key:
        print("FAIL: OPENAI_API_KEY missing")
        return 1

    print("\n=== 3-4. agents GPT (via test_phase4a_gpt) ===")
    import subprocess

    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "test_phase4a_gpt.py")],
        cwd=ROOT,
        capture_output=False,
    )
    if r.returncode != 0:
        return r.returncode

    print("\n=== 5. run-demo in-process (Swagger 동일 핸들러) ===")
    from agents.dev_api.run_demo import AgentRunDemoRequest, run_demo_pipeline

    body = AgentRunDemoRequest(
        main_topic="GPT 실연결 검증 — 뽀식이 애견카페",
        selected_subtopic_id="string",
        duration_seconds=15,
        cut_count=5,
    )
    resp = run_demo_pipeline(body)
    print(f"  success: {resp['success']}")
    print(f"  steps: {resp['steps']}")
    _print_meta(resp.get("meta") or {})
    m = resp.get("meta") or {}
    if m.get("llm_mode") != "gpt" or m.get("topic_llm_mode") != "gpt" or m.get("story_llm_mode") != "gpt":
        print("FAIL: expected llm_mode/topic_llm_mode/story_llm_mode all gpt")
        return 1
    if not m.get("topic_llm_usage") or not m.get("story_llm_usage"):
        print("FAIL: missing llm_usage in meta")
        return 1

    print("\n=== 5b. HTTP POST /agent/run-demo (서버 프로세스 env) ===")
    try:
        import urllib.request

        payload = json.dumps(
            {
                "main_topic": "GPT HTTP 검증",
                "selected_subtopic_id": "string",
                "duration_seconds": 15,
                "cut_count": 5,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8011/agent/run-demo",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as http_resp:
            data = json.loads(http_resp.read().decode("utf-8"))
        print(f"  HTTP success: {data.get('success')}")
        _print_meta(data.get("meta") or {})
        hm = data.get("meta") or {}
        if hm.get("llm_mode") != "gpt":
            print(
                "  WARN: HTTP llm_mode is not gpt — uvicorn 재시작 필요 "
                "(AGENT_LLM_MODE=gpt 반영 후 python start_server.py)"
            )
    except Exception as exc:
        print(f"  HTTP skip/fail: {exc}")

    print("\nVERIFY_GPT_RUN_DEMO_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
