from __future__ import annotations

from aios_agent.security.redaction import redact, redact_object


def test_aws_access_key():
    key = "AKIA" + "IOSFODG7EXAMPLEX"  # 4 + 16
    assert key not in redact(f"id = {key}")


def test_github_token():
    out = redact("token ghp_" + "a" * 36)
    assert "ghp_" + "a" * 36 not in out


def test_openai_style_key():
    key = "sk-" + "b" * 40
    assert key not in redact(f"api key: {key}")


def test_password_assignment():
    out = redact("password=hunter2 and PASSWD: hunter3")
    assert "hunter2" not in out
    assert "hunter3" not in out


def test_bearer_header():
    out = redact("Authorization: Bearer abcdefghijklmnop")
    assert "abcdefghijklmnop" not in out


def test_email():
    assert "john@example.com" not in redact("contact john@example.com here")


def test_jwt():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signaturepart"
    assert jwt not in redact(f"jwt={jwt}")


def test_plain_text_untouched():
    text = "le dossier /home/user/projet contient 3 fichiers"
    assert redact(text) == text


def test_redact_object_recursive():
    payload = {
        "cmd": "curl -H 'Authorization: Bearer secretvalue' http://x",
        "nested": {"password": "p@ss"},
        "count": 3,
    }
    out = redact_object(payload)
    assert "secretvalue" not in out["cmd"]
    assert out["nested"]["password"] != "p@ss"
    assert out["count"] == 3
