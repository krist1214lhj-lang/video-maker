from __future__ import annotations

import importlib
import json
import unittest

from agents.llm.openai_client import ChatCompletionResult, LLMClientError
from agents.llm.review_generator import GptReviewGenerator, _parse_review_response

review_mod = importlib.import_module("agents.08_review_agent")
fmt_mod = importlib.import_module("agents.04_format_agent")


class FakeClient:
    def __init__(self, text: str):
        self.text = text

    def complete_json(self, request):
        return ChatCompletionResult(
            text=self.text,
            model=request.model,
            usage={
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
            },
        )


def make_input():
    return review_mod.ReviewAgentInput(
        production_result=review_mod.ProductionResultSnapshot(
            project_slug="demo",
            format=fmt_mod.OutputFormat.SHORTS,
            target_duration_seconds=15,
            actual_duration_seconds=15.0,
            character_consistency_score=0.92,
            has_subtitle_track=True,
            has_voice_track=True,
            subtitle_cue_count=5,
            narration_line_count=5,
            render_success=True,
            aspect_ratio="9:16",
        ),
        duration_seconds=15,
    )


class ReviewGeneratorTest(unittest.TestCase):
    def test_parse_review_response_builds_revision_fields(self):
        payload = {
            "review_passed": False,
            "quality_score": 0.62,
            "strengths": ["캐릭터는 귀엽다"],
            "weaknesses": ["나레이션이 길다"],
            "revision_required": True,
            "retry_target_agent": "05_narration_subtitle",
            "retry_action": "shorten_script",
            "revision_reason": "15초 Shorts 기준으로 나레이션이 길다.",
            "review_summary": "나레이션 축약 후 재검토 권장.",
            "checks": [
                {
                    "check_id": "narration_quality",
                    "passed": False,
                    "message": "대사가 길어 템포가 느립니다.",
                    "severity": "warning",
                }
            ],
        }

        report, target = _parse_review_response(
            json.dumps(payload, ensure_ascii=False),
            input_data=make_input(),
        )

        self.assertFalse(report.passed)
        self.assertEqual(report.score, 0.62)
        self.assertEqual(target, review_mod.RetryTargetAgent.NARRATION_SUBTITLE)
        self.assertEqual(report.retry_action, "shorten_script")
        self.assertIn("15초", report.revision_reason)
        self.assertEqual(report.checks[0].check_id, review_mod.ReviewCheckId.NARRATION_QUALITY)

    def test_gpt_reviewer_wraps_bad_json_as_llm_error(self):
        reviewer = GptReviewGenerator(client=FakeClient('{"review_passed": false}'))

        with self.assertRaises(LLMClientError):
            reviewer.review(make_input())

    def test_run_review_agent_falls_back_to_mock_on_gpt_error(self):
        reviewer = GptReviewGenerator(client=FakeClient('{"review_passed": false}'))

        result = review_mod.run_review_agent(make_input(), reviewer=reviewer)

        self.assertTrue(result.success)
        self.assertEqual(result.meta.get("review_llm_mode"), "mock")
        self.assertEqual(result.meta.get("review_llm_fallback_reason"), "gpt_error")
        self.assertEqual(result.retry_target_agent, review_mod.RetryTargetAgent.NONE)
        self.assertTrue(result.review_report.passed)


if __name__ == "__main__":
    unittest.main()
