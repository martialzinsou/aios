from __future__ import annotations

from pathlib import Path

from aios_agent.security.audit import AuditLog


def test_append_and_verify(tmp_path: Path):
    log = AuditLog(tmp_path / "a.jsonl")
    log.append("session_start", actor="system", outcome="ok")
    log.append("tool_call", action="read_file", target="/x", outcome="ok")
    assert len(log.records()) == 2
    assert log.verify() is True


def test_verify_detects_tampering(tmp_path: Path):
    path = tmp_path / "a.jsonl"
    log = AuditLog(path)
    log.append("tool_call", action="write_file", target="/x", outcome="ok")
    log.append("tool_call", action="delete_path", target="/x", outcome="ok")
    assert log.verify() is True

    # rewrite the first record's outcome
    text = path.read_text("utf-8").splitlines()
    text[0] = text[0].replace('"outcome":"ok"', '"outcome":"forged"')
    path.write_text("\n".join(text) + "\n", "utf-8")

    reloaded = AuditLog(path)
    assert reloaded.verify() is False


def test_reload_restores_sequence(tmp_path: Path):
    path = tmp_path / "a.jsonl"
    log = AuditLog(path)
    for i in range(5):
        log.append("tool_call", action=f"a{i}", outcome="ok")
    again = AuditLog(path)
    assert len(again.records()) == 5
    assert again.records()[-1]["seq"] == 4
    assert again.verify() is True


def test_removal_detected(tmp_path: Path):
    path = tmp_path / "a.jsonl"
    log = AuditLog(path)
    log.append("e1", outcome="ok")
    log.append("e2", outcome="ok")
    log.append("e3", outcome="ok")
    # dropping a *middle* record breaks both seq numbering and the hash chain
    lines = path.read_text("utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n", "utf-8")
    assert AuditLog(path).verify() is False


def test_detail_is_redacted(tmp_path: Path):
    log = AuditLog(tmp_path / "a.jsonl")
    log.append("tool_call", action="run_command", detail={"args": {"command": "export TOKEN=abcd1234"}})
    rec = log.records()[0]
    assert "abcd1234" not in str(rec["detail"])
