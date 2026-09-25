"""Append-only, hash-chained audit log.

Every privileged decision the agent takes is written here: the tool, its
arguments (after redaction), the policy verdict, who approved it and the
outcome.  Records are chained with SHA-256 so a tamper attempt is detectable
via :meth:`AuditLog.verify`.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .redaction import redact_object

_GENESIS = "0" * 64


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


class AuditLog:
    def __init__(self, path: "str | Path | None" = None) -> None:
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._records: List[Dict[str, Any]] = []
        self._seq = 0
        if self._path and self._path.exists():
            self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        assert self._path is not None
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                self._records.append(json.loads(line))
        self._seq = len(self._records)

    def append(
        self,
        event: str,
        *,
        action: str = "",
        target: str = "",
        actor: str = "agent",
        verdict: str = "",
        approved_by: str = "",
        outcome: str = "",
        detail: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            prev_hash = (
                self._records[-1]["hash"] if self._records else _GENESIS
            )
            payload: Dict[str, Any] = {
                "seq": self._seq,
                "ts": _now(),
                "event": event,
                "action": action,
                "target": target,
                "actor": actor,
                "verdict": verdict,
                "approved_by": approved_by,
                "outcome": outcome,
                "detail": redact_object(detail or {}),
                "prev": prev_hash,
            }
            payload["hash"] = hashlib.sha256(
                _canonical(payload).encode("utf-8")
            ).hexdigest()
            self._records.append(payload)
            self._seq += 1
            self._flush(payload)
            return payload

    def _flush(self, record: Dict[str, Any]) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = _canonical(record) + "\n"
        fd = os.open(str(self._path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    # -- reading / verification -------------------------------------------
    @property
    def path(self) -> "Path | None":
        """Journal file, or ``None`` when the log is memory-only."""
        return self._path

    def records(self) -> List[Dict[str, Any]]:
        return list(self._records)

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self._records)

    def verify(self) -> bool:
        """Recompute the hash chain; ``False`` means a record was altered."""
        prev = _GENESIS
        for i, rec in enumerate(self._records):
            body = dict(rec)
            stored = body.pop("hash", "")
            if body.get("prev") != prev:
                return False
            if body.get("seq") != i:
                return False
            recomputed = hashlib.sha256(
                _canonical(body).encode("utf-8")
            ).hexdigest()
            if recomputed != stored:
                return False
            prev = stored
        return True

    @staticmethod
    def default_path() -> Path:
        base = os.environ.get("AIOS_STATE_DIR")
        if base:
            return Path(base) / "audit.jsonl"
        return Path.home() / ".local" / "share" / "aios" / "audit.jsonl"
