"""Execution sandbox: path jail + bounded command runner.

Nothing in the agent touches the filesystem or the shell without going through
a :class:`Sandbox`.  The jail is a *deny-by-default* allow-list of roots; the
command runner enforces timeouts, environment scrubbing, output caps and POSIX
resource limits.
"""
from __future__ import annotations

import os
import resource
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from ..errors import SandboxViolation
from .policy import Risk

#: Patterns that make an otherwise ordinary command destructive / privileged.
_PRIVILEGED_TOKENS = {"sudo", "su", "doas", "pkexec", "chroot", "nsenter"}
_DESTRUCTIVE_TOKENS = {
    "rm", "mkfs", "mkfs.ext4", "mkfs.xfs", "dd", "shred", "wipefs",
    "parted", "fdisk", "sgdisk", "shutdown", "reboot", "halt", "poweroff",
    "kill", "killall", "pkill", "systemctl", "service", "iptables",
    "userdel", "groupdel", "chown", "chmod", "chattr", "truncate",
    "mv", "format", "diskutil", "launchctl",
}
_DESTRUCTIVE_FLAGS = {"-r", "-rf", "-fr", "-f", "--no-preserve-root"}

_MAX_OUTPUT = 200_000  # bytes kept per stream
_DEFAULT_TIMEOUT = 30.0


def classify_command(argv: Sequence[str]) -> Risk:
    """Best-effort risk classification of a command line."""
    if not argv:
        return Risk.READ
    tokens = [Path(str(a)).name for a in argv]
    head = tokens[0]
    if head in _PRIVILEGED_TOKENS or any(t in _PRIVILEGED_TOKENS for t in tokens):
        return Risk.PRIVILEGED
    if head in {"curl", "wget", "nc", "ncat", "ssh", "scp", "rsync", "ftp", "sftp"}:
        return Risk.NETWORK
    if head in _DESTRUCTIVE_TOKENS:
        # mv/rm/chmod only become destructive when they recurse or force.
        if head in {"mv", "chmod", "chown", "truncate"}:
            if any(f in _DESTRUCTIVE_FLAGS for f in tokens) or _abs_target(argv):
                return Risk.DESTRUCTIVE
            return Risk.WRITE
        return Risk.DESTRUCTIVE
    if any(t in _DESTRUCTIVE_TOKENS for t in tokens[1:]):
        return Risk.DESTRUCTIVE
    if head in {"python", "python3", "node", "bash", "sh", "zsh", "perl", "ruby"}:
        return Risk.EXECUTE
    return Risk.EXECUTE


def _abs_target(argv: Sequence[str]) -> bool:
    for a in argv[1:]:
        if str(a).startswith("/"):
            return True
    return False


@dataclass
class CommandSpec:
    argv: List[str]
    cwd: Optional[str] = None
    timeout: float = _DEFAULT_TIMEOUT
    env: Optional[Dict[str, str]] = None
    stdin: Optional[str] = None
    label: str = ""

    @classmethod
    def parse(cls, command: str, **kw) -> "CommandSpec":
        return cls(argv=shlex.split(command), **kw)

    @property
    def rendered(self) -> str:
        return " ".join(shlex.quote(a) for a in self.argv)


@dataclass
class CommandResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    argv: List[str] = field(default_factory=list)

    def as_text(self, limit: int = 4000) -> str:
        body = self.stdout or self.stderr
        if len(body) > limit:
            body = body[:limit] + f"\n… [{len(body) - limit} bytes truncés]"
        return body


def _scrubbed_env(extra: Optional[Mapping[str, str]]) -> Dict[str, str]:
    """Keep a minimal, secret-free environment for child processes."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TZ", "SHELL", "USER", "LOGNAME")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PATH"] = env.get("PATH", "/usr/bin:/bin")
    # Deliberately drop anything that looks like a credential.
    for key in list(os.environ):
        upper = key.upper()
        if any(s in upper for s in ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL")):
            env.pop(key, None)
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def _apply_rlimits() -> None:  # pragma: no cover - exercised in subprocess
    limits = [
        (resource.RLIMIT_CPU, 30),
        (resource.RLIMIT_FSIZE, 64 * 1024 * 1024),
        (resource.RLIMIT_NOFILE, 256),
    ]
    for what, value in limits:
        try:
            resource.setrlimit(what, (value, value))
        except (ValueError, OSError):
            pass
    try:
        mem = 1024 * 1024 * 1024  # 1 GiB address space
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    except (ValueError, OSError):
        pass


class Sandbox:
    """Path jail + bounded subprocess runner."""

    def __init__(
        self,
        allowed_roots: Optional[Sequence[str]] = None,
        *,
        allow_outside_roots: bool = False,
        default_timeout: float = _DEFAULT_TIMEOUT,
        allow_network_commands: bool = True,
    ) -> None:
        if allowed_roots is None:
            home = Path.home()
            allowed_roots = [str(home)]
        self.allowed_roots = [Path(r).expanduser().resolve() for r in allowed_roots]
        self.allow_outside_roots = allow_outside_roots
        self.default_timeout = default_timeout
        self.allow_network_commands = allow_network_commands

    # -- path jail ---------------------------------------------------------
    def resolve(self, path: "str | Path") -> Path:
        """Resolve *path* and assert it stays inside the jail."""
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        resolved = p.resolve()
        if self.allow_outside_roots:
            return resolved
        for root in self.allowed_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise SandboxViolation(
            f"path {resolved} is outside the sandbox roots "
            f"({', '.join(str(r) for r in self.allowed_roots)})"
        )

    def contains(self, path: "str | Path") -> bool:
        try:
            self.resolve(path)
            return True
        except SandboxViolation:
            return False

    # -- command execution -------------------------------------------------
    def run(self, spec: CommandSpec) -> CommandResult:
        argv = list(spec.argv)
        if not argv:
            raise SandboxViolation("empty command")
        if not self.allow_network_commands and classify_command(argv) == Risk.NETWORK:
            raise SandboxViolation(f"network command disabled: {argv[0]}")

        cwd = spec.cwd
        if cwd is not None:
            cwd = str(self.resolve(cwd))

        # Binary must live somewhere sane (no PATH-trick into a jail dir).
        exe = argv[0]
        if "/" in exe and not self.contains(exe):
            raise SandboxViolation(f"refusing to exec {exe}: outside the jail")

        timeout = spec.timeout or self.default_timeout
        started = time.monotonic()
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv list, shell=False
                argv,
                cwd=cwd,
                env=_scrubbed_env(spec.env),
                stdin=subprocess.PIPE if spec.stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                preexec_fn=_apply_rlimits if os.name == "posix" else None,
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            return CommandResult(
                ok=False,
                returncode=127,
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                duration=time.monotonic() - started,
                argv=argv,
            )

        timed_out = False
        try:
            out, err = proc.communicate(input=spec.stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
            try:
                out, err = proc.communicate(timeout=5)
            except Exception:  # pragma: no cover
                out, err = "", ""
        duration = time.monotonic() - started
        return CommandResult(
            ok=(not timed_out) and proc.returncode == 0,
            returncode=-1 if timed_out else int(proc.returncode or 0),
            stdout=(out or "")[:_MAX_OUTPUT],
            stderr=(err or "")[:_MAX_OUTPUT],
            duration=duration,
            timed_out=timed_out,
            argv=argv,
        )


def _kill_group(proc: "subprocess.Popen[str]") -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except Exception:  # pragma: no cover
            pass
