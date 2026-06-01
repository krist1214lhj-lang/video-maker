from .base import VideoGenerationRequest, VideoGenerationResult, VideoProvider
from .fal_provider import FalVideoProvider
from .mock_provider import MockVideoProvider
from .replicate_provider import ReplicateVideoProvider
from .runway_provider import RunwayVideoProvider


def get_video_provider(name: str | None) -> VideoProvider:
    provider_name = (name or "mock").strip().lower()
    providers: dict[str, VideoProvider] = {
        "mock": MockVideoProvider(),
        "fal": FalVideoProvider(),
        "fal.ai": FalVideoProvider(),
        "replicate": ReplicateVideoProvider(),
        "runway": RunwayVideoProvider(),
    }

    if provider_name not in providers:
        supported = ", ".join(sorted(providers))
        raise ValueError(f"Unsupported video provider '{provider_name}'. Supported providers: {supported}")

    return providers[provider_name]
