from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import settings


class LLMClient:
    def __init__(self) -> None:
        self.enabled = settings.ollama_enabled
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        self.timeout = settings.ollama_timeout_seconds

    def _extract_json_object(self, text: str) -> dict[str, Any]:
        text = text.strip()
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else {"value": value}
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {"value": value}
            raise

    def chat_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("LLM is disabled.")

        payload = {
            "model": self.model,
            "format": "json",
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {"temperature": 0.1},
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        content = data.get("message", {}).get("content", "{}").strip()
        return self._extract_json_object(content)

    def chat_text(self, system_prompt: str, user_prompt: str) -> str:
        if not self.enabled:
            raise RuntimeError("LLM is disabled.")

        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {"temperature": 0.2},
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        return data.get("message", {}).get("content", "").strip()
