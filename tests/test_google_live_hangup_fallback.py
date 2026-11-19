import pytest

from src.config import GoogleProviderConfig
from src.providers.google_live import GoogleLiveProvider


class _DummySession:
    def __init__(self):
        self.conversation_history = []
        self.cleanup_after_tts = False
        self.current_action = None


class _DummySessionStore:
    def __init__(self):
        self.session = _DummySession()

    async def get_by_call_id(self, _call_id):
        return self.session

    async def upsert_call(self, _session):
        return None


@pytest.mark.unit
def test_google_live_turn_complete_fallback_wait_gate():
    provider = GoogleLiveProvider(
        config=GoogleProviderConfig(hangup_fallback_turn_complete_timeout_sec=2.5),
        on_event=lambda e: None,
    )

    provider._hangup_fallback_turn_complete_seen = False
    assert provider._should_wait_for_turn_complete_before_fallback(10.0, 8.0) is True
    assert provider._should_wait_for_turn_complete_before_fallback(11.0, 8.0) is False

    provider._hangup_fallback_turn_complete_seen = True
    assert provider._should_wait_for_turn_complete_before_fallback(10.0, 8.0) is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_google_live_flushes_pending_user_transcription_before_fallback():
    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)
    provider._call_id = "call-1"
    provider._session_store = _DummySessionStore()
    provider._input_transcription_buffer = "I don't want the transcript"

    flushed = await provider._flush_pending_user_transcription(reason="test")

    assert flushed is True
    assert provider._input_transcription_buffer == ""
    assert provider._last_input_transcription_fragment == ""
    assert provider._last_final_user_text == "I don't want the transcript"
    assert provider._session_store.session.conversation_history[-1]["role"] == "user"
    assert provider._session_store.session.conversation_history[-1]["content"] == "I don't want the transcript"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_google_live_flush_skips_duplicate_pending_user_text():
    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)
    provider._input_transcription_buffer = "No transcript"
    provider._last_final_user_text = "No transcript"

    flushed = await provider._flush_pending_user_transcription(reason="duplicate")

    assert flushed is False
    assert provider._input_transcription_buffer == ""
    assert provider._last_input_transcription_fragment == ""


@pytest.mark.asyncio
@pytest.mark.unit
async def test_google_live_disarms_cleanup_after_tts_fallback_on_user_speech():
    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)
    provider._call_id = "call-1"
    provider._session_store = _DummySessionStore()

    provider._hangup_fallback_armed = True
    provider._hangup_after_response = False
    provider._session_store.session.cleanup_after_tts = True

    await provider._maybe_disarm_cleanup_after_tts_fallback_on_user_speech()

    assert provider._session_store.session.cleanup_after_tts is False
    assert provider._hangup_fallback_armed is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_google_live_flushes_pending_transcriptions_on_disconnect():
    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)
    provider._call_id = "call-1"
    provider._session_store = _DummySessionStore()

    provider._input_transcription_buffer = "user fragment"
    provider._output_transcription_buffer = "assistant fragment"

    await provider._flush_pending_transcriptions_on_disconnect(code=1011, reason="Internal error occurred.")

    history = provider._session_store.session.conversation_history
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "(partial) user fragment"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "(partial) assistant fragment"

    assert provider._input_transcription_buffer == ""
    assert provider._output_transcription_buffer == ""


@pytest.mark.asyncio
@pytest.mark.unit
async def test_google_live_blocks_hangup_tool_during_pending_attended_transfer(monkeypatch):
    provider = GoogleLiveProvider(config=GoogleProviderConfig(), on_event=lambda e: None)
    provider._call_id = "call-1"
    provider._session_store = _DummySessionStore()
    provider._session_store.session.current_action = {"type": "attended_transfer"}
    provider._caller_channel_id = "caller-1"
    provider._bridge_id = "bridge-1"
    provider._full_config = {}
    provider._allowed_tools = ["hangup_call", "cancel_transfer"]

    send_messages = []

    async def fake_send_message(payload):
        send_messages.append(payload)

    async def unexpected_execute_tool(*args, **kwargs):
        raise AssertionError("Tool execution should be blocked during pending attended transfer")

    monkeypatch.setattr(provider, "_send_message", fake_send_message)
    monkeypatch.setattr(provider._tool_adapter, "execute_tool", unexpected_execute_tool)

    await provider._handle_tool_call(
        {
            "toolCall": {
                "functionCalls": [
                    {
                        "id": "tc-1",
                        "name": "hangup_call",
                        "args": {"farewell_message": "Bye"},
                    }
                ]
            }
        }
    )

    assert provider._hangup_after_response is False
    assert len(send_messages) == 1
    response = send_messages[0]["toolResponse"]["functionResponses"][0]["response"]
    assert response["status"] == "error"
    assert "attended transfer is pending" in response["message"].lower()
    assert provider._session_store.session.conversation_history == []
    event = provider._session_store.session.tool_calls[0]
    assert event["tool_call_id"] == "tc-1"
    assert event["name"] == "hangup_call"
    assert event["status"] == "failure"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_google_live_farewell_does_not_latch_across_turns():
    """A greeting like 'thank you for calling' must not latch the farewell for the whole call."""
    provider = GoogleLiveProvider(
        config=GoogleProviderConfig(hangup_markers_enabled=True),
        on_event=lambda e: None,
    )
    provider._call_id = "call-1"
    provider._session_store = _DummySessionStore()
    # Simulate the greeting latching the farewell in an earlier turn.
    provider._assistant_farewell_intent = "thank you for calling"

    # A new user turn begins (input transcription fragment).
    await provider._handle_server_content(
        {"serverContent": {"inputTranscription": {"text": "goodbye"}}}
    )

    # The greeting must not survive the turn boundary.
    assert provider._assistant_farewell_intent is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_google_live_greeting_farewell_does_not_arm_cleanup_on_later_goodbye():
    """A weak 'goodbye' after a greeting-latched farewell must not end a real call."""
    provider = GoogleLiveProvider(
        config=GoogleProviderConfig(hangup_markers_enabled=True),
        on_event=lambda e: None,
    )
    provider._call_id = "call-1"
    provider._session_store = _DummySessionStore()
    # Greeting latched the farewell in an earlier turn.
    provider._assistant_farewell_intent = "thank you for calling"

    # New user turn: the caller says "goodbye" (a weak marker) and the assistant
    # responds with a normal line, not a farewell.
    await provider._handle_server_content(
        {"serverContent": {"inputTranscription": {"text": "goodbye"}}}
    )
    await provider._maybe_arm_cleanup_after_tts(
        user_text="goodbye", assistant_text="I can help you with that."
    )

    assert provider._session_store.session.cleanup_after_tts is False
    assert provider._hangup_fallback_armed is False


@pytest.mark.parametrize("text", ["byelaws", "goodbyes", "call setup", "maybe"])
def test_google_marker_matching_rejects_embedded_words(text):
    provider = GoogleLiveProvider(config=GoogleProviderConfig(hangup_markers_enabled=True), on_event=None)
    provider._hangup_policy["markers"]["end_call"] = ["bye", "all set"]
    assert provider._detect_user_end_intent(text) is None


@pytest.mark.parametrize("text, expected", [("Bye!", "bye"), ("I'm all set.", "all set")])
def test_google_marker_matching_accepts_whole_phrases(text, expected):
    provider = GoogleLiveProvider(config=GoogleProviderConfig(hangup_markers_enabled=True), on_event=None)
    provider._hangup_policy["markers"]["end_call"] = ["bye", "all set"]
    assert provider._detect_user_end_intent(text) == expected


@pytest.mark.asyncio
async def test_google_fallback_uses_completed_turn_not_old_intent():
    provider = GoogleLiveProvider(config=GoogleProviderConfig(hangup_markers_enabled=True), on_event=None)
    provider._call_id = "call-1"
    provider._session_store = _DummySessionStore()
    provider._assistant_farewell_intent = "thank you for calling"
    provider._user_end_intent = "hang up"
    await provider._maybe_arm_cleanup_after_tts(user_text="I need an appointment", assistant_text="What day works?")
    assert not provider._session_store.session.cleanup_after_tts


@pytest.mark.asyncio
async def test_google_fallback_does_not_arm_during_deferred_transfer():
    provider = GoogleLiveProvider(config=GoogleProviderConfig(hangup_markers_enabled=True), on_event=None)
    provider._call_id = "call-1"
    provider._session_store = _DummySessionStore()
    provider._session_store.session.pending_deferred_transfer = {"id": "transfer-1"}
    await provider._maybe_arm_cleanup_after_tts(user_text="bye", assistant_text="Goodbye")
    assert not provider._session_store.session.cleanup_after_tts
    assert not provider._hangup_fallback_armed


@pytest.mark.asyncio
async def test_google_transfer_reset_cancels_watchdog_and_allows_later_farewell():
    import asyncio
    provider = GoogleLiveProvider(config=GoogleProviderConfig(hangup_markers_enabled=True), on_event=None)
    provider._call_id = "call-1"
    provider._session_store = _DummySessionStore()
    provider._hangup_after_response = True
    provider._hangup_fallback_emitted = True
    provider._hangup_ready_emitted = True
    provider._terminal_audio_cutoff_after_tool = True
    old_watchdog = asyncio.create_task(asyncio.sleep(60))
    provider._hangup_fallback_task = old_watchdog
    provider.release_terminal_output_protection()
    await asyncio.gather(old_watchdog, return_exceptions=True)
    assert old_watchdog.cancelled()
    assert not provider._hangup_after_response
    assert not provider._terminal_audio_cutoff_after_tool
    assert not provider._hangup_ready_emitted
    await provider._maybe_arm_cleanup_after_tts(user_text="Goodbye", assistant_text="Goodbye!")
    assert provider._session_store.session.cleanup_after_tts
    assert provider._hangup_fallback_armed
    assert not provider._hangup_fallback_emitted
    watchdog = provider._hangup_fallback_task
    provider.release_terminal_output_protection()
    await asyncio.gather(watchdog, return_exceptions=True)
