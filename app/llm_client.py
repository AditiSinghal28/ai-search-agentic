from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import settings


class LLMClient:
    """Small provider wrapper for Ollama or OpenAI-compatible chat APIs."""

    def __init__(self) -> None:
        self.provider = settings.llm_provider
        self.ollama_enabled = settings.ollama_enabled
        self.ollama_base_url = settings.ollama_base_url.rstrip("/")
        self.ollama_model = settings.ollama_model
        self.ollama_timeout = settings.ollama_timeout_seconds

        self.openai_api_key = settings.openai_api_key
        self.openai_base_url = settings.openai_base_url.rstrip("/")
        self.openai_model = settings.openai_model
        self.openai_timeout = settings.openai_timeout_seconds

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

    def _chat_ollama(self, *, system_prompt: str, user_prompt: str, json_mode: bool) -> str:
        if not self.ollama_enabled:
            raise RuntimeError("Ollama LLM is disabled.")
        payload: dict[str, Any] = {
            "model": self.ollama_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {"temperature": 0.1 if json_mode else 0.2},
        }
        if json_mode:
            payload["format"] = "json"
        with httpx.Client(timeout=self.ollama_timeout) as client:
            response = client.post(f"{self.ollama_base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        return data.get("message", {}).get("content", "").strip()

    def _chat_openai(self, *, system_prompt: str, user_prompt: str, json_mode: bool) -> str:
        if not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        payload: dict[str, Any] = {
            "model": self.openai_model,
            "temperature": 0.1 if json_mode else 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.openai_timeout) as client:
            response = client.post(f"{self.openai_base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

    def _chat(self, *, system_prompt: str, user_prompt: str, json_mode: bool) -> str:
        if self.provider == "none":
            raise RuntimeError("LLM fallback is disabled.")
        if self.provider == "openai":
            return self._chat_openai(system_prompt=system_prompt, user_prompt=user_prompt, json_mode=json_mode)
        return self._chat_ollama(system_prompt=system_prompt, user_prompt=user_prompt, json_mode=json_mode)

    def chat_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        return self._extract_json_object(self._chat(system_prompt=system_prompt, user_prompt=user_prompt, json_mode=True))

    def chat_text(self, system_prompt: str, user_prompt: str) -> str:
        return self._chat(system_prompt=system_prompt, user_prompt=user_prompt, json_mode=False)
