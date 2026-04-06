# ping-claude —> Your local Claude Code terminal, on your phone

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)
![Offline](https://img.shields.io/badge/works-completely%20offline-brightgreen.svg)
![Tailscale](https://img.shields.io/badge/secured%20by-Tailscale-blue.svg)

Control Claude Code from your phone. Approve permissions, send commands, watch Claude work in real time, all through a secure Tailscale connection. No cloud relay, no third-party services. The server runs on your machine.

```
Claude finishes a task on your laptop  →  notification on your phone, wherever you are
Claude wants to run `rm -rf build/`   →  [Approve] [Deny] — no need to open your laptop
You speak a follow-up command          →  transcribed, sent, Claude keeps working
All of this over Tailscale — your machine, your network, zero cloud relay
```
## Demo

https://github.com/user-attachments/assets/77fda315-b0c0-4f5d-989b-b1d733c9c4c3

https://github.com/user-attachments/assets/85d70da9-c907-4f89-8e55-007d3683a88b




## Why this exists

Claude Code is powerful, but it runs in a terminal. When you step away to grab coffee, take a walk, or work from your couch, you lose visibility. You come back to find Claude finished 10 minutes ago and has been sitting idle, or worse, it's been waiting for permission to run a command.

ping-claude fixes that. It hooks into Claude Code's event system and bridges everything to your phone over a WebSocket connection. The server runs on your machine. The hook script uses zero external dependencies. Everything stays local.

**The key difference:** this works with Claude Code API keys. Anthropic's official Remote Control and Dispatch features require a paid Pro subscription and run through their servers. ping-claude gives you the same control over your own infrastructure, with better reliability (no silent disconnections, no 48-hour outages), and it adds features they don't have: voice transcription, multi-session dashboard, and a tabbed UI that separates active work from history.

## What you get

**Remote approve/deny** for permission requests. When Claude needs to run a bash command or make a destructive change, you get Approve and Deny buttons on your phone. No need to walk back to your desk.

**Voice commands** via the web UI. Record a voice message and it gets transcribed through Groq's Whisper API (free tier, sub-second latency) and sent to Claude as a follow-up command. Anthropic's Channels feature doesn't support voice transcription.

**Text commands** sent directly from the web UI. Target specific sessions when running multiple Claude instances. The tabbed interface keeps your active tasks separate from completed history so you can focus on what needs attention.

**Real-time activity feed** showing what Claude is doing right now. As Claude reads files, edits code, runs commands, and searches the codebase, tool chips update live in the feed. All sessions appear in one dashboard so you can monitor multiple Claude Code terminals at once.

**Session management** across multiple terminals. View all active sessions with their state (working, idle, waiting for input). End sessions remotely if needed.

**Zero cloud dependency**. The server runs on your machine. Events never touch external servers (except Groq for optional voice transcription). Your code stays private.

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
    │                       │                    │    │  Web UI
    │                       │                    │    │
    │                  poll for commands ◄────────┤    │
    │                       │                    │    │
    │  ◄── feed command ────┘                    │    │
    │                                            │    │
    ├── PreToolUse ──→ (activity event) ─────────┤    │
    ├── PostToolUse ─→ (activity event) ─────────┤    │
    │                                                 │
```

The hook script (`companion_hook.py`) is invoked by Claude Code on every event. It uses only Python stdlib (no pip dependencies, no import delays). It sends events to the local server over TCP and polls for commands from your phone.

The server (`websocket_server.py`) tracks sessions, manages the command queue, and broadcasts events to all connected WebSocket clients.

The React webapp serves a mobile-optimized UI with Active/History tabs. Active shows only pending permissions and running tools. History shows the full event log. This separation keeps the mobile experience clean even during long sessions.

## Setup

### 1. Install ping-claude

```bash
git clone https://github.com/pranavkumaarofficial/ping-claude.git
cd ping-claude
pip install -e .
```

### 2. Install Tailscale

Install Tailscale from [tailscale.com/download](https://tailscale.com/download) and run `tailscale up` to join your Tailnet. This gives your machine a stable IP (100.x.x.x) accessible from your other devices.

Tailscale handles NAT traversal and encryption automatically. You don't need to configure firewalls or expose ports to the internet.

### 3. Install the hooks

```bash
ping-claude install
```

This adds five hooks to `~/.claude/settings.json`:

| Hook | What it does |
|------|-------------|
| **Stop** | Fires when Claude finishes a task. Polls for 5 minutes waiting for your next command. |
| **PermissionRequest** | Fires when Claude needs approval. Polls for 2 minutes for your approve/deny. |
| **Notification** | Fires on idle prompts and input dialogs. Sends you a notification. |
| **PreToolUse** | Fires before Bash commands. Shows "running..." in the activity feed. |
| **PostToolUse** | Fires after any tool completes. Updates the activity feed with results. |

### 4. Build the webapp

The webapp is a React app built with Vite. You only need to build it once (or after pulling updates).

```bash
cd laptop/server/webapp
npm install
npm run build
cd ../../..
```

### 5. Start the server

```bash
ping-claude start
```

The server automatically serves the built webapp. You'll see three endpoints:

- **Hook listener**: tcp://127.0.0.1:8766 (Claude Code connects here)
- **WebSocket**: ws://0.0.0.0:8765 (your phone connects here)
- **Webapp HTTP**: http://0.0.0.0:8767 (serves the React app)

### 6. Connect from your phone

Open `http://<your-tailscale-ip>:8767` on your phone. Add to Home Screen for a native app experience.

You'll see the Tailscale IP printed in the server output. It looks like `100.98.99.60`.

### 7. (Optional) Enable voice commands

Voice messages are transcribed using [Groq's Whisper API](https://console.groq.com/keys). The free tier handles it fine.

```bash
ping-claude voice --key "YOUR_GROQ_API_KEY"
```

Now you can send voice messages in the web UI and they'll be transcribed and sent to Claude.

## CLI reference

```bash
ping-claude install              # Add hooks to ~/.claude/settings.json
ping-claude uninstall            # Remove hooks
ping-claude start                # Start the server (webapp enabled by default)
ping-claude start --no-webapp    # Start without web UI
ping-claude status               # Show server status, hooks, and config
ping-claude voice --key K        # Save Groq API key for voice transcription
```

## How the Stop hook works

This is the core mechanism that makes remote control possible.

When Claude finishes a task, Claude Code fires the Stop hook. Normally, the session would end. But `companion_hook.py` intercepts it: it sends the "task completed" event to the server (which notifies your phone), then enters a polling loop.

For the next 5 minutes, it checks the server every 2 seconds for a command from your phone. If you send one (via text, voice, or button), the hook prints a `{"decision": "block", "reason": "your command"}` response, which tells Claude Code to continue the session with your command as new input.

If no command arrives within 5 minutes, the hook times out and the session ends normally.

This approach has zero overhead during normal Claude Code usage. The hook only activates when Claude stops and waits for input.

## Architecture

```
ping-claude/
├── laptop/
│   ├── cli.py                    # CLI entry point (ping-claude command)
│   ├── hooks/
│   │   └── companion_hook.py     # Claude Code hook script (stdlib only, zero deps)
│   └── server/
│       ├── websocket_server.py   # Event hub: TCP for hooks, WebSocket for phones
│       ├── tailscale_helper.py   # Tailscale IP detection + QR code pairing
│       ├── webapp_server.py      # HTTP server for webapp static files (aiohttp)
│       └── webapp/               # React PWA (Vite build)
│           ├── src/              # React source (App.jsx, index.css)
│           ├── public/           # Static assets (icons, manifest, sw.js)
│           └── dist/             # Built output (served by webapp_server.py)
├── pyproject.toml                # Package config, dependencies, entry points
└── README.md
```

**companion_hook.py** has zero external dependencies. It's invoked by Claude Code as a subprocess on every hook event, so it needs to start fast. All communication is stdlib `socket` + `json`.

**websocket_server.py** is the central event hub. Hooks connect over TCP (:8766), clients connect over WebSocket (:8765). It tracks sessions, manages a command queue, and broadcasts events to all listeners.

**webapp_server.py** serves the built React app over HTTP (:8767). The webapp connects to the WebSocket server for live updates.

## Webapp UI

The webapp is a mobile-first React app with two tabs:

**Active tab** shows only what needs attention: pending permission requests, running tools, and the most recent Claude response. This keeps the UI clean during long sessions.

**History tab** shows the full event log: all messages, tool calls, and completed permissions. You can scroll back to see what happened earlier.

The webapp uses browser localStorage to track which permissions you've already approved or denied. When you switch tabs, completed items stay in History and don't clutter the Active view.

## Why not just use Claude's official features?

Anthropic has three relevant features:

1. **Remote Control** (Feb 2026): Control Claude Code from the Claude.ai web interface. Requires Pro subscription. Has silent disconnection bugs and broken auto-reconnect.

2. **Dispatch** (Mar 2026): Send tasks to Claude from your phone and get results later. Requires Pro subscription. Was completely non-functional for 48+ hours in late March. Ignores model settings and asks for permission on every single command (even harmless reads).

3. **Channels** (Research Preview): Official Telegram/Discord/iMessage plugins. Auto-loads in all sessions whether you want it or not. Drops messages frequently. No voice transcription. No permission relay (you still need the laptop for approvals).

ping-claude works with API keys (no Pro subscription needed), runs entirely on your infrastructure, and has been more reliable than any of these in practice. Plus it has features they don't: voice commands, multi-session dashboard, and a clean tabbed UI.

The trade-off is you need to run the server yourself. But if you're using Claude Code with API keys anyway, you already have the technical background to run a Python server.

## Requirements

- Python 3.9+
- Node.js (for building the webapp)
- `websockets` (core server)
- `aiohttp` (webapp server)
- `groq` (optional, for voice transcription)
- Tailscale (for secure remote access)

## Contributing

This is a passion project. If you find it useful and want to contribute, pull requests are welcome. Some areas that could use help:

- Native mobile app (Expo/React Native) with push notifications
- Better error handling in the webapp
- Tests for the hook script and server
- Docker setup for easier deployment

## License

MIT
