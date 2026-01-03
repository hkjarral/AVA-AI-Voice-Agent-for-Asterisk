"""
A new, simplified Asterisk ARI WebSocket Client.
Focuses on robust connection and logging to debug startup issues.
"""

import asyncio
import json
import os
import time
import uuid
import audioop
import wave
from typing import Dict, Any, Optional, Callable, List
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import websockets
import structlog
from urllib.parse import quote

import ssl
from websockets.exceptions import ConnectionClosed
from websockets.asyncio.client import ClientConnection

from .config import AsteriskConfig
from .logging_config import get_logger

logger = get_logger(__name__)

class ARIClient:
    """A client for interacting with the Asterisk REST Interface (ARI)."""

    def __init__(self, username: str, password: str, base_url: str, app_name: str, ssl_verify: bool = True):
        self.username = username
        self.password = password
        self.app_name = app_name
        self.http_url = base_url
        self.ssl_verify = ssl_verify
        # Determine WebSocket scheme based on HTTP scheme
        if base_url.startswith("https://"):
            ws_scheme = "wss"
            ws_host = base_url.replace("https://", "").split('/')[0]
        else:
            ws_scheme = "ws"
            ws_host = base_url.replace("http://", "").split('/')[0]
        safe_username = quote(username)
        safe_password = quote(password)
        self.ws_url = f"{ws_scheme}://{ws_host}/ari/events?api_key={safe_username}:{safe_password}&app={app_name}&subscribeAll=true&subscribe=ChannelAudioFrame"
        self.websocket: Optional[ClientConnection] = None
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.running = False
        self._should_reconnect = True  # Control flag for reconnect supervisor
        self._reconnect_attempt = 0
        self._max_reconnect_backoff = 60  # Max seconds between reconnect attempts
        self._connected = False  # True readiness state for /ready endpoint
        self.event_handlers: Dict[str, List[Callable]] = {}
        self.active_playbacks: Dict[str, str] = {}
        self.audio_frame_handler: Optional[Callable] = None

    def on_event(self, event_type: str, handler: Callable):
        """Alias for add_event_handler for backward compatibility."""
        self.add_event_handler(event_type, handler)

    @property
    def is_connected(self) -> bool:
        """Return true ARI connection state for readiness checks."""
        return self._connected and self.running and self.websocket is not None

    async def connect(self):
        """Connect to the ARI WebSocket and establish an HTTP session."""
        # Log connection details for troubleshooting
        ws_scheme = "wss" if self.ws_url.startswith("wss://") else "ws"
        http_scheme = "https" if self.http_url.startswith("https://") else "http"
        logger.info(
            "Connecting to ARI...",
            attempt=self._reconnect_attempt + 1,
            http_scheme=http_scheme,
            ws_scheme=ws_scheme,
            http_url=self.http_url,
        )
        self._connected = False
        try:
            # Configure SSL context for HTTPS/WSS
            ssl_context = None
            if http_scheme == "https":
                if self.ssl_verify:
                    ssl_context = ssl.create_default_context()
                else:
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                    logger.warning("SSL certificate verification disabled for ARI connection")

            # First, test HTTP connection to ensure ARI is available
            if self.http_session is None or self.http_session.closed:
                connector = aiohttp.TCPConnector(ssl=ssl_context) if ssl_context else None
                self.http_session = aiohttp.ClientSession(
                    auth=aiohttp.BasicAuth(self.username, self.password),
                    connector=connector
                )
            
            async with self.http_session.get(f"{self.http_url}/asterisk/info") as response:
                if response.status != 200:
                    raise ConnectionError(f"Failed to connect to ARI HTTP endpoint. Status: {response.status}")
                logger.info("Successfully connected to ARI HTTP endpoint.", scheme=http_scheme, ssl_verify=self.ssl_verify)

            # Then, connect to the WebSocket
            self.websocket = await websockets.connect(self.ws_url, ssl=ssl_context)
            self.running = True
            self._connected = True
            self._reconnect_attempt = 0  # Reset on successful connect
            logger.info("Successfully connected to ARI WebSocket.", scheme=ws_scheme)
        except Exception as e:
            self._connected = False
            logger.error("Failed to connect to ARI", error=str(e), attempt=self._reconnect_attempt + 1)
            if self.http_session and not self.http_session.closed:
                await self.http_session.close()
                self.http_session = None
            raise

    async def start_listening(self):
        """Start listening for events from the ARI WebSocket with automatic reconnection."""
        await self._listen_with_reconnect()

    async def _listen_with_reconnect(self):
        """
        Supervised listener loop with automatic reconnection.
        
        On disconnect:
        1. Mark as not connected (affects /ready)
        2. Exponential backoff delay
        3. Attempt reconnect
        4. Resume listening
        
        Loop continues until _should_reconnect is False (explicit shutdown).
        """
        while self._should_reconnect:
            # Ensure we're connected before listening
            if not self.running or not self.websocket:
                try:
                    await self.connect()
                except Exception as e:
                    self._reconnect_attempt += 1
                    backoff = min(2 ** self._reconnect_attempt, self._max_reconnect_backoff)
                    logger.warning(
                        "ARI connection failed, will retry",
                        attempt=self._reconnect_attempt,
                        backoff_seconds=backoff,
                        error=str(e)
                    )
                    await asyncio.sleep(backoff)
                    continue

            logger.info("Starting ARI event listener.")
            try:
                # Note: PlaybackFinished is registered by Engine.start(). Avoid duplicate registration here.
                async for message in self.websocket:
                    try:
                        event_data = json.loads(message)
                        event_type = event_data.get("type")
                        
                        # Handle audio frames from ExternalMedia connections
                        if event_type == "ChannelAudioFrame":
                            channel = event_data.get('channel', {})
                            channel_id = channel.get('id')
                            logger.debug("ChannelAudioFrame received", channel_id=channel_id)
                            asyncio.create_task(self._on_audio_frame(channel, event_data))
                        
                        # Handle other events
                        if event_type and event_type in self.event_handlers:
                            for handler in self.event_handlers[event_type]:
                                # Call the handler with just the event data
                                asyncio.create_task(handler(event_data))
                    except json.JSONDecodeError:
                        logger.warning("Failed to decode ARI event JSON", message=message)
                        
            except ConnectionClosed:
                self._connected = False
                self.running = False
                self.websocket = None
                if self._should_reconnect:
                    self._reconnect_attempt += 1
                    backoff = min(2 ** self._reconnect_attempt, self._max_reconnect_backoff)
                    logger.warning(
                        "ARI WebSocket connection closed, will reconnect",
                        attempt=self._reconnect_attempt,
                        backoff_seconds=backoff
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.info("ARI WebSocket closed (shutdown requested).")
                    break
                    
            except Exception as e:
                self._connected = False
                self.running = False
                self.websocket = None
                if self._should_reconnect:
                    self._reconnect_attempt += 1
                    backoff = min(2 ** self._reconnect_attempt, self._max_reconnect_backoff)
                    logger.error(
                        "ARI listener error, will reconnect",
                        attempt=self._reconnect_attempt,
                        backoff_seconds=backoff,
                        error=str(e),
                        exc_info=True
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error("ARI listener error (shutdown requested).", exc_info=True)
                    break
        
        logger.info("ARI reconnect supervisor stopped.")

    async def disconnect(self):
        """Disconnect from the ARI WebSocket and close the HTTP session.
        
        Also stops the reconnect supervisor to prevent automatic reconnection.
        """
        self._should_reconnect = False  # Stop the reconnect supervisor
        self._connected = False
        self.running = False
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
            self.http_session = None
        logger.info("Disconnected from ARI.")

    def add_event_handler(self, event_type: str, handler: Callable):
        """Register a handler for a specific ARI event type."""
        if event_type not in self.event_handlers:
            self.event_handlers[event_type] = []
        self.event_handlers[event_type].append(handler)
        logger.debug("Added event handler", event_type=event_type, handler=handler.__name__)

    async def handle_audio_frame(self, event_data: dict, audio_handler: Callable):
        """Handle audio frames from snoop channels."""
        channel = event_data.get('channel', {})
        channel_id = channel.get('id')
        audio_data = event_data.get('audio', {})
        
        if channel_id and audio_data:
            # Extract the raw audio data
            audio_payload = audio_data.get('data')
            if audio_payload:
                # Convert from base64 if needed, or handle as raw bytes
                import base64
                try:
                    raw_audio = base64.b64decode(audio_payload)
                    await audio_handler(channel_id, raw_audio)
                except Exception as e:
                    logger.error("Error processing audio frame", error=str(e))

    async def handle_dtmf_received(self, event_data: dict, dtmf_handler: Callable):
        """Handle DTMF events from snoop channels."""
        channel = event_data.get('channel', {})
        channel_id = channel.get('id')
        digit = event_data.get('digit')
        
        if channel_id and digit:
            await dtmf_handler(channel_id, digit)

    async def send_command(
        self,
        method: str,
        resource: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        tolerate_statuses: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Send a command to the ARI HTTP endpoint.

        tolerate_statuses: Optional list of HTTP status codes that should not be logged as errors
        (useful for idempotent cleanup cases like 404 on DELETE of already-gone resources).
        """
        url = f"{self.http_url}/{resource}"
        
        # Handle channelVars specially - they need to be in the JSON body, not query params
        if params and "channelVars" in params:
            channel_vars = params.pop("channelVars")
            if data is None:
                data = {}
            data["channelVars"] = channel_vars
        
        try:
            async with self.http_session.request(method, url, json=data, params=params) as response:
                if response.status >= 400:
                    reason = await response.text()
                    if tolerate_statuses and response.status in tolerate_statuses:
                        logger.debug(
                            "ARI command tolerated non-2xx",
                            method=method,
                            url=url,
                            status=response.status,
                            reason=reason,
                        )
                    else:
                        logger.error("ARI command failed", method=method, url=url, status=response.status, reason=reason)
                    # Return a dict regardless for callers to branch on status
                    return {"status": response.status, "reason": reason}
                if response.status == 204: # No Content
                    return {"status": response.status}
                return await response.json()
        except aiohttp.ClientError as e:
            logger.error("ARI HTTP request failed", exc_info=True)
            return {"status": 500, "reason": str(e)}

    async def originate_channel(
        self,
        *,
        endpoint: str,
        app: str,
        app_args: str = "",
        timeout: int = 60,
        channel_vars: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Originate an outbound channel via ARI (POST /channels).

        This mirrors the engine's existing originate usage patterns and relies on the
        ARI server to place the call and enter the Stasis app on answer.
        """
        params: Dict[str, Any] = {
            "endpoint": str(endpoint),
            "app": str(app),
            "timeout": str(int(timeout)),
        }
        if app_args:
            params["appArgs"] = str(app_args)
        if channel_vars:
            params["channelVars"] = channel_vars
        return await self.send_command("POST", "channels", params=params)

    async def continue_in_dialplan(
        self,
        channel_id: str,
        *,
        context: str,
        extension: str = "s",
        priority: int = 1,
        label: Optional[str] = None,
    ) -> bool:
        """Return a Stasis channel back to the dialplan (POST /channels/{id}/continue)."""
        params: Dict[str, Any] = {
            "context": str(context),
            "extension": str(extension),
            "priority": str(int(priority)),
        }
        if label:
            params["label"] = str(label)
        resp = await self.send_command("POST", f"channels/{channel_id}/continue", params=params)
        status = resp.get("status") if isinstance(resp, dict) else None
        if status is not None and int(status) >= 400:
            return False
        return True

    async def answer_channel(self, channel_id: str):
        """Answer a channel."""
        logger.info("Answering channel", channel_id=channel_id)
        await self.send_command("POST", f"channels/{channel_id}/answer")

    async def hangup_channel(self, channel_id: str):
        """Hang up a channel."""
        logger.info("Hanging up channel", channel_id=channel_id)
        # We add a check here. If the command fails with a 404, we log it
        # as a debug message instead of an error, as this can happen in race
        # conditions during cleanup and is not necessarily a critical failure.
        response = await self.send_command("DELETE", f"channels/{channel_id}", tolerate_statuses=[404])
        if response and response.get("status") == 404:
            logger.debug("Channel hangup failed (404), likely already hung up.", channel_id=channel_id)

    async def execute_application(self, channel_id: str, app_name: str, app_data: str) -> bool:
        """Execute an Asterisk application on a channel."""
        try:
            logger.info("Executing application on channel", 
                       channel_id=channel_id, 
                       app_name=app_name, 
                       app_data=app_data)
            
            response = await self.send_command(
                "POST", 
                f"channels/{channel_id}/applications/{app_name}",
                data={"app": app_name, "appArgs": app_data}
            )
            
            if response:
                logger.info("Application executed successfully", 
                           channel_id=channel_id, 
                           app_name=app_name)
                return True
            else:
                logger.error("Failed to execute application", 
                           channel_id=channel_id, 
                           app_name=app_name)
                return False
                
        except Exception as e:
            logger.error("Error executing application", 
                        channel_id=channel_id, 
                        app_name=app_name, 
                        error=str(e))
            return False

    async def play_media(self, channel_id: str, media_uri: str) -> Optional[Dict[str, Any]]:
        """Play media on a channel."""
        logger.info("Playing media on channel", channel_id=channel_id, media_uri=media_uri)
        return await self.send_command("POST", f"channels/{channel_id}/play", data={"media": media_uri})

    async def play_media_on_channel_with_id(self, channel_id: str, media_uri: str, playback_id: str) -> bool:
        """Play media on a channel with a deterministic playback ID."""
        try:
            data = {"media": media_uri, "playbackId": playback_id}
            response = await self.send_command("POST", f"channels/{channel_id}/play", data=data)
            if response and response.get("id") == playback_id:
                logger.info(
                    "Channel playback started with deterministic ID",
                    channel_id=channel_id,
                    media_uri=media_uri,
                    playback_id=playback_id,
                )
                return True
            logger.error(
                "Failed to start channel playback with deterministic ID",
                channel_id=channel_id,
                media_uri=media_uri,
                playback_id=playback_id,
                response=response,
            )
            return False
        except Exception:
            logger.error(
                "Error starting channel playback with deterministic ID",
                channel_id=channel_id,
                media_uri=media_uri,
                playback_id=playback_id,
                exc_info=True,
            )
            return False

    async def set_channel_var(self, channel_id: str, variable: str, value: str = "") -> bool:
        """Set a channel variable via ARI.

        Note: Asterisk allows setting dialplan function-like vars (e.g. TALK_DETECT(set))
        through the same interface, which is required to enable talk detection events.
        """
        try:
            resp = await self.send_command(
                "POST",
                f"channels/{channel_id}/variable",
                data={"variable": variable, "value": value},
            )
            # Some ARI implementations return {} on success.
            return resp is not None
        except Exception:
            logger.error("Failed to set channel variable", channel_id=channel_id, variable=variable, exc_info=True)
            return False


    async def create_bridge(self, bridge_type: str = "mixing") -> Optional[str]:
        """Create a new bridge for channel mixing."""
        try:
            response = await self.send_command(
                "POST",
                "bridges",
                data={
                    "type": bridge_type,
                    "name": f"bridge_{uuid.uuid4().hex[:8]}"
                }
            )
            
            if response.get("id"):
                logger.info("Bridge created", bridge_id=response["id"], bridge_type=bridge_type)
                return response["id"]
            else:
                logger.error("Failed to create bridge", response=response)
                return None
        except Exception as e:
            logger.error("Error creating bridge", error=str(e))
            return None

    async def stop_playback(self, playback_id: str) -> bool:
        """Stop an active playback by its playbackId."""
        try:
            response = await self.send_command("DELETE", f"playbacks/{playback_id}")
            status = response.get("status") if isinstance(response, dict) else None
            if status is not None:
                if 200 <= int(status) < 300:
                    logger.info("Playback stopped", playback_id=playback_id, status=status)
                    return True
                logger.debug("Failed to stop playback (may already be finished)", playback_id=playback_id, response=response)
                return False
            # Some ARI implementations return empty body on success
            logger.info("Playback stop response without status; assuming success", playback_id=playback_id)
            return True
        except Exception:
            logger.error("Error stopping playback", playback_id=playback_id, exc_info=True)
            return False

    async def record_channel(
        self,
        channel_id: str,
        name: str,
        format: str = "wav",
        if_exists: str = "overwrite",
        max_duration_seconds: int = 180,
        max_silence_seconds: int = 0,
        beep: bool = False,
        terminate_on: str = "none",
    ) -> bool:
        """Start recording a channel via ARI (preferred over invoking MixMonitor).

        ARI route: POST /ari/channels/{channelId}/record
        Common params: name, format, ifExists, maxDurationSeconds, maxSilenceSeconds, beep, terminateOn
        """
        try:
            logger.info(
                "Starting ARI channel recording",
                channel_id=channel_id,
                name=name,
                format=format,
                ifExists=if_exists,
            )
            # Some ARI implementations accept JSON body for these params
            # ARI expects query params; ensure all values are strings to satisfy yarl
            payload = {
                "name": str(name),
                "format": str(format),
                "ifExists": str(if_exists),
                "maxDurationSeconds": str(int(max_duration_seconds)),
                "maxSilenceSeconds": str(int(max_silence_seconds)),
                "beep": "true" if bool(beep) else "false",
                "terminateOn": str(terminate_on),
            }
            response = await self.send_command(
                "POST",
                f"channels/{channel_id}/record",
                params=payload,
            )
            status = response.get("status") if isinstance(response, dict) else None
            if status is not None and not (200 <= int(status) < 300):
                logger.error(
                    "Failed to start ARI channel recording",
                    channel_id=channel_id,
                    response=response,
                )
                return False
            logger.info("ARI channel recording started", channel_id=channel_id, name=name)
            return True
        except Exception:
            logger.error("Error starting ARI channel recording", channel_id=channel_id, exc_info=True)
            return False

    async def add_channel_to_bridge(self, bridge_id: str, channel_id: str) -> bool:
        """Add a channel to a bridge."""
        try:
            response = await self.send_command(
                "POST",
                f"bridges/{bridge_id}/addChannel",
                data={"channel": channel_id}
            )

            # send_command returns {"status": 204} for No Content on success
            status = response.get("status") if isinstance(response, dict) else None
            if status is not None:
                if 200 <= int(status) < 300:
                    logger.info("Channel added to bridge", bridge_id=bridge_id, channel_id=channel_id, status=status)
                    return True
                # Idempotency: Asterisk can return a conflict if the channel is already in the bridge.
                # Treat this as success to make attach retries safe.
                reason = str(response.get("reason", "") or "")
                if int(status) in (409, 422) and ("already" in reason.lower()) and ("bridge" in reason.lower()):
                    logger.info(
                        "Channel already in bridge (treated as success)",
                        bridge_id=bridge_id,
                        channel_id=channel_id,
                        status=status,
                    )
                    return True
                else:
                    logger.error("Failed to add channel to bridge", bridge_id=bridge_id, channel_id=channel_id, status=status, response=response)
                    return False

            # If no explicit status was returned, assume success and log response for traceability
            logger.info("Channel add-to-bridge response without status; assuming success", bridge_id=bridge_id, channel_id=channel_id, response=response)
            return True

        except Exception as e:
            logger.error("Error adding channel to bridge", 
                        bridge_id=bridge_id, 
                        channel_id=channel_id, 
                        error=str(e))
            return False


    async def play_audio_response(self, channel_id: str, audio_data: bytes):
        """Saves TTS audio to shared media directory and commands Asterisk to play it."""
        logger.info("Starting audio playback process", channel_id=channel_id, audio_size=len(audio_data))
        
        # CRITICAL: Validate channel before attempting playback
        logger.debug("Validating channel before playback", channel_id=channel_id)
        if not await self.validate_channel_for_playback(channel_id):
            logger.warning("Channel validation failed - skipping audio playback", channel_id=channel_id)
            return
        
        unique_filename = f"response-{uuid.uuid4()}.ulaw"
        # Use the shared RAM space for high-performance audio file storage
        # Put files in the ai-generated subdirectory to match the symlink
        container_path = f"/mnt/asterisk_media/ai-generated/{unique_filename}"
        # Use the symlinked path that Asterisk can access
        # Remove .ulaw extension since Asterisk adds it automatically
        asterisk_media_uri = f"sound:ai-generated/{unique_filename[:-5]}"

        try:
            # TTS now generates ulaw data directly, no conversion needed
            logger.debug("Writing ulaw audio file to ai-generated subdirectory", path=container_path)
            
            logger.debug("Writing ulaw audio file", path=container_path, size=len(audio_data))
            with open(container_path, "wb") as f:
                f.write(audio_data)
            
            # Audio generated as ulaw format at 8000 Hz for Asterisk compatibility
            logger.debug("Ulaw audio file written (generated as ulaw at 8000 Hz)", path=container_path)
            
            # Set file permissions for Asterisk readability via group
            # Files inherit group ownership from setgid directory (set up by preflight.sh)
            # No chown needed - appuser is member of asterisk group
            try:
                os.chmod(container_path, 0o664)  # Group-readable
            except Exception as e:
                logger.warning("Failed to set file permissions", path=container_path, error=str(e))
            
            logger.debug("Verifying file creation", path=container_path, exists=os.path.exists(container_path))
            if os.path.exists(container_path):
                file_size = os.path.getsize(container_path)
                logger.debug("File created successfully", path=container_path, size=file_size)
                
                # File is ready for playback
                logger.debug("Attempting to play media", channel_id=channel_id, media_uri=asterisk_media_uri)

            playback = await self.play_media(channel_id, asterisk_media_uri)
            if playback and 'id' in playback:
                self.active_playbacks[playback['id']] = container_path
                logger.info("Audio playback initiated successfully", 
                          channel_id=channel_id, 
                          filename=unique_filename, 
                          playback_id=playback['id'])
            else:
                logger.error("Failed to initiate audio playback", 
                           channel_id=channel_id, 
                           playback_response=playback)
        except Exception as e:
            logger.error("Failed to play audio file", channel_id=channel_id, error=str(e), exc_info=True)

    async def _on_playback_finished(self, event):
        """Event handler for cleaning up audio files after a short delay."""
        playback_id = event.get("playback", {}).get("id")
        file_path = self.active_playbacks.pop(playback_id, None)
        if file_path:
            # Add a delay to ensure Asterisk has finished with the file
            import asyncio
            await asyncio.sleep(2.0)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.debug("Successfully deleted audio file", file_path=file_path)
                except OSError:
                    logger.error("Error deleting audio file", file_path=file_path, exc_info=True)
        
        # Call the engine's PlaybackFinished handler if it exists
        if hasattr(self, 'engine') and hasattr(self.engine, '_on_playback_finished'):
            await self.engine._on_playback_finished(event)

    async def cleanup_call_files(self, channel_id: str):
        """Clean up any remaining audio files for a specific call."""
        import os
        import glob
        
        # Clean up any files that might be associated with this call
        # Look for files in the ai-generated directory that might be orphaned
        ai_generated_dir = "/mnt/asterisk_media/ai-generated"
        if os.path.exists(ai_generated_dir):
            # Find any files that might be associated with this call
            # This is a safety net for files that weren't cleaned up by playback_finished
            pattern = os.path.join(ai_generated_dir, "response-*.ulaw")
            files = glob.glob(pattern)
            
            for file_path in files:
                try:
                    # Check if file is older than 30 seconds (safety check)
                    if os.path.getmtime(file_path) < (time.time() - 30):
                        os.remove(file_path)
                        logger.debug("Cleaned up orphaned audio file", file_path=file_path)
                except OSError as e:
                    logger.debug("Could not clean up file", file_path=file_path, error=str(e))

    async def _on_audio_frame(self, channel, event):
        """Handles incoming raw audio frames from the snoop channel."""
        try:
            logger.debug("Processing audio frame", channel_id=channel.get('id'), event_keys=list(event.keys()))
            # Get the audio frame data
            frame_data = event.get("frame", {})
            audio_payload = frame_data.get("data", "")
            
            # Log frame format information
            frame_format = frame_data.get("format", "unknown")
            frame_samples = frame_data.get("samples", 0)
            logger.debug("Audio frame details", format=frame_format, samples=frame_samples, payload_length=len(audio_payload))
            
            if audio_payload:
                # Decode base64 audio data
                import base64
                audio_data = base64.b64decode(audio_payload)
                logger.debug("Decoded audio data", bytes=len(audio_data), format=frame_format)
                
                # Forward to the audio frame handler
                if self.audio_frame_handler:
                    await self.audio_frame_handler(audio_data)
                    logger.debug("Forwarded audio frame to handler")
                else:
                    logger.debug(f"Received audio frame but no handler set: {len(audio_data)} bytes")
            else:
                logger.debug("Received audio frame with no data")
        except Exception as e:
            logger.error("Error processing audio frame", error=str(e), exc_info=True)

    def set_audio_frame_handler(self, handler: Callable):
        """Set the handler for incoming audio frames."""
        self.audio_frame_handler = handler


    async def play_audio_file(self, channel_id: str, file_path: str) -> bool:
        """Play an audio file to the specified channel with enhanced error handling."""
        try:
            import time
            start_time = time.time()
            
            # Enhanced file verification with detailed logging (non-blocking)
            for attempt in range(15):  # Try up to 15 times (1.5 seconds total)
                if os.path.exists(file_path):
                    if os.access(file_path, os.R_OK):
                        file_size = os.path.getsize(file_path)
                        if file_size > 0:
                            logger.debug(f"File verified: {file_path} ({file_size} bytes) - attempt {attempt + 1}")
                            break
                        else:
                            logger.warning(f"File exists but is empty: {file_path} - attempt {attempt + 1}")
                    else:
                        logger.warning(f"File exists but not readable: {file_path} (permissions: {oct(os.stat(file_path).st_mode)[-3:]}) - attempt {attempt + 1}")
                else:
                    logger.warning(f"File not found: {file_path} - attempt {attempt + 1}")
                
                await asyncio.sleep(0.1)  # 100ms delay (non-blocking)
            
            # Final verification
            if not os.path.exists(file_path):
                logger.error(f"Audio file not found after 15 attempts: {file_path}")
                return False
            
            if not os.access(file_path, os.R_OK):
                logger.error(f"Audio file not readable: {file_path} (permissions: {oct(os.stat(file_path).st_mode)[-3:]})")
                return False

            file_size = os.path.getsize(file_path)
            if file_size == 0:
                logger.error(f"Audio file is empty: {file_path}")
                return False

            # Set channel variable for debugging
            await self.send_command(
                "POST",
                f"channels/{channel_id}/variable",
                data={"variable": "AUDIO_FILE_PATH", "value": file_path}
            )
            
            # Use ARI to play the file
            result = await self.send_command(
                "POST",
                f"channels/{channel_id}/play",
                data={"media": f"sound:{file_path}"}
            )
            
            elapsed_time = (time.time() - start_time) * 1000  # milliseconds
            if result:
                logger.info(f"Playing audio file {file_path} ({file_size} bytes) on channel {channel_id} - took {elapsed_time:.1f}ms")
                return True
            else:
                logger.error(f"Failed to play audio file {file_path} after {elapsed_time:.1f}ms")
                return False
                
        except Exception as e:
            logger.error(f"Error playing audio file {file_path}: {e}")
            return False

    async def create_audio_file_from_ulaw(self, ulaw_data: bytes, sample_rate: int = 8000) -> str:
        """Convert ulaw audio data to a WAV file and return the file path."""
        try:
            # Convert ulaw to linear PCM
            pcm_data = audioop.ulaw2lin(ulaw_data, 2)  # 2 bytes per sample (16-bit)
            
            # Create timestamped filename for better debugging
            import time
            timestamp = int(time.time() * 1000)  # milliseconds
            filename = f"audio_{timestamp}_{len(pcm_data)}.wav"
            temp_file_path = f"/tmp/asterisk-audio/{filename}"
            try:
                os.makedirs("/tmp/asterisk-audio", mode=0o700, exist_ok=True)
                try:
                    os.chmod("/tmp/asterisk-audio", 0o700)
                except Exception:
                    pass
            except Exception:
                pass
            
            # Write WAV file
            with wave.open(temp_file_path, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 2 bytes per sample (16-bit)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(pcm_data)
            
            # Set proper permissions for Asterisk to read the file
            os.chmod(temp_file_path, 0o600)  # rw-------
            
            # Force filesystem sync (non-blocking) and verify file exists
            await asyncio.to_thread(os.sync)
            
            # Wait and verify file is accessible (non-blocking)
            for attempt in range(10):  # Try up to 10 times (1 second total)
                if os.path.exists(temp_file_path) and os.access(temp_file_path, os.R_OK):
                    file_size = os.path.getsize(temp_file_path)
                    if file_size > 0:
                        logger.debug(f"Created WAV file: {temp_file_path} ({file_size} bytes) - attempt {attempt + 1}")
                        return temp_file_path
                await asyncio.sleep(0.1)  # 100ms delay (non-blocking)
            
            logger.error(f"Failed to create accessible WAV file after 10 attempts: {temp_file_path}")
            return ""
            
        except Exception as e:
            logger.error(f"Error creating audio file from ulaw: {e}")
            return ""


    async def cleanup_audio_file(self, file_path: str, delay: float = 5.0):
        """Clean up an audio file after a delay."""
        try:
            await asyncio.sleep(delay)
            if os.path.exists(file_path):
                os.unlink(file_path)
                logger.debug(f"Cleaned up audio file: {file_path}")
        except Exception as e:
            logger.error(f"Error cleaning up audio file {file_path}: {e}")

    async def stop_audio_streaming(self, channel_id: str) -> bool:
        """Stop audio streaming and clean up media channel and bridge."""
        media_info = self.active_media_channels.pop(channel_id, None)
        if not media_info:
            logger.warning("No active media channel found to stop for channel.", channel_id=channel_id)
            return True # Not a failure if it's already gone

        media_channel_id = media_info['media_channel_id']
        bridge_id = media_info['bridge_id']
        logger.info("Stopping audio streaming and cleaning up resources...",
                    channel_id=channel_id, media_channel_id=media_channel_id, bridge_id=bridge_id)
        
        try:
            # We don't need to remove channels from the bridge, just destroy it
            await self.destroy_bridge(bridge_id)
            # Hanging up the original channel is handled by StasisEnd
            await self.hangup_channel(media_channel_id)
            logger.info("Successfully cleaned up bridge and media channel.", bridge_id=bridge_id, media_channel_id=media_channel_id)
            return True
        except Exception as e:
            logger.error("Error during audio streaming cleanup", exc_info=True)
            return False


    async def is_channel_active(self, channel_id: str) -> bool:
        """Check if a channel is still active and in Stasis application."""
        try:
            # Try to get channel information from ARI
            result = await self.send_command("GET", f"channels/{channel_id}")
            if result and result.get("id") == channel_id:
                # Channel exists, now check if it's in our Stasis app
                state = result.get("state", "")
                logger.debug("Channel status check", 
                           channel_id=channel_id, 
                           state=state,
                           exists=True)
                return state in ["Up", "Ring", "Ringing", "Dialing"]
            else:
                logger.debug("Channel not found in ARI", 
                           channel_id=channel_id,
                           result=result)
                return False
        except Exception as e:
            logger.debug("Error checking channel status", 
                        channel_id=channel_id, 
                        error=str(e))
            return False

    async def validate_channel_for_playback(self, channel_id: str) -> bool:
        """Validate that a channel is ready for audio playback."""
        try:
            # First check if channel is active
            if not await self.is_channel_active(channel_id):
                logger.warning("Channel validation failed: channel not active", 
                             channel_id=channel_id)
                return False
            
            # Additional check: try to get channel info to ensure it's accessible
            result = await self.send_command("GET", f"channels/{channel_id}")
            if not result:
                logger.warning("Channel validation failed: cannot retrieve channel info", 
                             channel_id=channel_id)
                return False
            
            # Check if channel is in the correct state for playback
            state = result.get("state", "")
            if state not in ["Up"]:
                logger.warning("Channel validation failed: not in correct state for playback", 
                             channel_id=channel_id, 
                             state=state)
                return False
            
            logger.debug("Channel validation successful", 
                        channel_id=channel_id, 
                        state=state)
            return True
            
        except Exception as e:
            logger.warning("Channel validation failed: exception occurred", 
                         channel_id=channel_id, 
                         error=str(e))
            return False

    async def create_external_media_channel(self, app: str, external_host: str, format: str = "ulaw", direction: str = "both", encapsulation: str = "rtp") -> Optional[Dict[str, Any]]:
        """
        Create an External Media channel for RTP communication.
        
        Args:
            app: ARI application name
            external_host: External host:port for RTP (e.g., "127.0.0.1:18080")
            format: Audio format (default: "ulaw")
            direction: Media direction (default: "both") - both, sendonly, recvonly
            encapsulation: Transport protocol (default: "rtp")
            
        Returns:
            Channel information dict or None if failed
        """
        try:
            response = await self.send_command(
                "POST",
                "channels/externalMedia",
                data={
                    "app": app,
                    "external_host": external_host,
                    "format": format,
                    "direction": direction,
                    "encapsulation": encapsulation
                }
            )
            
            if response and response.get("id"):
                logger.info("External Media channel created", 
                           channel_id=response["id"], 
                           external_host=external_host,
                           format=format)
                return response
            else:
                logger.error("Failed to create External Media channel", response=response)
                return None
                
        except Exception as e:
            logger.error("Error creating External Media channel", 
                        external_host=external_host, 
                        error=str(e))
            return None

    async def play_audio_via_bridge(self, bridge_id: str, media_uri: str) -> Optional[str]:
        """
        Play audio to a bridge.
        
        Args:
            bridge_id: Bridge ID to play audio to
            media_uri: Media URI (e.g., "sound:ai-generated/greeting-123")
            
        Returns:
            Playback ID string or None if failed
        """
        try:
            data = {"media": media_uri}
            response = await self.send_command("POST", f"bridges/{bridge_id}/play", data=data)
            
            if response and response.get("id"):
                playback_id = response["id"]
                logger.info("Bridge playback started", 
                           bridge_id=bridge_id, 
                           media_uri=media_uri,
                           playback_id=playback_id)
                return playback_id
            else:
                logger.error("Failed to start bridge playback", 
                            bridge_id=bridge_id, 
                            media_uri=media_uri,
                            response=response)
                return None
                
        except Exception as e:
            logger.error("Error starting bridge playback", 
                        bridge_id=bridge_id, 
                        media_uri=media_uri,
                        error=str(e))
            return None

    async def play_media_on_bridge_with_id(self, bridge_id: str, media_uri: str, playback_id: str) -> bool:
        """
        Play media on bridge with a deterministic playback ID.
        
        Args:
            bridge_id: Bridge ID to play audio to
            media_uri: Media URI (e.g., "sound:ai-generated/greeting-123")
            playback_id: Deterministic playback ID to use
            
        Returns:
            True if successful, False otherwise
        """
        try:
            data = {"media": media_uri, "playbackId": playback_id}
            response = await self.send_command("POST", f"bridges/{bridge_id}/play", data=data)
            
            if response and response.get("id") == playback_id:
                logger.info("Bridge playback started with deterministic ID", 
                           bridge_id=bridge_id, 
                           media_uri=media_uri,
                           playback_id=playback_id)
                return True
            else:
                logger.error("Failed to start bridge playback with deterministic ID", 
                            bridge_id=bridge_id, 
                            media_uri=media_uri,
                            playback_id=playback_id,
                            response=response)
                return False
                
        except Exception as e:
            logger.error("Error starting bridge playback with deterministic ID", 
                        bridge_id=bridge_id, 
                        media_uri=media_uri,
                        playback_id=playback_id,
                        error=str(e))
            return False

    async def create_external_media(self, external_host: str, external_port: int, fmt: str = "ulaw", direction: str = "both") -> Optional[str]:
        """
        Create an External Media channel and return the channel ID.
        
        Args:
            external_host: External host IP (e.g., "127.0.0.1")
            external_port: External port (e.g., 18080)
            fmt: Audio format (default: "ulaw")
            direction: Media direction (default: "both")
            
        Returns:
            Channel ID string or None if failed
        """
        external_host_port = f"{external_host}:{external_port}"
        response = await self.create_external_media_channel(
            app=self.app_name,
            external_host=external_host_port,
            format=fmt,
            direction=direction
        )
        
        if response and response.get("id"):
            return response["id"]
        else:
            logger.error("Failed to create External Media channel", 
                        external_host=external_host_port,
                        format=fmt,
                        direction=direction)
            return None

    async def remove_channel_from_bridge(self, bridge_id: str, channel_id: str) -> bool:
        """Remove a channel from a bridge."""
        try:
            response = await self.send_command(
                "POST",
                f"bridges/{bridge_id}/removeChannel",
                data={"channel": channel_id}
            )

            status = response.get("status") if isinstance(response, dict) else None
            if status is not None:
                if 200 <= int(status) < 300:
                    logger.info("Channel removed from bridge", bridge_id=bridge_id, channel_id=channel_id, status=status)
                    return True
                else:
                    logger.error("Failed to remove channel from bridge", bridge_id=bridge_id, channel_id=channel_id, status=status, response=response)
                    return False

            logger.info("Channel remove-from-bridge response without status; assuming success", bridge_id=bridge_id, channel_id=channel_id, response=response)
            return True
            
        except Exception as e:
            logger.error("Error removing channel from bridge", 
                        bridge_id=bridge_id, 
                        channel_id=channel_id, 
                        error=str(e))
            return False

    async def destroy_bridge(self, bridge_id: str) -> bool:
        """Destroy a bridge."""
        try:
            response = await self.send_command("DELETE", f"bridges/{bridge_id}", tolerate_statuses=[404])
            
            status = response.get("status") if isinstance(response, dict) else None
            if status is not None:
                if 200 <= int(status) < 300:
                    logger.info("Bridge destroyed", bridge_id=bridge_id, status=status)
                    return True
                if int(status) == 404:
                    logger.debug("Bridge destroy idempotent - already gone", bridge_id=bridge_id)
                    return True
                else:
                    logger.error("Failed to destroy bridge", bridge_id=bridge_id, status=status, response=response)
                    return False

            logger.info("Bridge destroy response without status; assuming success", bridge_id=bridge_id, response=response)
            return True
            
        except Exception as e:
            logger.error("Error destroying bridge", bridge_id=bridge_id, error=str(e))
            return False
