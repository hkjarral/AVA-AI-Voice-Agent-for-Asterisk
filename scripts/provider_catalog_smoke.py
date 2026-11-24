#!/usr/bin/env python3
"""Small, explicitly invoked provider API checks; never places telephone calls.

Run inside the engine environment with its existing environment/secrets available:
  python scripts/provider_catalog_smoke.py --config config/ai-agent.yaml --run
Without --run this prints credential/voice prerequisite availability only. JSON
contains no credentials, transcripts, prompts, or audio. No configuration is saved.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Provider logging can include operator configuration. This isolated CLI emits
# only the explicit non-secret result schema below, including on failed calls.
logging.disable(logging.CRITICAL)
import structlog
def _drop_log(*args):
    raise structlog.DropEvent
structlog.configure(processors=[_drop_log], wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL), cache_logger_on_first_use=False)

from src.config import (
    AzureTTSProviderConfig, CambAiProviderConfig, DeepgramProviderConfig,
    ElevenLabsProviderConfig, FishAudioProviderConfig, GoogleProviderConfig, GrokProviderConfig,
    GroqTTSProviderConfig, LLMConfig, MiniMaxLLMProviderConfig,
    OpenAIProviderConfig, OpenAIRealtimeProviderConfig, TelnyxLLMProviderConfig,
)
from src.config.loaders import load_yaml_with_local_override
from src.config.provider_instances import resolve_secret_value
from src.utils.provider_catalog import provider_default

TEXT = "This is a short provider configuration test."
TESTS = (
    "openai_realtime_tts", "openai_llm", "openai_stt", "deepgram_flux_tts",
    "deepgram_agent", "google_live", "google_live_thinking", "groq_arabic_tts",
    "deepseek_llm", "minimax_llm", "telnyx_llm", "elevenlabs_tts", "fishaudio_tts",
    "cambai_tts", "google_tts", "azure_tts",
    "openai_realtime_full", "grok_full",
)
OPTIONAL_TESTS = (
    "openai_sol_llm", "openai_astra_llm", "google_llm",
    "elevenlabs_v3_tts", "elevenlabs_v4_tts", "elevenlabs_v4_turbo_tts",
)


class SkipTest(Exception):
    pass


def safe_error_details(exc):
    """Extract bounded protocol identifiers, never an exception/provider message."""
    result = {"error_type": type(exc).__name__}
    response = getattr(exc, "response", None)
    status = getattr(exc, "status", None) or getattr(response, "status_code", None)
    # Legacy adapters wrap status/body in RuntimeError rather than a typed HTTP
    # exception. Read it only to extract allowlisted scalar identifiers.
    message = str(exc)[:4096]
    if not isinstance(status, int):
        match = re.search(r"\bstatus\s*[:=]?\s*([1-5][0-9]{2})\b", message)
        status = int(match[1]) if match else None
    if isinstance(status, int) and 100 <= status <= 599:
        result["http_status"] = status
    for key in ("code", "param"):
        match = re.search(r'"' + key + r'"\s*:\s*"([^"\\]{1,80})"', message)
        if match and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", match[1]):
            result["provider_error_" + key] = match[1]
    return result


def entry(config, *names):
    providers = config.get("providers") or {}
    for name in names:
        value = providers.get(name)
        if isinstance(value, dict):
            return dict(value)
    return {}


def credential(config, names, legacy):
    cfg = entry(config, *names)
    key = resolve_secret_value(cfg, file_field="api_key_file", env_field="api_key_env", inline_field="api_key", legacy_env_names=legacy)
    if not key or key.startswith("${"):
        raise SkipTest("credentials_unavailable")
    return cfg, key


async def collect_tts(adapter, call_id, state):
    pcm = bytearray()
    try:
        await adapter.start()
        await adapter.open_call(call_id, {})
        async for chunk in adapter.synthesize(call_id, TEXT, {}):
            pcm.extend(chunk)
            if len(pcm) > 8 * 1024 * 1024:
                raise RuntimeError("unexpected_audio_volume")
        if not pcm:
            raise RuntimeError("no_audio")
        state["audio_bytes"] = len(pcm)
        return bytes(pcm)
    finally:
        try:
            await adapter.close_call(call_id)
        finally:
            await adapter.stop()


async def collect_llm(adapter, call_id, options=None):
    try:
        await adapter.start()
        await adapter.open_call(call_id, {})
        response = await adapter.generate(call_id, "Reply with OK.", {}, {"max_tokens": 128, **(options or {})})
        if not (getattr(response, "text", None) or (response if isinstance(response, str) else "")):
            raise RuntimeError("empty_response")
    finally:
        try:
            await adapter.close_call(call_id)
        finally:
            await adapter.stop()


async def run_one(name, config, timeout, cache):
    state = {"provider": name, "status": "PASS"}
    call_id = "provider-smoke-" + uuid.uuid4().hex[:12]
    began = time.monotonic()
    try:
        async with asyncio.timeout(timeout):
            if name in {"openai_realtime_full", "grok_full"}:
                async def on_event(event):
                    pass
                if name == "openai_realtime_full":
                    cfg, key = credential(config, ("openai_realtime", "openai"), ("OPENAI_API_KEY",))
                    from src.providers.openai_realtime import OpenAIRealtimeProvider
                    state.update(model=provider_default("openai_realtime", "model"), voice="marin")
                    provider = OpenAIRealtimeProvider(OpenAIRealtimeProviderConfig(api_key=key, model=state["model"], voice=state["voice"], input_transcription_model="gpt-transcribe", reasoning_effort="low", greeting=None, connect_attempt_timeout_sec=10, connect_total_timeout_sec=timeout), on_event)
                else:
                    cfg, key = credential(config, ("grok",), ("XAI_API_KEY",))
                    from src.providers.grok import GrokProvider
                    state.update(model=provider_default("grok", "model"), voice=provider_default("grok", "voice"))
                    provider = GrokProvider(GrokProviderConfig(api_key=key, model=state["model"], voice=state["voice"], reasoning_effort="none", greeting=None, connect_attempt_timeout_sec=10, connect_total_timeout_sec=timeout), on_event)
                # Exercise the real setup/ACK/cleanup paths without requesting
                # the automatic first turn or dispatching any tools.
                async def no_initial_response():
                    pass
                provider._ensure_response_request = no_initial_response
                try:
                    await provider.start_session(call_id, {"tools": []})
                    if not provider._session_ack_event or not provider._session_ack_event.is_set():
                        raise RuntimeError("settings_not_applied")
                    state["setup_acknowledged"] = True
                finally:
                    await provider.stop_session()
            elif name.startswith("openai_"):
                cfg, key = credential(config, (name, "openai", "openai_realtime"), ("OPENAI_API_KEY",))
                from src.pipelines.openai import OpenAILLMAdapter, OpenAISTTAdapter, OpenAITTSAdapter
                if name == "openai_realtime_tts":
                    state.update(model="gpt-realtime-2.1-mini", voice="marin")
                    adapter = OpenAITTSAdapter("openai_tts", None, OpenAIProviderConfig(api_key=key, tts_model=state["model"], voice=state["voice"], tts_transport="realtime", target_encoding="linear16", target_sample_rate_hz=16000, tts_realtime_timeout_sec=timeout, response_timeout_sec=timeout))
                    cache["pcm16_16k"] = await collect_tts(adapter, call_id, state)
                elif name in {"openai_llm", "openai_sol_llm", "openai_astra_llm"}:
                    state["model"] = {"openai_llm": "gpt-6-luna", "openai_sol_llm": "gpt-6.2-sol", "openai_astra_llm": "gpt-6.2-astra"}[name]
                    effort = "none" if name == "openai_llm" else "low"
                    adapter = OpenAILLMAdapter("openai_llm", None, OpenAIProviderConfig(api_key=key, chat_model=state["model"], reasoning_effort=effort, response_timeout_sec=timeout))
                    await collect_llm(adapter, call_id, {"max_completion_tokens": 1024} if effort != "none" else None)
                else:
                    state["model"] = "gpt-transcribe"
                    adapter = OpenAISTTAdapter("openai_stt", None, OpenAIProviderConfig(api_key=key, stt_model=state["model"], response_timeout_sec=timeout))
                    try:
                        await adapter.start()
                        await adapter.open_call(call_id, {})
                        pcm = cache.get("pcm16_16k") or b"\0\0" * 16000
                        transcript = await adapter.transcribe(call_id, pcm, 16000, {})
                        if cache.get("pcm16_16k") and not transcript:
                            raise RuntimeError("empty_transcript_for_synthetic_speech")
                        state["input_audio_bytes"] = len(pcm)
                    finally:
                        await adapter.close_call(call_id)
                        await adapter.stop()
            elif name == "deepgram_flux_tts":
                cfg, key = credential(config, ("deepgram_tts", "deepgram"), ("DEEPGRAM_API_KEY",))
                from src.pipelines.deepgram import DeepgramTTSAdapter
                state.update(model="flux-haley-en", voice="flux-haley-en")
                adapter = DeepgramTTSAdapter("deepgram_tts", None, DeepgramProviderConfig(api_key=key, tts_model=state["model"], tts_version="v2"), {"timeout_sec": timeout})
                await collect_tts(adapter, call_id, state)
            elif name == "deepgram_agent":
                cfg, key = credential(config, ("deepgram",), ("DEEPGRAM_API_KEY",))
                from src.providers.deepgram import DeepgramProvider
                state.update(model=provider_default("deepgram", "think_model"), voice="flux-haley-en")
                ready = asyncio.Event()
                async def on_event(event):
                    if event.get("type") == "AgentAudio" and event.get("data"):
                        state["audio_bytes"] = state.get("audio_bytes", 0) + len(event["data"])
                        ready.set()
                provider = DeepgramProvider(DeepgramProviderConfig(api_key=key, model="flux-general-en", tts_model=state["voice"], tts_version="v2", think_model=state["model"], greeting=TEXT, instructions="Speak only the supplied test greeting.", connect_attempt_timeout_sec=10, connect_total_timeout_sec=timeout), LLMConfig(), on_event)
                try:
                    await provider.start_session(call_id, {"tools": []})
                    if provider._ack_event is not None:
                        await provider._ack_event.wait()
                    if not provider._settings_acked:
                        raise RuntimeError("settings_not_applied")
                    await ready.wait()
                finally:
                    await provider.stop_session()
            elif name.startswith("google_live"):
                cfg, key = credential(config, ("google_live", "google_llm", "google"), ("GOOGLE_API_KEY", "GOOGLE_LIVE_API_KEY"))
                from src.providers.google_live import GoogleLiveProvider
                state["model"] = "gemini-3.8-live-extended-thinking" if name.endswith("thinking") else "gemini-3.8-live"
                state["voice"] = "Aoede"
                async def on_event(event):
                    pass
                provider = GoogleLiveProvider(GoogleProviderConfig(api_key=key, llm_model=state["model"], tts_voice_name=state["voice"], greeting=None, thinking_level="low" if name.endswith("thinking") else None, connect_attempt_timeout_sec=10, connect_total_timeout_sec=timeout), on_event)
                try:
                    await provider.start_session(call_id, {"tools": []})
                    if not provider._setup_complete:
                        raise RuntimeError("setup_not_applied")
                finally:
                    await provider.stop_session()
            elif name == "groq_arabic_tts":
                cfg, key = credential(config, ("groq_tts", "groq"), ("GROQ_API_KEY",))
                from src.pipelines.groq import GroqTTSAdapter
                state.update(model="canopylabs/orpheus-arabic-saudi", voice="abdullah")
                adapter = GroqTTSAdapter("groq_tts", None, GroqTTSProviderConfig(api_key=key, tts_model=state["model"], voice=state["voice"], request_timeout_sec=timeout))
                await collect_tts(adapter, call_id, state)
            elif name == "deepseek_llm":
                cfg, key = credential(config, (name,), ("DEEPSEEK_API_KEY",))
                from src.pipelines.openai import OpenAILLMAdapter
                state["model"] = provider_default("deepseek", "chat_model")
                adapter = OpenAILLMAdapter(name, None, OpenAIProviderConfig(api_key=key, chat_model=state["model"], chat_base_url="https://api.deepseek.com", response_timeout_sec=timeout))
                await collect_llm(adapter, call_id)
            elif name in {"minimax_llm", "telnyx_llm"}:
                vendor = name.split("_")[0]
                cfg, key = credential(config, (name, vendor), (vendor.upper() + "_API_KEY",))
                state["model"] = provider_default(vendor, "chat_model")
                if vendor == "minimax":
                    from src.pipelines.minimax import MiniMaxLLMAdapter
                    adapter = MiniMaxLLMAdapter(name, None, MiniMaxLLMProviderConfig(api_key=key, chat_model=state["model"], response_timeout_sec=timeout))
                else:
                    from src.pipelines.telnyx import TelnyxLLMAdapter
                    adapter = TelnyxLLMAdapter(name, None, TelnyxLLMProviderConfig(api_key=key, chat_model=state["model"], response_timeout_sec=timeout))
                await collect_llm(adapter, call_id)
            elif name.startswith("elevenlabs_"):
                cfg, key = credential(config, ("elevenlabs_tts", "elevenlabs"), ("ELEVENLABS_API_KEY",))
                if not cfg.get("voice_id"):
                    raise SkipTest("configured_voice_required")
                from src.pipelines.elevenlabs import ElevenLabsTTSAdapter
                model = {"elevenlabs_tts": "eleven_flash_v2_5", "elevenlabs_v3_tts": "eleven_v3", "elevenlabs_v4_tts": "eleven_v4", "elevenlabs_v4_turbo_tts": "eleven_v4_turbo"}[name]
                state.update(model=model, voice_source="configured")
                adapter = ElevenLabsTTSAdapter(name, None, ElevenLabsProviderConfig(api_key=key, model_id=state["model"], voice_id=cfg["voice_id"]))
                await collect_tts(adapter, call_id, state)
            elif name == "fishaudio_tts":
                cfg, key = credential(config, (name, "fishaudio"), ("FISH_AUDIO_API_KEY", "FISHAUDIO_API_KEY"))
                if not cfg.get("reference_id"):
                    raise SkipTest("configured_voice_required")
                from src.pipelines.fish_audio import FishAudioTTSAdapter
                state.update(model=provider_default("fishaudio", "model"), voice_source="configured")
                adapter = FishAudioTTSAdapter(name, None, FishAudioProviderConfig(api_key=key, model=state["model"], reference_id=cfg["reference_id"], read_timeout_sec=timeout))
                await collect_tts(adapter, call_id, state)
            elif name == "cambai_tts":
                cfg, key = credential(config, (name, "cambai"), ("CAMB_API_KEY",))
                from src.pipelines.cambai import CambAiTTSAdapter
                state.update(model="mars-flash", voice_source="configured" if cfg.get("voice_id") else "documented_default")
                adapter = CambAiTTSAdapter(name, None, CambAiProviderConfig(api_key=key, voice_id=int(cfg.get("voice_id", 147320))))
                await collect_tts(adapter, call_id, state)
            elif name == "google_llm":
                cfg, key = credential(config, (name, "google"), ("GOOGLE_API_KEY",))
                from src.pipelines.google import GoogleLLMAdapter
                state["model"] = provider_default("google", "llm_model")
                adapter = GoogleLLMAdapter(name, None, GoogleProviderConfig(api_key=key, llm_model=state["model"]), {"timeout_sec": timeout})
                await collect_llm(adapter, call_id, {"max_output_tokens": 1024, "thinking_level": "low"})
            elif name == "google_tts":
                cfg, key = credential(config, (name, "google"), ("GOOGLE_API_KEY",))
                from src.pipelines.google import GoogleTTSAdapter
                state.update(model="google_cloud_tts", voice_source="configured" if cfg.get("tts_voice_name") else "documented_default")
                adapter = GoogleTTSAdapter(name, None, GoogleProviderConfig(api_key=key, tts_voice_name=cfg.get("tts_voice_name", "en-US-Neural2-C")))
                await collect_tts(adapter, call_id, state)
            elif name == "azure_tts":
                cfg, key = credential(config, (name, "azure"), ("AZURE_SPEECH_KEY",))
                region = cfg.get("region") or os.getenv("AZURE_SPEECH_REGION") or os.getenv("AZURE_REGION")
                if not region:
                    raise SkipTest("configured_region_required")
                from src.pipelines.azure import AzureTTSAdapter
                state.update(model="azure_speech_tts", voice_source="configured" if cfg.get("voice_name") else "documented_default")
                adapter = AzureTTSAdapter(name, None, AzureTTSProviderConfig(api_key=key, region=region, voice_name=cfg.get("voice_name", "en-US-JennyNeural")))
                await collect_tts(adapter, call_id, state)
    except SkipTest as exc:
        state.update(status="SKIP", reason=str(exc))
    except Exception as exc:
        # Do not serialize the message/traceback: upstream errors can echo keys,
        # endpoint query strings, operator prompts, and voice identifiers.
        state.update(status="FAIL", **safe_error_details(exc))
    state["latency_ms"] = round((time.monotonic() - began) * 1000)
    return state


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Optional active YAML; read only, including sibling local override")
    parser.add_argument("--providers", default=",".join(TESTS), help="Comma-separated test names; optional paid model checks: " + ", ".join(OPTIONAL_TESTS))
    parser.add_argument("--timeout", type=float, default=35.0)
    parser.add_argument("--run", action="store_true", help="Actually run small billable provider requests")
    args = parser.parse_args()
    names = [name.strip() for name in args.providers.split(",") if name.strip()]
    if not names or any(name not in TESTS + OPTIONAL_TESTS for name in names):
        parser.error("Unknown provider test; select from " + ", ".join(TESTS + OPTIONAL_TESTS))
    if not 1 <= args.timeout <= 120:
        parser.error("--timeout must be between 1 and 120 seconds")
    config = load_yaml_with_local_override(str(args.config)) if args.config else {}
    if not args.run:
        print(json.dumps({"status": "DRY_RUN", "providers": names, "configuration_changed": False, "requests_sent": 0}))
        return 0
    results = []
    cache = {}
    # Sequential requests bound spend and allow STT to reuse synthetic TTS PCM.
    for name in names:
        result = await run_one(name, config, args.timeout, cache)
        results.append(result)
        print(json.dumps(result), flush=True)
    print(json.dumps({"summary": {status: sum(r["status"] == status for r in results) for status in ("PASS", "FAIL", "SKIP")}, "configuration_changed": False}))
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        print(json.dumps({"status": "FAIL", "error_type": type(error).__name__}), flush=True)
        raise SystemExit(1)
