from __future__ import annotations

import importlib
import json
import unittest

from agents.llm.character_generator import (
    GptCharacterPromptBuilder,
    _parse_character_response,
)
from agents.llm.openai_client import ChatCompletionResult, LLMClientError

character_mod = importlib.import_module("agents.03_character_agent")
story_mod = importlib.import_module("agents.02_story_agent")


class FakeClient:
    def __init__(self, text: str):
        self.text = text

    def complete_json(self, request):
        return ChatCompletionResult(
            text=self.text,
            model=request.model,
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
        )


def make_input():
    return character_mod.CharacterAgentInput(
        topic="애견카페에서 신나게 노는 뽀식이",
        story=character_mod.StoryContext(
            main_topic="애견카페에서 신나게 노는 뽀식이",
            subtopic_id="subtopic_1",
            subtopic_title="첫 만남의 어색함",
            tone=story_mod.StoryTone.COMIC,
            title="코믹형",
            summary="애견카페에서 벌어지는 짧은 이야기",
            story_arc="도입-반응-마무리",
            cut_count=5,
        ),
        reference_character=character_mod.ReferenceCharacter(
            name="bposik_v2",
            species_or_type="cream/apricot toy poodle",
            identity_lock_strength="high",
        ),
    )


class CharacterGeneratorParserTest(unittest.TestCase):
    def test_parse_character_response_builds_profile_and_constraints(self):
        inp = make_input()
        payload = {
            "character_profile": {
                "character_name": "bposik_v2",
                "display_name": "Bposik",
                "species_or_type": "toy poodle",
                "character_summary": "Small cream dog with fixed identity.",
                "identity_lock_strength": "high",
            },
            "character_prompt": "Keep Bposik as the same small cream dog in every cut.",
            "visual_constraints": {
                "style_lock_rules": ["Reference Character: bposik_v2."],
                "must_preserve": ["face shape"],
                "allowed_variations": ["expression"],
            },
        }

        profile, prompt, constraints = _parse_character_response(
            json.dumps(payload),
            inp=inp,
            reference_images=["/reference_characters/bposik_v2/main.png"],
            memory_payload=None,
        )

        self.assertEqual(profile.character_name, "bposik_v2")
        self.assertEqual(profile.public_reference_url, "/reference_characters/bposik_v2/main.png")
        self.assertIn("same small cream dog", prompt)
        self.assertEqual(constraints.must_preserve, ["face shape"])

    def test_gpt_builder_wraps_bad_json_as_llm_error(self):
        builder = GptCharacterPromptBuilder(client=FakeClient('{"bad": true}'))

        with self.assertRaises(LLMClientError):
            builder.build(make_input(), reference_images=[], memory_payload=None)

    def test_run_character_agent_falls_back_to_mock_on_gpt_error(self):
        builder = GptCharacterPromptBuilder(client=FakeClient('{"bad": true}'))

        result = character_mod.run_character_agent(make_input(), builder=builder)

        self.assertTrue(result.success)
        self.assertEqual(result.meta.get("character_llm_mode"), "mock")
        self.assertEqual(result.meta.get("character_llm_fallback_reason"), "gpt_error")
        self.assertIn("Character consistency", result.character_prompt)


if __name__ == "__main__":
    unittest.main()
