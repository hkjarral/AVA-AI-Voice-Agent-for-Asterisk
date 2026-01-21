"""
External Media Engine for Asterisk AI Voice Agent.
Uses ARI External Media channels for bidirectional RTP communication.
"""

import asyncio
import contextlib
import os
import signal
import uuid
import json
import base64
import audioop
from typing import Dict, Any, Optional

from .ari_client import ARIClient
from aiohttp import web
from .config import AppConfig, load_config
from .logging_config import get_logger, configure_logging
from .providers.base import AIProviderInterface
from .rtp_server import RTPServer
from .providers.deepgram import DeepgramProvider
from .providers.local import LocalProvider

logger = get_logger(__name__)

class ExternalMediaEngine:
    """
    External Media Engine for Asterisk AI Voice Agent.
    
    This engine uses ARI External Media channels to establish bidirectional
    RTP communication with Asterisk, enabling real-time audio processing
    with AI providers.
    """
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.ari_client: Optional[ARIClient] = None
        self._ari_listener_task: Optional[asyncio.Task] = None
        self.rtp_server: Optional[RTPServer] = None
        self.providers: Dict[str, AIProviderInterface] = {}
        self.active_calls: Dict[str, Dict[str, Any]] = {}
        self.external_media_channels: Dict[str, str] = {}  # call_id -> external_channel_id
        self.bridges: Dict[str, str] = {}  # call_id -> bridge_id
        self.running = False
        
        # Initialize providers
        self._initialize_providers()
        
        # Initialize RTP server
        rtp_config = getattr(config, 'rtp', None)
        if rtp_config:
            base_port = getattr(rtp_config, "port", getattr(rtp_config, "rtp_port", 18080))
            range_value = getattr(rtp_config, "port_range", None)
            if isinstance(range_value, (list, tuple)) and len(range_value) == 2:
                port_range = (int(range_value[0]), int(range_value[1]))
            else:
                port_range = None
            self.rtp_server = RTPServer(
                host=rtp_config.host,
                port=base_port,
                engine_callback=self._on_rtp_audio_received,
                codec=getattr(rtp_config, "codec", "ulaw"),
                port_range=port_range,
            )
        else:
            raise ValueError("RTP configuration not found in config")
    
    def _initialize_providers(self):
        """Initialize AI providers."""
        try:
            # Initialize Local provider
            if hasattr(self.config, 'providers') and 'local' in self.config.providers:
                local_config = self.config.providers['local']
                if local_config.get('enabled', True):
                    # Create a wrapper for the async callback
                    def on_event_wrapper(event):
                        asyncio.create_task(self._on_provider_event(event))
                    self.providers['local'] = LocalProvider(local_config, on_event_wrapper)
                    logger.info("Local provider initialized")
            
            # Initialize Deepgram provider
            if hasattr(self.config, 'providers') and 'deepgram' in self.config.providers:
                deepgram_config = self.config.providers['deepgram']
                if deepgram_config.get('api_key'):
                    def on_event_wrapper(event):
                        asyncio.create_task(self._on_provider_event(event))
                    self.providers['deepgram'] = DeepgramProvider(deepgram_config, self.config.llm, on_event_wrapper)
                    logger.info("Deepgram provider initialized")
            
            logger.info(f"Initialized {len(self.providers)} providers: {list(self.providers.keys())}")
            
        except Exception as e:
            logger.error(f"Failed to initialize providers: {e}")
            raise
    
    async def _on_provider_event(self, event: Dict[str, Any]):
        """Handle events from AI providers."""
        try:
            event_type = event.get('type')
            call_id = event.get('call_id')
            
            if event_type == 'AgentAudio':
                # Handle audio response from LocalProvider
                audio_data = event.get('data')
                if audio_data and call_id:
                    # Convert ulaw to PCM for RTP
                    pcm_data = audioop.ulaw2lin(audio_data, 2)
                    await self.rtp_server.send_audio(call_id, pcm_data)
                    logger.debug("Audio response sent via RTP", call_id=call_id, size=len(pcm_data))
            
            elif event_type == 'transcript':
                # Handle transcript from STT
                transcript = event.get('transcript')
                if transcript and call_id:
                    logger.info("Transcript received", call_id=call_id, transcript=transcript)
            
            elif event_type == 'error':
                # Handle provider errors
                error = event.get('error')
                logger.error("Provider error", call_id=call_id, error=error)
            
        except Exception as e:
            logger.error(f"Error handling provider event: {e}")
    
    async def start(self):
        """Start the External Media engine."""
        if self.running:
            logger.warning("External Media engine already running")
            return
        
        try:
            # Start RTP server
            if self.rtp_server:
                await self.rtp_server.start()
                logger.info("RTP server started")
            
            # Initialize ARI client
            self.ari_client = ARIClient(
                username=self.config.asterisk.username,
                password=self.config.asterisk.password,
                base_url=f"{self.config.asterisk.scheme}://{self.config.asterisk.host}:{self.config.asterisk.port}/ari",
                app_name=self.config.asterisk.app_name,
                ssl_verify=self.config.asterisk.ssl_verify
            )
            
            # Set up event handlers
            self.ari_client.add_event_handler("StasisStart", self._on_stasis_start)
            self.ari_client.add_event_handler("ChannelDestroyed", self._on_channel_destroyed)
            self.ari_client.add_event_handler("PlaybackFinished", self._on_playback_finished)
            
            # Start ARI reconnect supervisor (initial connect happens in the background).
            if not self._ari_listener_task or self._ari_listener_task.done():
                self._ari_listener_task = asyncio.create_task(self.ari_client.start_listening())
                self._ari_listener_task.add_done_callback(self._on_ari_listener_task_done)
            logger.info("ARI reconnect supervisor started")
            
            self.running = True
            logger.info("External Media engine started successfully")
            
        except Exception as e:
            logger.error(f"Failed to start External Media engine: {e}")
            await self.stop()
            raise
    
    async def stop(self):
        """Stop the External Media engine."""
        if not self.running:
            return
        
        self.running = False
        
        try:
            # Stop RTP server
            if self.rtp_server:
                await self.rtp_server.stop()
                logger.info("RTP server stopped")
            
            # Disconnect ARI
            if self.ari_client:
                await self.ari_client.disconnect()
                logger.info("ARI client disconnected")

            task = getattr(self, "_ari_listener_task", None)
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            
            logger.info("External Media engine stopped")
            
        except Exception as e:
            logger.error(f"Error stopping External Media engine: {e}")

    def _on_ari_listener_task_done(self, task: "asyncio.Task") -> None:
        """Log background ARI listener task failures (prevents swallowed exceptions)."""
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        except Exception as err:
            logger.debug("Failed inspecting ARI listener task result", error=str(err))
            return
        if exc:
            logger.error("ARI listener task exited unexpectedly", error=str(exc))
    
    async def _on_stasis_start(self, event: Dict[str, Any]):
        """Handle StasisStart event - create External Media channel and bridge."""
        try:
            channel = event.get('channel', {})
            channel_id = channel.get('id')
            caller_info = channel.get('caller', {})
            
            logger.info("StasisStart event received", 
                       channel_id=channel_id,
                       caller_name=caller_info.get('name'),
                       caller_number=caller_info.get('number'))
            
            if not channel_id:
                logger.error("No channel ID in StasisStart event")
                return
            
            # Skip External Media channels - only process caller channels
            if channel_id in self.external_media_channels.values():
                logger.debug("Skipping StasisStart for External Media channel", channel_id=channel_id)
                return
            
            # Check if call is already in progress
            if channel_id in self.active_calls:
                logger.warning("Call already in progress", channel_id=channel_id)
                return
            
            # Answer the channel
            await self.ari_client.answer_channel(channel_id)
            logger.info("Channel answered", channel_id=channel_id)
            
            # Create RTP session
            rtp_port = await self.rtp_server.create_session(channel_id)
            logger.info("RTP session created", channel_id=channel_id, rtp_port=rtp_port)
            
            # Create External Media channel
            external_host = f"{self.rtp_server.host}:{rtp_port}"
            external_channel = await self.ari_client.create_external_media_channel(
                app=self.config.asterisk.app_name,
                external_host=external_host,
                format="ulaw",
                encapsulation="rtp"
            )
            
            if not external_channel:
                logger.error("Failed to create External Media channel", channel_id=channel_id)
                await self._cleanup_call(channel_id)
                return
            
            external_channel_id = external_channel['id']
            self.external_media_channels[channel_id] = external_channel_id
            logger.info("External Media channel created", 
                       channel_id=channel_id, 
                       external_channel_id=external_channel_id,
                       external_host=external_host)
            
            # Create bridge
            bridge_id = await self.ari_client.create_bridge("mixing")
            if not bridge_id:
                logger.error("Failed to create bridge", channel_id=channel_id)
                await self._cleanup_call(channel_id)
                return
            
            self.bridges[channel_id] = bridge_id
            logger.info("Bridge created", channel_id=channel_id, bridge_id=bridge_id)
            
            # Add both channels to bridge
            caller_success = await self.ari_client.add_channel_to_bridge(bridge_id, channel_id)
            external_success = await self.ari_client.add_channel_to_bridge(bridge_id, external_channel_id)
            
            if not caller_success or not external_success:
                logger.error("Failed to add channels to bridge", 
                           channel_id=channel_id, 
                           bridge_id=bridge_id,
                           caller_success=caller_success,
                           external_success=external_success)
                await self._cleanup_call(channel_id)
                return
            
            logger.info("Channels added to bridge", 
                       channel_id=channel_id, 
                       bridge_id=bridge_id,
                       external_channel_id=external_channel_id)
            
            # Start AI pipeline
            await self._start_ai_pipeline(channel_id)
            
        except Exception as e:
            logger.error(f"Error handling StasisStart: {e}", exc_info=True)
            if 'channel_id' in locals():
                await self._cleanup_call(channel_id)
    
    async def _start_ai_pipeline(self, call_id: str):
        """Start AI pipeline for a call."""
        try:
            # Get provider
            provider_name = self.config.default_provider
            provider = self.providers.get(provider_name)
            
            if not provider:
                logger.error("Provider not found", provider=provider_name)
                return
            
            # Store call info
            self.active_calls[call_id] = {
                "provider": provider,
                "conversation_state": "greeting",
                "bridge_id": self.bridges.get(call_id),
                "external_channel_id": self.external_media_channels.get(call_id)
            }
            
            logger.info("AI pipeline started", call_id=call_id, provider=provider_name)
            
            # Play initial greeting
            await provider.play_initial_greeting(call_id)
            
        except Exception as e:
            logger.error(f"Failed to start AI pipeline for call {call_id}: {e}")
    
    
    async def _on_rtp_audio_received(self, call_id: str, ssrc: int, pcm_data: bytes):
        """Handle incoming RTP audio from caller."""
        try:
            call_info = self.active_calls.get(call_id)
            if not call_info:
                logger.debug("No active call for RTP audio", call_id=call_id)
                return
            
            provider = call_info["provider"]
            
            # Convert PCM to ulaw for LocalProvider
            import audioop
            ulaw_data = audioop.lin2ulaw(pcm_data, 2)
            
            # Send audio to provider via WebSocket
            if hasattr(provider, 'send_audio') and provider.websocket:
                audio_message = {
                    "type": "audio",
                    "call_id": call_id,
                    "data": base64.b64encode(ulaw_data).decode('utf-8'),
                    "format": "ulaw"
                }
                await provider.websocket.send(json.dumps(audio_message))
                logger.debug("Audio sent to LocalProvider", call_id=call_id, size=len(ulaw_data))
            
        except Exception as e:
            logger.error(f"Error processing RTP audio for call {call_id}: {e}")
    
    async def _on_channel_destroyed(self, event: Dict[str, Any]):
        """Handle ChannelDestroyed event - cleanup call resources."""
        try:
            channel = event.get('channel', {})
            channel_id = channel.get('id')
            
            if not channel_id:
                return
            
            logger.info("Channel destroyed", channel_id=channel_id)
            await self._cleanup_call(channel_id)
            
        except Exception as e:
            logger.error(f"Error handling ChannelDestroyed: {e}")
    
    async def _on_playback_finished(self, event: Dict[str, Any]):
        """Handle PlaybackFinished event."""
        try:
            playback_id = event.get('playback', {}).get('id')
            if playback_id in self.ari_client.active_playbacks:
                # Clean up the playback reference
                del self.ari_client.active_playbacks[playback_id]
                logger.debug("Playback finished and cleaned up", playback_id=playback_id)
                
        except Exception as e:
            logger.error(f"Error handling PlaybackFinished: {e}")
    
    async def _cleanup_call(self, call_id: str):
        """Cleanup all resources for a call."""
        try:
            logger.info("Cleaning up call", call_id=call_id)
            
            # Cleanup RTP session
            if self.rtp_server:
                await self.rtp_server.cleanup_session(call_id)
            
            # Remove from bridge and destroy bridge
            bridge_id = self.bridges.pop(call_id, None)
            if bridge_id:
                await self.ari_client.destroy_bridge(bridge_id)
                logger.info("Bridge destroyed", bridge_id=bridge_id)
            
            # Remove external media channel reference
            self.external_media_channels.pop(call_id, None)
            
            # Remove active call
            self.active_calls.pop(call_id, None)
            
            logger.info("Call cleanup completed", call_id=call_id)
            
        except Exception as e:
            logger.error(f"Error cleaning up call {call_id}: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get engine statistics."""
        rtp_stats = self.rtp_server.get_stats() if self.rtp_server else {}
        
        return {
            "running": self.running,
            "active_calls": len(self.active_calls),
            "external_media_channels": len(self.external_media_channels),
            "bridges": len(self.bridges),
            "providers": list(self.providers.keys()),
            "rtp_server": rtp_stats
        }

async def main():
    """Main entry point for External Media engine."""
    # Configure logging
    configure_logging()
    logger = get_logger(__name__)
    
    try:
        # Load configuration
        config = load_config()
        logger.info("Configuration loaded")
        
        # Create and start engine
        engine = ExternalMediaEngine(config)
        
        # Set up signal handlers
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            asyncio.create_task(engine.stop())
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Start engine
        await engine.start()
        
        # Keep running
        logger.info("External Media engine running...")
        while engine.running:
            await asyncio.sleep(1)
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        if 'engine' in locals():
            await engine.stop()
        logger.info("External Media engine shutdown complete")

if __name__ == "__main__":
    asyncio.run(main())
