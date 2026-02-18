#!/usr/bin/env python3
"""
Simulates Claude Code firing hook events.
Sends fake hook JSON to companion_hook.py via stdin,
exactly how Claude Code would invoke it.

Usage:
  python test_hook_sim.py stop          # simulate task completion
  python test_hook_sim.py input         # simulate Claude waiting for input
  python test_hook_sim.py permission    # simulate permission prompt
  python test_hook_sim.py rapid         # fire 5 events in quick succession
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


def _make_transcript(messages: list[dict]) -> str:
    """Write a temporary JSONL transcript file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="pingclaude_test_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for msg in messages:
            fh.write(json.dumps(msg) + "\n")
    return path


def fire_hook(hook_json: dict) -> None:
    """Run the hook script with the given JSON on stdin."""
    payload = json.dumps(hook_json)
    result = subprocess.run(
        [sys.executable, HOOK_SCRIPT],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    status = "OK" if result.returncode == 0 else f"EXIT {result.returncode}"
    etype = hook_json.get("hook_event_name", "?")
    print(f"  [{status}]  {etype}", end="")
    if result.stderr:
        print(f"  stderr: {result.stderr.strip()}", end="")
    print()


def sim_stop():
    """Simulate Claude finishing a task (Stop event)."""
    transcript = _make_transcript([
        {"role": "user", "content": "Refactor the auth module to use JWT"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I've refactored the auth module. Changes:\n"
             "- Replaced session-based auth with JWT tokens\n"
             "- Added token refresh endpoint at /api/auth/refresh\n"
             "- Updated middleware to validate JWT signatures\n"
             "- All 23 tests passing."},
            {"type": "tool_use", "id": "toolu_01", "name": "Edit",
             "input": {"file_path": "src/auth.py"}},
        ]},
    ])
    fire_hook({
        "hook_event_name": "Stop",
        "session_id": "test-session-001",
        "transcript_path": transcript,
        "cwd": "/home/dev/myproject",
        "stop_hook_active": False,
    })
    os.unlink(transcript)


def sim_input_needed():
    """Simulate Claude waiting for user input (Notification idle_prompt)."""
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
        "cwd": "/home/dev/myproject",
        "message": "Claude is waiting for your input",
        "title": "Input needed",
    })
    os.unlink(transcript)


def sim_permission():
    """Simulate a permission prompt."""
    transcript = _make_transcript([
        {"role": "user", "content": "Delete all temp files"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I need to run `rm -rf /tmp/build_*`. "
             "This will delete 47 temporary build directories."},
        ]},
    ])
    fire_hook({
        "hook_event_name": "Notification",
        "notification_type": "permission_prompt",
        "session_id": "test-session-003",
        "transcript_path": transcript,
        "cwd": "/home/dev/myproject",
        "message": "Claude wants to run: rm -rf /tmp/build_*",
        "title": "Permission required",
    })
    os.unlink(transcript)


def sim_rapid():
    """Fire 5 events quickly to test broadcast under load."""
    for i in range(5):
        transcript = _make_transcript([
            {"role": "assistant", "content": f"Completed step {i+1} of 5."},
        ])
        fire_hook({
            "hook_event_name": "Stop",
            "session_id": f"test-rapid-{i}",
            "transcript_path": transcript,
            "cwd": "/home/dev/myproject",
            "stop_hook_active": False,
        })
        os.unlink(transcript)
        time.sleep(0.2)


SCENARIOS = {
    "stop": ("Task completed (Stop)", sim_stop),
    "input": ("Waiting for input (idle_prompt)", sim_input_needed),
    "permission": ("Permission prompt", sim_permission),
    "rapid": ("Rapid-fire 5 events", sim_rapid),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in SCENARIOS:
        print("Usage: python test_hook_sim.py <scenario>\n")
        print("Scenarios:")
        for key, (desc, _) in SCENARIOS.items():
            print(f"  {key:12s}  {desc}")
        sys.exit(1)

    name = sys.argv[1]
    desc, fn = SCENARIOS[name]
    print(f"\n--- Simulating: {desc} ---\n")
    fn()
    print("\nDone.\n")


if __name__ == "__main__":
    main()
