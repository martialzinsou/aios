from __future__ import annotations

import pytest

from aios_agent.errors import ConfirmationRejected, PolicyDenied
from aios_agent.security.audit import AuditLog
from aios_agent.security.confirmation import (
    AlwaysAllow,
    AlwaysDeny,
    CallbackConfirmer,
    ConfirmationRequest,
)
from aios_agent.security.permission import PermissionManager
from aios_agent.security.policy import Decision, Risk, Rule, StaticPolicy


@pytest.fixture
def audit() -> AuditLog:
    return AuditLog()


def test_read_allowed_without_asking_human(audit: AuditLog):
    asked = []
    pm = PermissionManager(
        confirmer=CallbackConfirmer(lambda r: asked.append(r) or True),
        audit=audit,
    )
    auth = pm.authorize("read_file", Risk.READ, "/home/u/a.txt")
    assert auth.allowed
    assert auth.approved_by == "policy"
    assert asked == []


def test_write_requires_confirmation_and_denial_blocks(audit: AuditLog):
    pm = PermissionManager(confirmer=AlwaysDeny(), audit=audit)
    auth = pm.authorize("write_file", Risk.WRITE, "/home/u/a.txt", summary="ecrire")
    assert not auth.allowed
    assert auth.verdict is Decision.CONFIRM
    with pytest.raises(ConfirmationRejected):
        auth.raise_if_denied()


def test_write_allowed_after_approval(audit: AuditLog):
    seen = []
    pm = PermissionManager(
        confirmer=CallbackConfirmer(lambda r: seen.append(r) or True), audit=audit
    )
    auth = pm.authorize("write_file", Risk.WRITE, "/home/u/a.txt", summary="ecrire")
    assert auth.allowed
    assert auth.approved_by == "operator"
    assert seen and seen[0].action == "write_file"
    assert seen[0].risk == "write"


def test_privileged_is_denied_without_prompting(audit: AuditLog):
    asked = []
    pm = PermissionManager(
        confirmer=CallbackConfirmer(lambda r: asked.append(r) or True), audit=audit
    )
    auth = pm.authorize("run_command", Risk.PRIVILEGED, "sudo rm -rf /")
    assert not auth.allowed
    assert auth.verdict is Decision.DENY
    assert asked == []
    with pytest.raises(PolicyDenied):
        auth.raise_if_denied()


def test_grant_reuse_after_approval(audit: AuditLog):
    asked = []
    pm = PermissionManager(
        confirmer=CallbackConfirmer(lambda r: asked.append(r) or True),
        audit=audit,
        remember_confirmations=True,
        grant_ttl=60,
    )
    first = pm.authorize("write_file", Risk.WRITE, "/home/u/a.txt")
    second = pm.authorize("write_file", Risk.WRITE, "/home/u/a.txt")
    assert first.allowed and second.allowed
    assert len(asked) == 1
    assert second.grant_id
    assert len(pm.active_grants) == 1
    assert pm.revoke_all() == 1
    third = pm.authorize("write_file", Risk.WRITE, "/home/u/a.txt")
    assert len(asked) == 2
    assert third.allowed


def test_grant_does_not_match_other_target(audit: AuditLog):
    pm = PermissionManager(
        confirmer=AlwaysAllow(), audit=audit, remember_confirmations=True
    )
    pm.authorize("write_file", Risk.WRITE, "/home/u/a.txt")
    other = pm.authorize("write_file", Risk.WRITE, "/home/u/b.txt")
    assert other.verdict is Decision.CONFIRM  # not covered by the grant
    assert other.grant_id  # newly minted after fresh approval


def test_grant_expiry(audit: AuditLog):
    pm = PermissionManager(
        confirmer=AlwaysAllow(), audit=audit, remember_confirmations=True, grant_ttl=-1
    )
    pm.authorize("write_file", Risk.WRITE, "/a")
    assert pm.active_grants == []


def test_audit_records_outcomes(audit: AuditLog):
    pm = PermissionManager(confirmer=AlwaysDeny(), audit=audit)
    pm.authorize("write_file", Risk.WRITE, "/a", summary="s")
    pm.authorize("read_file", Risk.READ, "/b")
    outcomes = [r["outcome"] for r in audit.records()]
    assert outcomes == ["rejected", "auto"]
    assert audit.verify()


def test_custom_rule_can_widen_access(audit: AuditLog):
    policy = StaticPolicy(
        rules=[Rule(id="allow-tmp", decision=Decision.ALLOW, target="/tmp/*")]
    )
    pm = PermissionManager(policy=policy, confirmer=AlwaysDeny(), audit=audit)
    assert pm.authorize("write_file", Risk.WRITE, "/tmp/x").allowed
    assert not pm.authorize("write_file", Risk.WRITE, "/home/x").allowed
