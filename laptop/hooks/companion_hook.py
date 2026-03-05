#!/usr/bin/env python3
"""
Ping Claude -- Hook Script (Bidirectional)

Handles Stop, PermissionRequest, and Notification hooks from Claude Code.
Dependencies: NONE (stdlib only).
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
SEND_TIMEOUT = 3
TRANSCRIPT_TAIL = 200_000

STOP_POLL_SECONDS = 10
STOP_POLL_INTERVAL = 2
PERM_POLL_SECONDS = 110
PERM_POLL_INTERVAL = 2

DEBUG_LOG = os.path.join(os.path.dirname(__file__), "hook_debug.log")


def _debug(msg: str) -> None:
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()}  {msg}\n")
    except OSError:
        pass


def read_hook_input() -> dict | None:
    try:
        raw = sys.stdin.read()
        if not raw:
            return None
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def _extract_text(content) -> str:
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
    if not transcript_path or not os.path.isfile(transcript_path):
        return ""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            if size > TRANSCRIPT_TAIL:
                fh.seek(size - TRANSCRIPT_TAIL)
                fh.readline()
            lines = fh.readlines()
    except OSError:
        return ""

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


def send_recv(payload: dict) -> dict | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(SEND_TIMEOUT)
    try:
        sock.connect((SERVER_HOST, SERVER_PORT))
        sock.sendall(json.dumps(payload).encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
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
        pass
    finally:
        sock.close()
    return None


def poll_command(session_id: str, command_filter: list[str]) -> dict | None:
    resp = send_recv({
        "request": "poll_command",
        "session_id": session_id,
        "command_filter": command_filter,
    })
    if resp and resp.get("command"):
        return resp["command"]
    return None


def handle_stop(hook: dict) -> None:
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

    if is_reentry:
        _debug("stop re-entry -- no queued command, letting Claude stop")
        return

    sid = hook.get("session_id", "")
    iterations = STOP_POLL_SECONDS // STOP_POLL_INTERVAL
    for _ in range(iterations):
        time.sleep(STOP_POLL_INTERVAL)
        cmd = poll_command(sid, ["phone_voice"])
        if cmd:
            _block_stop(cmd)
            return

    _debug("stop poll timed out -- no phone command")


def _block_stop(cmd: dict) -> None:
    text = cmd.get("text", "")
    _debug(f"BLOCKING STOP -- phone command: {text[:80]}")
    result = {
        "decision": "block",
        "reason": f"User replied from phone: {text}",
    }
    print(json.dumps(result))


def handle_permission_request(hook: dict) -> None:
    last_msg = read_last_assistant_message(hook.get("transcript_path", ""))

    payload = build_payload(hook, "permission_request", last_msg)
    payload["tool_name"] = hook.get("tool_name", "")
    payload["tool_input"] = _truncate(hook.get("tool_input", {}), 500)
    payload["request"] = "poll_command"
    payload["command_filter"] = ["phone_approve", "phone_deny"]

    resp = send_recv(payload)
    cmd = resp.get("command") if resp else None
    if cmd:
        _output_permission_decision(cmd)
        return

    sid = hook.get("session_id", "")
    iterations = PERM_POLL_SECONDS // PERM_POLL_INTERVAL
    for _ in range(iterations):
        time.sleep(PERM_POLL_INTERVAL)
        cmd = poll_command(sid, ["phone_approve", "phone_deny"])
        if cmd:
            _output_permission_decision(cmd)
            return

    _debug("permission poll timed out -- no phone response")


def _output_permission_decision(cmd: dict) -> None:
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
    if isinstance(obj, dict):
        s = json.dumps(obj)
    else:
        s = str(obj)
    return s[:max_len]


def handle_notification(hook: dict) -> None:
    last_msg = read_last_assistant_message(hook.get("transcript_path", ""))
    payload = build_payload(hook, "input_needed", last_msg)
    payload["request"] = "notify"
    send_recv(payload)
    _debug("notification sent")


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
            _debug(f"notification type '{ntype}' -- ignoring")

    else:
        _debug(f"unknown hook event: {name}")

    sys.exit(0)


if __name__ == "__main__":
    main()
