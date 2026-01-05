"""
PlaybackManager - Centralized audio playback and TTS gating management.

This extracts all playback/TTS gating logic from the Engine and provides
a clean interface for playing audio with deterministic playback IDs and
token-aware gating.
"""

import asyncio
import time
import os
import inspect
from typing import Optional, Dict, TYPE_CHECKING
import structlog

from src.core.session_store import SessionStore
from src.core.models import PlaybackRef, CallSession

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.core.conversation_coordinator import ConversationCoordinator

logger = structlog.get_logger(__name__)


class PlaybackManager:
    """
    Manages audio playback with deterministic IDs and token-aware gating.
    
    Responsibilities:
    - Generate deterministic playback IDs
    - Manage file lifecycle in /mnt/asterisk_media
    - Handle token/refcount gating
    - Track active playbacks
    - Provide fallback mechanisms
    """
    
    def __init__(
        self,
        session_store: SessionStore,
        ari_client,
        media_dir: str = "/mnt/asterisk_media/ai-generated",
        conversation_coordinator: Optional["ConversationCoordinator"] = None,
    ):
        self.session_store = session_store
        self.ari_client = ari_client
        self.media_dir = media_dir
        self.conversation_coordinator = conversation_coordinator
        
        # Ensure media directory exists
        # Note: Directory should be set up with setgid bit by preflight.sh
        # so files inherit group ownership from the directory (asterisk group)
        try:
            os.makedirs(media_dir, exist_ok=True)
            try:
                # Group-writable so asterisk group members can access
                os.chmod(media_dir, 0o775)
            except Exception:
                pass

            try:
                mount_root = "/mnt/asterisk_media"
                if str(media_dir).startswith(mount_root):
                    if not os.path.ismount(mount_root):
                        logger.warning(
                            "Asterisk sounds volume does not appear to be mounted; file-based playback may fail",
                            mount_root=mount_root,
                            media_dir=media_dir,
                            hint="Run: ./preflight.sh --apply-fixes  (then restart containers)",
                        )
                    elif not os.access(media_dir, os.W_OK):
                        logger.warning(
                            "Asterisk media directory is not writable; file-based playback may fail",
                            media_dir=media_dir,
                            hint="Run: ./preflight.sh --apply-fixes  (then restart containers)",
                        )
            except Exception:
                pass
        except (PermissionError, OSError):
            # Fallback to a writable temp directory on CI/containers (handles read-only FS too)
            fallback = os.getenv("AST_MEDIA_DIR", "/tmp/asterisk_media/ai-generated")
            try:
                os.makedirs(fallback, exist_ok=True)
            except (PermissionError, OSError):
                try:
                    # Last resort within /tmp
                    fallback = "/tmp/ai-generated"
                    os.makedirs(fallback, exist_ok=True)
                except Exception:
                    pass
            logger.warning(
                "PlaybackManager media_dir fallback due to permission/ROFS",
                requested=media_dir,
                fallback=fallback,
            )
            self.media_dir = fallback
        
        # Internal counters for unique playback IDs even within the same ns tick
        self._last_playback_ts = 0
        self._playback_seq = 0

        logger.info("PlaybackManager initialized",
                   media_dir=self.media_dir)
    
    async def play_audio(self, call_id: str, audio_bytes: bytes, 
                        playback_type: str = "response") -> Optional[str]:
        """
        Play audio with deterministic playback ID and gating.
        
        Args:
            call_id: Canonical call ID
            audio_bytes: Audio data to play
            playback_type: Type of playback (greeting, response, etc.)
        
        Returns:
            playback_id if successful, None if failed
        """
        try:
            # Get session to determine target channel
            session = await self.session_store.get_by_call_id(call_id)
            
            if not session:
                logger.error("Cannot play audio - call session not found",
                           call_id=call_id)
                return None
            
            # Generate deterministic playback ID
            playback_id = self._generate_playback_id(call_id, playback_type)
            
            # Create audio file
            audio_file = await self._create_audio_file(audio_bytes, playback_id)
            if not audio_file:
                return None
            
            # Set TTS gating before playing (via coordinator if available)
            gating_success = True
            if self.conversation_coordinator:
                gating_success = await self.conversation_coordinator.on_tts_start(call_id, playback_id)
            else:
                gating_success = await self.session_store.set_gating_token(call_id, playback_id)

            if not gating_success:
                logger.error(
                    "Failed to start playback gating",
                    call_id=call_id,
                    playback_id=playback_id,
                )
                if self.conversation_coordinator:
                    await self.conversation_coordinator.update_conversation_state(call_id, "listening")
                return None
            
            # Create playback reference
            playback_ref = PlaybackRef(
                playback_id=playback_id,
                call_id=call_id,
                channel_id=session.caller_channel_id,
                bridge_id=session.bridge_id,
                media_uri=f"sound:ai-generated/{os.path.basename(audio_file).replace('.ulaw', '')}",
                audio_file=audio_file
            )
            
            # Track playback reference BEFORE playing to avoid race condition
            # (PlaybackFinished might arrive before add_playback completes if order is reversed)
            await self.session_store.add_playback(playback_ref)
            
            # Play audio via ARI
            success = await self._play_via_ari(session, audio_file, playback_id)
            if not success:
                # Cleanup if playback failed to start
                await self.session_store.pop_playback(playback_id)
                # Cleanup gating token
                if self.conversation_coordinator:
                    await self.conversation_coordinator.cancel_tts(call_id, playback_id)
                    await self.conversation_coordinator.update_conversation_state(call_id, "listening")
                else:
                    await self.session_store.clear_gating_token(call_id, playback_id)
                return None
            
            # Schedule token-aware fallback to ensure gating is cleared even if PlaybackFinished is missed
            await self._schedule_gating_fallback(call_id, playback_id, len(audio_bytes))
            
            logger.info("🔊 AUDIO PLAYBACK - Started",
                       call_id=call_id,
                       playback_id=playback_id,
                       audio_size=len(audio_bytes),
                       playback_type=playback_type)
            
            return playback_id
            
        except Exception as e:
            logger.error("Error playing audio",
                        call_id=call_id,
                        playback_type=playback_type,
                        error=str(e),
                        exc_info=True)
            return None
    
    async def on_playback_finished(self, playback_id: str) -> bool:
        """
        Handle PlaybackFinished event from Asterisk.
        
        Args:
            playback_id: The playback ID that finished
        
        Returns:
            True if handled successfully, False otherwise
        """
        try:
            # Get playback reference
            playback_ref = await self.session_store.pop_playback(playback_id)
            if not playback_ref:
                logger.warning("🔊 PlaybackFinished for unknown playback ID",
                             playback_id=playback_id)
                return False
            
            # Clear TTS gating token
            if self.conversation_coordinator:
                success = await self.conversation_coordinator.on_tts_end(
                    playback_ref.call_id,
                    playback_id,
                    reason="playback-finished",
                )
                await self.conversation_coordinator.update_conversation_state(
                    playback_ref.call_id,
                    "listening",
                )
            else:
                success = await self.session_store.clear_gating_token(
                    playback_ref.call_id, playback_id)
            
            # Clean up audio file
            await self._cleanup_audio_file(playback_ref.audio_file)
            
            logger.info("🔊 PlaybackFinished - Audio playback completed",
                       playback_id=playback_id,
                       call_id=playback_ref.call_id,
                       gating_cleared=success)
            
            return True
            
        except Exception as e:
            logger.error("Error handling PlaybackFinished",
                        playback_id=playback_id,
                        error=str(e),
                        exc_info=True)
            return False

    async def wait_for_playback_end(
        self,
        call_id: str,
        playback_id: str,
        *,
        timeout_sec: float,
        poll_interval_sec: float = 0.05,
    ) -> bool:
        """Wait until a playback is no longer considered active.

        This is used by pipelines so that sleeps can be interrupted by barge-in
        (which stops playback and clears gating tokens).

        Completion conditions:
        - PlaybackRef removed from SessionStore, OR
        - Gating token cleared for this playback_id.
        """
        try:
            deadline = time.time() + max(0.0, float(timeout_sec))
            while time.time() < deadline:
                playback_ref = await self.session_store.get_playback(playback_id)
                if not playback_ref:
                    return True
                session = await self.session_store.get_by_call_id(call_id)
                if session:
                    try:
                        if playback_id not in (getattr(session, "tts_tokens", set()) or set()):
                            return True
                    except Exception:
                        pass
                await asyncio.sleep(max(0.01, float(poll_interval_sec)))
            return False
        except Exception:
            logger.debug("wait_for_playback_end failed", call_id=call_id, playback_id=playback_id, exc_info=True)
            return False
    
    def _generate_playback_id(self, call_id: str, playback_type: str) -> str:
        """Generate deterministic, unique playback ID with ns resolution and sequence."""
        ts = time.time_ns()
        if ts == self._last_playback_ts:
            self._playback_seq += 1
        else:
            self._last_playback_ts = ts
            self._playback_seq = 0
        suffix = f"-{self._playback_seq}" if self._playback_seq else ""
        return f"{playback_type}:{call_id}:{ts}{suffix}"
    
    async def _create_audio_file(self, audio_bytes: bytes, playback_id: str) -> Optional[str]:
        """Create audio file from bytes."""
        try:
            # Generate unique filename
            filename = f"audio-{playback_id.replace(':', '-')}.ulaw"
            file_path = os.path.join(self.media_dir, filename)
            
            # Write audio data
            with open(file_path, 'wb') as f:
                f.write(audio_bytes)
            
            # Set file permissions for Asterisk readability via group
            # Files inherit group ownership from setgid directory (set up by preflight.sh)
            # No chown needed - appuser is member of asterisk group
            try:
                # Group-readable so asterisk group members can access
                os.chmod(file_path, 0o664)
            except Exception:
                pass
            
            logger.debug("Audio file created",
                        file_path=file_path,
                        size=len(audio_bytes))
            
            return file_path
            
        except Exception as e:
            logger.error("Error creating audio file",
                        playback_id=playback_id,
                        error=str(e),
                        exc_info=True)
            return None
    
    async def _play_via_ari(self, session: CallSession, audio_file: str, 
                           playback_id: str) -> bool:
        """Play audio file via ARI.

        For pipeline playbacks we prefer channel-scoped playback (caller channel) to avoid
        leaking TTS into ExternalMedia capture (which causes false barge-in triggers).
        """
        try:
            # Create sound URI (remove .ulaw extension - Asterisk adds it)
            sound_uri = f"sound:ai-generated/{os.path.basename(audio_file).replace('.ulaw', '')}"

            success = False
            is_pipeline = playback_id.startswith("pipeline-")
            if is_pipeline and getattr(session, "caller_channel_id", None):
                play_chan_with_id = getattr(self.ari_client, "play_media_on_channel_with_id", None)
                if play_chan_with_id and callable(play_chan_with_id):
                    result = play_chan_with_id(session.caller_channel_id, sound_uri, playback_id)
                    if inspect.isawaitable(result):
                        result = await result
                    success = bool(result)
                else:
                    raise AttributeError("ARI client missing channel playback method")
            else:
                if not session.bridge_id:
                    logger.error(
                        "Cannot play audio - no bridge ID",
                        call_id=session.call_id,
                        playback_id=playback_id,
                    )
                    return False
                play_basic = getattr(self.ari_client, "play_audio_via_bridge", None)
                play_with_id = getattr(self.ari_client, "play_media_on_bridge_with_id", None)
                if play_with_id and callable(play_with_id):
                    result = play_with_id(session.bridge_id, sound_uri, playback_id)
                    if inspect.isawaitable(result):
                        result = await result
                    success = bool(result)
                elif play_basic and callable(play_basic):
                    result = play_basic(session.bridge_id, sound_uri)
                    if inspect.isawaitable(result):
                        result = await result
                    success = bool(result)
                else:
                    raise AttributeError("ARI client missing bridge playback method")
            
            if success:
                logger.info(
                    "Playback started",
                    bridge_id=session.bridge_id,
                    channel_id=getattr(session, "caller_channel_id", None),
                    media_uri=sound_uri,
                    playback_id=playback_id,
                    target="channel" if is_pipeline else "bridge",
                )
            else:
                logger.error(
                    "Failed to start playback",
                    bridge_id=session.bridge_id,
                    channel_id=getattr(session, "caller_channel_id", None),
                    media_uri=sound_uri,
                    playback_id=playback_id,
                    target="channel" if is_pipeline else "bridge",
                )
            return success
            
        except Exception as e:
            logger.error("Error playing audio via ARI",
                        call_id=session.call_id,
                        playback_id=playback_id,
                        error=str(e),
                        exc_info=True)
            return False
    
    async def _cleanup_audio_file(self, audio_file: str) -> None:
        """Clean up audio file after playback."""
        try:
            if os.path.exists(audio_file):
                os.remove(audio_file)
                logger.debug("Audio file cleaned up",
                           file_path=audio_file)
        except Exception as e:
            logger.warning("Error cleaning up audio file",
                         file_path=audio_file,
                         error=str(e))
    
    async def _schedule_gating_fallback(self, call_id: str, playback_id: str, audio_size: int) -> None:
        """
        Schedule a token-aware fallback to ensure gating is cleared even if PlaybackFinished is missed.
        
        Args:
            call_id: Call ID
            playback_id: Playback ID
            audio_size: Size of audio in bytes (μ-law @ 8kHz)
        """
        try:
            # Calculate audio duration: 8kHz uLaw = 8000 samples/sec = 1 byte per sample
            audio_duration = audio_size / 8000.0  # seconds
            
            # Use longer safety margin for pipeline mode (file-based playback has more latency)
            # Pipeline mode: Asterisk file loading + processing + event delivery = 0.8-1.8s typical
            # Full agent mode: Streaming has lower latency, use shorter margin
            is_pipeline = playback_id.startswith("pipeline-")
            fallback_delay = audio_duration + (2.5 if is_pipeline else 0.5)  # safety margin
            
            logger.info("[TIMER] Scheduled: action=gating_fallback",
                        call_id=call_id,
                        playback_id=playback_id,
                        delay_seconds=round(fallback_delay, 2),
                        audio_duration=round(audio_duration, 2),
                        is_pipeline=is_pipeline,
                        safety_margin=2.5 if is_pipeline else 0.5)
            
            # Schedule the fallback task
            asyncio.create_task(self._gating_fallback_task(call_id, playback_id, fallback_delay))
            
        except Exception as e:
            logger.error("Error scheduling gating fallback",
                        call_id=call_id,
                        playback_id=playback_id,
                        error=str(e))
    
    async def _gating_fallback_task(self, call_id: str, playback_id: str, delay: float) -> None:
        """
        Fallback task that clears gating token after delay if still active.
        
        Args:
            call_id: Call ID
            playback_id: Playback ID to clear
            delay: Delay in seconds before checking
        """
        try:
            await asyncio.sleep(delay)
            
            # Check if playback is still active
            playback_ref = await self.session_store.get_playback(playback_id)
            if playback_ref:
                # PlaybackFinished may have been missed. Clean up gating and local playback tracking
                # so we don't leak playbacks/gating for long-running calls.
                popped_ref = None
                try:
                    popped_ref = await self.session_store.pop_playback(playback_id)
                except Exception:
                    popped_ref = None

                # Best-effort: cleanup the audio file if we have it.
                try:
                    if popped_ref and getattr(popped_ref, "audio_file", None):
                        await self._cleanup_audio_file(str(popped_ref.audio_file))
                except Exception:
                    logger.debug("Audio file cleanup failed during gating fallback", call_id=call_id, playback_id=playback_id, exc_info=True)

                # Clear gating token as fallback (idempotent).
                if self.conversation_coordinator:
                    success = await self.conversation_coordinator.on_tts_end(
                        call_id,
                        playback_id,
                        reason="gating-fallback",
                    )
                    await self.conversation_coordinator.update_conversation_state(
                        call_id,
                        "listening",
                    )
                else:
                    success = await self.session_store.clear_gating_token(call_id, playback_id)
                logger.warning("[TIMER] Executed: action=gating_fallback",
                              call_id=call_id,
                              playback_id=playback_id,
                              result="gating_cleared",
                              success=success,
                              playback_ref_removed=bool(popped_ref))
            else:
                logger.info("[TIMER] Skipped: action=gating_fallback",
                            call_id=call_id,
                            playback_id=playback_id,
                            reason="playback_already_finished")
                
        except Exception as e:
            logger.error("Error in gating fallback task",
                        call_id=call_id,
                        playback_id=playback_id,
                        error=str(e))

    async def get_active_playbacks(self) -> Dict[str, PlaybackRef]:
        """Get all active playbacks."""
        # This would need to be implemented in SessionStore
        # For now, return empty dict
        return {}
    
    async def cleanup_expired_playbacks(self, max_age_seconds: float = 300) -> int:
        """Clean up playbacks older than max_age_seconds."""
        # This would need to be implemented in SessionStore
        # For now, return 0
        _ = max_age_seconds  # Suppress unused argument warning
        return 0
