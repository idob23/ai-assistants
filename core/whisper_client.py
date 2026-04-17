"""Транскрипция голосовых сообщений.

Использует faster-whisper для локальной транскрипции аудио:
- ленивая загрузка модели при первом вызове
- async-обёртка для использования в asyncio-ботах
- поддержка bytes (для Telegram voice messages)
"""

import asyncio
import logging
import os
import tempfile
import time

from faster_whisper import WhisperModel

from core.config import get_settings

log = logging.getLogger(__name__)


class WhisperClient:

    def __init__(self, model_size: str = "small", device: str = "cpu",
                 compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model: WhisperModel | None = None

    def _ensure_model(self):
        if self._model is not None:
            return
        log.info("Loading whisper model: size=%s device=%s compute_type=%s",
                 self.model_size, self.device, self.compute_type)
        start = time.monotonic()
        self._model = WhisperModel(
            self.model_size, device=self.device, compute_type=self.compute_type,
        )
        elapsed = time.monotonic() - start
        log.info("Whisper model loaded in %.1fs", elapsed)

    def _transcribe_sync(self, audio_path: str, language: str) -> str:
        self._ensure_model()
        start = time.monotonic()
        segments, info = self._model.transcribe(audio_path, language=language)
        parts = []
        for segment in segments:
            parts.append(segment.text)
        elapsed = time.monotonic() - start
        log.info(
            "Transcription done: duration=%.1fs time=%.1fs segments=%d",
            info.duration, elapsed, len(parts),
        )
        return " ".join(parts).strip()

    async def transcribe(self, audio_path: str, language: str = "ru") -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio_path, language)

    async def transcribe_bytes(self, audio_data: bytes,
                               suffix: str = ".ogg",
                               language: str = "ru") -> str:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            tmp.write(audio_data)
            tmp.close()
            return await self.transcribe(tmp.name, language)
        finally:
            os.unlink(tmp.name)


def create_whisper_client() -> WhisperClient:
    settings = get_settings()
    return WhisperClient(model_size=settings.WHISPER_MODEL)
