"""Обёртка над Anthropic Messages API.

Единая точка взаимодействия с Claude для всех ботов:
- отправка текстовых сообщений
- отправка изображений (vision)
- управление system prompt
- retry и обработка ошибок API
"""

import asyncio
import logging

import anthropic

from core.config import get_settings

log = logging.getLogger(__name__)


class ClaudeClient:

    def __init__(self, api_key: str, model: str, base_url: str):
        self.model = model
        self.client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
        )

    async def chat(
        self,
        messages: list[dict],
        system: str = "",
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> anthropic.types.Message:
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        last_exc = None
        for attempt in range(3):
            try:
                response = await self.client.messages.create(**kwargs)
                log.info(
                    "Claude response: model=%s messages=%d input_tokens=%d output_tokens=%d",
                    self.model,
                    len(messages),
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
                return response
            except (anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
                last_exc = exc
                delay = 2 ** (attempt + 1)  # 2, 4, 8
                log.warning(
                    "Claude API error (attempt %d/3): %s — retrying in %ds",
                    attempt + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        raise last_exc


class ConversationHistory:

    def __init__(self, system_prompt: str = ""):
        self.system_prompt = system_prompt
        self.messages: list[dict] = []

    def add_user(self, content: str | list):
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str | list):
        self.messages.append({"role": "assistant", "content": content})

    def add_tool_result(self, tool_use_id: str, content: str):
        self.messages.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
            ],
        })

    def get_messages(self) -> list[dict]:
        return list(self.messages)

    def clear(self):
        self.messages.clear()

    def __len__(self) -> int:
        return len(self.messages)


def create_client() -> ClaudeClient:
    settings = get_settings()
    return ClaudeClient(
        api_key=settings.CLAUDE_API_KEY,
        model=settings.CLAUDE_MODEL,
        base_url=settings.CLAUDE_BASE_URL,
    )
