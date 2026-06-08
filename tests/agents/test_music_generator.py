from __future__ import annotations

import importlib
import json
import unittest

from agents.llm.music_generator import GptMusicPlanner, _parse_music_response
from agents.llm.openai_client import ChatCompletionResult, LLMClientError

music_mod = importlib.import_module("agents.06_music_agent")
story_mod = importlib.import_module("agents.02_story_agent")
char_mod = importlib.import_module("agents.03_character_agent")


class FakeClient:
    def __init__(self, text: str):
        self.text = text

    def complete_json(self, request):
        return ChatCompletionResult(
            text=self.text,
            model=request.model,
            usage={
                "prompt_tokens": 90,
                "completion_tokens": 45,
                "total_tokens": 135,
            },
        )


def make_input():
    return music_mod.MusicAgentInput(
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
        emotion="코믹",
        duration_seconds=15,
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
    )


class MusicGeneratorTest(unittest.TestCase):
    def test_parse_music_response_builds_bgm_spec(self):
        payload = {
            "music_style": "quirky acoustic pop",
            "mood": "playful and dry",
            "bpm": 126,
            "instruments": ["ukulele", "muted percussion", "soft bass"],
            "music_prompt": "Instrumental 15s BGM, quirky acoustic pop, no vocals.",
            "duration_seconds": 15,
        }

        style, bpm, prompt, meta = _parse_music_response(
            json.dumps(payload, ensure_ascii=False),
            input_data=make_input(),
        )

        self.assertEqual(style, "quirky acoustic pop")
        self.assertEqual(bpm, 126)
        self.assertIn("no vocals", prompt)
        self.assertEqual(meta["mood"], "playful and dry")
        self.assertEqual(meta["instruments"][0], "ukulele")
        self.assertEqual(meta["duration_seconds"], 15)

    def test_gpt_planner_wraps_bad_json_as_llm_error(self):
        planner = GptMusicPlanner(client=FakeClient('{"music_style": "bad"}'))

        with self.assertRaises(LLMClientError):
            planner.plan(make_input())

    def test_run_music_agent_falls_back_to_mock_on_gpt_error(self):
        planner = GptMusicPlanner(client=FakeClient('{"music_style": "bad"}'))

        result = music_mod.run_music_agent(make_input(), planner=planner)

        self.assertTrue(result.success)
        self.assertEqual(result.meta.get("music_llm_mode"), "mock")
        self.assertEqual(result.meta.get("music_llm_fallback_reason"), "gpt_error")
        self.assertEqual(result.music_style, "upbeat acoustic pop")
        self.assertEqual(result.bpm, 126)


if __name__ == "__main__":
    unittest.main()
