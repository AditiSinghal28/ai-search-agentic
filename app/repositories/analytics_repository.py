import re
from typing import Any

from sqlalchemy import bindparam, text

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
    "ai_search_query_logs",
    "ai_search_query_feedback",
}

SQL_KEYWORDS = {
    "select", "from", "join", "left", "right", "inner", "outer", "where", "group",
    "order", "limit", "having", "and", "or", "on", "as", "with", "union",
}

BLOCKED_SQL_PATTERNS = [
    r"\binsert\b", r"\bupdate\b", r"\bdelete\b", r"\bdrop\b", r"\balter\b",
    r"\btruncate\b", r"\bcreate\b", r"\breplace\b", r";", r"--",
]


class AnalyticsRepository:
    def __init__(self) -> None:
        if settings.auto_create_ai_tables:
            self.ensure_ai_tables()

    def _normalize_sql(self, sql: str) -> str:
        sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        sql = re.sub(r"\s+", " ", sql).strip()
        return sql

    def _extract_used_tables(self, sql: str) -> set[str]:
        used_tables: set[str] = set()
        pattern = re.compile(
            r"""
            (?:from|join)\s+
            (?:`?(?:[a-zA-Z_][a-zA-Z0-9_]*)`?\.)?
            `?([a-zA-Z_][a-zA-Z0-9_]*)`?
            """,
            flags=re.IGNORECASE | re.VERBOSE,
        )
        for match in pattern.finditer(sql):
            table_name = match.group(1).lower().strip()
            if table_name and table_name not in SQL_KEYWORDS:
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

    def _statement(self, sql: str, parameters: dict[str, Any]):
        stmt = text(sql)
        for key, value in parameters.items():
            if isinstance(value, (list, tuple, set)):
                stmt = stmt.bindparams(bindparam(key, expanding=True))
        return stmt

    def execute_select(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        self.validate_sql(sql)
        with engine.connect() as conn:
            rows = conn.execute(self._statement(sql, parameters), parameters).mappings().fetchmany(settings.max_sql_rows)
        return [dict(row) for row in rows]

    def ensure_ai_tables(self) -> None:
        self.ensure_query_log_table()
        self.ensure_feedback_table()

    def ensure_query_log_table(self) -> None:
        sql = """
        CREATE TABLE IF NOT EXISTS ai_search_query_logs (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            conversation_id VARCHAR(100) NULL,
            hospital_id BIGINT UNSIGNED NULL,
            query_text TEXT NOT NULL,
            normalized_query TEXT NULL,
            intent VARCHAR(100) NULL,
            semantic_score DECIMAL(8,5) NULL,
            semantic_method VARCHAR(50) NULL,
            matched_example TEXT NULL,
            plan_json JSON NULL,
            sql_text MEDIUMTEXT NULL,
            parameters_json JSON NULL,
            result_json JSON NULL,
            answer_text MEDIUMTEXT NULL,
            chart_hint_json JSON NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'started',
            error_text MEDIUMTEXT NULL,
            latency_ms INT UNSIGNED NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_ai_logs_conv_created (conversation_id, created_at),
            INDEX idx_ai_logs_hospital_created (hospital_id, created_at),
            INDEX idx_ai_logs_intent_created (intent, created_at),
            INDEX idx_ai_logs_status_created (status, created_at)
        )
        """
        with engine.begin() as conn:
            conn.execute(text(sql))

    def ensure_feedback_table(self) -> None:
        sql = """
        CREATE TABLE IF NOT EXISTS ai_search_query_feedback (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            query_log_id BIGINT UNSIGNED NOT NULL,
            hospital_id BIGINT UNSIGNED NULL,
            is_helpful TINYINT(1) NULL,
            corrected_intent VARCHAR(100) NULL,
            corrected_answer MEDIUMTEXT NULL,
            feedback_text TEXT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_ai_feedback_log (query_log_id),
            INDEX idx_ai_feedback_hospital_created (hospital_id, created_at)
        )
        """
        with engine.begin() as conn:
            conn.execute(text(sql))

    def create_query_log(
        self,
        *,
        hospital_id: int,
        query_text: str,
        normalized_query: str | None = None,
        conversation_id: str | None = None,
    ) -> int | None:
        try:
            sql = """
            INSERT INTO ai_search_query_logs (conversation_id, hospital_id, query_text, normalized_query, status)
            VALUES (:conversation_id, :hospital_id, :query_text, :normalized_query, 'started')
            """
            with engine.begin() as conn:
                result = conn.execute(text(sql), {
                    "conversation_id": conversation_id,
                    "hospital_id": hospital_id,
                    "query_text": query_text,
                    "normalized_query": normalized_query,
                })
                return int(result.lastrowid or 0)
        except Exception:
            return None

    def update_query_log(self, log_id: int | None, **fields: Any) -> None:
        if not log_id or not fields:
            return
        allowed = {
            "conversation_id", "normalized_query", "intent", "semantic_score", "semantic_method", "matched_example",
            "plan_json", "sql_text", "parameters_json", "result_json", "answer_text",
            "chart_hint_json", "status", "error_text", "latency_ms",
        }
        safe_fields = {k: v for k, v in fields.items() if k in allowed}
        if not safe_fields:
            return
        assignments = ", ".join(f"{key} = :{key}" for key in safe_fields)
        params = dict(safe_fields)
        params["id"] = log_id
        try:
            with engine.begin() as conn:
                conn.execute(text(f"UPDATE ai_search_query_logs SET {assignments} WHERE id = :id"), params)
        except Exception:
            return

    def insert_feedback(
        self,
        *,
        query_log_id: int,
        hospital_id: int | None = None,
        is_helpful: bool | None = None,
        corrected_intent: str | None = None,
        corrected_answer: str | None = None,
        feedback_text: str | None = None,
    ) -> int:
        sql = """
        INSERT INTO ai_search_query_feedback (
            query_log_id, hospital_id, is_helpful, corrected_intent, corrected_answer, feedback_text
        ) VALUES (
            :query_log_id, :hospital_id, :is_helpful, :corrected_intent, :corrected_answer, :feedback_text
        )
        """
        with engine.begin() as conn:
            result = conn.execute(text(sql), {
                "query_log_id": query_log_id,
                "hospital_id": hospital_id,
                "is_helpful": None if is_helpful is None else int(is_helpful),
                "corrected_intent": corrected_intent,
                "corrected_answer": corrected_answer,
                "feedback_text": feedback_text,
            })
            return int(result.lastrowid or 0)

    def list_recent_logs(self, hospital_id: int, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        clauses = ["hospital_id = :hospital_id"]
        params: dict[str, Any] = {"hospital_id": hospital_id, "limit": limit}
        if status:
            clauses.append("status = :status")
            params["status"] = status
        sql = f"""
        SELECT id, conversation_id, query_text, normalized_query, intent, semantic_score,
               semantic_method, matched_example, status, error_text, latency_ms, created_at
        FROM ai_search_query_logs
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at DESC
        LIMIT :limit
        """
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().fetchall()
        return [dict(row) for row in rows]

    def get_last_successful_log(self, *, hospital_id: int, conversation_id: str) -> dict[str, Any] | None:
        sql = """
        SELECT id, query_text, normalized_query, intent, plan_json, answer_text, created_at
        FROM ai_search_query_logs
        WHERE hospital_id = :hospital_id
          AND conversation_id = :conversation_id
          AND status = 'success'
        ORDER BY created_at DESC
        LIMIT 1
        """
        with engine.connect() as conn:
            row = conn.execute(text(sql), {"hospital_id": hospital_id, "conversation_id": conversation_id}).mappings().first()
        return dict(row) if row else None

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
