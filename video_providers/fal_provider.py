import os

from .base import VideoGenerationRequest, VideoGenerationResult, VideoProvider


class FalVideoProvider(VideoProvider):
    name = "fal"

    def generate_clip(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        if not os.getenv("FAL_KEY"):
            raise RuntimeError("FAL_KEY is not configured. Use provider='mock' or add FAL_KEY to .env.")

        raise NotImplementedError("fal.ai image-to-video adapter is scaffolded but not implemented yet.")
