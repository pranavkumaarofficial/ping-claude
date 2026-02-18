#!/usr/bin/env python3
"""
Simulates Claude Code firing hook events.
Sends fake hook JSON to companion_hook.py via stdin,
exactly how Claude Code would invoke it.

Usage:
  python test_hook_sim.py stop          # simulate task completion (polls 10s for phone command)
  python test_hook_sim.py perm          # simulate PermissionRequest (polls ~110s for approve/deny)
  python test_hook_sim.py notify        # simulate Notification (fire-and-forget)
  python test_hook_sim.py rapid         # fire 5 events in quick succession

Test flow:
  1. Terminal 1:  python laptop/server/websocket_server.py
  2. Terminal 2:  python laptop/tests/test_phone_sim.py
  3. Terminal 3:  python laptop/tests/test_hook_sim.py stop
  4. In Terminal 2, type: say add some tests
     → The hook should block the stop and print the decision JSON in Terminal 3
"""

import json
import os
import subprocess
import sys
import tempfile
import time

HOOK_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "hooks", "companion_hook.py"
)


def _make_transcript(messages: list) -> str:
    """Write a temporary JSONL transcript file using the envelope format."""
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="pingclaude_test_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for msg in messages:
            # Claude Code transcript uses envelope format:
            # {"type": "assistant", "message": {"role": "assistant", "content": [...]}}
            role = msg.get("role", "unknown")
            envelope = {
                "type": role,
                "message": msg,
            }
            fh.write(json.dumps(envelope) + "\n")
    return path


def fire_hook(hook_json: dict, timeout: int = 15) -> None:
    """Run the hook script with the given JSON on stdin."""
    payload = json.dumps(hook_json)
    etype = hook_json.get("hook_event_name", "?")
    print(f"  Firing {etype} hook (timeout={timeout}s) ...")

    try:
        result = subprocess.run(
            [sys.executable, HOOK_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"  [TIMEOUT after {timeout}s]  {etype}")
        return

    status = "OK" if result.returncode == 0 else f"EXIT {result.returncode}"
    print(f"  [{status}]  {etype}")

    if result.stdout.strip():
        print(f"\n  HOOK OUTPUT (stdout):")
        try:
            parsed = json.loads(result.stdout.strip())
            print(f"  {json.dumps(parsed, indent=2)}")
        except json.JSONDecodeError:
            print(f"  {result.stdout.strip()}")

    if result.stderr.strip():
        print(f"  stderr: {result.stderr.strip()}")


def sim_stop():
    """Simulate Claude finishing a task (Stop hook).
    The hook will notify the phone and poll for ~10s for a follow-up command.
    Use the phone sim to send 'say <text>' during the poll window."""
    transcript = _make_transcript([
        {"role": "user", "content": "Refactor the auth module to use JWT"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I've refactored the auth module. Changes:\n"
             "- Replaced session-based auth with JWT tokens\n"
             "- Added token refresh endpoint at /api/auth/refresh\n"
             "- Updated middleware to validate JWT signatures\n"
             "- All 23 tests passing."},
        ]},
    ])
    fire_hook({
        "hook_event_name": "Stop",
        "session_id": "test-session-001",
        "transcript_path": transcript,
        "cwd": "C:\\Users\\dev\\myproject",
        "stop_hook_active": False,
    }, timeout=20)
    os.unlink(transcript)


def sim_perm():
    """Simulate a PermissionRequest hook.
    The hook will notify the phone and poll for approve/deny.
    Use the phone sim to send 'approve' or 'deny'."""
    transcript = _make_transcript([
        {"role": "user", "content": "Delete all temp files"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I need to run `rm -rf /tmp/build_*`. "
             "This will delete 47 temporary build directories."},
        ]},
    ])
    fire_hook({
        "hook_event_name": "PermissionRequest",
        "session_id": "test-session-003",
        "transcript_path": transcript,
        "cwd": "C:\\Users\\dev\\myproject",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /tmp/build_*"},
    }, timeout=130)
    os.unlink(transcript)


def sim_notify():
    """Simulate a Notification (idle_prompt). Fire and forget."""
    transcript = _make_transcript([
        {"role": "user", "content": "Deploy the app to staging"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I've prepared the deployment. Before I proceed:\n\n"
             "Should I deploy to staging with the current database migrations? "
             "This will run 3 pending migrations that alter the users table."},
        ]},
    ])
    fire_hook({
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "test-session-002",
        "transcript_path": transcript,
        "cwd": "C:\\Users\\dev\\myproject",
        "message": "Claude is waiting for your input",
        "title": "Input needed",
    }, timeout=10)
    os.unlink(transcript)


def sim_rapid():
    """Fire 5 stop events quickly to test broadcast under load."""
    for i in range(5):
        transcript = _make_transcript([
            {"role": "assistant", "content": [
                {"type": "text", "text": f"Completed step {i+1} of 5."},
            ]},
        ])
        fire_hook({
            "hook_event_name": "Stop",
            "session_id": f"test-rapid-{i}",
            "transcript_path": transcript,
            "cwd": "C:\\Users\\dev\\myproject",
            "stop_hook_active": False,
        }, timeout=20)
        os.unlink(transcript)
        time.sleep(0.3)


SCENARIOS = {
    "stop":   ("Stop — polls 10s for phone commands", sim_stop),
    "perm":   ("PermissionRequest — polls for approve/deny", sim_perm),
    "notify": ("Notification — fire and forget", sim_notify),
    "rapid":  ("Rapid-fire 5 stop events", sim_rapid),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in SCENARIOS:
        print("Usage: python test_hook_sim.py <scenario>\n")
        print("Scenarios:")
        for key, (desc, _) in SCENARIOS.items():
            print(f"  {key:12s}  {desc}")
        print("\nTest flow:")
        print("  1. Start server:     python laptop/server/websocket_server.py")
        print("  2. Start phone sim:  python laptop/tests/test_phone_sim.py")
        print("  3. Fire a hook:      python laptop/tests/test_hook_sim.py stop")
        print("  4. In phone sim, type 'say add tests' or 'approve'/'deny'")
        sys.exit(1)

    name = sys.argv[1]
    desc, fn = SCENARIOS[name]
    print(f"\n--- Simulating: {desc} ---\n")
    fn()
    print("\nDone.\n")


if __name__ == "__main__":
    main()
