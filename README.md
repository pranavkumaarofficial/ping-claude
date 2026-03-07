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
ping-claude install                        # Add hooks to ~/.claude/settings.json
ping-claude uninstall                      # Remove hooks
ping-claude start [--telegram] [--webapp]  # Start the server (add flags for integrations)
ping-claude status                         # Show server status, hooks, and config
ping-claude telegram --token T             # Save Telegram bot token
ping-claude voice --key K                  # Save Groq API key for voice transcription
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
│       ├── websocket_server.py   # Event hub: TCP for hooks, WebSocket for phones
│       ├── telegram_bot.py       # Telegram bridge: notifications, commands, voice, activity
│       ├── tailscale_helper.py   # Tailscale IP detection + QR code pairing
│       ├── webapp_server.py      # HTTP server for webapp static files (aiohttp)
│       └── webapp/               # React PWA (Vite build)
│           ├── src/              # React source (App.jsx, index.css)
│           ├── public/           # Static assets (icons, manifest, sw.js)
│           └── dist/             # Built output (served by webapp_server.py)
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

All Telegram dependencies install automatically with `pip install -e ".[telegram]"`.

## Webapp UI (PWA)

ping-claude includes a mobile-first web app that gives you the same control as Telegram — directly from your phone's browser. No app store needed.

### Setup

```bash
pip install -e ".[webapp]"    # or ".[all]" for Telegram + webapp

# Build the webapp (one-time, requires Node.js)
cd laptop/server/webapp
npm install && npm run build
cd ../../..

ping-claude start --webapp    # add --telegram too if you want both
```

Open `http://<your-tailscale-ip>:8767` on your phone. Add to Home Screen for a native app experience.

### What you get

- **Real-time event feed** — task completions, permission requests, and input prompts appear as cards
- **Approve/Deny buttons** — permission request cards have inline action buttons
- **Send commands** — type in the input bar to send follow-up commands to Claude
- **Session targeting** — dropdown to target specific sessions when running multiple Claude instances
- **Activity feed** — collapsible live view of what Claude is doing (tool starts/ends)
- **Browser notifications** — get notified even when the tab is in the background
- **Works offline** — the app shell is cached by a service worker for instant loading

### Architecture

The webapp is a vanilla HTML/JS/CSS PWA (no build step). A lightweight aiohttp server on port 8767 serves the static files. The webapp connects to the existing WebSocket server on port 8765 — the same protocol phone clients already use.

```
Browser (PWA)  ──  WS :8765  ──>  websocket_server.py (existing)
                   HTTP :8767 -->  webapp_server.py (static files)
```

## Roadmap

- [ ] Native mobile app (Expo/React Native) with richer UI
- [ ] Bidirectional voice — Claude speaks responses back to you via TTS
- [ ] Multi-user support for team environments
- [ ] Desktop notification fallback when Telegram isn't available
- Tailscale (for secure remote access)

## License

MIT
