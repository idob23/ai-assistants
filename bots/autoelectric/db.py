"""Работа с PostgreSQL для бота-автоэлектрика.

Функции:
- подключение к БД (connection pool)
- CRUD для vehicles, diagnostic_sessions, fault_codes
- сохранение diagnosis_cases и agent_miscalls
- выполнение миграций при старте
"""
