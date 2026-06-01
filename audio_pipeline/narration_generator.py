from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class NarrationCutInput:
    cut_number: int
    narration: str
    subtitle: str = ""
    duration: int = 4


@dataclass(frozen=True)
class NarrationResult:
    cut_number: int
    status: str
    text: str
    mp3_path: str
    public_url: str
    duration_seconds: float
    provider: str = "mock"


class NarrationGenerator:
    """CUT별 나레이션을 mp3로 변환하는 파이프라인 (현재는 mock 경로만 생성)."""

    provider = "mock"

    def __init__(self, narration_dir: Path):
        self.narration_dir = narration_dir
        self.narration_dir.mkdir(parents=True, exist_ok=True)

    def generate_cut(self, cut: NarrationCutInput) -> NarrationResult:
        cut_name = f"cut_{cut.cut_number:02d}"
        mp3_path = self.narration_dir / f"{cut_name}.mp3"
        manifest_path = self.narration_dir / f"{cut_name}.json"

        placeholder = (
            f"MOCK_NARRATION cut={cut.cut_number}\n"
            f"text={cut.narration.strip()}\n"
            f"duration={cut.duration}\n"
        )
        mp3_path.write_bytes(placeholder.encode("utf-8"))

        manifest_path.write_text(
            (
                "{\n"
                f'  "cut_number": {cut.cut_number},\n'
                f'  "text": {json_escape(cut.narration)},\n'
                f'  "subtitle": {json_escape(cut.subtitle)},\n'
                f'  "duration_seconds": {max(cut.duration, 1)},\n'
                f'  "provider": "{self.provider}",\n'
                f'  "status": "mock_completed",\n'
                f'  "generated_at": "{datetime.now(timezone.utc).isoformat()}"\n'
                "}"
            ),
            encoding="utf-8",
        )

        return NarrationResult(
            cut_number=cut.cut_number,
            status="mock_completed",
            text=cut.narration.strip(),
            mp3_path=str(mp3_path),
            public_url=public_url_for(mp3_path),
            duration_seconds=float(max(cut.duration, 1)),
            provider=self.provider,
        )

    def generate_for_cuts(self, cuts: list[NarrationCutInput]) -> list[NarrationResult]:
        return [self.generate_cut(cut) for cut in cuts]


def json_escape(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def public_url_for(path: Path) -> str:
    parts = path.parts
    if "generated_outputs" in parts:
        index = parts.index("generated_outputs")
        relative = "/".join(parts[index:])
        return f"/{relative}"
    return f"/{path.name}"
