#!/usr/bin/env python3
"""
Ping Claude — Hook Script
Captures Claude Code hook events, classifies them, extracts the last
assistant message from the transcript, and ships a structured JSON
payload to the Ping Claude server over a raw TCP socket.

Dependencies: NONE (stdlib only — this script must never require pip).
Execution budget: <500 ms wall-clock.  Must never block or crash Claude Code.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timezone

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8766
SEND_TIMEOUT = 2          # seconds — bail fast if server is down
TRANSCRIPT_TAIL = 200_000 # read last 200 KB of transcript (enough context, fast)

# debug log — remove once hooks are confirmed working
DEBUG_LOG = os.path.join(os.path.dirname(__file__), "hook_debug.log")


# ---------------------------------------------------------------------------
# stdin
# ---------------------------------------------------------------------------

def read_hook_input() -> dict | None:
    """Read the hook event JSON that Claude Code pipes to stdin."""
    try:
        raw = sys.stdin.read()
        if not raw:
            return None
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError, OSError):
        return None


# ---------------------------------------------------------------------------
# event classification
# ---------------------------------------------------------------------------

def classify(hook: dict) -> str | None:
    """
    Map the raw hook event to one of our event types.
    Returns None for events we don't care about (caller should exit 0).
    """
    name = hook.get("hook_event_name", "")

    if name == "Stop":
        return "task_completed"

    if name == "Notification":
        ntype = hook.get("notification_type", "")
        if ntype in ("idle_prompt", "permission_prompt", "elicitation_dialog"):
            return "input_needed"
        # other notification types (auth_success etc.) — not interesting
        return None

    return None


# ---------------------------------------------------------------------------
# transcript reading
# ---------------------------------------------------------------------------

def _extract_text(content) -> str:
    """Pull human-readable text out of an assistant message's content field."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts)

    return ""


def read_last_assistant_message(transcript_path: str) -> str:
    """
    Read the transcript JSONL and return the text of the last assistant
    message.  Only reads the tail of the file to stay fast on large sessions.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return ""

    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            if size > TRANSCRIPT_TAIL:
                fh.seek(size - TRANSCRIPT_TAIL)
                fh.readline()          # discard partial first line

            lines = fh.readlines()
    except OSError:
        return ""

    # walk backward — first assistant message we hit is the latest
    # transcript lines use an envelope: {"type": "assistant", "message": {"role": "assistant", "content": [...]}}
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("type") != "assistant":
            continue

        msg = entry.get("message", {})
        content = msg.get("content", "")
        text = _extract_text(content)
        if text:
            return text[-3000:]

    return ""


# ---------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------

def build_payload(hook: dict, event_type: str, last_msg: str) -> dict:
    return {
        "event_type": event_type,
        "session_id": hook.get("session_id", ""),
        "cwd": hook.get("cwd", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "transcript_path": hook.get("transcript_path", ""),
        "last_message": last_msg,
        # notification-specific (empty strings for Stop events)
        "notification_message": hook.get("message", ""),
        "notification_title": hook.get("title", ""),
    }


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

def send(payload: dict) -> None:
    """
    Fire-and-forget TCP send to the local Ping Claude server.
    Fails silently — a crashed server must never break Claude Code.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(SEND_TIMEOUT)
    try:
        sock.connect((SERVER_HOST, SERVER_PORT))
        sock.sendall(json.dumps(payload).encode("utf-8"))
    except (ConnectionRefusedError, ConnectionResetError,
            socket.timeout, OSError):
        pass  # server not running — that's fine
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _debug(msg: str) -> None:
    """Append a line to the debug log (best-effort)."""
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()}  {msg}\n")
    except OSError:
        pass


def main() -> None:
    _debug("HOOK INVOKED")

    hook = read_hook_input()
    if hook is None:
        _debug("stdin was empty or invalid JSON")
        sys.exit(0)

    _debug(f"hook_event_name={hook.get('hook_event_name')}  keys={list(hook.keys())}")

    event_type = classify(hook)
    if event_type is None:
        _debug(f"classified as None — skipping")
        sys.exit(0)

    _debug(f"classified as {event_type}")
    last_msg = read_last_assistant_message(hook.get("transcript_path", ""))
    payload = build_payload(hook, event_type, last_msg)
    send(payload)
    _debug(f"sent to server — payload event_type={event_type}")
    sys.exit(0)


if __name__ == "__main__":
    main()
