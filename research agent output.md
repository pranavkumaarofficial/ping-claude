# Ping Claude - Build Plan

## Project Structure

```bash
ping-claude/
├── research agent output.md  # This file — build plan + research
├── pyproject.toml            # pip install ping-claude
├── laptop/                   # Laptop-side code
│   ├── hooks/
│   │   └── companion_hook.py # Claude Code hook (stdlib only, zero deps)
│   ├── server/
│   │   ├── websocket_server.py  # TCP :8766 (hooks) + WS :8765 (phones)
│   │   ├── tailscale_helper.py  # Tailscale IP detection + QR
│   │   └── requirements.txt
│   ├── cli.py                # ping-claude install/start/status
│   └── tests/
│       ├── test_hook_sim.py  # Simulate hook events
│       └── test_phone_sim.py # Simulate phone connection
├── android/                  # Android app
│   ├── app/
│   │   ├── build.gradle.kts
│   │   └── src/
│   │       └── main/
│   │           ├── kotlin/
│   │           │   └── com/pingclaude/
│   │           │       ├── MainActivity.kt
│   │           │       ├── WebSocketClient.kt
│   │           │       ├── QRScanner.kt
│   │           │       ├── notification/
│   │           │       │   ├── NotificationManager.kt
│   │           │       │   └── ActionReceiver.kt
│   │           │       ├── ai/
│   │           │       │   ├── SummarizerManager.kt
│   │           │       │   ├── LlamaCppBridge.kt
│   │           │       │   └── ModelDownloader.kt
│   │           │       ├── voice/
│   │           │       │   ├── VoiceListenerService.kt
│   │           │       │   ├── WakeWordManager.kt
│   │           │       │   └── TTSManager.kt
│   │           │       └── tailscale/
│   │           │           └── TailscaleManager.kt
│   │           ├── assets/
│   │           │   └── hey_claude.ppn          # Porcupine wake word model
│   │           ├── jniLibs/                    # llama.cpp native libs
│   │           │   ├── arm64-v8a/
│   │           │   │   └── libllama.so
│   │           │   └── armeabi-v7a/
│   │           │       └── libllama.so
│   │           └── AndroidManifest.xml
│   ├── build.gradle.kts
│   └── settings.gradle.kts
└── tests/
    ├── test_hooks.py
    └── test_websocket.py
```

---

## CLAUDE.md (The Agent Bible)

### PROJECT VISION

Build an Android app that lets developers monitor and control Claude Code from anywhere in the world using:
- **Tailscale** for secure remote access (laptop stays at home, phone anywhere)
- **Qwen 2.5 0.5B** on-device via llama.cpp for AI summarization on ANY Android device (no API calls, no cloud, no special hardware)
- **Rich Android notifications** with action buttons + inline voice reply
- **"Hey Claude" custom wake word** via Picovoice Porcupine (fully offline)
- **Two-way voice control** with context-aware TTS (speaks only when Claude needs you)
- **Zero external dependencies** (no Telegram, ntfy, Pushover, no cloud APIs)

### OUTCOME

**Laptop setup (3 commands, one time):**
```
pip install ping-claude
ping-claude install    # auto-detects Python, injects hooks into Claude Code settings
ping-claude start      # starts server, shows QR code for phone pairing
```

**Phone setup:** Download APK from GitHub, scan QR code, done.

No API keys, no cloud services, no accounts to create beyond Tailscale (one-time).

### ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────┐
│         Laptop (Home/Office)        │
│                                     │
│  Claude Code                        │
│      │ stdin (hook events)          │
│      ▼                              │
│  companion_hook.py                  │
│      │                              │
│      ▼                              │
│  WebSocket Server (:8765)           │
│      │         ▲                    │
│      │         │ (user commands)    │
│  Tailscale     │                    │
└──────┼─────────┼────────────────────┘
       │         │
   Tailscale VPN (WireGuard encrypted)
   100.x.x.x:8765
       │         │
┌──────┼─────────┼────────────────────┐
│      ▼         │    Phone (Anywhere)│
│  WebSocket Client                   │
│      │         ▲                    │
│      ▼         │                    │
│  ┌─────────────────────────┐        │
│  │   SummarizerManager     │        │
│  │   (Qwen 0.5B/llama.cpp)│        │
│  └────────┬────────────────┘        │
│           │                         │
│  ┌────────▼────────────────┐        │
│  │  Smart Notification     │        │
│  │  [Approve] [Deny] [Reply]│       │
│  │  + Direct Voice Reply    │       │
│  └────────┬────────────────┘        │
│           │                         │
│  ┌────────▼────────────────┐        │
│  │  Voice Engine           │        │
│  │  Porcupine ("Hey Claude")│       │
│  │  + SpeechRecognizer     │        │
│  │  + TTS (auto-announce)  │        │
│  └─────────────────────────┘        │
└─────────────────────────────────────┘
```

### TWO-WAY VOICE FLOW (CORE UX)

This is the flagship experience. The flow is context-aware:

```
CASE 1: Claude COMPLETED a task (no input needed)
─────────────────────────────────────────────────
  Claude finishes → Hook fires → WebSocket → Phone
  Phone: notification chime + vibrate
  Notification shows: AI summary + [OK] button
  User: sees it, ignores or taps OK
  (NO TTS — don't annoy the user for routine completions)

CASE 2: Claude is WAITING for user input (the important case)
──────────────────────────────────────────────────────────────
  Claude asks a question → Hook fires (type: "input_needed")
  Phone: URGENT chime + distinct vibration pattern
  TTS auto-reads: "Claude is asking: should I deploy to staging?"
  User: "Hey Claude" → wake word detected
  User: "yes, go ahead" → SpeechRecognizer captures
  Phone: sends "yes, go ahead" via WebSocket → laptop
  Laptop: pipes response to Claude Code
  Phone: confirmation vibrate + "Sent!" notification update

CASE 3: User wants status anytime (user-initiated)
───────────────────────────────────────────────────
  User: "Hey Claude" → wake word detected
  Phone: chime, starts listening
  User: "what's happening?"
  Phone: queries laptop via WebSocket for current status
  Laptop: returns current Claude state
  Phone TTS: "Claude is refactoring the auth module. No input needed."
```

### KEY DESIGN DECISIONS

1. **TTS only fires when Claude NEEDS the user** — routine completions are silent notifications. This is the difference between a useful tool and an annoying one.
2. **Qwen 2.5 0.5B is the ONLY summarization model** — no fallback to regex, no tiered system. One model, works on any Android device with 3GB+ RAM. If the model hasn't downloaded yet, show raw text until it's ready.
3. **Porcupine is fully offline** — the wake word model (.ppn) is bundled in the APK. No internet needed for detection. The access key is embedded (free tier, open-source project).
4. **SpeechRecognizer for command capture** — after "Hey Claude" triggers, Android's built-in speech recognition handles the actual command. On Android 13+ this works offline too.

---

## AGENT TASKS BREAKDOWN

### PHASE 1: Laptop Backend (Foundation)

**Task 1.1: Claude Code Hook Script**
- **Goal:** Python hook that captures Claude Code events and classifies them
- **Files:** `laptop/hooks/companion_hook.py`
- **Input:** JSON from Claude Code stdin (hook events)
- **Output:** Structured event sent to WebSocket server
- **Event classification:**
  - `task_completed` — Claude finished work, no input needed
  - `input_needed` — Claude is asking a question, waiting for user
  - `error` — something failed
  - `progress` — intermediate update
- **Success Criteria:**
  - Reads JSON from stdin without errors
  - Extracts: event_type, session_id, transcript_path
  - Parses last assistant message from transcript
  - Classifies event as completed vs input_needed (critical for voice flow)
  - Sends structured JSON to localhost WebSocket (ws://127.0.0.1:8765)
  - Handles connection failures gracefully (doesn't crash Claude Code)
  - Runs in <500ms (must not slow down Claude Code)
- **Test:** Run manually with mock stdin, verify WebSocket receives correctly classified data

**Task 1.2: WebSocket Server**
- **Goal:** Bidirectional WebSocket server bridging laptop and phone
- **Tech:** Python asyncio + websockets library
- **Files:** `laptop/server/websocket_server.py`
- **Success Criteria:**
  - Listens on ws://0.0.0.0:8765
  - Accepts connections from Tailscale network only (validates 100.x.x.x)
  - Receives events from hook script, broadcasts to connected phones
  - Receives commands FROM phone (approve, deny, custom text)
  - Pipes received commands back to Claude Code via appropriate mechanism
  - Tracks current Claude state for status queries
  - Handles client connect/disconnect cleanly
  - Logs all events for debugging
- **Test:** Connect with `websocat`, send mock events both directions

**Task 1.3: Tailscale Integration**
- **Goal:** Auto-detect Tailscale IP + generate QR code
- **Files:** `laptop/server/tailscale_helper.py`
- **Success Criteria:**
  - Runs `tailscale ip -4` and parses output
  - Validates IP is in 100.x.x.x range
  - Generates QR code containing `pingclaude://100.x.x.x:8765`
  - Displays QR in terminal (ASCII) or opens as image
  - Returns clear error if Tailscale not running
- **Test:** Works with and without Tailscale running

**Task 1.4: CLI Tool + Installer (`ping-claude` command)**
- **Goal:** `pip install ping-claude` → 3-command setup
- **Files:** `pyproject.toml`, `laptop/cli.py`
- **Commands:**
  - `ping-claude install` — auto-detects Python path, merges hooks into `~/.claude/settings.json`, installs dependencies
  - `ping-claude start` — starts WebSocket server, detects Tailscale, shows QR code
  - `ping-claude status` — shows server status, connected phones, active sessions
  - `ping-claude uninstall` — removes hooks from settings.json, stops server
- **Success Criteria:**
  - Auto-detects Python executable path (handles conda, venv, system Python)
  - Merges hook config into existing settings.json (doesn't overwrite other settings)
  - Works on Windows, macOS, Linux
  - `ping-claude start` runs server in foreground with clean output (QR + logs)
  - Clear error messages for every failure case
- **Test:** `pip install -e .` → `ping-claude install` → `ping-claude start` on Windows/macOS

---

### PHASE 2: Android App (Foundation)

**Task 2.1: Project Setup**
- **Goal:** Working Android Studio project with all dependencies
- **Files:** `android/build.gradle.kts`, `android/app/build.gradle.kts`, `android/settings.gradle.kts`
- **Dependencies:**
  - Jetpack Compose + Material3 (UI)
  - Ktor Client WebSocket (networking)
  - ZXing / ML Kit Barcode (QR code scanner)
  - Kotlinx Serialization (JSON)
  - Picovoice Porcupine Android SDK (wake word)
  - llama.cpp Android JNI bindings (on-device LLM)
  - DataStore Preferences (settings persistence)
- **Success Criteria:**
  - Project compiles without errors
  - All dependencies resolve
  - Min SDK: API 26 (Android 8.0) for broad compatibility
  - Target SDK: API 34 (Android 14)
  - Kotlin 2.0+
- **Test:** `./gradlew build` succeeds

**Task 2.2: Basic UI (Jetpack Compose)**
- **Goal:** Main screen with connection status and event feed
- **Files:** `android/app/src/main/kotlin/com/pingclaude/MainActivity.kt`
- **UI Elements:**
  - "Scan QR Code" button (first-time setup)
  - Connection status: "Connected to [laptop-name]" / "Disconnected"
  - Last event card with AI summary
  - "Hey Claude" toggle (enable/disable voice listening)
  - Settings (manual IP entry, model download status, notification preferences)
- **Success Criteria:**
  - Material3 design, clean and professional
  - Smooth animations on state changes
  - Handles configuration changes (rotation)
  - Dark mode support
- **Test:** UI renders correctly, no crashes on rotation

**Task 2.3: WebSocket Client**
- **Goal:** Persistent WebSocket connection to laptop via Tailscale
- **Files:** `android/app/src/main/kotlin/com/pingclaude/WebSocketClient.kt`
- **Success Criteria:**
  - Connects to ws://[TAILSCALE_IP]:8765
  - Receives events as JSON, deserializes to sealed class hierarchy
  - Sends commands back (approve, deny, custom text, status query)
  - Auto-reconnects on disconnect with exponential backoff
  - Exposes `Flow<ConnectionState>` (Connected/Disconnected/Reconnecting)
  - Exposes `Flow<ClaudeEvent>` for UI and notification consumption
  - Runs as part of a foreground service (survives app being backgrounded)
- **Test:** Connects to laptop, receives mock events, survives app kill

**Task 2.4: QR Code Scanner**
- **Goal:** Scan laptop's QR code to auto-configure connection
- **Files:** `android/app/src/main/kotlin/com/pingclaude/QRScanner.kt`
- **Success Criteria:**
  - Opens camera with clean viewfinder UI
  - Scans QR code with format: `pingclaude://100.x.x.x:8765`
  - Validates IP is in Tailscale range (100.x.x.x)
  - Saves IP to DataStore preferences
  - Auto-connects WebSocket after successful scan
  - Shows error for invalid QR codes
- **Test:** Scan generated QR, verify immediate connection

---

### PHASE 3: On-Device AI Summarization (Qwen 2.5 0.5B via llama.cpp)

**Task 3.1: llama.cpp Integration**
- **Goal:** Set up llama.cpp native bindings for Android
- **Files:** `android/app/src/main/kotlin/com/pingclaude/ai/LlamaCppBridge.kt`
- **Approach:** Use llama.cpp's official Android example (examples/llama.android/) as reference. Extract JNI bindings and prebuilt .so libraries for arm64-v8a and armeabi-v7a.
- **Success Criteria:**
  - JNI bridge loads native library without crashes
  - Can load a GGUF model file from app's internal storage
  - Can run inference (prompt in → text out) on CPU
  - Configurable: nCtx=2048, nThreads=4, nGpuLayers=0 (CPU only)
  - Proper memory management (load/unload model)
  - Does NOT crash on low-memory devices (catches OOM gracefully)
- **Test:** Load a small test model, generate text, verify output

**Task 3.2: Model Downloader**
- **Goal:** Download Qwen 2.5 0.5B-Instruct Q4_K_M GGUF on first launch
- **Files:** `android/app/src/main/kotlin/com/pingclaude/ai/ModelDownloader.kt`
- **Model:** `Qwen2.5-0.5B-Instruct` in Q4_K_M GGUF format (~380MB)
- **Source:** HuggingFace (direct URL to .gguf file)
- **Success Criteria:**
  - Shows download progress in UI (percentage + MB)
  - Downloads to app internal storage (not SD card)
  - Supports resume on interrupted downloads
  - Validates file integrity after download (file size check)
  - Shows "Model ready" state in UI when complete
  - User can trigger re-download if model is corrupted
  - Download happens in background (WorkManager or foreground service)
  - Notification shows download progress
- **Test:** Download completes, model loads successfully in llama.cpp

**Task 3.3: Summarizer**
- **Goal:** Summarize Claude Code transcripts into 1-2 sentence notifications
- **Files:** `android/app/src/main/kotlin/com/pingclaude/ai/SummarizerManager.kt`
- **Input:** Raw Claude Code transcript (200-1000 words)
- **Output:** 1-2 sentence summary suitable for a notification
- **Prompt template (ChatML format for Qwen):**
```
<|im_start|>system
You are a notification assistant for a coding tool. Summarize the developer's coding session into 1-2 brief sentences. Focus on WHAT was done and the RESULT. Be specific (mention file names, test counts, etc). No fluff.<|im_end|>
<|im_start|>user
Summarize this coding session for a phone notification:

{transcript}<|im_end|>
<|im_start|>assistant
```
- **Success Criteria:**
  - Loads Qwen model via LlamaCppBridge on app startup
  - Generates summaries in 2-5 seconds on mid-range devices
  - Temperature=0.3, topK=20 (consistent, factual summaries)
  - Max output tokens: 80
  - Truncates input to last 2000 chars if transcript is too long
  - Caches results (don't re-summarize same event)
  - If model not downloaded yet: show raw last assistant message as-is (NOT regex, just the raw text)
  - Thread-safe (inference runs on dedicated coroutine dispatcher)
- **Performance targets:**
  - Flagship phone (Snapdragon 8xx): 1-2 seconds
  - Mid-range phone (Snapdragon 6xx): 2-5 seconds
  - Budget phone (Snapdragon 4xx): 4-8 seconds
  - RAM usage during inference: ~500-600MB
- **Test:** Feed 10 real Claude Code transcripts, verify summaries are coherent and useful

---

### PHASE 4: Smart Notifications + Direct Reply

**Task 4.1: Notification Service**
- **Goal:** Context-aware notifications that behave differently based on event type
- **Files:** `android/app/src/main/kotlin/com/pingclaude/notification/NotificationManager.kt`
- **Two notification channels:**
  - `claude_completed` — normal priority, default sound
  - `claude_needs_input` — high priority, urgent sound, heads-up display
- **Notification content:**
  - Title: "Claude Code" (clean, no emoji in title)
  - Body: AI-generated summary from SummarizerManager
  - Big text style (expandable for longer summaries)
  - Timestamp
- **For task_completed events:**
  - Action buttons: [OK] [View Details]
  - Normal priority, standard chime
- **For input_needed events:**
  - Action buttons: [Approve] [Deny] [Reply with voice]
  - HIGH priority, urgent chime, heads-up notification
  - Triggers TTS announcement (see Phase 5)
- **Success Criteria:**
  - Two distinct notification channels with different behaviors
  - Notifications appear within 1 second of WebSocket event
  - Grouped notifications if multiple arrive while unread
  - Notification updates in-place (doesn't stack duplicates)
  - Professional appearance
- **Test:** Both notification types look and sound distinct

**Task 4.2: Notification Direct Reply (Inline Voice/Text)**
- **Goal:** Users can reply to Claude directly from the notification shade
- **Files:** `android/app/src/main/kotlin/com/pingclaude/notification/ActionReceiver.kt`
- **This is the baseline voice input** — user pulls down notification, taps Reply, uses keyboard mic to speak or types. Works on every Android device, zero extra setup.
- **Success Criteria:**
  - RemoteInput field on input_needed notifications: "Reply to Claude..."
  - BroadcastReceiver handles the reply intent
  - Extracts text from RemoteInput bundle
  - Sends reply text via WebSocket to laptop
  - Updates notification to show "Sent: [reply text]" with checkmark
  - Dismisses after 3 seconds
  - Also handles button taps: Approve sends "y", Deny sends "n"
- **Test:** Reply from notification (typed and voice), verify laptop receives

---

### PHASE 5: "Hey Claude" Voice Engine (Porcupine + TTS)

**Task 5.1: Wake Word Detection Service**
- **Goal:** Always-listening foreground service that detects "Hey Claude"
- **Files:** `android/app/src/main/kotlin/com/pingclaude/voice/WakeWordManager.kt`, `android/app/src/main/kotlin/com/pingclaude/voice/VoiceListenerService.kt`
- **Tech:** Picovoice Porcupine Android SDK (Apache 2.0, free tier for open-source, fully offline)
- **Wake word model:** Train "Hey Claude" on console.picovoice.ai (free), bundle as `hey_claude.ppn` in assets
- **Architecture:**
```
  VoiceListenerService (Foreground Service, FOREGROUND_SERVICE_TYPE_MICROPHONE)
      │
      ├── Persistent notification: "Ping Claude: Listening..."
      │
      ├── AudioRecord (16kHz mono)
      │       │
      │       ▼
      ├── Porcupine.process(audioFrame) — runs every ~32ms
      │       │
      │       ▼ (wake word detected!)
      │
      ├── Vibrate + chime sound
      │
      ├── SpeechRecognizer.startListening() — captures command
      │       │
      │       ▼
      ├── Recognized text → send via WebSocket
      │
      └── Return to wake word listening
```
- **Success Criteria:**
  - Foreground service starts when user enables "Hey Claude" in settings
  - Persistent notification shows listening state
  - Porcupine processes audio frames for "Hey Claude" detection
  - On detection: vibrate + chime + visual indicator (if screen on)
  - Launches SpeechRecognizer to capture follow-up command
  - Sends recognized text via WebSocket to laptop
  - Returns to wake word listening after command is sent
  - Toggle on/off from app settings and notification
  - Battery usage: 2-5% per hour (acceptable for productivity tool)
- **Permissions required:**
  - `RECORD_AUDIO`
  - `FOREGROUND_SERVICE`
  - `FOREGROUND_SERVICE_MICROPHONE`
  - `POST_NOTIFICATIONS`
- **Manifest additions:**
```xml
<service
    android:name=".voice.VoiceListenerService"
    android:foregroundServiceType="microphone"
    android:exported="false" />
```
- **Test:** Say "Hey Claude, approve the changes" — verify laptop receives "approve the changes"

**Task 5.2: Context-Aware TTS Announcements**
- **Goal:** Phone speaks Claude's questions out loud when input is needed
- **Files:** `android/app/src/main/kotlin/com/pingclaude/voice/TTSManager.kt`
- **Uses:** Android's built-in TextToSpeech engine (no extra dependencies)
- **Behavior:**
  - ONLY speaks for `input_needed` events (Claude is waiting for the user)
  - NEVER speaks for `task_completed` (silent notification only)
  - Speaks the AI-summarized version, not raw transcript
  - Example: "Claude is asking: should I add rate limiting to the auth module?"
  - After speaking, Porcupine stays active so user can immediately say "Hey Claude, yes"
- **Success Criteria:**
  - TTS initialized with default system voice
  - Speaks only for input_needed events
  - Respects phone silent mode / DND
  - Adjustable volume in settings
  - Can be disabled entirely in settings ("silent mode — notifications only")
  - If user is on a phone call: queues announcement, delivers after call ends
  - Works with Bluetooth headphones / car audio
- **Test:** Trigger input_needed event, verify TTS speaks summary, then "Hey Claude" → response → sent to laptop

**Task 5.3: Status Query (User-Initiated)**
- **Goal:** User can ask "Hey Claude, what's happening?" anytime
- **Files:** Integrated into `VoiceListenerService.kt` + `WebSocketClient.kt`
- **Flow:**
  1. User says "Hey Claude"
  2. Porcupine triggers, SpeechRecognizer captures "what's happening" or "status"
  3. App recognizes this as a status query (keyword match: "status", "what's happening", "how's it going", "update")
  4. Sends status request via WebSocket to laptop
  5. Laptop's WebSocket server returns current Claude state
  6. Phone summarizes with Qwen and reads via TTS
  7. Example: "Claude is running tests on the payment module. 47 of 52 tests passing. No input needed."
- **Success Criteria:**
  - Detects status-query intent from recognized speech
  - Queries laptop for current state
  - TTS reads back the status
  - If Claude is idle: "Claude is idle. No active tasks."
  - If not connected: "Not connected to laptop. Check Tailscale."
- **Test:** Say "Hey Claude, what's happening?" — hear accurate status

---

### PHASE 6: Polish and Release

**Task 6.1: Error Handling and Edge Cases**
- **Goal:** Everything fails gracefully with clear user guidance
- **Scenarios:**
  - No Tailscale installed: Show setup instructions with link
  - Tailscale disconnected: "Disconnected" indicator + auto-retry every 30s
  - WebSocket server down on laptop: "Laptop offline" + reconnection attempts
  - Model not downloaded: Show raw text notifications + "Download AI model" prompt
  - Model download interrupted: Resume download, show progress
  - Porcupine initialization fails: Disable voice, notify user, still work as notification-only
  - Low memory during inference: Catch OOM, show raw text, suggest closing other apps
  - Battery optimization killing service: Detect and guide user to exempt the app
  - Invalid QR code: Clear error with "Try again" button
- **Success Criteria:**
  - No unhandled crashes (try/catch everything at boundaries)
  - Every error state has a clear user-facing message
  - App always degrades to "at minimum, show notifications" — never fully breaks
  - All errors logged to Logcat with appropriate levels

**Task 6.2: OEM Battery Optimization Handling**
- **Goal:** Guide users on Xiaomi/Samsung/Huawei/Oppo to exempt app from battery killing
- **Files:** Integrated into settings UI
- **Success Criteria:**
  - Detects device manufacturer
  - Shows manufacturer-specific instructions for disabling battery optimization
  - Links to dontkillmyapp.com for edge cases
  - Checks if app is already exempt and shows green checkmark

**Task 6.3: Integration Tests**
- **Goal:** End-to-end testing of critical paths
- **Files:** `tests/test_integration.py`
- **Tests:**
  - Hook script → WebSocket → Android notification (within 2 seconds)
  - input_needed event → TTS speaks → voice reply → laptop receives
  - QR scan → connection established
  - Model download → summarization works
  - Wake word → command → WebSocket delivery
- **Test:** Automated where possible, manual checklist for voice/hardware

**Task 6.4: Documentation**
- **Goal:** README that sells + docs that unblock
- **Files:** `README.md`, `docs/SETUP.md`, `docs/ARCHITECTURE.md`
- **README must have:**
  - Hero GIF at the very top (the "Hey Claude" demo moment)
  - One-sentence description
  - Quick Start within first scroll
  - "How it works" diagram
  - Tech stack badges
  - Comparison vs alternatives (ntfy, Telegram bots, Happy Coder)
- **SETUP.md:** Step-by-step with screenshots, troubleshooting FAQ
- **ARCHITECTURE.md:** System diagram, data flow, security model

**Task 6.5: APK Release Build**
- **Goal:** Signed APK for GitHub Releases
- **Files:** `android/app/build.gradle.kts` (release config)
- **Success Criteria:**
  - ProGuard/R8 enabled and configured (don't strip JNI or Porcupine)
  - Minified
  - Signed with release key
  - APK size reasonable (excluding model download — model is downloaded at runtime)
  - GitHub Actions workflow to build APK on push to main
- **Test:** Install on fresh device, full flow works

---

## CRITICAL REQUIREMENTS

### Non-Negotiables:
1. **No cloud AI** — All summarization runs on-device via Qwen 0.5B + llama.cpp
2. **No third-party notification services** — Direct WebSocket over Tailscale
3. **No API keys for core functionality** — Porcupine access key is bundled (free tier, open-source)
4. **Privacy first** — No telemetry, no tracking, no data collection, all AI local
5. **Works on any Android 8.0+ device** — Qwen 0.5B runs on 3GB+ RAM phones

### Tech Stack:
- **Laptop:** Python 3.10+ / asyncio / websockets
- **Android:** Kotlin 2.0+ / Jetpack Compose / Material3
- **On-device AI:** Qwen 2.5 0.5B-Instruct Q4_K_M via llama.cpp (~380MB download)
- **Wake word:** Picovoice Porcupine (Apache 2.0 SDK, free tier, offline)
- **Voice capture:** Android SpeechRecognizer (system API, offline on Android 13+)
- **TTS:** Android TextToSpeech (system API)
- **Networking:** Tailscale (WireGuard) + WebSocket
- **QR pairing:** ZXing or ML Kit Barcode

### What This Is NOT:
- NOT a full remote terminal (use Happy Coder for that)
- NOT a chat interface with Claude (use Telegram bots for that)
- NOT a notification relay service (use ntfy for that)
- This IS: a hands-free, voice-first companion that pings you when Claude needs you and lets you respond without touching your laptop

---

## PROGRESS TRACKING

### Phase 1: Laptop Backend
- [x] Task 1.1: Hook Script (event classification) — DONE, tested with real Claude Code
- [x] Task 1.2: WebSocket Server (bidirectional) — DONE, multi-session tracking
- [x] Task 1.3: Tailscale Integration + QR — DONE
- [ ] Task 1.4: CLI Tool + Installer (`ping-claude install/start`)

### Phase 2: Android Foundation
- [ ] Task 2.1: Project Setup + Dependencies
- [ ] Task 2.2: Basic UI (Compose + Material3)
- [ ] Task 2.3: WebSocket Client (persistent)
- [ ] Task 2.4: QR Scanner

### Phase 3: On-Device AI
- [ ] Task 3.1: llama.cpp Integration (JNI)
- [ ] Task 3.2: Model Downloader (Qwen 0.5B)
- [ ] Task 3.3: Summarizer

### Phase 4: Smart Notifications
- [ ] Task 4.1: Context-Aware Notification Service
- [ ] Task 4.2: Direct Reply + Action Buttons

### Phase 5: Voice Engine
- [ ] Task 5.1: "Hey Claude" Wake Word (Porcupine)
- [ ] Task 5.2: Context-Aware TTS
- [ ] Task 5.3: Status Query

### Phase 6: Polish
- [ ] Task 6.1: Error Handling
- [ ] Task 6.2: OEM Battery Optimization
- [ ] Task 6.3: Integration Tests
- [ ] Task 6.4: Documentation + README
- [ ] Task 6.5: APK Release Build

---

## CURRENT TASK

**NEXT UP:** Task 1.4 — CLI Tool + Installer

Phase 1 core (hook + server + tailscale) is working end-to-end with real Claude Code sessions. Key findings during development:
- Hooks must use full Python path (e.g. `C:\Users\...\python.exe`) — bare `python` doesn't resolve in hook execution context
- Claude Code snapshots hooks at startup — settings.json edits require a new session
- Transcript JSONL uses envelope format: `{"type": "assistant", "message": {"role": "assistant", "content": [...]}}`
- `from __future__ import annotations` needed for Python 3.9 compat on Windows

---

## COMPETITIVE LANDSCAPE (Research Summary)

### What exists:
- **Happy Coder** (12.3k stars) — Full mobile client, iOS only, uses cloud
- **claude-ping** (conbon) — WhatsApp bridge, name taken
- **claude-notify** (343max, jamez01) — Pushover notifications, name taken
- **15+ other repos** — Telegram bots, ntfy hooks, Pushover integrations

### What Ping Claude does differently:
1. **On-device AI summarization** (Qwen 0.5B via llama.cpp) — nobody else does this
2. **Custom "Hey Claude" wake word** (Porcupine) — nobody else does this
3. **Context-aware TTS** (speaks only when Claude needs you) — nobody else does this
4. **Zero cloud dependency** (Tailscale direct + local AI) — most competitors use ntfy/Pushover/Telegram
5. **QR code pairing** — nobody else does this

### Name availability:
- `ping-claude` — AVAILABLE on GitHub
