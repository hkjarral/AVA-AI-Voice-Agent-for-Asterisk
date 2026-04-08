from __future__ import annotations

import audioop
import io
import logging
import os
import subprocess
import tempfile
import wave

from constants import ULAW_SAMPLE_RATE


class AudioProcessor:
    """Handles audio format conversions for MVP uLaw 8kHz pipeline.

    Primary path uses Python's audioop (in-process, no temp files).
    Falls back to sox subprocess if audioop conversion fails.
    """

    @staticmethod
    def resample_audio(
        input_data: bytes,
        input_rate: int,
        output_rate: int,
        input_format: str = "raw",
        output_format: str = "raw",
    ) -> bytes:
        """Resample raw PCM16 mono audio in-process via audioop.

        Falls back to sox subprocess on failure.
        """
        if input_rate == output_rate:
            return input_data

        try:
            resampled, _ = audioop.ratecv(input_data, 2, 1, input_rate, output_rate, None)
            return resampled
        except Exception as exc:
            logging.warning("audioop resample failed (%s), falling back to sox", exc)

        # ── sox fallback ──
        try:
            with tempfile.NamedTemporaryFile(
                suffix=f".{input_format}", delete=False
            ) as input_file:
                input_file.write(input_data)
                input_path = input_file.name

            with tempfile.NamedTemporaryFile(
                suffix=f".{output_format}", delete=False
            ) as output_file:
                output_path = output_file.name

            cmd = [
                "sox",
                "-t", "raw",
                "-r", str(input_rate),
                "-e", "signed-integer",
                "-b", "16",
                "-c", "1",
                input_path,
                "-r", str(output_rate),
                "-c", "1",
                "-e", "signed-integer",
                "-b", "16",
                output_path,
            ]

            subprocess.run(cmd, capture_output=True, check=True)

            with open(output_path, "rb") as f:
                resampled_data = f.read()

            os.unlink(input_path)
            os.unlink(output_path)

            return resampled_data

        except Exception as exc:  # pragma: no cover
            logging.error("Audio resampling failed (sox fallback): %s", exc)
            return input_data

    @staticmethod
    def pcm16_to_ulaw_8k(pcm_data: bytes, input_rate: int) -> bytes:
        """Convert raw PCM16 mono audio to 8 kHz µ-law in-process.

        Skips WAV header parsing — use when you already have raw PCM16 bytes.
        Falls back to convert_to_ulaw_8k via a WAV wrapper on failure.
        """
        # Preserve original data for fallback (ratecv reassigns pcm_data)
        original_pcm = pcm_data
        try:
            if input_rate != ULAW_SAMPLE_RATE:
                pcm_data, _ = audioop.ratecv(
                    pcm_data, 2, 1, input_rate, ULAW_SAMPLE_RATE, None
                )
            return audioop.lin2ulaw(pcm_data, 2)
        except Exception as exc:
            logging.warning(
                "pcm16_to_ulaw_8k failed (%s), falling back to WAV path", exc
            )
            # Build a minimal WAV wrapper using ORIGINAL data and delegate
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(input_rate)
                wf.writeframes(original_pcm)
            ulaw_data = AudioProcessor.convert_to_ulaw_8k(buf.getvalue(), input_rate)
            if not ulaw_data:
                logging.error("pcm16_to_ulaw_8k fallback also failed, returning empty")
                return b""
            return ulaw_data

    @staticmethod
    def convert_to_ulaw_8k(input_data: bytes, input_rate: int) -> bytes:
        """Convert WAV audio to 8 kHz µ-law in-process via audioop.

        Falls back to sox subprocess on failure.
        """
        try:
            # Parse WAV header to extract raw PCM
            wav_io = io.BytesIO(input_data)
            with wave.open(wav_io, "rb") as wf:
                pcm_data = wf.readframes(wf.getnframes())
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()

            # Stereo → mono
            if channels > 1:
                pcm_data = audioop.tomono(pcm_data, sampwidth, 1, 1)

            # Ensure 16-bit samples
            if sampwidth != 2:
                pcm_data = audioop.lin2lin(pcm_data, sampwidth, 2)

            # Resample to 8 kHz
            if framerate != ULAW_SAMPLE_RATE:
                pcm_data, _ = audioop.ratecv(
                    pcm_data, 2, 1, framerate, ULAW_SAMPLE_RATE, None
                )

            # PCM16 → µ-law
            ulaw_data = audioop.lin2ulaw(pcm_data, 2)
            return ulaw_data

        except Exception as exc:
            logging.warning(
                "audioop uLaw conversion failed (%s), falling back to sox", exc
            )

        # ── sox fallback ──
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as input_file:
                input_file.write(input_data)
                input_path = input_file.name

            with tempfile.NamedTemporaryFile(suffix=".ulaw", delete=False) as output_file:
                output_path = output_file.name

            cmd = [
                "sox",
                input_path,
                "-r", str(ULAW_SAMPLE_RATE),
                "-c", "1",
                "-e", "mu-law",
                "-t", "raw",
                output_path,
            ]

            subprocess.run(cmd, capture_output=True, check=True)

            with open(output_path, "rb") as f:
                ulaw_data = f.read()

            os.unlink(input_path)
            os.unlink(output_path)

            return ulaw_data

        except Exception as exc:  # pragma: no cover
            logging.error("uLaw conversion failed (sox fallback): %s", exc)
            return input_data
