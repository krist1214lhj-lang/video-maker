"""01·02 에이전트용 프롬프트 빌더."""

from __future__ import annotations

def topic_system_prompt(locale: str) -> str:
    lang = "한국어" if (locale or "ko").startswith("ko") else "the user's locale"
    return f"""You are a short-form video planning assistant.
Return ONLY valid JSON (no markdown).
Write all user-facing strings in {lang}.
Produce exactly 3 subtopics with distinct narrative angles."""


def topic_user_prompt(
    *,
    main_topic: str,
    style: str,
    duration_seconds: int,
    cut_count: int,
) -> str:
    return f"""Main topic: {main_topic}
Visual style: {style or "(unspecified)"}
Target duration (seconds): {duration_seconds}
Cut count: {cut_count}

Return JSON:
{{
  "subtopics": [
    {{
      "id": "subtopic_1",
      "title": "...",
      "angle": "relationship|place|emotion",
      "hook": "one vivid opening hook",
      "one_line_pitch": "one sentence pitch"
    }},
    {{ "id": "subtopic_2", ... }},
    {{ "id": "subtopic_3", ... }}
  ]
}}
Rules:
- ids MUST be subtopic_1, subtopic_2, subtopic_3
- angles MUST be exactly: relationship, place, emotion
- titles must be specific to the main topic, not generic placeholders"""


def story_system_prompt() -> str:
    return """You are a short-form video storywriter.
Return ONLY valid JSON (no markdown).
For each tone (comic, emotional, twist), write a distinct story with cut_flow beats matching cut_count."""


def story_user_prompt(
    *,
    main_topic: str,
    subtopic_title: str,
    subtopic_hook: str,
    subtopic_pitch: str,
    style: str,
    duration_seconds: int,
    cut_count: int,
) -> str:
    return f"""Main topic: {main_topic}
Selected subtopic title: {subtopic_title}
Hook: {subtopic_hook}
Pitch: {subtopic_pitch}
Style: {style or "(unspecified)"}
Duration (seconds): {duration_seconds}
Cut count: {cut_count}

Return JSON:
{{
  "variants": [
    {{
      "tone": "comic",
      "title": "...",
      "summary": "2-3 sentences",
      "story_arc": "short arc label",
      "cut_flow": [
        {{
          "cut": 1,
          "scene": "...",
          "emotion": "...",
          "narration": "...",
          "subtitle": "short on-screen text"
        }}
      ]
    }},
    {{ "tone": "emotional", ... }},
    {{ "tone": "twist", ... }}
  ]
}}
Rules:
- Exactly 3 variants with tones: comic, emotional, twist (all different)
- Each cut_flow length MUST equal {cut_count}
- cut numbers 1..{cut_count}
- Korean copy for narration/subtitle if the topic is Korean"""
