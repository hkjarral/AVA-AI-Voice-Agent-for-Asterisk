from backends.registry import STT_REGISTRY
from backends.stt.vosk_backend import VoskBackend
from backends.stt.sherpa_backend import SherpaBackend
from backends.stt.kroko_backend import KrokoBackend
from backends.stt.faster_whisper_backend import FasterWhisperBackend
from backends.stt.whisper_cpp_backend import WhisperCppBackend
from backends.stt.tone_backend import ToneBackend

STT_REGISTRY.register(VoskBackend)
STT_REGISTRY.register(SherpaBackend)
STT_REGISTRY.register(KrokoBackend)
STT_REGISTRY.register(FasterWhisperBackend)
STT_REGISTRY.register(WhisperCppBackend)
STT_REGISTRY.register(ToneBackend)
