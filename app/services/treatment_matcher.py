from __future__ import annotations

from typing import Any

from app.repositories.analytics_repository import AnalyticsRepository
from app.services.entity_index import EntityIndex


class EntityMatcher:
    """Backward-compatible wrapper around the searchable EntityIndex."""

    def __init__(self, repo: AnalyticsRepository) -> None:
        self.index = EntityIndex(repo)

    def match_treatment(self, hospital_id: int, query: str) -> dict[str, Any] | None:
        return self.index.search_one(hospital_id, "treatment", query)

    def match_doctor(self, hospital_id: int, query: str) -> dict[str, Any] | None:
        return self.index.search_one(hospital_id, "doctor", query)

    def match_medicine(self, hospital_id: int, query: str) -> dict[str, Any] | None:
        return self.index.search_one(hospital_id, "medicine", query)
