"""
Agent 08 — review_agent (Phase 3A-3: design only)

역할: production_result 검증 → review_report, retry_target_agent (Mock).

검증 항목:
  - 캐릭터 일관성
  - 자막 존재 여부
  - 음성 존재 여부
  - 길이 검증
  - 출력 형식 검증

연결 금지: main.py / API / UI
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

_format_mod = importlib.import_module("agents.04_format_agent")
OutputFormat = _format_mod.OutputFormat


class RetryTargetAgent(str, Enum):
    NONE = ""
    TOPIC = "01_topic"
    STORY = "02_story"
    CHARACTER = "03_character"
    NARRATION_SUBTITLE = "05_narration_subtitle"
    MUSIC = "06_music"
    PRODUCTION = "07_production"
    FORMAT = "04_format"


class ReviewCheckId(str, Enum):
    TOPIC_CONSISTENCY = "topic_consistency"
    STORY_FLOW = "story_flow"
    CHARACTER_CONSISTENCY = "character_consistency"
    NARRATION_QUALITY = "narration_quality"
    SUBTITLE_PRESENT = "subtitle_present"
    SUBTITLE_FIT = "subtitle_fit"
    VOICE_PRESENT = "voice_present"
    MUSIC_FIT = "music_fit"
    SHORTS_FIT = "shorts_fit"
    BPOSIK_VOICE = "bposik_voice"
    DURATION = "duration"
    OUTPUT_FORMAT = "output_format"


@dataclass
class ReviewCheck:
    check_id: ReviewCheckId
    passed: bool
    message: str
    severity: str  # info | warning | error


@dataclass
class ProductionResultSnapshot:
    """08 입력: 07 이후(또는 Mock) 산출물 스냅샷."""

    project_slug: str
    format: OutputFormat
    target_duration_seconds: int
    actual_duration_seconds: float
    character_consistency_score: float
    has_subtitle_track: bool
    has_voice_track: bool
    subtitle_cue_count: int
    narration_line_count: int
    render_success: bool
    aspect_ratio: str = ""


@dataclass
class ReviewReport:
    passed: bool
    score: float
    checks: list[ReviewCheck]
    summary_ko: str
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    revision_required: bool = False
    retry_action: str = ""
    revision_reason: str = ""


@dataclass
class ReviewAgentInput:
    production_result: ProductionResultSnapshot
    topic_result: Any | None = None
    story_result: Any | None = None
    character_profile: Any | None = None
    narration_script: Any | None = None
    subtitle_script: Any | None = None
    music_result: Any | None = None
    format_plan: Any | None = None
    duration_seconds: int | None = None


@dataclass
class ReviewAgentResult:
    success: bool
    review_report: ReviewReport | None
    retry_target_agent: RetryTargetAgent
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meta: dict[str, Any] = field(default_factory=dict)


class Reviewer(Protocol):
    def review(self, input_data: ReviewAgentInput) -> tuple[ReviewReport, RetryTargetAgent]:
        ...


@dataclass
class MockReviewer:
    def review(self, input_data: ReviewAgentInput) -> tuple[ReviewReport, RetryTargetAgent]:
        pr = input_data.production_result
        checks: list[ReviewCheck] = []
        retry = RetryTargetAgent.NONE

        char_ok = pr.character_consistency_score >= 0.7
        checks.append(
            ReviewCheck(
                check_id=ReviewCheckId.CHARACTER_CONSISTENCY,
                passed=char_ok,
                message=(
                    f"character consistency {pr.character_consistency_score:.2f}"
                    + (" OK" if char_ok else " — identity drift risk")
                ),
                severity="error" if not char_ok else "info",
            )
        )
        if not char_ok:
            retry = RetryTargetAgent.CHARACTER

        sub_ok = pr.has_subtitle_track and pr.subtitle_cue_count > 0
        checks.append(
            ReviewCheck(
                check_id=ReviewCheckId.SUBTITLE_PRESENT,
                passed=sub_ok,
                message=f"subtitle cues={pr.subtitle_cue_count}",
                severity="error" if not sub_ok else "info",
            )
        )
        if not sub_ok and retry == RetryTargetAgent.NONE:
            retry = RetryTargetAgent.NARRATION_SUBTITLE

        voice_ok = pr.has_voice_track and pr.narration_line_count > 0
        checks.append(
            ReviewCheck(
                check_id=ReviewCheckId.VOICE_PRESENT,
                passed=voice_ok,
                message=f"narration lines={pr.narration_line_count}",
                severity="error" if not voice_ok else "info",
            )
        )
        if not voice_ok and retry == RetryTargetAgent.NONE:
            retry = RetryTargetAgent.NARRATION_SUBTITLE

        dur_delta = abs(pr.actual_duration_seconds - pr.target_duration_seconds)
        dur_ok = dur_delta <= max(5.0, pr.target_duration_seconds * 0.25)
        checks.append(
            ReviewCheck(
                check_id=ReviewCheckId.DURATION,
                passed=dur_ok,
                message=(
                    f"target={pr.target_duration_seconds}s actual={pr.actual_duration_seconds:.1f}s "
                    f"delta={dur_delta:.1f}s"
                ),
                severity="warning" if not dur_ok else "info",
            )
        )
        if not dur_ok and retry == RetryTargetAgent.NONE:
            retry = RetryTargetAgent.PRODUCTION

        fmt_ok = bool(pr.format) and pr.render_success
        checks.append(
            ReviewCheck(
                check_id=ReviewCheckId.OUTPUT_FORMAT,
                passed=fmt_ok,
                message=f"format={pr.format.value} render_success={pr.render_success}",
                severity="error" if not fmt_ok else "info",
            )
        )
        if not fmt_ok and retry == RetryTargetAgent.NONE:
            retry = RetryTargetAgent.PRODUCTION

        passed = all(c.passed for c in checks)
        score = sum(1 for c in checks if c.passed) / len(checks) if checks else 0.0
        summary = "검증 통과" if passed else f"재시도 권장: {retry.value or 'none'}"
        action = ""
        reason = ""
        if not passed:
            if retry == RetryTargetAgent.CHARACTER:
                action = "strengthen_identity"
                reason = "캐릭터 일관성 점수가 기준보다 낮습니다."
            elif retry == RetryTargetAgent.NARRATION_SUBTITLE:
                action = "repair_script_tracks"
                reason = "나레이션 또는 자막 트랙이 부족합니다."
            elif retry == RetryTargetAgent.PRODUCTION:
                action = "fix_render_or_timing"
                reason = "렌더 또는 길이 조건을 다시 맞춰야 합니다."
            else:
                action = "review_pipeline"
                reason = "검증 실패 항목을 재검토해야 합니다."
        strengths = ["기본 산출물 구조가 유효합니다."] if passed else []
        weaknesses = [reason] if reason else []
        return (
            ReviewReport(
                passed=passed,
                score=score,
                checks=checks,
                summary_ko=summary,
                strengths=strengths,
                weaknesses=weaknesses,
                revision_required=not passed,
                retry_action=action,
                revision_reason=reason,
            ),
            retry,
        )


def run_review_agent(
    input_data: ReviewAgentInput,
    *,
    reviewer: Reviewer | None = None,
    llm_mode: str | None = None,
) -> ReviewAgentResult:
    from agents.llm.config import AgentLLMMode, ResolvedLLMMode
    from agents.llm.openai_client import LLMClientError
    from agents.llm.review_generator import create_review_generator, reviewer_mode_label

    resolved = ResolvedLLMMode(mode=AgentLLMMode.MOCK, requested=AgentLLMMode.MOCK)
    gen = reviewer
    if gen is None:
        gen, resolved = create_review_generator(llm_mode)

    fallback_error = None
    try:
        report, retry = gen.review(input_data)
    except LLMClientError as exc:
        fallback_error = str(exc)
        gen = MockReviewer()
        report, retry = gen.review(input_data)
        resolved = ResolvedLLMMode(
            mode=AgentLLMMode.MOCK,
            requested=resolved.requested,
            fallback_reason="gpt_error",
        )

    final_retry = retry if not report.passed else RetryTargetAgent.NONE
    meta: dict[str, Any] = {
        "reviewer": reviewer_mode_label(gen, resolved=resolved),
        "llm_mode": resolved.mode.value,
        "llm_mode_requested": resolved.requested.value,
        "llm_fallback_reason": resolved.fallback_reason,
        "review_llm_mode": resolved.mode.value,
        "review_llm_mode_requested": resolved.requested.value,
        "review_llm_fallback_reason": resolved.fallback_reason,
        "review_passed": report.passed,
        "quality_score": report.score,
        "retry_target_agent": final_retry.value,
        "retry_action": report.retry_action if final_retry != RetryTargetAgent.NONE else "",
        "revision_reason": report.revision_reason if final_retry != RetryTargetAgent.NONE else "",
        "review_summary": report.summary_ko,
    }
    if fallback_error:
        meta["llm_error"] = fallback_error
    if hasattr(gen, "last_meta"):
        meta.update(getattr(gen, "last_meta") or {})
    return ReviewAgentResult(
        success=True,
        review_report=report,
        retry_target_agent=final_retry,
        meta=meta,
    )


def example_review_result() -> dict[str, Any]:
    r = run_review_agent(
        ReviewAgentInput(
            production_result=ProductionResultSnapshot(
                project_slug="demo",
                format=OutputFormat.SHORTS,
                target_duration_seconds=20,
                actual_duration_seconds=19.5,
                character_consistency_score=0.92,
                has_subtitle_track=True,
                has_voice_track=True,
                subtitle_cue_count=5,
                narration_line_count=5,
                render_success=True,
                aspect_ratio="9:16",
            )
        )
    )
    assert r.review_report
    return {
        "success": r.success,
        "passed": r.review_report.passed,
        "score": r.review_report.score,
        "retry_target_agent": r.retry_target_agent.value,
    }
