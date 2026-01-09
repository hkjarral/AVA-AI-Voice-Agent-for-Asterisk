"""
ElevenLabs Conversational AI Provider

Full agent provider that integrates ElevenLabs' Conversational AI platform
for real-time voice conversations with STT, LLM, and TTS capabilities.

WebSocket Protocol:
- Endpoint: wss://api.elevenlabs.io/v1/convai/conversation
- Auth: xi-api-key header or signed URL
- Audio: PCM16 base64 encoded, 16kHz mono
"""
import asyncio
import base64
import json
import logging
import os
import audioop
import struct
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass

import websockets
from websockets.asyncio.client import ClientConnection

from .base import AIProviderInterface, ProviderCapabilities, ProviderCapabilitiesMixin
from .elevenlabs_config import ElevenLabsAgentConfig

logger = logging.getLogger(__name__)


@dataclass
class ElevenLabsSessionState:
    """Tracks the state of an ElevenLabs conversation session."""
    conversation_id: Optional[str] = None
    is_agent_speaking: bool = False
    is_user_speaking: bool = False
    pending_audio_chunks: List[bytes] = None
    total_audio_sent: int = 0
    total_audio_received: int = 0
    
    def __post_init__(self):
        if self.pending_audio_chunks is None:
            self.pending_audio_chunks = []


class ElevenLabsAgentProvider(AIProviderInterface, ProviderCapabilitiesMixin):
    """
    ElevenLabs Conversational AI full agent provider.
    
    Provides STT + LLM + TTS in a single WebSocket connection,
    similar to Deepgram Voice Agent and OpenAI Realtime providers.
    """
    
    # ElevenLabs WebSocket endpoint
    CONVAI_WS_URL = "wss://api.elevenlabs.io/v1/convai/conversation"
    
    def __init__(
        self,
        config: ElevenLabsAgentConfig,
        on_event: Callable[[Dict[str, Any]], None],
        tool_registry: Optional[Any] = None,
    ):
        super().__init__(on_event)
        self.config = config
        self.tool_registry = tool_registry
        
        # WebSocket connection
        self._ws: Optional[ClientConnection] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        
        # Session state
        self._call_id: Optional[str] = None
        self._session_state = ElevenLabsSessionState()
        self._connected = False
        self._closing = False
        
        # Audio resampling state
        self._resample_state_in = None  # For input resampling
        self._resample_state_out = None  # For output resampling
        
        # Turn latency tracking (Milestone 21 - Call History)
        self._turn_start_time: Optional[float] = None
        self._turn_first_audio_received: bool = False
        self._session_store = None  # Set via engine for latency tracking
        
        logger.info(f"[elevenlabs] Provider initialized with agent_id={config.agent_id[:8]}...")
    
    @property
    def supported_codecs(self) -> List[str]:
        """Returns supported codec names."""
        return ["linear16", "pcm16", "ulaw"]
    
    def is_ready(self) -> bool:
        """Check if provider is properly configured with required API key and agent ID."""
        api_key = getattr(self.config, 'api_key', None) or os.getenv("ELEVENLABS_API_KEY", "")
        agent_id = getattr(self.config, 'agent_id', None) or os.getenv("ELEVENLABS_AGENT_ID", "")
        return bool(api_key and str(api_key).strip() and agent_id and str(agent_id).strip())

    def get_capabilities(self) -> ProviderCapabilities:
        """Return static capability hints for the orchestrator."""
        return ProviderCapabilities(
            input_encodings=["linear16", "pcm16", "ulaw"],
            input_sample_rates_hz=[8000, 16000],
            output_encodings=["linear16", "pcm16"],
            output_sample_rates_hz=[16000, 22050],
            preferred_chunk_ms=20,
            can_negotiate=False,
            is_full_agent=True,
            has_native_vad=True,
            has_native_barge_in=True,
            requires_continuous_audio=True,
        )
    
    async def start_session(
        self,
        call_id: str,
        on_event: Optional[Callable] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Initialize the connection to ElevenLabs Conversational AI.
        
        Args:
            call_id: Unique identifier for this call
            on_event: Optional callback for events (uses self.on_event if not provided)
            context: Optional context with greeting, instructions, tools, etc.
        """
        self._call_id = call_id
        self._session_state = ElevenLabsSessionState()
        
        # Reset connection state for new session
        self._connected = False
        self._closing = False
        self._ws = None
        self._receive_task = None
        self._keepalive_task = None
        self._resample_state_in = None
        self._resample_state_out = None
        
        if on_event:
            self.on_event = on_event
        
        # Get API key from config or environment
        api_key = self.config.api_key or os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY not configured")
        
        agent_id = self.config.agent_id or os.getenv("ELEVENLABS_AGENT_ID", "")
        if not agent_id:
            raise ValueError("ELEVENLABS_AGENT_ID not configured")
        
        logger.info(f"[elevenlabs] [{call_id}] Connecting to ElevenLabs Conversational AI...")
        
        # For authenticated agents, get a signed URL first
        signed_url = await self._get_signed_url(api_key, agent_id, call_id)
        
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    signed_url,
                    max_size=16 * 1024 * 1024,  # 16MB max message size
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ),
                timeout=10.0,
            )
            self._connected = True
            logger.info(f"[elevenlabs] [{call_id}] WebSocket connected")
            
            # Start receive loop
            self._receive_task = asyncio.create_task(self._receive_loop())
            
            # Send initial configuration if context provided
            if context:
                await self._send_session_config(context)
            
            # Emit session started event
            await self.on_event({
                "type": "session_started",
                "call_id": call_id,
                "provider": "elevenlabs_agent",
            })
            
        except asyncio.TimeoutError:
            logger.error(f"[elevenlabs] [{call_id}] Connection timeout")
            raise ConnectionError("ElevenLabs connection timeout")
        except Exception as e:
            logger.error(f"[elevenlabs] [{call_id}] Connection failed: {e}")
            raise
    
    async def _get_signed_url(self, api_key: str, agent_id: str, call_id: str) -> str:
        """
        Get a signed URL for connecting to an authenticated ElevenLabs agent.
        
        For agents with authentication enabled, we need to request a signed URL
        from the ElevenLabs API before connecting via WebSocket.
        """
        import aiohttp
        
        url = f"https://api.elevenlabs.io/v1/convai/conversation/get_signed_url?agent_id={agent_id}"
        headers = {
            "xi-api-key": api_key,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"[elevenlabs] [{call_id}] Failed to get signed URL: {response.status} - {error_text}")
                        raise ConnectionError(f"Failed to get signed URL: {response.status}")
                    
                    data = await response.json()
                    signed_url = data.get("signed_url")
                    
                    if not signed_url:
                        raise ConnectionError("No signed_url in response")
                    
                    logger.info(f"[elevenlabs] [{call_id}] Got signed URL for authenticated agent")
                    return signed_url
                    
        except aiohttp.ClientError as e:
            logger.error(f"[elevenlabs] [{call_id}] HTTP error getting signed URL: {e}")
            raise ConnectionError(f"HTTP error: {e}")
    
    async def _send_session_config(self, context: Dict[str, Any]) -> None:
        """Send session configuration to ElevenLabs."""
        if not self._ws or not self._connected:
            return
        
        # Build conversation initiation data
        init_data = {}
        
        # Get caller info for personalization
        caller_name = context.get("caller_name", "there")
        caller_id = context.get("caller_id", "")
        
        # Add dynamic variables for personalization (used in system prompt)
        dynamic_vars = {
            "caller_name": caller_name,
            "caller_id": caller_id,
        }
        init_data["dynamic_variables"] = dynamic_vars
        logger.info(f"[elevenlabs] [{self._call_id}] Dynamic variables: {dynamic_vars}")
        
        # Build conversation config override
        # This sends the first message with variables already substituted
        # NOTE: Requires "First message" override enabled in ElevenLabs Security settings
        conversation_override = {
            "agent": {}
        }
        
        # Override first message with caller_name substituted
        # This bypasses ElevenLabs template rendering which fails if variable is in first message
        if context.get("greeting"):
            # Substitute variables in greeting
            greeting = context["greeting"]
            try:
                greeting = greeting.format(caller_name=caller_name, caller_id=caller_id)
            except (KeyError, ValueError) as e:
                logger.warning(f"[elevenlabs] [{self._call_id}] Greeting format failed: {e}. Using as-is.")
            conversation_override["agent"]["first_message"] = greeting
            logger.info(f"[elevenlabs] [{self._call_id}] Override first_message: {greeting[:50]}...")
        
        # Add custom system prompt if provided (overrides ElevenLabs dashboard prompt)
        if context.get("instructions"):
            # Substitute variables in system prompt too
            prompt = context["instructions"]
            try:
                prompt = prompt.format(caller_name=caller_name, caller_id=caller_id)
            except (KeyError, ValueError) as e:
                logger.warning(f"[elevenlabs] [{self._call_id}] System prompt format failed: {e}. Using as-is.")
            conversation_override["agent"]["prompt"] = {"prompt": prompt}
            logger.info(f"[elevenlabs] [{self._call_id}] Override system_prompt: {len(prompt)} chars")
        
        # Only add override if we have something to override
        if conversation_override["agent"]:
            init_data["conversation_config_override"] = conversation_override
        
        # Note: Tools are configured in ElevenLabs dashboard, not sent via WebSocket
        # See docs/Provider-ElevenLabs-Setup.md for tool configuration instructions
        
        # Send initialization message if we have config
        # NOTE: Per ElevenLabs AsyncAPI spec, properties must be at root level (flat structure)
        # NOT nested inside a "conversation_initiation_client_data" key
        if init_data:
            message = {"type": "conversation_initiation_client_data"}
            message.update(init_data)  # Flatten: dynamic_variables, conversation_config_override at root
            await self._ws.send(json.dumps(message))
            logger.debug(f"[elevenlabs] [{self._call_id}] Sent session config: {list(message.keys())}")
    
    async def send_audio(
        self,
        audio_chunk: bytes,
        sample_rate: Optional[int] = None,
        encoding: Optional[str] = None,
    ) -> None:
        """
        Send audio chunk to ElevenLabs.
        
        Audio is expected to be from telephony (μ-law 8kHz) and will be
        converted to PCM16 16kHz for ElevenLabs.
        """
        if not self._ws or not self._connected or self._closing:
            logger.debug(f"[elevenlabs] [{self._call_id}] send_audio skipped: ws={self._ws is not None}, connected={self._connected}, closing={self._closing}")
            return
        
        # Determine input format
        in_rate = sample_rate or self.config.input_sample_rate_hz
        in_encoding = encoding or self.config.input_encoding
        
        # Log first audio chunk for debugging
        if self._session_state.total_audio_sent == 0:
            logger.info(f"[elevenlabs] [{self._call_id}] First audio chunk: {len(audio_chunk)} bytes, rate={in_rate}, encoding={in_encoding}")
        
        # Convert to PCM16 if needed
        pcm16_audio = audio_chunk
        
        if in_encoding in ("ulaw", "mulaw"):
            # Decode μ-law to PCM16
            pcm16_audio = audioop.ulaw2lin(audio_chunk, 2)
        elif in_encoding == "alaw":
            pcm16_audio = audioop.alaw2lin(audio_chunk, 2)
        
        # Resample to 16kHz if needed
        target_rate = self.config.provider_input_sample_rate_hz
        if in_rate != target_rate:
            pcm16_audio, self._resample_state_in = audioop.ratecv(
                pcm16_audio, 2, 1, in_rate, target_rate, self._resample_state_in
            )
        
        # Encode to base64
        audio_b64 = base64.b64encode(pcm16_audio).decode("utf-8")
        
        # Send audio message
        message = {
            "user_audio_chunk": audio_b64,
        }
        
        try:
            await self._ws.send(json.dumps(message))
            self._session_state.total_audio_sent += len(pcm16_audio)
            # Log every 10 chunks for debugging
            chunks_sent = self._session_state.total_audio_sent // 640  # 640 bytes = 20ms @ 16kHz
            if chunks_sent % 50 == 0 and chunks_sent > 0:
                logger.debug(f"[elevenlabs] [{self._call_id}] Audio progress: {self._session_state.total_audio_sent} bytes sent")
        except Exception as e:
            logger.warning(f"[elevenlabs] [{self._call_id}] Failed to send audio: {e}")
    
    async def send_interrupt(self) -> None:
        """Send interrupt signal to stop agent speech (barge-in)."""
        if not self._ws or not self._connected:
            return
        
        message = {"type": "interrupt"}
        try:
            await self._ws.send(json.dumps(message))
            logger.debug(f"[elevenlabs] [{self._call_id}] Sent interrupt")
        except Exception as e:
            logger.warning(f"[elevenlabs] [{self._call_id}] Failed to send interrupt: {e}")
    
    async def stop_session(self) -> None:
        """Close the connection and clean up resources."""
        self._closing = True
        logger.info(f"[elevenlabs] [{self._call_id}] Stopping session...")
        
        # Cancel tasks
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await asyncio.wait_for(self._receive_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await asyncio.wait_for(self._keepalive_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        
        # Close WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.debug(f"[elevenlabs] [{self._call_id}] WebSocket close error: {e}")
            self._ws = None
        
        self._connected = False
        
        # Emit session ended event
        await self.on_event({
            "type": "session_ended",
            "call_id": self._call_id,
            "provider": "elevenlabs_agent",
            "audio_sent_bytes": self._session_state.total_audio_sent,
            "audio_received_bytes": self._session_state.total_audio_received,
        })
        
        logger.info(f"[elevenlabs] [{self._call_id}] Session stopped")
    
    async def _receive_loop(self) -> None:
        """Process incoming WebSocket messages from ElevenLabs."""
        logger.debug(f"[elevenlabs] [{self._call_id}] Receive loop started")
        
        try:
            async for message in self._ws:
                if self._closing:
                    break
                
                try:
                    await self._handle_message(message)
                except Exception as e:
                    logger.error(f"[elevenlabs] [{self._call_id}] Error handling message: {e}")
                    
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"[elevenlabs] [{self._call_id}] WebSocket closed: {e}")
        except asyncio.CancelledError:
            logger.debug(f"[elevenlabs] [{self._call_id}] Receive loop cancelled")
        except Exception as e:
            logger.error(f"[elevenlabs] [{self._call_id}] Receive loop error: {e}")
        finally:
            self._connected = False
    
    async def _handle_message(self, raw_message: str) -> None:
        """Handle a single WebSocket message."""
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning(f"[elevenlabs] [{self._call_id}] Invalid JSON message")
            return
        
        msg_type = data.get("type", "")
        
        # Log all message types for debugging
        if msg_type and msg_type not in ("ping", "internal_vad_score", "internal_turn_probability", "internal_tentative_agent_response"):
            logger.debug(f"[elevenlabs] [{self._call_id}] Received message type: {msg_type}")
        
        # Handle different message types
        if msg_type == "conversation_initiation_metadata":
            await self._handle_conversation_init(data)
        
        elif msg_type == "audio":
            await self._handle_audio(data)
        
        elif msg_type == "agent_response":
            await self._handle_agent_response(data)
        
        elif msg_type == "user_transcript":
            await self._handle_user_transcript(data)
        
        elif msg_type == "agent_response_correction":
            await self._handle_agent_correction(data)
        
        elif msg_type == "interruption":
            await self._handle_interruption(data)
        
        elif msg_type == "ping":
            await self._handle_ping(data)
        
        elif msg_type == "internal_vad_score":
            # VAD score updates - used for voice activity detection
            pass  # Handled internally by ElevenLabs
        
        elif msg_type == "internal_turn_probability":
            # Turn-taking probability updates
            pass
        
        elif msg_type == "internal_tentative_agent_response":
            # Tentative response (before finalization)
            pass
        
        elif msg_type == "client_tool_call":
            await self._handle_tool_call(data)
        
        elif msg_type == "error":
            await self._handle_error(data)
        
        else:
            logger.debug(f"[elevenlabs] [{self._call_id}] Unhandled message type: {msg_type}")
    
    async def _handle_conversation_init(self, data: Dict[str, Any]) -> None:
        """Handle conversation initialization metadata."""
        metadata = data.get("conversation_initiation_metadata_event", {})
        self._session_state.conversation_id = metadata.get("conversation_id")
        
        logger.info(
            f"[elevenlabs] [{self._call_id}] Conversation initialized: "
            f"{self._session_state.conversation_id}"
        )
        
        await self.on_event({
            "type": "conversation_initialized",
            "call_id": self._call_id,
            "conversation_id": self._session_state.conversation_id,
        })
    
    async def _handle_audio(self, data: Dict[str, Any]) -> None:
        """Handle audio chunk from agent."""
        audio_event = data.get("audio_event", {})
        audio_b64 = audio_event.get("audio_base_64", "")
        
        if not audio_b64:
            # Try alternative field name
            audio_b64 = data.get("audio", "")
            if not audio_b64:
                logger.debug(f"[elevenlabs] [{self._call_id}] Empty audio event: {list(data.keys())}")
                return
        
        # Track turn latency on first audio output (Milestone 21 - Call History)
        if self._turn_start_time is not None and not self._turn_first_audio_received:
            import time
            self._turn_first_audio_received = True
            turn_latency_ms = (time.time() - self._turn_start_time) * 1000
            # Save to session for call history
            if self._session_store and self._call_id:
                try:
                    call_id_copy = self._call_id
                    latency_copy = turn_latency_ms
                    async def save_latency():
                        try:
                            session = await self._session_store.get_by_call_id(call_id_copy)
                            if session:
                                session.turn_latencies_ms.append(latency_copy)
                                await self._session_store.upsert_call(session)
                                logger.debug(f"[elevenlabs] Turn latency saved: {round(latency_copy, 1)}ms")
                        except Exception as e:
                            logger.debug(f"[elevenlabs] Failed to save turn latency: {e}")
                    asyncio.create_task(save_latency())
                except Exception as e:
                    logger.debug(f"[elevenlabs] Failed to create latency save task: {e}")
            logger.info(f"[elevenlabs] [{self._call_id}] Turn latency: {round(turn_latency_ms, 1)}ms")
            # Reset for next turn
            self._turn_start_time = None
        
        # Log first audio received
        if self._session_state.total_audio_received == 0:
            logger.info(f"[elevenlabs] [{self._call_id}] First agent audio received")
        
        # Decode base64 audio (PCM16 16kHz from ElevenLabs)
        pcm16_audio = base64.b64decode(audio_b64)
        self._session_state.total_audio_received += len(pcm16_audio)
        
        # Convert to telephony format if needed
        output_audio = self._convert_output_audio(pcm16_audio)
        
        # Emit audio event
        await self.on_event({
            "type": "AgentAudio",
            "call_id": self._call_id,
            "data": output_audio,
            "encoding": self.config.target_encoding,
            "sample_rate": self.config.target_sample_rate_hz,
        })
    
    def _convert_output_audio(self, pcm16_audio: bytes) -> bytes:
        """Convert PCM16 audio from ElevenLabs to telephony format."""
        # ElevenLabs outputs PCM16 at 16kHz
        source_rate = self.config.output_sample_rate_hz
        target_rate = self.config.target_sample_rate_hz
        target_encoding = self.config.target_encoding
        
        output = pcm16_audio
        
        # Resample if needed
        if source_rate != target_rate:
            output, self._resample_state_out = audioop.ratecv(
                output, 2, 1, source_rate, target_rate, self._resample_state_out
            )
        
        # Encode to μ-law or a-law if needed
        if target_encoding in ("ulaw", "mulaw"):
            output = audioop.lin2ulaw(output, 2)
        elif target_encoding == "alaw":
            output = audioop.lin2alaw(output, 2)
        
        return output
    
    async def _handle_agent_response(self, data: Dict[str, Any]) -> None:
        """Handle agent text response (transcript of what agent said)."""
        response = data.get("agent_response_event", {})
        text = response.get("agent_response", "")
        
        if text:
            logger.debug(f"[elevenlabs] [{self._call_id}] Agent: {text[:100]}...")
            
            await self.on_event({
                "type": "agent_transcript",
                "call_id": self._call_id,
                "text": text,
                "role": "assistant",
            })
    
    async def _handle_user_transcript(self, data: Dict[str, Any]) -> None:
        """Handle user transcript (STT result)."""
        # ElevenLabs API format: {"type": "user_transcript", "user_transcription_event": {...}}
        # The nested event contains the actual transcript
        transcript_event = data.get("user_transcription_event", {})
        
        # Try multiple possible field names for the transcript text
        text = (
            transcript_event.get("user_transcript", "") or
            transcript_event.get("transcript", "") or
            transcript_event.get("text", "") or
            data.get("user_transcript", "") or
            data.get("transcript", "")
        )
        
        # Debug: log nested structure if no text found
        if not text:
            logger.warning(f"[elevenlabs] [{self._call_id}] user_transcript no text, event_keys: {list(transcript_event.keys())}, data_keys: {list(data.keys())}")
        
        # ElevenLabs user_transcript messages are always final (no interim transcripts)
        # Start timer on every user transcript - measures: speech end → first AI audio
        if text:
            import time
            self._turn_start_time = time.time()
            self._turn_first_audio_received = False
            logger.info(f"[elevenlabs] [{self._call_id}] User: {text[:100]}... (latency timer started)")
            
            await self.on_event({
                "type": "transcript",
                "call_id": self._call_id,
                "text": text,
                "is_final": True,  # ElevenLabs sends final transcripts only
                "role": "user",
            })
    
    async def _handle_agent_correction(self, data: Dict[str, Any]) -> None:
        """Handle agent response correction (when interrupted)."""
        correction = data.get("agent_response_correction_event", {})
        original = correction.get("original_agent_response", "")
        corrected = correction.get("corrected_agent_response", "")
        
        logger.debug(
            f"[elevenlabs] [{self._call_id}] Agent correction: "
            f"'{original[:30]}...' -> '{corrected[:30]}...'"
        )
        
        await self.on_event({
            "type": "agent_correction",
            "call_id": self._call_id,
            "original": original,
            "corrected": corrected,
        })
    
    async def _handle_interruption(self, data: Dict[str, Any]) -> None:
        """Handle interruption event (barge-in detected)."""
        logger.debug(f"[elevenlabs] [{self._call_id}] Interruption detected")
        
        await self.on_event({
            "type": "interruption",
            "call_id": self._call_id,
        })
    
    async def _handle_ping(self, data: Dict[str, Any]) -> None:
        """Handle ping message - send pong response."""
        ping_event = data.get("ping_event", {})
        event_id = ping_event.get("event_id")
        
        if event_id:
            pong = {
                "type": "pong",
                "event_id": event_id,
            }
            try:
                await self._ws.send(json.dumps(pong))
            except Exception as e:
                logger.warning(f"[elevenlabs] [{self._call_id}] Failed to send pong: {e}")

    async def _handle_tool_call(self, data: Dict[str, Any]) -> None:
        """Handle tool/function call request from ElevenLabs."""
        tool_call = data.get("client_tool_call", {})
        tool_name = tool_call.get("tool_name", "")
        tool_call_id = tool_call.get("tool_call_id", "")
        parameters = tool_call.get("parameters", {})
        
        logger.info(f"[elevenlabs] [{self._call_id}] Tool call: {tool_name}")
        
        # Emit tool call event for engine to handle
        await self.on_event({
            "type": "function_call",
            "call_id": self._call_id,
            "function_name": tool_name,
            "function_call_id": tool_call_id,
            "parameters": parameters,
        })
    
    async def send_tool_result(
        self,
        tool_call_id: str,
        result: Any,
        is_error: bool = False,
    ) -> None:
        """Send tool execution result back to ElevenLabs."""
        if not self._ws or not self._connected:
            return
        
        message = {
            "type": "client_tool_result",
            "tool_call_id": tool_call_id,
            "result": json.dumps(result) if not isinstance(result, str) else result,
            "is_error": is_error,
        }
        
        try:
            await self._ws.send(json.dumps(message))
            logger.debug(f"[elevenlabs] [{self._call_id}] Sent tool result for {tool_call_id}")
        except Exception as e:
            logger.warning(f"[elevenlabs] [{self._call_id}] Failed to send tool result: {e}")
    
    async def _handle_error(self, data: Dict[str, Any]) -> None:
        """Handle error message from ElevenLabs."""
        error = data.get("error", {})
        code = error.get("code", "unknown")
        message = error.get("message", "Unknown error")
        
        logger.error(f"[elevenlabs] [{self._call_id}] Error: {code} - {message}")
        
        await self.on_event({
            "type": "error",
            "call_id": self._call_id,
            "error_code": code,
            "error_message": message,
        })
