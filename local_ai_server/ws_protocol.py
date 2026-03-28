from __future__ import annotations

import json
import logging
from dataclasses import replace

from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from constants import DEFAULT_MODE, SUPPORTED_MODES
from session import SessionContext


class WebSocketProtocol:
    def __init__(self, server):
        self._server = server

    async def handle_json_message(self, websocket, session: SessionContext, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logging.warning("❓ Invalid JSON message received (length=%d)", len(message))
            return

        msg_type_raw = data.get("type")
        if msg_type_raw is None:
            logging.warning("JSON payload missing 'type' (keys=%s)", sorted(data.keys()))
            return
        msg_type = (
            str(msg_type_raw)
            .replace("\x00", "")
            .strip()
            .lower()
            .replace("-", "_")
        )
        if not msg_type:
            logging.warning(
                "JSON payload has invalid 'type' (raw=%r, raw_type=%s, payload_keys=%s)",
                msg_type_raw,
                type(msg_type_raw).__name__,
                sorted(data.keys()),
            )
            return

        if msg_type == "auth":
            token = (data.get("auth_token") or data.get("token") or "").strip()
            call_id = data.get("call_id")
            if call_id:
                session.call_id = call_id
            if not self._server.ws_auth_token or token == self._server.ws_auth_token:
                session.authenticated = True
                await self._server._send_json(websocket, {"type": "auth_response", "status": "ok"})
                logging.info("🔐 WS AUTH - Authenticated session call_id=%s", session.call_id)
            else:
                await self._server._send_json(
                    websocket,
                    {
                        "type": "auth_response",
                        "status": "error",
                        "message": "invalid_auth_token",
                    },
                )
                logging.warning("🔐 WS AUTH - Invalid token call_id=%s", session.call_id)
            return

        if self._server.ws_auth_token and not session.authenticated:
            await self._server._send_json(
                websocket,
                {
                    "type": "auth_response",
                    "status": "error",
                    "message": "authentication_required",
                },
            )
            logging.warning(
                "🔐 WS AUTH - Message rejected before auth type=%s call_id=%s",
                msg_type,
                session.call_id,
            )
            return

        if msg_type == "set_mode":
            requested = data.get("mode", DEFAULT_MODE)
            if requested in SUPPORTED_MODES:
                session.mode = requested
                logging.info("Session mode updated to %s", session.mode)
            else:
                logging.warning("Unsupported mode requested: %s", requested)
            call_id = data.get("call_id")
            if call_id:
                session.call_id = call_id
            await self._server._send_json(
                websocket,
                {"type": "mode_ready", "mode": session.mode, "call_id": session.call_id},
            )
            return

        if msg_type == "audio":
            await self._server._handle_audio_payload(websocket, session, data)
            return

        if msg_type == "barge_in":
            call_id = data.get("call_id")
            if call_id:
                session.call_id = call_id
            self._server._clear_whisper_stt_suppression(session, reason="engine_barge_in")
            await self._server._send_json(
                websocket,
                {
                    "type": "barge_in_ack",
                    "status": "ok",
                    "call_id": session.call_id,
                    "request_id": data.get("request_id"),
                },
            )
            return

        if msg_type == "tts_request":
            await self._server._handle_tts_request(websocket, session, data)
            return

        if msg_type == "llm_request":
            await self._server._handle_llm_request(websocket, session, data)
            return

        if msg_type == "llm_tool_request":
            await self._server._handle_llm_tool_request(websocket, session, data)
            return

        if msg_type == "reload_models":
            logging.info("🔄 RELOAD REQUEST - Hot reloading all models...")
            await self._server.reload_models()
            await self._server._send_json(
                websocket,
                {
                    "type": "reload_response",
                    "status": "success",
                    "message": "All models reloaded successfully",
                },
            )
            return

        if msg_type == "reload_llm":
            logging.info("🔄 LLM RELOAD REQUEST - Hot reloading LLM with optimizations...")
            requested_path = data.get("llm_model_path") or data.get("model_path")
            if requested_path:
                self._server.config = replace(self._server.config, llm_model_path=requested_path)
                self._server._apply_config(self._server.config)
            await self._server.reload_llm_only()
            await self._server._send_json(
                websocket,
                {
                    "type": "reload_response",
                    "status": "success",
                    "message": (
                        "LLM model reloaded with optimizations (ctx="
                        f"{self._server.llm_context}, batch={self._server.llm_batch}, temp={self._server.llm_temperature}, "
                        f"max_tokens={self._server.llm_max_tokens})"
                    ),
                },
            )
            return

        if msg_type == "switch_model":
            logging.info("🔄 MODEL SWITCH REQUEST - Switching model configuration...")
            try:
                response = await self._server.model_manager.switch_model(data)
            except Exception as exc:
                logging.error("❌ Model switch failed: %s", exc)
                response = {"type": "switch_response", "status": "error", "message": str(exc)}
            await self._server._send_json(websocket, response)
            return

        if msg_type == "status":
            await self._server._send_json(websocket, self._server.model_manager.status())
            return

        if msg_type == "capabilities":
            await self._server._send_json(
                websocket,
                {
                    "type": "capabilities_response",
                    "capabilities": self._server.model_manager.capabilities(),
                },
            )
            return

        if msg_type == "backends":
            from backends import load_builtin_backends
            from backends.registry import STT_REGISTRY, TTS_REGISTRY, LLM_REGISTRY
            load_builtin_backends()
            await self._server._send_json(
                websocket,
                {
                    "type": "backends_response",
                    "stt": STT_REGISTRY.info(),
                    "tts": TTS_REGISTRY.info(),
                    "llm": LLM_REGISTRY.info(),
                },
            )
            return

        if msg_type == "backend_schema":
            backend_type = data.get("backend_type", "").strip().lower()
            backend_name = data.get("backend_name", "").strip().lower()
            from backends import load_builtin_backends
            from backends.registry import STT_REGISTRY, TTS_REGISTRY, LLM_REGISTRY
            load_builtin_backends()
            registry_map = {"stt": STT_REGISTRY, "tts": TTS_REGISTRY, "llm": LLM_REGISTRY}
            registry = registry_map.get(backend_type)
            if not registry:
                await self._server._send_json(
                    websocket,
                    {"type": "backend_schema_response", "error": f"Unknown backend type: {backend_type}"},
                )
                return
            cls = registry.get(backend_name)
            if not cls:
                await self._server._send_json(
                    websocket,
                    {"type": "backend_schema_response", "error": f"Unknown backend: {backend_name}"},
                )
                return
            try:
                schema = cls.config_schema()
                available = cls.is_available()
            except Exception as e:
                schema = {}
                available = False
            await self._server._send_json(
                websocket,
                {
                    "type": "backend_schema_response",
                    "backend_type": backend_type,
                    "backend_name": backend_name,
                    "schema": schema,
                    "available": available,
                },
            )
            return

        logging.warning("❓ Unknown message type: raw=%r normalized=%s", msg_type_raw, msg_type)

    async def handle_binary_message(self, websocket, session: SessionContext, message: bytes) -> None:
        if self._server.ws_auth_token and not session.authenticated:
            await self._server._send_json(
                websocket,
                {
                    "type": "auth_response",
                    "status": "error",
                    "message": "authentication_required",
                },
            )
            logging.warning(
                "🔐 WS AUTH - Dropping binary audio before auth call_id=%s bytes=%d",
                session.call_id,
                len(message),
            )
            return
        logging.info("🎵 AUDIO INPUT - Received binary audio: %s bytes", len(message))
        await self._server._handle_audio_payload(
            websocket,
            session,
            data={"mode": session.mode},
            incoming_bytes=message,
        )

    async def handler(self, websocket):
        logging.info("🔌 Client connected: %s", websocket.remote_address)

        bind_host = self._server.config.ws_host
        is_remote_bind = bind_host not in ("127.0.0.1", "localhost", "::1")
        if is_remote_bind and not self._server.ws_auth_token:
            logging.error(
                "🚨 SECURITY: Rejecting connection - server bound to %s but LOCAL_WS_AUTH_TOKEN not set",
                bind_host,
            )
            await websocket.close(
                1008, "Server misconfigured: auth token required for remote access"
            )
            return

        session = SessionContext()
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    await self.handle_binary_message(websocket, session, message)
                else:
                    await self.handle_json_message(websocket, session, message)
        except ConnectionClosedError:
            logging.debug("🔌 Client disconnected (no close frame)")
        except ConnectionClosedOK:
            logging.debug("🔌 Client disconnected normally")
        except Exception as exc:
            logging.error("❌ WebSocket handler error: %s", exc, exc_info=True)
        finally:
            try:
                await self._server._flush_sherpa_offline_trailing(websocket, session)
            except Exception:
                logging.debug("Failed sherpa trailing flush", exc_info=True)
            try:
                await self._server._flush_tone_trailing(websocket, session)
            except Exception:
                logging.debug("Failed tone trailing flush", exc_info=True)
            finally:
                self._server._reset_stt_session(session)
                logging.debug("🔌 Connection closed: %s", websocket.remote_address)
