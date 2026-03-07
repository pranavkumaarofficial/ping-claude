#!/usr/bin/env python3
"""
Ping Claude -- Telegram Bot Bridge

Bridges the WebSocket server to Telegram, sending notifications and
receiving commands (approve/deny/say) via inline keyboards and commands.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from html import escape as html_escape
from pathlib import Path
from typing import Callable

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)

log = logging.getLogger("pingclaude.telegram")

CONFIG_PATH = Path.home() / ".claude" / "ping-claude.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def save_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _md_to_html(text: str) -> str:
    """Convert common markdown from Claude's output to Telegram HTML."""
    chunks = []
    # Split by fenced code blocks (```lang\n...\n```)
    parts = re.split(r'(```\w*\n.*?```)', text, flags=re.DOTALL)

    for part in parts:
        code_match = re.match(r'```\w*\n(.*?)```', part, flags=re.DOTALL)
        if code_match:
            chunks.append(f'<pre>{html_escape(code_match.group(1))}</pre>')
        else:
            s = html_escape(part)
            # inline code `text`
            s = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', s)
            # bold **text**
            s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
            # italic *text* (content must touch the stars -- no spaces)
            s = re.sub(r'\*(\S(?:[^*]*\S)?)\*', r'<i>\1</i>', s)
            # headers ## Text -> bold
            s = re.sub(r'^#{1,6}\s+(.+)$', r'<b>\1</b>', s, flags=re.MULTILINE)
            chunks.append(s)

    return ''.join(chunks)


def _session_preview(last_msg: str, max_len: int = 60) -> str:
    """Extract a short preview from the last assistant message."""
    if not last_msg:
        return ""
    # Take first non-empty line
    for line in last_msg.strip().splitlines():
        line = line.strip()
        if line and not line.startswith("```"):
            if len(line) > max_len:
                return line[:max_len] + "..."
            return line
    return ""


class TelegramBridge:
    def __init__(self, bot_token: str, enqueue_command: Callable):
        self.bot_token = bot_token
        self.enqueue_command = enqueue_command
        self.app: Application | None = None
        self.bot: Bot | None = None
        self.chat_ids: set[int] = set()
        self.target_session: dict[int, str] = {}  # chat_id -> session_id
        self._activity: dict[str, dict] = {}
        self._pending_flushes: dict[str, asyncio.Task] = {}

        config = load_config()
        saved_chat_id = config.get("telegram", {}).get("chat_id")
        if saved_chat_id:
            self.chat_ids.add(int(saved_chat_id))

    async def start(self) -> None:
        self.app = Application.builder().token(self.bot_token).build()
        self.bot = self.app.bot

        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("target", self._cmd_target))
        self.app.add_handler(CommandHandler("say", self._cmd_say))
        self.app.add_handler(CommandHandler("clear", self._cmd_clear))
        self.app.add_handler(CommandHandler("end", self._cmd_end))
        self.app.add_handler(CommandHandler("endall", self._cmd_endall))
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))
        self.app.add_handler(MessageHandler(filters.VOICE, self._handle_voice))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)

        bot_info = await self.bot.get_me()
        log.info(f"Telegram bot ...... @{bot_info.username}")

        for chat_id in self.chat_ids:
            try:
                await self.bot.send_message(chat_id, "Ping Claude server started.")
            except Exception:
                pass

    async def stop(self) -> None:
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    # --- send helper (handles message splitting) ---

    async def _send_html(self, chat_id: int, text: str, reply_markup=None) -> None:
        """Send HTML message, splitting into multiple if it exceeds Telegram's limit."""
        if len(text) <= 4000:
            try:
                await self.bot.send_message(
                    chat_id, text, parse_mode="HTML", reply_markup=reply_markup,
                )
            except Exception as exc:
                log.warning(f"Telegram send failed: {exc}")
            return

        # Split at line boundaries
        lines = text.split('\n')
        chunks: list[str] = []
        buf: list[str] = []
        buf_len = 0

        for line in lines:
            needed = len(line) + 1
            if buf_len + needed > 3900 and buf:
                chunks.append('\n'.join(buf))
                buf = []
                buf_len = 0
            buf.append(line)
            buf_len += needed
        if buf:
            chunks.append('\n'.join(buf))

        for i, chunk in enumerate(chunks):
            markup = reply_markup if i == len(chunks) - 1 else None
            try:
                await self.bot.send_message(
                    chat_id, chunk, parse_mode="HTML", reply_markup=markup,
                )
            except Exception as exc:
                log.warning(f"Telegram send chunk {i+1}/{len(chunks)} failed: {exc}")

    # --- event handler (called by server on broadcast) ---

    async def on_event(self, event: dict) -> None:
        if not self.chat_ids:
            return

        etype = event.get("event_type", "")

        if etype in ("tool_start", "tool_end"):
            await self._handle_activity(event)
            return

        sid = event.get("session_id", "")
        if sid:
            self._activity.pop(sid, None)

        project = event.get("project", "unknown")
        sid_short = sid[:12]
        last_msg = event.get("last_message", "")

        e_project = html_escape(project)
        e_msg = _md_to_html(last_msg)

        if etype == "task_completed":
            text = (
                f"<b>Task Completed</b>\n"
                f"<code>{e_project}</code> · <code>{sid_short}</code>\n\n"
                f"{e_msg}"
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Reply", callback_data=f"reply:{sid}"),
                    InlineKeyboardButton("Status", callback_data="status"),
                ]
            ])

        elif etype == "permission_request":
            tool = html_escape(event.get("tool_name", "unknown"))
            tool_input = event.get("tool_input", "")
            tool_input_str = html_escape(str(tool_input)[:300])
            text = (
                f"<b>Permission Request</b>\n"
                f"<code>{e_project}</code> · <code>{sid_short}</code>\n\n"
                f"Tool: <code>{tool}</code>\n"
                f"<pre>{tool_input_str}</pre>\n\n"
                f"{e_msg}"
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Approve", callback_data=f"approve:{sid}"),
                    InlineKeyboardButton("Deny", callback_data=f"deny:{sid}"),
                ]
            ])

        elif etype == "input_needed":
            text = (
                f"<b>Waiting for Input</b>\n"
                f"<code>{e_project}</code> · <code>{sid_short}</code>\n\n"
                f"{e_msg}"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Reply", callback_data=f"reply:{sid}")]
            ])

        else:
            return

        for chat_id in self.chat_ids:
            await self._send_html(chat_id, text, reply_markup=keyboard)

    # --- command handlers ---

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self.chat_ids.add(chat_id)

        config = load_config()
        config.setdefault("telegram", {})["chat_id"] = str(chat_id)
        save_config(config)

        await update.message.reply_text(
            "<b>Ping Claude connected!</b>\n\n"
            "/status -- active sessions\n"
            "/say &lt;text&gt; -- send to Claude\n"
            "/target &lt;id&gt; -- target a session\n"
            "/end [id] -- end a session\n"
            "/endall -- end all sessions\n"
            "/clear -- remove dead sessions\n"
            "/help -- all commands",
            parse_mode="HTML",
        )
        log.info(f"Telegram chat registered: {chat_id}")

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "<b>Ping Claude</b>\n\n"
            "/status -- active sessions + polling state\n"
            "/target &lt;id&gt; -- target a session (prefix match)\n"
            "/say &lt;text&gt; -- send command to Claude\n"
            "/end [id] -- end a session (stops after current work)\n"
            "/endall -- end all sessions\n"
            "/clear -- remove dead sessions\n\n"
            "Tap <b>Approve</b>/<b>Deny</b> on permission requests.\n"
            "Tap <b>Reply</b> then type your message.",
            parse_mode="HTML",
        )

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        from laptop.server.websocket_server import all_sessions_summary, pending_commands
        summary = all_sessions_summary()

        if summary["total"] == 0:
            await update.message.reply_text("No active sessions.")
            return

        lines = [f"<b>Sessions: {summary['total']}</b>\n"]
        for i, s in enumerate(summary["sessions"], 1):
            poll = "LIVE" if s.get("polling") else "DEAD"
            sid = s["session_id"][:12]
            status = s["status"].replace("waiting_for_input", "waiting")
            preview = _session_preview(s.get("last_message", ""))
            lines.append(f"{i}. <code>{sid}</code> [{poll}] ({status})")
            if preview:
                lines.append(f"   {html_escape(preview)}")

        target = self.target_session.get(update.effective_chat.id, "")
        if target:
            lines.append(f"\nTarget: <code>{target[:12]}</code>")
        if pending_commands:
            lines.append(f"Pending: {len(pending_commands)}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_clear(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        from laptop.server.websocket_server import prune_dead_sessions
        count = prune_dead_sessions(force=True)
        await update.message.reply_text(f"Cleared {count} dead session(s).")

    async def _cmd_end(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        args = update.message.text.split(maxsplit=1)

        if len(args) >= 2:
            sid = args[1].strip()
        else:
            sid = self.target_session.get(chat_id, "")

        if not sid:
            await update.message.reply_text(
                "Usage: /end &lt;session_id&gt;\n"
                "Or /target a session first, then /end",
                parse_mode="HTML",
            )
            return

        self.enqueue_command({
            "text": "terminate",
            "source": "phone_terminate",
            "session_id": sid,
            "timestamp": _now(),
        })

        from laptop.server.websocket_server import is_session_polling
        if is_session_polling(sid):
            msg = f"End queued for <code>{html_escape(sid[:12])}</code>. Session will stop after current work."
        else:
            msg = f"End queued for <code>{html_escape(sid[:12])}</code>, but session is not polling."
        await update.message.reply_text(msg, parse_mode="HTML")
        log.info(f"Telegram END session={sid[:12]}")

    async def _cmd_endall(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        from laptop.server.websocket_server import sessions

        if not sessions:
            await update.message.reply_text("No active sessions.")
            return

        count = 0
        for sid in list(sessions.keys()):
            self.enqueue_command({
                "text": "terminate",
                "source": "phone_terminate",
                "session_id": sid,
                "timestamp": _now(),
            })
            count += 1

        await update.message.reply_text(
            f"End queued for <b>{count}</b> session(s).",
            parse_mode="HTML",
        )
        log.info(f"Telegram ENDALL: {count} sessions")

    async def _cmd_target(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        args = update.message.text.split(maxsplit=1)

        if len(args) < 2:
            self.target_session.pop(chat_id, None)
            await update.message.reply_text("Target cleared.")
            return

        prefix = args[1].strip()
        self.target_session[chat_id] = prefix
        await update.message.reply_text(
            f"Target: <code>{html_escape(prefix[:12])}</code>",
            parse_mode="HTML",
        )

    async def _cmd_say(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        args = update.message.text.split(maxsplit=1)

        if len(args) < 2 or not args[1].strip():
            await update.message.reply_text("Usage: /say &lt;your command&gt;", parse_mode="HTML")
            return

        text = args[1].strip()
        sid = self.target_session.get(chat_id, "")

        self.enqueue_command({
            "text": text,
            "source": "phone_voice",
            "session_id": sid,
            "timestamp": _now(),
        })

        warning = self._polling_warning(sid)
        reply = f"Sent: <code>{html_escape(text[:200])}</code>"
        if warning:
            reply += f"\n{warning}"
        await update.message.reply_text(reply, parse_mode="HTML")
        log.info(f"Telegram /say: {text[:80]}")

    # --- callback query handler (inline buttons) ---

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        data = query.data or ""
        parts = data.split(":", 1)
        action = parts[0]
        sid = parts[1] if len(parts) > 1 else ""

        if action == "approve":
            self.enqueue_command({
                "text": "y", "source": "phone_approve",
                "session_id": sid, "timestamp": _now(),
            })
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("Approved.")
            log.info(f"Telegram APPROVE session={sid[:12]}")

        elif action == "deny":
            self.enqueue_command({
                "text": "n", "source": "phone_deny",
                "session_id": sid, "timestamp": _now(),
            })
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("Denied.")
            log.info(f"Telegram DENY session={sid[:12]}")

        elif action == "reply":
            self.target_session[query.message.chat_id] = sid
            warning = self._polling_warning(sid)
            reply = f"Target: <code>{sid[:12]}</code>. Send your message."
            if warning:
                reply += f"\n{warning}"
            await query.message.reply_text(reply, parse_mode="HTML")

        elif action == "status":
            from laptop.server.websocket_server import all_sessions_summary
            summary = all_sessions_summary()
            lines = [f"<b>Sessions: {summary['total']}</b>"]
            for i, s in enumerate(summary["sessions"], 1):
                poll = "LIVE" if s.get("polling") else "DEAD"
                sid_s = s["session_id"][:12]
                status = s["status"].replace("waiting_for_input", "waiting")
                preview = _session_preview(s.get("last_message", ""))
                lines.append(f"{i}. <code>{sid_s}</code> [{poll}] ({status})")
                if preview:
                    lines.append(f"   {html_escape(preview)}")
            await query.message.reply_text("\n".join(lines), parse_mode="HTML")

    # --- plain text handler (for reply flow) ---

    def _polling_warning(self, sid: str) -> str:
        if not sid:
            return ""
        from laptop.server.websocket_server import is_session_polling
        if not is_session_polling(sid):
            return "[!] Session not active -- hook timed out. /status to check."
        return ""

    async def _handle_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        if not text:
            return

        sid = self.target_session.get(chat_id, "")
        self.enqueue_command({
            "text": text,
            "source": "phone_voice",
            "session_id": sid,
            "timestamp": _now(),
        })

        warning = self._polling_warning(sid)
        reply = f"Sent: <code>{html_escape(text[:200])}</code>"
        if warning:
            reply += f"\n{warning}"
        await update.message.reply_text(reply, parse_mode="HTML")
        log.info(f"Telegram text: {text[:80]}")

    # --- voice message handler ---

    async def _handle_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        config = load_config()
        groq_key = config.get("groq_api_key", "")
        if not groq_key:
            await update.message.reply_text(
                "Voice not configured.\n"
                "Run: <code>ping-claude voice --key YOUR_GROQ_KEY</code>",
                parse_mode="HTML",
            )
            return

        voice = update.message.voice
        try:
            tg_file = await voice.get_file()
            ogg_bytes = await tg_file.download_as_bytearray()
        except Exception as exc:
            log.warning(f"voice download failed: {exc}")
            await update.message.reply_text("Failed to download voice message.")
            return

        text = await _transcribe_voice(ogg_bytes, groq_key)
        if not text:
            await update.message.reply_text("Could not transcribe voice.")
            return

        sid = self.target_session.get(chat_id, "")
        self.enqueue_command({
            "text": text,
            "source": "phone_voice",
            "session_id": sid,
            "timestamp": _now(),
        })

        warning = self._polling_warning(sid)
        reply = f"Voice: <code>{html_escape(text[:200])}</code>"
        if warning:
            reply += f"\n{warning}"
        await update.message.reply_text(reply, parse_mode="HTML")
        log.info(f"Telegram voice: {text[:80]}")

    # --- activity feed (real-time tool tracking) ---

    @staticmethod
    def _format_tool_line(tool: str, summary: str, result: str = "",
                          running: bool = False) -> str:
        icon = "\u2192" if running else "\u2713"
        if tool == "Bash":
            line = f"{icon} $ {summary}"
        elif tool in ("Read", "Edit", "Write", "Glob", "Grep"):
            line = f"{icon} {tool} {summary}"
        elif tool == "WebSearch":
            line = f"{icon} Search: {summary}"
        elif tool == "Task":
            line = f"{icon} Agent: {summary}"
        else:
            line = f"{icon} {tool}: {summary}" if summary else f"{icon} {tool}"
        if running:
            line += "..."
        if result and not running:
            line += f" \u2192 {result}"
        return line

    async def _handle_activity(self, event: dict) -> None:
        sid = event.get("session_id", "")
        if not sid:
            return

        etype = event.get("event_type", "")
        tool = event.get("tool_name", "")
        summary = event.get("tool_input_summary", "")
        result = event.get("tool_result_summary", "")
        project = event.get("project", "unknown")

        state = self._activity.setdefault(sid, {
            "entries": [], "current": None, "count": 0,
            "project": project, "msgs": {}, "last_edit": 0.0,
        })

        if etype == "tool_start":
            state["current"] = self._format_tool_line(tool, summary, running=True)
            state["count"] += 1
        elif etype == "tool_end":
            state["entries"].append(
                self._format_tool_line(tool, summary, result=result))
            state["current"] = None
            state["count"] += 1
            if len(state["entries"]) > 10:
                state["entries"] = state["entries"][-10:]

        state["project"] = project
        await self._flush_activity(sid)

    async def _flush_activity(self, sid: str) -> None:
        state = self._activity.get(sid)
        if not state:
            return

        now = time.time()
        elapsed = now - state["last_edit"]
        if elapsed < 2.0:
            if sid not in self._pending_flushes or self._pending_flushes[sid].done():
                self._pending_flushes[sid] = asyncio.create_task(
                    self._delayed_flush(sid, 2.0 - elapsed))
            return

        state["last_edit"] = now
        text = self._build_activity_text(sid, state)

        for chat_id in self.chat_ids:
            msg_id = state["msgs"].get(chat_id)
            if msg_id:
                try:
                    await self.bot.edit_message_text(
                        text, chat_id=chat_id, message_id=msg_id,
                        parse_mode="HTML")
                except Exception:
                    try:
                        msg = await self.bot.send_message(
                            chat_id, text, parse_mode="HTML")
                        state["msgs"][chat_id] = msg.message_id
                    except Exception:
                        pass
            else:
                try:
                    msg = await self.bot.send_message(
                        chat_id, text, parse_mode="HTML")
                    state["msgs"][chat_id] = msg.message_id
                except Exception:
                    pass

    async def _delayed_flush(self, sid: str, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._flush_activity(sid)

    def _build_activity_text(self, sid: str, state: dict) -> str:
        sid_short = sid[:12]
        project = html_escape(state["project"])
        count = state["count"]

        lines = [
            f"<b>Working...</b> ({count} actions)",
            f"<code>{project}</code> \u00b7 <code>{sid_short}</code>",
            "",
        ]
        for entry in state["entries"]:
            lines.append(html_escape(entry))
        if state["current"]:
            lines.append(html_escape(state["current"]))

        return "\n".join(lines)


async def _transcribe_voice(ogg_bytes: bytearray, api_key: str) -> str:
    import aiohttp
    try:
        form = aiohttp.FormData()
        form.add_field("file", ogg_bytes, filename="voice.ogg",
                       content_type="audio/ogg")
        form.add_field("model", "whisper-large-v3")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                data=form,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning(f"Groq transcription failed: {resp.status} {body[:200]}")
                    return ""
                result = await resp.json()
                return result.get("text", "").strip()
    except Exception as exc:
        log.warning(f"voice transcription error: {exc}")
        return ""


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
