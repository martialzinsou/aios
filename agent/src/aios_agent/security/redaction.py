"""Secret / PII redaction.

Anything the agent echoes back to a model or writes to a log goes through
``redact`` first, so an accidentally-read credential never leaves the machine
nor lands in the audit trail.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

_MASK = "•" * 8

#: (compiled pattern, replacement) — order matters, specific before generic.
_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # AWS access key id
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), _MASK),
    # GitHub tokens
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), _MASK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), _MASK),
    # Slack
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), _MASK),
    # Google API key
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), _MASK),
    # OpenAI / Anthropic style keys
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), _MASK),
    # JWT
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"), _MASK),
    # Private keys
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), _MASK),
    # key = value  /  key: value   (password, token, secret, api_key, ...)
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key"
            r"|auth|credential|private[_-]?key)\b(\s*[=:]\s*)([^\s'\"]+)"
        ),
        r"\1\2" + _MASK,
    ),
    # Authorization: Bearer xxx
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{8,}"), r"\1 " + _MASK),
    # Credit card numbers (13-19 digits with optional separators)
    (re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), _MASK),
    # Email addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), _MASK),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


_SENSITIVE_KEY = re.compile(
    r"(?i)^(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key"
    r"|auth|authorization|credential|credentials|private[_-]?key)$"
)


def redact_object(obj: Any) -> Any:
    """Recursively redact strings (and sensitive keys) inside dicts/lists."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SENSITIVE_KEY.match(k):
                out[k] = _MASK if v not in (None, "", False) else v
            else:
                out[k] = redact_object(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_object(v) for v in obj]
    return obj
