from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class SubtitleCutInput:
    cut_number: int
    narration: str
    subtitle: str = ""
    duration: int = 4


@dataclass(frozen=True)
class SubtitleResult:
    cut_number: int
    status: str
    srt_path: str
    public_url: str
    cue_count: int


@dataclass(frozen=True)
class SubtitleBundleResult:
    status: str
    master_srt_path: str
    master_srt_url: str
    cuts: list[SubtitleResult]
    provider: str = "mock"


class SubtitleGenerator:
    """나레이션 텍스트 기반 SRT 자막 생성 (mock 파일)."""

    provider = "mock"

    def __init__(self, subtitles_dir: Path):
        self.subtitles_dir = subtitles_dir
        self.subtitles_dir.mkdir(parents=True, exist_ok=True)

    def _format_timestamp(self, total_seconds: float) -> str:
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        milliseconds = int(round((total_seconds - int(total_seconds)) * 1000))
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    def _build_cues(self, cut: SubtitleCutInput, start_offset: float) -> tuple[str, float, int]:
        display_text = (cut.subtitle or cut.narration).strip()
        if not display_text:
            display_text = f"CUT {cut.cut_number}"

        duration = float(max(cut.duration, 1))
        end_offset = start_offset + duration
        cue = (
            "1\n"
            f"{self._format_timestamp(start_offset)} --> {self._format_timestamp(end_offset)}\n"
            f"{display_text}\n"
        )
        return cue, end_offset, 1

    def generate_cut(self, cut: SubtitleCutInput, start_offset: float = 0.0) -> tuple[SubtitleResult, float]:
        cue_text, end_offset, cue_count = self._build_cues(cut, start_offset)
        cut_name = f"cut_{cut.cut_number:02d}"
        srt_path = self.subtitles_dir / f"{cut_name}.srt"
        srt_path.write_text(cue_text, encoding="utf-8")

        return (
            SubtitleResult(
                cut_number=cut.cut_number,
                status="mock_completed",
                srt_path=str(srt_path),
                public_url=public_url_for(srt_path),
                cue_count=cue_count,
            ),
            end_offset,
        )

    def generate_bundle(self, cuts: list[SubtitleCutInput]) -> SubtitleBundleResult:
        timeline_offset = 0.0
        per_cut_results: list[SubtitleResult] = []
        master_blocks: list[str] = []
        cue_index = 1

        for cut in cuts:
            display_text = (cut.subtitle or cut.narration).strip() or f"CUT {cut.cut_number}"
            duration = float(max(cut.duration, 1))
            start = timeline_offset
            end = start + duration
            master_blocks.append(
                f"{cue_index}\n"
                f"{self._format_timestamp(start)} --> {self._format_timestamp(end)}\n"
                f"{display_text}\n"
            )
            cue_index += 1
            timeline_offset = end

            cut_result, _ = self.generate_cut(cut, start_offset=start)
            per_cut_results.append(cut_result)

        master_srt_path = self.subtitles_dir / "master.srt"
        master_srt_path.write_text("\n".join(master_blocks).strip() + "\n", encoding="utf-8")

        manifest_path = self.subtitles_dir / "subtitles.json"
        manifest_path.write_text(
            (
                "{\n"
                f'  "provider": "{self.provider}",\n'
                f'  "status": "mock_completed",\n'
                f'  "master_srt": "{master_srt_path.name}",\n'
                f'  "generated_at": "{datetime.now(timezone.utc).isoformat()}"\n'
                "}"
            ),
            encoding="utf-8",
        )

        return SubtitleBundleResult(
            status="mock_completed",
            master_srt_path=str(master_srt_path),
            master_srt_url=public_url_for(master_srt_path),
            cuts=per_cut_results,
            provider=self.provider,
        )


def public_url_for(path: Path) -> str:
    parts = path.parts
    if "generated_outputs" in parts:
        index = parts.index("generated_outputs")
        relative = "/".join(parts[index:])
        return f"/{relative}"
    return f"/{path.name}"
