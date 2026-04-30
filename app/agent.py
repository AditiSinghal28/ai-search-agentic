from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.config import settings
from app.llm_client import LLMClient
from app.prompts import SCHEMA_SUMMARY, SQL_GENERATOR_PROMPT
from app.repositories.analytics_repository import AnalyticsRepository
from app.schemas import PlannerOutput, QueryRequest, QueryResponse, SqlGenerationOutput
from app.services.date_parser import detect_date_range, extract_weekday
from app.services.intent_catalog import INTENT_CATALOG
from app.services.intent_matcher import IntentMatcher
from app.services.treatment_matcher import EntityMatcher

VALID_BOOKING_STATUSES = {
    "unverified",
    "pending",
    "accepted",
    "rejected",
    "cancelled",
    "no_show",
    "rescheduled",
    "completed",
}

# treatments.category enum in your DB:
# ('consultation', 'treatment', 'operation', 'other')
VALID_GROUP_TYPES = {"consultation", "treatment", "operation", "other"}


class SearchAgent:
    def __init__(self) -> None:
        self.repo = AnalyticsRepository()
        self.llm = LLMClient()
        self.matcher = EntityMatcher(self.repo)
        self.intent_matcher = IntentMatcher()

    def _today(self, payload: QueryRequest) -> date:
        return date.fromisoformat(payload.today) if payload.today else date.today()

    def _normalized_query(self, query: str) -> str:
        q = query.lower().strip()
        q = re.sub(r"\s+", " ", q)
        return q

    def _extract_status(self, query: str) -> str | None:
        q = self._normalized_query(query)
        for status in VALID_BOOKING_STATUSES:
            human = status.replace("_", " ")
            if re.search(rf"\b{re.escape(human)}\b", q):
                return status
        if "no show" in q:
            return "no_show"
        return None

    def _extract_top_n(self, query: str, default: int = 5) -> int:
        match = re.search(r"\btop\s+(\d+)\b", query.lower())
        return int(match.group(1)) if match else default

    def _extract_medicine_name(self, query: str) -> str | None:
        q = query.strip()

        patterns = [
            r"(?:stock of|stock for|current stock of|current stock for|check stock of|check stock for)\s+(.+)$",
            r"(?:how much stock is left for)\s+(.+)$",
            r"(?:show me medicine stock for)\s+(.+)$",
        ]

        for pattern in patterns:
            match = re.search(pattern, q, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(" ?.")

        return None

    def _extract_doctor_name(self, query: str) -> str | None:
        q = query.strip()
        patterns = [
            r"doctor\s+([a-zA-Z .'-]+)",
            r"dr\.?\s+([a-zA-Z .'-]+)",
            r"schedule\s+for\s+([a-zA-Z .'-]+)",
            r"working hours of\s+([a-zA-Z .'-]+)",
            r"available\s+for\s+([a-zA-Z .'-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, q, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(" ?.")
        return None

    def _format_time_value(self, value: Any) -> str:
        if value is None:
            return ""

        text = str(value).strip()

        # Handles weird ISO-like duration strings if they ever appear
        match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", text)
        if match:
            h = int(match.group(1) or 0)
            m = int(match.group(2) or 0)
            s = int(match.group(3) or 0)
            return f"{h:02d}:{m:02d}" if s == 0 else f"{h:02d}:{m:02d}:{s:02d}"

        # Normal SQL TIME values like 10:00:00
        time_match = re.fullmatch(r"(\d{2}):(\d{2})(?::\d{2})?", text)
        if time_match:
            return f"{time_match.group(1)}:{time_match.group(2)}"

        return text

    def _build_plan(self, payload: QueryRequest) -> PlannerOutput:
        normalized_q = self.intent_matcher.normalize(payload.query)
        today = self._today(payload)
        date_from, date_to, date_label = detect_date_range(normalized_q, today)
        weekday = extract_weekday(normalized_q)
        status = self._extract_status(normalized_q)

        def plan(intent: str, tables: list[str], answer_style: str = "summary", **entities: Any) -> PlannerOutput:
            return PlannerOutput(
                intent=intent,
                date_from=date_from.isoformat() if date_from else None,
                date_to=date_to.isoformat() if date_to else None,
                needs_time_split=False,
                needs_comparison=False,
                relevant_tables=tables,
                reasoning_summary=f"Detected {intent} intent from natural-language query.",
                answer_style=answer_style,  # type: ignore[arg-type]
                sql_notes=[],
                entities={
                    "date_label": date_label,
                    "weekday": weekday,
                    "status": status,
                    **entities,
                },
            )

        q = normalized_q

        # Deterministic rules first

        if any(p in q for p in ["stock of", "stock for", "current stock", "medicine stock", "check stock"]):
            medicine_text = self._extract_medicine_name(payload.query)
            return plan("medicine_stock", ["medicines"], "single_value", medicine_text=medicine_text)

        if any(p in q for p in [
            "how many medicines do we have in stock",
            "how many medicines are available",
            "count medicines in stock",
            "count medicines in inventory",
            "how many stocked medicines are there",
            "how many medicine items do we currently have",
        ]):
            return plan("medicine_inventory_count", ["medicines"], "single_value")

        if any(p in q for p in [
            "total medicine stock",
            "sum of all medicine stock",
            "total stock across medicines",
            "how much stock do we have across all medicines",
        ]):
            return plan("medicine_stock_total", ["medicines"], "single_value")

        if "prescription" in q and any(p in q for p in ["how many", "count", "written", "number of"]):
            return plan("prescription_count", ["prescriptions"], "single_value")

        if any(p in q for p in ["unpaid billing", "unpaid bill", "unpaid entries", "unpaid billing entries", "unpaid bills"]):
            return plan("unpaid_billing_count", ["patient_billing_entries"], "single_value")

        if "patient" in q and any(p in q for p in ["registered", "registration", "new patients"]):
            return plan("patient_count", ["patients"], "single_value")

        if ("top" in q and "treatment" in q and "revenue" in q) or ("treatments by revenue" in q):
            return plan(
                "top_treatments_by_revenue",
                ["patient_billing_entries", "treatments"],
                "list",
                top_n=self._extract_top_n(q, 5),
            )

        if "doctor" in q and any(p in q for p in ["most appointments", "most booking", "highest appointments", "highest bookings", "busiest doctor"]):
            return plan("doctor_most_appointments", ["bookings", "doctors"], "comparison")

        if any(p in q for p in ["show me", "list", "display"]) and (status is not None or "booking" in q or "appointment" in q):
            return plan("booking_list", ["bookings", "doctors"], "list")

        if ("time slot" in q and "busiest" in q) or ("slot is busiest" in q):
            return plan("busiest_time_slot", ["bookings"], "single_value")

        if any(p in q for p in ["morning", "noon", "evening"]) and any(p in q for p in ["how many bookings", "bookings are there", "split"]):
            return plan("busiest_day_part", ["bookings"], "comparison")

        if "revenue" in q or "income" in q or "earnings" in q:
            treatment_text = self._extract_treatment_text(payload.query)
            if treatment_text:
                return plan(
                    "revenue_by_treatment",
                    ["patient_billing_entries", "treatments"],
                    "single_value",
                    treatment_text=treatment_text,
                )

        if any(p in q for p in ["revenue", "income", "earnings", "collections"]):
            return plan("revenue_total", ["patient_billing_entries"], "single_value")

        if any(p in q for p in ["how many bookings", "bookings are there", "booking count", "count bookings", "count booking"]):
            return plan("booking_count", ["bookings"], "single_value")

        if weekday and "busiest" in q:
            return plan("busiest_time_slot", ["bookings"], "single_value")

        if any(p in q for p in ["schedule", "available", "working hours"]) and ("doctor" in q or "dr" in q):
            return plan("schedule_lookup", ["schedules", "doctors"], "list")

        # Semantic matcher second

        matched = self.intent_matcher.match(payload.query)
        if matched:
            matched_intent = matched["intent"]
            meta = INTENT_CATALOG.get(matched_intent)
            if meta:
                extra_entities: dict[str, Any] = {
                    "semantic_score": matched["score"],
                    "semantic_method": matched["method"],
                    "semantic_example": matched["matched_example"],
                }
                if matched_intent == "top_treatments_by_revenue":
                    extra_entities["top_n"] = self._extract_top_n(payload.query, 5)

                return plan(
                    matched_intent,
                    list(meta["tables"]),
                    str(meta["answer_style"]),
                    **extra_entities,
                )

        # LLM fallback last

        return plan(
            "generic_sql",
            ["bookings", "patient_billing_entries", "doctors", "treatments", "patients", "prescriptions", "medicines"],
            "summary",
        )

    def _generate_sql_from_plan(self, payload: QueryRequest, plan: PlannerOutput) -> SqlGenerationOutput:
        e = plan.entities
        params: dict[str, Any] = {"hospital_id": payload.hospital_id}

        if plan.date_from and plan.date_to:
            params["date_from"] = plan.date_from
            params["date_to"] = plan.date_to

        if plan.intent == "booking_count":
            clauses = ["b.hospital_id = :hospital_id"]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(b.booking_date) BETWEEN :date_from AND :date_to")
            if e.get("weekday"):
                params["weekday"] = str(e["weekday"]).capitalize()
                clauses.append("DAYNAME(b.booking_date) = :weekday")
            if e.get("status"):
                params["status"] = e["status"]
                clauses.append("b.status = :status")

            sql = f"SELECT COUNT(*) AS booking_count FROM bookings b WHERE {' AND '.join(clauses)}"
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Booking count query.")

        if plan.intent == "booking_list":
            clauses = ["b.hospital_id = :hospital_id"]
            if e.get("status"):
                params["status"] = e["status"]
                clauses.append("b.status = :status")
            if plan.date_from and plan.date_to:
                clauses.append("DATE(b.booking_date) BETWEEN :date_from AND :date_to")
            if e.get("weekday"):
                params["weekday"] = str(e["weekday"]).capitalize()
                clauses.append("DAYNAME(b.booking_date) = :weekday")

            sql = f"""
            SELECT b.id, b.booking_date, b.start_time, b.end_time, b.patient_name, b.patient_phone, b.status,
                   d.name AS doctor_name, b.cause
            FROM bookings b
            LEFT JOIN doctors d ON d.id = b.doctor_id AND d.hospital_id = b.hospital_id
            WHERE {' AND '.join(clauses)}
            ORDER BY b.booking_date ASC, b.start_time ASC
            LIMIT 100
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Booking list query.")

        if plan.intent == "revenue_by_treatment":
            treatment_text = str(e.get("treatment_text") or "").strip()
            if not treatment_text:
                raise ValueError("Treatment name could not be extracted from the query.")

            matched = self.matcher.match_treatment(payload.hospital_id, treatment_text)

            clauses = [
                "pbe.hospital_id = :hospital_id",
                "pbe.is_paid = 1",
                "pbe.paid_at IS NOT NULL",
                "pbe.type IN ('consultation', 'treatment', 'operation', 'medicine')",
            ]

            if plan.date_from and plan.date_to:
                clauses.append("DATE(pbe.paid_at) BETWEEN :date_from AND :date_to")

            if matched:
                params["treatment_id"] = int(matched["id"])
                plan.entities["matched_treatment"] = matched
                clauses.append("pbe.treatment_id = :treatment_id")

                sql = f"""
                SELECT COALESCE(SUM(pbe.amount), 0) AS total_revenue,
                       COUNT(*) AS paid_entries_count,
                       MAX(t.name) AS treatment_name
                FROM patient_billing_entries pbe
                LEFT JOIN treatments t
                  ON t.id = pbe.treatment_id
                 AND t.hospital_id = pbe.hospital_id
                WHERE {' AND '.join(clauses)}
                """
                return SqlGenerationOutput(
                    sql=sql,
                    parameters=params,
                    chart_hint={"metric": "currency", "key": "total_revenue"},
                    explanation="Revenue filtered by matched treatment.",
                )

            # fallback: try matching by billing description text
            params["treatment_search"] = f"%{treatment_text}%"
            clauses.append("(LOWER(pbe.description) LIKE LOWER(:treatment_search) OR LOWER(pbe.type) LIKE LOWER(:treatment_search))")

            sql = f"""
            SELECT COALESCE(SUM(pbe.amount), 0) AS total_revenue,
                   COUNT(*) AS paid_entries_count,
                   :raw_treatment_text AS treatment_name
            FROM patient_billing_entries pbe
            WHERE {' AND '.join(clauses)}
            """
            params["raw_treatment_text"] = treatment_text

            return SqlGenerationOutput(
                sql=sql,
                parameters=params,
                chart_hint={"metric": "currency", "key": "total_revenue"},
                explanation="Revenue filtered by billing description fallback.",
            )

        if plan.intent == "revenue_total":
            clauses = [
                "hospital_id = :hospital_id",
                "is_paid = 1",
                "paid_at IS NOT NULL",
                "type IN ('consultation', 'medicine', 'treatment', 'operation', 'custom_profit')",
            ]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(paid_at) BETWEEN :date_from AND :date_to")

            sql = f"""
            SELECT COALESCE(SUM(amount), 0) AS total_revenue,
                   COUNT(*) AS paid_entries_count
            FROM patient_billing_entries
            WHERE {' AND '.join(clauses)}
            """
            return SqlGenerationOutput(
                sql=sql,
                parameters=params,
                chart_hint={"metric": "currency", "key": "total_revenue"},
                explanation="Revenue from paid billing entries.",
            )

        if plan.intent == "doctor_most_appointments":
            clauses = ["b.hospital_id = :hospital_id", "b.status IN :active_statuses"]
            params["active_statuses"] = tuple(settings.active_booking_statuses)
            if plan.date_from and plan.date_to:
                clauses.append("DATE(b.booking_date) BETWEEN :date_from AND :date_to")

            sql = f"""
            SELECT d.id AS doctor_id, d.name AS doctor_name, COUNT(*) AS appointment_count
            FROM bookings b
            JOIN doctors d ON d.id = b.doctor_id AND d.hospital_id = b.hospital_id
            WHERE {' AND '.join(clauses)}
            GROUP BY d.id, d.name
            ORDER BY appointment_count DESC, d.name ASC
            LIMIT 1
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Doctor leaderboard by appointments.")

        if plan.intent == "top_treatments_by_revenue":
            params["top_n"] = int(e.get("top_n", 5))
            clauses = [
                "pbe.hospital_id = :hospital_id",
                "pbe.is_paid = 1",
                "pbe.paid_at IS NOT NULL",
                "pbe.type IN ('consultation', 'treatment', 'operation', 'medicine')",
            ]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(pbe.paid_at) BETWEEN :date_from AND :date_to")

            sql = f"""
            SELECT COALESCE(t.name, pbe.description, pbe.type) AS treatment_name,
                   COALESCE(SUM(pbe.amount), 0) AS total_revenue,
                   COUNT(*) AS paid_entries_count
            FROM patient_billing_entries pbe
            LEFT JOIN treatments t
              ON t.id = pbe.treatment_id
             AND t.hospital_id = pbe.hospital_id
            WHERE {' AND '.join(clauses)}
            GROUP BY COALESCE(t.name, pbe.description, pbe.type)
            ORDER BY total_revenue DESC, treatment_name ASC
            LIMIT :top_n
            """
            return SqlGenerationOutput(
                sql=sql,
                parameters=params,
                chart_hint={"type": "bar", "x": "treatment_name", "y": "total_revenue"},
                explanation="Top treatments by revenue query.",
            )

        if plan.intent == "patient_count":
            clauses = ["1 = 1"]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(created_at) BETWEEN :date_from AND :date_to")

            sql = f"SELECT COUNT(*) AS patient_count FROM patients WHERE {' AND '.join(clauses)}"
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Patient registrations query.")

        if plan.intent == "busiest_day_part":
            clauses = ["hospital_id = :hospital_id"]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(booking_date) BETWEEN :date_from AND :date_to")
            if e.get("weekday"):
                params["weekday"] = str(e["weekday"]).capitalize()
                clauses.append("DAYNAME(booking_date) = :weekday")

            sql = f"""
            SELECT CASE
                     WHEN TIME(start_time) < '12:00:00' THEN 'morning'
                     WHEN TIME(start_time) < '17:00:00' THEN 'noon'
                     ELSE 'evening'
                   END AS day_part,
                   COUNT(*) AS booking_count
            FROM bookings
            WHERE {' AND '.join(clauses)}
            GROUP BY day_part
            ORDER BY booking_count DESC, day_part ASC
            """
            return SqlGenerationOutput(
                sql=sql,
                parameters=params,
                chart_hint={"type": "bar", "x": "day_part", "y": "booking_count"},
                explanation="Booking split by day part query.",
            )

        if plan.intent == "busiest_time_slot":
            clauses = ["hospital_id = :hospital_id"]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(booking_date) BETWEEN :date_from AND :date_to")
            if e.get("weekday"):
                params["weekday"] = str(e["weekday"]).capitalize()
                clauses.append("DAYNAME(booking_date) = :weekday")

            sql = f"""
            SELECT TIME_FORMAT(start_time, '%H:%i') AS time_slot, COUNT(*) AS booking_count
            FROM bookings
            WHERE {' AND '.join(clauses)}
            GROUP BY TIME_FORMAT(start_time, '%H:%i')
            ORDER BY booking_count DESC, time_slot ASC
            LIMIT 1
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Busiest time slot query.")

        if plan.intent == "unpaid_billing_count":
            clauses = ["hospital_id = :hospital_id", "COALESCE(is_paid, 0) = 0"]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(created_at) BETWEEN :date_from AND :date_to")

            sql = f"""
            SELECT COUNT(*) AS unpaid_billing_count,
                   COALESCE(SUM(amount), 0) AS unpaid_amount
            FROM patient_billing_entries
            WHERE {' AND '.join(clauses)}
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Unpaid billing query.")

        if plan.intent == "prescription_count":
            clauses = ["hospital_id = :hospital_id"]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(created_at) BETWEEN :date_from AND :date_to")

            sql = f"""
            SELECT COUNT(*) AS prescription_count
            FROM prescriptions
            WHERE {' AND '.join(clauses)}
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Prescription count query.")

        if plan.intent == "medicine_stock":
            medicine_text = str(e.get("medicine_text") or "").strip()
            if not medicine_text:
                raise ValueError("Medicine name could not be extracted from the query.")

            matched = self.matcher.match_medicine(payload.hospital_id, medicine_text)
            if not matched:
                raise ValueError(f"No matching medicine found for '{medicine_text}'.")

            params["medicine_id"] = int(matched["id"])
            plan.entities["matched_medicine"] = matched

            sql = """
            SELECT id, name, stock, unit, dosage, price, description
            FROM medicines
            WHERE hospital_id = :hospital_id AND id = :medicine_id
            LIMIT 1
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Medicine stock lookup query.")

        if plan.intent == "medicine_inventory_count":
            sql = """
            SELECT COUNT(*) AS medicine_count
            FROM medicines
            WHERE hospital_id = :hospital_id
              AND COALESCE(stock, 0) > 0
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Medicine inventory count query.")

        if plan.intent == "medicine_stock_total":
            sql = """
            SELECT COALESCE(SUM(stock), 0) AS total_stock
            FROM medicines
            WHERE hospital_id = :hospital_id
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Medicine stock total query.")

        if plan.intent == "schedule_lookup":
            doctor_text = self._extract_doctor_name(payload.query)
            if not doctor_text:
                raise ValueError("Doctor name could not be extracted from the query.")

            matched = self.matcher.match_doctor(payload.hospital_id, doctor_text)
            if not matched:
                raise ValueError(f"No matching doctor found for '{doctor_text}'.")

            params["doctor_id"] = int(matched["id"])
            plan.entities["matched_doctor"] = matched

            sql = """
            SELECT s.day, s.start_time, s.end_time, s.is_off, d.name AS doctor_name
            FROM schedules s
            JOIN doctors d ON d.id = s.doctor_id
            WHERE d.hospital_id = :hospital_id
              AND s.doctor_id = :doctor_id
            ORDER BY FIELD(LOWER(s.day), 'monday','tuesday','wednesday','thursday','friday','saturday','sunday')
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Doctor schedule lookup query.")

        return self._generate_sql_via_llm(payload, plan)

    def _generate_sql_via_llm(self, payload: QueryRequest, plan: PlannerOutput) -> SqlGenerationOutput:
        prompt_payload: dict[str, Any] = {
            "query": payload.query,
            "hospital_id": payload.hospital_id,
            "today": self._today(payload).isoformat(),
            "plan": plan.model_dump(),
            "active_booking_statuses": list(settings.active_booking_statuses),
        }

        try:
            raw = self.llm.chat_json(
                system_prompt=f"{SCHEMA_SUMMARY}\n\n{SQL_GENERATOR_PROMPT}",
                user_prompt=json.dumps(prompt_payload, ensure_ascii=False, indent=2),
            )
            generated = SqlGenerationOutput(**raw)
        except (RuntimeError, ValidationError, Exception) as exc:
            raise ValueError(f"Could not reliably generate SQL for this query yet: {exc}") from exc

        clean_params = {}
        for key, value in generated.parameters.items():
            clean_params[str(key).lstrip(":").strip()] = value

        generated.parameters = clean_params
        generated.parameters.setdefault("hospital_id", payload.hospital_id)
        if plan.date_from:
            generated.parameters.setdefault("date_from", plan.date_from)
        if plan.date_to:
            generated.parameters.setdefault("date_to", plan.date_to)

        return generated

    def _answer_from_rows(self, payload: QueryRequest, plan: PlannerOutput, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "I could not find matching data for that query in the medical system."

        first = rows[0]
        currency = settings.default_currency
        label = plan.entities.get("date_label")
        weekday = plan.entities.get("weekday")
        status = plan.entities.get("status")

        if plan.intent == "booking_count":
            count = first.get("booking_count", 0)
            parts = []
            if status:
                parts.append(str(status).replace("_", " "))
            parts.append("bookings")
            subject = " ".join(parts)

            if label:
                if str(label).startswith("last ") or label == "yesterday":
                    return f"There were {count} {subject} {label}."
                return f"There are {count} {subject} for {label}."

            if weekday:
                return f"There are {count} {subject} for {str(weekday).capitalize()}."

            return f"There are {count} {subject}."

        if plan.intent == "booking_list":
            header_parts = []
            if status:
                header_parts.append(str(status).replace("_", " "))
            header_parts.append("bookings")
            header = " ".join(header_parts)

            if label:
                intro = f"I found {len(rows)} {header} for {label}."
            elif weekday:
                intro = f"I found {len(rows)} {header} for {str(weekday).capitalize()}."
            else:
                intro = f"I found {len(rows)} {header}."

            lines = []
            for row in rows[:10]:
                start_time = self._format_time_value(row.get("start_time"))
                end_time = self._format_time_value(row.get("end_time"))
                lines.append(
                    f"- #{row.get('id')}: {row.get('patient_name')} with {row.get('doctor_name')} on "
                    f"{row.get('booking_date')} from {start_time} to {end_time} ({row.get('status')})"
                )

            if len(rows) > 10:
                lines.append(f"...and {len(rows) - 10} more.")

            return intro + "\n" + "\n".join(lines)
        
        if plan.intent == "revenue_by_treatment":
            treatment_name = (
                (plan.entities.get("matched_treatment") or {}).get("name")
                or first.get("treatment_name")
                or plan.entities.get("treatment_text")
                or "that treatment"
            )
            return (
                f"Total revenue from {treatment_name} is "
                f"{currency} {first.get('total_revenue', 0)} "
                f"from {first.get('paid_entries_count', 0)} paid entries."
            )

        if plan.intent == "revenue_total":
            return (
                f"Total revenue{f' for {label}' if label else ''} is "
                f"{currency} {first.get('total_revenue', 0)} from {first.get('paid_entries_count', 0)} paid entries."
            )

        if plan.intent == "doctor_most_appointments":
            return (
                f"{first.get('doctor_name')} has the most appointments"
                f"{f' for {label}' if label else ''} with {first.get('appointment_count')} bookings."
            )

        if plan.intent == "top_treatments_by_revenue":
            lines = []
            for idx, row in enumerate(rows, start=1):
                lines.append(
                    f"{idx}. {row.get('treatment_name')} — {currency} {row.get('total_revenue')} "
                    f"from {row.get('paid_entries_count')} paid entries"
                )
            return "Top treatments by revenue:\n" + "\n".join(lines)

        if plan.intent == "patient_count":
            count = first.get("patient_count", 0)
            if label:
                return f"{count} patients were registered {label}."
            return f"{count} patients were registered."

        if plan.intent == "busiest_day_part":
            parts = ", ".join(f"{row.get('day_part')}: {row.get('booking_count')}" for row in rows)
            if label:
                return f"The booking split for {label} is {parts}."
            return f"The booking split is {parts}."

        if plan.intent == "busiest_time_slot":
            slot = self._format_time_value(first.get("time_slot"))
            if weekday:
                return f"The busiest time slot on {str(weekday).capitalize()} is {slot} with {first.get('booking_count')} bookings."
            return f"The busiest time slot is {slot} with {first.get('booking_count')} bookings."

        if plan.intent == "unpaid_billing_count":
            count = first.get("unpaid_billing_count", 0)
            amount = first.get("unpaid_amount", 0)
            if count == 0:
                return f"There are no unpaid billing entries{f' for {label}' if label else ''}."
            return f"There are {count} unpaid billing entries totaling {currency} {amount}{f' for {label}' if label else ''}."

        if plan.intent == "prescription_count":
            count = first.get("prescription_count", 0)
            if label:
                return f"{count} prescriptions were written {label}."
            return f"{count} prescriptions were written."

        if plan.intent == "medicine_stock":
            stock = first.get("stock")
            unit = str(first.get("unit") or "").strip()
            if stock == 1:
                unit_text = unit
            else:
                unit_text = unit + "s" if unit and not unit.endswith("s") else unit
            return f"Current stock of {first.get('name')} is {stock} {unit_text}."

        if plan.intent == "medicine_inventory_count":
            return f"There are {first.get('medicine_count', 0)} medicines currently in stock."

        if plan.intent == "medicine_stock_total":
            return f"The total stock across all medicines is {first.get('total_stock', 0)}."

        if plan.intent == "schedule_lookup":
            doctor_name = first.get("doctor_name")
            pieces = []
            for row in rows:
                start_time = self._format_time_value(row.get("start_time"))
                end_time = self._format_time_value(row.get("end_time"))
                if row.get("is_off"):
                    pieces.append(f"{row.get('day')}: off")
                else:
                    pieces.append(f"{row.get('day')}: {start_time} to {end_time}")
            return f"Schedule for {doctor_name}: " + "; ".join(pieces) + "."

        return f"I found {len(rows)} matching result rows for your query."

    def run(self, payload: QueryRequest) -> QueryResponse:
        plan = self._build_plan(payload)
        generated = self._generate_sql_from_plan(payload, plan)
        rows = self.repo.execute_select(generated.sql, generated.parameters)
        answer = self._answer_from_rows(payload, plan, rows)

        return QueryResponse(
            original_query=payload.query,
            plan=plan.model_dump(),
            sql=generated.sql,
            parameters=generated.parameters,
            rows=rows,
            answer=answer,
            chart_hint=generated.chart_hint,
        )
    
    def _extract_treatment_text(self, query: str) -> str | None:
        q = query.strip()

        patterns = [
            r"(?:revenue|income|earnings)\s+from\s+(.+)$",
            r"(?:how much revenue did)\s+(.+?)\s+(?:generate|bring in|make)\b",
            r"(?:money made from)\s+(.+)$",
        ]

        for pattern in patterns:
            match = re.search(pattern, q, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip(" ?.")

        return None