from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class BgmMoodRecommendation:
    mood: str
    tempo: str
    instrumentation: str
    energy: str
    style_tags: list[str]
    rationale: str


@dataclass(frozen=True)
class BgmResult:
    status: str
    mood: BgmMoodRecommendation
    mp3_path: str
    public_url: str
    provider: str = "mock"


class BgmGenerator:
    """topic/style 기반 BGM 무드 추천 및 mock BGM 경로 생성."""

    provider = "mock"

    def __init__(self, bgm_dir: Path):
        self.bgm_dir = bgm_dir
        self.bgm_dir.mkdir(parents=True, exist_ok=True)

    def recommend_mood(self, topic: str, style: str) -> BgmMoodRecommendation:
        topic_lower = topic.lower()
        style_lower = style.lower()
        tags = ["cinematic", "emotional", "architectural"]

        if any(token in topic_lower for token in ("rain", "비", "storm", "우울")):
            mood = "melancholic rainy night"
            tempo = "68-76 BPM"
            instrumentation = "soft piano, distant strings, low ambient pad"
            energy = "low"
            tags.extend(["rain", "intimate", "nocturnal"])
        elif any(token in style_lower for token in ("건축", "architecture", "spatial")):
            mood = "contemplative architectural drift"
            tempo = "72-80 BPM"
            instrumentation = "minimal piano, warm analog pad, subtle pulse"
            energy = "low-medium"
            tags.extend(["minimal", "premium", "wide-space"])
        else:
            mood = "gentle cinematic reflection"
            tempo = "74-82 BPM"
            instrumentation = "piano-led score with soft strings"
            energy = "low-medium"

        rationale = (
            f"Topic '{topic}' and style '{style}' suggest a {mood} bed that supports narration "
            "without competing with dialogue."
        )
        return BgmMoodRecommendation(
            mood=mood,
            tempo=tempo,
            instrumentation=instrumentation,
            energy=energy,
            style_tags=tags,
            rationale=rationale,
        )

    def generate(self, topic: str, style: str) -> BgmResult:
        mood = self.recommend_mood(topic, style)
        mp3_path = self.bgm_dir / "bgm.mp3"
        manifest_path = self.bgm_dir / "bgm.json"

        placeholder = (
            "MOCK_BGM\n"
            f"mood={mood.mood}\n"
            f"tempo={mood.tempo}\n"
            f"instrumentation={mood.instrumentation}\n"
        )
        mp3_path.write_bytes(placeholder.encode("utf-8"))
        manifest_path.write_text(
            (
                "{\n"
                f'  "mood": "{mood.mood}",\n'
                f'  "tempo": "{mood.tempo}",\n'
                f'  "instrumentation": "{mood.instrumentation}",\n'
                f'  "energy": "{mood.energy}",\n'
                f'  "style_tags": {json_list(mood.style_tags)},\n'
                f'  "provider": "{self.provider}",\n'
                f'  "status": "mock_completed",\n'
                f'  "generated_at": "{datetime.now(timezone.utc).isoformat()}"\n'
                "}"
            ),
            encoding="utf-8",
        )

        return BgmResult(
            status="mock_completed",
            mood=mood,
            mp3_path=str(mp3_path),
            public_url=public_url_for(mp3_path),
            provider=self.provider,
        )


def json_list(values: list[str]) -> str:
    encoded = ", ".join(f'"{value}"' for value in values)
    return f"[{encoded}]"


def public_url_for(path: Path) -> str:
    parts = path.parts
    if "generated_outputs" in parts:
        index = parts.index("generated_outputs")
        relative = "/".join(parts[index:])
        return f"/{relative}"
    return f"/{path.name}"
