from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class FFmpegNotInstalledError(RuntimeError):
    pass


class FFmpegRunError(RuntimeError):
    def __init__(self, message: str, *, command: list[str] | None = None, stderr: str = "", log_path: str = ""):
        super().__init__(message)
        self.command = command or []
        self.stderr = stderr
        self.log_path = log_path


@dataclass(frozen=True)
class ProjectFinalExportInput:
    video_path: Path
    audio_tracks: list[Path]
    subtitle_path: Path
    output_path: Path
    bgm_track: Path | None = None
    audio_durations: dict[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectFinalExportResult:
    status: str
    output_path: str
    ffmpeg_command: str
    ffmpeg_log_path: str = ""
    duration_seconds: float | None = None
    duration: str = "unknown"
    resolution: str = "unknown"
    file_size_bytes: int = 0
    file_size: str = "unknown"
    message: str = ""
    export_logs: list[str] = field(default_factory=list)
    narration_track_path: str = ""
    probe_summary: dict = field(default_factory=dict)
    inputs: dict = field(default_factory=dict)


def format_duration(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "unknown"
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def natural_sort_key(path: Path) -> tuple[int, str]:
    match = re.search(r"(\d+)", path.stem)
    return (int(match.group(1)) if match else 999, path.name)


def escape_subtitle_filter_path(path: Path) -> str:
    normalized = path.resolve().as_posix()
    return normalized.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def build_subtitle_filter(path: Path) -> str:
    return f"subtitles=filename='{escape_subtitle_filter_path(path)}'"


def escape_concat_path(path: Path) -> str:
    normalized = path.resolve().as_posix()
    return normalized.replace("'", "'\\''")


def _append_log(
    log_path: Path,
    label: str,
    command: list[str] | None = None,
    stdout: str = "",
    stderr: str = "",
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n=== {label} ===\n")
        if command:
            handle.write(" ".join(command) + "\n")
        if stdout.strip():
            handle.write(stdout.strip() + "\n")
        if stderr.strip():
            handle.write("[stderr]\n" + stderr.strip() + "\n")


def _run_ffmpeg(command: list[str], *, log_path: Path, label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True)
    _append_log(log_path, label, command, result.stdout, result.stderr)
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        tail = stderr.splitlines()[-1] if stderr else "ffmpeg command failed."
        raise FFmpegRunError(
            tail,
            command=command,
            stderr=stderr,
            log_path=str(log_path),
        )
    return result


def is_valid_media_file(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 128:
        return False

    if path.read_bytes()[:4].startswith(b"MOCK"):
        return False

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return True

    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=format_name",
            "-of",
            "default=nw=1:nk=1",
            str(path.resolve()),
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _read_sidecar_duration(mp3_path: Path, default: float = 3.0) -> float:
    sidecar_path = mp3_path.with_suffix(".json")
    if not sidecar_path.exists():
        return default

    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default

    duration = payload.get("duration_seconds") or payload.get("duration")
    try:
        return max(float(duration), 0.5)
    except (TypeError, ValueError):
        return default


def resolve_narration_track(mp3_path: Path, exports_dir: Path, log_path: Path) -> Path:
    if is_valid_media_file(mp3_path):
        return mp3_path.resolve()

    duration = _read_sidecar_duration(mp3_path)
    resolved_path = exports_dir / f"resolved_{mp3_path.name}"
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=mono",
        "-t",
        str(duration),
        "-c:a",
        "libmp3lame",
        "-q:a",
        "6",
        str(resolved_path),
    ]
    _run_ffmpeg(command, log_path=log_path, label=f"resolve mock audio {mp3_path.name}")
    return resolved_path.resolve()


def probe_audio_metadata(audio_path: Path) -> dict:
    if not audio_path.exists():
        return {"duration_seconds": None, "duration": "unknown", "file_size_bytes": 0}

    file_size_bytes = audio_path.stat().st_size
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {
            "duration_seconds": None,
            "duration": "unknown",
            "file_size_bytes": file_size_bytes,
        }

    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(audio_path.resolve()),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        duration_seconds = round(float(result.stdout.strip()), 3) if result.stdout.strip() else None
    except (subprocess.CalledProcessError, ValueError):
        duration_seconds = None

    return {
        "duration_seconds": duration_seconds,
        "duration": format_duration(duration_seconds),
        "file_size_bytes": file_size_bytes,
    }


def probe_video_metadata(video_path: Path) -> dict:
    if not video_path.exists():
        return {
            "duration_seconds": None,
            "duration": "unknown",
            "resolution": "unknown",
            "file_size_bytes": 0,
            "file_size": "unknown",
        }

    file_size_bytes = video_path.stat().st_size
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {
            "duration_seconds": None,
            "duration": "unknown",
            "resolution": "unknown",
            "file_size_bytes": file_size_bytes,
            "file_size": format_file_size(file_size_bytes),
        }

    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,duration",
                "-show_entries",
                "format=duration,size",
                "-of",
                "json",
                str(video_path.resolve()),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout or "{}")
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {
            "duration_seconds": None,
            "duration": "unknown",
            "resolution": "unknown",
            "file_size_bytes": file_size_bytes,
            "file_size": format_file_size(file_size_bytes),
        }

    stream = (payload.get("streams") or [{}])[0]
    fmt = payload.get("format") or {}
    width = stream.get("width")
    height = stream.get("height")
    duration_raw = fmt.get("duration") or stream.get("duration")
    duration_seconds = round(float(duration_raw), 2) if duration_raw else None
    size = int(fmt.get("size") or file_size_bytes)
    resolution = f"{width}x{height}" if width and height else "unknown"

    return {
        "duration_seconds": duration_seconds,
        "duration": format_duration(duration_seconds),
        "resolution": resolution,
        "file_size_bytes": size,
        "file_size": format_file_size(size),
    }


def probe_export_streams(video_path: Path) -> dict:
    summary = {
        "video_streams": 0,
        "audio_streams": 0,
        "subtitle_streams": 0,
        "streams": [],
    }
    if not video_path.exists():
        return summary

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return summary

    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=index,codec_type,codec_name",
                "-of",
                "json",
                str(video_path.resolve()),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout or "{}")
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return summary

    for stream in payload.get("streams") or []:
        codec_type = stream.get("codec_type") or ""
        summary["streams"].append(
            {
                "index": stream.get("index"),
                "codec_type": codec_type,
                "codec_name": stream.get("codec_name"),
            }
        )
        if codec_type == "video":
            summary["video_streams"] += 1
        elif codec_type == "audio":
            summary["audio_streams"] += 1
        elif codec_type == "subtitle":
            summary["subtitle_streams"] += 1

    return summary


def build_export_cut_normalize_filter(per_cut_duration: float) -> str:
    duration = max(0.1, float(per_cut_duration))
    return (
        "scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"trim=duration={duration},setpts=PTS-STARTPTS,"
        f"tpad=stop_mode=clone:stop_duration={duration},"
        f"trim=duration={duration},setpts=PTS-STARTPTS"
    )


def normalize_export_cut_video(
    source_path: Path,
    output_path: Path,
    per_cut_duration: float,
    log_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path.resolve()),
        "-vf",
        build_export_cut_normalize_filter(per_cut_duration),
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path.resolve()),
    ]
    _run_ffmpeg(command, log_path=log_path, label=f"normalize export cut {source_path.name}")


def _write_video_concat_list(video_paths: list[Path], concat_list_path: Path) -> None:
    concat_list_path.write_text(
        "\n".join(f"file '{escape_concat_path(path)}'" for path in video_paths) + "\n",
        encoding="utf-8",
    )


def stitch_normalized_export_videos(
    normalized_paths: list[Path],
    output_path: Path,
    log_path: Path,
) -> None:
    if not normalized_paths:
        raise FFmpegRunError("No normalized export clips to stitch.", log_path=str(log_path))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list_path = output_path.with_suffix(".concat.txt")
    _write_video_concat_list(normalized_paths, concat_list_path)
    copy_command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-c",
        "copy",
        str(output_path.resolve()),
    ]
    reencode_command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-an",
        str(output_path.resolve()),
    ]
    try:
        _run_ffmpeg(copy_command, log_path=log_path, label="concat normalized export cuts")
    except FFmpegRunError:
        _run_ffmpeg(reencode_command, log_path=log_path, label="concat normalized export cuts (re-encode)")
    finally:
        concat_list_path.unlink(missing_ok=True)


def _write_audio_concat_list(audio_tracks: list[Path], concat_list_path: Path) -> None:
    concat_list_path.write_text(
        "\n".join(f"file '{escape_concat_path(track)}'" for track in audio_tracks) + "\n",
        encoding="utf-8",
    )


def _concat_audio_tracks(audio_tracks: list[Path], merged_audio_path: Path, log_path: Path) -> None:
    concat_list_path = merged_audio_path.with_suffix(".txt")
    _write_audio_concat_list(audio_tracks, concat_list_path)
    copy_command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-c",
        "copy",
        str(merged_audio_path),
    ]
    reencode_command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-c:a",
        "libmp3lame",
        "-q:a",
        "4",
        str(merged_audio_path),
    ]
    try:
        _run_ffmpeg(copy_command, log_path=log_path, label="concat narration tracks")
    except FFmpegRunError:
        _run_ffmpeg(reencode_command, log_path=log_path, label="concat narration tracks (re-encode)")
    finally:
        concat_list_path.unlink(missing_ok=True)


def concat_narration_tracks(audio_tracks: list[Path], merged_audio_path: Path, log_path: Path) -> None:
    _concat_audio_tracks(audio_tracks, merged_audio_path, log_path)


def pad_narration_track_to_duration(
    source_path: Path,
    target_duration: float,
    output_path: Path,
    log_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, float(target_duration))
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path.resolve()),
        "-af",
        "apad",
        "-t",
        f"{duration:.3f}",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "4",
        str(output_path.resolve()),
    ]
    _run_ffmpeg(command, log_path=log_path, label=f"pad narration {source_path.name} to {duration:.3f}s")
    return output_path.resolve()


def build_synced_narration_tracks(
    source_tracks: list[Path],
    slot_durations: dict[int, float],
    output_dir: Path,
    log_path: Path,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    padded_tracks: list[Path] = []
    for track in sorted(source_tracks, key=natural_sort_key):
        cut_number = natural_sort_key(track)[0]
        slot_duration = float(slot_durations.get(cut_number) or 0)
        if slot_duration <= 0:
            padded_tracks.append(track)
            continue
        padded_path = output_dir / f"padded_{track.name}"
        pad_narration_track_to_duration(track, slot_duration, padded_path, log_path)
        padded_tracks.append(padded_path)
    return padded_tracks


def _build_narration_track(
    resolved_tracks: list[Path],
    exports_dir: Path,
    log_path: Path,
) -> tuple[Path, list[Path]]:
    narration_track_path = exports_dir / "narration_track.mp3"
    cleanup_paths: list[Path] = [path for path in resolved_tracks if path.name.startswith("resolved_")]

    if len(resolved_tracks) == 1:
        shutil.copy2(resolved_tracks[0], narration_track_path)
        _append_log(log_path, "single narration track copied", stdout="Audio merged")
    else:
        _concat_audio_tracks(resolved_tracks, narration_track_path, log_path)
        _append_log(log_path, "narration tracks concatenated", stdout="Audio merged")

    if not is_valid_media_file(narration_track_path):
        raise FFmpegRunError(
            "Narration track merge failed.",
            log_path=str(log_path),
        )

    return narration_track_path, cleanup_paths


def _fit_audio_tracks_to_durations(
    resolved_tracks: list[Path],
    durations: dict[int, float],
    exports_dir: Path,
    log_path: Path,
) -> tuple[list[Path], list[Path]]:
    if not durations:
        return resolved_tracks, []

    fitted_tracks: list[Path] = []
    cleanup_paths: list[Path] = []
    for track in resolved_tracks:
        cut_number = natural_sort_key(track)[0]
        duration = float(durations.get(cut_number) or 0)
        if duration <= 0:
            fitted_tracks.append(track)
            continue
        fitted_path = exports_dir / f"duration_fit_{track.stem}.mp3"
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(track),
            "-af",
            "apad",
            "-t",
            f"{duration:.3f}",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(fitted_path),
        ]
        _run_ffmpeg(command, log_path=log_path, label=f"fit narration duration {track.name}")
        fitted_tracks.append(fitted_path.resolve())
        cleanup_paths.append(fitted_path)
    return fitted_tracks, cleanup_paths


def assemble_project_final_export(export_input: ProjectFinalExportInput) -> ProjectFinalExportResult:
    if not shutil.which("ffmpeg"):
        raise FFmpegNotInstalledError("ffmpeg not installed")

    video_path = export_input.video_path.resolve()
    subtitle_path = export_input.subtitle_path.resolve()
    output_path = export_input.output_path.resolve()
    exports_dir = output_path.parent
    log_path = exports_dir / "ffmpeg_export.log"
    export_logs: list[str] = []

    log_path.write_text("Final export ffmpeg log\n", encoding="utf-8")

    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not export_input.audio_tracks:
        raise FileNotFoundError("No narration audio tracks found.")
    if not subtitle_path.exists():
        raise FileNotFoundError(f"Subtitle not found: {subtitle_path}")
    if export_input.bgm_track is not None and not export_input.bgm_track.exists():
        raise FileNotFoundError(f"BGM track not found: {export_input.bgm_track}")

    exports_dir.mkdir(parents=True, exist_ok=True)

    sorted_tracks = sorted(export_input.audio_tracks, key=natural_sort_key)
    source_tracks = [track for track in sorted_tracks if track.exists()]
    if not source_tracks:
        raise FileNotFoundError("No usable narration audio tracks found.")

    resolved_tracks = [resolve_narration_track(track, exports_dir, log_path) for track in source_tracks]
    fitted_tracks, fitted_cleanup_paths = _fit_audio_tracks_to_durations(
        resolved_tracks,
        export_input.audio_durations,
        exports_dir,
        log_path,
    )
    cleanup_paths = [path for path in resolved_tracks if path.name.startswith("resolved_")] + fitted_cleanup_paths
    narration_track_path, narration_cleanup_paths = _build_narration_track(fitted_tracks, exports_dir, log_path)
    cleanup_paths.extend(narration_cleanup_paths)
    export_logs.append("Audio merged")

    subtitle_filter = build_subtitle_filter(subtitle_path)
    if export_input.bgm_track:
        bgm_path = export_input.bgm_track.resolve()
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(narration_track_path),
            "-i",
            str(bgm_path),
            "-filter_complex",
            (
                "[1:a]volume=1.0[a1];"
                "[2:a]volume=0.15[a2];"
                "[a1][a2]amix=inputs=2:duration=longest:dropout_transition=2:normalize=0[aout]"
            ),
            "-vf",
            subtitle_filter,
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_path),
        ]
    else:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(narration_track_path),
            "-vf",
            subtitle_filter,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_path),
        ]

    try:
        _run_ffmpeg(command, log_path=log_path, label="assemble final export")
        export_logs.append("Video merged")
        export_logs.append("Subtitle burned")
        for line in export_logs:
            _append_log(log_path, line, stdout=line)
    finally:
        for path in cleanup_paths:
            path.unlink(missing_ok=True)

    if not output_path.exists() or output_path.stat().st_size < 1024:
        raise FFmpegRunError(
            "ffmpeg finished but final_export.mp4 was not created.",
            command=command,
            log_path=str(log_path),
        )

    probe_summary = probe_export_streams(output_path)
    if probe_summary["video_streams"] < 1:
        raise FFmpegRunError(
            "ffprobe verification failed: output has no video stream.",
            command=command,
            log_path=str(log_path),
        )
    if probe_summary["audio_streams"] < 1:
        raise FFmpegRunError(
            "ffprobe verification failed: output has no audio stream.",
            command=command,
            log_path=str(log_path),
        )

    _append_log(
        log_path,
        "ffprobe verification",
        stdout=json.dumps(probe_summary, ensure_ascii=False, indent=2),
    )

    media_metadata = probe_video_metadata(output_path)
    message = "\n".join(export_logs)
    return ProjectFinalExportResult(
        status="ready",
        output_path=str(output_path),
        ffmpeg_command=" ".join(command),
        ffmpeg_log_path=str(log_path),
        duration_seconds=media_metadata.get("duration_seconds"),
        duration=media_metadata.get("duration", "unknown"),
        resolution=media_metadata.get("resolution", "unknown"),
        file_size_bytes=media_metadata.get("file_size_bytes", 0),
        file_size=media_metadata.get("file_size", "unknown"),
        message=message,
        export_logs=export_logs,
        narration_track_path=str(narration_track_path),
        probe_summary=probe_summary,
        inputs={
            "video_path": str(video_path),
            "audio_tracks": [str(path) for path in source_tracks],
            "resolved_audio_tracks": [str(path) for path in resolved_tracks],
            "audio_durations": {str(key): value for key, value in export_input.audio_durations.items()},
            "narration_track_path": str(narration_track_path),
            "bgm_track": str(export_input.bgm_track) if export_input.bgm_track else None,
            "subtitle_path": str(subtitle_path),
            "output_path": str(output_path),
        },
    )
