from __future__ import annotations

import json
import time
from typing import Iterable

from ..errors import PlannerError
from ..planner.llm_interface import parse_llm_json_object
from ..planner.llm_interface import post_chat_completion


class OpenAICompatibleClient:
    def __init__(self, settings, logger):
        self._settings = settings
        self._logger = logger

    @property
    def enabled(self) -> bool:
        return bool(
            self._settings.llm.enabled
            and self._settings.llm.api_url
            and self._settings.llm.api_key
            and self._settings.llm.model_name
        )

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, object],
        response_label: str,
        image_data_urls: Iterable[str] | None = None,
    ) -> dict[str, object]:
        if not self.enabled:
            raise PlannerError("llm_backend_disabled")
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": json.dumps(user_payload, ensure_ascii=False),
            }
        ]
        for data_url in image_data_urls or []:
            if data_url:
                content.append({"type": "image_url", "image_url": {"url": data_url}})
        payload = {
            "model": self._settings.llm.model_name,
            "temperature": self._settings.llm.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        image_count = max(0, len(content) - 1)
        max_attempts = max(1, self._settings.llm.max_retries + 1)
        last_error: Exception | None = None
        for attempt_index in range(1, max_attempts + 1):
            self._logger.info(
                "llm request start: label=%s model=%s attempt=%s/%s images=%s",
                response_label,
                self._settings.llm.model_name,
                attempt_index,
                max_attempts,
                image_count,
            )
            started = time.monotonic()
            try:
                response = post_chat_completion(
                    api_url=self._settings.llm.api_url,
                    api_key=self._settings.llm.api_key,
                    request_timeout_sec=self._settings.llm.request_timeout_sec,
                    payload=payload,
                )
                raw_content = response["choices"][0]["message"]["content"]
                parsed = parse_llm_json_object(raw_content, response_label=response_label)
                self._logger.info(
                    "llm request done: label=%s attempt=%s/%s elapsed=%.3fs",
                    response_label,
                    attempt_index,
                    max_attempts,
                    time.monotonic() - started,
                )
                return parsed
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self._logger.error(
                    "llm request failed: label=%s attempt=%s/%s elapsed=%.3fs error=%s",
                    response_label,
                    attempt_index,
                    max_attempts,
                    time.monotonic() - started,
                    exc,
                )
                if attempt_index >= max_attempts:
                    break
        raise PlannerError(f"llm_request_failed:{response_label}:{last_error}")
