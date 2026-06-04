"""
Agent 03 — character_agent (Phase 3A-2: design only)

역할:
  - 고정 캐릭터 관리 (reference_characters/)
  - reference_character 슬롯 + 스토리 맥락 → character_prompt
  - character_profile · visual_constraints 생성

입력: topic, story, reference_character
출력: character_profile, character_prompt, visual_constraints

연결 금지: main.py / FastAPI / templates
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

_story_mod = importlib.import_module("agents.02_story_agent")
StoryVariant = _story_mod.StoryVariant
StoryTone = _story_mod.StoryTone

REFERENCE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
REFERENCE_PRIORITY = (
    "main.png",
    "main.jpg",
    "reference.png",
    "reference.jpg",
)

MUST_PRESERVE_DEFAULT = [
    "face shape",
    "fur color and pattern",
    "body proportions",
    "silhouette",
]
ALLOWED_VARIATIONS_DEFAULT = [
    "expression",
    "pose",
    "gaze direction",
    "action per cut",
]


def default_reference_characters_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "reference_characters"


@dataclass
class StoryContext:
    """02_story_agent 에서 선택된 스토리 (Director가 전달)."""

    main_topic: str
    subtopic_id: str
    subtopic_title: str
    tone: StoryTone
    title: str
    summary: str
    story_arc: str
    cut_count: int

    @classmethod
    def from_variant(
        cls,
        *,
        main_topic: str,
        subtopic_id: str,
        subtopic_title: str,
        variant: StoryVariant,
        cut_count: int,
    ) -> StoryContext:
        return cls(
            main_topic=main_topic,
            subtopic_id=subtopic_id,
            subtopic_title=subtopic_title,
            tone=variant.tone,
            title=variant.title,
            summary=variant.narration_outline or variant.summary,
            story_arc=variant.logline or variant.story_arc,
            cut_count=cut_count,
        )


@dataclass
class ReferenceCharacter:
    """입력: reference_characters/{name}/ 레퍼런스 슬롯."""

    name: str
    display_name: str = ""
    species_or_type: str = ""
    visual_traits: str = ""
    style_notes: str = ""
    identity_lock_strength: str = "high"


@dataclass
class CharacterProfile:
    """출력: 해석·바인딩된 캐릭터 프로필."""

    character_name: str
    display_name: str
    species_or_type: str
    character_summary: str
    reference_dir: str
    memory_file: str
    reference_images: list[str]
    public_reference_url: str
    identity_lock_strength: str


@dataclass
class VisualConstraints:
    """출력: 컷 생성 시 지켜야 할 비주얼 제약."""

    style_lock_rules: list[str]
    must_preserve: list[str]
    allowed_variations: list[str]


@dataclass
class CharacterAgentInput:
    topic: str
    story: StoryContext
    reference_character: ReferenceCharacter
    project_slug: str = ""
    reference_characters_dir: Path | None = None


@dataclass
class CharacterAgentResult:
    success: bool
    character_profile: CharacterProfile | None
    character_prompt: str
    visual_constraints: VisualConstraints | None
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meta: dict[str, Any] = field(default_factory=dict)


class CharacterPromptBuilder(Protocol):
    def build(
        self,
        input_data: CharacterAgentInput,
        *,
        reference_images: list[str],
        memory_payload: dict[str, Any] | None,
    ) -> tuple[CharacterProfile, str, VisualConstraints]:
        ...


def _discover_reference_images(character_dir: Path) -> list[Path]:
    if not character_dir.is_dir():
        return []
    paths: list[Path] = []
    seen: set[str] = set()
    for name in REFERENCE_PRIORITY:
        candidate = character_dir / name
        if candidate.is_file():
            key = str(candidate)
            if key not in seen:
                paths.append(candidate)
                seen.add(key)
    for path in sorted(character_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in REFERENCE_EXTENSIONS and str(path) not in seen:
            paths.append(path)
            seen.add(str(path))
    return paths


def _public_url_for_reference(root: Path, image_path: Path) -> str:
    try:
        rel = image_path.relative_to(root)
        return f"/reference_characters/{rel.as_posix()}"
    except ValueError:
        return f"/reference_characters/{image_path.name}"


def _load_memory(character_dir: Path) -> dict[str, Any] | None:
    memory_path = character_dir / "character_memory.json"
    if not memory_path.is_file():
        return None
    try:
        return json.loads(memory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@dataclass
class MockCharacterPromptBuilder:
    def build(
        self,
        input_data: CharacterAgentInput,
        *,
        reference_images: list[str],
        memory_payload: dict[str, Any] | None,
    ) -> tuple[CharacterProfile, str, VisualConstraints]:
        ref = input_data.reference_character
        story = input_data.story
        name = ref.name.strip()
        display = ref.display_name.strip() or name.replace("_", " ").title()
        root = input_data.reference_characters_dir or default_reference_characters_dir()

        memory_prompt = ""
        if memory_payload:
            memory_prompt = str(memory_payload.get("character_prompt") or "").strip()

        story_hook = (
            f"Story tone: {story.tone.label_ko}. Arc: {story.story_arc}. "
            f"Scene anchor: {story.subtopic_title}."
        )
        style_lock_rules = [
            f"Reference Character: {name}.",
            "Preserve face shape, fur color, body proportions, and silhouette across all cuts.",
            "Only vary expression, pose, gaze, and action per cut.",
            f"Identity lock strength: {ref.identity_lock_strength}.",
        ]
        if ref.visual_traits:
            style_lock_rules.append(f"Visual traits: {ref.visual_traits}")
        if ref.style_notes:
            style_lock_rules.append(f"Style notes: {ref.style_notes}")

        character_summary = (
            ref.species_or_type
            or (memory_payload or {}).get("character_summary")
            or f"{display} — fixed identity character for '{input_data.topic.strip()}'."
        )
        if not isinstance(character_summary, str):
            character_summary = str(character_summary)
        character_summary = character_summary.strip()

        character_prompt = (
            f"Character consistency for {display}: {character_summary} "
            f"{story_hook} "
            + (memory_prompt + " " if memory_prompt else "")
            + " ".join(style_lock_rules)
        ).strip()

        ref_dir = str(root / name)
        primary_url = reference_images[0] if reference_images else ""

        profile = CharacterProfile(
            character_name=name,
            display_name=display,
            species_or_type=ref.species_or_type or character_summary,
            character_summary=character_summary,
            reference_dir=ref_dir,
            memory_file=f"reference_characters/{name}/character_memory.json",
            reference_images=reference_images,
            public_reference_url=primary_url,
            identity_lock_strength=ref.identity_lock_strength,
        )
        constraints = VisualConstraints(
            style_lock_rules=style_lock_rules,
            must_preserve=list(MUST_PRESERVE_DEFAULT),
            allowed_variations=list(ALLOWED_VARIATIONS_DEFAULT),
        )
        return profile, character_prompt, constraints


def run_character_agent(
    input_data: CharacterAgentInput,
    *,
    builder: CharacterPromptBuilder | None = None,
) -> CharacterAgentResult:
    if not input_data.topic.strip() or not input_data.reference_character.name.strip():
        return CharacterAgentResult(
            success=False,
            character_profile=None,
            character_prompt="",
            visual_constraints=None,
            meta={"error": "topic and reference_character.name are required"},
        )

    root = input_data.reference_characters_dir or default_reference_characters_dir()
    char_name = input_data.reference_character.name.strip()
    char_dir = root / char_name
    image_paths = _discover_reference_images(char_dir)
    reference_urls = [_public_url_for_reference(root, p) for p in image_paths]
    memory = _load_memory(char_dir)

    gen = builder or MockCharacterPromptBuilder()
    profile, prompt, constraints = gen.build(
        input_data,
        reference_images=reference_urls,
        memory_payload=memory,
    )

    return CharacterAgentResult(
        success=True,
        character_profile=profile,
        character_prompt=prompt,
        visual_constraints=constraints,
        meta={
            "reference_image_count": len(reference_urls),
            "memory_loaded": memory is not None,
            "builder": type(gen).__name__,
        },
    )


def example_character_result() -> dict[str, Any]:
    story_mod = importlib.import_module("agents.02_story_agent")
    topic_mod = importlib.import_module("agents.01_topic_agent")
    tr = topic_mod.run_topic_agent(
        topic_mod.TopicAgentInput(main_topic="애견카페에서 신나게 노는 뽀식이", style="애니메이션")
    )
    sr = story_mod.run_story_agent(
        story_mod.StoryAgentInput(
            main_topic=tr.main_topic,
            subtopic=tr.subtopics[0],
            style=tr.style,
            cut_count=tr.cut_count,
        )
    )
    ctx = StoryContext.from_variant(
        main_topic=tr.main_topic,
        subtopic_id=tr.subtopics[0].id,
        subtopic_title=tr.subtopics[0].title,
        variant=sr.variants[0],
        cut_count=tr.cut_count,
    )
    result = run_character_agent(
        CharacterAgentInput(
            topic=tr.main_topic,
            story=ctx,
            reference_character=ReferenceCharacter(
                name="bposik_v2",
                species_or_type="cream/apricot toy poodle",
                identity_lock_strength="high",
            ),
        )
    )
    profile = result.character_profile
    vc = result.visual_constraints
    return {
        "success": result.success,
        "character_prompt": result.character_prompt[:200] + "...",
        "character_profile": {
            "character_name": profile.character_name if profile else None,
            "public_reference_url": profile.public_reference_url if profile else None,
        },
        "visual_constraints": {
            "style_lock_rules_count": len(vc.style_lock_rules) if vc else 0,
            "must_preserve": vc.must_preserve[:2] if vc else [],
        },
    }
