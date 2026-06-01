from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VideoGenerationRequest:
    job_id: str
    cut_number: int
    image_path: str
    image_prompt: str
    motion_prompt: str
    duration: float
    output_dir: Path
    character_lock_prompt: str = ""
    scene_context_prompt: str = ""
    continuity_constraints: dict | None = None


@dataclass(frozen=True)
class VideoGenerationResult:
    job_id: str
    status: str
    clip_path: str
    provider: str
    message: str
    manifest_path: str = ""
    prompt_path: str = ""


class VideoProvider(ABC):
    name: str

    @abstractmethod
    def generate_clip(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Create or queue an image-to-video clip generation job."""
