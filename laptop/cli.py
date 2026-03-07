#!/usr/bin/env python3
"""
Ping Claude CLI

Commands:
  ping-claude install              -- merge hooks into ~/.claude/settings.json
  ping-claude start [--telegram]   -- launch server (with optional Telegram bot)
  ping-claude status               -- show server status
  ping-claude telegram --token T   -- configure Telegram bot token
  ping-claude voice --key K        -- configure Groq API key for voice
  ping-claude uninstall            -- remove hooks from settings.json
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

HOOK_SCRIPT = Path(__file__).parent / "hooks" / "companion_hook.py"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
CONFIG_PATH = Path.home() / ".claude" / "ping-claude.json"


def load_settings() -> dict:
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
    SETTINGS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def save_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_hook_command() -> str:
    python_path = sys.executable
    hook_path = HOOK_SCRIPT.resolve()
    return f'{python_path} "{hook_path}"'


def install() -> None:
    print("Installing Ping Claude hooks...\n")

    if not HOOK_SCRIPT.exists():
        print(f"ERROR: Hook script not found at {HOOK_SCRIPT}")
        print("Make sure you're running from the ping-claude repository.")
        sys.exit(1)

    settings = load_settings()
    hooks = settings.setdefault("hooks", {})

    hook_cmd = get_hook_command()
    ping_claude_hooks = {
        "Stop": [{
            "hooks": [{
                "type": "command",
                "command": hook_cmd,
                "timeout": 60
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
        }],
        "PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{
                "type": "command",
                "command": hook_cmd,
                "timeout": 3
            }]
        }],
        "PostToolUse": [{
            "matcher": "Bash|Edit|Write|Read|Grep|Glob|WebSearch|WebFetch|Task",
            "hooks": [{
                "type": "command",
                "command": hook_cmd,
                "timeout": 5
            }]
        }]
    }

    for event_type, config in ping_claude_hooks.items():
        hooks[event_type] = config

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
    print("Uninstalling Ping Claude hooks...\n")

    if not SETTINGS_PATH.exists():
        print("No settings.json found -- nothing to uninstall.")
        return

    settings = load_settings()
    hooks = settings.get("hooks", {})

    removed = []
    all_hook_events = [
        "Stop", "PermissionRequest", "Notification", "PreToolUse", "PostToolUse",
    ]
    for event_type in all_hook_events:
        if event_type in hooks:
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
        print("No Ping Claude hooks found -- nothing to uninstall.")
    print()


def status() -> None:
    print("Ping Claude Server Status\n")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(("127.0.0.1", 8766))
        sock.close()

        if result == 0:
            print("[OK] Server is running")
            print("  Hook endpoint:  tcp://127.0.0.1:8766")
            print("  Phone endpoint: ws://127.0.0.1:8765")

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

    if SETTINGS_PATH.exists():
        settings = load_settings()
        hooks = settings.get("hooks", {})
        ping_claude_hooks = []
        for event_type in ["Stop", "PermissionRequest", "Notification",
                           "PreToolUse", "PostToolUse"]:
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

    config = load_config()
    tg = config.get("telegram", {})
    if tg.get("bot_token"):
        print(f"\n[OK] Telegram bot configured")
        if tg.get("chat_id"):
            print(f"  Chat ID: {tg['chat_id']}")
    else:
        print(f"\n[!] Telegram not configured")
        print("  Run: ping-claude telegram --token <BOT_TOKEN>")

    if config.get("groq_api_key"):
        print("[OK] Voice commands enabled (Groq Whisper)")
    else:
        print("[!] Voice not configured")
        print("  Run: ping-claude voice --key <GROQ_API_KEY>")

    print()


def telegram_setup() -> None:
    token = None
    for i, arg in enumerate(sys.argv):
        if arg == "--token" and i + 1 < len(sys.argv):
            token = sys.argv[i + 1]

    if not token:
        print("Usage: ping-claude telegram --token <BOT_TOKEN>\n")
        print("Steps:")
        print("  1. Open Telegram, search for @BotFather")
        print("  2. Send /newbot and follow the prompts")
        print("  3. Copy the bot token")
        print("  4. Run: ping-claude telegram --token <YOUR_TOKEN>")
        print("  5. Start server: ping-claude start --telegram")
        print("  6. Send /start to your bot in Telegram")
        return

    config = load_config()
    config.setdefault("telegram", {})["bot_token"] = token
    save_config(config)

    print("[OK] Telegram bot token saved!")
    print(f"  Config: {CONFIG_PATH}")
    print("\nNext steps:")
    print("  1. Start server with: ping-claude start --telegram")
    print("  2. Open Telegram and send /start to your bot")
    print()


def voice_setup() -> None:
    key = None
    for i, arg in enumerate(sys.argv):
        if arg == "--key" and i + 1 < len(sys.argv):
            key = sys.argv[i + 1]

    if not key:
        print("Usage: ping-claude voice --key <GROQ_API_KEY>\n")
        print("Steps:")
        print("  1. Go to https://console.groq.com/keys")
        print("  2. Create a free API key")
        print("  3. Run: ping-claude voice --key <YOUR_KEY>")
        print("  4. Send voice messages in Telegram -- they'll be transcribed")
        return

    config = load_config()
    config["groq_api_key"] = key
    save_config(config)

    print("[OK] Groq API key saved!")
    print(f"  Config: {CONFIG_PATH}")
    print("\nVoice commands are now enabled in Telegram.")
    print()


def start() -> None:
    print("Starting Ping Claude server...\n")

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

    import asyncio
    from laptop.server.websocket_server import main as server_main

    enable_tg = "--telegram" in sys.argv
    try:
        asyncio.run(server_main(enable_telegram=enable_tg))
    except KeyboardInterrupt:
        print("\n\nServer stopped.")


def main() -> None:
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
    elif cmd == "telegram":
        telegram_setup()
    elif cmd == "voice":
        voice_setup()
    elif cmd in ("-h", "--help", "help"):
        print(__doc__)
    else:
        print(f"Unknown command: {cmd}\n")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
