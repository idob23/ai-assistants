"""Точка входа бота-автоэлектрика.

Инициализирует конфигурацию, подключение к БД,
регистрирует хэндлеры и запускает polling.
"""

import asyncio
import logging
import re
from pathlib import Path

from aiogram.types import Message

from core.config import get_settings
from core.telegram_bot import BaseTelegramBot
from bots.autoelectric.db import Database
from bots.autoelectric.handlers import AutoelectricHandlers

log = logging.getLogger(__name__)

X431_URL_RE = re.compile(r"https?://[^\s]*x431[^\s]*com[^\s]*/Report/", re.IGNORECASE)
PROMPT_PATH = Path(__file__).parent / "system_prompt.md"


class AutoelectricBot(BaseTelegramBot):

    def __init__(self):
        settings = get_settings()
        super().__init__(token=settings.TELEGRAM_BOT_TOKEN, allowed_users=None)
        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
        self.db = Database(settings.DATABASE_URL)
        self.handlers = AutoelectricHandlers(self.db)
        self.handlers.register(self.dp)

    async def setup(self):
        await self.db.connect()
        await self.db.init_schema()

    async def process_message(self, message: Message, text: str,
                              image_b64: str | None = None):
        history = self.get_history(message.chat.id)

        # Check for pending /close or /miscall input
        if text and await self.handlers.try_handle_close(message):
            return
        if text and await self.handlers.try_handle_miscall(message):
            return

        # Check for X431 URL
        if text and X431_URL_RE.search(text):
            try:
                url_match = X431_URL_RE.search(text)
                # Extract full URL (up to whitespace)
                full_url = text[url_match.start():].split()[0]
                summary = await self.handlers.handle_x431_url(full_url)
                history.add_user(summary)
            except Exception as exc:
                log.error("X431 parse error: %s", exc)
                await message.answer(f"Не удалось загрузить отчёт X431: {exc}")
                return
        elif image_b64:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_b64,
                    },
                },
            ]
            if text:
                content.append({"type": "text", "text": text})
            history.add_user(content)
        else:
            history.add_user(text or "")

        response = await self.claude_client.chat(
            messages=history.get_messages(),
            system=history.system_prompt,
        )

        response_text = response.content[0].text
        history.add_assistant(response_text)
        await self.reply(message, response_text)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    bot = AutoelectricBot()
    await bot.setup()
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
