from backends.registry import TTS_REGISTRY
from backends.tts.piper_backend import PiperBackend
from backends.tts.kokoro_backend import KokoroBackend
from backends.tts.melotts_backend import MeloTTSBackend
from backends.tts.silero_backend import SileroBackend

TTS_REGISTRY.register(PiperBackend)
TTS_REGISTRY.register(KokoroBackend)
TTS_REGISTRY.register(MeloTTSBackend)
TTS_REGISTRY.register(SileroBackend)
