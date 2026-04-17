"""Базовый класс TelegramBot.

Предоставляет общий интерфейс для всех ботов парка:
- инициализация aiogram-бота и диспетчера
- регистрация хэндлеров (text, voice, photo)
- middleware для фильтрации пользователей
- polling-запуск
"""

import base64
import io
import logging

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.types import Message, ContentType

from core.claude_client import ClaudeClient, ConversationHistory, create_client
from core.whisper_client import create_whisper_client, WhisperClient

log = logging.getLogger(__name__)


class _AccessMiddleware(BaseMiddleware):

    def __init__(self, allowed_users: list[int]):
        self.allowed_users = allowed_users

    async def __call__(self, handler, event: Message, data: dict):
        if event.from_user and event.from_user.id not in self.allowed_users:
            await event.answer("Доступ ограничен")
            return
        return await handler(event, data)


class BaseTelegramBot:

    def __init__(self, token: str, allowed_users: list[int] | None = None):
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.conversations: dict[int, ConversationHistory] = {}
        self.system_prompt: str = ""
        self.claude_client: ClaudeClient = create_client()
        self.whisper_client: WhisperClient = create_whisper_client()

        if allowed_users:
            self.dp.message.middleware(_AccessMiddleware(allowed_users))

        self.dp.message.register(self.handle_text, lambda m: m.content_type == ContentType.TEXT)
        self.dp.message.register(self.handle_voice, lambda m: m.content_type == ContentType.VOICE)
        self.dp.message.register(self.handle_photo, lambda m: m.content_type == ContentType.PHOTO)

    def get_history(self, chat_id: int) -> ConversationHistory:
        if chat_id not in self.conversations:
            self.conversations[chat_id] = ConversationHistory(
                system_prompt=self.system_prompt,
            )
        return self.conversations[chat_id]

    async def handle_text(self, message: Message):
        await self.process_message(message, text=message.text)

    async def handle_voice(self, message: Message):
        file = await self.bot.get_file(message.voice.file_id)
        buf = io.BytesIO()
        await self.bot.download_file(file.file_path, buf)
        transcribed_text = await self.whisper_client.transcribe_bytes(
            buf.getvalue(), suffix=".ogg",
        )
        await message.answer(f"\U0001f3a4 {transcribed_text}")
        await self.process_message(message, text=transcribed_text)

    async def handle_photo(self, message: Message):
        photo = message.photo[-1]
        file = await self.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await self.bot.download_file(file.file_path, buf)
        b64_string = base64.b64encode(buf.getvalue()).decode()
        caption = message.caption or ""
        await self.process_message(message, text=caption, image_b64=b64_string)

    async def process_message(self, message: Message, text: str,
                              image_b64: str | None = None):
        raise NotImplementedError

    async def reply(self, message: Message, text: str):
        while text:
            if len(text) <= 4000:
                await message.answer(text)
                break
            split = text.rfind("\n", 0, 4000)
            if split == -1:
                split = 4000
            await message.answer(text[:split])
            text = text[split:].lstrip("\n")

    async def start(self):
        log.info("Bot starting...")
        await self.dp.start_polling(self.bot)
