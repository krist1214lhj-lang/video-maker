from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class AssemblyInput:
    job_id: str
    cut_videos: list[Path]
    narration_tracks: list[Path] = field(default_factory=list)
    bgm_track: Path | None = None
    subtitle_file: Path | None = None
    output_path: Path | None = None
    use_mock: bool = True


@dataclass(frozen=True)
class FinalExportResult:
    status: str
    mode: str
    final_video_path: str
    final_video_url: str
    ffmpeg_command: str = ""
    message: str = ""
    inputs: dict = field(default_factory=dict)


class FFmpegEditor:
    """CUT mp4 + narration + bgm + subtitle을 final_video.mp4로 합성."""

    def assemble(self, assembly: AssemblyInput) -> FinalExportResult:
        export_dir = assembly.output_path.parent if assembly.output_path else Path("generated_outputs") / assembly.job_id / "final"
        export_dir.mkdir(parents=True, exist_ok=True)
        final_path = assembly.output_path or (export_dir / "final_video.mp4")

        inputs_summary = {
            "cut_videos": [str(path) for path in assembly.cut_videos],
            "narration_tracks": [str(path) for path in assembly.narration_tracks],
            "bgm_track": str(assembly.bgm_track) if assembly.bgm_track else "",
            "subtitle_file": str(assembly.subtitle_file) if assembly.subtitle_file else "",
        }

        ffmpeg_command = self._build_ffmpeg_command(assembly, final_path)

        if assembly.use_mock or not shutil.which("ffmpeg"):
            return self._mock_export(final_path, ffmpeg_command, inputs_summary)

        try:
            subprocess.run(ffmpeg_command, shell=True, check=True, capture_output=True, text=True)
            return FinalExportResult(
                status="completed",
                mode="ffmpeg",
                final_video_path=str(final_path),
                final_video_url=public_url_for(final_path),
                ffmpeg_command=ffmpeg_command,
                message="Final video assembled with ffmpeg.",
                inputs=inputs_summary,
            )
        except subprocess.CalledProcessError as error:
            mock_result = self._mock_export(final_path, ffmpeg_command, inputs_summary)
            return FinalExportResult(
                status="mock_completed",
                mode="mock_fallback",
                final_video_path=mock_result.final_video_path,
                final_video_url=mock_result.final_video_url,
                ffmpeg_command=ffmpeg_command,
                message=f"ffmpeg failed, mock export created instead: {error}",
                inputs=inputs_summary,
            )

    def _mock_export(self, final_path: Path, ffmpeg_command: str, inputs_summary: dict) -> FinalExportResult:
        source_video = None
        for candidate in inputs_summary.get("cut_videos", []):
            path = Path(candidate)
            if path.exists() and path.stat().st_size > 1024:
                source_video = path
                break

        if source_video:
            shutil.copy2(source_video, final_path)
        else:
            placeholder = (
                "MOCK_FINAL_VIDEO\n"
                f"generated_at={datetime.now(timezone.utc).isoformat()}\n"
                f"planned_ffmpeg={ffmpeg_command}\n"
            )
            final_path.write_bytes(placeholder.encode("utf-8"))

        manifest_path = final_path.with_suffix(".json")
        manifest_path.write_text(
            (
                "{\n"
                '  "status": "mock_completed",\n'
                '  "mode": "mock",\n'
                f'  "final_video": "{final_path.name}",\n'
                f'  "ffmpeg_command": "{ffmpeg_command.replace(chr(34), chr(39))}",\n'
                f'  "generated_at": "{datetime.now(timezone.utc).isoformat()}"\n'
                "}"
            ),
            encoding="utf-8",
        )

        return FinalExportResult(
            status="mock_completed",
            mode="mock",
            final_video_path=str(final_path),
            final_video_url=public_url_for(final_path),
            ffmpeg_command=ffmpeg_command,
            message="Mock final export created. Replace with real ffmpeg when audio/video assets are ready.",
            inputs=inputs_summary,
        )

    def _build_ffmpeg_command(self, assembly: AssemblyInput, final_path: Path) -> str:
        if not assembly.cut_videos:
            return f"# ffmpeg -y -f lavfi -i color=c=black:s=1920x1080:d=5 {final_path}"

        concat_list = final_path.parent / "concat_list.txt"
        concat_lines = [f"file '{path.resolve()}'" for path in assembly.cut_videos if path.exists()]
        concat_list.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

        command_parts = [
            "ffmpeg -y",
            f"-f concat -safe 0 -i {concat_list}",
        ]

        if assembly.narration_tracks:
            command_parts.append(f"-i {assembly.narration_tracks[0]}")
        if assembly.bgm_track and assembly.bgm_track.exists():
            command_parts.append(f"-i {assembly.bgm_track}")
        if assembly.subtitle_file and assembly.subtitle_file.exists():
            command_parts.append(f"-vf subtitles={assembly.subtitle_file}")

        command_parts.append(f"-c:v libx264 -c:a aac {final_path}")
        return " ".join(command_parts)


def public_url_for(path: Path) -> str:
    parts = path.parts
    if "generated_outputs" in parts:
        index = parts.index("generated_outputs")
        relative = "/".join(parts[index:])
        return f"/{relative}"
    return f"/{path.name}"
