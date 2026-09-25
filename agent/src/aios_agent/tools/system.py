from __future__ import annotations

import os
import platform
import shutil
from typing import Any, Dict, List

from ..security.policy import Risk
from .base import Tool, ToolContext, ToolResult


class SystemInfo(Tool):
    name = "system_info"
    description = "Report OS, CPU, memory and disk usage of this machine."
    risk = Risk.READ
    parameters = {"type": "object", "properties": {}}

    def execute(self, ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
        lines = [
            f"system   : {platform.system()} {platform.release()}",
            f"machine  : {platform.machine()}",
            f"python   : {platform.python_version()}",
            f"cores    : {os.cpu_count()}",
            f"hostname : {platform.node()}",
        ]
        try:
            u = shutil.disk_usage("/")
            lines.append(
                f"disk     : {u.used // 2**30}G utilisés / {u.total // 2**30}G"
            )
        except OSError:
            pass
        mem = _memory_info()
        if mem:
            lines.append(mem)
        return ToolResult.success("\n".join(lines))


def _memory_info() -> str:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            data = {}
            for line in fh:
                key, _, rest = line.partition(":")
                data[key.strip()] = rest.strip()
        total = data.get("MemTotal", "")
        avail = data.get("MemAvailable", "")
        if total and avail:
            return f"memory   : {avail} libres / {total}"
    except OSError:
        pass
    return ""


class ListProcesses(Tool):
    name = "list_processes"
    description = "List running processes (pid, name, cpu% when available)."
    risk = Risk.READ
    parameters = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 40},
        },
    }

    def execute(self, ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
        limit = min(int(args.get("limit") or 40), 300)
        rows = _read_proc()
        if not rows:
            return ToolResult.success("(aucune information /proc disponible)")
        rows.sort(key=lambda r: r[2], reverse=True)
        out = ["pid      cpu%   name"]
        for pid, name, cpu in rows[:limit]:
            out.append(f"{pid:<8} {cpu:5.1f}  {name}")
        return ToolResult.success("\n".join(out), data={"count": len(rows)})


def _read_proc() -> List[tuple]:
    if not os.path.isdir("/proc"):
        return []
    rows: List[tuple] = []
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            name, cpu = "?", 0.0
            try:
                with open(f"/proc/{pid}/comm", "r", encoding="utf-8") as fh:
                    name = fh.read().strip()
            except OSError:
                pass
            try:
                with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as fh:
                    fields = fh.read().split()
                utime = int(fields[13])
                stime = int(fields[14])
                cpu = round((utime + stime) / os.sysconf("SC_CLK_TCK"), 1)
            except (OSError, IndexError, ValueError, ValueError):
                pass
            rows.append((pid, name, cpu))
    except OSError:
        return []
    return rows


DEFAULT_SYSTEM_TOOLS: List[Tool] = [SystemInfo(), ListProcesses()]
