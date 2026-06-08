from __future__ import annotations

import importlib
import json
import unittest

from agents.llm.narration_subtitle_generator import (
    GptNarrationSubtitleBuilder,
    _parse_narration_response,
)
from agents.llm.openai_client import ChatCompletionResult, LLMClientError

narr_mod = importlib.import_module("agents.05_narration_subtitle_agent")
story_mod = importlib.import_module("agents.02_story_agent")
fmt_mod = importlib.import_module("agents.04_format_agent")
char_mod = importlib.import_module("agents.03_character_agent")


class FakeClient:
    def __init__(self, text: str):
        self.text = text

    def complete_json(self, request):
        return ChatCompletionResult(
            text=self.text,
            model=request.model,
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        )


def make_input():
    return narr_mod.NarrationSubtitleAgentInput(
        story=char_mod.StoryContext(
            main_topic="애견카페에서 신나게 노는 뽀식이",
            subtopic_id="subtopic_1",
            subtopic_title="첫 만남의 어색함",
            tone=story_mod.StoryTone.COMIC,
            title="코믹형",
            summary="뽀식이가 애견카페에서 친구를 만난다.",
            story_arc="도입-당황-수습-반전-마무리",
            cut_count=3,
        ),
        tone=story_mod.StoryTone.COMIC,
        format=fmt_mod.FormatPlan(
            format=fmt_mod.OutputFormat.SHORTS,
            label_ko="숏츠",
            aspect_ratio="9:16",
            target_duration_seconds=15,
            recommended_cut_count=3,
            pacing="fast",
            platform_notes="Target platform: youtube_shorts.",
            production_hints=[],
        ),
        character_profile=char_mod.CharacterProfile(
            character_name="bposik_v2",
            display_name="Bposik",
            species_or_type="9살 푸들믹스",
            character_summary="살짝 시니컬하고 천방지축인 노견.",
            reference_dir="reference_characters/bposik_v2",
            memory_file="reference_characters/bposik_v2/character_memory.json",
            reference_images=[],
            public_reference_url="",
            identity_lock_strength="high",
        ),
        target_platform="youtube_shorts",
        duration_seconds=15,
    )


class NarrationSubtitleGeneratorTest(unittest.TestCase):
    def test_parse_narration_response_builds_scripts_and_voice_profile(self):
        inp = make_input()
        payload = {
            "narration_script": {
                "locale": "ko",
                "lines": [
                    {"cut": 1, "text": "아홉 살이면, 카페쯤은 알지.", "emotion": "코믹"},
                    {"cut": 2, "text": "근데 저 친구, 꽤 빠르네.", "emotion": "코믹"},
                    {"cut": 3, "text": "좋아, 오늘은 내가 봐준다.", "emotion": "코믹"},
                ],
                "full_text": "아홉 살이면, 카페쯤은 알지.\n근데 저 친구, 꽤 빠르네.\n좋아, 오늘은 내가 봐준다.",
            },
            "subtitle_script": {
                "locale": "ko",
                "cues": [
                    {"cut": 1, "text": "카페쯤은 알지", "start_sec": 0.0, "end_sec": 4.0},
                    {"cut": 2, "text": "저 친구 빠르네", "start_sec": 5.0, "end_sec": 9.0},
                    {"cut": 3, "text": "오늘은 봐준다", "start_sec": 10.0, "end_sec": 14.0},
                ],
                "srt_preview": "",
            },
            "voice_style": {
                "voice_id": "ko_bposik_playful_dry",
                "label_ko": "살짝 시니컬한 뽀식이 목소리",
                "pace": "fast",
                "pitch": "mid",
                "force_short_dialogue": True,
            },
            "character_voice_profile": {
                "narrator_perspective": "first_person",
                "speaking_style": "playful_dry",
                "sarcasm_level": "medium",
                "energy_level": "chaotic",
                "age_tone": "senior",
                "sentence_length": "very_short",
            },
            "estimated_voice_seconds": 11.5,
        }

        narration, subtitle, voice, profile, estimated = _parse_narration_response(
            json.dumps(payload, ensure_ascii=False),
            inp=inp,
        )

        self.assertEqual(len(narration.lines), 3)
        self.assertEqual(len(subtitle.cues), 3)
        self.assertEqual(voice.voice_id, "ko_bposik_playful_dry")
        self.assertEqual(profile.narrator_perspective, "first_person")
        self.assertEqual(profile.age_tone, "senior")
        self.assertEqual(estimated, 11.5)

    def test_gpt_builder_wraps_bad_json_as_llm_error(self):
        builder = GptNarrationSubtitleBuilder(client=FakeClient('{"bad": true}'))

        with self.assertRaises(LLMClientError):
            builder.build(make_input())

    def test_run_agent_falls_back_to_mock_on_gpt_error(self):
        builder = GptNarrationSubtitleBuilder(client=FakeClient('{"bad": true}'))

        result = narr_mod.run_narration_subtitle_agent(make_input(), builder=builder)

        self.assertTrue(result.success)
        self.assertEqual(result.meta.get("narration_llm_mode"), "mock")
        self.assertEqual(result.meta.get("subtitle_llm_mode"), "mock")
        self.assertEqual(result.meta.get("narration_llm_fallback_reason"), "gpt_error")
        self.assertIn("character_voice_profile", result.meta)


if __name__ == "__main__":
    unittest.main()
