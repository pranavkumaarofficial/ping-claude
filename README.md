# ping-claude

Monitor and control Claude Code from your phone. No cloud, no third-party services, no API keys.

```
Claude finishes a task     →  your phone buzzes
Claude needs your input    →  your phone speaks the question out loud
You say "Hey Claude, yes"  →  response sent back to your laptop
```

## How it works

Your laptop runs a lightweight server that hooks into Claude Code. Your phone connects directly over [Tailscale](https://tailscale.com) (WireGuard VPN). An on-device AI model summarizes what Claude is doing so notifications actually make sense.

```
Claude Code  →  Hook Script  →  WebSocket Server  →  Your Phone
                (laptop)          (laptop)          (anywhere)
                                     ↑
                              Tailscale VPN
                           (encrypted, no cloud relay)
```

## What's different

Most Claude Code notification tools relay through Telegram, ntfy, or Pushover. This one doesn't.

- **On-device AI** summarizes Claude's output into readable notifications (Qwen 2.5 0.5B via llama.cpp, runs on any phone)
- **"Hey Claude" wake word** for hands-free voice control (Picovoice Porcupine, fully offline)
- **Context-aware TTS** that only speaks when Claude actually needs you, not on every completion
- **Direct connection** over Tailscale, no data leaves your devices

## Current status

**Phase 1 complete** - laptop backend is working end-to-end with real Claude Code sessions.

- [x] Hook script that captures Claude Code events (task completed, input needed)
- [x] WebSocket server with multi-session tracking (handles multiple Claude Code terminals)
- [x] Tailscale IP detection + QR code for phone pairing
- [x] Tested with real Claude Code on Windows
- [ ] `pip install ping-claude` CLI installer
- [ ] Android app (Kotlin + Jetpack Compose)
- [ ] On-device AI summarization
- [ ] "Hey Claude" voice engine

## Try it (early/manual setup)

Requires Python 3.9+ and the `websockets` package.

```bash
# Terminal 1: start the server
pip install websockets
python laptop/server/websocket_server.py

# Terminal 2: simulate a phone connection
python laptop/tests/test_phone_sim.py

# Terminal 3: simulate a Claude Code hook event
python laptop/tests/test_hook_sim.py stop
```

The phone sim will show the event arrive in real-time. For real Claude Code integration, hooks need to be added to `~/.claude/settings.json` (automated installer coming soon).

## Tech stack

| Component | Tech |
|-----------|------|
| Hook script | Python, stdlib only (zero deps) |
| Server | Python asyncio + websockets |
| Networking | Tailscale (WireGuard) |
| Android app | Kotlin, Jetpack Compose, Material3 |
| On-device AI | Qwen 2.5 0.5B via llama.cpp |
| Wake word | Picovoice Porcupine |
| TTS | Android system TextToSpeech |

## License

MIT
