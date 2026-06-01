from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ProjectSubtitleCueInput:
    cut_number: int
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class ProjectSubtitleResult:
    status: str
    srt_path: str
    public_url: str
    cue_count: int
    provider: str = "mock"


class ProjectSubtitleGenerator:
    """script.json subtitles를 projects/{project}/subtitles/subtitle.srt mock 파일로 생성."""

    provider = "mock"

    def __init__(self, subtitles_dir: Path, *, public_url_builder):
        self.subtitles_dir = subtitles_dir
        self.public_url_builder = public_url_builder
        self.subtitles_dir.mkdir(parents=True, exist_ok=True)

    def _format_timestamp(self, total_seconds: float) -> str:
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        milliseconds = int(round((total_seconds - int(total_seconds)) * 1000))
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    def generate(self, cues: list[ProjectSubtitleCueInput]) -> ProjectSubtitleResult:
        blocks: list[str] = []
        normalized_cues: list[dict] = []

        for index, cue in enumerate(cues, start=1):
            start = float(cue.start)
            end = float(cue.end)
            if end <= start:
                end = start + 1.0
            text = cue.text.strip() or f"CUT {cue.cut_number}"
            blocks.append(
                f"{index}\n"
                f"{self._format_timestamp(start)} --> {self._format_timestamp(end)}\n"
                f"{text}\n"
            )
            normalized_cues.append(
                {
                    "cut_number": cue.cut_number,
                    "text": text,
                    "start": start,
                    "end": end,
                }
            )

        srt_path = self.subtitles_dir / "subtitle.srt"
        srt_path.write_text("\n".join(blocks).strip() + "\n", encoding="utf-8")

        manifest = {
            "provider": self.provider,
            "status": "mock_completed",
            "subtitle_file": srt_path.name,
            "cue_count": len(normalized_cues),
            "cues": normalized_cues,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        manifest_path = self.subtitles_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        return ProjectSubtitleResult(
            status="mock_completed",
            srt_path=str(srt_path),
            public_url=self.public_url_builder(srt_path),
            cue_count=len(normalized_cues),
            provider=self.provider,
        )
