from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
from websockets.exceptions import ConnectionClosed
import websockets.client as ws_client

from constants import DEBUG_AUDIO_FLOW, PCM16_TARGET_RATE


class KrokoSTTBackend:
    """
    Kroko ASR streaming STT backend via WebSocket.
    """

    def __init__(
        self,
        url: str,
        api_key: Optional[str] = None,
        language: str = "en-US",
        endpoints: bool = True,
    ):
        self.base_url = url
        self.api_key = api_key
        self.language = language
        self.endpoints = endpoints
        self._subprocess: Optional[asyncio.subprocess.Process] = None

    def build_connection_url(self) -> str:
        if "app.kroko.ai" in self.base_url:
            params = (
                f"?languageCode={self.language}"
                f"&endpoints={'true' if self.endpoints else 'false'}"
            )
            if self.api_key:
                params += f"&apiKey={self.api_key}"
            return f"{self.base_url}{params}"
        return self.base_url

    async def connect(self) -> Any:
        url = self.build_connection_url()
        logging.info("🎤 KROKO - Connecting to %s", url.split("?")[0])

        ws = await ws_client.connect(url)

        if "app.kroko.ai" in self.base_url:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                data = json.loads(msg)
                if data.get("type") == "connected":
                    logging.info(
                        "✅ KROKO - Connected to hosted API, session=%s", data.get("id")
                    )
            except asyncio.TimeoutError:
                logging.warning("⚠️ KROKO - No connected message received, continuing")
        else:
            logging.info("✅ KROKO - Connected to on-premise server")

        return ws

    @staticmethod
    def pcm16_to_float32(pcm16_audio: bytes) -> bytes:
        samples = np.frombuffer(pcm16_audio, dtype=np.int16)
        float_samples = samples.astype(np.float32) / 32768.0
        return float_samples.tobytes()

    async def send_audio(self, ws: Any, pcm16_audio: bytes) -> None:
        if ws is None:
            logging.warning("🎤 KROKO - Cannot send audio, no WebSocket connection")
            return

        float32_audio = self.pcm16_to_float32(pcm16_audio)
        await ws.send(float32_audio)

        if DEBUG_AUDIO_FLOW:
            logging.debug(
                "🎤 KROKO - Sent %d bytes PCM16 → %d bytes float32",
                len(pcm16_audio),
                len(float32_audio),
            )

    async def receive_transcript(
        self, ws: Any, timeout: float = 0.1
    ) -> Optional[Dict[str, Any]]:
        if ws is None:
            return None

        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(msg)
            if DEBUG_AUDIO_FLOW:
                logging.debug("🎤 KROKO - Received: %s", data)
            return data
        except asyncio.TimeoutError:
            return None
        except json.JSONDecodeError as exc:
            logging.warning("⚠️ KROKO - Invalid JSON response: %s", exc)
            return None
        except ConnectionClosed:
            logging.warning("⚠️ KROKO - Connection closed")
            return None
        except Exception as exc:
            logging.error("❌ KROKO - Receive error: %s", exc)
            return None

    async def close(self, ws: Any) -> None:
        if ws:
            try:
                await ws.close()
                logging.info("🎤 KROKO - Connection closed")
            except Exception:
                logging.debug("KROKO - Close error (ignored)")

    async def start_subprocess(self, model_path: str, port: int = 6006) -> bool:
        kroko_binary = "/usr/local/bin/kroko-server"

        if not os.path.exists(kroko_binary):
            logging.warning(
                "⚠️ KROKO - Binary not found at %s, using external server", kroko_binary
            )
            return False

        if not os.path.exists(model_path):
            logging.error("❌ KROKO - Model not found at %s", model_path)
            return False

        try:
            logging.info("🚀 KROKO - Starting embedded server on port %d", port)

            self._subprocess = await asyncio.create_subprocess_exec(
                kroko_binary,
                f"--model={model_path}",
                f"--port={port}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            await asyncio.sleep(2.0)

            if self._subprocess.returncode is not None:
                stderr = await self._subprocess.stderr.read()
                logging.error("❌ KROKO - Subprocess failed: %s", stderr.decode())
                return False

            logging.info(
                "✅ KROKO - Embedded server started (PID=%d)", self._subprocess.pid
            )
            return True

        except Exception as exc:
            logging.error("❌ KROKO - Failed to start subprocess: %s", exc)
            return False

    async def stop_subprocess(self) -> None:
        if self._subprocess:
            try:
                self._subprocess.terminate()
                await asyncio.wait_for(self._subprocess.wait(), timeout=5.0)
                logging.info("🛑 KROKO - Subprocess stopped")
            except asyncio.TimeoutError:
                self._subprocess.kill()
                logging.warning("⚠️ KROKO - Subprocess killed (timeout)")
            except Exception as exc:
                logging.error("❌ KROKO - Error stopping subprocess: %s", exc)
            finally:
                self._subprocess = None


class SherpaONNXSTTBackend:
    """Local streaming STT backend using sherpa-onnx."""

    def __init__(self, model_path: str, sample_rate: int = PCM16_TARGET_RATE):
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.recognizer = None
        self._initialized = False

    def initialize(self) -> bool:
        try:
            import sherpa_onnx

            if not os.path.exists(self.model_path):
                logging.error("❌ SHERPA - Model not found at %s", self.model_path)
                return False

            tokens_file = self._find_tokens_file()
            encoder_file = self._find_encoder_file()
            decoder_file = self._find_decoder_file()
            joiner_file = self._find_joiner_file()

            if not all([tokens_file, encoder_file, decoder_file, joiner_file]):
                missing = []
                if not tokens_file:
                    missing.append("tokens.txt")
                if not encoder_file:
                    missing.append("encoder*.onnx")
                if not decoder_file:
                    missing.append("decoder*.onnx")
                if not joiner_file:
                    missing.append("joiner*.onnx")
                logging.error("❌ SHERPA - Missing model files: %s", ", ".join(missing))
                return False

            logging.info("📁 SHERPA - Model files found:")
            logging.info("   tokens: %s", tokens_file)
            logging.info("   encoder: %s", encoder_file)
            logging.info("   decoder: %s", decoder_file)
            logging.info("   joiner: %s", joiner_file)

            self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=tokens_file,
                encoder=encoder_file,
                decoder=decoder_file,
                joiner=joiner_file,
                num_threads=2,
                sample_rate=self.sample_rate,
                enable_endpoint_detection=True,
                decoding_method="greedy_search",
            )
            self._initialized = True
            logging.info(
                "✅ SHERPA - Recognizer initialized with model %s", self.model_path
            )
            return True
        except ImportError:
            logging.error("❌ SHERPA - sherpa-onnx not installed")
            return False
        except Exception as exc:
            logging.error("❌ SHERPA - Failed to initialize: %s", exc)
            return False

    def _find_file_by_pattern(self, directory: str, prefix: str, suffix: str = ".onnx") -> str:
        if not os.path.isdir(directory):
            return ""
        for filename in os.listdir(directory):
            if filename.startswith(prefix) and filename.endswith(suffix):
                return os.path.join(directory, filename)
        return ""

    def _find_tokens_file(self) -> str:
        if os.path.isdir(self.model_path):
            tokens_path = os.path.join(self.model_path, "tokens.txt")
            if os.path.exists(tokens_path):
                return tokens_path
        model_dir = os.path.dirname(self.model_path)
        tokens_path = os.path.join(model_dir, "tokens.txt")
        if os.path.exists(tokens_path):
            return tokens_path
        return ""

    def _find_encoder_file(self) -> str:
        search_dir = (
            self.model_path if os.path.isdir(self.model_path) else os.path.dirname(self.model_path)
        )
        exact = os.path.join(search_dir, "encoder.onnx")
        if os.path.exists(exact):
            return exact
        int8 = self._find_file_by_pattern(search_dir, "encoder", ".int8.onnx")
        if int8:
            return int8
        return self._find_file_by_pattern(search_dir, "encoder", ".onnx")

    def _find_decoder_file(self) -> str:
        search_dir = (
            self.model_path if os.path.isdir(self.model_path) else os.path.dirname(self.model_path)
        )
        exact = os.path.join(search_dir, "decoder.onnx")
        if os.path.exists(exact):
            return exact
        int8 = self._find_file_by_pattern(search_dir, "decoder", ".int8.onnx")
        if int8:
            return int8
        return self._find_file_by_pattern(search_dir, "decoder", ".onnx")

    def _find_joiner_file(self) -> str:
        search_dir = (
            self.model_path if os.path.isdir(self.model_path) else os.path.dirname(self.model_path)
        )
        exact = os.path.join(search_dir, "joiner.onnx")
        if os.path.exists(exact):
            return exact
        int8 = self._find_file_by_pattern(search_dir, "joiner", ".int8.onnx")
        if int8:
            return int8
        return self._find_file_by_pattern(search_dir, "joiner", ".onnx")

    def create_stream(self) -> Any:
        if not self._initialized or not self.recognizer:
            return None
        return self.recognizer.create_stream()

    def process_audio(self, stream: Any, pcm16_audio: bytes) -> Optional[Dict[str, Any]]:
        if stream is None or not self._initialized:
            return None

        try:
            samples = np.frombuffer(pcm16_audio, dtype=np.int16)
            float_samples = samples.astype(np.float32) / 32768.0
            stream.accept_waveform(self.sample_rate, float_samples)
            if self.recognizer.is_ready(stream):
                self.recognizer.decode_stream(stream)

            result = self.recognizer.get_result(stream)
            if isinstance(result, str):
                text = result.strip()
            elif hasattr(result, "text"):
                text = result.text.strip() if result.text else ""
            else:
                text = str(result).strip() if result else ""

            if not text:
                return None

            is_final = self.recognizer.is_endpoint(stream)
            if is_final:
                self.recognizer.reset(stream)
                return {"type": "final", "text": text}
            return {"type": "partial", "text": text}
        except Exception as exc:
            logging.error("❌ SHERPA - Process error: %s", exc)
            return None

    def close_stream(self, stream: Any) -> None:
        pass

    def shutdown(self) -> None:
        self.recognizer = None
        self._initialized = False
        logging.info("🛑 SHERPA - Recognizer shutdown")


class SherpaOfflineSTTBackend:
    """
    Offline (non-streaming) STT backend using sherpa-onnx OfflineRecognizer + Silero VAD.

    Used for models that only support offline/batch inference (e.g., GigaAM for Russian).
    Silero VAD detects speech segments, and each complete segment is transcribed via
    OfflineRecognizer.from_transducer().

    The OfflineRecognizer is shared across sessions (thread-safe for decode_stream).
    VAD instances are per-session to prevent cross-session contamination — create one
    via ``create_session_vad()`` and pass it into ``process_audio()`` / ``finalize()``.

    Requires:
      - Transducer model files (tokens.txt, encoder*.onnx, decoder*.onnx, joiner*.onnx)
      - Silero VAD model (silero_vad.onnx)
    """

    def __init__(
        self,
        model_path: str,
        vad_model_path: str,
        sample_rate: int = PCM16_TARGET_RATE,
        preroll_ms: int = 0,
        vad_threshold: float = 0.5,
        vad_min_silence_ms: int = 500,
        vad_min_speech_ms: int = 250,
    ):
        self.model_path = model_path
        self.vad_model_path = vad_model_path
        self.sample_rate = sample_rate
        self.preroll_ms = max(0, int(preroll_ms))
        self._preroll_samples = int(self.sample_rate * (self.preroll_ms / 1000.0))
        self.vad_threshold = max(0.0, min(1.0, float(vad_threshold)))
        self.vad_min_silence_ms = max(0, int(vad_min_silence_ms))
        self.vad_min_speech_ms = max(0, int(vad_min_speech_ms))
        self.recognizer = None
        self._vad_config = None  # Stored for per-session VAD creation
        self._initialized = False
        self._min_audio_length = int(sample_rate * 0.25)
        self._debug_segments = str(os.getenv("SHERPA_OFFLINE_DEBUG_SEGMENTS", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._vad_chunk_count = 0

    def initialize(self) -> bool:
        try:
            import sherpa_onnx

            if not os.path.exists(self.model_path):
                logging.error("❌ SHERPA-OFFLINE - Model not found at %s", self.model_path)
                return False

            if not self.vad_model_path or not os.path.exists(self.vad_model_path):
                logging.error(
                    "❌ SHERPA-OFFLINE - Silero VAD model not found at %s. "
                    "Download from: https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
                    self.vad_model_path,
                )
                return False

            tokens_file = self._find_file("tokens.txt", ".txt")
            encoder_file = self._find_onnx("encoder")
            decoder_file = self._find_onnx("decoder")
            joiner_file = self._find_onnx("joiner")

            if not all([tokens_file, encoder_file, decoder_file, joiner_file]):
                missing = []
                if not tokens_file:
                    missing.append("tokens.txt")
                if not encoder_file:
                    missing.append("encoder*.onnx")
                if not decoder_file:
                    missing.append("decoder*.onnx")
                if not joiner_file:
                    missing.append("joiner*.onnx")
                logging.error("❌ SHERPA-OFFLINE - Missing model files: %s", ", ".join(missing))
                return False

            # Offline mode only supports non-streaming transducer models. Streaming
            # zipformer models belong in SherpaONNXSTTBackend.
            encoder_name = os.path.basename(encoder_file).lower()
            model_name = os.path.basename(os.path.normpath(self.model_path)).lower()
            is_streaming = "chunk" in encoder_name or "streaming" in model_name

            logging.info("📁 SHERPA-OFFLINE - Model files found:")
            logging.info("   tokens: %s", tokens_file)
            logging.info("   encoder: %s", encoder_file)
            logging.info("   decoder: %s", decoder_file)
            logging.info("   joiner: %s", joiner_file)
            logging.info("   vad: %s", self.vad_model_path)
            logging.info("   model type: %s", "streaming" if is_streaming else "offline")

            if is_streaming:
                logging.error(
                    "❌ SHERPA-OFFLINE - Offline mode requires a non-streaming Sherpa transducer model. "
                    "Got streaming model at %s. Use SHERPA_MODEL_TYPE=online for streaming models "
                    "or switch SHERPA_MODEL_PATH to an offline model such as sherpa-onnx-zipformer-en-2023-06-26.",
                    self.model_path,
                )
                return False

            self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                tokens=tokens_file,
                encoder=encoder_file,
                decoder=decoder_file,
                joiner=joiner_file,
                num_threads=2,
                sample_rate=self.sample_rate,
                decoding_method="greedy_search",
            )

            # Store VAD config for per-session creation (no shared VAD instance).
            self._vad_config = sherpa_onnx.VadModelConfig()
            self._vad_config.silero_vad.model = self.vad_model_path
            self._vad_config.silero_vad.threshold = self.vad_threshold
            self._vad_config.silero_vad.min_silence_duration = self.vad_min_silence_ms / 1000.0
            self._vad_config.silero_vad.min_speech_duration = self.vad_min_speech_ms / 1000.0
            self._vad_config.silero_vad.max_speech_duration = 20.0
            self._vad_config.sample_rate = self.sample_rate

            # Validate VAD config by creating (and discarding) a test instance.
            _test_vad = sherpa_onnx.VoiceActivityDetector(self._vad_config, buffer_size_in_seconds=30)
            del _test_vad

            self._initialized = True
            logging.info(
                "✅ SHERPA-OFFLINE - OfflineRecognizer + Silero VAD initialized with model %s "
                "(preroll_ms=%d threshold=%.2f min_silence_ms=%d min_speech_ms=%d)",
                self.model_path,
                self.preroll_ms,
                self.vad_threshold,
                self.vad_min_silence_ms,
                self.vad_min_speech_ms,
            )
            return True
        except ImportError:
            logging.error("❌ SHERPA-OFFLINE - sherpa-onnx not installed")
            return False
        except Exception as exc:
            logging.error("❌ SHERPA-OFFLINE - Failed to initialize: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Per-session VAD lifecycle
    # ------------------------------------------------------------------

    def create_session_vad(self) -> Optional[Any]:
        """Create a per-session Silero VAD instance. Returns None on failure."""
        if not self._initialized or self._vad_config is None:
            return None
        try:
            import sherpa_onnx
            return sherpa_onnx.VoiceActivityDetector(self._vad_config, buffer_size_in_seconds=30)
        except Exception as exc:
            logging.error("❌ SHERPA-OFFLINE - Failed to create session VAD: %s", exc)
            return None

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------

    def _find_file(self, name: str, suffix: str) -> str:
        search_dir = self.model_path if os.path.isdir(self.model_path) else os.path.dirname(self.model_path)
        path = os.path.join(search_dir, name)
        if os.path.exists(path):
            return path
        return ""

    def _find_onnx(self, prefix: str) -> str:
        search_dir = self.model_path if os.path.isdir(self.model_path) else os.path.dirname(self.model_path)
        if not os.path.isdir(search_dir):
            return ""
        exact = os.path.join(search_dir, f"{prefix}.onnx")
        if os.path.exists(exact):
            return exact
        for filename in os.listdir(search_dir):
            if filename.startswith(prefix) and filename.endswith(".int8.onnx"):
                return os.path.join(search_dir, filename)
        for filename in os.listdir(search_dir):
            if filename.startswith(prefix) and filename.endswith(".onnx"):
                return os.path.join(search_dir, filename)
        return ""

    # ------------------------------------------------------------------
    # Audio processing (all methods require a per-session VAD)
    # ------------------------------------------------------------------

    def _transcribe_segment(self, speech_samples: np.ndarray) -> str:
        """Transcribe a single speech segment using a non-streaming recognizer."""
        stream = self.recognizer.create_stream()
        stream.accept_waveform(self.sample_rate, speech_samples)
        self.recognizer.decode_stream(stream)
        return stream.result.text.strip() if hasattr(stream.result, "text") else str(stream.result).strip()

    def _copy_segment_samples(self, speech_segment: Any) -> np.ndarray:
        """Materialize VAD output into Python-owned float32 memory before pop/decode."""
        samples = list(speech_segment.samples)
        return np.asarray(samples, dtype=np.float32).copy()

    def _segment_stats(self, speech_samples: np.ndarray) -> Dict[str, Any]:
        if len(speech_samples) == 0:
            return {
                "samples": 0,
                "duration_ms": 0,
                "rms": 0.0,
                "min": 0.0,
                "max": 0.0,
                "max_abs": 0.0,
                "first5": [],
            }

        speech64 = speech_samples.astype(np.float64, copy=False)
        return {
            "samples": len(speech_samples),
            "duration_ms": int(len(speech_samples) / self.sample_rate * 1000),
            "rms": float(np.sqrt(np.mean(speech64 ** 2))),
            "min": float(np.min(speech_samples)),
            "max": float(np.max(speech_samples)),
            "max_abs": float(np.max(np.abs(speech_samples))),
            "first5": speech_samples[:5].tolist() if len(speech_samples) >= 5 else speech_samples.tolist(),
        }

    def _pcm16_to_float32(self, pcm16_audio: bytes) -> np.ndarray:
        if not pcm16_audio:
            return np.array([], dtype=np.float32)
        samples = np.frombuffer(pcm16_audio, dtype=np.int16)
        return samples.astype(np.float32) / 32768.0

    def _merge_preroll(
        self,
        speech_samples: np.ndarray,
        preroll_pcm16: Optional[bytes],
    ) -> np.ndarray:
        if self._preroll_samples <= 0 or not preroll_pcm16:
            return speech_samples

        preroll_samples = self._pcm16_to_float32(preroll_pcm16)
        if len(preroll_samples) == 0:
            return speech_samples

        # Keep only the requested preroll window and remove any exact overlap with
        # the segment prefix so we don't duplicate audio when the VAD already kept
        # part of the utterance.
        preroll_samples = preroll_samples[-self._preroll_samples:]
        max_overlap = min(len(preroll_samples), len(speech_samples))
        overlap = 0
        for candidate in range(max_overlap, 0, -1):
            if np.allclose(preroll_samples[-candidate:], speech_samples[:candidate], atol=1e-4):
                overlap = candidate
                break

        if overlap:
            preroll_samples = preroll_samples[:-overlap]

        if len(preroll_samples) == 0:
            return speech_samples

        return np.concatenate([preroll_samples, speech_samples]).astype(np.float32, copy=False)

    def _validate_segment_samples(self, speech_samples: np.ndarray) -> Optional[str]:
        if len(speech_samples) == 0:
            return "empty"
        if not np.isfinite(speech_samples).all():
            return "non-finite samples"
        max_abs = float(np.max(np.abs(speech_samples)))
        if max_abs > 1.5:
            return f"out-of-range samples (max_abs={max_abs:.6f})"
        return None

    def _log_segment(self, seg_idx: int, stats: Dict[str, Any], validation_error: Optional[str]) -> None:
        level = logging.warning if validation_error else logging.info
        suffix = f" validation={validation_error}" if validation_error else " validation=ok"
        if self._debug_segments:
            level(
                "🔍 SHERPA-OFFLINE VAD segment[%d] - samples=%d duration_ms=%d rms=%.6f "
                "min=%.6f max=%.6f max_abs=%.6f first5=%s min_required=%d%s",
                seg_idx,
                stats["samples"],
                stats["duration_ms"],
                stats["rms"],
                stats["min"],
                stats["max"],
                stats["max_abs"],
                stats["first5"],
                self._min_audio_length,
                suffix,
            )
            return

        level(
            "🔍 SHERPA-OFFLINE VAD segment[%d] - samples=%d duration_ms=%d rms=%.6f "
            "min=%.6f max=%.6f max_abs=%.6f min_required=%d%s",
            seg_idx,
            stats["samples"],
            stats["duration_ms"],
            stats["rms"],
            stats["min"],
            stats["max"],
            stats["max_abs"],
            self._min_audio_length,
            suffix,
        )

    def process_audio(
        self,
        vad: Any,
        pcm16_audio: bytes,
        preroll_pcm16: Optional[bytes] = None,
    ) -> Optional[Dict[str, Any]]:
        """Feed audio through a per-session VAD; transcribe complete speech segments."""
        if not self._initialized or self.recognizer is None or vad is None:
            return None

        try:
            samples = np.frombuffer(pcm16_audio, dtype=np.int16)
            float_samples = samples.astype(np.float32) / 32768.0

            rms = float(np.sqrt(np.mean(float_samples ** 2)))
            self._vad_chunk_count += 1
            if rms > 0.002 or self._vad_chunk_count % 100 == 1:
                logging.debug(
                    "🔍 SHERPA-OFFLINE VAD - chunk=%d samples=%d rms=%.6f speech=%s",
                    self._vad_chunk_count, len(float_samples), rms, rms > 0.002,
                )

            vad.accept_waveform(float_samples)

            has_segments = not vad.empty()
            if has_segments:
                logging.info(
                    "🔍 SHERPA-OFFLINE VAD - Speech segment(s) detected at chunk=%d",
                    self._vad_chunk_count,
                )

            # Process ALL queued speech segments (not just the first).
            texts = []
            seg_idx = 0
            while not vad.empty():
                speech_segment = vad.front
                speech_samples = self._copy_segment_samples(speech_segment)
                vad.pop()
                speech_samples = self._merge_preroll(speech_samples, preroll_pcm16)
                validation_error = self._validate_segment_samples(speech_samples)
                stats = self._segment_stats(speech_samples)
                self._log_segment(seg_idx, stats, validation_error)
                seg_idx += 1

                if validation_error:
                    logging.warning(
                        "⚠️ SHERPA-OFFLINE - Segment[%d] rejected before decode: %s",
                        seg_idx - 1,
                        validation_error,
                    )
                    continue

                if len(speech_samples) < self._min_audio_length:
                    logging.info(
                        "🔍 SHERPA-OFFLINE VAD segment skipped (too short): %d < %d samples",
                        len(speech_samples), self._min_audio_length,
                    )
                    continue

                text = self._transcribe_segment(speech_samples)
                logging.info(
                    "🔍 SHERPA-OFFLINE transcribe seg[%d] result: '%s' (empty=%s)",
                    seg_idx - 1, text or "", text is None or text == "",
                )
                if text:
                    texts.append(text)

            if texts:
                return {"type": "final", "text": " ".join(texts)}

            return None

        except Exception as exc:
            logging.error("❌ SHERPA-OFFLINE - Process error: %s", exc)
            return None

    def finalize(self, vad: Any) -> Optional[Dict[str, Any]]:
        """Flush remaining speech from a per-session VAD and transcribe it."""
        if not self._initialized or self.recognizer is None or vad is None:
            return None

        try:
            vad.flush()

            texts = []
            seg_idx = 0
            while not vad.empty():
                speech_segment = vad.front
                speech_samples = self._copy_segment_samples(speech_segment)
                vad.pop()
                validation_error = self._validate_segment_samples(speech_samples)
                stats = self._segment_stats(speech_samples)
                self._log_segment(seg_idx, stats, validation_error)
                seg_idx += 1
                if validation_error:
                    logging.warning(
                        "⚠️ SHERPA-OFFLINE - Finalize segment[%d] rejected before decode: %s",
                        seg_idx - 1,
                        validation_error,
                    )
                    continue
                if len(speech_samples) < self._min_audio_length:
                    continue

                text = self._transcribe_segment(speech_samples)
                if text:
                    texts.append(text)

            if texts:
                return {"type": "final", "text": " ".join(texts)}
            return None

        except Exception as exc:
            logging.error("❌ SHERPA-OFFLINE - Finalize error: %s", exc)
            return None

    def transcribe_pcm16(self, pcm16_audio: bytes) -> Optional[Dict[str, Any]]:
        """Direct transcription without VAD (for pre-segmented audio)."""
        if not self._initialized or self.recognizer is None:
            return None

        try:
            samples = np.frombuffer(pcm16_audio, dtype=np.int16)
            float_samples = samples.astype(np.float32) / 32768.0

            text = self._transcribe_segment(float_samples)
            if text:
                return {"type": "final", "text": text}
            return None

        except Exception as exc:
            logging.error("❌ SHERPA-OFFLINE - Transcribe error: %s", exc)
            return None

    def shutdown(self) -> None:
        self.recognizer = None
        self._vad_config = None
        self._initialized = False
        logging.info("🛑 SHERPA-OFFLINE - Recognizer shutdown")


class ToneSTTBackend:
    """
    Native T-one streaming CTC backend.

    T-one expects:
    - 8 kHz mono audio
    - 300 ms chunks (2400 samples)
    - int32 samples in the int16 range
    - per-session streaming state
    """

    CHUNK_SAMPLES = 2400
    SAMPLE_RATE = 8000

    def __init__(
        self,
        model_path: str,
        decoder_type: str = "beam_search",
        kenlm_path: str = "",
    ):
        self.model_path = model_path
        self.decoder_type = (decoder_type or "beam_search").strip().lower()
        self.kenlm_path = kenlm_path or ""
        self.pipeline = None
        self._initialized = False

    def initialize(self) -> bool:
        try:
            from tone.decoder import BeamSearchCTCDecoder, DecoderType
            from tone.logprob_splitter import StreamingLogprobSplitter
            from tone.onnx_wrapper import StreamingCTCModel
            from tone.pipeline import StreamingCTCPipeline

            model_file = os.path.join(self.model_path, "model.onnx")
            if not os.path.isfile(model_file):
                logging.error("❌ T-ONE - model.onnx not found at %s", model_file)
                return False

            if self.decoder_type not in {"beam_search", "greedy"}:
                logging.error("❌ T-ONE - Unsupported decoder type: %s", self.decoder_type)
                return False

            if self.decoder_type == "beam_search":
                kenlm_path = self.kenlm_path or os.path.join(self.model_path, "kenlm.bin")
                if not os.path.isfile(kenlm_path):
                    logging.error(
                        "❌ T-ONE - beam_search requires kenlm.bin at %s (set TONE_KENLM_PATH if stored elsewhere)",
                        kenlm_path,
                    )
                    return False
                decoder = BeamSearchCTCDecoder.from_local(kenlm_path)
                self.kenlm_path = kenlm_path
            else:
                decoder = None

            model = StreamingCTCModel.from_local(model_file)
            splitter = StreamingLogprobSplitter()
            if decoder is None:
                from tone.decoder import GreedyCTCDecoder

                decoder = GreedyCTCDecoder()

            self.pipeline = StreamingCTCPipeline(model, splitter, decoder)
            self._initialized = True
            logging.info(
                "✅ T-ONE - Initialized (model=%s decoder=%s kenlm=%s)",
                self.model_path,
                self.decoder_type,
                self.kenlm_path or "(not used)",
            )
            return True
        except ImportError:
            logging.error("❌ T-ONE - tone package not installed")
            return False
        except Exception as exc:
            logging.error("❌ T-ONE - Failed to initialize: %s", exc)
            return False

    def create_session_state(self) -> Optional[Dict[str, Any]]:
        if not self._initialized or self.pipeline is None:
            return None
        return {"pipeline_state": None, "last_partial": ""}

    def _decode_partial_text(self, state: Optional[Dict[str, Any]]) -> str:
        if not state or self.pipeline is None:
            return ""
        try:
            pipeline_state = state.get("pipeline_state")
            if not pipeline_state:
                return ""
            logprob_state = pipeline_state[1]
            past_logprobs = getattr(logprob_state, "past_logprobs", None)
            if past_logprobs is None or len(past_logprobs) == 0:
                return ""
            text = self.pipeline.decoder.forward(past_logprobs)
            return (text or "").strip()
        except Exception:
            return ""

    def process_audio(self, state: Dict[str, Any], chunk_samples: np.ndarray) -> List[Dict[str, Any]]:
        if not self._initialized or self.pipeline is None:
            return []
        if chunk_samples.shape != (self.CHUNK_SAMPLES,):
            raise ValueError(
                f"T-one expects {self.CHUNK_SAMPLES} samples per chunk, got {chunk_samples.shape}"
            )

        phrases, next_state = self.pipeline.forward(chunk_samples, state.get("pipeline_state"))
        state["pipeline_state"] = next_state

        updates: List[Dict[str, Any]] = []
        for phrase in phrases:
            text = (getattr(phrase, "text", "") or "").strip()
            if not text:
                continue
            updates.append(
                {
                    "text": text,
                    "is_final": True,
                    "is_partial": False,
                    "confidence": None,
                }
            )
            state["last_partial"] = ""

        partial_text = self._decode_partial_text(state)
        last_partial = (state.get("last_partial") or "").strip()
        if partial_text and partial_text != last_partial:
            state["last_partial"] = partial_text
            updates.append(
                {
                    "text": partial_text,
                    "is_final": False,
                    "is_partial": True,
                    "confidence": None,
                }
            )

        return updates

    def finalize(self, state: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self._initialized or self.pipeline is None or not state:
            return []
        try:
            phrases, next_state = self.pipeline.finalize(state.get("pipeline_state"))
            state["pipeline_state"] = next_state
            state["last_partial"] = ""
            updates: List[Dict[str, Any]] = []
            for phrase in phrases:
                text = (getattr(phrase, "text", "") or "").strip()
                if not text:
                    continue
                updates.append(
                    {
                        "text": text,
                        "is_final": True,
                        "is_partial": False,
                        "confidence": None,
                    }
                )
            return updates
        except Exception as exc:
            logging.error("❌ T-ONE - Finalize failed: %s", exc)
            return []

    def shutdown(self) -> None:
        self.pipeline = None
        self._initialized = False
        logging.info("🛑 T-ONE - Pipeline shutdown")


class FasterWhisperSTTBackend:
    """
    Faster-Whisper STT backend using CTranslate2-optimized Whisper.
    
    Provides high-accuracy transcription with good performance on both CPU and GPU.
    Uses chunked processing for pseudo-streaming (Whisper is not natively streaming).
    
    Model sizes: tiny, base, small, medium, large-v2, large-v3
    """
    
    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "en",
        sample_rate: int = 16000,
    ):
        """
        Initialize Faster-Whisper backend.
        
        Args:
            model_size: Model size (tiny, base, small, medium, large-v2, large-v3)
            device: Device to use (cpu, cuda, auto)
            compute_type: Computation type (int8, float16, float32)
            language: Language code for transcription
            sample_rate: Audio sample rate (default 16000 Hz)
        """
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.sample_rate = sample_rate
        self.model = None
        self._initialized = False
        # Audio buffer for chunked processing
        self._audio_buffer = np.array([], dtype=np.float32)
        # Minimum audio length for processing (1.5 seconds)
        self._min_audio_length = int(sample_rate * 1.5)
        # Last transcript to detect changes
        self._last_text = ""
    
    def initialize(self) -> bool:
        """Initialize the Faster-Whisper model."""
        try:
            from faster_whisper import WhisperModel
            
            logging.info(
                "🎤 FASTER-WHISPER - Loading model=%s device=%s compute=%s",
                self.model_size, self.device, self.compute_type
            )
            
            # Auto-detect device if set to auto
            device = self.device
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"
            
            self.model = WhisperModel(
                self.model_size,
                device=device,
                compute_type=self.compute_type,
            )
            
            self._initialized = True
            logging.info("✅ FASTER-WHISPER - Model loaded successfully")
            return True
            
        except ImportError:
            logging.error("❌ FASTER-WHISPER - faster-whisper not installed")
            return False
        except Exception as exc:
            logging.error("❌ FASTER-WHISPER - Failed to initialize: %s", exc)
            return False
    
    def process_audio(self, pcm16_audio: bytes) -> Optional[Dict[str, Any]]:
        """
        Process PCM16 audio and return transcript.
        
        Buffers audio and processes when enough has accumulated.
        Returns partial results during buffering, final on silence detection.
        
        Args:
            pcm16_audio: Audio in PCM16 format, 16kHz mono
            
        Returns:
            Dict with keys: type ("partial"|"final"), text
            None if no result yet
        """
        if not self._initialized or self.model is None:
            return None
        
        try:
            # Convert PCM16 to float32
            samples = np.frombuffer(pcm16_audio, dtype=np.int16)
            float_samples = samples.astype(np.float32) / 32768.0
            
            # Add to buffer
            self._audio_buffer = np.concatenate([self._audio_buffer, float_samples])
            
            # Only process if we have enough audio
            if len(self._audio_buffer) < self._min_audio_length:
                return None
            
            # Transcribe the buffered audio
            # Disable VAD for telephony audio - it often filters out speech
            segments, info = self.model.transcribe(
                self._audio_buffer,
                language=self.language,
                beam_size=1,  # Faster decoding
                vad_filter=False,  # Disabled - telephony audio often misdetected as silence
            )
            
            # Collect all segment texts
            text = " ".join(segment.text.strip() for segment in segments)
            
            if not text:
                return None
            
            # Check if text changed (indicates ongoing speech)
            if text != self._last_text:
                self._last_text = text
                return {"type": "partial", "text": text}
            
            return None
            
        except Exception as exc:
            logging.error("❌ FASTER-WHISPER - Transcription error: %s", exc)
            return None
    
    def finalize(self) -> Optional[Dict[str, Any]]:
        """
        Finalize transcription and return final result.
        
        Called when speech ends (silence detected).
        Clears the buffer and returns final transcript.
        """
        if not self._initialized or self.model is None:
            return None
        
        if len(self._audio_buffer) == 0:
            return None
        
        try:
            # Transcribe remaining audio
            segments, info = self.model.transcribe(
                self._audio_buffer,
                language=self.language,
                beam_size=5,  # Better quality for final
                vad_filter=True,
            )
            
            text = " ".join(segment.text.strip() for segment in segments)
            
            # Clear buffer
            self._audio_buffer = np.array([], dtype=np.float32)
            self._last_text = ""
            
            if text:
                return {"type": "final", "text": text}
            return None
            
        except Exception as exc:
            logging.error("❌ FASTER-WHISPER - Finalize error: %s", exc)
            self._audio_buffer = np.array([], dtype=np.float32)
            return None
    
    def reset(self) -> None:
        """Reset the audio buffer."""
        self._audio_buffer = np.array([], dtype=np.float32)
        self._last_text = ""

    def transcribe_pcm16(self, pcm16_audio: bytes) -> str:
        """
        Transcribe a complete utterance (PCM16 16kHz mono) in one shot.

        This is intended for telephony turn-taking where we segment utterances
        outside the model and avoid backend-internal VAD filters that can
        mis-detect phone audio as silence.
        """
        if not self._initialized or self.model is None:
            return ""
        if not pcm16_audio:
            return ""

        try:
            samples = np.frombuffer(pcm16_audio, dtype=np.int16)
            float_samples = samples.astype(np.float32) / 32768.0
            lang = (self.language or "").strip().lower()
            language = None if not lang or lang == "auto" else self.language
            segments, _info = self.model.transcribe(
                float_samples,
                language=language,
                beam_size=1,
                vad_filter=False,
            )
            text = " ".join(segment.text.strip() for segment in segments if getattr(segment, "text", None))
            return (text or "").strip()
        except Exception:
            logging.error("❌ FASTER-WHISPER - transcribe_pcm16 failed", exc_info=True)
            return ""
    
    def shutdown(self) -> None:
        """Shutdown the model."""
        self.model = None
        self._initialized = False
        self._audio_buffer = np.array([], dtype=np.float32)
        logging.info("🛑 FASTER-WHISPER - Model shutdown")


class WhisperCppSTTBackend:
    """
    Whisper.cpp STT backend using ggml-optimized Whisper.
    
    Uses the same ggml backend as llama-cpp-python, avoiding library conflicts
    that cause segfaults with CTranslate2 (faster-whisper).
    
    Model sizes: tiny, base, small, medium, large
    """
    
    def __init__(
        self,
        model_path: str = "/app/models/stt/ggml-base.en.bin",
        language: str = "en",
        sample_rate: int = 16000,
    ):
        """
        Initialize Whisper.cpp backend.
        
        Args:
            model_path: Path to ggml Whisper model file (.bin)
            language: Language code for transcription
            sample_rate: Audio sample rate (default 16000 Hz)
        """
        self.model_path = model_path
        self.language = language
        self.sample_rate = sample_rate
        self.model = None
        self._initialized = False
        # Audio buffer for chunked processing
        self._audio_buffer = np.array([], dtype=np.float32)
        # Minimum audio length for processing (1.5 seconds)
        self._min_audio_length = int(sample_rate * 1.5)
        # Last transcript to detect changes
        self._last_text = ""
    
    def initialize(self) -> bool:
        """Initialize the Whisper.cpp model."""
        try:
            from pywhispercpp.model import Model
            
            logging.info(
                "🎤 WHISPER.CPP - Loading model from %s",
                self.model_path
            )
            
            if not os.path.exists(self.model_path):
                logging.error("❌ WHISPER.CPP - Model file not found: %s", self.model_path)
                return False
            
            self.model = Model(self.model_path, n_threads=4)
            
            self._initialized = True
            logging.info("✅ WHISPER.CPP - Model loaded successfully")
            return True
            
        except ImportError:
            logging.error("❌ WHISPER.CPP - pywhispercpp not installed")
            return False
        except Exception as exc:
            logging.error("❌ WHISPER.CPP - Failed to initialize: %s", exc)
            return False
    
    # Known Whisper hallucinations to filter out
    HALLUCINATION_PATTERNS = {
        "[BLANK_AUDIO]", "[MUSIC]", "[APPLAUSE]", "[LAUGHTER]",
        "you", "You", "YOU", "Thank you.", "Thanks for watching.",
        "Bye.", "Goodbye.", "See you.", "Subscribe.",
    }
    
    def _compute_energy(self, samples: np.ndarray) -> float:
        """Compute RMS energy of audio samples."""
        return float(np.sqrt(np.mean(samples ** 2)))
    
    def _is_hallucination(self, text: str) -> bool:
        """Check if text is a known Whisper hallucination."""
        text_clean = text.strip()
        # Exact match hallucinations
        if text_clean in self.HALLUCINATION_PATTERNS:
            return True
        # Short repetitive patterns (e.g., "you you you")
        words = text_clean.lower().split()
        if len(words) >= 2 and len(set(words)) == 1:
            return True
        return False
    
    def process_audio(self, pcm16_audio: bytes) -> Optional[Dict[str, Any]]:
        """
        Process PCM16 audio and return transcript.
        
        Args:
            pcm16_audio: Audio in PCM16 format, 16kHz mono
            
        Returns:
            Dict with keys: type ("partial"|"final"), text
            None if no result yet
        """
        if not self._initialized or self.model is None:
            return None
        
        try:
            # Convert PCM16 to float32
            samples = np.frombuffer(pcm16_audio, dtype=np.int16)
            float_samples = samples.astype(np.float32) / 32768.0
            
            # Energy-based VAD: skip silence
            energy = self._compute_energy(float_samples)
            if energy < 0.01:  # Silence threshold
                return None
            
            # Add to buffer
            self._audio_buffer = np.concatenate([self._audio_buffer, float_samples])
            
            # Only process if we have enough audio
            if len(self._audio_buffer) < self._min_audio_length:
                return None
            
            # Check buffer energy before processing
            buffer_energy = self._compute_energy(self._audio_buffer)
            if buffer_energy < 0.02:  # Buffer too quiet
                self._audio_buffer = np.array([], dtype=np.float32)
                return None
            
            # Transcribe the buffered audio
            segments = self.model.transcribe(self._audio_buffer, language=self.language)
            
            # Collect all segment texts
            text = " ".join(seg.text.strip() for seg in segments if seg.text)
            
            if not text:
                return None
            
            # Filter out hallucinations
            if self._is_hallucination(text):
                logging.debug("🔇 WHISPER.CPP - Filtered hallucination: '%s'", text)
                return None
            
            # Check if text changed (indicates ongoing speech)
            if text != self._last_text:
                self._last_text = text
                return {"type": "partial", "text": text}
            
            return None
            
        except Exception as exc:
            logging.error("❌ WHISPER.CPP - Transcription error: %s", exc)
            return None
    
    def finalize(self) -> Optional[Dict[str, Any]]:
        """
        Finalize transcription and return final result.
        
        Called when speech ends (silence detected).
        Clears the buffer and returns final transcript.
        """
        if not self._initialized or self.model is None:
            return None
        
        if len(self._audio_buffer) == 0:
            return None
        
        try:
            # Check buffer energy - skip if too quiet
            buffer_energy = self._compute_energy(self._audio_buffer)
            if buffer_energy < 0.02:
                self._audio_buffer = np.array([], dtype=np.float32)
                self._last_text = ""
                return None
            
            # Transcribe remaining audio
            segments = self.model.transcribe(self._audio_buffer, language=self.language)
            
            text = " ".join(seg.text.strip() for seg in segments if seg.text)
            
            # Clear buffer
            self._audio_buffer = np.array([], dtype=np.float32)
            self._last_text = ""
            
            if text:
                # Filter out hallucinations
                if self._is_hallucination(text):
                    logging.debug("🔇 WHISPER.CPP - Filtered hallucination in finalize: '%s'", text)
                    return None
                return {"type": "final", "text": text}
            return None
            
        except Exception as exc:
            logging.error("❌ WHISPER.CPP - Finalize error: %s", exc)
            self._audio_buffer = np.array([], dtype=np.float32)
            return None
    
    def reset(self) -> None:
        """Reset the audio buffer."""
        self._audio_buffer = np.array([], dtype=np.float32)
        self._last_text = ""

    def transcribe_pcm16(self, pcm16_audio: bytes) -> str:
        """Transcribe a complete utterance (PCM16 16kHz mono) in one shot."""
        if not self._initialized or self.model is None:
            return ""
        if not pcm16_audio:
            return ""
        try:
            samples = np.frombuffer(pcm16_audio, dtype=np.int16)
            float_samples = samples.astype(np.float32) / 32768.0
            segments = self.model.transcribe(float_samples, language=self.language)
            text = " ".join(seg.text.strip() for seg in segments if getattr(seg, "text", None))
            text = (text or "").strip()
            if text and self._is_hallucination(text):
                logging.debug("🔇 WHISPER.CPP - Filtered hallucination (oneshot): '%s'", text)
                return ""
            return text
        except Exception:
            logging.error("❌ WHISPER.CPP - transcribe_pcm16 failed", exc_info=True)
            return ""
    
    def shutdown(self) -> None:
        """Shutdown the model."""
        self.model = None
        self._initialized = False
        self._audio_buffer = np.array([], dtype=np.float32)
        logging.info("🛑 WHISPER.CPP - Model shutdown")
