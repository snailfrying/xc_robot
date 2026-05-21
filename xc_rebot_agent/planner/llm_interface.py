from __future__ import annotations

import json
import time
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse

from ..errors import PlannerError


def response_content_to_text(raw_content: object) -> str:
    if isinstance(raw_content, list):
        return "".join(part.get("text", "") for part in raw_content if isinstance(part, dict))
    return str(raw_content)


def strip_json_fence(content_text: str) -> str:
    stripped = content_text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_llm_json_object(raw_content: object, *, response_label: str) -> dict[str, object]:
    text = response_content_to_text(raw_content)
    candidates = [text]
    stripped = strip_json_fence(text)
    if stripped != text:
        candidates.append(stripped)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise PlannerError(f"{response_label}_json_parse_failed")


def normalize_api_url(raw_url: str) -> str:
    normalized = raw_url.strip()
    if not normalized:
        return normalized
    parsed = urlparse(normalized)
    if parsed.path.endswith("/chat/completions") or parsed.path.endswith("/v1/chat/completions"):
        return normalized
    if parsed.path.endswith("/openai") or parsed.path.endswith("/v1"):
        return normalized.rstrip("/") + "/chat/completions"
    return normalized.rstrip("/") + "/v1/chat/completions"


def post_chat_completion(
    *,
    api_url: str,
    api_key: str,
    request_timeout_sec: float,
    payload: dict[str, object],
) -> dict[str, object]:
    started_at = time.monotonic()
    request = urllib_request.Request(
        normalize_api_url(api_url),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=request_timeout_sec) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        body_preview = exc.read().decode("utf-8", errors="replace")[:500]
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        raise PlannerError(
            f"llm_http_status_error:code={exc.code},elapsed_ms={elapsed_ms:.1f},body={body_preview}"
        ) from exc
    except urllib_error.URLError as exc:
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        raise PlannerError(f"llm_transport_error:{exc},elapsed_ms={elapsed_ms:.1f}") from exc
