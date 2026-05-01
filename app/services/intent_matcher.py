from __future__ import annotations

import re
from difflib import SequenceMatcher, get_close_matches
from typing import Any

from app.services.intent_catalog import INTENT_CATALOG

try:
    from sentence_transformers import SentenceTransformer, util
except Exception:  # pragma: no cover
    SentenceTransformer = None
    util = None


class IntentMatcher:
    """
    Hybrid intent matcher:
    1. normalize text
    2. auto-correct close tokens using catalog vocabulary
    3. embedding similarity across intent examples
    4. lexical fallback if embeddings are unavailable
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        min_confidence: float = 0.56,
    ) -> None:
        self.model_name = model_name
        self.min_confidence = min_confidence
        self._model = None
        self._example_rows: list[dict[str, Any]] = []
        self._example_embeddings = None

        self.alias_map = {
            "appointments": "bookings",
            "appointment": "booking",
            "visits": "bookings",
            "visit": "booking",
            "earning": "revenue",
            "rebenue": "revenue",
            "revnu": "revenue",
            "revenu": "revenue",
            "profit": "revenue",
            "sales": "revenue",
            "earnings": "revenue",
            "income": "revenue",
            "collections": "revenue",
            "collection": "revenue",
            "medication": "medicine",
            "medications": "medicines",
            "drug": "medicine",
            "drugs": "medicines",
            "doctor timings": "schedule",
            "working hours": "schedule",
            "inventory": "stock",
            "fees": "billing",
            "fee": "billing",
        }

        # create vocabulary first so normalize() can use it safely
        self._vocabulary: list[str] = []
        self._build_seed_vocabulary()
        self._build_examples()
        self._build_vocabulary()

    def _build_seed_vocabulary(self) -> None:
        vocab: set[str] = set()

        for intent, meta in INTENT_CATALOG.items():
            vocab.add(intent.lower())
            for example in meta["examples"]:
                text = self._basic_normalize(example)
                text = self._apply_aliases(text)
                for token in re.findall(r"[a-z0-9_]+", text):
                    if len(token) >= 3:
                        vocab.add(token)

        for value in self.alias_map.values():
            for token in re.findall(r"[a-z0-9_]+", value.lower()):
                if len(token) >= 3:
                    vocab.add(token)

        self._vocabulary = sorted(vocab)

    def _build_examples(self) -> None:
        rows: list[dict[str, Any]] = []
        for intent, meta in INTENT_CATALOG.items():
            for example in meta["examples"]:
                rows.append(
                    {
                        "intent": intent,
                        "example": example,
                        "normalized_example": self.normalize(example),
                    }
                )
        self._example_rows = rows

    def _build_vocabulary(self) -> None:
        vocab: set[str] = set(self._vocabulary)

        for row in self._example_rows:
            for token in re.findall(r"[a-z0-9_]+", row["normalized_example"]):
                if len(token) >= 3:
                    vocab.add(token)

        for value in self.alias_map.values():
            for token in re.findall(r"[a-z0-9_]+", value.lower()):
                if len(token) >= 3:
                    vocab.add(token)

        self._vocabulary = sorted(vocab)

    def _load_model(self):
        if self._model is None and SentenceTransformer is not None:
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def _ensure_embeddings(self) -> None:
        if self._example_embeddings is not None:
            return
        model = self._load_model()
        if model is None:
            return
        texts = [row["normalized_example"] for row in self._example_rows]
        self._example_embeddings = model.encode(
            texts,
            convert_to_tensor=True,
            normalize_embeddings=True,
        )

    def _basic_normalize(self, text: str) -> str:
        text = text.lower().strip()
        text = text.replace("&", " and ")
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _apply_aliases(self, text: str) -> str:
        updated = text
        for src, dst in sorted(self.alias_map.items(), key=lambda x: len(x[0]), reverse=True):
            updated = re.sub(rf"\b{re.escape(src)}\b", dst, updated)
        return updated

    def _correct_tokens(self, text: str) -> str:
        if not getattr(self, "_vocabulary", None):
            return text

        tokens = text.split()
        corrected: list[str] = []

        for token in tokens:
            if len(token) <= 3 or token.isdigit():
                corrected.append(token)
                continue

            matches = get_close_matches(token, self._vocabulary, n=1, cutoff=0.82)
            if matches:
                corrected.append(matches[0])
            else:
                corrected.append(token)

        return " ".join(corrected)

    def normalize(self, text: str) -> str:
        normalized = self._basic_normalize(text)
        normalized = self._apply_aliases(normalized)
        normalized = self._correct_tokens(normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _lexical_score(self, a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

    def _embedding_match(self, query: str) -> dict[str, Any] | None:
        self._ensure_embeddings()
        model = self._load_model()
        if model is None or self._example_embeddings is None or util is None:
            return None

        query_embedding = model.encode(
            [query],
            convert_to_tensor=True,
            normalize_embeddings=True,
        )

        scores = util.cos_sim(query_embedding, self._example_embeddings)[0].tolist()

        best_intent = None
        best_score = -1.0
        best_example = None

        for idx, score in enumerate(scores):
            row = self._example_rows[idx]
            if score > best_score:
                best_score = float(score)
                best_intent = row["intent"]
                best_example = row["example"]

        if best_intent is None:
            return None

        return {
            "intent": best_intent,
            "score": best_score,
            "matched_example": best_example,
            "method": "embedding",
        }

    def _lexical_match(self, query: str) -> dict[str, Any] | None:
        best_intent = None
        best_score = -1.0
        best_example = None

        for row in self._example_rows:
            score = self._lexical_score(query, row["normalized_example"])
            if score > best_score:
                best_score = score
                best_intent = row["intent"]
                best_example = row["example"]

        if best_intent is None:
            return None

        return {
            "intent": best_intent,
            "score": float(best_score),
            "matched_example": best_example,
            "method": "lexical",
        }

    def match(self, raw_query: str) -> dict[str, Any] | None:
        query = self.normalize(raw_query)

        embedding_result = self._embedding_match(query)
        lexical_result = self._lexical_match(query)

        candidates = [r for r in [embedding_result, lexical_result] if r is not None]
        if not candidates:
            return None

        best = max(candidates, key=lambda x: x["score"])

        if best["score"] < self.min_confidence:
            return None

        best["normalized_query"] = query
        return best