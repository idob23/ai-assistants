"""Хелпер для обработки URL отчётов X431."""

import asyncio
import logging

from bots.autoelectric.x431_parser import X431ReportParser

log = logging.getLogger(__name__)


async def handle_x431_url(bot, message, url: str):
    """Parse X431 report, save to DB, reply with summary, add to history."""
    parser = X431ReportParser()
    report = await asyncio.to_thread(parser.fetch_report, url)

    # Find or create vehicle
    vehicle = await bot.db.find_vehicle_by_vin(report.vin) if report.vin else None
    if not vehicle and report.vin:
        vehicle = await bot.db.find_vehicle_by_vin_masked(report.vin)

    make, model = "", ""
    if report.make_model:
        parts = report.make_model.replace("_", " ").split("/", 1)
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
        vehicle_id = await bot.db.create_vehicle(
            vin=report.vin,
            vin_masked=report.vin or "",
            make=make,
            model=model,
            year=year,
        )

    session_id = await bot.db.save_diagnostic_session(vehicle_id, report)
    fault_count = await bot.db.save_fault_codes(
        session_id, vehicle_id, report.subsystems,
    )

    # Build summary
    lines = [
        "Отчёт X431 загружен.",
        f"Машина: {make} {model} {report.year}, VIN: {report.vin}",
        f"Дата диагностики: {report.diag_datetime}",
        f"Найдено {fault_count} кодов ошибок "
        f"в {len(report.subsystems)} подсистемах:",
    ]
    shown = 0
    for sub in report.subsystems:
        for fc in sub.fault_codes:
            if shown >= 5:
                break
            lines.append(
                f"- {sub.name}: {fc.code} — {fc.description} ({fc.status})"
            )
            shown += 1
        if shown >= 5:
            break
    if fault_count > 5:
        lines.append(f"... и ещё {fault_count - 5}")

    summary = "\n".join(lines)
    await bot.reply(message, summary)

    chat_id = message.chat.id
    bot.get_history(chat_id).add_user(f"Отчёт X431: {summary}")
