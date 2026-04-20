"""Работа с PostgreSQL для бота-автоэлектрика.

Async-обёртка над psycopg 3:
- подключение к БД (одно соединение)
- CRUD для vehicles, diagnostic_sessions, fault_codes
- сохранение diagnosis_cases и agent_miscalls
- выполнение начальной миграции при старте
"""

import json
import logging
from dataclasses import asdict
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

MIGRATION_PATH = Path(__file__).parent.parent.parent / "migrations" / "001_initial.sql"


class Database:

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._conn: psycopg.AsyncConnection | None = None

    async def connect(self):
        self._conn = await psycopg.AsyncConnection.connect(
            self.dsn, row_factory=dict_row,
        )
        log.info("Connected to database")

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None
            log.info("Database connection closed")

    async def init_schema(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        await self._conn.execute(sql)
        await self._conn.commit()
        log.info("Database schema initialized")

    # --- vehicles ---

    async def find_vehicle_by_vin(self, vin: str) -> dict | None:
        cur = await self._conn.execute(
            "SELECT * FROM vehicles WHERE vin = %s LIMIT 1", (vin,),
        )
        return await cur.fetchone()

    async def find_vehicle_by_vin_masked(self, vin_masked: str) -> dict | None:
        cur = await self._conn.execute(
            "SELECT * FROM vehicles WHERE vin_masked = %s LIMIT 1", (vin_masked,),
        )
        return await cur.fetchone()

    async def create_vehicle(self, vin: str | None, vin_masked: str,
                             make: str, model: str, year: int | None) -> int:
        cur = await self._conn.execute(
            "INSERT INTO vehicles (vin, vin_masked, make, model, year) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (vin, vin_masked, make, model, year),
        )
        await self._conn.commit()
        row = await cur.fetchone()
        return row["id"]

    # --- diagnostic_sessions ---

    async def save_diagnostic_session(self, vehicle_id: int, report) -> int:
        raw = json.dumps(asdict(report), ensure_ascii=False, default=str)
        cur = await self._conn.execute(
            "INSERT INTO diagnostic_sessions "
            "(vehicle_id, report_code, report_type, diag_datetime, source_url, raw_report) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb) RETURNING id",
            (vehicle_id, report.report_code, report.report_type,
             report.diag_datetime, report.source_url, raw),
        )
        await self._conn.commit()
        row = await cur.fetchone()
        return row["id"]

    # --- fault_codes ---

    async def save_fault_codes(self, session_id: int, vehicle_id: int,
                               subsystems: list) -> int:
        count = 0
        for subsystem in subsystems:
            for fc in subsystem.fault_codes:
                await self._conn.execute(
                    "INSERT INTO fault_codes "
                    "(session_id, vehicle_id, code, description, status, subsystem_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (session_id, vehicle_id, fc.code, fc.description,
                     fc.status, subsystem.name),
                )
                count += 1
        await self._conn.commit()
        return count

    async def find_fault_code_history(self, code: str,
                                      vehicle_id: int | None = None) -> list[dict]:
        if vehicle_id is not None:
            cur = await self._conn.execute(
                "SELECT fc.code, fc.description, fc.status, fc.subsystem_name, "
                "ds.diag_datetime "
                "FROM fault_codes fc "
                "JOIN diagnostic_sessions ds ON ds.id = fc.session_id "
                "WHERE fc.code = %s AND fc.vehicle_id = %s "
                "ORDER BY ds.diag_datetime DESC",
                (code, vehicle_id),
            )
        else:
            cur = await self._conn.execute(
                "SELECT fc.code, fc.description, fc.status, fc.subsystem_name, "
                "ds.diag_datetime "
                "FROM fault_codes fc "
                "JOIN diagnostic_sessions ds ON ds.id = fc.session_id "
                "WHERE fc.code = %s "
                "ORDER BY ds.diag_datetime DESC",
                (code,),
            )
        return await cur.fetchall()

    # --- diagnosis_cases ---

    async def create_case(self, vehicle_id: int | None,
                          session_id: int | None, symptom: str,
                          telegram_thread_id: str | None = None) -> int:
        cur = await self._conn.execute(
            "INSERT INTO diagnosis_cases "
            "(vehicle_id, session_id, symptom, telegram_thread_id) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (vehicle_id, session_id, symptom, telegram_thread_id),
        )
        await self._conn.commit()
        row = await cur.fetchone()
        return row["id"]

    async def close_case(self, case_id: int, resolution: str,
                         confidence: str = "high") -> None:
        await self._conn.execute(
            "UPDATE diagnosis_cases "
            "SET resolution = %s, confidence = %s, status = 'closed', closed_at = now() "
            "WHERE id = %s",
            (resolution, confidence, case_id),
        )
        await self._conn.commit()

    async def get_open_cases(self) -> list[dict]:
        cur = await self._conn.execute(
            "SELECT * FROM diagnosis_cases WHERE status = 'open' "
            "ORDER BY created_at DESC",
        )
        return await cur.fetchall()

    async def update_hypotheses(self, case_id: int,
                                hypotheses: list[dict]) -> None:
        await self._conn.execute(
            "UPDATE diagnosis_cases SET hypotheses = %s::jsonb WHERE id = %s",
            (json.dumps(hypotheses, ensure_ascii=False), case_id),
        )
        await self._conn.commit()

    # --- agent_miscalls ---

    async def log_miscall(self, case_id: int, predicted: str,
                          actual: str, notes: str = "") -> int:
        cur = await self._conn.execute(
            "INSERT INTO agent_miscalls (case_id, predicted, actual, notes) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (case_id, predicted, actual, notes),
        )
        await self._conn.commit()
        row = await cur.fetchone()
        return row["id"]

    # --- stats ---

    async def get_cases_stats(self) -> dict:
        """Return aggregated stats over all cases."""
        cur = await self._conn.execute(
            "SELECT "
            "  COUNT(*) AS total, "
            "  COUNT(*) FILTER (WHERE status = 'open') AS open_count, "
            "  COUNT(*) FILTER (WHERE status = 'closed') AS closed_count, "
            "  COUNT(*) FILTER (WHERE status = 'closed' "
            "    AND resolution LIKE 'abandoned%%') AS abandoned_count "
            "FROM diagnosis_cases"
        )
        row = await cur.fetchone()
        return {
            "total": row["total"] or 0,
            "open": row["open_count"] or 0,
            "closed": row["closed_count"] or 0,
            "abandoned": row["abandoned_count"] or 0,
        }

    async def get_miscalls_stats(self, limit: int = 5) -> dict:
        """Return miscall count + last N miscalls joined with case info."""
        cur = await self._conn.execute(
            "SELECT COUNT(*) AS total FROM agent_miscalls"
        )
        row = await cur.fetchone()
        total = row["total"] or 0

        cur = await self._conn.execute(
            "SELECT m.id, m.case_id, m.actual, m.created_at, "
            "       c.symptom "
            "FROM agent_miscalls m "
            "LEFT JOIN diagnosis_cases c ON c.id = m.case_id "
            "ORDER BY m.created_at DESC "
            "LIMIT %s",
            (limit,),
        )
        recent = await cur.fetchall()
        return {"total": total, "recent": list(recent)}

    async def get_vehicles_count(self) -> int:
        cur = await self._conn.execute("SELECT COUNT(*) AS n FROM vehicles")
        row = await cur.fetchone()
        return row["n"] or 0

    async def get_sessions_count(self) -> int:
        cur = await self._conn.execute(
            "SELECT COUNT(*) AS n FROM diagnostic_sessions"
        )
        row = await cur.fetchone()
        return row["n"] or 0
