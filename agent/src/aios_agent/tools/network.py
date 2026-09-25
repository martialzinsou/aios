from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any, Dict, List
from urllib.parse import urlparse

from ..security.policy import Risk
from .base import Tool, ToolContext, ToolResult

_MAX_BYTES = 200_000
_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class HttpGet(Tool):
    """Outbound HTTP GET.

    Always ``NETWORK`` risk → the baseline policy demands an explicit human
    confirmation before any bytes leave the machine.
    """

    name = "http_get"
    description = "Fetch a URL over HTTP(S) and return its text body."
    risk = Risk.NETWORK
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL"},
            "max_bytes": {"type": "integer", "default": 50000},
        },
        "required": ["url"],
    }

    def target(self, args: Dict[str, Any]) -> str:
        return str(args.get("url", ""))

    def summary(self, args: Dict[str, Any]) -> str:
        return f"Envoyer une requête HTTP vers {args.get('url', '?')}"

    def execute(self, ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
        url = self.require_str(args, "url")
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            return ToolResult.failure(
                f"scheme not allowed: {parsed.scheme or '(none)'}", Risk.NETWORK
            )
        if (parsed.hostname or "").lower() in _BLOCKED_HOSTS:
            return ToolResult.failure("loopback targets are blocked", Risk.NETWORK)

        limit = min(int(args.get("max_bytes") or 50000), _MAX_BYTES)
        req = urllib.request.Request(url, headers={"User-Agent": "aiOS-agent/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read(limit)
                status = getattr(resp, "status", 200)
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            return ToolResult.failure(f"HTTP {exc.code}: {exc.reason}", Risk.NETWORK)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            return ToolResult.failure(f"request failed: {exc}", Risk.NETWORK)

        text = body.decode("utf-8", errors="replace")
        header = f"[{status}] {ctype} — {len(body)} octets"
        return ToolResult.success(f"{header}\n{text}", risk=Risk.NETWORK)


DEFAULT_NET_TOOLS: List[Tool] = [HttpGet()]
