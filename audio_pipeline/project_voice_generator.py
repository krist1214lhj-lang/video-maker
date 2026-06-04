from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_TTS_VOICE = "alloy"


@dataclass(frozen=True)
class ProjectVoiceCutInput:
    cut_number: int
    narration: str
    duration: int = 4


@dataclass(frozen=True)
class ProjectVoiceResult:
    cut_number: int
    status: str
    text: str
    mp3_path: str
    public_url: str
    duration_seconds: float
    provider: str = "mock"


class ProjectVoiceGenerator:
    """script.json narration을 OpenAI TTS mp3로 생성하고, 실패 시 mock으로 fallback."""

    def __init__(
        self,
        audio_dir: Path,
        *,
        public_url_builder,
        api_key: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        preferred_provider: str = "openai",
    ):
        self.audio_dir = audio_dir
        self.public_url_builder = public_url_builder
        self.api_key = (api_key if api_key is not None else os.getenv("OPENAI_API_KEY") or "").strip()
        self.model = (model or os.getenv("OPENAI_TTS_MODEL") or DEFAULT_TTS_MODEL).strip()
        self.voice = (voice or os.getenv("OPENAI_TTS_VOICE") or DEFAULT_TTS_VOICE).strip()
        self.preferred_provider = (preferred_provider or "openai").strip().lower()
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._openai_client = None

    def _get_openai_client(self):
        if not self.api_key:
            return None
        if self._openai_client is None:
            from openai import OpenAI

            self._openai_client = OpenAI(api_key=self.api_key)
        return self._openai_client

    def generate_cut(self, cut: ProjectVoiceCutInput) -> ProjectVoiceResult:
        text = cut.narration.strip()
        if not text:
            raise ValueError(f"CUT {cut.cut_number} narration text is empty.")

        file_stem = f"narration_{cut.cut_number:02d}"
        mp3_path = self.audio_dir / f"{file_stem}.mp3"
        sidecar_path = self.audio_dir / f"{file_stem}.json"
        fallback_duration = float(max(cut.duration, 1))

        openai_error: str | None = None
        if self.preferred_provider == "mock":
            return self._generate_mock_cut(
                cut=cut,
                text=text,
                mp3_path=mp3_path,
                sidecar_path=sidecar_path,
                fallback_duration=fallback_duration,
                openai_error=None,
            )
        if self._get_openai_client():
            try:
                return self._generate_openai_cut(
                    cut=cut,
                    text=text,
                    mp3_path=mp3_path,
                    sidecar_path=sidecar_path,
                    fallback_duration=fallback_duration,
                )
            except Exception as exc:  # noqa: BLE001 - per-cut fallback to mock
                openai_error = str(exc)
                logger.warning(
                    "OpenAI TTS failed for cut %s, falling back to mock: %s",
                    cut.cut_number,
                    openai_error,
                )

        if self.preferred_provider == "openai":
            raise RuntimeError(openai_error or "OPENAI_API_KEY is not configured.")

        return self._generate_mock_cut(
            cut=cut,
            text=text,
            mp3_path=mp3_path,
            sidecar_path=sidecar_path,
            fallback_duration=fallback_duration,
            openai_error=openai_error,
        )

    def _generate_openai_cut(
        self,
        *,
        cut: ProjectVoiceCutInput,
        text: str,
        mp3_path: Path,
        sidecar_path: Path,
        fallback_duration: float,
    ) -> ProjectVoiceResult:
        client = self._get_openai_client()
        if client is None:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        response = client.audio.speech.create(
            model=self.model,
            voice=self.voice,
            input=text,
            response_format="mp3",
        )
        mp3_path.write_bytes(response.content)

        duration_seconds = probe_mp3_duration(mp3_path, fallback_duration)
        sidecar = {
            "cut_number": cut.cut_number,
            "text": text,
            "duration_seconds": duration_seconds,
            "provider": "openai",
            "model": self.model,
            "voice": self.voice,
            "status": "completed",
            "mp3_path": str(mp3_path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")

        return ProjectVoiceResult(
            cut_number=cut.cut_number,
            status="completed",
            text=text,
            mp3_path=str(mp3_path),
            public_url=self.public_url_builder(mp3_path),
            duration_seconds=duration_seconds,
            provider="openai",
        )

    def _generate_mock_cut(
        self,
        *,
        cut: ProjectVoiceCutInput,
        text: str,
        mp3_path: Path,
        sidecar_path: Path,
        fallback_duration: float,
        openai_error: str | None = None,
    ) -> ProjectVoiceResult:
        placeholder = (
            f"MOCK_VOICE cut={cut.cut_number}\n"
            f"text={text}\n"
            f"duration={cut.duration}\n"
        )
        mp3_path.write_bytes(placeholder.encode("utf-8"))

        sidecar = {
            "cut_number": cut.cut_number,
            "text": text,
            "duration_seconds": fallback_duration,
            "provider": "mock",
            "status": "mock_completed",
            "mp3_path": str(mp3_path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        if openai_error:
            sidecar["openai_error"] = openai_error

        sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")

        return ProjectVoiceResult(
            cut_number=cut.cut_number,
            status="mock_completed",
            text=text,
            mp3_path=str(mp3_path),
            public_url=self.public_url_builder(mp3_path),
            duration_seconds=fallback_duration,
            provider="mock",
        )

    def generate_for_cuts(self, cuts: list[ProjectVoiceCutInput]) -> list[ProjectVoiceResult]:
        return [self.generate_cut(cut) for cut in cuts]


def probe_mp3_duration(mp3_path: Path, default: float) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not mp3_path.exists():
        return default

    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(mp3_path.resolve()),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return default

    try:
        return max(float(result.stdout.strip()), 0.5)
    except (TypeError, ValueError):
        return default
