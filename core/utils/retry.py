"""Retry-логика для внешних API.

Экспоненциальный backoff с jitter для устойчивости
к временным сбоям Anthropic API и Telegram API.
"""
