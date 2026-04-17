"""Хэндлеры команд бота-автоэлектрика.

Команды:
- /start  — приветствие
- /status — количество открытых кейсов
- /close  — закрытие кейса
- /miscall — запись ошибки агента
"""

import asyncio
import logging

from aiogram import Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from bots.autoelectric.db import Database
from bots.autoelectric.x431_parser import X431ReportParser

log = logging.getLogger(__name__)


class AutoelectricHandlers:

    def __init__(self, db: Database):
        self.db = db
        self._awaiting_close: set[int] = set()
        self._awaiting_miscall: set[int] = set()

    def register(self, dp: Dispatcher):
        dp.message.register(self.cmd_start, Command("start"))
        dp.message.register(self.cmd_status, Command("status"))
        dp.message.register(self.cmd_close, Command("close"))
        dp.message.register(self.cmd_miscall, Command("miscall"))

    async def cmd_start(self, message: Message):
        await message.answer(
            "Привет! Я помощник автоэлектрика. Отправь описание проблемы "
            "текстом или голосом, или кинь ссылку на отчёт X431."
        )

    async def cmd_status(self, message: Message):
        cases = await self.db.get_open_cases()
        await message.answer(f"Открытых кейсов: {len(cases)}")

    async def cmd_close(self, message: Message):
        self._awaiting_close.add(message.chat.id)
        await message.answer(
            "Какой диагноз в итоге? Опиши что оказалось причиной."
        )

    async def cmd_miscall(self, message: Message):
        self._awaiting_miscall.add(message.chat.id)
        await message.answer(
            "Что агент предсказал и что оказалось на самом деле? Формат:\n"
            "предсказание | реальность | комментарий"
        )

    async def try_handle_close(self, message: Message) -> bool:
        if message.chat.id not in self._awaiting_close:
            return False
        self._awaiting_close.discard(message.chat.id)
        cases = await self.db.get_open_cases()
        if not cases:
            await message.answer("Нет открытых кейсов для закрытия.")
            return True
        case = cases[0]
        await self.db.close_case(case["id"], resolution=message.text)
        await message.answer(f"Кейс #{case['id']} закрыт.")
        return True

    async def try_handle_miscall(self, message: Message) -> bool:
        if message.chat.id not in self._awaiting_miscall:
            return False
        self._awaiting_miscall.discard(message.chat.id)
        parts = message.text.split("|")
        predicted = parts[0].strip() if len(parts) >= 1 else ""
        actual = parts[1].strip() if len(parts) >= 2 else ""
        notes = parts[2].strip() if len(parts) >= 3 else ""
        cases = await self.db.get_open_cases()
        if not cases:
            await message.answer("Нет открытых кейсов для записи ошибки.")
            return True
        case_id = cases[0]["id"]
        await self.db.log_miscall(case_id, predicted, actual, notes)
        await message.answer("Ошибка агента записана. Спасибо за обратную связь!")
        return True

    async def handle_x431_url(self, url: str) -> str:
        parser = X431ReportParser()
        report = await asyncio.to_thread(parser.fetch_report, url)

        # Find or create vehicle
        vehicle = None
        if report.vin and "*" not in report.vin:
            vehicle = await self.db.find_vehicle_by_vin(report.vin)
        if not vehicle and report.vin:
            vehicle = await self.db.find_vehicle_by_vin_masked(report.vin)

        make, model = "", ""
        if report.make_model:
            parts = report.make_model.split("/", 1)
            make = parts[0].strip()
            model = parts[1].strip() if len(parts) > 1 else ""

        year = None
        if report.year:
            try:
                year = int(report.year)
            except ValueError:
                pass

        if vehicle:
            vehicle_id = vehicle["id"]
        else:
            vehicle_id = await self.db.create_vehicle(
                vin=report.vin if report.vin and "*" not in report.vin else None,
                vin_masked=report.vin or "",
                make=make,
                model=model,
                year=year,
            )

        session_id = await self.db.save_diagnostic_session(vehicle_id, report)
        fault_count = await self.db.save_fault_codes(
            session_id, vehicle_id, report.subsystems,
        )

        # Build summary
        lines = [
            f"Отчёт X431 загружен. Машина: {make} {model} {report.year}, "
            f"VIN: {report.vin}.",
            f"Найдено {fault_count} кодов ошибок "
            f"в {len(report.subsystems)} подсистемах:",
        ]
        for sub in report.subsystems:
            for fc in sub.fault_codes:
                lines.append(
                    f"- {sub.name}: {fc.code} — {fc.description} ({fc.status})"
                )

        return "\n".join(lines)
