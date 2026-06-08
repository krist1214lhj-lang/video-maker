from __future__ import annotations

import importlib
import os
import unittest

director_mod = importlib.import_module("agents.09_director_agent")
review_mod = importlib.import_module("agents.08_review_agent")


def _review_result(
    *,
    passed: bool,
    target,
    action: str = "",
    reason: str = "",
    meta_target: str | None = None,
):
    report = review_mod.ReviewReport(
        passed=passed,
        score=1.0 if passed else 0.5,
        checks=[
            review_mod.ReviewCheck(
                check_id=review_mod.ReviewCheckId.SHORTS_FIT,
                passed=passed,
                message="test review",
                severity="info" if passed else "error",
            )
        ],
        summary_ko="검증 통과" if passed else "재시도 필요",
        revision_required=not passed,
        retry_action=action,
        revision_reason=reason,
    )
    meta = {
        "retry_target_agent": meta_target if meta_target is not None else target.value,
        "retry_action": action,
        "revision_reason": reason,
    }
    return review_mod.ReviewAgentResult(
        success=True,
        review_report=report,
        retry_target_agent=target,
        meta=meta,
    )


def _director_input(**kwargs):
    base = {
        "main_topic": "애견카페에서 신나게 노는 뽀식이",
        "style": "애니메이션",
        "duration_seconds": 15,
        "cut_count": 5,
        "selected_subtopic_id": "subtopic_1",
    }
    base.update(kwargs)
    return director_mod.PlanningDirectorInput(**base)


class RevisionLoopTest(unittest.TestCase):
    def setUp(self):
        os.environ["AGENT_LLM_MODE"] = "mock"
        self._original_review = director_mod._review_mod.run_review_agent

    def tearDown(self):
        director_mod._review_mod.run_review_agent = self._original_review

    def test_default_max_revision_retries_keeps_success_with_empty_history(self):
        result = director_mod.run_full_pipeline(_director_input())

        self.assertTrue(result.success)
        self.assertEqual(result.meta.get("revision_attempts"), 0)
        self.assertEqual(result.meta.get("revision_history"), [])

    def test_failed_review_with_zero_retries_records_history(self):
        director_mod._review_mod.run_review_agent = lambda input_data: _review_result(
            passed=False,
            target=review_mod.RetryTargetAgent.NARRATION_SUBTITLE,
            action="shorten_script",
            reason="나레이션이 길다.",
        )

        result = director_mod.run_full_pipeline(_director_input())

        self.assertFalse(result.success)
        history = result.meta.get("revision_history")
        self.assertEqual(len(history), 1)
        self.assertFalse(history[0]["executed"])
        self.assertEqual(history[0]["stop_reason"], "max_revision_retries_exceeded")
        self.assertEqual(history[0]["retry_target_agent"], "05_narration_subtitle")

    def test_unknown_retry_target_does_not_retry(self):
        director_mod._review_mod.run_review_agent = lambda input_data: _review_result(
            passed=False,
            target=review_mod.RetryTargetAgent.NONE,
            action="unknown_action",
            reason="알 수 없는 대상입니다.",
            meta_target="99_unknown",
        )

        result = director_mod.run_full_pipeline(_director_input(max_revision_retries=1))

        self.assertFalse(result.success)
        history = result.meta.get("revision_history")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["retry_target_agent"], "99_unknown")
        self.assertFalse(history[0]["executed"])
        self.assertEqual(history[0]["stop_reason"], "unsupported_retry_target")

    def test_max_retry_exceeded_after_supported_rerun(self):
        calls = {"count": 0}

        def review_once_then_fail(input_data):
            calls["count"] += 1
            return _review_result(
                passed=False,
                target=review_mod.RetryTargetAgent.MUSIC,
                action="adjust_mood",
                reason="음악 분위기가 맞지 않습니다.",
            )

        director_mod._review_mod.run_review_agent = review_once_then_fail

        result = director_mod.run_full_pipeline(_director_input(max_revision_retries=1))

        self.assertFalse(result.success)
        self.assertEqual(calls["count"], 2)
        history = result.meta.get("revision_history")
        self.assertEqual(len(history), 2)
        self.assertTrue(history[0]["executed"])
        self.assertEqual(history[0]["stop_reason"], "review_failed")
        self.assertFalse(history[1]["executed"])
        self.assertEqual(history[1]["stop_reason"], "max_revision_retries_exceeded")

    def test_supported_music_retry_can_recover(self):
        calls = {"count": 0}

        def fail_then_pass(input_data):
            calls["count"] += 1
            if calls["count"] == 1:
                return _review_result(
                    passed=False,
                    target=review_mod.RetryTargetAgent.MUSIC,
                    action="adjust_mood",
                    reason="음악 분위기가 맞지 않습니다.",
                )
            return _review_result(passed=True, target=review_mod.RetryTargetAgent.NONE)

        director_mod._review_mod.run_review_agent = fail_then_pass

        result = director_mod.run_full_pipeline(_director_input(max_revision_retries=1))

        self.assertTrue(result.success)
        self.assertEqual(calls["count"], 2)
        history = result.meta.get("revision_history")
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]["executed"])
        self.assertEqual(history[0]["stop_reason"], "review_passed")


if __name__ == "__main__":
    unittest.main()
