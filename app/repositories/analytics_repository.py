import re
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import engine

ALLOWED_TABLES = {
    "bookings",
    "booking_treatments",
    "caseentries",
    "doctors",
    "hospital_financials",
    "investigations",
    "medicines",
    "patients",
    "patient_billing_entries",
    "prescriptions",
    "prescription_items",
    "procedures",
    "schedules",
    "specializations",
    "treatments",
    "users",
}

SQL_KEYWORDS = {
    "select",
    "from",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "where",
    "group",
    "order",
    "limit",
    "having",
    "and",
    "or",
    "on",
    "as",
    "with",
    "union",
}

BLOCKED_SQL_PATTERNS = [
    r"\binsert\b",
    r"\bupdate\b",
    r"\bdelete\b",
    r"\bdrop\b",
    r"\balter\b",
    r"\btruncate\b",
    r"\bcreate\b",
    r"\breplace\b",
    r";",
    r"--",
]


class AnalyticsRepository:
    def _normalize_sql(self, sql: str) -> str:
        sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        sql = re.sub(r"\s+", " ", sql).strip()
        return sql

    def _extract_used_tables(self, sql: str) -> set[str]:
        used_tables: set[str] = set()

        pattern = re.compile(
            r"""
            (?:from|join)\s+
            (?:
                `?(?:[a-zA-Z_][a-zA-Z0-9_]*)`?\.
            )?
            `?([a-zA-Z_][a-zA-Z0-9_]*)`?
            """,
            flags=re.IGNORECASE | re.VERBOSE,
        )

        for match in pattern.finditer(sql):
            table_name = match.group(1).lower().strip()
            if not table_name:
                continue
            if table_name in SQL_KEYWORDS:
                continue
            used_tables.add(table_name)

        return used_tables

    def validate_sql(self, sql: str) -> None:
        normalized = self._normalize_sql(sql)
        lowered = normalized.lower()

        if not lowered:
            raise ValueError("Generated SQL is empty.")

        if not (lowered.startswith("select") or lowered.startswith("with")):
            raise ValueError("Only SELECT queries are allowed.")

        for pattern in BLOCKED_SQL_PATTERNS:
            if re.search(pattern, lowered, flags=re.IGNORECASE):
                raise ValueError(f"Blocked SQL pattern detected: {pattern}")

        used_tables = self._extract_used_tables(lowered)
        unknown = used_tables - ALLOWED_TABLES
        if unknown:
            raise ValueError(f"Unknown or blocked tables used: {sorted(unknown)}")

    def execute_select(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        self.validate_sql(sql)
        with engine.connect() as conn:
            rows = conn.execute(text(sql), parameters).mappings().fetchmany(settings.max_sql_rows)
        return [dict(row) for row in rows]

    def list_medicines(self, hospital_id: int) -> list[dict[str, Any]]:
        sql = """
        SELECT id, hospital_id, name, unit, dosage, price, stock, description
        FROM medicines
        WHERE hospital_id = :hospital_id
        ORDER BY name ASC
        """
        return self.execute_select(sql, {"hospital_id": hospital_id})

    def list_doctors(self, hospital_id: int) -> list[dict[str, Any]]:
        sql = """
        SELECT id, hospital_id, name, doctor_code, qualification, phone, gender, experience_years
        FROM doctors
        WHERE hospital_id = :hospital_id
        ORDER BY name ASC
        """
        return self.execute_select(sql, {"hospital_id": hospital_id})

    def list_treatments(self, hospital_id: int) -> list[dict[str, Any]]:
        sql = """
        SELECT id, hospital_id, name, code, category, base_price, is_active
        FROM treatments
        WHERE hospital_id = :hospital_id
        ORDER BY name ASC
        """
        return self.execute_select(sql, {"hospital_id": hospital_id})