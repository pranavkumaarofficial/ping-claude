#!/usr/bin/env python3
"""
Simulates an Android phone connecting to the Ping Claude server.

Usage:
  python test_phone_sim.py                 # connect to localhost
  python test_phone_sim.py 100.x.x.x      # connect via Tailscale IP

Interactive commands:
  status             -- query current Claude state
  target <id>        -- target a session (prefix match), or 'target' to clear
  approve            -- send approval (to targeted session)
  deny               -- send denial (to targeted session)
  say <text>         -- send a text command (to targeted session)
  history            -- request recent event history
  quit               -- disconnect
"""

import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("ERROR: pip install websockets", file=sys.stderr)
    sys.exit(1)

target_session = ""


async def receiver(ws):
    try:
        async for raw in ws:
            data = json.loads(raw)
            msg_type = data.get("type", data.get("event_type", "unknown"))
            print(f"\n{'='*60}")
            print(f"  RECEIVED: {msg_type}")
            print(f"{'='*60}")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print(f"{'='*60}")
            print(f"\n[target: {target_session[:12] or 'any'}] > ", end="", flush=True)
    except websockets.ConnectionClosed as exc:
        print(f"\n  Connection closed: {exc}")


async def sender(ws):
    global target_session
    loop = asyncio.get_event_loop()

    while True:
        print(f"[target: {target_session[:12] or 'any'}] > ", end="", flush=True)
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

        if line.startswith("target"):
            parts = line.split(maxsplit=1)
            if len(parts) > 1:
                prefix = parts[1].strip()
                # request sessions to find full ID from prefix
                await ws.send(json.dumps({"type": "status_query"}))
                # wait briefly for response
                await asyncio.sleep(0.5)
                target_session = prefix
                print(f"  -> targeting session: {prefix}")
            else:
                target_session = ""
                print("  -> targeting: any session")
            continue

        if line == "approve":
            msg = {"type": "approve"}
            if target_session:
                msg["session_id"] = target_session
            await ws.send(json.dumps(msg))
            print(f"  -> sent APPROVE (session: {target_session[:12] or 'any'})")
            continue

        if line == "deny":
            msg = {"type": "deny"}
            if target_session:
                msg["session_id"] = target_session
            await ws.send(json.dumps(msg))
            print(f"  -> sent DENY (session: {target_session[:12] or 'any'})")
            continue

        if line == "history":
            await ws.send(json.dumps({"type": "history"}))
            continue

        if line.startswith("say "):
            text = line[4:].strip()
            if text:
                msg = {"type": "command", "text": text}
                if target_session:
                    msg["session_id"] = target_session
                await ws.send(json.dumps(msg))
                print(f"  -> sent COMMAND (session: {target_session[:12] or 'any'}): {text}")
            else:
                print("  usage: say <your message>")
            continue

        print(f"  Unknown command: {line}")
        print("  Available: status | target <id> | approve | deny | say <text> | history | quit")


async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    uri = f"ws://{host}:8765"

    print(f"\nConnecting to {uri} ...")

    try:
        async with websockets.connect(uri) as ws:
            print(f"Connected!\n")
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
