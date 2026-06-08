"""03_character_agent — Mock/GPT character prompt builder."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from agents.llm.config import (
    AgentLLMMode,
    ResolvedLLMMode,
    resolve_character_model,
    resolve_effective_llm_mode,
)
from agents.llm.openai_client import (
    ChatCompletionRequest,
    LLMClient,
    LLMClientError,
    OpenAIChatClient,
    parse_json_object,
)

_character_mod = importlib.import_module("agents.03_character_agent")
CharacterAgentInput = _character_mod.CharacterAgentInput
CharacterProfile = _character_mod.CharacterProfile
CharacterPromptBuilder = _character_mod.CharacterPromptBuilder
MockCharacterPromptBuilder = _character_mod.MockCharacterPromptBuilder
VisualConstraints = _character_mod.VisualConstraints
MUST_PRESERVE_DEFAULT = _character_mod.MUST_PRESERVE_DEFAULT
ALLOWED_VARIATIONS_DEFAULT = _character_mod.ALLOWED_VARIATIONS_DEFAULT
default_reference_characters_dir = _character_mod.default_reference_characters_dir


def _character_system_prompt() -> str:
    return """You are a short-form video character continuity assistant.
Return ONLY valid JSON.
Create a concise character profile, a reusable character_prompt, and visual constraints.
Do not claim to inspect reference images; use only the provided text and file/url labels."""


def _safe_memory_payload(memory_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(memory_payload, dict):
        return {}
    allowed = {
        "character_name",
        "character_summary",
        "character_prompt",
        "visual_traits",
        "style_notes",
    }
    return {k: memory_payload.get(k) for k in allowed if memory_payload.get(k)}


def _character_user_prompt(
    inp: CharacterAgentInput,
    *,
    reference_images: list[str],
    memory_payload: dict[str, Any] | None,
) -> str:
    ref = inp.reference_character
    story = inp.story
    memory = _safe_memory_payload(memory_payload)
    return f"""Main topic: {inp.topic.strip()}
Project slug: {inp.project_slug or "(unspecified)"}

Story:
- tone: {story.tone.value} ({story.tone.label_ko})
- title: {story.title}
- summary: {story.summary}
- arc: {story.story_arc}
- subtopic: {story.subtopic_title}
- cut_count: {story.cut_count}

Reference character input:
- name: {ref.name.strip()}
- display_name: {ref.display_name or "(unspecified)"}
- species_or_type: {ref.species_or_type or "(unspecified)"}
- visual_traits: {ref.visual_traits or "(unspecified)"}
- style_notes: {ref.style_notes or "(unspecified)"}
- identity_lock_strength: {ref.identity_lock_strength or "high"}
- reference_images: {reference_images}
- memory: {memory}

JSON:
{{
  "character_profile": {{
    "character_name": "{ref.name.strip()}",
    "display_name": "...",
    "species_or_type": "...",
    "character_summary": "...",
    "identity_lock_strength": "{ref.identity_lock_strength or "high"}"
  }},
  "character_prompt": "Reusable prompt for preserving the same character identity across all cuts.",
  "visual_constraints": {{
    "style_lock_rules": ["..."],
    "must_preserve": ["face shape", "fur color and pattern", "body proportions", "silhouette"],
    "allowed_variations": ["expression", "pose", "gaze direction", "action per cut"]
  }}
}}
Rules:
- Preserve the same individual character across all cuts.
- Vary only expression, pose, gaze, and action per cut.
- Keep character_prompt practical for downstream image/video generation.
- Do not invent precise visual traits unless present in input or memory."""


def _as_string_list(value: Any, *, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(fallback)
    out = [str(item).strip() for item in value if str(item).strip()]
    return out or list(fallback)


def _parse_character_response(
    text: str,
    *,
    inp: CharacterAgentInput,
    reference_images: list[str],
    memory_payload: dict[str, Any] | None,
) -> tuple[CharacterProfile, str, VisualConstraints]:
    payload = parse_json_object(text)
    profile_payload = payload.get("character_profile")
    constraints_payload = payload.get("visual_constraints")
    if not isinstance(profile_payload, dict):
        raise ValueError("character_profile must be an object")
    if not isinstance(constraints_payload, dict):
        raise ValueError("visual_constraints must be an object")

    ref = inp.reference_character
    root = inp.reference_characters_dir or default_reference_characters_dir()
    name = str(profile_payload.get("character_name") or ref.name).strip()
    if not name:
        raise ValueError("character_name is required")
    display = str(profile_payload.get("display_name") or ref.display_name or name.replace("_", " ").title()).strip()
    summary = str(
        profile_payload.get("character_summary")
        or (memory_payload or {}).get("character_summary")
        or ref.species_or_type
        or f"{display} fixed identity character for '{inp.topic.strip()}'."
    ).strip()
    species = str(profile_payload.get("species_or_type") or ref.species_or_type or summary).strip()
    strength = str(profile_payload.get("identity_lock_strength") or ref.identity_lock_strength or "high").strip()
    prompt = str(payload.get("character_prompt") or "").strip()
    if not prompt:
        raise ValueError("character_prompt is required")

    constraints = VisualConstraints(
        style_lock_rules=_as_string_list(
            constraints_payload.get("style_lock_rules"),
            fallback=[
                f"Reference Character: {name}.",
                "Preserve face shape, fur color, body proportions, and silhouette across all cuts.",
                "Only vary expression, pose, gaze, and action per cut.",
                f"Identity lock strength: {strength}.",
            ],
        ),
        must_preserve=_as_string_list(
            constraints_payload.get("must_preserve"),
            fallback=list(MUST_PRESERVE_DEFAULT),
        ),
        allowed_variations=_as_string_list(
            constraints_payload.get("allowed_variations"),
            fallback=list(ALLOWED_VARIATIONS_DEFAULT),
        ),
    )
    profile = CharacterProfile(
        character_name=name,
        display_name=display,
        species_or_type=species,
        character_summary=summary,
        reference_dir=str(root / name),
        memory_file=f"reference_characters/{name}/character_memory.json",
        reference_images=reference_images,
        public_reference_url=reference_images[0] if reference_images else "",
        identity_lock_strength=strength,
    )
    return profile, prompt, constraints


@dataclass
class GptCharacterPromptBuilder:
    client: LLMClient
    model: str | None = None
    temperature: float = 0.5

    def build(
        self,
        input_data: CharacterAgentInput,
        *,
        reference_images: list[str],
        memory_payload: dict[str, Any] | None,
    ) -> tuple[CharacterProfile, str, VisualConstraints]:
        model = (self.model or resolve_character_model()).strip()
        result = self.client.complete_json(
            ChatCompletionRequest(
                system_prompt=_character_system_prompt(),
                user_prompt=_character_user_prompt(
                    input_data,
                    reference_images=reference_images,
                    memory_payload=memory_payload,
                ),
                model=model,
                temperature=self.temperature,
                max_output_tokens=2048,
            )
        )
        try:
            parsed = _parse_character_response(
                result.text,
                inp=input_data,
                reference_images=reference_images,
                memory_payload=memory_payload,
            )
        except ValueError as exc:
            raise LLMClientError(f"character JSON parse failed: {exc}") from exc
        object.__setattr__(
            self,
            "_last_meta",
            {
                "llm_model": result.model,
                "llm_usage": result.usage,
                "llm_provider": result.provider,
            },
        )
        return parsed

    @property
    def last_meta(self) -> dict[str, Any]:
        return getattr(self, "_last_meta", {})


def create_character_prompt_builder(
    mode: str | None = None,
) -> tuple[CharacterPromptBuilder, ResolvedLLMMode]:
    resolved = resolve_effective_llm_mode(mode)
    if resolved.mode == AgentLLMMode.MOCK:
        return MockCharacterPromptBuilder(), resolved
    return GptCharacterPromptBuilder(client=OpenAIChatClient()), resolved


def builder_mode_label(builder: Any, *, resolved: ResolvedLLMMode) -> str:
    name = type(builder).__name__
    if isinstance(builder, GptCharacterPromptBuilder):
        model = (getattr(builder, "last_meta", {}) or {}).get("llm_model") or ""
        label = f"{name}({model})" if model else name
    else:
        label = name
    if resolved.fallback_reason:
        return f"{label}[fallback:{resolved.fallback_reason}]"
    return label
