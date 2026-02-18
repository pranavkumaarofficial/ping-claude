#!/usr/bin/env python3
"""
Ping Claude — Hook Script (Bidirectional)

Handles Stop, PermissionRequest, and Notification hooks from Claude Code.

- Stop: Notifies phone, polls briefly for follow-up text commands from phone.
        If a command arrives, blocks the stop and injects it as context.
- PermissionRequest: Notifies phone, polls for approve/deny from phone.
        Outputs decision JSON so Claude Code auto-approves/denies.
- Notification: Fire and forget — just notifies the phone.

Dependencies: NONE (stdlib only — this script must never require pip).
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from datetime import datetime, timezone

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8766
SEND_TIMEOUT = 3            # per-operation TCP timeout (seconds)
TRANSCRIPT_TAIL = 200_000   # read last 200 KB of transcript

STOP_POLL_SECONDS = 10      # how long Stop hook waits for phone commands
STOP_POLL_INTERVAL = 2      # seconds between polls
PERM_POLL_SECONDS = 110     # how long PermissionRequest waits for phone decision
PERM_POLL_INTERVAL = 2      # seconds between polls

# debug log — remove once everything is stable
DEBUG_LOG = os.path.join(os.path.dirname(__file__), "hook_debug.log")


# ---------------------------------------------------------------------------
# debug
# ---------------------------------------------------------------------------

def _debug(msg: str) -> None:
    """Append a line to the debug log (best-effort)."""
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()}  {msg}\n")
    except OSError:
        pass


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
# transcript reading
# ---------------------------------------------------------------------------

def _extract_text(content) -> str:
    """Pull human-readable text out of an assistant message's content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
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
    # transcript envelope: {"type": "assistant", "message": {"role": "assistant", "content": [...]}}
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
        "notification_message": hook.get("message", ""),
        "notification_title": hook.get("title", ""),
    }


# ---------------------------------------------------------------------------
# TCP comms (bidirectional)
# ---------------------------------------------------------------------------

def send_recv(payload: dict) -> dict | None:
    """Send JSON to server, receive JSON response. Returns None on failure."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(SEND_TIMEOUT)
    try:
        sock.connect((SERVER_HOST, SERVER_PORT))
        sock.sendall(json.dumps(payload).encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)   # signal done sending, keep read open
        # read server response
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        if chunks:
            return json.loads(b"".join(chunks).decode("utf-8"))
    except (ConnectionRefusedError, ConnectionResetError,
            socket.timeout, OSError, json.JSONDecodeError):
        pass                            # server down — never break Claude Code
    finally:
        sock.close()
    return None


def poll_command(session_id: str, command_filter: list[str]) -> dict | None:
    """Poll the server for a pending command matching the filter."""
    resp = send_recv({
        "request": "poll_command",
        "session_id": session_id,
        "command_filter": command_filter,
    })
    if resp and resp.get("command"):
        return resp["command"]
    return None


# ---------------------------------------------------------------------------
# Stop handler
# ---------------------------------------------------------------------------

def handle_stop(hook: dict) -> None:
    """
    Stop hook: notify phone that Claude finished, then briefly poll for
    a follow-up text command.  If one arrives, block the stop so Claude
    processes it.
    """
    # If this stop was triggered by us blocking a previous stop,
    # only do an immediate check (no polling delay).
    is_reentry = hook.get("stop_hook_active", False)

    last_msg = read_last_assistant_message(hook.get("transcript_path", ""))
    payload = build_payload(hook, "task_completed", last_msg)
    payload["request"] = "poll_command"
    payload["command_filter"] = ["phone_voice"]

    resp = send_recv(payload)
    cmd = resp.get("command") if resp else None
    if cmd:
        _block_stop(cmd)
        return

    # On re-entry, don't poll — let Claude stop immediately
    if is_reentry:
        _debug("stop re-entry — no queued command, letting Claude stop")
        return

    # Poll for a short window
    sid = hook.get("session_id", "")
    iterations = STOP_POLL_SECONDS // STOP_POLL_INTERVAL
    for _ in range(iterations):
        time.sleep(STOP_POLL_INTERVAL)
        cmd = poll_command(sid, ["phone_voice"])
        if cmd:
            _block_stop(cmd)
            return

    _debug("stop poll timed out — no phone command")


def _block_stop(cmd: dict) -> None:
    """Output JSON that blocks Claude from stopping, injecting the phone command."""
    text = cmd.get("text", "")
    _debug(f"BLOCKING STOP — phone command: {text[:80]}")
    result = {
        "decision": "block",
        "reason": f"User replied from phone: {text}",
    }
    print(json.dumps(result))


# ---------------------------------------------------------------------------
# PermissionRequest handler
# ---------------------------------------------------------------------------

def handle_permission_request(hook: dict) -> None:
    """
    PermissionRequest hook: notify phone about the tool permission prompt,
    then poll for approve/deny.  Output decision JSON for Claude Code.
    """
    last_msg = read_last_assistant_message(hook.get("transcript_path", ""))

    payload = build_payload(hook, "permission_request", last_msg)
    # include tool info so the phone can show what's being requested
    payload["tool_name"] = hook.get("tool_name", "")
    payload["tool_input"] = _truncate(hook.get("tool_input", {}), 500)
    payload["request"] = "poll_command"
    payload["command_filter"] = ["phone_approve", "phone_deny"]

    resp = send_recv(payload)
    cmd = resp.get("command") if resp else None
    if cmd:
        _output_permission_decision(cmd)
        return

    # Poll until phone responds or timeout
    sid = hook.get("session_id", "")
    iterations = PERM_POLL_SECONDS // PERM_POLL_INTERVAL
    for _ in range(iterations):
        time.sleep(PERM_POLL_INTERVAL)
        cmd = poll_command(sid, ["phone_approve", "phone_deny"])
        if cmd:
            _output_permission_decision(cmd)
            return

    _debug("permission poll timed out — no phone response")


def _output_permission_decision(cmd: dict) -> None:
    """Output permission decision JSON for Claude Code to consume."""
    source = cmd.get("source", "")
    behavior = "allow" if source == "phone_approve" else "deny"
    _debug(f"permission decision: {behavior}")
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {
                "behavior": behavior,
            },
        },
    }
    print(json.dumps(result))


def _truncate(obj, max_len: int) -> str:
    """JSON-serialize and truncate for phone display."""
    if isinstance(obj, dict):
        s = json.dumps(obj)
    else:
        s = str(obj)
    return s[:max_len]


# ---------------------------------------------------------------------------
# Notification handler
# ---------------------------------------------------------------------------

def handle_notification(hook: dict) -> None:
    """Notification hook: fire and forget — just tell the phone."""
    last_msg = read_last_assistant_message(hook.get("transcript_path", ""))
    payload = build_payload(hook, "input_needed", last_msg)
    payload["request"] = "notify"
    send_recv(payload)
    _debug("notification sent")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    _debug("HOOK INVOKED")

    hook = read_hook_input()
    if hook is None:
        _debug("stdin was empty or invalid JSON")
        sys.exit(0)

    name = hook.get("hook_event_name", "")
    _debug(f"hook_event_name={name}  keys={list(hook.keys())}")

    if name == "Stop":
        handle_stop(hook)

    elif name == "PermissionRequest":
        handle_permission_request(hook)

    elif name == "Notification":
        ntype = hook.get("notification_type", "")
        if ntype in ("idle_prompt", "elicitation_dialog"):
            handle_notification(hook)
        else:
            _debug(f"notification type '{ntype}' — ignoring")

    else:
        _debug(f"unknown hook event: {name}")

    sys.exit(0)


if __name__ == "__main__":
    main()
