from __future__ import annotations

from aios_agent.security.policy import (
    DEFAULT_DECISIONS,
    Decision,
    Risk,
    Rule,
    StaticPolicy,
)


def test_least_privilege_defaults():
    assert DEFAULT_DECISIONS[Risk.READ] is Decision.ALLOW
    assert DEFAULT_DECISIONS[Risk.WRITE] is Decision.CONFIRM
    assert DEFAULT_DECISIONS[Risk.NETWORK] is Decision.CONFIRM
    assert DEFAULT_DECISIONS[Risk.EXECUTE] is Decision.CONFIRM
    assert DEFAULT_DECISIONS[Risk.DESTRUCTIVE] is Decision.CONFIRM
    assert DEFAULT_DECISIONS[Risk.PRIVILEGED] is Decision.DENY


def test_default_policy_denies_privilege_escalation():
    policy = StaticPolicy.default()
    verdict = policy.evaluate("run_command", Risk.PRIVILEGED, "sudo ls")
    assert verdict.decision is Decision.DENY
    assert verdict.rule_id == "deny-privileged"


def test_default_policy_allows_reads_everywhere():
    policy = StaticPolicy.default()
    verdict = policy.evaluate("read_file", Risk.READ, "/etc/hostname")
    assert verdict.decision is Decision.ALLOW


def test_default_policy_refuses_writes_to_os_but_not_to_home():
    policy = StaticPolicy.default()
    assert policy.evaluate("write_file", Risk.WRITE, "/usr/bin/foo").decision is Decision.DENY
    assert policy.evaluate("write_file", Risk.WRITE, "/etc/passwd").decision is Decision.DENY
    assert policy.evaluate("write_file", Risk.WRITE, "/home/u/x").decision is Decision.CONFIRM


def test_default_policy_refuses_all_access_to_dev():
    policy = StaticPolicy.default()
    for risk in Risk:
        assert policy.evaluate("read_file", risk, "/dev/sda").decision is Decision.DENY


def test_first_match_wins():
    policy = StaticPolicy(
        rules=[
            Rule(id="deny-x", decision=Decision.DENY, action="write_file", target="/a/*"),
            Rule(id="allow-x", decision=Decision.ALLOW, action="write_file", target="/a/*"),
        ]
    )
    assert policy.evaluate("write_file", Risk.WRITE, "/a/b").rule_id == "deny-x"


def test_rule_glob_matching():
    rule = Rule(id="r", decision=Decision.ALLOW, action="fs_*", target="*.txt")
    assert rule.matches("fs_read", Risk.READ, "notes.txt")
    assert not rule.matches("shell_run", Risk.READ, "notes.txt")
    assert not rule.matches("fs_read", Risk.READ, "notes.md")


def test_risk_filter_on_rule():
    rule = Rule(id="r", decision=Decision.DENY, risk=Risk.DESTRUCTIVE)
    assert rule.matches("x", Risk.DESTRUCTIVE, "")
    assert not rule.matches("x", Risk.READ, "")


def test_policy_dict_roundtrip():
    original = StaticPolicy.default()
    clone = StaticPolicy.from_dict(original.to_dict())
    assert [r.to_dict() for r in clone.rules] == [r.to_dict() for r in original.rules]
    assert clone.defaults == original.defaults
