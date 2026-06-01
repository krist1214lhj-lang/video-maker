from .base import ImageGenerationRequest, ImageGenerationResult, ImageProvider


class MockImageProvider(ImageProvider):
    name = "mock"

    def __init__(self, crop_to_16_9):
        self._crop_to_16_9 = crop_to_16_9

    def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        return ImageGenerationResult(
            image_url=request.fallback_url,
            status="mock",
            error=None,
            provider=self.name,
            reference_frame={
                "provider": self.name,
                "mode": "placeholder",
                "inheritance_applied": False,
            },
        )
