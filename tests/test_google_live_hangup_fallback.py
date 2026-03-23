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
