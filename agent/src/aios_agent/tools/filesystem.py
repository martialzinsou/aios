from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from ..security.policy import Risk
from .base import Tool, ToolContext, ToolResult

_MAX_READ = 512 * 1024
_MAX_LINES = 4000
_MAX_HITS = 200


class ReadFile(Tool):
    name = "read_file"
    description = "Read a UTF-8 text file inside the sandbox."
    risk = Risk.READ
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to read"},
            "offset": {"type": "integer", "description": "1-based line offset", "default": 1},
            "limit": {"type": "integer", "description": "Max lines to return", "default": 200},
        },
        "required": ["path"],
    }

    def target(self, args: Dict[str, Any]) -> str:
        return str(args.get("path", ""))

    def summary(self, args: Dict[str, Any]) -> str:
        return f"Lire {args.get('path', '?')}"

    def execute(self, ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
        path = ctx.jail(self.require_str(args, "path"))
        p = Path(path)
        if not p.exists():
            return ToolResult.failure(f"not found: {path}")
        if p.is_dir():
            return ToolResult.failure(f"is a directory: {path}")
        if p.stat().st_size > _MAX_READ:
            return ToolResult.failure(f"file too large (> {_MAX_READ} bytes): {path}")

        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult.failure(f"not a text file: {path}")

        lines = text.splitlines()
        offset = max(1, int(args.get("offset", 1) or 1))
        limit = min(int(args.get("limit", 200) or 200), _MAX_LINES)
        window = lines[offset - 1 : offset - 1 + limit]
        body = "\n".join(
            f"{i + offset:6d}\t{line}" for i, line in enumerate(window)
        )
        header = f"# {path} — {len(lines)} lignes, affichage {offset}..{offset + len(window) - 1}"
        return ToolResult.success(f"{header}\n{body}", data={"lines": len(lines)})


class WriteFile(Tool):
    name = "write_file"
    description = "Create or overwrite a text file inside the sandbox."
    risk = Risk.WRITE
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to write"},
            "content": {"type": "string", "description": "New content"},
            "append": {"type": "boolean", "default": False},
        },
        "required": ["path", "content"],
    }

    def target(self, args: Dict[str, Any]) -> str:
        return str(args.get("path", ""))

    def summary(self, args: Dict[str, Any]) -> str:
        n = len(str(args.get("content", "")))
        verb = "Ajouter" if args.get("append") else "Écrire/écraser"
        return f"{verb} {n} octets dans {args.get('path', '?')}"

    def execute(self, ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
        raw_path = self.require_str(args, "path")
        content = args.get("content")
        if not isinstance(content, str):
            return ToolResult.failure("content must be a string")
        path = ctx.jail(raw_path)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if args.get("append") else "w"
        with p.open(mode, encoding="utf-8") as fh:
            fh.write(content)
        verb = "appended to" if mode == "a" else "wrote"
        return ToolResult.success(f"{verb} {p} ({len(content)} chars)")


class ListDir(Tool):
    name = "list_dir"
    description = "List the entries of a directory inside the sandbox."
    risk = Risk.READ
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to list"},
            "show_hidden": {"type": "boolean", "default": False},
        },
        "required": ["path"],
    }

    def target(self, args: Dict[str, Any]) -> str:
        return str(args.get("path", ""))

    def summary(self, args: Dict[str, Any]) -> str:
        return f"Lister {args.get('path', '?')}"

    def execute(self, ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
        path = ctx.jail(self.require_str(args, "path"))
        p = Path(path)
        if not p.exists():
            return ToolResult.failure(f"not found: {path}")
        if not p.is_dir():
            return ToolResult.failure(f"not a directory: {path}")
        show_hidden = bool(args.get("show_hidden", False))
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        rows: List[str] = []
        for entry in entries:
            if not show_hidden and entry.name.startswith("."):
                continue
            try:
                size = entry.stat().st_size if entry.is_file() else "-"
            except OSError:
                size = "?"
            kind = "d" if entry.is_dir() else ("l" if entry.is_symlink() else "f")
            rows.append(f"{kind} {str(size):>10}  {entry.name}")
        if not rows:
            return ToolResult.success(f"(vide) {path}")
        return ToolResult.success("\n".join(rows[:500]), data={"count": len(rows)})


class Search(Tool):
    name = "search"
    description = (
        "Search files by name glob and/or by content regex inside a directory."
    )
    risk = Risk.READ
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Regex for content, or name pattern"},
            "root": {"type": "string", "description": "Directory to search", "default": "."},
            "mode": {"type": "string", "enum": ["content", "name"], "default": "content"},
            "file_glob": {"type": "string", "description": "Restrict to e.g. '*.py'", "default": "*"},
            "max_hits": {"type": "integer", "default": 50},
        },
        "required": ["query"],
    }

    def target(self, args: Dict[str, Any]) -> str:
        return str(args.get("root", ""))

    def summary(self, args: Dict[str, Any]) -> str:
        return f"Chercher {args.get('query', '?')!r} dans {args.get('root', '.')}" 

    def execute(self, ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
        query = self.require_str(args, "query")
        root = ctx.jail(str(args.get("root") or "."))
        mode = str(args.get("mode") or "content")
        file_glob = str(args.get("file_glob") or "*")
        max_hits = min(int(args.get("max_hits") or 50), _MAX_HITS)
        base = Path(root)
        if not base.exists():
            return ToolResult.failure(f"not found: {root}")

        if mode == "name":
            try:
                if any(c in query for c in "*?["):
                    pattern = re.compile(fnmatch.translate(query))
                else:
                    pattern = re.compile(query)
            except re.error as exc:
                return ToolResult.failure(f"bad pattern: {exc}")
            hits: List[str] = []
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in filenames:
                    rel = str(Path(dirpath, fn).relative_to(base))
                    if pattern.search(rel):
                        hits.append(rel)
                        if len(hits) >= max_hits:
                            break
                if len(hits) >= max_hits:
                    break
            return ToolResult.success("\n".join(hits) or "(aucun résultat)",
                                       data={"hits": len(hits)})

        try:
            regex = re.compile(query)
        except re.error as exc:
            return ToolResult.failure(f"bad regex: {exc}")

        found: List[str] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if not fnmatch.fnmatch(fn, file_glob):
                    continue
                fp = Path(dirpath, fn)
                try:
                    if fp.stat().st_size > _MAX_READ:
                        continue
                    text = fp.read_text("utf-8", errors="ignore")
                except (OSError, UnicodeDecodeError):
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        rel = fp.relative_to(base)
                        found.append(f"{rel}:{i}: {line.strip()[:200]}")
                        if len(found) >= max_hits:
                            break
                if len(found) >= max_hits:
                    break
            if len(found) >= max_hits:
                break
        return ToolResult.success("\n".join(found) or "(aucun résultat)",
                                  data={"hits": len(found)})


class DeletePath(Tool):
    name = "delete_path"
    description = "Delete a file or an empty directory. Requires confirmation."
    risk = Risk.DESTRUCTIVE
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to delete"},
            "recursive": {"type": "boolean", "default": False},
        },
        "required": ["path"],
    }

    def target(self, args: Dict[str, Any]) -> str:
        return str(args.get("path", ""))

    def summary(self, args: Dict[str, Any]) -> str:
        rec = " (récursif)" if args.get("recursive") else ""
        return f"Supprimer définitivement {args.get('path', '?')}{rec}"

    def execute(self, ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
        import shutil

        path = ctx.jail(self.require_str(args, "path"))
        p = Path(path)
        if not p.exists() and not p.is_symlink():
            return ToolResult.failure(f"not found: {path}")
        # Refuse to delete the jail root itself.
        for root in ctx.sandbox.allowed_roots:
            if p.resolve() == root:
                return ToolResult.failure("refusing to delete a sandbox root")
        if p.is_dir():
            if args.get("recursive"):
                shutil.rmtree(p)
            else:
                p.rmdir()
        else:
            p.unlink()
        return ToolResult.success(f"deleted {path}")


DEFAULT_FS_TOOLS: List[Tool] = [
    ReadFile(),
    WriteFile(),
    ListDir(),
    Search(),
    DeletePath(),
]
