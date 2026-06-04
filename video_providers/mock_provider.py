import json
import shutil
import subprocess
from datetime import datetime, timezone

from .base import VideoGenerationRequest, VideoGenerationResult, VideoProvider
from .media_paths import resolve_local_image_path


class MockVideoProvider(VideoProvider):
    name = "mock"

    def _write_image_still_mp4(self, clip_path, duration: float, image_path: str, cut_number: int) -> str:
        local_image = resolve_local_image_path(image_path)
        if local_image is None or not local_image.is_file() or local_image.stat().st_size <= 0:
            raise RuntimeError(
                f"Cut {cut_number} 이미지가 없어 Mock 영상을 만들 수 없습니다. 먼저 이미지를 생성해주세요."
            )

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise RuntimeError("ffmpeg is required to create a playable mock mp4, but it was not found in PATH.")

        clip_duration = max(1.0, float(duration or 2))
        command = [
            ffmpeg_path,
            "-y",
            "-loop",
            "1",
            "-i",
            str(local_image),
            "-t",
            f"{clip_duration:.3f}",
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black",
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
            raise RuntimeError(
                f"Cut {cut_number} Mock 영상 생성에 실패했습니다: {completed.stderr.strip()}"
            )
        return str(local_image)

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

        source_image_path = self._write_image_still_mp4(
            clip_path,
            request.duration,
            request.image_path,
            request.cut_number,
        )

        manifest = {
            "job_id": request.job_id,
            "provider": self.name,
            "status": "mock_completed",
            "created_at": created_at,
            "cut_number": request.cut_number,
            "image_path": request.image_path,
            "source_image_path": source_image_path,
            "image_prompt": request.image_prompt,
            "character_lock_prompt": request.character_lock_prompt,
            "scene_context_prompt": request.scene_context_prompt,
            "continuity_constraints": request.continuity_constraints or {},
            "motion_prompt": request.motion_prompt,
            "duration": request.duration,
            "clip_path": str(clip_path),
            "video_prompt_txt": str(prompt_path),
            "message": "Mock provider created a still-image mp4 from the cut image.",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        return VideoGenerationResult(
            job_id=request.job_id,
            status="mock_completed",
            clip_path=str(clip_path),
            provider=self.name,
            message="Mock video clip completed. Still-image mp4 was written to generated_clips.",
            manifest_path=str(manifest_path),
            prompt_path=str(prompt_path),
        )
