from .base import ImageGenerationRequest, ImageGenerationResult, ImageProvider
from .mock_provider import MockImageProvider
from .openai_provider import OpenAIImageProvider
from .replicate_provider import ReplicateImageProvider


def get_image_provider(name: str | None, *, crop_to_16_9) -> ImageProvider:
    provider_name = (name or "openai").strip().lower()
    providers: dict[str, ImageProvider] = {
        "openai": OpenAIImageProvider(crop_to_16_9),
        "replicate": ReplicateImageProvider(crop_to_16_9),
        "mock": MockImageProvider(crop_to_16_9),
    }

    if provider_name not in providers:
        supported = ", ".join(sorted(providers))
        raise ValueError(f"Unsupported image provider '{provider_name}'. Supported providers: {supported}")

    return providers[provider_name]
