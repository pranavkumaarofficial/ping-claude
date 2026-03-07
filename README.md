# ping-claude

Control Claude Code from your phone. Get push notifications when tasks finish, approve or deny permission requests, send follow-up commands, and watch Claude work in real-time — all through a secure Tailscale connection.

```
Claude finishes refactoring auth.py  →  Push notification with full output
Claude wants to run `rm -rf build/`  →  [Approve] [Deny] buttons on your phone
You send a voice message             →  transcribed and fed to Claude as input
Claude reads files, runs tests       →  live activity feed updating on your phone
```

## Why this exists

Claude Code is great, but it runs in a terminal on your laptop. When you step away — to grab coffee, take a walk, or work from your couch — you lose visibility. You come back to find Claude finished 10 minutes ago and has been sitting idle, or worse, it's been waiting for permission to run a command.

ping-claude fixes that. It hooks into Claude Code's event system and bridges everything to a WebSocket server accessible over Tailscale. No cloud relay, no third-party services. The server runs on your machine, and the hook script uses zero external dependencies.

## What you get

**Push notifications** for every significant event — task completions, permission requests, and input prompts. Each notification includes Claude's last message so you know what happened without opening your laptop.

**Remote approve/deny** for permission requests. When Claude wants to run a bash command or make a destructive change, you get Approve and Deny buttons. No need to walk back to your desk.

**Voice commands** via the web UI. Record a voice message, and it gets transcribed through Groq's Whisper API (free tier, sub-second latency) and sent to Claude as a follow-up command.

**Text commands** sent directly from the web UI. Target specific sessions when running multiple Claude instances.

**Real-time activity feed** that shows what Claude is doing right now. As Claude reads files, edits code, runs commands, and searches the codebase, tool chips update live in the feed.

**Session management** across multiple Claude Code terminals. View all active sessions with their state (working, idle, waiting for input). End sessions remotely.

## How it works

```
Claude Code                                      Your Phone
    │                                                 │
    ├── Stop event ──→ companion_hook.py              │
    │                       │                         │
    │                       ├── TCP :8766 ──→ websocket_server.py
    │                       │                    │    │
    │                       │                    ├────┤ WebSocket :8765
    │                       │                    │    │      │
    │                       │                    │    │      ▼
    │                       │                    │    │  Web UI / Phone
    │                       │                    │    │
    │                  poll for commands ◄────────┤    │
    │                       │                    │    │
    │  ◄── feed command ────┘                    │    │
    │                                            │    │
    ├── PreToolUse ──→ (activity event) ─────────┤    │
    ├── PostToolUse ─→ (activity event) ─────────┤    │
    │                                                 │
```

The hook script (`companion_hook.py`) is invoked by Claude Code on every event. It uses only Python stdlib — no pip dependencies, no import delays. It sends events to the local server over TCP and polls for commands from your phone.

The server (`websocket_server.py`) tracks sessions, manages the command queue, and broadcasts events to all connected WebSocket clients.

## Setup

### 1. Install ping-claude

```bash
git clone https://github.com/pranavkumaarofficial/ping-claude.git
cd ping-claude
pip install -e .
```

### 2. Install Tailscale

Install Tailscale from [tailscale.com/download](https://tailscale.com/download) and run `tailscale up` to join your Tailnet. This gives your machine a stable IP (100.x.x.x) accessible from your other devices.

### 3. Install the hooks

```bash
ping-claude install
```

This adds five hooks to `~/.claude/settings.json`:

| Hook | What it does |
|------|-------------|
| **Stop** | Fires when Claude finishes a task. Polls for 5 minutes waiting for your next command. |
| **PermissionRequest** | Fires when Claude needs approval. Polls for 2 minutes for your approve/deny. |
| **Notification** | Fires on idle prompts and input dialogs. Sends you a push notification. |
| **PreToolUse** | Fires before Bash commands. Shows "running..." in the activity feed. |
| **PostToolUse** | Fires after any tool completes. Updates the activity feed with results. |

### 4. Start the server

```bash
ping-claude start
```

### 5. Connect from your phone

Open `http://<your-tailscale-ip>:8767` on your phone. Add to Home Screen for a native app experience.

### 6. (Optional) Enable voice commands

Voice messages are transcribed using [Groq's Whisper API](https://console.groq.com/keys). The free tier handles it fine.

```bash
ping-claude voice --key "YOUR_GROQ_API_KEY"
```

Now you can send voice messages in the web UI and they'll be transcribed and sent to Claude.

## CLI reference

```bash
ping-claude install              # Add hooks to ~/.claude/settings.json
ping-claude uninstall            # Remove hooks
ping-claude start                # Start the server
ping-claude status               # Show server status, hooks, and config
ping-claude voice --key K        # Save Groq API key for voice transcription
```

## Architecture

```
ping-claude/
├── laptop/
│   ├── cli.py                    # CLI entry point (ping-claude command)
│   ├── hooks/
│   │   └── companion_hook.py     # Claude Code hook script (stdlib only, zero deps)
│   └── server/
│       ├── websocket_server.py   # Event hub: TCP for hooks, WebSocket for clients
│       └── tailscale_helper.py   # Tailscale IP detection + QR code pairing
├── pyproject.toml                # Package config, dependencies, entry points
└── README.md
```

**companion_hook.py** has zero external dependencies. It's invoked by Claude Code as a subprocess on every hook event, so it needs to start fast. All communication is stdlib `socket` + `json`.

**websocket_server.py** is the central event hub. Hooks connect over TCP (:8766), clients connect over WebSocket (:8765). It tracks sessions, manages a command queue, and broadcasts events to all listeners.

## How the Stop hook works

This is the core mechanism that makes remote control possible.

When Claude finishes a task, Claude Code fires the Stop hook. Normally, the session would end. But `companion_hook.py` intercepts it: it sends the "task completed" event to the server (which notifies your phone), then enters a polling loop.

For the next 5 minutes, it checks the server every 2 seconds for a command from your phone. If you send one (via text, voice, or button), the hook prints a `{"decision": "block", "reason": "your command"}` response — which tells Claude Code to continue the session with your command as new input.

If no command arrives within 5 minutes, the hook times out and the session ends normally.

## Requirements

- Python 3.9+
- `websockets` (core server)
- `qrcode[pil]` (optional, for Tailscale QR pairing)
- Tailscale (for secure remote access)

## License

MIT
