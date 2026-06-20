"""
Unit tests for PlaybackManager.

Tests the audio playback and TTS gating functionality.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.core.models import CallSession, PlaybackRef
from src.core.session_store import SessionStore
from src.core.playback_manager import PlaybackManager


class TestPlaybackManager:
    """Test PlaybackManager functionality."""
    
    @pytest.fixture
    async def session_store(self):
        """Create a SessionStore instance for testing."""
        return SessionStore()
    
    @pytest.fixture
    def mock_ari_client(self):
        """Create a mock ARI client."""
        mock_client = MagicMock()
        mock_client.play_audio_via_bridge = AsyncMock(return_value=True)
        mock_client.play_media_on_bridge_with_id = AsyncMock(return_value=True)
        return mock_client
    
    @pytest.fixture
    async def playback_manager(self, session_store, mock_ari_client):
        """Create a PlaybackManager instance for testing."""
        with patch('os.makedirs'):
            return PlaybackManager(session_store, mock_ari_client, "/tmp/test_media")
    
    @pytest.fixture
    async def sample_session(self, session_store):
        """Create a sample call session."""
        session = CallSession(
            call_id="test_call_123",
            caller_channel_id="1758498324.399",
            bridge_id="bridge_123",
            provider_name="local",
            conversation_state="greeting"
        )
        await session_store.upsert_call(session)
        return session
    
    @pytest.mark.asyncio
    async def test_play_audio_success(self, playback_manager, sample_session):
        """Test successful audio playback."""
        audio_bytes = b"fake_audio_data"
        
        with patch('builtins.open', MagicMock()), \
             patch('os.path.basename', return_value="audio-test_call_123-1234567890.ulaw"), \
             patch.object(playback_manager, '_create_audio_file', return_value="/tmp/test.ulaw"):
            
            playback_id = await playback_manager.play_audio(
                "test_call_123", 
                audio_bytes, 
                "response"
            )
            
            assert playback_id is not None
            assert playback_id.startswith("response:test_call_123:")
            
            # Verify gating token was set
            session = await playback_manager.session_store.get_by_call_id("test_call_123")
            assert session.tts_playing
            assert playback_id in session.tts_tokens
            assert session.tts_active_count == 1
            
            # Verify playback reference was added
            playback_ref = await playback_manager.session_store.get_playback(playback_id)
            assert playback_ref is not None
            assert playback_ref.call_id == "test_call_123"
    
    @pytest.mark.asyncio
    async def test_play_audio_session_not_found(self, playback_manager):
        """Test audio playback when session is not found."""
        audio_bytes = b"fake_audio_data"
        
        playback_id = await playback_manager.play_audio(
            "nonexistent_call", 
            audio_bytes, 
            "response"
        )
        
        assert playback_id is None
    
    @pytest.mark.asyncio
    async def test_play_audio_ari_failure(self, playback_manager, sample_session, mock_ari_client):
        """Test audio playback when ARI call fails."""
        audio_bytes = b"fake_audio_data"
        mock_ari_client.play_audio_via_bridge = AsyncMock(return_value=False)
        mock_ari_client.play_media_on_bridge_with_id = AsyncMock(return_value=False)
        
        with patch('builtins.open', MagicMock()), \
             patch('os.path.basename', return_value="audio-test_call_123-1234567890.ulaw"), \
             patch.object(playback_manager, '_create_audio_file', return_value="/tmp/test.ulaw"):
            
            playback_id = await playback_manager.play_audio(
                "test_call_123", 
                audio_bytes, 
                "response"
            )
            
            assert playback_id is None
            
            # Verify gating token was cleaned up
            session = await playback_manager.session_store.get_by_call_id("test_call_123")
            assert not session.tts_playing
            assert session.tts_active_count == 0
    
    @pytest.mark.asyncio
    async def test_on_playback_finished_success(self, playback_manager, sample_session):
        """Test successful PlaybackFinished handling."""
        # Set up gating token and playback reference
        await playback_manager.session_store.set_gating_token("test_call_123", "test_playback_123")
        # Create playback ref explicitly and add it
        playback_ref = PlaybackRef(
            playback_id="test_playback_123",
            call_id="test_call_123",
            channel_id="1758498324.399",
            bridge_id="bridge_123",
            media_uri="sound:ai-generated/test",
            audio_file="/tmp/test.ulaw",
        )
        await playback_manager.session_store.add_playback(playback_ref)
        
        with patch.object(playback_manager, '_cleanup_audio_file') as mock_cleanup:
            success = await playback_manager.on_playback_finished("test_playback_123")
            
            assert success
            
            # Verify gating token was cleared
            session = await playback_manager.session_store.get_by_call_id("test_call_123")
            assert not session.tts_playing
            assert session.tts_active_count == 0
            assert session.audio_capture_enabled
            
            # Verify playback reference was removed
            assert await playback_manager.session_store.get_playback("test_playback_123") is None
            
            # Verify cleanup was called
            mock_cleanup.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_on_playback_finished_unknown_id(self, playback_manager):
        """Test PlaybackFinished handling for unknown playback ID."""
        success = await playback_manager.on_playback_finished("unknown_playback_id")
        assert not success
    
    @pytest.mark.asyncio
    async def test_multiple_playbacks_gating(self, playback_manager, sample_session):
        """Test gating behavior with multiple concurrent playbacks."""
        audio_bytes = b"fake_audio_data"
        
        with patch('builtins.open', MagicMock()), \
             patch('os.path.basename', return_value="audio-test.ulaw"), \
             patch.object(playback_manager, '_create_audio_file', return_value="/tmp/test.ulaw"):
            
            # Start first playback
            playback_id_1 = await playback_manager.play_audio(
                "test_call_123", audio_bytes, "response"
            )
            assert playback_id_1 is not None
            
            # Start second playback
            playback_id_2 = await playback_manager.play_audio(
                "test_call_123", audio_bytes, "response"
            )
            assert playback_id_2 is not None
            
            # Verify both tokens are active
            session = await playback_manager.session_store.get_by_call_id("test_call_123")
            assert session.tts_playing
            assert session.tts_active_count == 2
            assert not session.audio_capture_enabled
            
            # Finish first playback
            await playback_manager.on_playback_finished(playback_id_1)
            
            # Verify still playing (one token remaining)
            session = await playback_manager.session_store.get_by_call_id("test_call_123")
            assert session.tts_playing
            assert session.tts_active_count == 1
            assert not session.audio_capture_enabled
            
            # Finish second playback
            await playback_manager.on_playback_finished(playback_id_2)
            
            # Verify no longer playing
            session = await playback_manager.session_store.get_by_call_id("test_call_123")
            assert not session.tts_playing
            assert session.tts_active_count == 0
            assert session.audio_capture_enabled
    
    @pytest.mark.asyncio
    async def test_playback_id_generation(self, playback_manager):
        """Test deterministic playback ID generation."""
        playback_id_1 = playback_manager._generate_playback_id("test_call_123", "response")
        playback_id_2 = playback_manager._generate_playback_id("test_call_123", "greeting")
        playback_id_3 = playback_manager._generate_playback_id("test_call_456", "response")
        
        # Check format
        assert playback_id_1.startswith("response:test_call_123:")
        assert playback_id_2.startswith("greeting:test_call_123:")
        assert playback_id_3.startswith("response:test_call_456:")
        
        # Check uniqueness (should be different due to timestamp)
        assert playback_id_1 != playback_id_2
        assert playback_id_1 != playback_id_3
    
    @pytest.mark.asyncio
    async def test_audio_file_creation(self, playback_manager):
        """Test audio file creation and cleanup."""
        audio_bytes = b"test_audio_data"
        playback_id = "test_playback_123"
        
        with patch('builtins.open', MagicMock()) as mock_open, \
             patch('os.path.join', return_value="/tmp/test.ulaw") as mock_join:
            
            file_path = await playback_manager._create_audio_file(audio_bytes, playback_id)
            
            assert file_path == "/tmp/test.ulaw"
            # Assert the specific intended path join happened, not an exact global
            # call count: the write now runs via asyncio.to_thread (LOW-R1, so it no
            # longer blocks the event loop), and the thread-pool machinery itself
            # calls os.path.join internally — making assert_called_once() flaky
            # depending on executor warmth / test order.
            mock_join.assert_any_call(playback_manager.media_dir, "audio-test_playback_123.ulaw")
            mock_open.assert_called_once_with("/tmp/test.ulaw", 'wb')
    
    @pytest.mark.asyncio
    async def test_audio_file_cleanup(self, playback_manager):
        """Test audio file cleanup."""
        with patch('os.path.exists', return_value=True) as mock_exists, \
             patch('os.remove') as mock_remove:
            
            await playback_manager._cleanup_audio_file("/tmp/test.ulaw")
            
            mock_exists.assert_called_once_with("/tmp/test.ulaw")
            mock_remove.assert_called_once_with("/tmp/test.ulaw")
