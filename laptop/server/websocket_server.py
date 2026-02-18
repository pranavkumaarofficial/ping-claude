#!/usr/bin/env python3
"""
Ping Claude — WebSocket Server (Bidirectional)
Sits between the Claude Code hook and connected Android phones.

Architecture:
  TCP  :8766  ← companion_hook.py sends events AND polls for phone commands
  WS   :8765  → Android phones connect here (Tailscale / localhost)

The server:
  1. Ingests hook events, updates internal Claude state, broadcasts to phones.
  2. Accepts commands FROM phones (approve / deny / text / status query).
  3. Serves pending phone commands BACK to the hook script (bidirectional TCP).
  4. Validates that WS connections come from Tailscale or localhost.

Hook protocol (TCP):
  Hook sends JSON → server responds with JSON.
  Request may include "event_type" (new event) and/or "request":"poll_command".
  Response always includes "status":"ok" and optionally "command":{...}.

Dependencies: websockets  (pip install websockets)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    import websockets
    from websockets.asyncio.server import serve, ServerConnection
except ImportError:
    print("ERROR: 'websockets' package not found.  Run:  pip install websockets", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

HOOK_PORT = 8766
WS_PORT   = 8765
ALLOWED_IP_PREFIXES = ("100.", "127.", "::1", "fd7a:", "10.", "192.168.")
MAX_EVENT_HISTORY = 50      # ring-buffer of recent events for late-joining phones

# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pingclaude")

# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

@dataclass
class SessionState:
    session_id: str
    status: str = "working"               # working | idle | waiting_for_input
    last_event_type: str = ""
    last_message: str = ""
    cwd: str = ""
    last_event_time: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# keyed by session_id — each Claude Code terminal gets its own entry
sessions: dict[str, SessionState] = {}
event_history: list[dict] = []           # ring-buffer (all sessions combined)
connected_phones: set[ServerConnection] = set()
pending_commands: list[dict] = []        # commands from phone, waiting to be consumed


def get_or_create_session(session_id: str) -> SessionState:
    if session_id not in sessions:
        sessions[session_id] = SessionState(session_id=session_id)
        log.info(f"  NEW SESSION {session_id[:12]}  (total active: {len(sessions)})")
    return sessions[session_id]


def all_sessions_summary() -> dict:
    """Summary for phone UI: how many sessions, their states."""
    return {
        "total": len(sessions),
        "sessions": [s.to_dict() for s in sessions.values()],
        "waiting": sum(1 for s in sessions.values() if s.status == "waiting_for_input"),
        "working": sum(1 for s in sessions.values() if s.status == "working"),
        "idle": sum(1 for s in sessions.values() if s.status == "idle"),
    }


def consume_pending_command(source_filter: list[str]) -> dict | None:
    """Pop and return the first pending command matching the source filter.
    Empty filter matches any command."""
    if not source_filter:
        if pending_commands:
            return pending_commands.pop(0)
        return None
    for i, cmd in enumerate(pending_commands):
        if cmd["source"] in source_filter:
            return pending_commands.pop(i)
    return None

# ---------------------------------------------------------------------------
# IP validation
# ---------------------------------------------------------------------------

def _peer_ip(ws: ServerConnection) -> str:
    """Extract the IP string from a websocket connection."""
    addr = ws.remote_address
    if isinstance(addr, tuple):
        return str(addr[0])
    return str(addr) if addr else "unknown"


def is_allowed(ws: ServerConnection) -> bool:
    ip = _peer_ip(ws)
    return any(ip.startswith(p) for p in ALLOWED_IP_PREFIXES)

# ---------------------------------------------------------------------------
# broadcast
# ---------------------------------------------------------------------------

async def broadcast(event: dict) -> None:
    if not connected_phones:
        log.info("  (no phones connected)")
        return

    payload = json.dumps(event)
    gone: set[ServerConnection] = set()

    for ws in connected_phones:
        try:
            await ws.send(payload)
        except websockets.ConnectionClosed:
            gone.add(ws)

    connected_phones.difference_update(gone)
    if gone:
        log.info(f"  pruned {len(gone)} dead connection(s)")

    log.info(f"  → broadcast to {len(connected_phones)} phone(s)")

# ---------------------------------------------------------------------------
# hook ingestion  (raw TCP on :8766)
# ---------------------------------------------------------------------------

async def handle_hook(reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
    """Bidirectional hook handler: receive event, optionally serve pending commands."""
    try:
        data = await asyncio.wait_for(reader.read(1 << 16), timeout=5.0)
        if not data:
            return

        msg = json.loads(data.decode("utf-8"))
        request_type = msg.get("request", "notify")
        response: dict = {"status": "ok"}

        # --- process event if present ---
        etype = msg.get("event_type")
        if etype:
            sid   = msg.get("session_id", "")
            cwd   = msg.get("cwd", "")
            project = Path(cwd).name if cwd else "unknown"
            log.info(f"HOOK  {etype}  session={sid[:12]}  project={project}")

            session = get_or_create_session(sid)
            session.last_event_type = etype
            session.last_message    = msg.get("last_message", "")
            session.cwd             = cwd
            session.last_event_time = msg.get("timestamp", "")

            if etype == "task_completed":
                session.status = "idle"
            elif etype in ("input_needed", "permission_request"):
                session.status = "waiting_for_input"

            # build broadcast event (shallow copy so we don't mutate msg)
            event = dict(msg)
            event["project"] = project
            event["active_sessions"] = all_sessions_summary()

            event_history.append(event)
            if len(event_history) > MAX_EVENT_HISTORY:
                event_history.pop(0)

            await broadcast(event)

        # --- serve pending command if requested ---
        if request_type == "poll_command":
            cmd_filter = msg.get("command_filter", [])
            cmd = consume_pending_command(cmd_filter)
            response["command"] = cmd
            if cmd:
                log.info(f"  → delivered command to hook: {cmd['source']} = {cmd['text'][:40]}")

        # --- send response ---
        writer.write(json.dumps(response).encode("utf-8"))
        await writer.drain()

    except asyncio.TimeoutError:
        log.warning("hook connection timed out")
    except json.JSONDecodeError as exc:
        log.warning(f"bad JSON from hook: {exc}")
    except Exception as exc:
        log.error(f"hook error: {exc}", exc_info=True)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# phone commands
# ---------------------------------------------------------------------------

async def handle_phone_message(data: dict, ws: ServerConnection) -> None:
    msg_type = data.get("type", "")

    if msg_type == "status_query":
        await ws.send(json.dumps({
            "type": "status_response",
            "sessions": all_sessions_summary(),
            "pending_commands": len(pending_commands),
        }))
        log.info(f"  ← status query from {_peer_ip(ws)}")
        return

    if msg_type == "approve":
        cmd = {"text": "y", "source": "phone_approve",
               "timestamp": _now()}
        pending_commands.append(cmd)
        await ws.send(json.dumps({"type": "command_ack", "text": "approve", "status": "queued"}))
        log.info(f"  ← APPROVE from {_peer_ip(ws)}")
        return

    if msg_type == "deny":
        cmd = {"text": "n", "source": "phone_deny",
               "timestamp": _now()}
        pending_commands.append(cmd)
        await ws.send(json.dumps({"type": "command_ack", "text": "deny", "status": "queued"}))
        log.info(f"  ← DENY from {_peer_ip(ws)}")
        return

    if msg_type == "command":
        text = data.get("text", "").strip()
        if not text:
            await ws.send(json.dumps({"type": "error", "message": "empty command"}))
            return
        cmd = {"text": text, "source": "phone_voice",
               "timestamp": _now()}
        pending_commands.append(cmd)
        await ws.send(json.dumps({"type": "command_ack", "text": text, "status": "queued"}))
        log.info(f"  ← COMMAND from {_peer_ip(ws)}: {text[:80]}")
        return

    if msg_type == "history":
        await ws.send(json.dumps({"type": "history_response", "events": event_history[-10:]}))
        return

    log.warning(f"  unknown message type from phone: {msg_type}")


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# phone WebSocket handler  (:8765)
# ---------------------------------------------------------------------------

async def handle_phone(ws: ServerConnection) -> None:
    ip = _peer_ip(ws)

    if not is_allowed(ws):
        log.warning(f"REJECTED  {ip}  (not on allowed network)")
        await ws.close(4003, "Forbidden")
        return

    connected_phones.add(ws)
    log.info(f"PHONE CONNECTED  {ip}  (total: {len(connected_phones)})")

    # send current state immediately so the phone UI can render
    await ws.send(json.dumps({
        "type": "state_sync",
        "sessions": all_sessions_summary(),
        "recent_events": event_history[-5:],
    }))

    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
                await handle_phone_message(data, ws)
            except json.JSONDecodeError:
                log.warning(f"bad JSON from phone {ip}")
    except websockets.ConnectionClosed:
        pass
    finally:
        connected_phones.discard(ws)
        log.info(f"PHONE DISCONNECTED  {ip}  (total: {len(connected_phones)})")

# ---------------------------------------------------------------------------
# tailscale detection
# ---------------------------------------------------------------------------

async def detect_tailscale() -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "tailscale", "ip", "-4",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        ip = stdout.decode().strip().split("\n")[0].strip()
        if ip.startswith("100."):
            return ip
    except FileNotFoundError:
        pass
    return None

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    # 1. TCP listener for hook events
    hook_server = await asyncio.start_server(handle_hook, "127.0.0.1", HOOK_PORT)
    log.info(f"Hook listener ........ tcp://127.0.0.1:{HOOK_PORT}")

    # 2. WebSocket listener for phones
    ws_server = await serve(handle_phone, "0.0.0.0", WS_PORT)
    log.info(f"Phone WebSocket ...... ws://0.0.0.0:{WS_PORT}")

    # 3. show Tailscale info
    ts_ip = await detect_tailscale()
    if ts_ip:
        log.info(f"Tailscale IP ......... {ts_ip}")
        log.info(f"Phone connects to ... ws://{ts_ip}:{WS_PORT}")
    else:
        log.warning("Tailscale not detected — phone connections limited to local network")

    log.info("")
    log.info("Ping Claude server is LIVE.  Waiting for events...")
    log.info("Press Ctrl+C to stop.\n")

    # keep running until killed
    try:
        await hook_server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        ws_server.close()
        await ws_server.wait_closed()
        hook_server.close()
        await hook_server.wait_closed()
        log.info("Server shut down.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("\nShutting down...")
