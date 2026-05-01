from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from rapidfuzz import fuzz, process

from app.repositories.analytics_repository import AnalyticsRepository

try:
    from sentence_transformers import SentenceTransformer, util
except Exception:  # pragma: no cover
    SentenceTransformer = None
    util = None


@dataclass
class EntityCandidate:
    entity_type: str
    id: int
    name: str
    score: float
    method: str
    matched_text: str
    row: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.row)
        data.update(
            {
                "entity_type": self.entity_type,
                "match_score": round(float(self.score), 4),
                "match_method": self.method,
                "matched_on": self.matched_text,
            }
        )
        return data


class EntityIndex:
    """
    Searchable entity index for treatments, medicines, and doctors.

    It still uses fuzzy matching, but it indexes multiple searchable aliases per entity
    and can optionally use sentence-transformer embeddings when installed.
    """

    def __init__(
        self,
        repo: AnalyticsRepository,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        fuzzy_threshold: int = 62,
        embedding_threshold: float = 0.52,
    ) -> None:
        self.repo = repo
        self.model_name = model_name
        self.fuzzy_threshold = fuzzy_threshold
        self.embedding_threshold = embedding_threshold
        self._model = None
        self._cache: dict[tuple[int, str], list[dict[str, Any]]] = {}
        self._embedding_cache: dict[tuple[int, str], Any] = {}

    def _normalize(self, value: Any) -> str:
        text = str(value or "").lower().strip()
        text = text.replace("&", " and ")
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _load_rows(self, hospital_id: int, entity_type: str) -> list[dict[str, Any]]:
        cache_key = (hospital_id, entity_type)
        if cache_key in self._cache:
            return self._cache[cache_key]

        if entity_type == "treatment":
            raw_rows = self.repo.list_treatments(hospital_id)
            fields = ["name", "code", "category"]
        elif entity_type == "medicine":
            raw_rows = self.repo.list_medicines(hospital_id)
            fields = ["name", "unit", "dosage", "description"]
        elif entity_type == "doctor":
            raw_rows = self.repo.list_doctors(hospital_id)
            fields = ["name", "doctor_code", "qualification", "phone"]
        else:
            raise ValueError(f"Unsupported entity type: {entity_type}")

        indexed: list[dict[str, Any]] = []
        for row in raw_rows:
            aliases: set[str] = set()
            for field in fields:
                normalized = self._normalize(row.get(field))
                if normalized:
                    aliases.add(normalized)

            # Useful generated aliases.
            if entity_type == "doctor" and row.get("name"):
                name = self._normalize(row["name"])
                aliases.add(name.replace("doctor ", ""))
                aliases.add(name.replace("dr ", ""))
            if entity_type == "treatment" and row.get("category"):
                aliases.add(self._normalize(row["category"]))

            indexed.append({"row": row, "aliases": sorted(aliases)})

        self._cache[cache_key] = indexed
        return indexed

    def _load_model(self):
        if self._model is None and SentenceTransformer is not None:
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _embedding_search(self, hospital_id: int, entity_type: str, query: str) -> EntityCandidate | None:
        model = self._load_model()
        if model is None or util is None:
            return None

        indexed = self._load_rows(hospital_id, entity_type)
        texts: list[str] = []
        text_to_row: list[dict[str, Any]] = []
        for item in indexed:
            for alias in item["aliases"]:
                texts.append(alias)
                text_to_row.append(item["row"])

        if not texts:
            return None

        cache_key = (hospital_id, entity_type)
        if cache_key not in self._embedding_cache:
            self._embedding_cache[cache_key] = model.encode(texts, convert_to_tensor=True, normalize_embeddings=True)

        q_emb = model.encode([query], convert_to_tensor=True, normalize_embeddings=True)
        scores = util.cos_sim(q_emb, self._embedding_cache[cache_key])[0].tolist()
        best_idx = max(range(len(scores)), key=lambda idx: scores[idx])
        score = float(scores[best_idx])
        if score < self.embedding_threshold:
            return None

        row = text_to_row[best_idx]
        return EntityCandidate(
            entity_type=entity_type,
            id=int(row["id"]),
            name=str(row.get("name") or row.get("id")),
            score=score,
            method="embedding",
            matched_text=texts[best_idx],
            row=row,
        )

    def _fuzzy_search(self, hospital_id: int, entity_type: str, query: str) -> EntityCandidate | None:
        q = self._normalize(query)
        if not q:
            return None

        indexed = self._load_rows(hospital_id, entity_type)
        choices: dict[str, dict[str, Any]] = {}
        for item in indexed:
            for alias in item["aliases"]:
                choices[alias] = item["row"]

        if not choices:
            return None

        # Exact/contains first because users commonly type partial names like derma.
        for alias, row in choices.items():
            if q == alias or q in alias or alias in q:
                return EntityCandidate(entity_type, int(row["id"]), str(row.get("name") or row["id"]), 100.0, "fuzzy", alias, row)

        result = process.extractOne(q, choices.keys(), scorer=fuzz.WRatio)
        if not result:
            return None
        matched_alias, score, _ = result
        if score < self.fuzzy_threshold:
            return None
        row = choices[matched_alias]
        return EntityCandidate(entity_type, int(row["id"]), str(row.get("name") or row["id"]), float(score), "fuzzy", matched_alias, row)

    def search_one(self, hospital_id: int, entity_type: str, query: str) -> dict[str, Any] | None:
        q = self._normalize(query)
        fuzzy = self._fuzzy_search(hospital_id, entity_type, q)
        embedding = self._embedding_search(hospital_id, entity_type, q)
        candidates = [c for c in [fuzzy, embedding] if c is not None]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.score).to_dict()
