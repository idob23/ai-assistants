"""Точка входа бота-автоэлектрика.

Инициализирует конфигурацию, подключение к БД,
регистрирует хэндлеры и запускает polling.
"""

from core.config import *  # noqa: F401, F403
from core.telegram_bot import *  # noqa: F401, F403
