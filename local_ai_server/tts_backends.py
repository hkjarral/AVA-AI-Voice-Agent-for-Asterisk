from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.parse import urlsplit, urlunsplit


class KokoroTTSBackend:
    """Kokoro TTS backend using the kokoro package."""

    def __init__(
        self, voice: str = "af_heart", lang_code: str = "a", model_path: Optional[str] = None
    ):
        self.voice = voice
        self.lang_code = lang_code
        self.model_path = model_path
        self.pipeline = None
        self._initialized = False
        self.sample_rate = 24000

    def initialize(self) -> bool:
        try:
            from kokoro import KPipeline
            from kokoro.model import KModel

            logging.info(
                "🎙️ KOKORO - Initializing TTS (voice=%s, lang=%s)",
                self.voice,
                self.lang_code,
            )

            if self.model_path and os.path.isdir(self.model_path):
                config_path = os.path.join(self.model_path, "config.json")
                model_path = os.path.join(self.model_path, "kokoro-v1_0.pth")

                if os.path.exists(config_path) and os.path.exists(model_path):
                    logging.info(
                        "🎙️ KOKORO - Loading local model from %s", self.model_path
                    )
                    kmodel = KModel(
                        config=config_path,
                        model=model_path,
                        repo_id="hexgrad/Kokoro-82M",
                    )
                    self.pipeline = KPipeline(
                        lang_code=self.lang_code,
                        model=kmodel,
                        repo_id="hexgrad/Kokoro-82M",
                    )
                else:
                    logging.warning(
                        "⚠️ KOKORO - Local model files not found, falling back to HuggingFace"
                    )
                    self.pipeline = KPipeline(
                        lang_code=self.lang_code, repo_id="hexgrad/Kokoro-82M"
                    )
            else:
                logging.info(
                    "🎙️ KOKORO - Using HuggingFace model (will download if needed)"
                )
                self.pipeline = KPipeline(
                    lang_code=self.lang_code, repo_id="hexgrad/Kokoro-82M"
                )

            self._initialized = True
            logging.info("✅ KOKORO - TTS initialized successfully")
            return True
        except ImportError as exc:
            # This can be a true missing dependency OR a CUDA/native import failure.
            logging.error("❌ KOKORO - Import failed: %s", exc, exc_info=True)
            return False
        except Exception as exc:
            logging.error("❌ KOKORO - Failed to initialize: %s", exc)
            return False

    def synthesize(self, text: str) -> bytes:
        if not self._initialized or not self.pipeline:
            logging.error("❌ KOKORO - Not initialized")
            return b""

        try:
            import numpy as np

            audio_chunks = []
            generator = self.pipeline(text, voice=self.voice)

            for _, (_, _, audio) in enumerate(generator):
                if audio is not None:
                    audio_chunks.append(audio)

            if not audio_chunks:
                logging.warning("⚠️ KOKORO - No audio generated")
                return b""

            full_audio = np.concatenate(audio_chunks)
            audio_int16 = (full_audio * 32767).astype(np.int16)

            logging.debug(
                "🎙️ KOKORO - Generated %d samples at %dHz",
                len(audio_int16),
                self.sample_rate,
            )
            return audio_int16.tobytes()
        except Exception as exc:
            logging.error("❌ KOKORO - Synthesis failed: %s", exc)
            return b""

    def shutdown(self) -> None:
        self.pipeline = None
        self._initialized = False
        logging.info("🛑 KOKORO - TTS shutdown")


class MeloTTSBackend:
    """
    MeloTTS backend - lightweight, CPU-optimized text-to-speech.
    
    Features:
    - Multiple English accents (US, British, Australian, Indian, Default)
    - Optimized for CPU inference
    - Low latency for real-time applications
    - Sample rate: 44100 Hz (resampled to target rate)
    
    Docs: https://github.com/myshell-ai/MeloTTS
    """
    
    # Voice/accent mapping
    VOICES = {
        "EN-US": "EN-US",      # American English
        "EN-BR": "EN-BR",      # British English  
        "EN-AU": "EN-AU",      # Australian English
        "EN-IN": "EN_INDIA",   # Indian English (note: different format)
        "EN-Default": "EN-Default",  # Default English
    }
    
    def __init__(
        self,
        voice: str = "EN-US",
        device: str = "cpu",
        speed: float = 1.0,
    ):
        """
        Initialize MeloTTS backend.
        
        Args:
            voice: Voice/accent to use (EN-US, EN-BR, EN-AU, EN-IN, EN-Default)
            device: Device to use (cpu or cuda)
            speed: Speech speed multiplier (1.0 = normal)
        """
        self.voice = voice
        self.device = device
        self.speed = speed
        self.model = None
        self.speaker_ids = None
        self._initialized = False
        self.sample_rate = 44100  # MeloTTS native rate
    
    def initialize(self) -> bool:
        """Initialize the MeloTTS model."""
        try:
            from melo.api import TTS
            self._patch_legacy_download_urls()
            
            logging.info(
                "🎙️ MELOTTS - Initializing (voice=%s, device=%s, speed=%.1f)",
                self.voice, self.device, self.speed
            )
            
            # Map voice to language code for model loading
            # MeloTTS uses language codes like 'EN' for English models
            lang = "EN"  # All our voices are English variants
            
            self.model = TTS(language=lang, device=self.device)
            # spk2id might be dict or HParams - convert to dict
            spk2id_raw = self.model.hps.data.spk2id
            if hasattr(spk2id_raw, '__dict__'):
                # HParams object - convert to dict
                self.speaker_ids = dict(spk2id_raw.__dict__) if hasattr(spk2id_raw, '__dict__') else {}
            elif isinstance(spk2id_raw, dict):
                self.speaker_ids = spk2id_raw
            else:
                # Fallback: try to iterate
                self.speaker_ids = {k: getattr(spk2id_raw, k) for k in dir(spk2id_raw) if not k.startswith('_')}
            
            logging.debug("🎙️ MELOTTS - Speaker IDs: %s", self.speaker_ids)
            
            # Verify the voice exists
            voice_key = self.VOICES.get(self.voice, self.voice)
            if voice_key not in self.speaker_ids:
                available = list(self.speaker_ids.keys())
                logging.warning(
                    "⚠️ MELOTTS - Voice '%s' not found, available: %s. Using first available.",
                    voice_key, available
                )
                self.voice = available[0] if available else "EN-US"
            
            self._initialized = True
            logging.info("✅ MELOTTS - TTS initialized successfully")
            return True
            
        except ImportError:
            logging.error("❌ MELOTTS - melo package not installed")
            return False
        except Exception as exc:
            logging.error("❌ MELOTTS - Failed to initialize: %s", exc)
            return False

    @staticmethod
    def _patch_legacy_download_urls() -> None:
        try:
            import melo.download_utils as download_utils
        except Exception:
            return

        legacy_host = "myshell-public-repo-hosting.s3.amazonaws.com"
        replacement_host = "myshell-public-repo-host.s3.amazonaws.com"
        patched = 0
        for attr in ("DOWNLOAD_CKPT_URLS", "DOWNLOAD_CONFIG_URLS", "PRETRAINED_MODELS"):
            value = getattr(download_utils, attr, None)
            if isinstance(value, dict):
                for key, url in list(value.items()):
                    if not isinstance(url, str):
                        continue
                    parsed = urlsplit(url)
                    if parsed.netloc != legacy_host:
                        continue
                    value[key] = urlunsplit(parsed._replace(netloc=replacement_host))
                    patched += 1

        if patched:
            logging.info("🎙️ MELOTTS - Patched %d legacy download URLs", patched)
    
    def synthesize(self, text: str) -> bytes:
        """
        Synthesize speech from text.
        
        Args:
            text: Text to synthesize
            
        Returns:
            Audio as PCM16 bytes at native sample rate (44100 Hz)
        """
        if not self._initialized or self.model is None:
            logging.error("❌ MELOTTS - Not initialized")
            return b""
        
        try:
            import numpy as np
            
            # Get speaker ID for the voice
            voice_key = self.VOICES.get(self.voice, self.voice)
            speaker_id = self.speaker_ids.get(voice_key, 0)
            
            # Generate audio
            audio = self.model.tts_to_file(
                text,
                speaker_id,
                None,  # Don't save to file
                speed=self.speed,
            )
            
            if audio is None or len(audio) == 0:
                logging.warning("⚠️ MELOTTS - No audio generated")
                return b""
            
            # Convert to int16
            if isinstance(audio, np.ndarray):
                # Normalize if needed
                if audio.dtype == np.float32 or audio.dtype == np.float64:
                    audio = (audio * 32767).astype(np.int16)
                elif audio.dtype != np.int16:
                    audio = audio.astype(np.int16)
            
            logging.debug(
                "🎙️ MELOTTS - Generated %d samples at %dHz",
                len(audio), self.sample_rate
            )
            return audio.tobytes()
            
        except Exception as exc:
            logging.error("❌ MELOTTS - Synthesis failed: %s", exc)
            return b""
    
    def shutdown(self) -> None:
        """Shutdown the model."""
        self.model = None
        self.speaker_ids = None
        self._initialized = False
        logging.info("🛑 MELOTTS - TTS shutdown")


class SileroTTSBackend:
    """
    Silero TTS backend — CPU-friendly, multi-language text-to-speech.

    Key advantage: native 8kHz output for telephony (no resampling needed).
    Supports Russian (primary), English, German, Spanish, French, Ukrainian.

    Docs: https://github.com/snakers4/silero-models
    """

    # Valid speakers per language (v3 model family)
    # See: https://github.com/snakers4/silero-models
    SPEAKERS = {
        "ru": ["aidar", "baya", "kseniya", "xenia", "eugene"],
        "en": ["en_0", "en_1", "en_2", "en_3", "en_4", "en_5"],
        "de": ["bernd_ungerer", "eva_k", "friedrich", "hokuspokus", "karlsson"],
        "es": ["es_0", "es_1", "es_2"],
        "fr": ["fr_0", "fr_1", "fr_2", "fr_3", "fr_4", "fr_5"],
        "ua": ["mykyta"],
    }

    # Default model IDs per language (the 'speaker' param in torch.hub.load)
    MODEL_IDS = {
        "ru": "v3_1_ru",
        "en": "v3_en",
        "de": "v3_de",
        "es": "v3_es",
        "fr": "v3_fr",
        "ua": "v3_ua",
    }

    def __init__(
        self,
        speaker: str = "xenia",
        language: str = "ru",
        model_id: str = "",
        sample_rate: int = 8000,
        model_path: str = "/app/models/tts/silero",
    ):
        self.speaker = speaker
        self.language = language
        self.model_id = model_id or self.MODEL_IDS.get(language, "v3_1_ru")
        self.sample_rate = sample_rate
        self.model_path = model_path
        self.model = None
        self._initialized = False

    def initialize(self) -> bool:
        try:
            import torch

            logging.info(
                "🎙️ SILERO - Initializing TTS (language=%s, model_id=%s, speaker=%s, rate=%d)",
                self.language,
                self.model_id,
                self.speaker,
                self.sample_rate,
            )

            # Direct torch.hub cache to the configured model path
            torch.hub.set_dir(self.model_path)

            # trust_repo=True is safe here: the hub cache is baked into the
            # Docker image at build time, so no network fetch occurs at runtime.
            model, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-models",
                model="silero_tts",
                language=self.language,
                speaker=self.model_id,
                trust_repo=True,
            )

            self.model = model

            # Validate speaker for the selected language
            valid_speakers = self.SPEAKERS.get(self.language, [])
            if valid_speakers and self.speaker not in valid_speakers:
                logging.warning(
                    "⚠️ SILERO - Speaker '%s' not valid for language '%s', available: %s. Using '%s'.",
                    self.speaker,
                    self.language,
                    valid_speakers,
                    valid_speakers[0],
                )
                self.speaker = valid_speakers[0]

            self._initialized = True
            logging.info("✅ SILERO - TTS initialized successfully")
            return True
        except ImportError as exc:
            logging.error("❌ SILERO - Import failed (torch not installed?): %s", exc)
            return False
        except Exception as exc:
            logging.error("❌ SILERO - Failed to initialize: %s", exc)
            return False

    def synthesize(self, text: str) -> bytes:
        if not self._initialized or self.model is None:
            logging.error("❌ SILERO - Not initialized")
            return b""

        try:
            import numpy as np

            audio = self.model.apply_tts(
                text=text,
                speaker=self.speaker,
                sample_rate=self.sample_rate,
            )

            # Convert torch tensor to int16 PCM bytes
            audio_np = audio.numpy() if hasattr(audio, "numpy") else audio
            audio_int16 = (audio_np * 32767).astype(np.int16)

            logging.debug(
                "🎙️ SILERO - Generated %d samples at %dHz",
                len(audio_int16),
                self.sample_rate,
            )
            return audio_int16.tobytes()
        except Exception as exc:
            logging.error("❌ SILERO - Synthesis failed: %s", exc)
            return b""

    def shutdown(self) -> None:
        self.model = None
        self._initialized = False
        logging.info("🛑 SILERO - TTS shutdown")
