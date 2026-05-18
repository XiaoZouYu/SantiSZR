from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        timeout_sec: float = 60.0,
    ) -> None:
        self.api_key = self._normalize_api_key(api_key or os.getenv("SANTISZR_LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY"))
        self.api_base = (api_base or os.getenv("SANTISZR_LLM_API_BASE") or "https://api.deepseek.com/v1").rstrip("/")
        self.model = model or os.getenv("SANTISZR_LLM_MODEL") or "deepseek-chat"
        self.timeout_sec = timeout_sec
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_sec, connect=min(20.0, timeout_sec)),
            headers={
                "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
                "Content-Type": "application/json",
            },
            trust_env=False,
        )

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def provider_name(self) -> str:
        normalized = self.api_base.lower()
        if "deepseek" in normalized:
            return "deepseek"
        if "dashscope" in normalized:
            return "dashscope"
        return self.api_base

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        if not self.is_configured():
            raise RuntimeError("LLM API key is not configured.")

        payload = {
            "model": model or self.model,
            "temperature": temperature,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt or "You are a concise Chinese copywriting assistant.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        response = self._client.post(f"{self.api_base}/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]["content"]
        if isinstance(message, list):
            message = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in message
            )
        return LLMResponse(
            text=str(message).strip(),
            provider=self.provider_name(),
            model=payload["model"],
        )

    def _normalize_api_key(self, value: str | None) -> str | None:
        if not value:
            return None
        cleaned = value.strip()
        if cleaned.startswith("[") and cleaned.endswith("]"):
            try:
                import json

                data = json.loads(cleaned)
                if isinstance(data, list) and data:
                    return str(data[0]).strip()
            except Exception:
                return cleaned.strip("[]\"' ")
        return cleaned
