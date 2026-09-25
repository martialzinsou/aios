from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from aios_agent.errors import SandboxViolation
from aios_agent.security.policy import Risk
from aios_agent.security.sandbox import CommandSpec, Sandbox, classify_command


def test_jail_allows_inside_paths(tmp_path: Path):
    jail = tmp_path / "jail"
    jail.mkdir()
    sb = Sandbox([str(jail)])
    assert sb.resolve(jail / "a" / ".." / "b") == (jail / "b").resolve()
    assert sb.contains(jail / "file.txt")


def test_jail_rejects_outside_paths(tmp_path: Path):
    jail = tmp_path / "jail"
    jail.mkdir()
    sb = Sandbox([str(jail)])
    with pytest.raises(SandboxViolation):
        sb.resolve(tmp_path / "outside.txt")
    with pytest.raises(SandboxViolation):
        sb.resolve("/etc/passwd")
    assert not sb.contains("/etc/passwd")


def test_jail_rejects_symlink_escape(tmp_path: Path):
    jail = tmp_path / "jail"
    jail.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", "utf-8")
    link = jail / "link.txt"
    link.symlink_to(secret)
    sb = Sandbox([str(jail)])
    with pytest.raises(SandboxViolation):
        sb.resolve(link)


def test_classify_command_risk_levels():
    assert classify_command(["ls", "-la"]) == Risk.EXECUTE
    assert classify_command(["rm", "-rf", "/tmp/x"]) == Risk.DESTRUCTIVE
    assert classify_command(["sudo", "ls"]) == Risk.PRIVILEGED
    assert classify_command(["curl", "http://example.com"]) == Risk.NETWORK
    assert classify_command(["dd", "if=/dev/zero"]) == Risk.DESTRUCTIVE
    assert classify_command(["chmod", "777", "/etc"]) == Risk.DESTRUCTIVE


def test_run_command_success(tmp_path: Path):
    sb = Sandbox([str(tmp_path)])
    res = sb.run(CommandSpec.parse("echo hello-aios"))
    assert res.ok
    assert "hello-aios" in res.stdout


def test_run_command_timeout(tmp_path: Path):
    sb = Sandbox([str(tmp_path)])
    started = time.monotonic()
    res = sb.run(CommandSpec.parse("sleep 5", timeout=0.4))
    assert res.timed_out
    assert not res.ok
    assert time.monotonic() - started < 4


def test_run_command_missing_binary(tmp_path: Path):
    sb = Sandbox([str(tmp_path)])
    res = sb.run(CommandSpec.parse("definitely-not-a-real-binary-xyz"))
    assert not res.ok
    assert res.returncode == 127


def test_env_is_scrubbed_of_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "super-secret-value")
    sb = Sandbox([str(tmp_path)])
    res = sb.run(CommandSpec.parse("printenv MY_API_TOKEN"))
    assert "super-secret-value" not in (res.stdout + res.stderr)
    assert res.returncode == 1


def test_network_commands_can_be_disabled(tmp_path: Path):
    sb = Sandbox([str(tmp_path)], allow_network_commands=False)
    with pytest.raises(SandboxViolation):
        sb.run(CommandSpec.parse("curl http://example.com"))


def test_refuses_exec_outside_jail(tmp_path: Path):
    jail = tmp_path / "jail"
    jail.mkdir()
    sb = Sandbox([str(jail)])
    with pytest.raises(SandboxViolation):
        sb.run(CommandSpec.parse("/bin/echo nope"))


def test_stdin_is_forwarded(tmp_path: Path):
    sb = Sandbox([str(tmp_path)])
    res = sb.run(CommandSpec.parse("cat", stdin="piped input"))
    assert "piped input" in res.stdout
