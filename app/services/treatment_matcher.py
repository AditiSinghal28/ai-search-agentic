from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz

from app.repositories.analytics_repository import AnalyticsRepository


class EntityMatcher:
    def __init__(self, repo: AnalyticsRepository) -> None:
        self.repo = repo

    def _normalize(self, value: Any) -> str:
        return str(value or "").strip().lower()

    def _match(
        self,
        items: list[dict[str, Any]],
        query: str,
        fields: list[str],
        threshold: int = 65,
    ) -> dict[str, Any] | None:
        q = self._normalize(query)
        if not q:
            return None

        best: dict[str, Any] | None = None
        best_score = 0

        for item in items:
            haystacks = []
            for field in fields:
                raw = item.get(field)
                text = self._normalize(raw)
                if text:
                    haystacks.append(text)

            if not haystacks:
                continue

            for text in haystacks:
                if q == text:
                    matched = dict(item)
                    matched["match_score"] = 100
                    matched["matched_on"] = text
                    return matched

            for text in haystacks:
                if q in text or text in q:
                    matched = dict(item)
                    matched["match_score"] = 95
                    matched["matched_on"] = text
                    return matched

            score = max(fuzz.WRatio(q, text) for text in haystacks)

            if score > best_score:
                best_score = score
                best = item

        if best and best_score >= threshold:
            matched = dict(best)
            matched["match_score"] = best_score
            return matched

        return None

    def match_treatment(self, hospital_id: int, query: str) -> dict[str, Any] | None:
        return self._match(
            self.repo.list_treatments(hospital_id),
            query,
            ["name", "code", "category"],
            threshold=65,
        )

    def match_doctor(self, hospital_id: int, query: str) -> dict[str, Any] | None:
        return self._match(
            self.repo.list_doctors(hospital_id),
            query,
            ["name", "doctor_code", "qualification", "phone"],
            threshold=65,
        )

    def match_medicine(self, hospital_id: int, query: str) -> dict[str, Any] | None:
        return self._match(
            self.repo.list_medicines(hospital_id),
            query,
            ["name", "unit", "dosage", "description"],
            threshold=65,
        )