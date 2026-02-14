import asyncio
import base64
import json
from typing import Callable, Optional, List, Dict, Any
import websockets
import websockets.exceptions
from websockets.asyncio.client import ClientConnection

from structlog import get_logger

import audioop
from ..config import LocalProviderConfig
from ..audio.resampler import resample_audio
from .base import AIProviderInterface
from ..tools.parser import parse_response_with_tools

logger = get_logger(__name__)

class LocalProvider(AIProviderInterface):
    """
    AI Provider that connects to the external Local AI Server via WebSockets.
    """
    def __init__(self, config: LocalProviderConfig, on_event: Callable[[Dict[str, Any]], None]):
        super().__init__(on_event)
        self.config = config
        self.websocket: Optional[ClientConnection] = None
        # Use effective_ws_url which prefers base_url over ws_url
        self.ws_url = config.effective_ws_url
        self.auth_token: Optional[str] = getattr(config, "auth_token", None) or None
        self.connect_timeout = float(getattr(config, "connect_timeout_sec", 5.0) or 5.0)
        self.response_timeout = float(getattr(config, "response_timeout_sec", 5.0) or 5.0)
        self._batch_ms = max(5, int(getattr(config, "chunk_ms", 200) or 200))
        self._listener_task: Optional[asyncio.Task] = None
        self._sender_task: Optional[asyncio.Task] = None
        self._send_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._active_call_id: Optional[str] = None
        self.input_mode: str = 'mulaw8k'  # or 'pcm16_8k' or 'pcm16_16k'
        self._pending_tts_responses: Dict[str, asyncio.Future] = {}  # Track pending TTS responses
        # Initial greeting text provided by engine/config (optional)
        self._initial_greeting: Optional[str] = None
        # Mode for local_ai_server: "full" or "stt" (for hybrid pipelines with cloud LLM)
        self._mode: str = getattr(config, 'mode', 'full') or 'full'
        # Track if server port is unavailable (not running at all)
        self._server_unavailable: bool = False
        self._resample_state_stt: Optional[tuple] = None
        # Parse host/port from ws_url for port checking
        self._server_host, self._server_port = self._parse_ws_url(self.ws_url)
        # Track if we were previously connected (for background reconnect on disconnect)
        self._was_connected: bool = False
        # Background reconnect task (runs when previously connected server disconnects)
        self._background_reconnect_task: Optional[asyncio.Task] = None

    def _parse_ws_url(self, ws_url: str) -> tuple:
        """Parse host and port from WebSocket URL."""
        try:
            # ws://127.0.0.1:8765 or ws://127.0.0.1:8765/ws
            url = ws_url.replace('ws://', '').replace('wss://', '')
            host_port = url.split('/')[0]
            if ':' in host_port:
                host, port = host_port.split(':')
                return host, int(port)
            return host_port, 8765  # default port
        except Exception:
            return '127.0.0.1', 8765

    async def _is_port_open(self, timeout: float = 0.5) -> bool:
        """Quick TCP check if local AI server port is listening.
        
        Returns True if port is open (server container is running).
        Returns False if port is closed (server not running/not set up).
        """
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self._server_host, self._server_port),
                timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            return False
        except Exception:
            return False

    def set_initial_greeting(self, text: Optional[str]) -> None:
        try:
            value = (text or "").strip()
        except Exception:
            value = ""
        self._initial_greeting = value or None

    @property
    def supported_codecs(self) -> List[str]:
        return ["ulaw"]

    def is_ready(self) -> bool:
        """
        Readiness for the *provider config* (mirrors other providers):
        - True when the WebSocket URL is configured.
        - Connection establishment happens on-demand when the provider is used.

        Note: connection state is exposed via `is_connected()`.
        """
        ws_url = getattr(self, "ws_url", None) or ""
        return bool(str(ws_url).strip())

    def is_connected(self) -> bool:
        """Return True only when the provider has an active WS connection."""
        return bool(self.websocket is not None and self.websocket.state.name == "OPEN")

    async def _connect_ws(self):
        # Use conservative client settings; server will drive pings if needed
        return await asyncio.wait_for(
            websockets.connect(
                self.ws_url,
                ping_interval=None,         # disable client pings to avoid false timeouts
                ping_timeout=None,
                close_timeout=10,
                max_size=None
            ),
            timeout=self.connect_timeout,
        )

    async def _authenticate(self) -> None:
        """Authenticate with local-ai-server if auth_token is configured."""
        if not self.auth_token or not self.websocket or self.websocket.state.name != "OPEN":
            return
        await self.websocket.send(
            json.dumps({"type": "auth", "auth_token": self.auth_token})
        )
        try:
            raw = await asyncio.wait_for(
                self.websocket.recv(), timeout=self.connect_timeout
            )
            if isinstance(raw, (bytes, bytearray)):
                raise RuntimeError("Unexpected binary auth response")
            data = json.loads(raw)
        except Exception as exc:
            raise RuntimeError(f"Auth handshake failed: {exc}") from exc

        if data.get("type") != "auth_response" or data.get("status") != "ok":
            raise RuntimeError(f"Auth rejected: {data}")

    async def _reconnect(self):
        # HYBRID APPROACH: Quick port check first
        # If port is closed, server is not running at all - skip immediately
        # If port is open, server is starting/running - use retry logic
        
        port_open = await self._is_port_open(timeout=0.5)
        
        if not port_open:
            # Port closed = server container not running = not set up
            logger.info(
                "⏭️ Local AI Server port not open - skipping connection",
                host=self._server_host,
                port=self._server_port,
                note="Start local-ai-server container if you want to use local STT/TTS/LLM"
            )
            self._server_unavailable = True
            return False
        
        # Port is open - server is running, proceed with retry logic for warmup
        logger.info(
            "🔄 Local AI Server port is open, connecting...",
            host=self._server_host,
            port=self._server_port
        )
        self._server_unavailable = False
        
        # Exponential backoff up to 30s, total ~3 minutes to cover LLM warmup (~111s)
        backoff_schedule = [2, 5, 10, 20, 30, 30, 30, 30]  # Total: ~157s
        total_elapsed = 0
        
        for attempt, delay in enumerate(backoff_schedule, 1):
            try:
                if attempt == 1:
                    logger.info(
                        "🔄 Connecting to Local AI Server...",
                        url=self.ws_url,
                        note="Server may be warming up models (~2 minutes)"
                    )
                else:
                    logger.info(
                        f"🔄 Reconnect attempt {attempt}/{len(backoff_schedule)}",
                        url=self.ws_url,
                        next_retry=f"{delay}s",
                        elapsed=f"{total_elapsed}s"
                    )
                
                self.websocket = await self._connect_ws()
                self._was_connected = True  # Mark that we successfully connected
                logger.info("✅ Connected to Local AI Server", elapsed=f"{total_elapsed}s")

                # Authenticate before starting receive/send loops if required.
                if self.auth_token:
                    await self._authenticate()
                    logger.info("🔐 Authenticated to Local AI Server", url=self.ws_url)
                
                # Cancel old tasks and restart listener/sender loops on new connection
                if self._listener_task and not self._listener_task.done():
                    self._listener_task.cancel()
                    logger.debug("Cancelled old listener task before restart")
                if self._sender_task and not self._sender_task.done():
                    self._sender_task.cancel()
                    logger.debug("Cancelled old sender task before restart")
                
                self._listener_task = asyncio.create_task(self._receive_loop())
                self._sender_task = asyncio.create_task(self._send_loop())
                logger.info("✅ Reconnected to Local AI Server, restarting receive loop")
                return True
                
            except (ConnectionRefusedError, OSError) as e:
                # Port was open but connection failed - server might be restarting
                # Re-check if port is still open before retrying
                if not await self._is_port_open(timeout=0.3):
                    logger.info(
                        "⏭️ Local AI Server port closed during retry - stopping",
                        note="Server may have stopped"
                    )
                    self._server_unavailable = True
                    return False
                
                # Port still open, continue retrying
                if attempt < len(backoff_schedule):
                    logger.debug(
                        f"Connection attempt {attempt} failed (likely warmup)",
                        error=type(e).__name__,
                        next_retry=f"{delay}s"
                    )
                else:
                    logger.warning(
                        "Connection failed after all retries",
                        attempts=len(backoff_schedule),
                        total_elapsed=f"{total_elapsed}s",
                        error=str(e)
                    )
            except Exception as e:
                logger.warning(
                    f"Reconnect attempt {attempt} failed",
                    error=f"{type(e).__name__}: {str(e)}",
                    next_retry=f"{delay}s" if attempt < len(backoff_schedule) else "none"
                )
            
            if attempt < len(backoff_schedule):
                await asyncio.sleep(delay)
                total_elapsed += delay
                
        return False

    async def _background_reconnect_loop(self):
        """Background task that periodically tries to reconnect for up to 12 minutes.
        
        Only runs when we were previously connected and got disconnected (e.g., server restart).
        Does not block anything - runs independently in the background.
        """
        max_duration = 12 * 60  # 12 minutes
        check_interval = 30  # Check every 30 seconds
        start_time = asyncio.get_event_loop().time()
        
        logger.info(
            "🔄 Starting background reconnect (server was previously connected)",
            max_duration="12 minutes",
            check_interval="30s"
        )
        
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= max_duration:
                logger.warning(
                    "⏹️ Background reconnect timed out after 12 minutes",
                    note="Local AI Server did not come back online"
                )
                break
            
            # Wait before checking
            await asyncio.sleep(check_interval)
            
            # Check if port is open
            if await self._is_port_open(timeout=1.0):
                logger.info("🔄 Local AI Server port detected, attempting reconnect...")
                success = await self._reconnect()
                if success:
                    logger.info("✅ Background reconnect successful")
                    self._was_connected = True
                    # Restart listener task
                    if not self._listener_task or self._listener_task.done():
                        self._listener_task = asyncio.create_task(self._receive_loop())
                    break
                else:
                    logger.debug("Reconnect attempt failed, will retry...")
            else:
                remaining = int(max_duration - elapsed)
                logger.debug(
                    f"Local AI Server port still closed, will check again in {check_interval}s",
                    remaining=f"{remaining}s"
                )
        
        self._background_reconnect_task = None

    def _start_background_reconnect(self):
        """Start background reconnect task if not already running."""
        if self._background_reconnect_task and not self._background_reconnect_task.done():
            logger.debug("Background reconnect task already running")
            return
        
        self._background_reconnect_task = asyncio.create_task(self._background_reconnect_loop())

    async def initialize(self):
        """Initialize persistent connection to Local AI Server.
        
        If the server port is not open (server not running), this will
        mark the provider as unavailable and return gracefully without error.
        """
        try:
            if self.websocket and self.websocket.state.name == "OPEN":
                logger.debug("WebSocket already connected, skipping initialization")
                return
            
            logger.info("Initializing connection to Local AI Server...", url=self.ws_url)
            # Use _reconnect method which has port check + retry logic
            success = await self._reconnect()
            if not success:
                if self._server_unavailable:
                    # Port was not open - server not set up, this is OK
                    logger.info(
                        "Local AI Server not available - provider will be inactive",
                        note="This is normal if you haven't set up local-ai-server"
                    )
                    return  # Don't raise, just mark as unavailable
                else:
                    # Port was open but connection failed after retries
                    raise RuntimeError("Failed to connect to Local AI Server after retries")
            logger.info("✅ Successfully connected to Local AI Server.")
        except RuntimeError:
            raise
        except Exception:
            logger.error("Failed to initialize connection to Local AI Server", exc_info=True)
            raise

    async def start_session(self, call_id: str, context: Optional[Dict[str, Any]] = None):
        try:
            # Check if already connected
            if self.websocket and self.websocket.state.name == "OPEN":
                logger.debug("WebSocket already connected, reusing connection", call_id=call_id)
                self._active_call_id = call_id
                # Ensure listener and sender tasks are running (may have crashed)
                if self._listener_task is None or self._listener_task.done():
                    logger.info("Restarting listener task for reused connection", call_id=call_id)
                    self._listener_task = asyncio.create_task(self._receive_loop())
                if self._sender_task is None or self._sender_task.done():
                    logger.info("Restarting sender task for reused connection", call_id=call_id)
                    self._sender_task = asyncio.create_task(self._send_loop())
                return
            
            # If not connected, initialize first
            await self.initialize()
            self._active_call_id = call_id
        except Exception:
            logger.error("Failed to start session", call_id=call_id, exc_info=True)
            raise

    async def send_audio(self, audio_chunk: bytes):
        """Send audio chunk to Local AI Server for STT processing."""
        try:
            logger.info("🎵 PROVIDER INPUT - Sending to Local AI Server",
                         bytes=len(audio_chunk),
                         queue_size=self._send_queue.qsize(),
                         input_mode=self.input_mode)
            
            # Enqueue for sender loop; drop if queue is full to avoid backpressure explosions
            await self._send_queue.put(audio_chunk)
            
        except Exception as e:
            logger.error("Failed to enqueue audio for Local AI Server", 
                         error=str(e), bytes=len(audio_chunk), exc_info=True)

    async def _send_loop(self):
        batch_ms = max(5, self._batch_ms)
        while True:
            try:
                # Wait for first chunk
                chunk = await self._send_queue.get()
                if chunk is None:
                    continue
                # Coalesce additional chunks available now (non-blocking)
                batch = [chunk]
                try:
                    while True:
                        batch.append(self._send_queue.get_nowait())
                except asyncio.QueueEmpty:
                    pass

                # Convert and send one aggregated message
                # Handle different input modes
                if self.input_mode == 'pcm16_16k':
                    # Already 16kHz PCM, just concatenate
                    pcm16k = b"".join(batch)
                elif self.input_mode == 'pcm16_8k':
                    # 8kHz PCM, resample to 16kHz
                    pcm8k = b"".join(batch)
                    pcm16k, self._resample_state_stt = resample_audio(pcm8k, 8000, 16000, state=self._resample_state_stt)
                else:
                    # µ-law 8kHz, convert to PCM then resample
                    pcm8k = b"".join(audioop.ulaw2lin(b, 2) for b in batch)
                    pcm16k, self._resample_state_stt = resample_audio(pcm8k, 8000, 16000, state=self._resample_state_stt)
                
                # Process audio batch for STT
                total_bytes = sum(len(b) for b in batch)
                logger.info("🔄 PROVIDER BATCH - Processing for STT",
                             frames=len(batch),
                             total_bytes=total_bytes,
                             input_mode=self.input_mode)
                
                msg = json.dumps({
                    "type": "audio", 
                    "data": base64.b64encode(pcm16k).decode('utf-8'),
                    "rate": 16000,
                    "format": "pcm16le",
                    "call_id": self._active_call_id,
                    "mode": self._mode  # "stt" for hybrid, "full" for all-local
                })
                try:
                    await self.websocket.send(msg)
                    logger.debug("WebSocket batch send successful", 
                                 frames=len(batch), 
                                 in_bytes=total_bytes,
                                 call_id=self._active_call_id,
                                 queue_depth=self._send_queue.qsize())
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning("WebSocket closed during send, attempting reconnect", 
                                   code=getattr(e, 'code', None), 
                                   reason=getattr(e, 'reason', None))
                    ok = await self._reconnect()
                    if ok:
                        try:
                            await self.websocket.send(msg)
                            logger.debug("WebSocket resend after reconnect successful", frames=len(batch))
                        except Exception as e:
                            logger.error("WebSocket resend failed after reconnect", error=str(e), exc_info=True)
                except Exception as e:
                    logger.error("WebSocket send error", error=str(e), exc_info=True)
                # Pace the loop
                await asyncio.sleep(batch_ms / 1000.0)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Sender loop error", exc_info=True)
                await asyncio.sleep(0.1)

    def set_input_mode(self, mode: str):
        # mode: 'mulaw8k' or 'pcm16_8k'
        self.input_mode = mode

    async def play_initial_greeting(self, call_id: str):
        """Play an initial greeting message to the caller."""
        try:
            # Ensure websocket connection exists
            if not self.websocket or self.websocket.state.name != "OPEN":
                await self.initialize()

            # Ensure the receive loop will attribute AgentAudio to this call
            self._active_call_id = call_id

            # Compute greeting to speak; skip if none
            greeting_text = self._initial_greeting or ""
            if not greeting_text.strip():
                logger.info("No initial greeting configured; skipping greeting playback", call_id=call_id)
                return

            # Send a TTS request that the local AI server understands; it will
            # reply with metadata (tts_audio) and then a binary payload, which
            # our receive loop will emit as AgentAudio for this call.
            tts_message = {
                "type": "tts_request",
                "call_id": call_id,
                "text": greeting_text,
            }

            await self.websocket.send(json.dumps(tts_message))
            logger.info("Sent greeting TTS request to Local AI Server", call_id=call_id)
            
            # Record greeting in conversation history
            if self.on_event:
                await self.on_event({
                    "type": "agent_transcript",
                    "call_id": call_id,
                    "text": greeting_text,
                })
                logger.debug("Recorded greeting in conversation history", call_id=call_id)
        except Exception as e:
            logger.error("Failed to send greeting message", call_id=call_id, error=str(e), exc_info=True)

    async def stop_session(self):
        # DON'T cancel the listener task - keep it running to receive AgentAudio events
        # if self._listener_task:
        #     self._listener_task.cancel()
        # DON'T close the WebSocket - keep it alive for reuse
        # if self.websocket:
        #     await self.websocket.close()
        #     logger.info("Disconnected from Local AI Server.")
        
        # Safety guard: drain send queue and discard pending frames
        queue_size = self._send_queue.qsize()
        if queue_size > 0:
            logger.debug("Draining send queue on stop_session", queue_size=queue_size)
            while not self._send_queue.empty():
                try:
                    self._send_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        
        # DON'T clear the active call ID immediately - keep it for AgentAudio processing
        # The call_id will be cleared when the TTS playback is complete
        # self._active_call_id = None
        logger.info("Provider session stopped, WebSocket connection and listener maintained. Call ID preserved for TTS processing.")

    async def clear_active_call_id(self):
        """Clear the active call ID after TTS playback is complete."""
        self._active_call_id = None
        logger.info("Active call ID cleared after TTS completion.")

    async def _receive_loop(self):
        if not self.websocket:
            return
        try:
            async for message in self.websocket:
                # Handle binary messages (raw audio)
                if isinstance(message, bytes):
                    # Safety guard: drop AgentAudio if no active call
                    if self._active_call_id is None:
                        logger.debug("Dropping AgentAudio - no active call", message_size=len(message))
                        continue
                    
                    audio_event = {'type': 'AgentAudio', 'data': message, 'call_id': self._active_call_id}
                    if self.on_event:
                        await self.on_event(audio_event)
                        # Heuristic: treat each binary message as a complete utterance
                        # so the engine will play it immediately. If the server later
                        # streams multi-frame replies, we can switch to explicit JSON
                        # delimiters (e.g., tts_start/tts_end) instead of this heuristic.
                        await self.on_event({
                            'type': 'AgentAudioDone',
                            'call_id': self._active_call_id,
                        })
                # Handle JSON messages (TTS responses, etc.)
                elif isinstance(message, str):
                    try:
                        data = json.loads(message)
                        # Handle TTS responses
                        if data.get("type") == "tts_response":
                            # Find the pending TTS response and complete it
                            text = data.get("text", "")
                            if text in self._pending_tts_responses:
                                future = self._pending_tts_responses.pop(text)
                                if not future.done():
                                    future.set_result(data)
                                    logger.info("TTS response received and delivered", text=text[:50])
                                else:
                                    logger.warning("TTS response received but future already completed", text=text[:50])
                            else:
                                logger.warning("TTS response received but no pending request found", text=text[:50])

                            # Additionally, if the TTS response carries base64 audio, decode and emit as AgentAudio
                            audio_b64 = data.get("audio_data") or data.get("audio")
                            if audio_b64:
                                try:
                                    audio_bytes = base64.b64decode(audio_b64)
                                except Exception:
                                    logger.warning("Invalid base64 in tts_response from Local AI Server")
                                    audio_bytes = b""

                                if audio_bytes and self.on_event:
                                    target_call_id = data.get("call_id") or self._active_call_id
                                    if target_call_id:
                                        try:
                                            await self.on_event({
                                                "type": "AgentAudio",
                                                "data": audio_bytes,
                                                "call_id": target_call_id,
                                            })
                                            await self.on_event({
                                                "type": "AgentAudioDone",
                                                "call_id": target_call_id,
                                            })
                                            # Signal farewell TTS received for hangup coordination
                                            if text and text.lower() == "goodbye":
                                                await self.on_event({
                                                    "type": "FarewellTTSReceived",
                                                    "call_id": target_call_id,
                                                    "audio_size": len(audio_bytes),
                                                })
                                                logger.info("🎤 Farewell TTS audio emitted", call_id=target_call_id, audio_size=len(audio_bytes))
                                        except Exception:
                                            logger.error("Failed to emit AgentAudio(/Done) for tts_response", exc_info=True)
                                    else:
                                        logger.debug("Dropping TTS audio - no active call to attribute", size=len(audio_bytes))
                        elif data.get("type") == "stt_result":
                            # Handle STT result - emit as transcript for conversation history
                            text = data.get("text", "").strip()
                            call_id = data.get("call_id") or self._active_call_id
                            is_final = data.get("is_final", True)
                            
                            if text and is_final and self.on_event:
                                await self.on_event({
                                    "type": "transcript",
                                    "call_id": call_id,
                                    "text": text,
                                })
                                logger.debug("Emitted user transcript for history", call_id=call_id, text=text[:50])
                        elif data.get("type") == "llm_response":
                            # Handle LLM response - parse for tool calls
                            llm_text = data.get("text", "")
                            call_id = data.get("call_id") or self._active_call_id
                            
                            # Parse the response for tool calls
                            clean_text, tool_calls = parse_response_with_tools(llm_text)
                            
                            # Emit agent transcript for conversation history (use clean text)
                            response_text = clean_text if clean_text else llm_text
                            if response_text and self.on_event:
                                await self.on_event({
                                    "type": "agent_transcript",
                                    "call_id": call_id,
                                    "text": response_text,
                                })
                                logger.debug("Emitted agent transcript for history", call_id=call_id, text=response_text[:50])
                            
                            if tool_calls:
                                logger.info(
                                    "🔧 Tool calls detected in local LLM response",
                                    call_id=call_id,
                                    tools=[tc.get("name") for tc in tool_calls]
                                )
                                # Emit tool call event for engine to handle
                                if self.on_event:
                                    await self.on_event({
                                        "type": "ToolCall",
                                        "call_id": call_id,
                                        "tool_calls": tool_calls,
                                        "text": clean_text,  # Text to speak (if any)
                                    })
                            else:
                                logger.debug(
                                    "LLM response received (no tools)",
                                    call_id=call_id,
                                    preview=llm_text[:80] if llm_text else "(empty)"
                                )
                        else:
                            logger.debug("Received JSON message from Local AI Server", message=data)
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON string message from Local AI Server", message=message)
                else:
                    logger.warning("Received unknown message type from Local AI Server", message_type=type(message))
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("Local AI Server connection closed", reason=str(e))
            # Attempt immediate reconnect
            logger.info("Attempting to reconnect to Local AI Server...")
            success = await self._reconnect()
            if success:
                logger.info("✅ Reconnected to Local AI Server, restarting receive loop")
                # Restart the receive loop
                if not self._listener_task or self._listener_task.done():
                    self._listener_task = asyncio.create_task(self._receive_loop())
            else:
                # Immediate reconnect failed - if we were previously connected,
                # start background reconnect task (non-blocking, up to 12 minutes)
                if self._was_connected:
                    logger.info(
                        "Immediate reconnect failed, starting background reconnect task",
                        note="Will check every 30s for up to 12 minutes"
                    )
                    self._start_background_reconnect()
                else:
                    logger.error("Failed to reconnect to Local AI Server")
        except Exception:
            logger.error("Error receiving events from Local AI Server", exc_info=True)

    async def speak(self, text: str):
        # This provider works by streaming STT->LLM->TTS on the server side.
        # Direct speech injection is not the primary mode of operation.
        logger.warning("Direct 'speak' method not implemented for this provider. Use the streaming pipeline.")
    
    async def text_to_speech(self, text: str) -> Optional[bytes]:
        """Generate TTS audio for the given text."""
        try:
            if not self.websocket or self.websocket.state.name != "OPEN":
                logger.error("WebSocket not connected for TTS")
                return None
            
            # Send TTS request to Local AI Server
            tts_message = {
                "type": "tts_request",
                "text": text,
                "call_id": self._active_call_id or "greeting"
            }
            
            await self.websocket.send(json.dumps(tts_message))
            logger.info("Sent TTS request to Local AI Server", text=text[:50] + "..." if len(text) > 50 else text)
            
            # Wait for TTS response using a future-based approach
            response_future = asyncio.Future()
            self._pending_tts_responses[text] = response_future
            
            try:
                # Wait for response with timeout
                response_data = await asyncio.wait_for(response_future, timeout=self.response_timeout)
                
                if response_data.get("type") == "tts_response" and response_data.get("audio_data"):
                    # Decode base64 audio data
                    audio_data = base64.b64decode(response_data["audio_data"])
                    logger.info("Received TTS audio data", size=len(audio_data))
                    return audio_data
                else:
                    logger.warning("Unexpected TTS response format", response=response_data)
                    return None
                    
            except asyncio.TimeoutError:
                logger.error("TTS request timed out")
                return None
            finally:
                # Clean up the pending response
                self._pending_tts_responses.pop(text, None)
                
        except Exception as e:
            logger.error("Failed to generate TTS", text=text, error=str(e), exc_info=True)
            return None
    
    def get_provider_info(self) -> Dict[str, Any]:
        return {
            "name": "LocalProvider",
            "type": "local_stream",
            "supported_codecs": self.supported_codecs,
        }
    
    # Backwards-compatible alias for older callers that treated readiness as "connected".
    # Prefer `is_connected()` for connection state.
    def is_connected_ready(self) -> bool:
        return self.is_connected()
