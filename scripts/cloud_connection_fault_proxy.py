#!/usr/bin/env python3
"""Loopback-only CONNECT fault proxy for supervised real-call qualification.

TLS remains end-to-end. No API keys, audio, WebSocket URLs or payloads are
inspected. Only initial CONNECT requests for --host are faulted. Enable for
ai_engine with wss_proxy=http://127.0.0.1:18766, then remove after testing.
"""
import argparse
import asyncio

CLOUD_HOSTS = {"api.openai.com", "api.x.ai", "agent.deepgram.com", "api.elevenlabs.io"}


def allowed(host):
    return host in CLOUD_HOSTS or host.endswith(".googleapis.com")


async def main(args):
    faults_remaining = args.fail_count
    attempts = 0

    async def handle(reader, writer):
        nonlocal faults_remaining, attempts
        upstream_writer = None
        tasks = []
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            method, target, _protocol = header.split(b"\r\n", 1)[0].decode("ascii").split()
            host, port = target.rsplit(":", 1)
            host = host.lower()
            if method != "CONNECT" or port != "443" or not allowed(host):
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            if host == args.host:
                attempts += 1
                if faults_remaining > 0:
                    faults_remaining -= 1
                    print(f"CONNECT host={host} attempt={attempts} injected={args.mode}", flush=True)
                    if args.mode == "timeout":
                        await asyncio.sleep(args.delay_sec)
                    else:
                        writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
                        await writer.drain()
                    return
                print(f"CONNECT host={host} attempt={attempts} tunnel=opening", flush=True)
            upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_connection(host, 443), timeout=10)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            async def copy(source, destination):
                while data := await source.read(65536):
                    destination.write(data)
                    await destination.drain()
            tasks = [asyncio.create_task(copy(reader, upstream_writer)), asyncio.create_task(copy(upstream_reader, writer))]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            # Never print request headers, URLs or exception bodies.
            pass
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for stream in (upstream_writer, writer):
                if stream:
                    stream.close()
                    try:
                        await stream.wait_closed()
                    except OSError:
                        pass

    server = await asyncio.start_server(handle, "127.0.0.1", args.port, limit=8192)
    print(f"Fault proxy listening on 127.0.0.1:{args.port}; target={args.host}; failures={args.fail_count}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Exact provider WebSocket hostname")
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--fail-count", type=int, default=1)
    parser.add_argument("--mode", choices=("reject", "timeout"), default="reject")
    parser.add_argument("--delay-sec", type=float, default=15)
    args = parser.parse_args()
    if not allowed(args.host) or not 1 <= args.port <= 65535 or args.fail_count < 0 or args.delay_sec <= 0:
        parser.error("Use an allowed cloud hostname, valid port and nonnegative fault count")
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
