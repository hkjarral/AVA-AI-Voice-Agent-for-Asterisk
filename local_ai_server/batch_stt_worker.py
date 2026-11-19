"""One-shot, process-isolated batch STT worker.

The parent starts at most one worker and kills it when a live call begins,
the client disconnects, or the deadline expires. No live-call model is shared.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys


MAX_PCM_BYTES = 120 * 16000 * 2
MAX_TRANSCRIPT_CHARS = 10000


def _transcribe(args: argparse.Namespace, pcm16: bytes) -> str:
    if args.backend == "faster_whisper":
        from stt_backends import FasterWhisperSTTBackend

        backend = FasterWhisperSTTBackend(
            model_size=args.model,
            device=args.device,
            compute_type=args.compute_type,
            language=args.configured_language,
        )
        if not backend.initialize():
            raise RuntimeError("stt_unavailable")
        try:
            return backend.transcribe_pcm16(
                pcm16, language=args.language or None, raise_on_error=True
            )
        finally:
            backend.shutdown()

    if args.backend == "whisper_cpp":
        from stt_backends import WhisperCppSTTBackend

        backend = WhisperCppSTTBackend(
            model_path=args.model, language=args.configured_language
        )
        if not backend.initialize():
            raise RuntimeError("stt_unavailable")
        try:
            return backend.transcribe_pcm16(pcm16, raise_on_error=True)
        finally:
            backend.shutdown()

    if args.backend == "vosk":
        from vosk import KaldiRecognizer, Model

        recognizer = KaldiRecognizer(Model(args.model), 16000)
        texts: list[str] = []
        for offset in range(0, len(pcm16), 32000):
            if recognizer.AcceptWaveform(pcm16[offset : offset + 32000]):
                part = json.loads(recognizer.Result())
                if isinstance(part.get("text"), str) and part["text"].strip():
                    texts.append(part["text"].strip())
        tail = json.loads(recognizer.FinalResult())
        if isinstance(tail.get("text"), str) and tail["text"].strip():
            texts.append(tail["text"].strip())
        return " ".join(texts)

    raise RuntimeError("unsupported_stt_backend")


def main() -> int:
    """Read one bounded PCM payload from stdin and emit one small JSON result."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--configured-language", default="en")
    parser.add_argument("--language", default="")
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    pcm16 = sys.stdin.buffer.read(MAX_PCM_BYTES + 1)
    if not pcm16 or len(pcm16) > MAX_PCM_BYTES or len(pcm16) % 2:
        print(json.dumps({"status": "error", "error": "invalid_audio_data"}))
        return 2
    try:
        text = _transcribe(args, pcm16)
        if not isinstance(text, str) or len(text) > MAX_TRANSCRIPT_CHARS:
            raise RuntimeError("invalid_transcript")
    except Exception:
        logging.exception("Batch STT worker failed")
        print(json.dumps({"status": "error", "error": "transcription_failed"}))
        return 1
    print(json.dumps({"status": "ok", "text": text}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
