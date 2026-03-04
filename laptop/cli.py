#!/usr/bin/env python3
"""
Ping Claude — CLI Tool
Manage installation, server, and hook configuration for Claude Code.

Commands:
  ping-claude install    — merge hooks into ~/.claude/settings.json
  ping-claude start      — launch server with QR code
  ping-claude status     — show server status
  ping-claude uninstall  — remove hooks from settings.json
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path


# Paths
HOOK_SCRIPT = Path(__file__).parent / "hooks" / "companion_hook.py"
SERVER_SCRIPT = Path(__file__).parent / "server" / "websocket_server.py"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def load_settings() -> dict:
    """Load ~/.claude/settings.json, creating it if missing."""
    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"WARNING: {SETTINGS_PATH} is malformed. Creating backup...")
        backup = SETTINGS_PATH.with_suffix(".json.bak")
        SETTINGS_PATH.rename(backup)
        return {}


def save_settings(data: dict) -> None:
    """Write ~/.claude/settings.json with pretty formatting."""
    SETTINGS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_hook_command() -> str:
    """Build the hook command with auto-detected Python path."""
    python_path = sys.executable
    hook_path = HOOK_SCRIPT.resolve()
    return f'{python_path} "{hook_path}"'


def install() -> None:
    """Merge Ping Claude hooks into ~/.claude/settings.json."""
    print("Installing Ping Claude hooks...\n")

    # Validate hook script exists
    if not HOOK_SCRIPT.exists():
        print(f"ERROR: Hook script not found at {HOOK_SCRIPT}")
        print("Make sure you're running from the ping-claude repository.")
        sys.exit(1)

    # Load existing settings
    settings = load_settings()
    hooks = settings.setdefault("hooks", {})

    # Define Ping Claude hook configuration
    hook_cmd = get_hook_command()
    ping_claude_hooks = {
        "Stop": [{
            "hooks": [{
                "type": "command",
                "command": hook_cmd,
                "timeout": 30
            }]
        }],
        "PermissionRequest": [{
            "hooks": [{
                "type": "command",
                "command": hook_cmd,
                "timeout": 120
            }]
        }],
        "Notification": [{
            "matcher": "idle_prompt|elicitation_dialog",
            "hooks": [{
                "type": "command",
                "command": hook_cmd,
                "timeout": 5
            }]
        }]
    }

    # Merge hooks (overwrite existing Ping Claude hooks, preserve others)
    for event_type, config in ping_claude_hooks.items():
        # Simple merge: replace entire hook for this event type
        # (Later: could be smarter about preserving non-Ping-Claude hooks)
        hooks[event_type] = config

    # Save updated settings
    save_settings(settings)

    print("[OK] Hooks installed successfully!")
    print(f"  Config: {SETTINGS_PATH}")
    print(f"  Python: {sys.executable}")
    print(f"  Hook:   {HOOK_SCRIPT}")
    print("\nNext steps:")
    print("  1. Start a NEW Claude Code session (hooks are loaded at startup)")
    print("  2. Run 'ping-claude start' to launch the server")
    print()


def uninstall() -> None:
    """Remove Ping Claude hooks from ~/.claude/settings.json."""
    print("Uninstalling Ping Claude hooks...\n")

    if not SETTINGS_PATH.exists():
        print("No settings.json found — nothing to uninstall.")
        return

    settings = load_settings()
    hooks = settings.get("hooks", {})

    # Remove Ping Claude hooks by checking if the command contains "companion_hook.py"
    removed = []
    for event_type in ["Stop", "PermissionRequest", "Notification"]:
        if event_type in hooks:
            # Check if this hook points to companion_hook.py
            configs = hooks[event_type]
            is_ping_claude = False
            for config in configs:
                for hook in config.get("hooks", []):
                    if "companion_hook.py" in hook.get("command", ""):
                        is_ping_claude = True
                        break
            if is_ping_claude:
                del hooks[event_type]
                removed.append(event_type)

    if removed:
        save_settings(settings)
        print("[OK] Removed hooks:")
        for event_type in removed:
            print(f"  - {event_type}")
        print(f"\nSettings updated: {SETTINGS_PATH}")
    else:
        print("No Ping Claude hooks found — nothing to uninstall.")
    print()


def status() -> None:
    """Check if the Ping Claude server is running."""
    print("Ping Claude Server Status\n")

    # Check if server is reachable
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(("127.0.0.1", 8766))
        sock.close()

        if result == 0:
            print("[OK] Server is running")
            print("  Hook endpoint:  tcp://127.0.0.1:8766")
            print("  Phone endpoint: ws://127.0.0.1:8765")

            # Try to get Tailscale IP
            from laptop.server.tailscale_helper import get_tailscale_ip
            ts_ip = get_tailscale_ip()
            if ts_ip:
                print(f"  Tailscale IP:   {ts_ip}")
            else:
                print("  Tailscale:      not detected")
        else:
            print("[!] Server is not running")
            print("\nStart the server with:")
            print("  ping-claude start")
    except Exception as exc:
        print(f"[!] Could not check server status: {exc}")

    print()

    # Check if hooks are installed
    if SETTINGS_PATH.exists():
        settings = load_settings()
        hooks = settings.get("hooks", {})
        ping_claude_hooks = []
        for event_type in ["Stop", "PermissionRequest", "Notification"]:
            if event_type in hooks:
                for config in hooks[event_type]:
                    for hook in config.get("hooks", []):
                        if "companion_hook.py" in hook.get("command", ""):
                            ping_claude_hooks.append(event_type)
                            break

        if ping_claude_hooks:
            print("[OK] Hooks installed:")
            for event_type in ping_claude_hooks:
                print(f"  - {event_type}")
        else:
            print("[!] Hooks not installed")
            print("\nInstall hooks with:")
            print("  ping-claude install")
    else:
        print("[!] No ~/.claude/settings.json found")

    print()


def start() -> None:
    """Launch the WebSocket server with QR code display."""
    print("Starting Ping Claude server...\n")

    # Validate server script exists
    if not SERVER_SCRIPT.exists():
        print(f"ERROR: Server script not found at {SERVER_SCRIPT}")
        sys.exit(1)

    # Check if already running
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", 8766))
        sock.close()

        if result == 0:
            print("Server is already running!")
            print("\nTo connect from your phone:")
            from laptop.server.tailscale_helper import get_tailscale_ip, print_qr_ascii, build_pairing_uri
            ts_ip = get_tailscale_ip()
            if ts_ip:
                uri = build_pairing_uri(ts_ip)
                print_qr_ascii(uri)
            else:
                print("  Tailscale not detected. Install from: https://tailscale.com/download")
            return
    except Exception:
        pass

    # Launch server
    try:
        subprocess.run([sys.executable, str(SERVER_SCRIPT)], check=True)
    except KeyboardInterrupt:
        print("\n\nServer stopped.")
    except subprocess.CalledProcessError as exc:
        print(f"\nERROR: Server failed with exit code {exc.returncode}")
        sys.exit(1)


def main() -> None:
    """CLI entry point."""
    if len(sys.argv) < 2:
        print(__doc__)
        print("Run 'ping-claude <command>' to get started.\n")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "install":
        install()
    elif cmd == "uninstall":
        uninstall()
    elif cmd == "start":
        start()
    elif cmd == "status":
        status()
    elif cmd in ("-h", "--help", "help"):
        print(__doc__)
    else:
        print(f"Unknown command: {cmd}\n")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
