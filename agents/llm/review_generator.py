"""08_review_agent — Mock/GPT pipeline quality reviewer."""

from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from agents.llm.config import (
    AgentLLMMode,
    ResolvedLLMMode,
    resolve_effective_llm_mode,
    resolve_review_model,
)
from agents.llm.openai_client import (
    ChatCompletionRequest,
    LLMClient,
    LLMClientError,
    OpenAIChatClient,
    parse_json_object,
)
from agents.llm.pipeline_meta import estimate_llm_cost_usd

_review_mod = importlib.import_module("agents.08_review_agent")

GptInput = _review_mod.ReviewAgentInput
MockReviewer = _review_mod.MockReviewer
RetryTargetAgent = _review_mod.RetryTargetAgent
ReviewCheck = _review_mod.ReviewCheck
ReviewCheckId = _review_mod.ReviewCheckId
ReviewReport = _review_mod.ReviewReport
Reviewer = _review_mod.Reviewer


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "value"):
        return getattr(value, "value")
    return str(value)


def _string(value: Any, fallback: str = "") -> str:
    text = str(value if value is not None else fallback).strip()
    return text or fallback


def _string_list(value: Any, *, fallback: list[str] | None = None) -> list[str]:
    if not isinstance(value, list):
        return list(fallback or [])
    items = [_string(item)[:160] for item in value if _string(item)]
    return items or list(fallback or [])


def _score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return round(min(max(score, 0.0), 1.0), 3)


def _retry_target(value: Any) -> RetryTargetAgent:
    raw = _string(value)
    for target in RetryTargetAgent:
        if raw == target.value or raw == target.name.lower() or raw == target.name:
            return target
    aliases = {
        "01": RetryTargetAgent.TOPIC,
        "topic": RetryTargetAgent.TOPIC,
        "02": RetryTargetAgent.STORY,
        "story": RetryTargetAgent.STORY,
        "03": RetryTargetAgent.CHARACTER,
        "character": RetryTargetAgent.CHARACTER,
        "05": RetryTargetAgent.NARRATION_SUBTITLE,
        "narration": RetryTargetAgent.NARRATION_SUBTITLE,
        "subtitle": RetryTargetAgent.NARRATION_SUBTITLE,
        "06": RetryTargetAgent.MUSIC,
        "music": RetryTargetAgent.MUSIC,
        "07": RetryTargetAgent.PRODUCTION,
        "production": RetryTargetAgent.PRODUCTION,
        "04": RetryTargetAgent.FORMAT,
        "format": RetryTargetAgent.FORMAT,
    }
    return aliases.get(raw.lower(), RetryTargetAgent.NONE)


def _retry_action(value: Any, target: RetryTargetAgent, passed: bool) -> str:
    action = _string(value)[:80]
    if passed:
        return ""
    if action:
        return action
    defaults = {
        RetryTargetAgent.TOPIC: "refocus_topic",
        RetryTargetAgent.STORY: "increase_conflict",
        RetryTargetAgent.CHARACTER: "strengthen_identity",
        RetryTargetAgent.NARRATION_SUBTITLE: "shorten_script",
        RetryTargetAgent.MUSIC: "adjust_mood",
        RetryTargetAgent.FORMAT: "fix_format_plan",
        RetryTargetAgent.PRODUCTION: "fix_timing",
    }
    return defaults.get(target, "review_pipeline")


def _check_id(value: Any) -> ReviewCheckId:
    raw = _string(value)
    for check_id in ReviewCheckId:
        if raw == check_id.value or raw == check_id.name.lower() or raw == check_id.name:
            return check_id
    return ReviewCheckId.SHORTS_FIT


def _review_system_prompt() -> str:
    return """You are a Korean short-form video pipeline quality reviewer.
Return ONLY valid JSON.
You are not just grading. You must give a clear retry target and retry action for an automated revision loop."""


def _review_user_prompt(input_data: GptInput) -> str:
    pr = input_data.production_result
    return f"""Pipeline snapshot:
topic_result: {_jsonable(input_data.topic_result)}
story_result: {_jsonable(input_data.story_result)}
character_profile: {_jsonable(input_data.character_profile)}
narration_script: {_jsonable(input_data.narration_script)}
subtitle_script: {_jsonable(input_data.subtitle_script)}
music_result: {_jsonable(input_data.music_result)}
format_plan: {_jsonable(input_data.format_plan)}
duration_seconds: {input_data.duration_seconds or pr.target_duration_seconds}
production_result: {_jsonable(pr)}

JSON:
{{
  "review_passed": true,
  "quality_score": 0.88,
  "strengths": ["topic, story, and Bposik tone align"],
  "weaknesses": ["subtitle line 2 is slightly long"],
  "revision_required": false,
  "retry_target_agent": "",
  "retry_action": "",
  "revision_reason": "",
  "review_summary": "15초 Shorts로 사용 가능한 구성입니다.",
  "checks": [
    {{
      "check_id": "shorts_fit",
      "passed": true,
      "message": "초반 hook과 길이가 적합합니다.",
      "severity": "info"
    }}
  ]
}}
Rules:
- retry_target_agent must be one of "", "01_topic", "02_story", "03_character", "04_format", "05_narration_subtitle", "06_music", "07_production".
- If review_passed is false, retry_target_agent, retry_action, and revision_reason must be non-empty.
- Prefer specific retry_action values like shorten_script, increase_conflict, strengthen_identity, adjust_mood, fix_timing.
- Evaluate: topic consistency, story flow, character identity, narration quality, subtitle readability, music fit, Shorts fit, 15-second timing.
- For Bposik/뽀식이, check 9-year-old poodle-mix identity, slightly cynical tone, cute energy, and optional senior-dog perspective.
- Be strict enough to guide revision, but do not fail a usable 15-second Shorts plan for minor polish issues."""


def _parse_review_response(text: str, *, input_data: GptInput) -> tuple[ReviewReport, RetryTargetAgent]:
    payload = parse_json_object(text)
    if "review_passed" not in payload:
        raise ValueError("review_passed is required")
    if "quality_score" not in payload:
        raise ValueError("quality_score is required")
    if "review_summary" not in payload:
        raise ValueError("review_summary is required")
    passed = bool(payload.get("review_passed"))
    quality_score = _score(payload.get("quality_score"))
    target = _retry_target(payload.get("retry_target_agent"))
    if passed:
        target = RetryTargetAgent.NONE
    elif target == RetryTargetAgent.NONE:
        target = RetryTargetAgent.PRODUCTION

    action = _retry_action(payload.get("retry_action"), target, passed)
    reason = _string(payload.get("revision_reason"))[:240]
    if not passed and not reason:
        reason = "품질 기준을 통과하지 못해 재시도가 필요합니다."

    raw_checks = payload.get("checks")
    checks: list[ReviewCheck] = []
    if isinstance(raw_checks, list):
        for item in raw_checks[:12]:
            if not isinstance(item, dict):
                continue
            severity = _string(item.get("severity"), "info").lower()
            if severity not in {"info", "warning", "error"}:
                severity = "warning"
            checks.append(
                ReviewCheck(
                    check_id=_check_id(item.get("check_id")),
                    passed=bool(item.get("passed")),
                    message=_string(item.get("message"), "review check")[:220],
                    severity=severity,
                )
            )
    if not checks:
        checks.append(
            ReviewCheck(
                check_id=ReviewCheckId.SHORTS_FIT,
                passed=passed,
                message=_string(payload.get("review_summary"), "review summary")[:220],
                severity="info" if passed else "error",
            )
        )

    summary = _string(
        payload.get("review_summary"),
        "검증 통과" if passed else f"재시도 권장: {target.value}",
    )[:240]
    report = ReviewReport(
        passed=passed,
        score=quality_score,
        checks=checks,
        summary_ko=summary,
        strengths=_string_list(payload.get("strengths")),
        weaknesses=_string_list(payload.get("weaknesses")),
        revision_required=bool(payload.get("revision_required", not passed)),
        retry_action=action,
        revision_reason=reason,
    )
    return report, target


@dataclass
class GptReviewGenerator:
    client: LLMClient
    model: str | None = None
    temperature: float = 0.2

    def review(self, input_data: GptInput) -> tuple[ReviewReport, RetryTargetAgent]:
        model = (self.model or resolve_review_model()).strip()
        result = self.client.complete_json(
            ChatCompletionRequest(
                system_prompt=_review_system_prompt(),
                user_prompt=_review_user_prompt(input_data),
                model=model,
                temperature=self.temperature,
                max_output_tokens=2048,
            )
        )
        try:
            report, target = _parse_review_response(result.text, input_data=input_data)
        except ValueError as exc:
            raise LLMClientError(f"review JSON parse failed: {exc}") from exc
        cost = estimate_llm_cost_usd(result.usage, model=result.model)
        self._last_meta = {
            "llm_model": result.model,
            "llm_usage": result.usage,
            "llm_provider": result.provider,
            "llm_est_cost_usd": round(cost, 6) if cost is not None else None,
        }
        return report, target

    @property
    def last_meta(self) -> dict[str, Any]:
        return getattr(self, "_last_meta", {})


def create_review_generator(mode: str | None = None) -> tuple[Reviewer, ResolvedLLMMode]:
    resolved = resolve_effective_llm_mode(mode)
    if resolved.mode == AgentLLMMode.MOCK:
        return MockReviewer(), resolved
    return GptReviewGenerator(client=OpenAIChatClient()), resolved


def reviewer_mode_label(reviewer: Any, *, resolved: ResolvedLLMMode) -> str:
    name = type(reviewer).__name__
    if isinstance(reviewer, GptReviewGenerator):
        model = (getattr(reviewer, "last_meta", {}) or {}).get("llm_model") or ""
        label = f"{name}({model})" if model else name
    else:
        label = name
    if resolved.fallback_reason:
        return f"{label}[fallback:{resolved.fallback_reason}]"
    return label
