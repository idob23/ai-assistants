"""Точка входа бота-автоэлектрика.

Инициализирует конфигурацию, подключение к БД,
регистрирует хэндлеры и запускает polling.
"""

import asyncio
import logging
import re
import sys
from pathlib import Path

from aiogram.types import Message

from core.config import get_settings
from core.telegram_bot import BaseTelegramBot
from bots.autoelectric.constants import WEB_SEARCH_TOOL, X431_MARKER
from bots.autoelectric.db import Database
from bots.autoelectric.handlers import handle_x431_url

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "system_prompt.md"

FALLBACK_PROMPT = (
    "Ты — ассистент автоэлектрика. Помогаешь диагностировать электрические "
    "неисправности грузовиков (КамАЗ, Урал, Sitrak) и легковых (УАЗ, Mitsubishi L200). "
    "Давай 2-3 гипотезы с измеримыми критериями проверки. "
    "Честность важнее уверенности — говори «не знаю» когда не знаешь."
)


class AutoelectricBot(BaseTelegramBot):

    def __init__(self, token: str, allowed_users: list[int] | None = None):
        super().__init__(token=token, allowed_users=allowed_users)
        try:
            self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.system_prompt = FALLBACK_PROMPT
        settings = get_settings()
        self.db = Database(settings.DATABASE_URL)
        self._pending_close: dict[int, int] = {}
        self._pending_miscall: dict[int, int] = {}
        self._active_case: dict[int, int] = {}

    async def setup(self):
        await self.db.connect()
        await self.db.init_schema()
        # Restore active cases from DB (survives process restart)
        cases = await self.db.get_open_cases()
        for c in cases:
            tid = c.get("telegram_thread_id") or ""
            if tid.startswith("tg:"):
                try:
                    chat_id = int(tid[3:])
                    self._active_case[chat_id] = c["id"]
                except ValueError:
                    pass

    async def _find_open_case_id(self, chat_id: int) -> int | None:
        """Return open case_id from cache or fall back to DB."""
        cached = self._active_case.get(chat_id)
        if cached:
            return cached
        cases = await self.db.get_open_cases()
        for c in cases:
            if c.get("telegram_thread_id") == f"tg:{chat_id}":
                self._active_case[chat_id] = c["id"]
                return c["id"]
        return None

    # --- command handlers (dispatched from handle_text) ---

    async def handle_text(self, message: Message):
        """Override base: intercept /commands before process_message."""
        if message.text and message.text.startswith("/"):
            chat_id = message.chat.id
            cancelled = False
            if chat_id in self._pending_close:
                self._pending_close.pop(chat_id)
                cancelled = True
            if chat_id in self._pending_miscall:
                self._pending_miscall.pop(chat_id)
                cancelled = True
            if cancelled:
                await message.answer("↩️ Ожидание отменено.")
            cmd = message.text.split()[0].split("@")[0]
            if cmd == "/start":
                return await self.cmd_start(message)
            if cmd == "/status":
                return await self.cmd_status(message)
            if cmd == "/close":
                return await self.cmd_close(message)
            if cmd == "/miscall":
                return await self.cmd_miscall(message)
        await self.process_message(message, text=message.text)

    async def cmd_start(self, message: Message):
        chat_id = message.chat.id
        self.get_history(chat_id).clear()
        # Close dangling case in DB if any
        case_id = await self._find_open_case_id(chat_id)
        if case_id:
            await self.db.close_case(
                case_id,
                resolution="abandoned (/start)",
                confidence="low",
            )
            self._active_case.pop(chat_id, None)
        await message.answer(
            "🔧 Агент-автоэлектрик на связи.\n"
            "Опиши симптом текстом, голосом или пришли фото.\n"
            "Для готовых данных — URL отчёта X431.\n"
            "\n"
            "Команды:\n"
            "/status  — открытые кейсы\n"
            "/close   — закрыть текущий кейс\n"
            "/miscall — отметить ошибку агента"
        )

    async def cmd_status(self, message: Message):
        cases = await self.db.get_open_cases()
        if not cases:
            await message.answer("✅ Открытых кейсов нет.")
            return
        lines = [f"📋 Открытых кейсов: {len(cases)}", ""]
        for c in cases[:5]:
            symptom = (c.get("symptom") or "")[:80]
            lines.append(f"  #{c['id']}  {symptom}")
        await message.answer("\n".join(lines))

    async def cmd_close(self, message: Message):
        chat_id = message.chat.id
        case_id = await self._find_open_case_id(chat_id)
        if not case_id:
            await message.answer("ℹ️ Нет открытых кейсов для этого чата.")
            return
        self._pending_close[chat_id] = case_id
        await message.answer(
            f"📝 Закрываем кейс #{case_id}.\nЧто оказалось причиной?"
        )

    async def cmd_miscall(self, message: Message):
        chat_id = message.chat.id
        case_id = await self._find_open_case_id(chat_id)
        if not case_id:
            await message.answer("ℹ️ Нет открытых кейсов для этого чата.")
            return
        self._pending_miscall[chat_id] = case_id
        await message.answer("🔄 Что было на самом деле?")

    # --- main message processing ---

    async def process_message(self, message: Message, text: str,
                              image_b64: str | None = None):
        chat_id = message.chat.id

        # Pending miscall
        if chat_id in self._pending_miscall and text:
            case_id = self._pending_miscall.pop(chat_id)
            await self.db.log_miscall(case_id, predicted="", actual=text)
            await message.answer("✅ Ошибка агента записана.")
            return

        # Pending close
        if chat_id in self._pending_close and text:
            case_id = self._pending_close.pop(chat_id)
            await self.db.close_case(case_id, resolution=text)
            self._active_case.pop(chat_id, None)
            await message.answer(f"✅ Кейс #{case_id} закрыт.")
            return

        # X431 URL
        if text and X431_MARKER in text.lower():
            try:
                url_match = re.search(r"https?://\S+", text)
                url = url_match.group(0) if url_match else text.strip()
                await handle_x431_url(self, message, url)
            except Exception as exc:
                log.error("X431 parse error: %s", exc, exc_info=True)
                await message.answer(f"⚠️ Не удалось загрузить отчёт X431: {exc}")
            return

        # Auto-open case on first meaningful message in chat
        if text and await self._find_open_case_id(chat_id) is None:
            case_id = await self.db.create_case(
                vehicle_id=None,
                session_id=None,
                symptom=text[:500],
                telegram_thread_id=f"tg:{chat_id}",
            )
            self._active_case[chat_id] = case_id

        # Claude conversation
        history = self.get_history(chat_id)

        if image_b64:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": text or "Фото"},
            ]
        else:
            content = text or ""

        history.add_user(content)
        try:
            response = await self.claude_client.chat(
                messages=history.get_messages(),
                system=self.system_prompt,
                tools=[WEB_SEARCH_TOOL],
            )
        except Exception as exc:
            history.messages.pop()  # rollback user-turn
            log.error("Claude API error: %s", exc, exc_info=True)
            await message.answer("⚠️ Ошибка при обращении к модели. Попробуй через минуту.")
            return

        reply_text = "".join(
            b.text for b in response.content if hasattr(b, "text")
        )
        history.add_assistant(reply_text)
        await self.reply(message, reply_text)


async def main():
    logging.basicConfig(
        level=get_settings().LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    bot = AutoelectricBot(
        token=settings.TELEGRAM_BOT_TOKEN,
        allowed_users=settings.allowed_user_ids or None,
    )
    await bot.setup()
    try:
        await bot.start()
    finally:
        await bot.db.close()
        await bot.bot.session.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
