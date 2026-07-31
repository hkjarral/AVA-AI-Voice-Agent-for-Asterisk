import os
import wave
import threading
from typing import Dict, Tuple, Optional

import audioop
import structlog

from .diagnostic_paths import (
    DEFAULT_DIAGNOSTIC_CAPTURE_DIR,
    UnsafeDiagnosticPathError,
    prepare_private_diagnostic_dir,
)


logger = structlog.get_logger(__name__)


class AudioCaptureManager:
    """Utility for capturing per-call audio streams to WAV files."""

    def __init__(
        self,
        base_dir: str = DEFAULT_DIAGNOSTIC_CAPTURE_DIR,
        keep_files: bool = False,
        enabled: bool = False,
    ):
        self.base_dir = str(base_dir)
        self.keep_files = bool(keep_files)
        self.enabled = bool(enabled)
        self.storage_ready = False
        self._lock = threading.Lock()
        # key -> (wave.Wave_write, sample_rate)
        self._handles: Dict[Tuple[str, str], Tuple[wave.Wave_write, int]] = {}
        try:
            # The empty capture root is an allowed installation artifact even
            # while capture is disabled. Per-call methods remain strict no-ops.
            self.base_dir = prepare_private_diagnostic_dir(self.base_dir)
            self.storage_ready = True
        except Exception as exc:
            # Never write diagnostic audio through an untrusted path. Calls
            # continue normally with capture disabled.
            self.enabled = False
            logger.warning(
                "Audio capture directory rejected; capture disabled",
                base_dir=self.base_dir,
                error=str(exc),
            )

    def _open_handle(self, call_id: str, stream_name: str, sample_rate: int) -> wave.Wave_write:
        path = os.path.join(self.base_dir, call_id, f"{stream_name}.wav")
        dir_path = os.path.dirname(path)
        prepare_private_diagnostic_dir(dir_path)
        wf = wave.open(path, "wb")
        wf.setnchannels(1)
        wf.setsampwidth(2)  # PCM16
        wf.setframerate(sample_rate)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return wf

    def append_pcm16(self, call_id: str, stream_name: str, pcm16: bytes, sample_rate: int) -> None:
        # This guard must stay ahead of payload inspection, key construction,
        # lock acquisition, directory/file handling, and WAV writes. Disabled
        # diagnostic capture is a privacy boundary, not a retention policy.
        if not self.enabled:
            return
        if not pcm16:
            return
        key = (call_id, stream_name)
        with self._lock:
            handle = self._handles.get(key)
            try:
                if handle is None:
                    wf = self._open_handle(call_id, stream_name, sample_rate)
                    self._handles[key] = (wf, sample_rate)
                else:
                    wf, existing_rate = handle
                    if existing_rate != sample_rate:
                        # Close and reopen with new rate to avoid inconsistent headers
                        try:
                            wf.close()
                        except Exception:
                            pass
                        wf = self._open_handle(call_id, stream_name, sample_rate)
                        self._handles[key] = (wf, sample_rate)
            except (UnsafeDiagnosticPathError, OSError) as exc:
                # An unsafe path or OS-level open failure is writer-wide. Disable
                # once, close any previously opened WAVs, and make later chunks
                # strict no-ops instead of retrying on every audio frame.
                self.enabled = False
                for open_handle, _rate in self._handles.values():
                    try:
                        open_handle.close()
                    except Exception:
                        pass
                self._handles.clear()
                logger.warning(
                    "Audio capture unavailable; capture disabled",
                    call_id=call_id,
                    stream_name=stream_name,
                    error=str(exc),
                )
                return
            wf = self._handles[key][0]
            try:
                wf.writeframes(pcm16)
            except Exception:
                # On write failure, close the handle to avoid corrupted files
                try:
                    wf.close()
                except Exception:
                    pass
                self._handles.pop(key, None)

    def append_encoded(
        self,
        call_id: str,
        stream_name: str,
        payload: bytes,
        encoding: str,
        sample_rate: int,
    ) -> None:
        # In particular, return before audioop conversion. Encoding work done
        # solely for diagnostics must not run while capture is disabled.
        if not self.enabled:
            return
        if not payload:
            return
        encoding = (encoding or "").lower()
        try:
            if encoding in ("ulaw", "mulaw", "g711_ulaw", "mu-law"):
                pcm16 = audioop.ulaw2lin(payload, 2)
                rate = sample_rate or 8000
            elif encoding in ("slin16", "linear16", "pcm16"):
                pcm16 = payload
                rate = sample_rate or 16000
            else:
                # Fallback: treat as PCM16
                pcm16 = payload
                rate = sample_rate or 16000
            self.append_pcm16(call_id, stream_name, pcm16, rate)
        except Exception as e:
            # Log capture failures for debugging but don't break call flow
            logger.warning(
                "Audio capture failed",
                call_id=call_id,
                stream_name=stream_name,
                encoding=encoding,
                sample_rate=sample_rate,
                payload_len=len(payload) if payload else 0,
                error=str(e),
                exc_info=True,
            )

    def close_call(self, call_id: str) -> None:
        # A disabled manager never owns per-call handles or files. Avoid the
        # lock and leave any artifacts from an earlier enabled run untouched.
        if not self.enabled:
            return
        keys_to_close = []
        with self._lock:
            for key, (wf, _rate) in list(self._handles.items()):
                if key[0] == call_id:
                    try:
                        wf.close()
                    except Exception:
                        pass
                    keys_to_close.append(key)
            for key in keys_to_close:
                self._handles.pop(key, None)
        # Only delete files if not in diagnostic/keep mode
        if self.keep_files:
            return
        # After closing wave handles, remove captured files and call directory
        try:
            call_dir = os.path.join(self.base_dir, call_id)
            if os.path.isdir(call_dir):
                try:
                    for name in os.listdir(call_dir):
                        fpath = os.path.join(call_dir, name)
                        try:
                            if os.path.isfile(fpath):
                                os.remove(fpath)
                        except Exception:
                            pass
                    os.rmdir(call_dir)
                except Exception:
                    pass
        except Exception:
            pass
