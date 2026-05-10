#!/usr/bin/env python3
"""
Local AI Server Component Checker
==================================
Verifies that STT, LLM, and TTS are working on a local_ai_server instance.

Usage:
    # Same host (default ws://127.0.0.1:8765)
    python3 scripts/check_local_server.py --local

    # Remote GPU server
    python3 scripts/check_local_server.py --remote 10.0.0.50

    # Custom port + auth token
    python3 scripts/check_local_server.py --remote 10.0.0.50 --port 9000 --auth-token mysecret

    # JSON output for CI
    python3 scripts/check_local_server.py --local --json

Exit codes:
    0 = All checks passed
    1 = Some checks failed
    2 = Connection error
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# websockets availability check + docker exec fallback
# ---------------------------------------------------------------------------
try:
    import websockets  # noqa: F401
    _HAS_WEBSOCKETS = True
except ImportError:
    _HAS_WEBSOCKETS = False


def _reexec_in_container(argv: List[str]) -> int:
    """Re-run this script inside the local_ai_server container via docker exec."""
    script_path = os.path.abspath(__file__)
    try:
        with open(script_path) as f:
            script_content = f.read()
    except Exception as exc:
        print(f"Cannot read script for container exec: {exc}", file=sys.stderr)
        return 2

    project_root = os.getcwd()
    auth_arg_present = False
    for i, arg in enumerate(argv[1:]):
        if arg == "--auth-token" or arg.startswith("--auth-token="):
            auth_arg_present = True
        if arg == "--project-root" and i + 2 <= len(argv[1:]):
            project_root = argv[i + 2]
        elif arg.startswith("--project-root="):
            project_root = arg.split("=", 1)[1]

    # Build the same argv but skip --project-root (not meaningful inside container)
    filtered = []
    skip_next = False
    for arg in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg == "--project-root":
            skip_next = True
            continue
        if arg.startswith("--project-root="):
            continue
        filtered.append(arg)

    if not auth_arg_present:
        auth_token = _load_auth_from_env(project_root)
        if auth_token:
            filtered.extend(["--auth-token", auth_token])

    cmd = [
        "docker", "exec", "-i", "local_ai_server",
        "python3", "-c", script_content,
    ] + filtered

    try:
        result = subprocess.run(cmd, timeout=120)
        return result.returncode
    except FileNotFoundError:
        print("docker not found. Install websockets on the host: pip3 install websockets", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("docker exec timed out", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"docker exec failed: {exc}", file=sys.stderr)
        return 2

# ---------------------------------------------------------------------------
# Colour helpers (disabled when --no-color or non-TTY)
# ---------------------------------------------------------------------------
_USE_COLOR = True


def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m" if _USE_COLOR else s


def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m" if _USE_COLOR else s


def _yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m" if _USE_COLOR else s


def _bold(s: str) -> str:
    return f"\033[1m{s}\033[0m" if _USE_COLOR else s


def _ok(msg: str) -> str:
    return _green("✅ " + msg)


def _fail(msg: str) -> str:
    return _red("❌ " + msg)


def _warn(msg: str) -> str:
    return _yellow("⚠️  " + msg)


# ---------------------------------------------------------------------------
# WebSocket helpers
# ---------------------------------------------------------------------------
LLM_TELEPHONY_WARN_SEC = 15.0


async def _connect(url: str, auth_token: Optional[str], timeout: float = 5.0):
    """Connect and optionally authenticate. Returns (ws, error_str | None)."""
    if not _HAS_WEBSOCKETS:
        return None, "Python 'websockets' package not installed. Run: pip3 install websockets"

    try:
        ws = await websockets.connect(url, open_timeout=timeout, max_size=None)
    except Exception as exc:
        return None, f"Cannot connect to {url}: {exc}"

    if not auth_token:
        return ws, None

    try:
        await ws.send(json.dumps({"type": "auth", "auth_token": auth_token}))
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        resp = json.loads(raw)
        if resp.get("status") != "ok":
            await ws.close()
            return None, f"Authentication failed: {resp.get('message', resp)}"
    except Exception as exc:
        try:
            await ws.close()
        except Exception:
            pass
        return None, f"Authentication error: {exc}"

    return ws, None


async def _send_recv_json(
    ws, payload: Dict[str, Any], timeout: float = 30.0
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Send a JSON message and receive one JSON response."""
    try:
        await ws.send(json.dumps(payload))
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        if isinstance(raw, bytes):
            return None, f"Expected JSON, got binary ({len(raw)} bytes)"
        return json.loads(raw), None
    except asyncio.TimeoutError:
        return None, f"Timeout ({timeout}s) waiting for response to {payload.get('type', '?')}"
    except Exception as exc:
        return None, str(exc)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
class CheckResult:
    def __init__(self, name: str, passed: bool, message: str, latency: Optional[float] = None, warning: Optional[str] = None):
        self.name = name
        self.passed = passed
        self.message = message
        self.latency = latency
        self.warning = warning

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name, "passed": self.passed, "message": self.message}
        if self.latency is not None:
            d["latency_sec"] = round(self.latency, 3)
        if self.warning:
            d["warning"] = self.warning
        return d


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


async def check_status(url: str, auth_token: Optional[str]) -> Tuple[Optional[Dict], List[CheckResult]]:
    """Check status and return (status_data, results)."""
    results: List[CheckResult] = []

    ws, err = await _connect(url, auth_token)
    if err:
        results.append(CheckResult("connection", False, err))
        return None, results

    resp, err = await _send_recv_json(ws, {"type": "status"})

    # Handle auth-required response
    if resp and resp.get("type") == "auth_response" and resp.get("message") == "authentication_required":
        await ws.close()
        results.append(CheckResult("connection", False, "Server requires authentication. Set LOCAL_WS_AUTH_TOKEN in .env or use --auth-token"))
        return None, results

    await ws.close()

    if err:
        results.append(CheckResult("connection", False, err))
        return None, results

    if not resp or resp.get("type") != "status_response":
        results.append(CheckResult("connection", False, f"Unexpected response: {resp}"))
        return None, results

    results.append(CheckResult("connection", True, f"Connected to {url}"))

    models = resp.get("models", {})
    gpu = resp.get("gpu", {})
    config = resp.get("config", {})

    # STT status
    stt = models.get("stt", {})
    stt_loaded = stt.get("loaded", False)
    stt_msg = f"{stt.get('backend', '?')} | {stt.get('display', '?')}"
    stt_details = []
    if stt.get("device"):
        stt_details.append(f"device={stt.get('device')}")
    if stt.get("compute_type"):
        stt_details.append(f"compute={stt.get('compute_type')}")
    if stt_details:
        stt_msg += " | " + ", ".join(stt_details)
    stt_warn = None
    if stt.get("backend") == "faster_whisper" and stt.get("device") == "cpu" and stt.get("compute_type") == "float16":
        stt_warn = "Faster-Whisper CPU + float16 is usually invalid; use int8 for CPU demos."
    results.append(CheckResult("stt_loaded", stt_loaded, stt_msg, warning=stt_warn))

    # LLM status
    llm = models.get("llm", {})
    llm_loaded = llm.get("loaded", False)
    llm_cfg = llm.get("config", {})
    tool_capability = llm.get("tool_capability") or {}
    llm_msg = (
        f"{llm.get('display', '?')} | "
        f"ctx={llm_cfg.get('context', '?')}, max_tokens={llm_cfg.get('max_tokens', '?')}, "
        f"gpu_layers={llm_cfg.get('gpu_layers', '?')}, tools={tool_capability.get('level', 'unknown')}"
    )
    llm_warn = None
    if config.get("runtime_mode") == "minimal" and not llm_loaded:
        llm_warn = "Runtime mode is 'minimal' — LLM not preloaded (loaded on demand)"
        llm_msg += " (minimal mode)"
    results.append(CheckResult("llm_loaded", llm_loaded, llm_msg, warning=llm_warn))

    # TTS status
    tts = models.get("tts", {})
    tts_loaded = tts.get("loaded", False)
    tts_msg = f"{tts.get('backend', '?')} | {tts.get('display', '?')}"
    results.append(CheckResult("tts_loaded", tts_loaded, tts_msg))

    # Runtime flags
    filler_enabled = _truthy(config.get("enable_filler_audio"), default=False)
    overlap_enabled = _truthy(config.get("llm_streaming_tts_overlap"), default=True)
    runtime_msg = f"filler_audio={filler_enabled}, llm_tts_overlap={overlap_enabled}"
    results.append(CheckResult("runtime_config", True, runtime_msg))

    # GPU status
    gpu_usable = gpu.get("runtime_usable", False)
    gpu_name = gpu.get("name") or "none"
    gpu_mem = gpu.get("memory_gb")
    gpu_msg = gpu_name
    if gpu_mem:
        gpu_msg += f" ({gpu_mem:.0f}GB)"
    gpu_msg += f" | usable={gpu_usable}"
    results.append(CheckResult("gpu", gpu_usable or True, gpu_msg))  # GPU is optional, don't fail

    # Degraded status
    if config.get("degraded"):
        errors = config.get("startup_errors", {})
        results.append(CheckResult("health", False, f"Server is degraded: {errors}"))

    return resp, results


async def check_llm(url: str, auth_token: Optional[str], timeout: float = 30.0) -> CheckResult:
    """Send a test prompt to the LLM and measure latency."""
    ws, err = await _connect(url, auth_token)
    if err:
        return CheckResult("llm_test", False, err)

    t0 = time.time()
    resp, err = await _send_recv_json(
        ws,
        {"type": "llm_request", "text": "Say hello in one sentence.", "mode": "llm"},
        timeout=timeout,
    )
    latency = time.time() - t0
    await ws.close()

    if err:
        return CheckResult("llm_test", False, err, latency=latency)

    if not resp or resp.get("type") != "llm_response":
        return CheckResult("llm_test", False, f"Unexpected: {resp}", latency=latency)

    text = (resp.get("text") or "").strip()
    if not text:
        return CheckResult("llm_test", False, "Empty LLM response", latency=latency)

    preview = text[:80] + ("..." if len(text) > 80 else "")
    warning = None
    if latency > LLM_TELEPHONY_WARN_SEC:
        warning = (
            f"LLM response took {latency:.1f}s — too slow for telephony. "
            "Consider using a cloud LLM (OpenAI, Deepgram, Telnyx) or adding a GPU."
        )

    return CheckResult("llm_test", True, f'"{preview}" ({latency:.2f}s)', latency=latency, warning=warning)


async def check_tts(url: str, auth_token: Optional[str]) -> CheckResult:
    """Send test text to TTS and verify audio is returned."""
    ws, err = await _connect(url, auth_token)
    if err:
        return CheckResult("tts_test", False, err)

    t0 = time.time()
    resp, err = await _send_recv_json(
        ws,
        {
            "type": "tts_request",
            "text": "Hello, this is a test of the text to speech system.",
            "response_format": "json",
        },
        timeout=15.0,
    )
    latency = time.time() - t0
    await ws.close()

    if err:
        return CheckResult("tts_test", False, err, latency=latency)

    if not resp or resp.get("type") != "tts_response":
        return CheckResult("tts_test", False, f"Unexpected: {resp}", latency=latency)

    byte_length = resp.get("byte_length", 0)
    encoding = resp.get("encoding", "?")
    sample_rate = resp.get("sample_rate_hz", "?")

    if byte_length == 0:
        return CheckResult("tts_test", False, "TTS returned 0 bytes audio", latency=latency)

    msg = f"{byte_length} bytes {encoding}@{sample_rate}Hz ({latency:.2f}s)"
    return CheckResult("tts_test", True, msg, latency=latency)


async def check_stt(url: str, auth_token: Optional[str]) -> CheckResult:
    """Round-trip test: TTS generates audio, then STT transcribes it."""
    # Step 1: Generate audio via TTS
    ws1, err = await _connect(url, auth_token)
    if err:
        return CheckResult("stt_test", False, f"TTS connection: {err}")

    resp, err = await _send_recv_json(
        ws1,
        {
            "type": "tts_request",
            "text": "Hello, this is a test of the speech recognition system.",
            "response_format": "json",
        },
        timeout=15.0,
    )
    await ws1.close()

    if err or not resp:
        return CheckResult("stt_test", False, f"TTS step failed: {err}")

    audio_b64 = resp.get("audio_data", "")
    if not audio_b64:
        return CheckResult("stt_test", False, "TTS returned no audio_data for STT test")

    # Step 2: Convert mulaw 8kHz -> PCM16 16kHz
    try:
        import audioop
        audio_mulaw = base64.b64decode(audio_b64)
        pcm8k = audioop.ulaw2lin(audio_mulaw, 2)
        pcm16k, _ = audioop.ratecv(pcm8k, 2, 1, 8000, 16000, None)
    except Exception as exc:
        return CheckResult("stt_test", False, f"Audio conversion failed: {exc}")

    # Step 3: Send to STT on fresh connection
    ws2, err = await _connect(url, auth_token)
    if err:
        return CheckResult("stt_test", False, f"STT connection: {err}")

    await _send_recv_json(ws2, {"type": "set_mode", "mode": "stt"}, timeout=5.0)

    t0 = time.time()
    audio_payload = {
        "type": "audio",
        "data": base64.b64encode(pcm16k).decode(),
        "mode": "stt",
        "rate": 16000,
    }
    try:
        await ws2.send(json.dumps(audio_payload))
    except Exception as exc:
        await ws2.close()
        return CheckResult("stt_test", False, f"Failed to send audio: {exc}")

    # Wait for final STT result
    transcript = ""
    try:
        while True:
            raw = await asyncio.wait_for(ws2.recv(), timeout=10.0)
            if isinstance(raw, str):
                msg = json.loads(raw)
                if msg.get("type") == "stt_result" and msg.get("is_final") and msg.get("text", "").strip():
                    transcript = msg["text"]
                    break
    except asyncio.TimeoutError:
        pass
    except Exception:
        pass

    latency = time.time() - t0
    await ws2.close()

    if transcript.strip():
        preview = transcript[:80] + ("..." if len(transcript) > 80 else "")
        return CheckResult("stt_test", True, f'"{preview}" ({latency:.2f}s)', latency=latency)
    else:
        return CheckResult("stt_test", False, f"No transcript returned within timeout ({latency:.2f}s)", latency=latency)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
async def run_all_checks(
    url: str,
    auth_token: Optional[str],
    llm_timeout: float = 30.0,
) -> Tuple[List[CheckResult], Optional[Dict]]:
    """Run all checks and return (results, status_data)."""
    all_results: List[CheckResult] = []

    # 1. Status check
    status_data, status_results = await check_status(url, auth_token)
    all_results.extend(status_results)

    if status_data is None:
        # Connection failed — skip functional tests
        return all_results, None

    # 2. Functional tests (LLM, TTS, STT)
    models = status_data.get("models", {})

    # LLM test (only if loaded or full mode)
    llm_loaded = models.get("llm", {}).get("loaded", False)
    if llm_loaded:
        all_results.append(await check_llm(url, auth_token, timeout=llm_timeout))
    else:
        all_results.append(CheckResult("llm_test", False, "Skipped — LLM not loaded", warning="LLM not loaded; functional test skipped"))

    # TTS test
    tts_loaded = models.get("tts", {}).get("loaded", False)
    if tts_loaded:
        all_results.append(await check_tts(url, auth_token))
    else:
        all_results.append(CheckResult("tts_test", False, "Skipped — TTS not loaded"))

    # STT test (requires both TTS and STT loaded for round-trip)
    stt_loaded = models.get("stt", {}).get("loaded", False)
    if stt_loaded and tts_loaded:
        all_results.append(await check_stt(url, auth_token))
    elif not stt_loaded:
        all_results.append(CheckResult("stt_test", False, "Skipped — STT not loaded"))
    else:
        all_results.append(CheckResult("stt_test", False, "Skipped — TTS not loaded (needed for round-trip test)"))

    return all_results, status_data


def _load_auth_from_env(project_root: Optional[str]) -> Optional[str]:
    """Read LOCAL_WS_AUTH_TOKEN from .env file."""
    if not project_root:
        project_root = os.getcwd()
    env_path = os.path.join(project_root, ".env")
    if not os.path.isfile(env_path):
        return None
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip() == "LOCAL_WS_AUTH_TOKEN":
                    return val.strip().strip('"').strip("'") or None
    except Exception:
        pass
    return None


def _print_text_report(url: str, results: List[CheckResult]) -> int:
    """Print human-readable report. Returns exit code."""
    print()
    print(_bold("=== Local AI Server Check ==="))
    print(f"Host: {url}")
    print()

    fail_count = 0
    warn_count = 0

    for r in results:
        if r.passed:
            print(_ok(f"{r.name}: {r.message}"))
        else:
            print(_fail(f"{r.name}: {r.message}"))
            fail_count += 1

        if r.warning:
            print(_warn(f"  {r.warning}"))
            warn_count += 1

    print()
    if fail_count == 0:
        print(_bold(_green("All checks passed ✅")))
        if warn_count > 0:
            print(_yellow(f"  ({warn_count} warning(s))"))
        return 0
    else:
        print(_bold(_red(f"{fail_count} check(s) failed ❌")))
        return 1


def _print_json_report(url: str, results: List[CheckResult], status_data: Optional[Dict]) -> int:
    """Print JSON report. Returns exit code."""
    fail_count = sum(1 for r in results if not r.passed)
    output = {
        "url": url,
        "checks": [r.to_dict() for r in results],
        "all_passed": fail_count == 0,
        "fail_count": fail_count,
    }
    if status_data:
        output["gpu"] = status_data.get("gpu", {})
        output["models"] = status_data.get("models", {})
    print(json.dumps(output, indent=2))
    return 0 if fail_count == 0 else 1


def main() -> None:
    # Early exit: if websockets not available and --local, re-run inside container
    if not _HAS_WEBSOCKETS and "--local" in sys.argv:
        print("websockets not installed on host — running inside local_ai_server container...", file=sys.stderr)
        sys.exit(_reexec_in_container(sys.argv))
    if not _HAS_WEBSOCKETS and "--remote" in sys.argv:
        print("Error: 'websockets' package required for --remote. Install: pip3 install websockets", file=sys.stderr)
        sys.exit(2)

    parser = argparse.ArgumentParser(
        description="Check Local AI Server components (STT, LLM, TTS)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python3 scripts/check_local_server.py --local
  python3 scripts/check_local_server.py --remote 10.0.0.50
  python3 scripts/check_local_server.py --remote 10.0.0.50 --port 9000 --auth-token secret
  python3 scripts/check_local_server.py --local --json""",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--local", action="store_true", help="Check local_ai_server on 127.0.0.1")
    group.add_argument("--remote", metavar="IP", help="Check remote local_ai_server at IP address")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket port (default: 8765)")
    parser.add_argument("--auth-token", default=None, help="Override LOCAL_WS_AUTH_TOKEN from .env")
    parser.add_argument("--project-root", default=None, help="Project root for .env lookup")
    parser.add_argument("--timeout", type=float, default=30.0, help="LLM timeout in seconds (default: 30)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    parser.add_argument("--no-color", action="store_true", help="Disable colour output")
    args = parser.parse_args()

    global _USE_COLOR
    if args.no_color or not sys.stdout.isatty():
        _USE_COLOR = False

    host = "127.0.0.1" if args.local else args.remote
    url = f"ws://{host}:{args.port}"

    # Auth token: CLI flag > .env file
    auth_token = args.auth_token
    if not auth_token:
        auth_token = _load_auth_from_env(args.project_root)

    results, status_data = asyncio.run(run_all_checks(url, auth_token, llm_timeout=args.timeout))

    if args.json_output:
        exit_code = _print_json_report(url, results, status_data)
    else:
        exit_code = _print_text_report(url, results)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
