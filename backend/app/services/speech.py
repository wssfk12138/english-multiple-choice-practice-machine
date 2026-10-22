from __future__ import annotations

import hashlib
from collections import OrderedDict
import importlib.util
import io
import os
import re
import struct
import threading
import wave
from pathlib import Path

from ..config import ROOT_DIR


TTS_DIR_ENV = "ENGLISH_PRACTICE_TTS_DIR"
ACCENT_DIRS = {"us": "en_US", "gb": "en_GB"}
ALLOWED_ACCENTS = ('us',)
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_TEXT_CHARS = 80
MAX_WORDS = 5
TEXT_PATTERN = re.compile(r"[A-Za-z][A-Za-z'’ .,!?-]*")


class SpeechError(Exception):
    def __init__(self, message: str, status_code: int = 400, code: str = "speech_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class SpeechUnavailable(SpeechError):
    def __init__(self, message: str = "离线发音资源不可用") -> None:
        super().__init__(message, 503, "speech_unavailable")


class SpeechAccentUnavailable(SpeechError):
    def __init__(self, accent: str) -> None:
        super().__init__(f"当前版本不提供 {accent} 口音", 503, "speech_accent_unavailable")


class SpeechService:
    """Lazy, single-worker Sherpa-ONNX synthesis for the local FastAPI app."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self._lock = threading.Lock()
        self._engines: dict[str, tuple[str, object]] = {}
        self._state_lock = threading.Lock()
        self._latest = 0
        self._cache: OrderedDict[tuple[str, str, str], dict[str, object]] = OrderedDict()
        self._cache_bytes = 0
        self._fingerprints: dict[str, tuple[tuple[int, ...], str]] = {}

    @property
    def root(self) -> Path:
        if self._root is not None:
            return self._root
        explicit = os.environ.get(TTS_DIR_ENV, "").strip()
        return Path(explicit).expanduser().resolve() if explicit else (ROOT_DIR / "tts")

    def _directory(self, accent: str) -> Path:
        return self.root / ACCENT_DIRS[accent]

    def _available(self, accent: str) -> bool:
        directory = self._directory(accent)
        return accent in ALLOWED_ACCENTS and (
            (directory / "model.onnx").is_file()
            and (directory / "tokens.txt").is_file()
            and (directory / "espeak-ng-data").is_dir()
        )

    def status(self) -> dict[str, object]:
        engine_ready = importlib.util.find_spec('sherpa_onnx') is not None
        accents = [accent for accent in ALLOWED_ACCENTS if engine_ready and self._available(accent)]
        return {
            "engine": "sherpa-onnx",
            "version": "1.13.8",
            "accents": accents,
            "available": bool(accents),
        }

    def _fingerprint(self, accent: str) -> str:
        files = [self._directory(accent) / name for name in ('model.onnx', 'tokens.txt')]
        stats = tuple(value for file in files for value in (file.stat().st_size, file.stat().st_mtime_ns))
        saved = self._fingerprints.get(accent)
        if saved and saved[0] == stats:
            return saved[1]
        digest = hashlib.sha256()
        for file in files:
            with file.open('rb') as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(block)
        fingerprint = digest.hexdigest()
        self._fingerprints[accent] = (stats, fingerprint)
        return fingerprint

    def _load(self, accent: str):
        if importlib.util.find_spec("sherpa_onnx") is None:
            raise SpeechUnavailable("服务器缺少离线发音引擎")
        import sherpa_onnx

        directory = self._directory(accent)
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(directory / "model.onnx"),
                    tokens=str(directory / "tokens.txt"),
                    data_dir=str(directory / "espeak-ng-data"),
                    lexicon="",
                ),
                num_threads=2,
                provider="cpu",
            ),
            max_num_sentences=1,
        )
        try:
            return sherpa_onnx.OfflineTts(config)
        except Exception as exc:
            raise SpeechUnavailable("离线发音引擎初始化失败") from exc

    def _validate_text(self, text: str) -> str:
        value = " ".join((text or "").split())
        if not value:
            raise SpeechError("请输入要朗读的单词或短语", 400, "speech_text_empty")
        if len(value) > MAX_TEXT_CHARS:
            raise SpeechError(f"朗读文本不能超过 {MAX_TEXT_CHARS} 个字符", 400, "speech_text_too_long")
        if len(value.split()) > MAX_WORDS:
            raise SpeechError(f"朗读文本不能超过 {MAX_WORDS} 个词", 400, "speech_text_too_long")
        if any(character in value for character in "<>/\\{}[]$`") or not TEXT_PATTERN.fullmatch(value):
            raise SpeechError("朗读文本包含不支持的字符", 400, "speech_text_invalid")
        return value

    def synthesize(self, text: str, accent: str = "us") -> dict[str, object]:
        value = self._validate_text(text)
        if accent not in ACCENT_DIRS:
            raise SpeechError("不支持的口音", 400, "speech_accent_invalid")
        if not self._available(accent):
            raise SpeechAccentUnavailable(accent)
        # Only the newest waiting request may enter the inference lock. The
        # running native call cannot be interrupted, but obsolete waiters do no
        # inference and their responses can never claim to be the current clip.
        with self._state_lock:
            self._latest += 1
            ticket = self._latest
        with self._lock:
            with self._state_lock:
                if ticket != self._latest:
                    raise SpeechError("已切换到新的发音", 409, "speech_superseded")
            fingerprint = self._fingerprint(accent)
            cache_key = (fingerprint, accent, value)
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return cached
            loaded = self._engines.get(accent)
            if loaded is None or loaded[0] != fingerprint:
                loaded = (fingerprint, self._load(accent))
                self._engines[accent] = loaded
            engine = loaded[1]
            try:
                audio = engine.generate(value, sid=0, speed=1.0)
            except Exception as exc:
                raise SpeechUnavailable("离线发音合成失败") from exc
            samples = list(audio.samples)
            if not samples or audio.sample_rate <= 0:
                raise SpeechUnavailable("离线发音没有返回有效音频")
            pcm = b"".join(
                struct.pack("<h", int(max(-1.0, min(1.0, float(sample))) * 32767))
                for sample in samples
            )
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(audio.sample_rate)
                handle.writeframes(pcm)
            result = {
                "audio": buffer.getvalue(),
                "sample_rate": audio.sample_rate,
                "samples": len(samples),
                "accent": accent,
            }
            size = len(result['audio'])
            if size <= MAX_CACHE_BYTES:
                self._cache[cache_key] = result
                self._cache_bytes += size
                while self._cache_bytes > MAX_CACHE_BYTES:
                    _, evicted = self._cache.popitem(last=False)
                    self._cache_bytes -= len(evicted['audio'])
            with self._state_lock:
                if ticket != self._latest:
                    raise SpeechError("已切换到新的发音", 409, "speech_superseded")
            return result


_services: dict[str, SpeechService] = {}
_services_lock = threading.Lock()


def get_speech_service() -> SpeechService:
    service = SpeechService()
    key = str(service.root)
    with _services_lock:
        return _services.setdefault(key, service)
