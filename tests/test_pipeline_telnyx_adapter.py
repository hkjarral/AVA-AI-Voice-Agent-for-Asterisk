"""Contract tests for the Telnyx text-generation adapter."""

from src.config import AppConfig, TelnyxLLMProviderConfig
from src.pipelines.telnyx import TelnyxLLMAdapter


def test_telnyx_summary_job_prompt_overrides_agent_persona():
    app_config = AppConfig(
        default_provider="local",
        providers={},
        asterisk={"host": "127.0.0.1", "username": "ari", "password": "secret"},
        llm={"initial_greeting": "hi", "prompt": "VOICE AGENT PERSONA"},
    )
    provider_config = TelnyxLLMProviderConfig(api_key="test-key")
    adapter = TelnyxLLMAdapter("telnyx_llm", app_config, provider_config, {})
    summary_prompt = "Summarize the call. Do not use the agent persona."

    options = adapter._compose_options(
        {
            "system_prompt": summary_prompt,
            "instructions": summary_prompt,
            "temperature": 0.3,
            "max_tokens": 300,
            "tools": [],
        }
    )
    payload = adapter._build_chat_payload(
        "user: What does it cost?\nassistant: It costs one dollar.",
        {"system_prompt": summary_prompt, "prior_messages": []},
        options,
    )

    assert payload["messages"][0] == {"role": "system", "content": summary_prompt}
    assert payload["messages"][-1]["role"] == "user"
    assert "VOICE AGENT PERSONA" not in str(payload["messages"])
    assert "tools" not in payload
