import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from .base import VideoGenerationRequest, VideoGenerationResult, VideoProvider
from .media_paths import resolve_local_image_path
from .public_url import (
    REPLICATE_NGROK_REQUIRED_MESSAGE,
    get_public_base_url,
    resolve_replicate_start_image_urls,
    validate_replicate_start_image_url,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=".env")
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

DEFAULT_REPLICATE_MODEL = "kwaivgi/kling-v2.1"
DEFAULT_REPLICATE_MODEL_VERSION = (
    "daad218feb714b03e2a1ac445986aebb9d05243cd00da2af17be2e4049f48f69"
)
REPLICATE_NEGATIVE_PROMPT = (
    "text, watermark, logo, distorted anatomy, flicker, warped geometry, "
    "different dog, different face, different breed, changed muzzle, long snout, different ear shape, "
    "different fur color, different body proportion, oversized dog, puppy-like redesign, random poodle, "
    "inconsistent character, new dog identity, changed eye spacing, changed nose shape, "
    "static neutral pose, blank expression, motionless"
)


class ReplicateVideoProvider(VideoProvider):
    name = "replicate"
    default_model = DEFAULT_REPLICATE_MODEL

    def _resolve_model_ref(self) -> str:
        configured = (os.getenv("REPLICATE_VIDEO_MODEL") or self.default_model).strip()
        if ":" in configured:
            return configured

        version = (os.getenv("REPLICATE_VIDEO_MODEL_VERSION") or DEFAULT_REPLICATE_MODEL_VERSION).strip()
        if version:
            return f"{configured}:{version}"
        return configured

    def _prepare_start_image(self, image_path: str) -> tuple[str, str, Path | None, str, str]:
        local_path = resolve_local_image_path(image_path)
        local_image_url = str(image_path or "").strip()

        if local_path and not local_path.exists():
            raise RuntimeError(
                f"Start image file not found: {local_path}. "
                "Regenerate storyboard images before running Replicate video generation."
            )

        public_base_url = get_public_base_url()
        if public_base_url:
            local_image_url, public_start_image_url, start_image = resolve_replicate_start_image_urls(image_path)
            print(
                f"[replicate] start_image public_url={public_start_image_url} "
                f"local_image_url={local_image_url}",
                flush=True,
            )
            return start_image, "public_url", local_path, local_image_url, public_start_image_url

        parsed = urlparse(local_image_url.split("?", 1)[0])
        if parsed.scheme in {"http", "https"}:
            hostname = (parsed.hostname or "").lower()
            if hostname in {"127.0.0.1", "localhost", "0.0.0.0"}:
                raise RuntimeError(REPLICATE_NGROK_REQUIRED_MESSAGE)
            is_valid, _reason = validate_replicate_start_image_url(local_image_url)
            if not is_valid:
                raise RuntimeError(REPLICATE_NGROK_REQUIRED_MESSAGE)
            return local_image_url, "public_url", local_path, local_image_url, local_image_url

        raise RuntimeError(REPLICATE_NGROK_REQUIRED_MESSAGE)

    def _extract_video_output(self, output) -> str:
        if isinstance(output, str):
            return output

        if isinstance(output, list) and output:
            return self._extract_video_output(output[0])

        if isinstance(output, dict):
            for key in ("video", "url", "output"):
                if output.get(key):
                    value = output[key]
                    return self._extract_video_output(value)

        if hasattr(output, "url"):
            return str(output.url)

        return str(output)

    def _jsonable_output(self, output):
        if isinstance(output, (str, int, float, bool)) or output is None:
            return output

        if isinstance(output, list):
            return [self._jsonable_output(item) for item in output]

        if isinstance(output, dict):
            return {key: self._jsonable_output(value) for key, value in output.items()}

        if hasattr(output, "url"):
            return {"type": output.__class__.__name__, "url": str(output.url)}

        return {"type": output.__class__.__name__, "value": str(output)}

    def _download_file_output(self, output, destination: Path) -> bool:
        if isinstance(output, list) and output:
            return self._download_file_output(output[0], destination)

        if hasattr(output, "read"):
            data = output.read()
            destination.write_bytes(data)
            return destination.stat().st_size > 0

        if not isinstance(output, (str, bytes, dict)):
            try:
                with destination.open("wb") as file:
                    for chunk in output:
                        if isinstance(chunk, str):
                            chunk = chunk.encode("utf-8")
                        file.write(chunk)
                return destination.stat().st_size > 0
            except TypeError:
                return False

        return False

    def _download_url(self, video_url: str, destination: Path) -> bool:
        if not video_url.startswith(("http://", "https://")):
            return False

        from urllib.request import urlopen

        with urlopen(video_url, timeout=180) as response:
            destination.write_bytes(response.read())

        return destination.stat().st_size > 0

    def generate_clip(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        load_dotenv(dotenv_path=".env")
        load_dotenv(dotenv_path=PROJECT_ROOT / ".env")
        api_token = os.getenv("REPLICATE_API_TOKEN")
        if not api_token:
            raise RuntimeError("REPLICATE_API_TOKEN is not configured. Use provider='mock' or add REPLICATE_API_TOKEN to .env.")

        try:
            import replicate
        except ImportError as error:
            raise RuntimeError("replicate package is not installed. Run pip install -r requirements.txt.") from error

        request.output_dir.mkdir(parents=True, exist_ok=True)
        cut_name = f"cut_{request.cut_number}"
        result_path = request.output_dir / f"{cut_name}_replicate_result.json"
        manifest_path = request.output_dir / f"{cut_name}_clip_manifest.json"
        local_clip_path = request.output_dir / f"{cut_name}_replicate.mp4"
        prompt_path = request.output_dir / f"{cut_name}_video_prompt.txt"
        created_at = datetime.now(timezone.utc).isoformat()
        model = self._resolve_model_ref()
        start_image, start_image_source, local_image_path, local_image_url, public_start_image_url = (
            self._prepare_start_image(request.image_path)
        )
        replicate_mode = (os.getenv("REPLICATE_VIDEO_MODE") or "standard").strip() or "standard"
        motion_prompt_text = request.motion_prompt if isinstance(request.motion_prompt, str) else str(request.motion_prompt or "")

        prompt_path.write_text(
            "\n\n".join(
                [
                    f"JOB: {request.job_id}",
                    f"CUT: {request.cut_number}",
                    f"PROVIDER: {self.name}",
                    f"MODEL: {model}",
                    f"MODE: {replicate_mode}",
                    f"DURATION: {request.duration}s",
                    f"START IMAGE SOURCE: {start_image_source}",
                    f"LOCAL IMAGE PATH: {local_image_path or 'none'}",
                    f"LOCAL IMAGE URL: {local_image_url}",
                    f"PUBLIC START IMAGE URL: {public_start_image_url}",
                    f"IMAGE INPUT: {request.image_path}",
                    "IMAGE PROMPT:",
                    request.image_prompt,
                    "CHARACTER LOCK PROMPT:",
                    request.character_lock_prompt,
                    "SCENE CONTEXT PROMPT:",
                    request.scene_context_prompt,
                    "CONTINUITY CONSTRAINTS:",
                    json.dumps(request.continuity_constraints or {}, ensure_ascii=False, indent=2),
                    "MOTION PROMPT:",
                    motion_prompt_text,
                ]
            ),
            encoding="utf-8",
        )

        client = replicate.Client(api_token=api_token)
        replicate_duration = 5 if request.duration <= 5 else 10
        input_payload = {
            "mode": replicate_mode,
            "prompt": motion_prompt_text,
            "start_image": start_image,
            "duration": replicate_duration,
            "negative_prompt": REPLICATE_NEGATIVE_PROMPT,
        }
        print(
            f"[replicate] CUT {request.cut_number} model={model} mode={replicate_mode} "
            f"start_image_source={start_image_source} public_start_image_url={public_start_image_url} "
            f"prompt_len={len(str(request.motion_prompt or ''))}",
            flush=True,
        )
        cut_id = f"cut_{int(request.cut_number):03d}"
        print("[replicate-start]", flush=True)
        print(f"cut_id: {cut_id}", flush=True)
        print(f"start_image: {public_start_image_url or start_image}", flush=True)
        print("[replicate-poll] waiting for model output", flush=True)
        try:
            output = client.run(model, input=input_payload)
            print("[replicate-complete]", flush=True)
        except Exception as error:
            print("[replicate-failed]", flush=True)
            print(
                f"[replicate] generation failed cut={request.cut_number} "
                f"error_type={error.__class__.__name__} error={error}",
                flush=True,
            )
            failure_payload = {
                "job_id": request.job_id,
                "provider": self.name,
                "model": model,
                "status": "failed",
                "created_at": created_at,
                "cut_number": request.cut_number,
                "image_path": request.image_path,
                "local_image_path": str(local_image_path) if local_image_path else "",
                "local_image_url": local_image_url,
                "public_start_image_url": public_start_image_url,
                "start_image_source": start_image_source,
                "image_prompt": request.image_prompt,
                "character_lock_prompt": request.character_lock_prompt,
                "scene_context_prompt": request.scene_context_prompt,
                "continuity_constraints": request.continuity_constraints or {},
                "motion_prompt": request.motion_prompt,
                "duration": request.duration,
                "replicate_duration": replicate_duration,
                "input": {
                    **input_payload,
                    "start_image": public_start_image_url or f"<{start_image_source} omitted>",
                },
                "error_type": error.__class__.__name__,
                "error": str(error),
                "message": "Replicate generation failed. Check model version, token, start image availability, and input schema.",
            }
            result_path.write_text(json.dumps(failure_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            manifest_path.write_text(json.dumps(failure_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            raise RuntimeError(
                "Replicate generation failed. "
                f"model={model}, start_image_source={start_image_source}, error={error}"
            ) from error

        video_output = self._extract_video_output(output)
        download_error = ""
        downloaded = False

        try:
            downloaded = self._download_file_output(output, local_clip_path)
            if not downloaded:
                downloaded = self._download_url(video_output, local_clip_path)
        except Exception as error:
            download_error = f"{error.__class__.__name__}: {error}"
            downloaded = False

        if downloaded and local_clip_path.stat().st_size < 1024:
            download_error = "Downloaded mp4 is too small to be a valid clip."
            downloaded = False

        local_clip_value = str(local_clip_path) if downloaded else ""
        clip_path = local_clip_value or video_output
        status = "replicate_completed" if downloaded else "failed"
        message = "Replicate image-to-video generation completed."
        if downloaded:
            message = (
                f"Replicate completed ({model}) and saved {local_clip_path.name} "
                f"({local_clip_path.stat().st_size} bytes)."
            )
        elif video_output:
            message = "Replicate completed and returned a video URL, but local mp4 download failed. Stored video_url in manifest."
        else:
            message = "Replicate returned no downloadable video output."

        result_payload = {
            "job_id": request.job_id,
            "provider": self.name,
            "model": model,
            "status": status,
            "created_at": created_at,
            "cut_number": request.cut_number,
            "image_path": request.image_path,
            "local_image_path": str(local_image_path) if local_image_path else "",
            "local_image_url": local_image_url,
            "public_start_image_url": public_start_image_url,
            "start_image_source": start_image_source,
            "image_prompt": request.image_prompt,
            "character_lock_prompt": request.character_lock_prompt,
            "scene_context_prompt": request.scene_context_prompt,
            "continuity_constraints": request.continuity_constraints or {},
            "motion_prompt": request.motion_prompt,
            "duration": request.duration,
            "replicate_duration": replicate_duration,
            "input": {
                **input_payload,
                "start_image": public_start_image_url or f"<{start_image_source} omitted>",
            },
            "raw_response": self._jsonable_output(output),
            "video_output": video_output,
            "video_url": video_output,
            "local_clip_path": local_clip_value,
            "downloaded": downloaded,
            "download_error": download_error,
        }
        result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        manifest = {
            **result_payload,
            "result_file": str(result_path),
            "video_prompt_txt": str(prompt_path),
            "clip_path": clip_path,
            "message": message,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        if status != "replicate_completed":
            raise RuntimeError(message)

        return VideoGenerationResult(
            job_id=request.job_id,
            status=status,
            clip_path=clip_path,
            provider=self.name,
            message=message,
            manifest_path=str(manifest_path),
            prompt_path=str(prompt_path),
        )
