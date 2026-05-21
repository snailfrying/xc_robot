from __future__ import annotations

import base64
import json
import re
from datetime import datetime


def sanitize_text(value: object) -> str:
    return str(value or "").strip()


def normalize_text(value: object) -> str:
    text = sanitize_text(value).lower()
    text = re.sub(r"[\s_\-]+", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return text


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def image_bytes_to_data_url(content_type: str, payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{content_type};base64,{encoded}"
