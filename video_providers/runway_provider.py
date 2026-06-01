import os

from .base import VideoGenerationRequest, VideoGenerationResult, VideoProvider


class RunwayVideoProvider(VideoProvider):
    name = "runway"

    def generate_clip(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        if not os.getenv("RUNWAY_API_KEY"):
            raise RuntimeError("RUNWAY_API_KEY is not configured. Use provider='mock' or add RUNWAY_API_KEY to .env.")

        raise NotImplementedError("Runway image-to-video adapter is scaffolded but not implemented yet.")
