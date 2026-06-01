from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ContinuityInheritanceStrength = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class ImageGenerationRequest:
    cut_number: int
    prompt: str
    fallback_url: str
    reference_image_path: Path | None = None
    previous_cut_image_path: Path | None = None
    continuity_inheritance_strength: ContinuityInheritanceStrength = "medium"
    output_path: Path | None = None


@dataclass(frozen=True)
class ImageGenerationResult:
    image_url: str
    status: str
    error: str | None
    provider: str
    reference_frame: dict = field(default_factory=dict)


class ImageProvider(ABC):
    name: str

    @abstractmethod
    def generate_image(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """Generate or edit a storyboard frame image."""
