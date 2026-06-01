import json
import shutil
import subprocess
from datetime import datetime, timezone

from .base import VideoGenerationRequest, VideoGenerationResult, VideoProvider


class MockVideoProvider(VideoProvider):
    name = "mock"

    def _write_placeholder_mp4(self, clip_path, duration: float) -> None:
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise RuntimeError("ffmpeg is required to create a playable mock mp4, but it was not found in PATH.")

        clip_duration = max(1.0, float(duration or 2))
        command = [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=1280x720:r=24:d={clip_duration}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(clip_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0 or not clip_path.exists() or clip_path.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg failed to create mock mp4: {completed.stderr.strip()}")

    def generate_clip(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        request.output_dir.mkdir(parents=True, exist_ok=True)

        cut_name = f"cut_{request.cut_number}"
        clip_path = request.output_dir / f"{cut_name}_mock.mp4"
        prompt_path = request.output_dir / f"{cut_name}_video_prompt.txt"
        manifest_path = request.output_dir / f"{cut_name}_clip_manifest.json"
        created_at = datetime.now(timezone.utc).isoformat()

        prompt_path.write_text(
            "\n\n".join(
                [
                    f"JOB: {request.job_id}",
                    f"CUT: {request.cut_number}",
                    f"PROVIDER: {self.name}",
                    f"DURATION: {request.duration}s",
                    f"IMAGE PATH: {request.image_path}",
                    "IMAGE PROMPT:",
                    request.image_prompt,
                    "CHARACTER LOCK PROMPT:",
                    request.character_lock_prompt,
                    "SCENE CONTEXT PROMPT:",
                    request.scene_context_prompt,
                    "CONTINUITY CONSTRAINTS:",
                    json.dumps(request.continuity_constraints or {}, ensure_ascii=False, indent=2),
                    "MOTION PROMPT:",
                    request.motion_prompt,
                    "NEGATIVE PROMPT:",
                    (
                        "different dog, different breed, different fur color, changed face, long snout, "
                        "oversized body, puppy-like different character, static neutral pose, blank expression, motionless"
                    ),
                ]
            ),
            encoding="utf-8",
        )

        self._write_placeholder_mp4(clip_path, request.duration)

        manifest = {
            "job_id": request.job_id,
            "provider": self.name,
            "status": "mock_completed",
            "created_at": created_at,
            "cut_number": request.cut_number,
            "image_path": request.image_path,
            "image_prompt": request.image_prompt,
            "character_lock_prompt": request.character_lock_prompt,
            "scene_context_prompt": request.scene_context_prompt,
            "continuity_constraints": request.continuity_constraints or {},
            "motion_prompt": request.motion_prompt,
            "duration": request.duration,
            "clip_path": str(clip_path),
            "video_prompt_txt": str(prompt_path),
            "message": "Mock provider created a playable placeholder mp4 file.",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        return VideoGenerationResult(
            job_id=request.job_id,
            status="mock_completed",
            clip_path=str(clip_path),
            provider=self.name,
            message="Mock video clip completed. Playable placeholder mp4 was written to generated_clips.",
            manifest_path=str(manifest_path),
            prompt_path=str(prompt_path),
        )
