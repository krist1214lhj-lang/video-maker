import os
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen

from dotenv import load_dotenv

from .base import ImageGenerationRequest, ImageGenerationResult, ImageProvider

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=".env")
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

DEFAULT_REPLICATE_IMAGE_MODEL = "black-forest-labs/flux-kontext-pro"


class ReplicateImageProvider(ImageProvider):
    name = "replicate"
    default_model = DEFAULT_REPLICATE_IMAGE_MODEL

    def __init__(self, crop_to_16_9):
        self._crop_to_16_9 = crop_to_16_9

    def _public_image_url(self, image_path: Path) -> str:
        public_base_url = (os.getenv("PUBLIC_BASE_URL") or os.getenv("NGROK_URL") or "").strip()
        if not public_base_url:
            raise RuntimeError(
                "Replicate image generation requires PUBLIC_BASE_URL or NGROK_URL for reference images."
            )

        if not public_base_url.startswith(("https://", "http://")):
            raise RuntimeError("PUBLIC_BASE_URL/NGROK_URL must include https:// or http://.")

        if image_path.is_relative_to(PROJECT_ROOT / "generated_images"):
            relative = f"/generated_images/{image_path.name}"
        elif image_path.is_relative_to(PROJECT_ROOT / "reference_characters"):
            rel = image_path.relative_to(PROJECT_ROOT / "reference_characters")
            relative = f"/reference_characters/{rel.as_posix()}"
        else:
            relative = f"/{image_path.name}"

        return urljoin(public_base_url.rstrip("/") + "/", relative.lstrip("/"))

    def _extract_image_output(self, output) -> str:
        if isinstance(output, str):
            return output

        if isinstance(output, list) and output:
            return self._extract_image_output(output[0])

        if isinstance(output, dict):
            for key in ("image", "url", "output"):
                if output.get(key):
                    return self._extract_image_output(output[key])

        if hasattr(output, "url"):
            return str(output.url)

        return str(output)

    def _download_image(self, url: str, destination: Path) -> None:
        with urlopen(url, timeout=120) as response:
            destination.write_bytes(response.read())

    def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        api_token = os.getenv("REPLICATE_API_TOKEN")
        if not api_token:
            return ImageGenerationResult(
                image_url=request.fallback_url,
                status="fallback",
                error="REPLICATE_API_TOKEN이 .env에 설정되어 있지 않습니다.",
                provider=self.name,
            )

        reference_path = request.reference_image_path
        if not reference_path or not reference_path.exists():
            return ImageGenerationResult(
                image_url=request.fallback_url,
                status="fallback",
                error="Replicate reference-image generation requires a saved reference frame.",
                provider=self.name,
            )

        try:
            import replicate
        except ImportError as error:
            return ImageGenerationResult(
                image_url=request.fallback_url,
                status="fallback",
                error=f"replicate package is not installed: {error}",
                provider=self.name,
            )

        model = os.getenv("REPLICATE_IMAGE_MODEL", self.default_model)
        output_path = request.output_path or Path("generated_images") / f"cut_{request.cut_number}.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            reference_url = self._public_image_url(reference_path)
            input_payload = {
                "prompt": request.prompt,
                "input_image": reference_url,
                "aspect_ratio": os.getenv("REPLICATE_IMAGE_ASPECT_RATIO", "16:9"),
            }

            client = replicate.Client(api_token=api_token)
            output = client.run(model, input=input_payload)
            image_url = self._extract_image_output(output)
            if not image_url:
                raise RuntimeError("Replicate image model returned no output URL.")

            self._download_image(image_url, output_path)
            cropped = self._crop_to_16_9(output_path.read_bytes())
            output_path.write_bytes(cropped)

            return ImageGenerationResult(
                image_url=f"/generated_images/{output_path.name}",
                status="generated",
                error=None,
                provider=self.name,
                reference_frame={
                    "provider": self.name,
                    "mode": "flux_kontext_reference",
                    "model": model,
                    "reference_image_path": str(reference_path),
                    "reference_image_url": reference_url,
                    "previous_cut_image_path": str(request.previous_cut_image_path)
                    if request.previous_cut_image_path
                    else None,
                    "continuity_inheritance_strength": request.continuity_inheritance_strength,
                    "inheritance_applied": True,
                    "input": input_payload,
                },
            )
        except Exception as exc:
            return ImageGenerationResult(
                image_url=request.fallback_url,
                status="fallback",
                error=str(exc),
                provider=self.name,
                reference_frame={
                    "provider": self.name,
                    "mode": "flux_kontext_reference",
                    "model": model,
                    "reference_image_path": str(reference_path),
                    "continuity_inheritance_strength": request.continuity_inheritance_strength,
                    "inheritance_applied": False,
                    "error": str(exc),
                },
            )
