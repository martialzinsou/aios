from __future__ import annotations

import json
from pathlib import Path

import pytest

from aios_agent.cli import main


def test_tools_command(capsys):
    assert main(["tools"]) == 0
    out = capsys.readouterr().out
    assert "read_file" in out
    assert "delete_path" in out
    assert "run_command" in out


def test_policy_command_is_valid_json(capsys):
    assert main(["policy"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "rules" in data and "defaults" in data
    assert data["defaults"]["privileged"] == "deny"


def test_doctor_runs(capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "aiOS agent" in out
    assert "modèle" in out


def test_run_json_output(jail: Path, tmp_path: Path, capsys):
    args = [
        "run", "--json",
        "--no-confirm", "--brain", "heuristic",
        "--jail", str(jail),
        "--audit", str(tmp_path / "audit.jsonl"),
        "--steps", "4",
        "infos système",
    ]
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "answered"
    assert payload["brain"] == "heuristic"
    assert payload["steps"]


def test_run_denies_write_in_no_confirm_mode(jail: Path, tmp_path: Path, capsys):
    target = jail / "cli.txt"
    args = [
        "run", "--json",
        "--no-confirm", "--brain", "heuristic",
        "--jail", str(jail),
        "--audit", str(tmp_path / "audit.jsonl"),
        f"crée un fichier dans {target}",
    ]
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert not target.exists()
    assert any(s["blocked"] for s in payload["steps"])


def test_audit_command_reads_journal(jail: Path, tmp_path: Path, capsys):
    audit = str(tmp_path / "audit.jsonl")
    main([
        "run", "--json", "--no-confirm", "--brain", "heuristic",
        "--jail", str(jail), "--audit", audit, "infos système",
    ])
    capsys.readouterr()
    assert main(["audit", "--audit", audit, "-n", "5"]) == 0
    assert main(["audit", "--audit", audit, "--verify"]) == 0


def test_audit_missing_file_returns_error(tmp_path: Path, capsys):
    assert main(["audit", "--audit", str(tmp_path / "missing.jsonl")]) == 1


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "aios" in capsys.readouterr().out
