from __future__ import annotations

import logging
from typing import Any, Dict

from capabilities import detect_capabilities
from control_plane import apply_switch_model_request
from status_builder import build_status_response

_RUNTIME_ONLY_CHANGE_KEYS = {
    "enable_filler_audio",
    "llm_streaming_tts_overlap",
}


def _changed_key(change: str) -> str:
    return change.split("=", 1)[0].strip()


def _requires_model_reload(changed: list[str]) -> bool:
    return any(_changed_key(change) not in _RUNTIME_ONLY_CHANGE_KEYS for change in changed)


class ModelManager:
    def __init__(self, server: Any):
        self._server = server
        self._pending_model_reload = False

    async def switch_model(self, data: Dict[str, Any]) -> Dict[str, Any]:
        dry_run = bool(data.get("dry_run", False))
        new_config, changed = apply_switch_model_request(self._server.config, data)

        requires_reload = _requires_model_reload(changed) or self._pending_model_reload
        if not changed and (dry_run or not requires_reload):
            return {
                "type": "switch_response",
                "status": "no_change",
                "message": "No configuration changes to apply",
            }

        self._server.config = new_config
        self._server._apply_config(self._server.config)
        self._server.buffer_timeout_ms = self._server.config.stt_idle_ms

        logging.info("📝 Configuration updated: %s", ", ".join(changed))
        self._pending_model_reload = requires_reload
        if dry_run:
            logging.info("🧪 SWITCH MODEL DRY-RUN - Skipping reload_models()")
        elif requires_reload:
            await self._server.reload_models()
            self._pending_model_reload = False
        else:
            if any(_changed_key(item) == "enable_filler_audio" for item in changed):
                if self._server.config.enable_filler_audio:
                    await self._server._presynthesize_fillers()
                else:
                    self._server._filler_cache.clear()
            logging.info("✅ Runtime-only configuration applied without model reload")

        if not dry_run and not requires_reload and hasattr(self._server, "_publish_live_status_now"):
            self._server._publish_live_status_now()

        return {
            "type": "switch_response",
            "status": "success",
            "message": (
                f"Models switched (dry_run): {', '.join(changed)}"
                if dry_run
                else (
                    f"Models switched and reloaded: {', '.join(changed)}"
                    if requires_reload
                    else f"Runtime config updated: {', '.join(changed)}"
                )
            ),
            "changed": changed,
        }

    def status(self) -> Dict[str, Any]:
        return build_status_response(self._server)

    def capabilities(self) -> Dict[str, Any]:
        return detect_capabilities(self._server.config)
