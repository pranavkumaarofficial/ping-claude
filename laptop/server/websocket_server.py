#!/usr/bin/env python3
"""
Ping Claude -- WebSocket Server (Bidirectional)

TCP  :8766  <- companion_hook.py sends events AND polls for phone commands
WS   :8765  -> Android phones connect here (Tailscale / localhost)

Hook protocol (TCP):
  Hook sends JSON -> server responds with JSON.
  Request may include "event_type" (new event) and/or "request":"poll_command".
  Response always includes "status":"ok" and optionally "command":{...}.
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

HOOK_PORT = 8766
WS_PORT   = 8765
ALLOWED_IP_PREFIXES = ("100.", "127.", "::1", "fd7a:", "10.", "192.168.")
MAX_EVENT_HISTORY = 50
ACTIVITY_EVENTS = frozenset({"tool_start", "tool_end"})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pingclaude")


@dataclass
class SessionState:
    session_id: str
    status: str = "working"
    last_event_type: str = ""
    last_message: str = ""
    cwd: str = ""
    last_event_time: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


sessions: dict[str, SessionState] = {}
event_history: list[dict] = []
connected_phones: set[ServerConnection] = set()
pending_commands: list[dict] = []
event_listeners: list = []
polling_sessions: dict[str, float] = {}  # session_id -> last poll timestamp


def get_or_create_session(session_id: str) -> SessionState:
    if session_id not in sessions:
        sessions[session_id] = SessionState(session_id=session_id)
        log.info(f"  NEW SESSION {session_id[:12]}  (total active: {len(sessions)})")
    return sessions[session_id]


def all_sessions_summary() -> dict:
    now = time.time()
    session_list = []
    for s in sessions.values():
        d = s.to_dict()
        d["polling"] = is_session_polling(s.session_id)
        session_list.append(d)
    return {
        "total": len(sessions),
        "sessions": session_list,
        "waiting": sum(1 for s in sessions.values() if s.status == "waiting_for_input"),
        "working": sum(1 for s in sessions.values() if s.status == "working"),
        "idle": sum(1 for s in sessions.values() if s.status == "idle"),
    }


POLL_ALIVE_THRESHOLD = 10  # seconds -- hook polls every 2s, so 10s means definitely dead
DEAD_SESSION_TTL = 300     # seconds -- auto-prune sessions dead longer than this


def is_session_polling(session_id: str) -> bool:
    if not session_id:
        return False
    for sid, ts in polling_sessions.items():
        if _session_match(sid, session_id) and (time.time() - ts) < POLL_ALIVE_THRESHOLD:
            return True
    return False


def _session_match(a: str, b: str) -> bool:
    if not a or not b:
        return True
    return a.startswith(b) or b.startswith(a)


def prune_dead_sessions(force: bool = False) -> int:
    """Remove sessions that are no longer polling.

    force=True removes all dead sessions immediately.
    force=False only removes sessions dead longer than DEAD_SESSION_TTL.
    """
    now = time.time()
    dead_sids = []
    for sid in list(sessions.keys()):
        if is_session_polling(sid):
            continue
        if force:
            dead_sids.append(sid)
        else:
            last_poll = polling_sessions.get(sid, 0)
            if last_poll and (now - last_poll) > DEAD_SESSION_TTL:
                dead_sids.append(sid)
    for sid in dead_sids:
        del sessions[sid]
        polling_sessions.pop(sid, None)
    if dead_sids:
        log.info(f"  pruned {len(dead_sids)} dead session(s)")
    return len(dead_sids)


async def _prune_loop() -> None:
    """Periodically remove stale dead sessions."""
    while True:
        await asyncio.sleep(120)
        prune_dead_sessions()


def consume_pending_command(source_filter: list[str], session_id: str = "") -> dict | None:
    for i, cmd in enumerate(pending_commands):
        cmd_sid = cmd.get("session_id", "")
        if cmd_sid and session_id and not _session_match(cmd_sid, session_id):
            continue
        # source filter
        if source_filter and cmd["source"] not in source_filter:
            continue
        return pending_commands.pop(i)
    return None


def _peer_ip(ws: ServerConnection) -> str:
    addr = ws.remote_address
    if isinstance(addr, tuple):
        return str(addr[0])
    return str(addr) if addr else "unknown"


def is_allowed(ws: ServerConnection) -> bool:
    ip = _peer_ip(ws)
    return any(ip.startswith(p) for p in ALLOWED_IP_PREFIXES)


async def broadcast(event: dict) -> None:
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

    if connected_phones:
        log.info(f"  -> broadcast to {len(connected_phones)} phone(s)")

    for listener in event_listeners:
        try:
            await listener(event)
        except Exception as exc:
            log.warning(f"  event listener error: {exc}")


async def handle_hook(reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
    try:
        data = await asyncio.wait_for(reader.read(1 << 16), timeout=5.0)
        if not data:
            return

        msg = json.loads(data.decode("utf-8"))
        request_type = msg.get("request", "notify")
        response: dict = {"status": "ok"}

        etype = msg.get("event_type")
        if etype:
            sid   = msg.get("session_id", "")
            cwd   = msg.get("cwd", "")
            project = Path(cwd).name if cwd else "unknown"
            is_activity = etype in ACTIVITY_EVENTS

            session = get_or_create_session(sid)
            session.last_event_type = etype
            session.cwd             = cwd
            session.last_event_time = msg.get("timestamp", "")

            if is_activity:
                session.status = "working"
            else:
                log.info(f"HOOK  {etype}  session={sid[:12]}  project={project}")
                session.last_message = msg.get("last_message", "")
                if etype == "task_completed":
                    session.status = "idle"
                elif etype in ("input_needed", "permission_request"):
                    session.status = "waiting_for_input"

            event = dict(msg)
            event["project"] = project

            if not is_activity:
                event["active_sessions"] = all_sessions_summary()
                event_history.append(event)
                if len(event_history) > MAX_EVENT_HISTORY:
                    event_history.pop(0)

            await broadcast(event)

        if request_type == "poll_command":
            cmd_filter = msg.get("command_filter", [])
            poll_sid = msg.get("session_id", "")
            if poll_sid:
                polling_sessions[poll_sid] = time.time()
            cmd = consume_pending_command(cmd_filter, poll_sid)
            response["command"] = cmd
            if cmd:
                log.info(f"  -> delivered command to hook: {cmd['source']} = {cmd['text'][:40]}")

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


async def _transcribe_voice(audio_b64: str) -> str:
    """Transcribe base64-encoded audio via Groq Whisper API."""
    import base64
    config_path = Path.home() / ".claude" / "ping-claude.json"
    if not config_path.exists():
        raise RuntimeError("No config file found")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    api_key = config.get("groq_api_key", "")
    if not api_key:
        raise RuntimeError("No Groq API key configured")

    audio_bytes = base64.b64decode(audio_b64)
    try:
        import aiohttp
    except ImportError:
        raise RuntimeError("aiohttp not installed")

    form = aiohttp.FormData()
    form.add_field("file", audio_bytes, filename="voice.webm", content_type="audio/webm")
    form.add_field("model", "whisper-large-v3")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data=form,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.warning(f"Groq transcription failed: {resp.status} {body[:200]}")
                return ""
            result = await resp.json()
            return result.get("text", "").strip()


async def handle_phone_message(data: dict, ws: ServerConnection) -> None:
    msg_type = data.get("type", "")

    if msg_type == "status_query":
        await ws.send(json.dumps({
            "type": "status_response",
            "sessions": all_sessions_summary(),
            "pending_commands": len(pending_commands),
        }))
        log.info(f"  <- status query from {_peer_ip(ws)}")
        return

    sid = data.get("session_id", "")

    if msg_type == "approve":
        cmd = {"text": "y", "source": "phone_approve",
               "session_id": sid, "timestamp": _now()}
        pending_commands.append(cmd)
        await ws.send(json.dumps({"type": "command_ack", "text": "approve", "status": "queued"}))
        log.info(f"  <- APPROVE from {_peer_ip(ws)}  session={sid[:12] or 'any'}")
        return

    if msg_type == "deny":
        cmd = {"text": "n", "source": "phone_deny",
               "session_id": sid, "timestamp": _now()}
        pending_commands.append(cmd)
        await ws.send(json.dumps({"type": "command_ack", "text": "deny", "status": "queued"}))
        log.info(f"  <- DENY from {_peer_ip(ws)}  session={sid[:12] or 'any'}")
        return

    if msg_type == "command":
        text = data.get("text", "").strip()
        if not text:
            await ws.send(json.dumps({"type": "error", "message": "empty command"}))
            return
        cmd = {"text": text, "source": "phone_voice",
               "session_id": sid, "timestamp": _now()}
        pending_commands.append(cmd)
        await ws.send(json.dumps({"type": "command_ack", "text": text, "status": "queued", "session_id": sid}))
        log.info(f"  <- COMMAND from {_peer_ip(ws)}  session={sid[:12] or 'any'}: {text[:80]}")
        return

    if msg_type == "history":
        await ws.send(json.dumps({"type": "history_response", "events": event_history[-10:]}))
        return

    if msg_type == "end_session":
        if not sid:
            await ws.send(json.dumps({"type": "error", "message": "session_id required"}))
            return
        cmd = {"text": "/end", "source": "phone_terminate",
               "session_id": sid, "timestamp": _now()}
        pending_commands.append(cmd)
        await ws.send(json.dumps({"type": "command_ack", "text": "end_session", "status": "queued", "session_id": sid}))
        log.info(f"  <- END SESSION from {_peer_ip(ws)}  session={sid[:12]}")
        return

    if msg_type == "end_all":
        for s in list(sessions.values()):
            cmd = {"text": "/end", "source": "phone_terminate",
                   "session_id": s.session_id, "timestamp": _now()}
            pending_commands.append(cmd)
        await ws.send(json.dumps({"type": "command_ack", "text": "end_all", "status": "queued"}))
        log.info(f"  <- END ALL from {_peer_ip(ws)}  ({len(sessions)} sessions)")
        return

    if msg_type == "image":
        image_b64 = data.get("image", "")
        caption = data.get("text", "").strip()
        if not image_b64:
            await ws.send(json.dumps({"type": "error", "message": "no image data"}))
            return
        import base64, tempfile
        img_bytes = base64.b64decode(image_b64)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", prefix="ping-claude-", delete=False)
        tmp.write(img_bytes)
        tmp.close()
        img_path = tmp.name.replace("\\", "/")
        text = f"Here is an image I'm sharing: {img_path}"
        if caption:
            text += f"\n{caption}"
        cmd = {"text": text, "source": "phone_image",
               "session_id": sid, "timestamp": _now()}
        pending_commands.append(cmd)
        await ws.send(json.dumps({"type": "command_ack", "text": f"Image saved & sent", "status": "queued", "session_id": sid}))
        log.info(f"  <- IMAGE from {_peer_ip(ws)}  saved to {img_path}")
        return

    if msg_type == "voice":
        audio_b64 = data.get("audio", "")
        if not audio_b64:
            await ws.send(json.dumps({"type": "error", "message": "no audio data"}))
            return
        log.info(f"  <- VOICE from {_peer_ip(ws)}  ({len(audio_b64) // 1024}KB b64)")
        try:
            text = await _transcribe_voice(audio_b64)
        except Exception as e:
            log.warning(f"Voice transcription failed: {e}")
            await ws.send(json.dumps({"type": "error", "message": "Transcription failed"}))
            return
        if not text:
            await ws.send(json.dumps({"type": "error", "message": "Could not transcribe audio"}))
            return
        cmd = {"text": text, "source": "phone_voice",
               "session_id": sid, "timestamp": _now()}
        pending_commands.append(cmd)
        await ws.send(json.dumps({"type": "command_ack", "text": text, "status": "queued", "session_id": sid}))
        log.info(f"  <- VOICE transcribed: {text[:80]}")
        return

    if msg_type == "clear_dead":
        count = prune_dead_sessions(force=True)
        await ws.send(json.dumps({
            "type": "command_ack", "text": f"cleared {count} dead session(s)", "status": "ok",
        }))
        # Send updated status
        await ws.send(json.dumps({
            "type": "status_response",
            "sessions": all_sessions_summary(),
            "pending_commands": len(pending_commands),
        }))
        log.info(f"  <- CLEAR DEAD from {_peer_ip(ws)}  (removed {count})")
        return

    log.warning(f"  unknown message type from phone: {msg_type}")


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def handle_phone(ws: ServerConnection) -> None:
    ip = _peer_ip(ws)

    if not is_allowed(ws):
        log.warning(f"REJECTED  {ip}  (not on allowed network)")
        await ws.close(4003, "Forbidden")
        return

    connected_phones.add(ws)
    log.info(f"PHONE CONNECTED  {ip}  (total: {len(connected_phones)})")

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


async def main(enable_telegram: bool = False, enable_webapp: bool = False) -> None:
    hook_server = await asyncio.start_server(handle_hook, "127.0.0.1", HOOK_PORT)
    log.info(f"Hook listener ........ tcp://127.0.0.1:{HOOK_PORT}")

    ws_server = await serve(handle_phone, "0.0.0.0", WS_PORT)
    log.info(f"Phone WebSocket ...... ws://0.0.0.0:{WS_PORT}")

    webapp_runner = None
    if enable_webapp:
        try:
            from laptop.server.webapp_server import start_webapp_server
            webapp_runner = await start_webapp_server()
        except ImportError:
            log.warning("Webapp: aiohttp not installed. Run: pip install aiohttp")

    telegram_bridge = None
    if enable_telegram:
        try:
            from laptop.server.telegram_bot import TelegramBridge, load_config
            config = load_config()
            bot_token = config.get("telegram", {}).get("bot_token", "")
            if bot_token:
                telegram_bridge = TelegramBridge(bot_token, pending_commands.append)
                await telegram_bridge.start()
                event_listeners.append(telegram_bridge.on_event)
            else:
                log.warning("Telegram: no bot token configured. Run: ping-claude telegram --token <TOKEN>")
        except ImportError:
            log.warning("Telegram: python-telegram-bot not installed. Run: pip install python-telegram-bot")

    ts_ip = await detect_tailscale()
    if ts_ip:
        log.info(f"Tailscale IP ......... {ts_ip}")
        log.info(f"Phone connects to ... ws://{ts_ip}:{WS_PORT}")
        if webapp_runner:
            log.info(f"Webapp URL ........... http://{ts_ip}:8767")
    else:
        log.warning("Tailscale not detected -- phone connections limited to local network")
        if webapp_runner:
            log.info(f"Webapp URL ........... http://localhost:8767")

    asyncio.create_task(_prune_loop())

    log.info("")
    log.info("Ping Claude server is LIVE.  Waiting for events...")
    log.info("Press Ctrl+C to stop.\n")

    try:
        await hook_server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        if telegram_bridge:
            await telegram_bridge.stop()
        if webapp_runner:
            await webapp_runner.cleanup()
        ws_server.close()
        await ws_server.wait_closed()
        hook_server.close()
        await hook_server.wait_closed()
        log.info("Server shut down.")


if __name__ == "__main__":
    enable_tg = "--telegram" in sys.argv
    enable_wa = "--webapp" in sys.argv
    try:
        asyncio.run(main(enable_telegram=enable_tg, enable_webapp=enable_wa))
    except KeyboardInterrupt:
        log.info("\nShutting down...")
