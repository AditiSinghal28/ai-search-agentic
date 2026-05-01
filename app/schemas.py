from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    hospital_id: int
    today: str | None = None
    timezone: str | None = "Asia/Kolkata"
    conversation_id: str | None = None


class PlannerOutput(BaseModel):
    intent: str
    date_from: str | None = None
    date_to: str | None = None
    needs_time_split: bool = False
    needs_comparison: bool = False
    relevant_tables: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    answer_style: Literal["single_value", "summary", "comparison", "list", "trend"] = "summary"
    sql_notes: list[str] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)


class SqlGenerationOutput(BaseModel):
    sql: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    chart_hint: str | dict[str, Any] | list[Any] | None = None
    explanation: str = ""


class QueryResponse(BaseModel):
    original_query: str
    plan: dict[str, Any]
    sql: str
    parameters: dict[str, Any]
    rows: list[dict[str, Any]]
    answer: str
    chart_hint: str | dict[str, Any] | list[Any] | None = None
    query_log_id: int | None = None
    follow_up_suggestions: list[str] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    query_log_id: int
    hospital_id: int | None = None
    is_helpful: bool | None = None
    corrected_intent: str | None = None
    corrected_answer: str | None = None
    feedback_text: str | None = None


class FeedbackResponse(BaseModel):
    id: int
    status: str = "saved"
