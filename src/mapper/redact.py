import re
from typing import Any

EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_REGEX = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
API_KEY_REGEX = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|AQ\.[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}"
    r"|bearer\s+[A-Za-z0-9_.-]{20,}|api[_-]?key[_-]?[=:]\s*[\"']?[A-Za-z0-9_.-]{16,})",
    re.IGNORECASE,
)


def redact_text(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    text = API_KEY_REGEX.sub("[REDACTED_API_KEY]", text)
    text = EMAIL_REGEX.sub("[REDACTED_EMAIL]", text)
    text = PHONE_REGEX.sub("[REDACTED_PHONE]", text)
    return text


def redact_data(data: Any) -> Any:
    if isinstance(data, str):
        return redact_text(data)
    if isinstance(data, dict):
        return {k: redact_data(v) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_data(item) for item in data]
    return data