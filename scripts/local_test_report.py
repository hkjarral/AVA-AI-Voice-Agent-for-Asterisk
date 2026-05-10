#!/usr/bin/env python3
"""Generate a Community Test Matrix submission from the last local-provider call.

Usage:
    python3 scripts/local_test_report.py [--json] [--ws-url URL] [--auth-token TOKEN]

Collects:
  1. Hardware info (CPU, RAM, GPU via nvidia-smi)
  2. Local AI Server status via WebSocket (model names, GPU, config)
  3. Last-call latency from local_ai_server docker logs
  4. .env / ai-agent.yaml for transport + pipeline config

Outputs a ready-to-paste COMMUNITY_TEST_MATRIX.md submission template.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------

def detect_cpu() -> str:
    """Return a short CPU description."""
    try:
        if platform.system() == "Linux":
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        elif platform.system() == "Darwin":
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                text=True, timeout=5,
            ).strip()
            if out:
                return out
    except Exception:
        pass
    return platform.processor() or "unknown"


def detect_ram_gb() -> str:
    try:
        if platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        kb = int(re.search(r"\d+", line).group())
                        return f"{round(kb / 1024 / 1024)}GB"
        elif platform.system() == "Darwin":
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True, timeout=5,
            ).strip()
            return f"{round(int(out) / 1024 / 1024 / 1024)}GB"
    except Exception:
        pass
    return "unknown"


def detect_gpu() -> str:
    """Return GPU name from nvidia-smi, or 'None (CPU only)'."""
    if not shutil.which("nvidia-smi"):
        return "None (CPU only)"
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            text=True, timeout=10,
        ).strip()
        if out:
            parts = out.split("\n")[0].split(",")
            name = parts[0].strip()
            mem = parts[1].strip() if len(parts) > 1 else ""
            if mem:
                return f"{name} {round(int(mem) / 1024)}GB"
            return name
    except Exception:
        pass
    return "GPU detected (details unavailable)"


def detect_os_version() -> str:
    try:
        if platform.system() == "Linux":
            for path in ["/etc/os-release", "/etc/lsb-release"]:
                if os.path.exists(path):
                    with open(path) as f:
                        for line in f:
                            if line.startswith("PRETTY_NAME="):
                                return line.split("=", 1)[1].strip().strip('"')
        return f"{platform.system()} {platform.release()}"
    except Exception:
        return platform.platform()


def detect_docker_version() -> str:
    try:
        out = subprocess.check_output(
            ["docker", "--version"], text=True, timeout=5,
        ).strip()
        return out.replace("Docker version ", "").split(",")[0]
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# .env / config reading
# ---------------------------------------------------------------------------

def read_env(project_root: Path) -> Dict[str, str]:
    """Read .env file into a dict (skip comments, strip quotes)."""
    env_path = project_root / ".env"
    values: Dict[str, str] = {}
    if not env_path.exists():
        return values
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            values[key.strip()] = val
    return values


def detect_pipeline(project_root: Path, env: Dict[str, str]) -> str:
    """Best-effort detection of pipeline from ai-agent.yaml or env."""
    # Check ai-agent.yaml
    for yaml_path in [
        project_root / "config" / "ai-agent.yaml",
        project_root / "config" / "ai-agent.yml",
    ]:
        if yaml_path.exists():
            try:
                text = yaml_path.read_text()
                m = re.search(r"provider:\s*(\S+)", text)
                if m:
                    return m.group(1)
            except Exception:
                pass
    return env.get("AI_PROVIDER", "unknown")


def detect_transport(env: Dict[str, str]) -> str:
    t = env.get("AUDIO_TRANSPORT", "").lower()
    if "audiosocket" in t:
        return "AudioSocket"
    if "external" in t or "rtp" in t:
        return "ExternalMedia RTP"
    return "ExternalMedia RTP"  # default


# ---------------------------------------------------------------------------
# WebSocket status query
# ---------------------------------------------------------------------------

async def query_local_ai_status(
    ws_url: str, auth_token: str = "",
) -> Optional[Dict[str, Any]]:
    """Connect to local_ai_server WS and request status.

    Tries three approaches in order:
    1. Host-side websockets library (if installed)
    2. docker exec into local_ai_server container (uses container's Python)
    3. Returns None (caller falls back to .env)
    """
    # Approach 1: host-side websockets
    try:
        import websockets  # type: ignore
        headers = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        try:
            async with websockets.connect(ws_url, additional_headers=headers, open_timeout=5) as ws:
                await ws.send(json.dumps({"type": "status"}))
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(raw)
                if data.get("type") == "status_response":
                    return data
        except Exception as exc:
            print(f"[WARN] Host WS query failed ({exc}); trying docker exec...", file=sys.stderr)
    except ImportError:
        pass  # Fall through to docker exec

    # Approach 2: docker exec into the container
    return _query_status_via_docker(ws_url, auth_token)


def _query_status_via_docker(
    ws_url: str, auth_token: str = "",
) -> Optional[Dict[str, Any]]:
    """Query local_ai_server status via docker exec (container has websockets).

    Uses a synchronous raw-socket WebSocket handshake + send/recv to avoid
    asyncio.run() conflicts when the container already has an event loop.
    """
    # Minimal synchronous WS client — no external deps beyond stdlib.
    # Works inside the container without interfering with the running server.
    auth_header = ""
    if auth_token:
        auth_header = f'        b"Authorization: Bearer {auth_token}\\r\\n" +'
    inner_script = f"""\
import socket, json, struct, hashlib, base64, os
sock = socket.create_connection(("127.0.0.1", 8765), timeout=5)
key = base64.b64encode(os.urandom(16)).decode()
req = (
    b"GET / HTTP/1.1\\r\\n"
    b"Host: 127.0.0.1:8765\\r\\n"
    b"Upgrade: websocket\\r\\n"
    b"Connection: Upgrade\\r\\n"
{auth_header}
    b"Sec-WebSocket-Key: " + key.encode() + b"\\r\\n"
    b"Sec-WebSocket-Version: 13\\r\\n"
    b"\\r\\n"
)
sock.sendall(req)
resp = b""
while b"\\r\\n\\r\\n" not in resp:
    resp += sock.recv(4096)
# Send status request as a text frame
payload = json.dumps({{"type": "status"}}).encode()
frame = bytearray()
frame.append(0x81)  # FIN + text
mask_key = os.urandom(4)
length = len(payload)
if length < 126:
    frame.append(0x80 | length)  # MASK bit + length
else:
    frame.append(0x80 | 126)
    frame.extend(struct.pack("!H", length))
frame.extend(mask_key)
masked = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))
frame.extend(masked)
sock.sendall(bytes(frame))
# Read response frame
data = b""
while len(data) < 2:
    data += sock.recv(4096)
b1, b2 = data[0], data[1]
plen = b2 & 0x7F
offset = 2
if plen == 126:
    while len(data) < 4:
        data += sock.recv(4096)
    plen = struct.unpack("!H", data[2:4])[0]
    offset = 4
elif plen == 127:
    while len(data) < 10:
        data += sock.recv(4096)
    plen = struct.unpack("!Q", data[2:10])[0]
    offset = 10
while len(data) < offset + plen:
    data += sock.recv(4096)
msg = data[offset:offset + plen].decode("utf-8", errors="replace")
sock.close()
print(msg)
"""
    try:
        proc = subprocess.run(
            ["docker", "exec", "-i", "local_ai_server", "python3", "-"],
            input=inner_script, text=True, timeout=15,
            capture_output=True,
        )
        out = proc.stdout.strip()
        if out:
            data = json.loads(out)
            if data.get("type") == "status_response":
                return data
        if proc.returncode != 0 and proc.stderr.strip():
            print(f"[WARN] docker exec stderr: {proc.stderr.strip()[:200]}", file=sys.stderr)
    except Exception as exc:
        print(f"[WARN] Could not query local_ai_server status: {exc}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Docker log parsing — extract last-call latency markers
# ---------------------------------------------------------------------------

def parse_local_ai_logs(lines: int = 2000) -> Dict[str, Any]:
    """Parse recent local_ai_server docker logs for latency markers."""
    latency: Dict[str, Any] = {}

    try:
        out = subprocess.check_output(
            ["docker", "logs", "--tail", str(lines), "local_ai_server"],
            text=True, timeout=15, stderr=subprocess.STDOUT,
        )
    except Exception as exc:
        print(f"[WARN] Could not read local_ai_server logs: {exc}", file=sys.stderr)
        return latency

    # LLM latency: "🤖 LLM RESULT - Completed in <ms> ms"
    llm_matches = re.findall(r"LLM RESULT.*?Completed in (\d+(?:\.\d+)?) ms", out)
    if llm_matches:
        latency["llm_last_ms"] = float(llm_matches[-1])
        latency["llm_all_ms"] = [float(x) for x in llm_matches]

    # LLM startup latency: "LLM STARTUP LATENCY - <ms> ms"
    startup_match = re.findall(r"LLM STARTUP LATENCY.*?(\d+(?:\.\d+)?) ms", out)
    if startup_match:
        latency["llm_startup_ms"] = float(startup_match[-1])

    # STT results count (proxy for call activity)
    stt_matches = re.findall(r"STT RESULT.*?transcript: '([^']*)'", out, re.IGNORECASE)
    latency["stt_transcripts_count"] = len(stt_matches)
    if stt_matches:
        latency["stt_last_transcript"] = stt_matches[-1][:80]

    # TTS results: "TTS RESULT - <backend> generated uLaw 8kHz audio: <bytes> bytes"
    tts_matches = re.findall(r"TTS RESULT.*?(\d+) bytes", out)
    latency["tts_responses_count"] = len(tts_matches)
    if tts_matches:
        latency["tts_last_bytes"] = int(tts_matches[-1])

    return latency


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_RE.sub('', text)


def _extract_kv(line: str, key: str) -> str:
    """Extract a key=value or key='value' from a log line."""
    # Strip ANSI color codes first (structlog colored console output)
    clean = _strip_ansi(line)
    m = re.search(rf'{key}=\'([^\']*)\'|{key}="([^"]*)"|{key}=(\S+)', clean)
    if m:
        return m.group(1) or m.group(2) or m.group(3) or ""
    return ""


def parse_tool_calls(lines: int = 15000) -> List[Dict[str, Any]]:
    """Parse recent ai_engine docker logs for tool call events."""
    tool_calls: List[Dict[str, Any]] = []

    try:
        out = subprocess.check_output(
            ["docker", "logs", "--tail", str(lines), "ai_engine"],
            text=True, timeout=15, stderr=subprocess.STDOUT,
        )
    except Exception as exc:
        print(f"[WARN] Could not read ai_engine logs: {exc}", file=sys.stderr)
        return tool_calls

    for line in out.splitlines():
        # Local tool patterns MUST be checked first because
        # "Local tool execution complete" contains "Tool execution complete"

        # Local tool execution complete
        if "Local tool execution complete" in line:
            raw_status = (_extract_kv(line, "status") or "success").lower()
            result = "failed" if raw_status in ("error", "failed", "failure") else "success"
            tool_calls.append({
                "name": _extract_kv(line, "tool_name") or _extract_kv(line, "function_name") or "unknown",
                "status": raw_status,
                "result": result,
                "source": "local_llm",
                "call_id": _extract_kv(line, "call_id") or "",
            })
            continue

        # Local tool execution failed
        if "Local tool execution failed" in line:
            tool_calls.append({
                "name": _extract_kv(line, "tool_name") or _extract_kv(line, "function_name") or "unknown",
                "status": "failed",
                "result": "failed",
                "error": _extract_kv(line, "error") or "",
                "source": "local_llm",
                "call_id": _extract_kv(line, "call_id") or "",
            })
            continue

        # Guardrail blocked tool call
        if "Dropping hangup_call" in line or "Dropping disallowed tool" in line:
            tool_calls.append({
                "name": "hangup_call",
                "status": "blocked",
                "result": "blocked_by_guardrail",
                "source": "guardrail",
                "call_id": _extract_kv(line, "call_id") or "",
            })
            continue

        # Tool execution complete (monolithic providers / pipeline)
        if "Tool execution complete" in line:
            raw_status = (_extract_kv(line, "status") or "success").lower()
            result = "failed" if raw_status in ("error", "failed", "failure") else "success"
            tool_calls.append({
                "name": _extract_kv(line, "function_name") or _extract_kv(line, "tool") or "unknown",
                "status": raw_status,
                "result": result,
                "source": "provider",
                "call_id": _extract_kv(line, "call_id") or "",
            })
            continue

        # Tool execution failed (monolithic providers / pipeline)
        if "Tool execution failed" in line:
            tool_calls.append({
                "name": _extract_kv(line, "function_name") or _extract_kv(line, "tool") or "unknown",
                "status": "failed",
                "result": "failed",
                "error": _extract_kv(line, "error") or "",
                "source": "provider",
                "call_id": _extract_kv(line, "call_id") or "",
            })
            continue

        # Post-call tool completed / failed
        if "Post-call tool completed" in line:
            tool_calls.append({
                "name": _extract_kv(line, "tool") or "unknown",
                "status": "success",
                "result": "success",
                "source": "post_call",
                "call_id": _extract_kv(line, "call_id") or "",
            })
            continue

        if "Post-call tool failed" in line:
            tool_calls.append({
                "name": _extract_kv(line, "tool") or "unknown",
                "status": "failed",
                "result": "failed",
                "error": _extract_kv(line, "error") or "",
                "source": "post_call",
                "call_id": _extract_kv(line, "call_id") or "",
            })
            continue

        # Pre-call tool completed / timed out / failed
        if "Pre-call tool completed" in line:
            tool_calls.append({
                "name": _extract_kv(line, "tool") or "unknown",
                "status": "success",
                "result": "success",
                "source": "pre_call",
                "call_id": _extract_kv(line, "call_id") or "",
            })
            continue

        if "Pre-call tool timed out" in line:
            tool_calls.append({
                "name": _extract_kv(line, "tool") or "unknown",
                "status": "timeout",
                "result": "failed",
                "source": "pre_call",
                "call_id": _extract_kv(line, "call_id") or "",
            })
            continue

        # LLM emitted <tool_call> markup but engine did NOT parse it as a tool call
        # (logged as "LLM response received (no tools)" with <tool_call> in preview)
        # This is important for evaluating LLM tool-calling quality across models.
        clean = _strip_ansi(line)
        if "LLM response received (no tools)" in clean and "<tool_call>" in clean:
            # Extract tool name from the preview text
            tc_match = re.search(r'"name"\s*:\s*"([^"]+)"', clean)
            tc_name = tc_match.group(1) if tc_match else "unknown"
            tool_calls.append({
                "name": tc_name,
                "status": "not_parsed",
                "result": "attempted_not_executed",
                "source": "llm_markup",
                "call_id": _extract_kv(line, "call_id") or "",
            })
            continue

    return tool_calls


def summarize_tool_calls(tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize tool calls into a compact report.

    Groups by tool name with counts for success, failed, blocked,
    and attempted_not_executed (LLM emitted markup but engine didn't parse it).
    """
    if not tool_calls:
        return {}

    summary: Dict[str, Any] = {}

    for call in tool_calls:
        name = call["name"]
        if name not in summary:
            summary[name] = {
                "success": 0,
                "failed": 0,
                "blocked": 0,
                "attempted_not_executed": 0,
                "sources": set(),
                "errors": [],
            }
        result = call.get("result", "success")
        if result == "success":
            summary[name]["success"] += 1
        elif "blocked" in result:
            summary[name]["blocked"] += 1
        elif result == "attempted_not_executed":
            summary[name]["attempted_not_executed"] += 1
        else:
            summary[name]["failed"] += 1
            err = call.get("error", "")
            if err:
                summary[name]["errors"].append(err)

        summary[name]["sources"].add(call.get("source", "unknown"))

    # Convert sets to lists for JSON serialization
    for name in summary:
        summary[name]["sources"] = sorted(summary[name]["sources"])

    return summary


# ---------------------------------------------------------------------------
# Extract model info from WS status
# ---------------------------------------------------------------------------

def _normalize_bool_str(value: Any, default: str = "unknown") -> str:
    """Normalize mixed bool/string runtime-flag values to 'true'/'false'.

    Status payload uses real booleans; env mode emits strings like '0'/'1'/'true'/'false'.
    Without this, str(bool('false')) renders 'false' as 'true', and the report
    inconsistently mixes '0'/'1' with 'true'/'false'.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return "true"
        if v in {"0", "false", "no", "off"}:
            return "false"
    return default


def extract_model_info(status: Optional[Dict[str, Any]], env: Dict[str, str]) -> Dict[str, str]:
    """Extract STT/TTS/LLM model details from WS status response or env."""
    info: Dict[str, str] = {
        "stt_backend": "unknown",
        "stt_model": "unknown",
        "stt_device": "unknown",
        "stt_compute": "unknown",
        "tts_backend": "unknown",
        "tts_voice": "unknown",
        "llm_model": "none",
        "llm_context": "N/A",
        "llm_max_tokens": "N/A",
        "llm_gpu_layers": env.get("LOCAL_LLM_GPU_LAYERS", "not set"),
        "llm_tool_capability": "unknown",
        "runtime_mode": "unknown",
        "filler_audio": "unknown",
        "llm_tts_overlap": "unknown",
    }

    if status:
        models = status.get("models", {})

        # STT
        stt = models.get("stt", {})
        info["stt_backend"] = stt.get("backend", status.get("stt_backend", "unknown"))
        info["stt_model"] = stt.get("display", stt.get("path", "unknown"))
        info["stt_device"] = str(stt.get("device", "unknown"))
        info["stt_compute"] = str(stt.get("compute_type", "unknown"))

        # TTS
        tts = models.get("tts", {})
        info["tts_backend"] = tts.get("backend", status.get("tts_backend", "unknown"))
        info["tts_voice"] = tts.get("display", tts.get("path", "unknown"))

        # LLM
        llm = models.get("llm", {})
        info["llm_model"] = llm.get("display", "none")
        llm_config = llm.get("config", {})
        info["llm_context"] = str(llm_config.get("context", "N/A"))
        info["llm_max_tokens"] = str(llm_config.get("max_tokens", "N/A"))
        info["llm_gpu_layers"] = str(llm_config.get("gpu_layers", info["llm_gpu_layers"]))
        tool_capability = llm.get("tool_capability")
        if isinstance(tool_capability, dict):
            info["llm_tool_capability"] = str(tool_capability.get("level", "unknown"))
        elif tool_capability is None:
            info["llm_tool_capability"] = "unknown"
        else:
            info["llm_tool_capability"] = str(tool_capability)

        # Config
        config = status.get("config", {})
        info["runtime_mode"] = config.get("runtime_mode", "unknown")
        info["filler_audio"] = _normalize_bool_str(config.get("enable_filler_audio"), default="false")
        info["llm_tts_overlap"] = _normalize_bool_str(config.get("llm_streaming_tts_overlap"), default="true")

        # GPU
        gpu = status.get("gpu", {})
        if gpu.get("runtime_usable") or gpu.get("runtime_detected"):
            info["gpu_from_server"] = gpu.get("name") or "detected"
            memory_gb = gpu.get("memory_gb")
            if memory_gb:
                info["gpu_from_server"] += f" ({memory_gb}GB)"
    else:
        # Fallback to env
        stt_backend = env.get("LOCAL_STT_BACKEND", "vosk")
        info["stt_backend"] = stt_backend
        # Dispatch on the active backend so we report the right model in the
        # fallback path (config.py uses different env vars per backend).
        if stt_backend == "faster_whisper":
            info["stt_model"] = env.get("FASTER_WHISPER_MODEL", "default")
        elif stt_backend == "sherpa":
            info["stt_model"] = env.get("SHERPA_MODEL_PATH", "default")
        elif stt_backend == "whisper_cpp":
            info["stt_model"] = env.get(
                "WHISPER_CPP_MODEL_PATH",
                env.get("LOCAL_WHISPER_CPP_MODEL_PATH", env.get("LOCAL_STT_MODEL_PATH", "default")),
            )
        elif stt_backend == "tone":
            info["stt_model"] = env.get("TONE_MODEL_PATH", "default")
        elif stt_backend == "kroko":
            info["stt_model"] = env.get("KROKO_MODEL_PATH", "default")
        else:
            # vosk and unknowns
            info["stt_model"] = env.get("LOCAL_STT_MODEL_PATH", "default")
        info["stt_device"] = env.get("FASTER_WHISPER_DEVICE", "unknown")
        info["stt_compute"] = env.get("FASTER_WHISPER_COMPUTE_TYPE", "unknown")
        info["tts_backend"] = env.get("LOCAL_TTS_BACKEND", "piper")
        info["tts_voice"] = env.get("LOCAL_TTS_MODEL_PATH", "default")
        info["llm_model"] = os.path.basename(env.get("LOCAL_LLM_MODEL_PATH", "none"))
        info["llm_context"] = env.get("LOCAL_LLM_CONTEXT", "default")
        info["llm_max_tokens"] = env.get("LOCAL_LLM_MAX_TOKENS", "default")
        gpu_avail = env.get("GPU_AVAILABLE", "false").lower() in ("1", "true", "yes")
        info["runtime_mode"] = env.get("LOCAL_AI_MODE", "minimal" if not gpu_avail else "full")
        info["filler_audio"] = _normalize_bool_str(env.get("LOCAL_ENABLE_FILLER_AUDIO"), default="false")
        info["llm_tts_overlap"] = _normalize_bool_str(env.get("LOCAL_LLM_STREAMING_TTS_OVERLAP"), default="true")

    return info


# ---------------------------------------------------------------------------
# Format output
# ---------------------------------------------------------------------------

def format_template(
    hw: Dict[str, str],
    model: Dict[str, str],
    latency: Dict[str, Any],
    pipeline: str,
    transport: str,
    tool_calls: Dict[str, Any],
) -> str:
    """Build the copy-paste community test matrix template."""
    llm_latency_str = "N/A"
    if "llm_last_ms" in latency:
        last = latency["llm_last_ms"]
        all_vals = latency.get("llm_all_ms", [last])
        if len(all_vals) > 1:
            avg = sum(all_vals) / len(all_vals)
            llm_latency_str = f"~{round(avg)}ms avg ({len(all_vals)} samples, last={round(last)}ms)"
        else:
            llm_latency_str = f"~{round(last)}ms"

    e2e_hint = ""
    if "llm_last_ms" in latency:
        # Rough E2E estimate: STT is near-instant for streaming, LLM dominates, TTS adds ~200-500ms
        e2e_ms = latency["llm_last_ms"] + 400  # rough TTS overhead
        if e2e_ms < 1000:
            e2e_hint = f"~{round(e2e_ms)}ms"
        else:
            e2e_hint = f"~{round(e2e_ms / 1000, 1)}s"
    else:
        e2e_hint = "not measured"

    llm_desc = model["llm_model"]
    if model["llm_context"] not in ("N/A", "default", "none"):
        llm_desc += f" / n_ctx={model['llm_context']}"
    if model["llm_max_tokens"] not in ("N/A", "default", "none"):
        llm_desc += f" / max_tokens={model['llm_max_tokens']}"

    lines = [
        "=" * 60,
        "COMMUNITY TEST MATRIX — Copy/paste this into a GitHub issue",
        "or PR to docs/COMMUNITY_TEST_MATRIX.md",
        "=" * 60,
        "",
        "```",
        f"**Date**: {date.today().isoformat()}",
        f"**Hardware**: {hw['cpu']}, {hw['ram']} RAM",
        f"**GPU**: {hw['gpu']}",
        f"**OS**: {hw['os']}",
        f"**Docker**: {hw['docker']}",
        f"**STT**: {model['stt_backend']} / {model['stt_model']}",
        f"**STT Runtime**: device={model['stt_device']}, compute={model['stt_compute']}",
        f"**TTS**: {model['tts_backend']} / {model['tts_voice']}",
        f"**LLM**: {llm_desc}",
        f"**LLM GPU Layers**: {model['llm_gpu_layers']}",
        f"**LLM Tool Capability**: {model['llm_tool_capability']}",
        f"**Transport**: {transport}",
        f"**Pipeline**: {pipeline}",
        f"**Runtime Mode**: {model['runtime_mode']}",
        f"**Runtime Flags**: filler_audio={model['filler_audio']}, llm_tts_overlap={model['llm_tts_overlap']}",
        f"**E2E Latency**: {e2e_hint}",
        f"**LLM Latency**: {llm_latency_str}",
        f"**STT Transcripts (last session)**: {latency.get('stt_transcripts_count', 0)}",
        f"**TTS Responses (last session)**: {latency.get('tts_responses_count', 0)}",
        f"**Quality (1-5)**: <your rating>",
        f"**Notes**: <any observations>",
    ]

    # Tool call section
    if tool_calls:
        lines.append("**Tool Calls**:")
        for tool, stats in tool_calls.items():
            ok = stats.get('success', 0)
            fail = stats.get('failed', 0)
            blocked = stats.get('blocked', 0)
            attempted = stats.get('attempted_not_executed', 0)
            sources = stats.get('sources', [])

            # Pick icon based on worst outcome
            if fail > 0:
                icon = "\u274c"
            elif blocked > 0 or attempted > 0:
                icon = "\u26a0\ufe0f"
            else:
                icon = "\u2705"

            parts = []
            if ok:
                parts.append(f"{ok} executed")
            if fail:
                parts.append(f"{fail} failed")
            if blocked:
                parts.append(f"{blocked} blocked")
            if attempted:
                parts.append(f"{attempted} attempted (not executed)")

            source_hint = f" [{', '.join(sources)}]" if sources else ""
            lines.append(f"  {icon} {tool}: {', '.join(parts)}{source_hint}")

            if fail > 0 and stats.get("errors"):
                errs = [e for e in stats["errors"] if e]
                if errs:
                    lines.append(f"    Errors: {', '.join(errs[:3])}")
    else:
        lines.append("**Tool Calls**: None detected")

    lines.extend([
        "```",
        "",
    ])

    # Also output the table row for direct PR addition
    lines.extend([
        "--- TABLE ROW (for direct PR to COMMUNITY_TEST_MATRIX.md) ---",
        "",
        f"| {date.today().isoformat()} "
        f"| @<your-github> "
        f"| {hw['cpu']}, {hw['ram']} "
        f"| {hw['gpu']} "
        f"| {model['stt_backend']} "
        f"| {model['stt_model']} "
        f"| {model['tts_backend']} "
        f"| {model['tts_voice']} "
        f"| {llm_desc} "
        f"| {model['llm_context']} "
        f"| {'em' if 'External' in transport else 'as'} "
        f"| {e2e_hint} "
        f"| <1-5> "
        f"| |",
        "",
    ])

    if "llm_startup_ms" in latency:
        lines.append(f"LLM warmup latency: {round(latency['llm_startup_ms'])}ms")
    if latency.get("stt_last_transcript"):
        lines.append(f"Last STT transcript: \"{latency['stt_last_transcript']}\"")

    return "\n".join(lines)


def format_json(
    hw: Dict[str, str],
    model: Dict[str, str],
    latency: Dict[str, Any],
    pipeline: str,
    transport: str,
    tool_calls: Dict[str, Any],
) -> str:
    """Build JSON output."""
    return json.dumps({
        "date": date.today().isoformat(),
        "hardware": hw,
        "models": model,
        "latency": latency,
        "pipeline": pipeline,
        "transport": transport,
        "tool_calls": tool_calls,
    }, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def async_main(args: argparse.Namespace) -> None:
    project_root = Path(args.project_root)
    env = read_env(project_root)

    # Hardware
    hw = {
        "cpu": detect_cpu(),
        "ram": detect_ram_gb(),
        "gpu": detect_gpu(),
        "os": detect_os_version(),
        "docker": detect_docker_version(),
    }

    # WS status
    ws_url = args.ws_url or env.get("LOCAL_WS_URL", f"ws://127.0.0.1:{env.get('LOCAL_WS_PORT', '8765')}")
    auth_token = args.auth_token or env.get("LOCAL_WS_AUTH_TOKEN", "")
    status = await query_local_ai_status(ws_url, auth_token)

    # Model info
    model = extract_model_info(status, env)

    # Latency from docker logs
    latency = parse_local_ai_logs(lines=args.log_lines)

    # Tool calls from ai_engine logs (use full history for sparse tool events)
    raw_tool_calls = parse_tool_calls()
    tool_calls = summarize_tool_calls(raw_tool_calls)

    # Pipeline + transport
    pipeline = detect_pipeline(project_root, env)
    transport = detect_transport(env)

    if args.json:
        print(format_json(hw, model, latency, pipeline, transport, tool_calls))
    else:
        print(format_template(hw, model, latency, pipeline, transport, tool_calls))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a Community Test Matrix submission from last local-provider call",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON instead of copy-paste template",
    )
    parser.add_argument(
        "--ws-url", default="",
        help="WebSocket URL for local_ai_server (default: from .env or ws://127.0.0.1:8765)",
    )
    parser.add_argument(
        "--auth-token", default="",
        help="Auth token for local_ai_server WS (default: from .env LOCAL_WS_AUTH_TOKEN)",
    )
    parser.add_argument(
        "--project-root", default=".",
        help="Path to project root (default: current directory)",
    )
    parser.add_argument(
        "--log-lines", type=int, default=2000,
        help="Number of docker log lines to parse (default: 2000)",
    )
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
