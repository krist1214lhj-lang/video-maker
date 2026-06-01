import base64
import os
from io import BytesIO
from pathlib import Path

from openai import OpenAI

from .base import ImageGenerationRequest, ImageGenerationResult, ImageProvider
from .reference_image import (
    is_openai_reference_image_error,
    prepare_openai_reference_image,
)


class OpenAIImageProvider(ImageProvider):
    name = "openai"
    PROMPT_ONLY_FALLBACK_MESSAGE = "reference image fallback: prompt-only"

    def __init__(self, crop_to_16_9):
        self._crop_to_16_9 = crop_to_16_9

    def _decode_result_image(self, result) -> bytes | None:
        image_data = result.data[0]
        if image_data.b64_json:
            return base64.b64decode(image_data.b64_json)
        if image_data.url:
            from urllib.request import urlopen

            with urlopen(image_data.url, timeout=60) as response:
                return response.read()
        return None

    def _call_openai_generate(self, client, model: str, prompt: str):
        return client.images.generate(
            model=model,
            prompt=prompt,
            n=1,
            size="1536x1024",
            quality=os.getenv("OPENAI_IMAGE_QUALITY", "medium"),
            output_format="png",
        )

    def _call_openai_edit(self, client, model: str, prompt: str, reference_file_path: Path):
        with reference_file_path.open("rb") as reference_file:
            return client.images.edit(
                model=model,
                image=reference_file,
                prompt=prompt,
                n=1,
                size="1536x1024",
                quality=os.getenv("OPENAI_IMAGE_QUALITY", "medium"),
                output_format="png",
            )

    def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return ImageGenerationResult(
                image_url=request.fallback_url,
                status="fallback",
                error="OPENAI_API_KEY가 .env에 설정되어 있지 않습니다.",
                provider=self.name,
            )

        output_path = request.output_path or Path("generated_images") / f"cut_{request.cut_number}.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5")

        reference_source = request.reference_image_path
        prepared_reference: Path | None = None
        character_name = reference_source.parent.name if reference_source else ""

        if reference_source and reference_source.exists():
            prepared_reference, conversion_error, _meta = prepare_openai_reference_image(
                reference_source,
                character_name=character_name,
            )
            if prepared_reference is None:
                label = character_name or "reference"
                return ImageGenerationResult(
                    image_url=request.fallback_url,
                    status="fallback",
                    error=f"{label} 레퍼런스 이미지를 OpenAI용 PNG로 변환하지 못했습니다.",
                    provider=self.name,
                    reference_frame={
                        "provider": self.name,
                        "mode": "reference_conversion_failed",
                        "reference_image_path": str(reference_source),
                        "conversion_error": conversion_error,
                    },
                )

        mode = "generate"
        reference_fallback = False
        warning: str | None = None

        try:
            client = OpenAI(api_key=api_key)

            if prepared_reference is not None:
                try:
                    result = self._call_openai_edit(client, model, request.prompt, prepared_reference)
                    mode = "edit_with_reference"
                    print(f"[reference] {character_name or 'bposik_v2'} image reference used")
                except Exception as exc:
                    if is_openai_reference_image_error(exc):
                        print(
                            f"[reference] OpenAI rejected converted reference for CUT {request.cut_number}; "
                            f"retrying prompt-only: {exc}"
                        )
                        print("[reference] prompt-only fallback")
                        result = self._call_openai_generate(client, model, request.prompt)
                        mode = "generate_prompt_only_fallback"
                        reference_fallback = True
                        warning = self.PROMPT_ONLY_FALLBACK_MESSAGE
                    else:
                        raise
            else:
                print("[reference] prompt-only fallback")
                result = self._call_openai_generate(client, model, request.prompt)

            raw_bytes = self._decode_result_image(result)
            if raw_bytes is None:
                return ImageGenerationResult(
                    image_url=request.fallback_url,
                    status="fallback",
                    error="이미지 응답에 b64_json 또는 url이 없습니다.",
                    provider=self.name,
                )

            image_bytes = self._crop_to_16_9(raw_bytes)
            output_path.write_bytes(image_bytes)
            return ImageGenerationResult(
                image_url=f"/generated_images/{output_path.name}",
                status="generated",
                error=warning,
                provider=self.name,
                reference_frame={
                    "provider": self.name,
                    "mode": mode,
                    "reference_image_path": str(reference_source) if reference_source else None,
                    "openai_reference_path": str(prepared_reference) if prepared_reference else None,
                    "previous_cut_image_path": str(request.previous_cut_image_path)
                    if request.previous_cut_image_path
                    else None,
                    "continuity_inheritance_strength": request.continuity_inheritance_strength,
                    "inheritance_applied": bool(prepared_reference and mode == "edit_with_reference"),
                    "reference_fallback": warning if reference_fallback else None,
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
                    "mode": mode,
                    "reference_image_path": str(reference_source) if reference_source else None,
                    "openai_reference_path": str(prepared_reference) if prepared_reference else None,
                },
            )
