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
TELEGRAM_MSG_LIMIT = 4000  # Telegram max is 4096, leave room for markup


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {}


def save_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


class TelegramBridge:
    def __init__(self, bot_token: str, enqueue_command: Callable):
        self.bot_token = bot_token
        self.enqueue_command = enqueue_command
        self.app: Application | None = None
        self.bot: Bot | None = None
        self.chat_ids: set[int] = set()
        self.target_session: dict[int, str] = {}  # chat_id -> session_id

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
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))

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

    # --- event handler (called by server on broadcast) ---

    async def on_event(self, event: dict) -> None:
        if not self.chat_ids:
            return

        etype = event.get("event_type", "")
        project = event.get("project", "unknown")
        sid = event.get("session_id", "")
        sid_short = sid[:12]
        last_msg = event.get("last_message", "")
        if len(last_msg) > TELEGRAM_MSG_LIMIT:
            last_msg = last_msg[:TELEGRAM_MSG_LIMIT] + "..."

        e_project = html_escape(project)
        e_msg = html_escape(last_msg)

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
            try:
                await self.bot.send_message(
                    chat_id, text, parse_mode="HTML",
                    reply_markup=keyboard,
                )
            except Exception as exc:
                log.warning(f"Telegram send failed: {exc}")

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
            "/help -- all commands",
            parse_mode="HTML",
        )
        log.info(f"Telegram chat registered: {chat_id}")

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "<b>Ping Claude</b>\n\n"
            "/status -- active sessions + polling state\n"
            "/target &lt;id&gt; -- target a session (prefix match)\n"
            "/say &lt;text&gt; -- send command to Claude\n\n"
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
        for s in summary["sessions"]:
            poll = "LIVE" if s.get("polling") else "DEAD"
            project = html_escape(Path(s["cwd"]).name if s["cwd"] else "?")
            sid = s["session_id"][:12]
            status = s["status"].replace("waiting_for_input", "waiting")
            lines.append(f"<code>{sid}</code> [{poll}] {project} ({status})")

        target = self.target_session.get(update.effective_chat.id, "")
        if target:
            lines.append(f"\nTarget: <code>{target[:12]}</code>")
        if pending_commands:
            lines.append(f"Pending: {len(pending_commands)}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

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
            for s in summary["sessions"]:
                project = html_escape(Path(s["cwd"]).name if s["cwd"] else "?")
                poll = "LIVE" if s.get("polling") else "DEAD"
                sid_s = s["session_id"][:12]
                lines.append(f"<code>{sid_s}</code> [{poll}] {project}")
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


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
