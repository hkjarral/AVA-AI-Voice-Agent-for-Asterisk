#!/usr/bin/env python3
"""
AAVA Test Call Script -- MVP v3

Originates a call to the AAVA demo IVR at (925) 736-6718 via Asterisk AMI,
records the call via MixMonitor (dialplan), sends DTMF to select a provider,
waits for the call to complete, transcribes the recording via Deepgram,
and outputs a JSON report.

Usage:
    python3 test_call.py --provider 7
    python3 test_call.py --provider all --timeout 45
    python3 test_call.py --provider 5 --output text

Approach:
    Previous versions used ARI (Stasis) for call control, but ARI-controlled
    channels don't flow audio through Asterisk's internal pipeline, making
    MixMonitor and ARI recording produce empty files.

    This version uses AMI (Asterisk Manager Interface) to originate calls
    into a dialplan context [aava-test-call] that handles MixMonitor and
    Dial() natively. The Python script monitors AMI events, injects DTMF
    via AMI PlayDTMF after the IVR answers, and waits for the call to end.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Optional: load .env from project root
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Provider Map
# ---------------------------------------------------------------------------
PROVIDER_MAP = {
    "5":  "Google Live API",
    "6":  "Deepgram Voice Agent",
    "7":  "OpenAI Realtime API",
    "8":  "Local Hybrid Pipeline",
    "9":  "ElevenLabs Agent",
    "10": "Fully Local Pipeline",
}

IVR_NUMBER = "19257366718"

# AMI credentials (from FreePBX manager.conf)
AMI_HOST = "127.0.0.1"
AMI_PORT = 5038
AMI_USER = "OgtKe0U6pHl1"
AMI_PASS = "Xr3CjJD0qVRR"


# ---------------------------------------------------------------------------
# AMI Client
# ---------------------------------------------------------------------------

class AMIClient:
    """Async AMI client using asyncio streams."""

    def __init__(self, host, port, username, secret):
        self.host = host
        self.port = port
        self.username = username
        self.secret = secret
        self.reader = None
        self.writer = None
        self._action_id = 0
        self._event_handlers = []

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection(
            self.host, self.port
        )
        # Read the AMI banner
        banner = await self.reader.readline()
        print(f"[AMI] {banner.decode().strip()}", file=sys.stderr)

        # Login
        resp = await self.send_action("Login", {
            "Username": self.username,
            "Secret": self.secret,
        })
        if resp.get("Response") != "Success":
            raise ConnectionError(
                f"AMI login failed: {resp.get('Message', 'unknown')}"
            )
        print("[AMI] Logged in", file=sys.stderr)

    async def close(self):
        if self.writer:
            try:
                await self.send_action("Logoff", {})
            except Exception:
                pass
            self.writer.close()

    async def _read_packet(self):
        """Read a single AMI packet (terminated by blank line)."""
        lines = []
        while True:
            line = await asyncio.wait_for(
                self.reader.readline(), timeout=30.0
            )
            line_str = line.decode(errors="replace").rstrip("\r\n")
            if line_str == "":
                if lines:
                    break
                continue
            lines.append(line_str)

        packet = {}
        for line in lines:
            if ": " in line:
                key, _, value = line.partition(": ")
                packet[key] = value
        return packet

    async def read_events(self, timeout=2.0):
        """Read events from AMI with a timeout. Returns list of events."""
        events = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                packet = await asyncio.wait_for(
                    self._read_packet(),
                    timeout=min(remaining, 1.0)
                )
                if "Event" in packet:
                    events.append(packet)
            except asyncio.TimeoutError:
                continue
        return events

    async def send_action(self, action, params=None, extra_headers=None):
        """Send an AMI action and wait for its response.

        Args:
            action: AMI action name
            params: dict of key-value params (one header per key)
            extra_headers: list of raw "Key: Value" strings for multi-value
                           headers like Variable
        """
        self._action_id += 1
        aid = f"tc-{self._action_id}"

        lines = [f"Action: {action}", f"ActionID: {aid}"]
        if params:
            for k, v in params.items():
                lines.append(f"{k}: {v}")
        if extra_headers:
            lines.extend(extra_headers)
        lines.append("")
        lines.append("")
        msg = "\r\n".join(lines)
        self.writer.write(msg.encode())
        await self.writer.drain()

        # Read response packets until we get one matching our ActionID
        while True:
            packet = await self._read_packet()
            if packet.get("ActionID") == aid:
                return packet

    async def originate(self, channel, context, exten, priority="1",
                        caller_id="", timeout_ms=45000, variables=None):
        """Originate a call via AMI using Context mode.

        When the channel answers, it enters context/exten/priority.
        Variables are sent as separate Variable headers to avoid
        pipe-separator parsing issues.
        """
        params = {
            "Channel": channel,
            "Context": context,
            "Exten": exten,
            "Priority": str(priority),
            "Timeout": str(timeout_ms),
            "Async": "true",
        }
        if caller_id:
            params["CallerID"] = caller_id

        # Build separate Variable headers for each variable
        extra = None
        if variables:
            extra = [f"Variable: {k}={v}" for k, v in variables.items()]

        return await self.send_action("Originate", params,
                                      extra_headers=extra)

    async def play_dtmf(self, channel, digit, duration_ms=500):
        """Send DTMF on a channel via AMI PlayDTMF."""
        return await self.send_action("PlayDTMF", {
            "Channel": channel,
            "Digit": str(digit),
            "Duration": str(duration_ms),
        })

    async def hangup(self, channel):
        """Hang up a channel."""
        return await self.send_action("Hangup", {
            "Channel": channel,
        })


# ---------------------------------------------------------------------------
# Deepgram transcription (optional)
# ---------------------------------------------------------------------------

async def transcribe_recording(recording_path, api_key):
    """Transcribe a WAV file via Deepgram REST API."""
    if not api_key or not os.path.isfile(recording_path):
        return None
    try:
        import httpx
        url = "https://api.deepgram.com/v1/listen"
        params = {
            "model": "nova-2",
            "smart_format": "true",
            "punctuate": "true",
        }
        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "audio/wav",
        }
        async with httpx.AsyncClient(timeout=120) as client:
            with open(recording_path, "rb") as f:
                audio_data = f.read()
            if len(audio_data) < 1000:
                print(
                    f"[WARN] Recording too small ({len(audio_data)} bytes), "
                    f"skipping transcription", file=sys.stderr
                )
                return None
            resp = await client.post(
                url, params=params, headers=headers, content=audio_data
            )
            if resp.status_code == 200:
                data = resp.json()
                channels = data.get("results", {}).get("channels", [])
                if not channels:
                    return None
                alternatives = channels[0].get("alternatives", [])
                if not alternatives:
                    return None
                transcript = alternatives[0].get("transcript", "")
                return transcript if transcript else None
            else:
                print(
                    f"[WARN] Deepgram returned {resp.status_code}: "
                    f"{resp.text[:200]}", file=sys.stderr
                )
    except ImportError:
        print("[WARN] httpx not installed, skipping transcription",
              file=sys.stderr)
    except Exception as e:
        print(f"[WARN] Deepgram transcription failed: {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Core test-call logic
# ---------------------------------------------------------------------------

async def run_test_call(provider_dtmf, timeout_secs, ari_host=None,
                        ari_port=None, ari_user=None, ari_pass=None,
                        ari_scheme=None):
    """Execute a single test call and return the report dict."""

    provider_name = PROVIDER_MAP.get(
        provider_dtmf, f"Unknown ({provider_dtmf})"
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    recording_name = f"test-call-{provider_dtmf}-{ts}"
    # Path without .wav extension — dialplan appends .wav
    recording_path = f"/var/spool/asterisk/monitor/{recording_name}"

    report = {
        "provider": provider_name,
        "provider_dtmf": provider_dtmf,
        "call_duration_seconds": 0,
        "greeting_latency_ms": None,
        "transcript": None,
        "recording_path": f"{recording_path}.wav",  # Final path with .wav
        "audio_quality": "unknown",
        "call_status": "failed",
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    ami = AMIClient(AMI_HOST, AMI_PORT, AMI_USER, AMI_PASS)
    pjsip_channel = None
    call_start_time = None
    dtmf_sent_time = None

    try:
        await ami.connect()
        print(
            f"[INFO] AMI connected. Testing provider {provider_dtmf} "
            f"({provider_name})", file=sys.stderr
        )

        # Originate via Local/ channel into aava-test-call context.
        # Context mode: both Local/ halves enter the same context and
        # both run Dial() to the IVR (creates 2 PJSIP calls).
        # MixMonitor captures audio because the bridge is active.
        # We send DTMF via AMI PlayDTMF after the call connects.
        variables = {
            "AAVA_TC_RECFILE": recording_path,
        }

        print("[INFO] Originating call...", file=sys.stderr)
        resp = await ami.originate(
            channel="Local/s@aava-test-call/n",
            context="aava-test-call",
            exten="s",
            priority="1",
            caller_id="Ava Test <9097882282>",
            timeout_ms=45000,
            variables=variables,
        )

        if resp.get("Response") != "Success":
            report["error"] = f"Originate failed: {resp.get('Message', '')}"
            return report

        print("[INFO] Originate accepted, waiting for events...",
              file=sys.stderr)

        # With Local/ + Context mode, AMI fires:
        # - OriginateResponse (call answered or failed)
        # - DialBegin (PJSIP channel dialing)
        # - DialEnd (IVR answered)
        # - BridgeEnter (Local/ + PJSIP bridged = audio path active)
        # We wait for BridgeEnter to know the call is fully connected.
        call_ended = False
        bridged = False
        answer_deadline = time.monotonic() + 60

        while time.monotonic() < answer_deadline:
            events = await ami.read_events(timeout=2.0)
            for evt in events:
                ename = evt.get("Event", "")

                if ename == "OriginateResponse":
                    status = evt.get("Response", "")
                    if status == "Success":
                        print("[INFO] Originate successful",
                              file=sys.stderr)
                    else:
                        reason = evt.get("Reason", "unknown")
                        report["error"] = f"Call failed: {reason}"
                        report["call_status"] = "no_answer"
                        return report

                if ename == "DialBegin":
                    dest = evt.get("DestChannel", "")
                    if "PJSIP/Telnyx" in dest and not pjsip_channel:
                        pjsip_channel = dest
                        print(
                            f"[INFO] Dialing IVR: {pjsip_channel}",
                            file=sys.stderr
                        )

                if ename == "BridgeEnter":
                    chan = evt.get("Channel", "")
                    if "PJSIP/Telnyx" in chan and not bridged:
                        pjsip_channel = chan
                        bridged = True
                        call_start_time = time.monotonic()
                        print(
                            f"[INFO] Call bridged! {pjsip_channel}",
                            file=sys.stderr
                        )

                if ename == "Hangup":
                    chan = evt.get("Channel", "")
                    if pjsip_channel and chan == pjsip_channel:
                        call_ended = True

            if bridged or call_ended:
                break

        if not bridged:
            if not call_ended:
                report["error"] = "Timed out waiting for bridge"
            report["call_status"] = "no_answer"
            return report

        # Call is bridged! MixMonitor is recording via dialplan.

        print("[INFO] Waiting 8s for IVR greeting...", file=sys.stderr)
        wait_end = time.monotonic() + 8.0
        while time.monotonic() < wait_end and not call_ended:
            events = await ami.read_events(timeout=1.0)
            for evt in events:
                if evt.get("Event") == "Hangup":
                    chan = evt.get("Channel", "")
                    if pjsip_channel and chan == pjsip_channel:
                        call_ended = True
                        print("[INFO] Call ended during IVR wait",
                              file=sys.stderr)

        if call_ended:
            report["call_status"] = "completed"
            if call_start_time:
                report["call_duration_seconds"] = round(
                    time.monotonic() - call_start_time, 1
                )
            await asyncio.sleep(1.0)
        else:
            # Send DTMF via AMI on the PJSIP channel
            print(f"[INFO] Sending DTMF: {provider_dtmf}", file=sys.stderr)
            for digit in str(provider_dtmf):
                dtmf_resp = await ami.play_dtmf(
                    pjsip_channel, digit, duration_ms=500
                )
                print(
                    f"[INFO] PlayDTMF '{digit}': "
                    f"{dtmf_resp.get('Response', '?')}",
                    file=sys.stderr
                )
                if len(str(provider_dtmf)) > 1:
                    await asyncio.sleep(0.3)

            dtmf_sent_time = time.monotonic()
            print("[INFO] DTMF sent", file=sys.stderr)

            # Wait for call to end (timeout or remote hangup)
            print(
                f"[INFO] Listening for {timeout_secs}s...",
                file=sys.stderr
            )
            call_deadline = time.monotonic() + timeout_secs

            while time.monotonic() < call_deadline and not call_ended:
                events = await ami.read_events(timeout=2.0)
                for evt in events:
                    if evt.get("Event") == "Hangup":
                        chan = evt.get("Channel", "")
                        if pjsip_channel and chan == pjsip_channel:
                            call_ended = True
                            print("[INFO] Remote hangup",
                                  file=sys.stderr)

            if not call_ended:
                print("[INFO] Timeout reached, hanging up...",
                      file=sys.stderr)
                if pjsip_channel:
                    await ami.hangup(pjsip_channel)

            if call_start_time:
                report["call_duration_seconds"] = round(
                    time.monotonic() - call_start_time, 1
                )

            report["call_status"] = "completed"

        # Wait for recording file to be written
        await asyncio.sleep(2.0)

        # Check recording
        rec_path = report["recording_path"]
        if os.path.isfile(rec_path):
            fsize = os.path.getsize(rec_path)
            print(f"[INFO] Recording: {fsize} bytes", file=sys.stderr)

            if fsize <= 44:
                print("[WARN] Recording empty (header only)",
                      file=sys.stderr)
                report["audio_quality"] = "no_audio"
            else:
                # Check for actual audio content
                try:
                    import wave
                    import struct
                    with wave.open(rec_path, "rb") as w:
                        frames = w.readframes(w.getnframes())
                        if w.getnframes() > 0:
                            samples = struct.unpack(
                                f"<{w.getnframes()}h", frames
                            )
                            max_amp = max(abs(s) for s in samples)
                            if max_amp == 0:
                                print(
                                    "[WARN] Recording is silence",
                                    file=sys.stderr
                                )
                                report["audio_quality"] = "silence"
                            else:
                                print(
                                    f"[INFO] Audio max amplitude: {max_amp}",
                                    file=sys.stderr
                                )
                except Exception:
                    pass
        else:
            print(f"[WARN] Recording not found: {rec_path}",
                  file=sys.stderr)
            report["recording_path"] = None

        # Attempt transcription
        deepgram_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
        if (deepgram_key and rec_path
                and os.path.isfile(rec_path)
                and os.path.getsize(rec_path) > 1000):
            print("[INFO] Transcribing via Deepgram...", file=sys.stderr)
            transcript = await transcribe_recording(rec_path, deepgram_key)
            if transcript:
                report["transcript"] = transcript
                print(
                    f"[INFO] Transcript: {transcript[:100]}...",
                    file=sys.stderr
                )
                report["audio_quality"] = "good"

        # Greeting latency from DTMF time (if we have transcript,
        # we know the greeting was received)
        if report.get("transcript") and dtmf_sent_time:
            # Approximate: first speech is typically 1-3s after DTMF
            report["greeting_latency_ms"] = None  # Can't measure precisely

    except Exception as e:
        report["error"] = str(e)
        report["call_status"] = "error"
        print(f"[ERROR] {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)

    finally:
        await ami.close()

    return report


async def run_all_providers(timeout_secs, *args, **kwargs):
    """Test all providers sequentially."""
    results = []
    sorted_keys = sorted(PROVIDER_MAP.keys(), key=int)
    for dtmf in sorted_keys:
        print(f"\n{'='*60}", file=sys.stderr)
        print(
            f"Testing provider {dtmf}: {PROVIDER_MAP[dtmf]}",
            file=sys.stderr
        )
        print(f"{'='*60}", file=sys.stderr)
        result = await run_test_call(dtmf, timeout_secs)
        results.append(result)
        if dtmf != sorted_keys[-1]:
            print("[INFO] Waiting 10s before next call...", file=sys.stderr)
            await asyncio.sleep(10)
    return results


def format_text_report(report):
    """Format a single report as human-readable text."""
    lines = [
        f"Provider: {report['provider']} (DTMF {report['provider_dtmf']})",
        f"Status:   {report['call_status']}",
        f"Duration: {report['call_duration_seconds']}s",
    ]
    if report.get("greeting_latency_ms") is not None:
        lines.append(
            f"Greeting Latency: {report['greeting_latency_ms']}ms"
        )
    lines.append(f"Audio Quality: {report['audio_quality']}")
    if report.get("transcript"):
        lines.append(f"Transcript: {report['transcript'][:200]}")
    if report.get("recording_path"):
        lines.append(f"Recording: {report['recording_path']}")
    if report.get("error"):
        lines.append(f"Error: {report['error']}")
    lines.append(f"Timestamp: {report['timestamp']}")
    return "\n".join(lines)


def format_all_text(results):
    """Format multi-provider results as a comparison table."""
    header = (
        f"{'Provider':<25} {'Status':<12} {'Duration':<10} "
        f"{'Latency':<12} {'Quality':<10}"
    )
    sep = "-" * len(header)
    lines = [header, sep]
    for r in results:
        latency = (
            f"{r['greeting_latency_ms']}ms"
            if r.get("greeting_latency_ms") is not None else "N/A"
        )
        lines.append(
            f"{r['provider']:<25} {r['call_status']:<12} "
            f"{r['call_duration_seconds']:<10} {latency:<12} "
            f"{r['audio_quality']:<10}"
        )
    lines.append(sep)

    completed = [
        r for r in results if r.get("greeting_latency_ms") is not None
    ]
    if completed:
        best = min(completed, key=lambda x: x["greeting_latency_ms"])
        worst = max(completed, key=lambda x: x["greeting_latency_ms"])
        lines.append(
            f"\nBest latency:  {best['provider']} "
            f"({best['greeting_latency_ms']}ms)"
        )
        lines.append(
            f"\nWorst latency: {worst['provider']} "
            f"({worst['greeting_latency_ms']}ms)"
        )

    failed = [r for r in results if r["call_status"] != "completed"]
    if failed:
        lines.append(
            f"\nFailed providers: "
            f"{', '.join(r['provider'] for r in failed)}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="AAVA Test Call -- Originate a call to the demo IVR, "
                    "select a provider via DTMF, record, and report."
    )
    parser.add_argument(
        "--provider", required=True,
        help="DTMF digit (5-10) or 'all' to test all providers."
    )
    parser.add_argument(
        "--timeout", type=int, default=60,
        help="Max call duration in seconds (default: 60)"
    )
    parser.add_argument(
        "--output", choices=["json", "text"], default="json",
        help="Output format (default: json)"
    )
    # Legacy ARI args (kept for compatibility but unused in v3)
    parser.add_argument("--ari-host", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--ari-port", type=int, default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--ari-user", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--ari-pass", default=None, help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.provider.lower() != "all" and args.provider not in PROVIDER_MAP:
        print(
            f"Error: Invalid provider DTMF '{args.provider}'. "
            f"Valid: "
            f"{', '.join(sorted(PROVIDER_MAP.keys(), key=int))} or 'all'",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.provider.lower() == "all":
        results = asyncio.run(run_all_providers(args.timeout))
        if args.output == "json":
            print(json.dumps(results, indent=2))
        else:
            print(format_all_text(results))
    else:
        result = asyncio.run(run_test_call(args.provider, args.timeout))
        if args.output == "json":
            print(json.dumps(result, indent=2))
        else:
            print(format_text_report(result))


if __name__ == "__main__":
    main()
