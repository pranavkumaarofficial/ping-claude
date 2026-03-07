# ping-claude

Control Claude Code from your phone. Get push notifications when tasks finish, approve or deny permission requests, send follow-up commands, and watch Claude work in real-time — all from Telegram.

```
Claude finishes refactoring auth.py  →  Telegram notification with full output
Claude wants to run `rm -rf build/`  →  [Approve] [Deny] buttons on your phone
You send a voice message             →  transcribed and fed to Claude as input
Claude reads files, runs tests       →  live activity feed updating on your phone
```

## Why this exists

Claude Code is great, but it runs in a terminal on your laptop. When you step away — to grab coffee, take a walk, or work from your couch — you lose visibility. You come back to find Claude finished 10 minutes ago and has been sitting idle, or worse, it's been waiting for permission to run a command.

ping-claude fixes that. It hooks into Claude Code's event system and bridges everything to a Telegram bot. No cloud relay, no third-party services beyond Telegram's API. The server runs on your machine, and the hook script uses zero external dependencies.

## What you get

**Push notifications** for every significant event — task completions, permission requests, and input prompts. Each notification includes Claude's last message so you know what happened without opening your laptop.

**Remote approve/deny** for permission requests. When Claude wants to run a bash command or make a destructive change, you get an inline keyboard with Approve and Deny buttons. No need to walk back to your desk.

**Voice commands** via Telegram voice messages. Record a voice note, and it gets transcribed through Groq's Whisper API (free tier, sub-second latency) and sent to Claude as a follow-up command.

**Text commands** through `/say` or just typing directly in the chat. Target specific sessions when running multiple Claude instances.

**Real-time activity feed** that shows what Claude is doing right now. As Claude reads files, edits code, runs commands, and searches the codebase, a single Telegram message updates in place:

```
Working... (12 actions)
ping-claude · abc123def

✓ Read cli.py
✓ Edit telegram_bot.py
✓ Grep "send_message"
→ $ npm test...
```

**Session management** across multiple Claude Code terminals. `/status` shows all active sessions with their state (working, idle, waiting for input). `/end` and `/endall` let you terminate sessions remotely.

## How it works

```
Claude Code                                      Your Phone
    │                                                 │
    ├── Stop event ──→ companion_hook.py              │
    │                       │                         │
    │                       ├── TCP :8766 ──→ websocket_server.py
    │                       │                    │    │
    │                       │                    ├────┤ telegram_bot.py
    │                       │                    │    │    │
    │                       │                    │    │    ├──→ Telegram Bot API
    │                       │                    │    │              │
    │                       │                    │    │              ▼
    │                       │                    │    │     Push notification
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

The server (`websocket_server.py`) tracks sessions, manages the command queue, and broadcasts events to all connected clients (Telegram bot, WebSocket phones, or both).

The Telegram bot (`telegram_bot.py`) formats events into readable messages, handles inline keyboards, processes voice messages, and manages the activity feed.

## Setup

### 1. Install ping-claude

```bash
git clone https://github.com/pranavkumaarofficial/ping-claude.git
cd ping-claude
pip install -e ".[telegram]"
```

This installs the `ping-claude` CLI tool and the Telegram dependencies (`python-telegram-bot`, `aiohttp`).

### 2. Create a Telegram bot

Open Telegram and search for [@BotFather](https://t.me/BotFather). Send `/newbot`, pick a name, and copy the bot token. It looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`.

### 3. Configure the bot token

```bash
ping-claude telegram --token "YOUR_BOT_TOKEN"
```

This saves the token to `~/.claude/ping-claude.json`. Your token never leaves your machine — it's only used to connect to Telegram's Bot API.

### 4. Install the hooks

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

### 5. Start the server

```bash
ping-claude start --telegram
```

### 6. Pair with Telegram

Open your bot in Telegram and send `/start`. That's it — you're connected.

Now open a **new** Claude Code session (hooks load at startup) and give it a task. You'll get a Telegram notification when it completes.

### 7. (Optional) Enable voice commands

Voice messages are transcribed using [Groq's Whisper API](https://console.groq.com/keys). The free tier handles it fine.

```bash
ping-claude voice --key "YOUR_GROQ_API_KEY"
```

Now you can send voice messages in Telegram and they'll be transcribed and sent to Claude.

## Telegram commands

| Command | What it does |
|---------|-------------|
| `/status` | Show all active Claude Code sessions with their state |
| `/say <text>` | Send a text command to Claude |
| `/target <id>` | Target a specific session (prefix match on session ID) |
| `/end [id]` | End a session — it stops after finishing current work |
| `/endall` | End all sessions |
| `/clear` | Remove dead sessions from the status list |
| `/help` | Show all commands |

You can also just **type directly** in the chat (without `/say`) and it'll be sent as a command. **Voice messages** work the same way — speak your command and it gets transcribed and sent.

When a permission request arrives, you get **[Approve]** and **[Deny]** buttons inline. When a task completes, you get **[Reply]** to target that session and **[Status]** to check all sessions.

## CLI reference

```bash
ping-claude install              # Add hooks to ~/.claude/settings.json
ping-claude uninstall            # Remove hooks
ping-claude start [--telegram]   # Start the server (add --telegram for Telegram bot)
ping-claude status               # Show server status, hooks, and config
ping-claude telegram --token T   # Save Telegram bot token
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
│       └── tailscale_helper.py   # Tailscale IP detection + QR code pairing
├── pyproject.toml                # Package config, dependencies, entry points
└── README.md
```

**companion_hook.py** has zero external dependencies. It's invoked by Claude Code as a subprocess on every hook event, so it needs to start fast. All communication is stdlib `socket` + `json`.

**websocket_server.py** is the central event hub. Hooks connect over TCP (:8766), phones connect over WebSocket (:8765). It tracks sessions, manages a command queue, and broadcasts events to all listeners.

**telegram_bot.py** subscribes to server events and formats them for Telegram. It handles inline keyboards, voice transcription via Groq Whisper, and an edit-in-place activity feed that updates every 2 seconds.

## How the Stop hook works

This is the core mechanism that makes remote control possible.

When Claude finishes a task, Claude Code fires the Stop hook. Normally, the session would end. But `companion_hook.py` intercepts it: it sends the "task completed" event to the server (which notifies your phone), then enters a polling loop.

For the next 5 minutes, it checks the server every 2 seconds for a command from your phone. If you send one (via text, voice, or inline button), the hook prints a `{"decision": "block", "reason": "your command"}` response — which tells Claude Code to continue the session with your command as new input.

If you send `/end` from Telegram, the hook receives a terminate signal and exits cleanly, letting Claude stop.

If no command arrives within 5 minutes, the hook times out and the session ends normally.

## Requirements

- Python 3.9+
- `websockets` (core server)
- `python-telegram-bot` (Telegram integration)
- `aiohttp` (voice transcription via Groq)
- `qrcode[pil]` (optional, for Tailscale QR pairing)

All Telegram dependencies install automatically with `pip install -e ".[telegram]"`.

## Roadmap

- [ ] Native mobile app (Expo/React Native) with richer UI
- [ ] Bidirectional voice — Claude speaks responses back to you via TTS
- [ ] Multi-user support for team environments
- [ ] Desktop notification fallback when Telegram isn't available

## License

MIT
