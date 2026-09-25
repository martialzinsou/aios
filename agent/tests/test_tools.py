from __future__ import annotations

from pathlib import Path

import pytest

from aios_agent.security.audit import AuditLog
from aios_agent.security.confirmation import AlwaysAllow, AlwaysDeny
from aios_agent.security.permission import PermissionManager
from aios_agent.security.policy import Risk, StaticPolicy
from aios_agent.security.sandbox import Sandbox
from aios_agent.tools import default_registry
from aios_agent.tools.base import ToolContext


def make_ctx(jail: Path, allow: bool = False, audit: AuditLog | None = None) -> ToolContext:
    audit = audit or AuditLog()
    return ToolContext(
        sandbox=Sandbox([str(jail)]),
        permissions=PermissionManager(
            policy=StaticPolicy.default(),
            confirmer=AlwaysAllow() if allow else AlwaysDeny(),
            audit=audit,
        ),
    )


def test_registry_lists_default_tools():
    names = default_registry().names()
    assert {"read_file", "write_file", "list_dir", "search",
            "delete_path", "run_command", "system_info", "http_get"} <= set(names)


def test_read_file_is_allowed_without_confirmation(jail: Path):
    reg = default_registry()
    res = reg.call("read_file", {"path": str(jail / "docs" / "readme.txt")},
                   ctx=make_ctx(jail))
    assert res.ok
    assert "hello aiOS" in res.output


def test_read_file_outside_jail_is_blocked(jail: Path, tmp_path: Path):
    reg = default_registry()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", "utf-8")
    res = reg.call("read_file", {"path": str(outside)}, ctx=make_ctx(jail))
    assert not res.ok
    assert "sandbox" in res.error


def test_read_binary_file_reports_error(jail: Path):
    reg = default_registry()
    res = reg.call("read_file", {"path": str(jail / "data.bin")}, ctx=make_ctx(jail))
    assert not res.ok
    assert "text" in res.error


def test_write_needs_confirmation_and_is_denied_by_default(jail: Path, audit: AuditLog):
    reg = default_registry()
    ctx = make_ctx(jail, allow=False, audit=audit)
    target = jail / "out.txt"
    res = reg.call("write_file", {"path": str(target), "content": "hi"}, ctx=ctx)
    assert not res.ok
    assert "refused" in res.error
    assert not target.exists()
    assert any(r["outcome"] == "rejected" for r in audit.records())


def test_write_succeeds_when_approved(jail: Path):
    reg = default_registry()
    target = jail / "out.txt"
    res = reg.call("write_file", {"path": str(target), "content": "hi"},
                   ctx=make_ctx(jail, allow=True))
    assert res.ok
    assert target.read_text("utf-8") == "hi"


def test_write_into_jail_subdir_creates_parents(jail: Path):
    reg = default_registry()
    target = jail / "new" / "deep" / "f.txt"
    res = reg.call("write_file", {"path": str(target), "content": "x"},
                   ctx=make_ctx(jail, allow=True))
    assert res.ok and target.exists()


def test_delete_is_denied_by_default(jail: Path, audit: AuditLog):
    reg = default_registry()
    victim = jail / "docs" / "notes.md"
    res = reg.call("delete_path", {"path": str(victim)},
                   ctx=make_ctx(jail, allow=False, audit=audit))
    assert not res.ok
    assert victim.exists()
    rec = [r for r in audit.records() if r["action"] == "delete_path"][0]
    assert rec["outcome"] == "rejected"


def test_delete_succeeds_when_approved(jail: Path):
    reg = default_registry()
    victim = jail / "docs" / "notes.md"
    res = reg.call("delete_path", {"path": str(victim)}, ctx=make_ctx(jail, allow=True))
    assert res.ok
    assert not victim.exists()


def test_delete_refuses_sandbox_root(jail: Path):
    reg = default_registry()
    res = reg.call("delete_path", {"path": str(jail)}, ctx=make_ctx(jail, allow=True))
    assert not res.ok
    assert jail.exists()


def test_list_dir(jail: Path):
    reg = default_registry()
    res = reg.call("list_dir", {"path": str(jail)}, ctx=make_ctx(jail))
    assert res.ok
    assert "docs" in res.output


def test_search_content(jail: Path):
    reg = default_registry()
    res = reg.call("search", {"query": "search me", "root": str(jail)},
                   ctx=make_ctx(jail))
    assert res.ok
    assert "notes.md" in res.output


def test_search_by_name_glob(jail: Path):
    reg = default_registry()
    res = reg.call("search", {"query": "*.md", "root": str(jail), "mode": "name"},
                   ctx=make_ctx(jail))
    assert res.ok
    assert "docs/notes.md" in res.output


def test_unknown_tool_is_reported_not_raised(jail: Path):
    reg = default_registry()
    res = reg.call("does_not_exist", {}, ctx=make_ctx(jail))
    assert not res.ok
    assert "unknown tool" in res.error


def test_run_command_requires_confirmation(jail: Path, audit: AuditLog):
    reg = default_registry()
    res = reg.call("run_command", {"command": "echo allowed"},
                   ctx=make_ctx(jail, allow=False, audit=audit))
    assert not res.ok
    assert "refused" in res.error


def test_run_command_when_approved(jail: Path):
    reg = default_registry()
    res = reg.call("run_command", {"command": "echo works"},
                   ctx=make_ctx(jail, allow=True))
    assert res.ok
    assert "works" in res.output


def test_sudo_is_refused_even_when_operator_would_approve(jail: Path):
    reg = default_registry()
    ctx = make_ctx(jail, allow=True)
    res = reg.call("run_command", {"command": "sudo rm -rf /"}, ctx=ctx)
    assert not res.ok
    assert "denied" in res.error


def test_risk_escalation_for_destructive_command(jail: Path, audit: AuditLog):
    from aios_agent.security.sandbox import classify_command
    from aios_agent.security.policy import Risk
    from aios_agent.tools.shell import RunCommand

    assert classify_command(["rm", "-rf", "/tmp/x"]) == Risk.DESTRUCTIVE
    tool = RunCommand()
    assert tool.risk_for({"command": "ls"}) == Risk.EXECUTE
    assert tool.risk_for({"command": "rm -rf /tmp/x"}) == Risk.DESTRUCTIVE
    assert tool.risk_for({"command": "sudo ls"}) == Risk.PRIVILEGED

    res = default_registry().call(
        "run_command", {"command": "rm -rf /tmp/whatever"},
        ctx=make_ctx(jail, allow=False, audit=audit),
    )
    assert not res.ok
    auth = [r for r in audit.records()
            if r["event"] == "authorize" and r["action"] == "run_command"][0]
    assert auth["outcome"] == "rejected"
    assert auth["verdict"] == "confirm"


def test_http_get_blocked_by_default(jail: Path, audit: AuditLog):
    reg = default_registry()
    res = reg.call("http_get", {"url": "http://example.com"},
                   ctx=make_ctx(jail, allow=False, audit=audit))
    assert not res.ok
    assert "refused" in res.error


def test_http_get_rejects_bad_scheme_when_approved(jail: Path):
    reg = default_registry()
    res = reg.call("http_get", {"url": "file:///etc/passwd"},
                   ctx=make_ctx(jail, allow=True))
    assert not res.ok
    assert "scheme" in res.error


def test_system_info_runs_without_confirmation(jail: Path):
    reg = default_registry()
    res = reg.call("system_info", {}, ctx=make_ctx(jail))
    assert res.ok
    assert "system" in res.output


def test_every_tool_declares_a_risk():
    for tool in default_registry().schemas():
        assert tool["risk"] in {"read", "write", "network", "execute",
                                "destructive", "privileged"}


def test_audit_chain_intact_after_calls(jail: Path):
    audit = AuditLog()
    ctx = make_ctx(jail, allow=True, audit=audit)
    reg = default_registry()
    reg.call("read_file", {"path": str(jail / "docs" / "readme.txt")}, ctx=ctx)
    reg.call("write_file", {"path": str(jail / "z.txt"), "content": "1"}, ctx=ctx)
    reg.call("run_command", {"command": "echo hi"}, ctx=ctx)
    assert audit.verify()
    # each call writes one 'authorize' + one 'tool_call' record
    assert len(audit.records()) == 6
