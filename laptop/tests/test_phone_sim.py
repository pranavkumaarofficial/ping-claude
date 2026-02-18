#!/usr/bin/env python3
"""
Simulates an Android phone connecting to the Ping Claude server.
Connects via WebSocket, prints received events, and lets you
send commands interactively.

Usage:
  python test_phone_sim.py                 # connect to localhost
  python test_phone_sim.py 100.x.x.x      # connect via Tailscale IP

Interactive commands (type and press Enter):
  status      — query current Claude state
  approve     — send approval
  deny        — send denial
  history     — request recent event history
  say <text>  — send a voice command
  quit        — disconnect
"""

import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("ERROR: pip install websockets", file=sys.stderr)
    sys.exit(1)


async def receiver(ws):
    """Print every message from the server."""
    try:
        async for raw in ws:
            data = json.loads(raw)
            msg_type = data.get("type", data.get("event_type", "unknown"))
            print(f"\n{'='*60}")
            print(f"  RECEIVED: {msg_type}")
            print(f"{'='*60}")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print(f"{'='*60}")
            print("\n> ", end="", flush=True)
    except websockets.ConnectionClosed as exc:
        print(f"\n  Connection closed: {exc}")


async def sender(ws):
    """Read commands from stdin and send to server."""
    loop = asyncio.get_event_loop()

    while True:
        print("> ", end="", flush=True)
        line = await loop.run_in_executor(None, sys.stdin.readline)
        line = line.strip()

        if not line:
            continue

        if line == "quit":
            await ws.close()
            return

        if line == "status":
            await ws.send(json.dumps({"type": "status_query"}))
            continue

        if line == "approve":
            await ws.send(json.dumps({"type": "approve"}))
            print("  → sent APPROVE")
            continue

        if line == "deny":
            await ws.send(json.dumps({"type": "deny"}))
            print("  → sent DENY")
            continue

        if line == "history":
            await ws.send(json.dumps({"type": "history"}))
            continue

        if line.startswith("say "):
            text = line[4:].strip()
            if text:
                await ws.send(json.dumps({"type": "command", "text": text}))
                print(f"  → sent COMMAND: {text}")
            else:
                print("  usage: say <your message>")
            continue

        print(f"  Unknown command: {line}")
        print("  Available: status | approve | deny | history | say <text> | quit")


async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    uri = f"ws://{host}:8765"

    print(f"\nConnecting to {uri} ...")

    try:
        async with websockets.connect(uri) as ws:
            print(f"Connected!\n")
            # run receiver and sender concurrently
            recv_task = asyncio.create_task(receiver(ws))
            send_task = asyncio.create_task(sender(ws))
            done, pending = await asyncio.wait(
                [recv_task, send_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
    except ConnectionRefusedError:
        print(f"\nERROR: Could not connect to {uri}")
        print("Is the Ping Claude server running?")
        print("  python laptop/server/websocket_server.py\n")
        sys.exit(1)
    except Exception as exc:
        print(f"\nERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDisconnected.")
