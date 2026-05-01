from __future__ import annotations

import json
import re
import time
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


    def _extract_treatment_category(self, query: str) -> str | None:
        q = self._normalized_query(query)

        generic_treatment_queries = {
            "show all treatments",
            "list all treatments",
            "show treatments",
            "available treatments",
            "treatment list",
            "what treatments are offered",
        }

        if q in generic_treatment_queries:
            return None

        if re.search(r"\bconsultations?\b", q):
            return "consultation"

        if re.search(r"\boperations?\b", q):
            return "operation"

        if re.search(r"\bothers?\b", q):
            return "other"

        if re.search(r"\bonly treatments\b|\btreatment category\b|\btreatment type\b", q):
            return "treatment"

        return None

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
                return self._clean_entity_text(match.group(1))
        return None

    def _clean_entity_text(self, text: str) -> str | None:
        cleaned = re.sub(
            r"\b(today|yesterday|this|last|current|month|week|year|fortnight|day|days|weeks|months|between|from|to|on|with|for)\b.*$",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip(" ?.")
        return cleaned or None

    def _extract_doctor_text(self, query: str) -> str | None:
        q = query.strip()
        patterns = [
            r"(?:doctor|dr\.?)\s+([a-zA-Z .'-]+)",
            r"(?:by|for|from|with)\s+(?:doctor|dr\.?)?\s*([a-zA-Z .'-]+)$",
            r"(?:appointments|bookings|prescriptions|revenue|income|earnings)\s+(?:by|for|from|with)\s+(?:doctor|dr\.?)?\s*([a-zA-Z .'-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, q, flags=re.IGNORECASE)
            if match:
                return self._clean_entity_text(match.group(1))
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


        if any(p in q for p in ["list all doctors", "show all doctors", "doctor list", "available doctors", "hospital doctors", "who are the doctors"]):
            return plan("doctor_list", ["doctors", "specializations"], "list")

        if any(p in q for p in ["all doctor schedules", "doctor schedule list", "all doctors working hours", "doctor availability", "schedules for all doctors", "available timings for all doctors"]):
            return plan("doctor_schedule_list", ["schedules", "doctors"], "list")

        if any(p in q for p in ["available medicines", "medicines in stock", "list available medicines", "stocked medicines", "medicines with stock left"]):
            return plan("available_medicines", ["medicines"], "list")

        if any(p in q for p in ["list all medicines", "show medicines", "medicine list", "medicine inventory", "what medicines are there", "list medicine stock"]):
            return plan("medicine_list", ["medicines"], "list")

        if any(p in q for p in ["list all treatments", "show treatments", "available treatments", "treatment list", "what treatments are offered", "show operations", "show consultations"]):
            return plan("treatment_list", ["treatments"], "list", category=self._extract_treatment_category(payload.query))

        if any(p in q for p in ["list all patients", "show patients", "patient list", "show registered patients", "list patients of this hospital", "show recent patients"]):
            return plan("patient_list", ["patients", "patient_billing_entries", "caseentries"], "list")

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

        if ("patient" in q or "booking" in q or "appointment" in q) and "treatment" in q and any(p in q for p in ["how many", "count", "for", "booked"]):
            treatment_text = self._extract_treatment_text(payload.query)
            if treatment_text:
                return plan("patients_by_treatment", ["bookings", "booking_treatments", "treatments"], "single_value", treatment_text=treatment_text)

        if ("top" in q and "treatment" in q and "revenue" in q) or ("treatments by revenue" in q):
            return plan(
                "top_treatments_by_revenue",
                ["patient_billing_entries", "treatments"],
                "list",
                top_n=self._extract_top_n(q, 5),
            )
        
        if ("doctor" in q or "dr" in q) and any(p in q for p in [
            "earns the most",
            "earn the most",
            "highest earning",
            "highest revenue",
            "most revenue",
            "top earning doctor",
            "doctor earns most",
        ]):
            return plan("doctor_most_revenue", ["patient_billing_entries", "bookings", "doctors"], "comparison")

        if "doctor" in q and any(p in q for p in ["most appointments", "most booking", "highest appointments", "highest bookings", "busiest doctor"]):
            return plan("doctor_most_appointments", ["bookings", "doctors"], "comparison")

        if any(p in q for p in ["revenue", "income", "earnings", "collections"]) and ("doctor" in q or "dr" in q):
            doctor_text = self._extract_doctor_text(payload.query)
            if doctor_text:
                return plan("revenue_by_doctor", ["patient_billing_entries", "bookings", "doctors"], "single_value", doctor_text=doctor_text)

        if any(p in q for p in ["prescription", "prescriptions"]) and ("doctor" in q or "dr" in q):
            doctor_text = self._extract_doctor_text(payload.query)
            if doctor_text:
                return plan("prescriptions_by_doctor", ["prescriptions", "doctors"], "single_value", doctor_text=doctor_text)

        if any(p in q for p in ["show me", "list", "display"]) and ("doctor" in q or "dr" in q) and ("booking" in q or "appointment" in q):
            doctor_text = self._extract_doctor_text(payload.query)
            if doctor_text:
                return plan("booking_list_by_doctor", ["bookings", "doctors"], "list", doctor_text=doctor_text)

        if ("doctor" in q or "dr" in q) and any(p in q for p in ["how many bookings", "how many appointments", "count bookings", "booking count", "appointments for", "bookings for"]):
            doctor_text = self._extract_doctor_text(payload.query)
            if doctor_text:
                return plan("bookings_by_doctor", ["bookings", "doctors"], "single_value", doctor_text=doctor_text)

        if any(p in q for p in ["show me", "list", "display"]) and (status is not None or "booking" in q or "appointment" in q):
            return plan("booking_list", ["bookings", "doctors"], "list")

        if ("time slot" in q and "busiest" in q) or ("slot is busiest" in q):
            return plan("busiest_time_slot", ["bookings"], "single_value")
        
        if "busiest day" in q or " busiest date" in q:
            return plan("busiest_day", ["bookings"], "single_value")

        if any(p in q for p in ["morning", "noon", "evening"]) and any(p in q for p in ["how many bookings", "bookings are there", "split"]):
            return plan("busiest_day_part", ["bookings"], "comparison")

        if any(p in q for p in ["revenue", "income", "earnings", "collections"]):
            treatment_text = self._extract_treatment_text(payload.query)

            if treatment_text:
                return plan(
                    "revenue_by_treatment",
                    ["patient_billing_entries", "treatments"],
                    "single_value",
                    treatment_text=treatment_text,
                )

            return plan("revenue_total", ["patient_billing_entries"], "single_value")

        if any(p in q for p in ["how many bookings", "bookings are there", "booking count", "count bookings", "count booking"]):
            return plan("booking_count", ["bookings"], "single_value")

        if weekday and "busiest" in q:
            return plan("busiest_time_slot", ["bookings"], "single_value")

        if any(p in q for p in ["schedule", "available", "working hours"]) and ("doctor" in q or "dr" in q):
            return plan("schedule_lookup", ["schedules", "doctors"], "list")
        
        if q in {"show performance", "performance", "show analytics", "analytics", "dashboard summary"}:
            return plan("unsupported_ambiguous", [], "summary")

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
                if matched_intent == "treatment_list":
                    extra_entities["category"] = self._extract_treatment_category(payload.query)
                if matched_intent in {"revenue_by_treatment", "patients_by_treatment"}:
                    extra_entities["treatment_text"] = self._extract_treatment_text(payload.query)
                if matched_intent in {"revenue_by_doctor", "bookings_by_doctor", "booking_list_by_doctor", "prescriptions_by_doctor"}:
                    extra_entities["doctor_text"] = self._extract_doctor_text(payload.query)

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

        if plan.intent == "unsupported_ambiguous":
            return SqlGenerationOutput(
                sql="SELECT 1 AS needs_clarification",
                parameters=params,
                chart_hint=None,
                explanation="Ambiguous query that needs clarification.",
            )

        if plan.date_from and plan.date_to:
            params["date_from"] = plan.date_from
            params["date_to"] = plan.date_to


        if plan.intent == "doctor_list":
            sql = """
            SELECT d.id, d.name, d.doctor_code, d.qualification, d.phone, d.gender,
                   d.experience_years, d.consultation_fee, s.specialization
            FROM doctors d
            LEFT JOIN specializations s
              ON s.id = d.specialization_id
             AND (s.hospital_id = d.hospital_id OR s.hospital_id IS NULL)
            WHERE d.hospital_id = :hospital_id
            ORDER BY d.name ASC
            LIMIT 100
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Doctor list query.")

        if plan.intent == "doctor_schedule_list":
            sql = """
            SELECT d.id AS doctor_id, d.name AS doctor_name, s.day, s.start_time, s.end_time, s.is_off
            FROM doctors d
            LEFT JOIN schedules s ON s.doctor_id = d.id
            WHERE d.hospital_id = :hospital_id
            ORDER BY d.name ASC,
                     FIELD(LOWER(s.day), 'monday','tuesday','wednesday','thursday','friday','saturday','sunday')
            LIMIT 300
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Schedule list for all doctors.")

        if plan.intent == "medicine_list":
            sql = """
            SELECT id, name, unit, dosage, price, stock, description
            FROM medicines
            WHERE hospital_id = :hospital_id
            ORDER BY name ASC
            LIMIT 200
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Medicine list query.")

        if plan.intent == "available_medicines":
            sql = """
            SELECT id, name, unit, dosage, price, stock, description
            FROM medicines
            WHERE hospital_id = :hospital_id
              AND COALESCE(stock, 0) > 0
            ORDER BY name ASC
            LIMIT 200
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Available medicines query.")

        if plan.intent == "treatment_list":
            clauses = ["hospital_id = :hospital_id", "is_active = 1"]
            if e.get("category"):
                params["category"] = e["category"]
                clauses.append("category = :category")
            sql = f"""
            SELECT id, name, code, category, base_price, is_active
            FROM treatments
            WHERE {' AND '.join(clauses)}
            ORDER BY category ASC, name ASC
            LIMIT 200
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Treatment list query.")

        if plan.intent == "patient_list":
            clauses = [
                "(EXISTS (SELECT 1 FROM patient_billing_entries pbe WHERE pbe.patient_id = p.id AND pbe.hospital_id = :hospital_id) "
                "OR EXISTS (SELECT 1 FROM caseentries ce WHERE ce.patient_id = p.id AND ce.hospital_id = :hospital_id))"
            ]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(p.created_at) BETWEEN :date_from AND :date_to")
            sql = f"""
            SELECT p.id, p.name, p.phone_no, p.ic_passport_no, p.age, p.gender,
                   p.city, p.country, p.created_at
            FROM patients p
            WHERE {' AND '.join(clauses)}
            ORDER BY p.created_at DESC, p.name ASC
            LIMIT 100
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Hospital-scoped patient list query.")

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

        if plan.intent in {"bookings_by_doctor", "booking_list_by_doctor"}:
            doctor_text = str(e.get("doctor_text") or "").strip()
            if not doctor_text:
                raise ValueError("Doctor name could not be extracted from the query.")
            matched = self.matcher.match_doctor(payload.hospital_id, doctor_text)
            if not matched:
                raise ValueError(f"No matching doctor found for '{doctor_text}'.")
            params["doctor_id"] = int(matched["id"])
            plan.entities["matched_doctor"] = matched
            clauses = ["b.hospital_id = :hospital_id", "b.doctor_id = :doctor_id"]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(b.booking_date) BETWEEN :date_from AND :date_to")
            if e.get("status"):
                params["status"] = e["status"]
                clauses.append("b.status = :status")
            if e.get("weekday"):
                params["weekday"] = str(e["weekday"]).capitalize()
                clauses.append("DAYNAME(b.booking_date) = :weekday")
            if plan.intent == "bookings_by_doctor":
                sql = f"""
                SELECT COUNT(*) AS booking_count, MAX(d.name) AS doctor_name
                FROM bookings b
                JOIN doctors d ON d.id = b.doctor_id AND d.hospital_id = b.hospital_id
                WHERE {' AND '.join(clauses)}
                """
                return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Booking count filtered by doctor.")
            sql = f"""
            SELECT b.id, b.booking_date, b.start_time, b.end_time, b.patient_name, b.patient_phone, b.status,
                   d.name AS doctor_name, b.cause
            FROM bookings b
            JOIN doctors d ON d.id = b.doctor_id AND d.hospital_id = b.hospital_id
            WHERE {' AND '.join(clauses)}
            ORDER BY b.booking_date ASC, b.start_time ASC
            LIMIT 100
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Booking list filtered by doctor.")

        if plan.intent == "revenue_by_doctor":
            doctor_text = str(e.get("doctor_text") or "").strip()
            if not doctor_text:
                raise ValueError("Doctor name could not be extracted from the query.")
            matched = self.matcher.match_doctor(payload.hospital_id, doctor_text)
            if not matched:
                raise ValueError(f"No matching doctor found for '{doctor_text}'.")
            params["doctor_id"] = int(matched["id"])
            plan.entities["matched_doctor"] = matched
            clauses = [
                "pbe.hospital_id = :hospital_id",
                "pbe.is_paid = 1",
                "pbe.paid_at IS NOT NULL",
                "b.doctor_id = :doctor_id",
                "pbe.type IN ('consultation', 'medicine', 'treatment', 'operation', 'custom_profit')",
            ]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(pbe.paid_at) BETWEEN :date_from AND :date_to")
            sql = f"""
            SELECT COALESCE(SUM(pbe.amount), 0) AS total_revenue,
                   COUNT(*) AS paid_entries_count,
                   MAX(d.name) AS doctor_name
            FROM patient_billing_entries pbe
            JOIN bookings b ON b.id = pbe.booking_id AND b.hospital_id = pbe.hospital_id
            JOIN doctors d ON d.id = b.doctor_id AND d.hospital_id = b.hospital_id
            WHERE {' AND '.join(clauses)}
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint={"metric": "currency", "key": "total_revenue"}, explanation="Revenue filtered by doctor.")

        if plan.intent == "prescriptions_by_doctor":
            doctor_text = str(e.get("doctor_text") or "").strip()
            if not doctor_text:
                raise ValueError("Doctor name could not be extracted from the query.")
            matched = self.matcher.match_doctor(payload.hospital_id, doctor_text)
            if not matched:
                raise ValueError(f"No matching doctor found for '{doctor_text}'.")
            params["doctor_id"] = int(matched["id"])
            plan.entities["matched_doctor"] = matched
            clauses = ["hospital_id = :hospital_id", "doctor_id = :doctor_id"]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(created_at) BETWEEN :date_from AND :date_to")
            sql = f"""
            SELECT COUNT(*) AS prescription_count, :doctor_name AS doctor_name
            FROM prescriptions
            WHERE {' AND '.join(clauses)}
            """
            params["doctor_name"] = matched.get("name")
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Prescription count filtered by doctor.")

        if plan.intent == "patients_by_treatment":
            treatment_text = str(e.get("treatment_text") or "").strip()
            if not treatment_text:
                raise ValueError("Treatment name could not be extracted from the query.")
            matched = self.matcher.match_treatment(payload.hospital_id, treatment_text)
            if not matched:
                raise ValueError(f"No matching treatment found for '{treatment_text}'.")
            params["treatment_id"] = int(matched["id"])
            plan.entities["matched_treatment"] = matched
            clauses = ["b.hospital_id = :hospital_id", "bt.treatment_id = :treatment_id"]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(b.booking_date) BETWEEN :date_from AND :date_to")
            sql = f"""
            SELECT COUNT(DISTINCT b.id) AS booking_count,
                   COUNT(DISTINCT b.patient_phone) AS patient_count,
                   MAX(t.name) AS treatment_name
            FROM bookings b
            JOIN booking_treatments bt ON bt.booking_id = b.id
            JOIN treatments t ON t.id = bt.treatment_id AND t.hospital_id = b.hospital_id
            WHERE {' AND '.join(clauses)}
            """
            return SqlGenerationOutput(sql=sql, parameters=params, chart_hint=None, explanation="Patient/booking count filtered by treatment.")


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

        if plan.intent == "doctor_most_revenue":
            clauses = [
                "pbe.hospital_id = :hospital_id",
                "pbe.is_paid = 1",
                "pbe.paid_at IS NOT NULL",
                "pbe.type IN ('consultation', 'medicine', 'treatment', 'operation', 'custom_profit')",
                "b.doctor_id IS NOT NULL",
            ]

            if plan.date_from and plan.date_to:
                clauses.append("DATE(pbe.paid_at) BETWEEN :date_from AND :date_to")

            sql = f"""
            SELECT d.id AS doctor_id,
                d.name AS doctor_name,
                COALESCE(SUM(pbe.amount), 0) AS total_revenue,
                COUNT(*) AS paid_entries_count
            FROM patient_billing_entries pbe
            JOIN bookings b
            ON b.id = pbe.booking_id
            AND b.hospital_id = pbe.hospital_id
            JOIN doctors d
            ON d.id = b.doctor_id
            AND d.hospital_id = b.hospital_id
            WHERE {' AND '.join(clauses)}
            GROUP BY d.id, d.name
            ORDER BY total_revenue DESC, doctor_name ASC
            LIMIT 1
            """

            return SqlGenerationOutput(
                sql=sql,
                parameters=params,
                chart_hint={"metric": "currency", "key": "total_revenue"},
                explanation="Doctor leaderboard by revenue.",
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
                "pbe.type IN ('consultation', 'treatment', 'operation')",
            ]
            if plan.date_from and plan.date_to:
                clauses.append("DATE(pbe.paid_at) BETWEEN :date_from AND :date_to")

            sql = f"""
            SELECT t.name AS treatment_name,
                COALESCE(SUM(pbe.amount), 0) AS total_revenue,
                COUNT(*) AS paid_entries_count
            FROM patient_billing_entries pbe
            JOIN treatments t
            ON t.id = pbe.treatment_id
            AND t.hospital_id = pbe.hospital_id
            WHERE {' AND '.join(clauses)}
            AND pbe.type IN ('consultation', 'treatment', 'operation')
            GROUP BY t.id, t.name
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

        if plan.intent == "busiest_day":
            clauses = ["hospital_id = :hospital_id"]

            if plan.date_from and plan.date_to:
                clauses.append("DATE(booking_date) BETWEEN :date_from AND :date_to")

            if e.get("status"):
                params["status"] = e["status"]
                clauses.append("status = :status")

            sql = f"""
            SELECT DATE(booking_date) AS booking_day,
                COUNT(*) AS booking_count
            FROM bookings
            WHERE {' AND '.join(clauses)}
            GROUP BY DATE(booking_date)
            ORDER BY booking_count DESC, booking_day ASC
            LIMIT 1
            """

            return SqlGenerationOutput(
                sql=sql,
                parameters=params,
                chart_hint=None,
                explanation="Busiest day by booking count.",
            )

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

        if plan.intent == "unsupported_ambiguous":
            return (
                "Performance can mean revenue, bookings, doctors, treatments, patients, or medicines. "
                "Please ask something more specific, like 'show revenue this month', "
                "'show bookings today', or 'top treatments by revenue'."
            )


        if plan.intent == "doctor_list":
            lines = []
            for row in rows[:20]:
                specialization = row.get("specialization") or "No specialization listed"
                fee = row.get("consultation_fee")
                fee_text = f", consultation fee {currency} {fee}" if fee is not None else ""
                lines.append(f"- Dr. {row.get('name')} ({specialization}) — {row.get('qualification')}{fee_text}")
            if len(rows) > 20:
                lines.append(f"...and {len(rows) - 20} more doctors.")
            return f"I found {len(rows)} doctors for this hospital.\n" + "\n".join(lines)

        if plan.intent == "doctor_schedule_list":
            grouped: dict[str, list[str]] = {}
            for row in rows:
                name = str(row.get("doctor_name") or "Unknown doctor")
                if row.get("day") is None:
                    grouped.setdefault(name, []).append("No schedule configured")
                    continue
                if row.get("is_off"):
                    piece = f"{row.get('day')}: off"
                else:
                    piece = f"{row.get('day')}: {self._format_time_value(row.get('start_time'))} to {self._format_time_value(row.get('end_time'))}"
                grouped.setdefault(name, []).append(piece)
            lines = [f"- {name}: " + "; ".join(parts[:7]) for name, parts in list(grouped.items())[:20]]
            if len(grouped) > 20:
                lines.append(f"...and {len(grouped) - 20} more doctors.")
            return f"I found schedules for {len(grouped)} doctors.\n" + "\n".join(lines)

        if plan.intent in {"medicine_list", "available_medicines"}:
            title = "available medicines" if plan.intent == "available_medicines" else "medicines"
            lines = []
            for row in rows[:20]:
                dosage = f" {row.get('dosage')}" if row.get("dosage") else ""
                unit = row.get("unit") or "units"
                lines.append(f"- {row.get('name')}{dosage}: stock {row.get('stock')} {unit}, price {currency} {row.get('price')}")
            if len(rows) > 20:
                lines.append(f"...and {len(rows) - 20} more medicines.")
            return f"I found {len(rows)} {title}.\n" + "\n".join(lines)

        if plan.intent == "treatment_list":
            category = plan.entities.get("category")
            label_text = f" {category}" if category else ""
            lines = []
            for row in rows[:20]:
                code = f" [{row.get('code')}]" if row.get("code") else ""
                lines.append(f"- {row.get('name')}{code} — {row.get('category')}, base price {currency} {row.get('base_price')}")
            if len(rows) > 20:
                lines.append(f"...and {len(rows) - 20} more treatments.")
            return f"I found {len(rows)}{label_text} treatments.\n" + "\n".join(lines)

        if plan.intent == "patient_list":
            lines = []
            for row in rows[:20]:
                location = ", ".join(str(x) for x in [row.get("city"), row.get("country")] if x)
                suffix = f" — {location}" if location else ""
                lines.append(f"- #{row.get('id')}: {row.get('name')} ({row.get('phone_no')}){suffix}")
            if len(rows) > 20:
                lines.append(f"...and {len(rows) - 20} more patients.")
            return f"I found {len(rows)} patients linked to this hospital.\n" + "\n".join(lines)

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
        
        if plan.intent == "bookings_by_doctor":
            doctor_name = first.get("doctor_name") or (plan.entities.get("matched_doctor") or {}).get("name") or "that doctor"
            count = first.get("booking_count", 0)
            return f"{doctor_name} has {count} bookings{f' for {label}' if label else ''}."

        if plan.intent == "booking_list_by_doctor":
            doctor_name = (plan.entities.get("matched_doctor") or {}).get("name") or first.get("doctor_name") or "that doctor"
            intro = f"I found {len(rows)} bookings for {doctor_name}{f' for {label}' if label else ''}."
            lines = []
            for row in rows[:10]:
                start_time = self._format_time_value(row.get("start_time"))
                end_time = self._format_time_value(row.get("end_time"))
                lines.append(f"- #{row.get('id')}: {row.get('patient_name')} on {row.get('booking_date')} from {start_time} to {end_time} ({row.get('status')})")
            if len(rows) > 10:
                lines.append(f"...and {len(rows) - 10} more.")
            return intro + "\n" + "\n".join(lines)

        if plan.intent == "revenue_by_doctor":
            doctor_name = first.get("doctor_name") or (plan.entities.get("matched_doctor") or {}).get("name") or "that doctor"
            return f"Total revenue for {doctor_name}{f' for {label}' if label else ''} is {currency} {first.get('total_revenue', 0)} from {first.get('paid_entries_count', 0)} paid entries."

        if plan.intent == "prescriptions_by_doctor":
            doctor_name = first.get("doctor_name") or (plan.entities.get("matched_doctor") or {}).get("name") or "that doctor"
            return f"{doctor_name} wrote {first.get('prescription_count', 0)} prescriptions{f' for {label}' if label else ''}."

        if plan.intent == "patients_by_treatment":
            treatment_name = first.get("treatment_name") or (plan.entities.get("matched_treatment") or {}).get("name") or "that treatment"
            return f"{treatment_name} has {first.get('booking_count', 0)} bookings and approximately {first.get('patient_count', 0)} unique patient phone numbers{f' for {label}' if label else ''}."

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
        
        if plan.intent == "doctor_most_revenue":
            return (
                f"{first.get('doctor_name')} has the highest revenue"
                f"{f' for {label}' if label else ''} with {currency} {first.get('total_revenue', 0)} "
                f"from {first.get('paid_entries_count', 0)} paid entries."
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
        
        if plan.intent == "busiest_day":
            return (
                f"The busiest day{f' in {label}' if label else ''} was "
                f"{first.get('booking_day')} with {first.get('booking_count')} bookings."
            )

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


    def _follow_up_suggestions(self, plan: PlannerOutput) -> list[str]:
        if plan.intent.startswith("revenue"):
            return ["Compare this with last month", "Break this down by treatment", "Show top treatments by revenue"]
        if "booking" in plan.intent:
            return ["Show this for last month", "Break this down by doctor", "Which time slot is busiest?"]
        if "medicine" in plan.intent:
            return ["Show all medicines in stock", "Which medicines are low in stock?", "What is the total medicine stock?"]
        if plan.intent in {"doctor_list", "doctor_schedule_list"}:
            return ["Show all doctor schedules", "Which doctor has the most appointments this week?", "Show bookings by doctor"]
        if plan.intent == "treatment_list":
            return ["Show top treatments by revenue", "Revenue from a treatment", "Show consultations only"]
        if plan.intent == "patient_list":
            return ["How many patients registered this month?", "Show recent bookings", "Show patients by treatment"]
        return ["Show this for this month", "Break this down by doctor", "Show the related records"]

    def _json_for_log(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    def run(self, payload: QueryRequest) -> QueryResponse:
        started = time.perf_counter()
        normalized_query = self.intent_matcher.normalize(payload.query)
        log_id = self.repo.create_query_log(
            hospital_id=payload.hospital_id,
            query_text=payload.query,
            normalized_query=normalized_query,
        )

        plan: PlannerOutput | None = None
        generated: SqlGenerationOutput | None = None

        try:
            plan = self._build_plan(payload)
            self.repo.update_query_log(
                log_id,
                normalized_query=normalized_query,
                intent=plan.intent,
                semantic_score=plan.entities.get("semantic_score"),
                semantic_method=plan.entities.get("semantic_method"),
                matched_example=plan.entities.get("semantic_example"),
                plan_json=self._json_for_log(plan.model_dump()),
                status="planned",
            )

            generated = self._generate_sql_from_plan(payload, plan)
            self.repo.update_query_log(
                log_id,
                sql_text=generated.sql,
                parameters_json=self._json_for_log(generated.parameters),
                chart_hint_json=self._json_for_log(generated.chart_hint),
                status="sql_generated",
            )

            rows = self.repo.execute_select(generated.sql, generated.parameters)
            answer = self._answer_from_rows(payload, plan, rows)
            latency_ms = int((time.perf_counter() - started) * 1000)

            self.repo.update_query_log(
                log_id,
                result_json=self._json_for_log(rows[:50]),
                answer_text=answer,
                status="success",
                latency_ms=latency_ms,
            )

            return QueryResponse(
                original_query=payload.query,
                plan=plan.model_dump(),
                sql=generated.sql,
                parameters=generated.parameters,
                rows=rows,
                answer=answer,
                chart_hint=generated.chart_hint,
                query_log_id=log_id,
                follow_up_suggestions=self._follow_up_suggestions(plan),
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            self.repo.update_query_log(
                log_id,
                intent=plan.intent if plan else None,
                plan_json=self._json_for_log(plan.model_dump()) if plan else None,
                sql_text=generated.sql if generated else None,
                parameters_json=self._json_for_log(generated.parameters) if generated else None,
                status="error",
                error_text=str(exc),
                latency_ms=latency_ms,
            )
            raise
    
    def _extract_treatment_text(self, query: str) -> str | None:
        q = query.strip()

        patterns = [
            r"(?:revenue|income|earnings|earning|sales|collections|money|profit)\s+(?:from|for|by|of)\s+(.+)$",
            r"(?:how much revenue did)\s+(.+?)\s+(?:generate|bring in|make)\b",
            r"(?:money made from)\s+(.+)$",
            r"(?:patients|bookings|appointments)\s+(?:for|by|from|of)\s+(.+)$",
        ]

        for pattern in patterns:
            match = re.search(pattern, q, flags=re.IGNORECASE)
            if match:
                text = match.group(1).strip(" ?.")
                text = re.sub(
                    r"\b(today|yesterday|this|last|current|month|week|year|fortnight|day|days|weeks|months|between|from|to|on|in)\b.*$",
                    "",
                    text,
                    flags=re.IGNORECASE,
                ).strip(" ?.")
                return self._validate_treatment_text(text)

        cleaned = re.sub(
            r"\b(revenue|income|earnings|earning|sales|collections|money|profit|total|how|much|did|make|made|generate|what|is|the|show|give|me)\b",
            " ",
            q,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(today|yesterday|this|last|current|month|week|year|fortnight|day|days|weeks|months|march|april|may|june|july|august|september|october|november|december|january|february)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ?.")

        return self._validate_treatment_text(cleaned)


    def _validate_treatment_text(self, text: str | None) -> str | None:
        if not text:
            return None

        cleaned = text.lower().strip()

        bad_phrases = {
            "what is the",
            "what is",
            "total",
            "total revenue",
            "revenue",
            "income",
            "earnings",
            "earning",
            "collections",
            "sales",
            "money",
            "profit",
            "show me",
            "give me",
            "all",
            "all treatments",
        }

        if cleaned in bad_phrases:
            return None

        bad_words = {"what", "how", "show", "give", "total", "revenue", "income", "earnings"}
        if any(word in cleaned.split() for word in bad_words):
            return None

        if len(cleaned.split()) > 5:
            return None

        return text.strip()