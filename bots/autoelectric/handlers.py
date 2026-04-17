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
    for sub in report.subsystems:
        for fc in sub.fault_codes:
            lines.append(
                f"- {sub.name}: {fc.code} — {fc.description} ({fc.status})"
            )

    summary = "\n".join(lines)
    await bot.reply(message, summary)

    chat_id = message.chat.id
    history = bot.get_history(chat_id)
    user_turn = (message.text or "").strip() or url
    history.add_user(user_turn)
    history.add_assistant(summary)

    # Auto-analysis: ask model to comment on the codes
    analysis_prompt = (
        "Проанализируй эти коды ошибок. Сгруппируй их по системам, "
        "определи наиболее вероятную общую причину и дай 2-3 гипотезы "
        "с критериями проверки. Формат гипотез как в system_prompt."
    )
    history.add_user(analysis_prompt)
    try:
        response = await bot.claude_client.chat(
            messages=history.get_messages(),
            system=bot.system_prompt,
        )
        analysis_text = "".join(
            b.text for b in response.content if hasattr(b, "text")
        ).strip()
        stop_reason = getattr(response, "stop_reason", None)
        log.info(
            "X431 auto-analysis: stop_reason=%s, text_len=%d, output_tokens=%d",
            stop_reason,
            len(analysis_text),
            response.usage.output_tokens,
        )
        if not analysis_text:
            log.warning("X431 auto-analysis produced empty text — skipping reply")
            history.messages.pop()  # rollback analysis_prompt
            return
        history.add_assistant(analysis_text)
        await bot.reply(message, analysis_text)
    except Exception as exc:
        history.messages.pop()  # rollback user-turn
        log.error("Claude analysis after X431 failed: %s", exc, exc_info=True)
