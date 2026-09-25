"""Least-privilege authorisation: policy → grants → human confirmation.

This is the single choke point every tool call goes through.  Order of checks:

1. an existing short-lived :class:`Grant` (pre-approved target) short-circuits;
2. the declarative :class:`StaticPolicy` decides ``ALLOW / CONFIRM / DENY``;
3. ``CONFIRM`` is resolved by the human operator — never by the agent;
4. every outcome is written to the tamper-evident audit log.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..errors import ConfirmationRejected, PolicyDenied
from .audit import AuditLog
from .confirmation import ConfirmationRequest, Confirmer, AlwaysDeny
from .policy import Decision, Risk, StaticPolicy, Verdict


@dataclass(frozen=True)
class Grant:
    """A pre-approved (action, target) pair, minted only after a human said yes."""

    id: str
    action: str
    target: str
    approved_by: str
    created_at: float
    ttl: float
    one_time: bool = False
    used: bool = False

    def expired(self) -> bool:
        return (time.monotonic() - self.created_at) > self.ttl

    def matches(self, action: str, target: str) -> bool:
        return self.action == action and self.target == target


@dataclass
class Authorization:
    allowed: bool
    verdict: Decision
    rule_id: str = ""
    reason: str = ""
    approved_by: str = ""
    grant_id: str = ""

    @property
    def denied(self) -> bool:
        return not self.allowed

    def raise_if_denied(self) -> None:
        if not self.allowed:
            if self.verdict is Decision.DENY:
                raise PolicyDenied(self.rule_id, self.reason)
            raise ConfirmationRejected(
                f"{self.rule_id}: {self.reason or 'user declined'}"
            )


class PermissionManager:
    def __init__(
        self,
        policy: Optional[StaticPolicy] = None,
        confirmer: Optional[Confirmer] = None,
        audit: Optional[AuditLog] = None,
        *,
        grant_ttl: float = 300.0,
        remember_confirmations: bool = False,
    ) -> None:
        self.policy = policy or StaticPolicy.default()
        self.confirmer = confirmer or AlwaysDeny()
        self.audit = audit or AuditLog()
        self.grant_ttl = grant_ttl
        self.remember_confirmations = remember_confirmations
        self._grants: List[Grant] = []

    # -- public API --------------------------------------------------------
    def authorize(
        self,
        action: str,
        risk: Risk,
        target: str = "",
        *,
        summary: str = "",
        payload: Optional[Dict[str, object]] = None,
        actor: str = "agent",
    ) -> Authorization:
        target = target or ""

        # 1. existing grant?
        grant = self._find_grant(action, target)
        if grant is not None:
            auth = Authorization(
                allowed=True,
                verdict=Decision.ALLOW,
                rule_id=f"grant:{grant.id}",
                reason="reusing a prior human approval",
                approved_by=grant.approved_by,
                grant_id=grant.id,
            )
            if grant.one_time:
                self._revoke(grant.id)
            self.audit.append(
                "authorize",
                action=action,
                target=target,
                actor=actor,
                verdict="allow",
                approved_by=grant.approved_by,
                outcome="granted",
                detail={"rule": auth.rule_id},
            )
            return auth

        # 2. static policy
        verdict: Verdict = self.policy.evaluate(action, risk, target)

        if verdict.decision is Decision.DENY:
            self.audit.append(
                "authorize",
                action=action,
                target=target,
                actor=actor,
                verdict="deny",
                outcome="denied",
                detail={"rule": verdict.rule_id, "reason": verdict.reason},
            )
            return Authorization(
                allowed=False,
                verdict=Decision.DENY,
                rule_id=verdict.rule_id,
                reason=verdict.reason or "denied by policy",
            )

        if verdict.decision is Decision.ALLOW:
            self.audit.append(
                "authorize",
                action=action,
                target=target,
                actor=actor,
                verdict="allow",
                outcome="auto",
                detail={"rule": verdict.rule_id, "risk": risk.value},
            )
            return Authorization(
                allowed=True,
                verdict=Decision.ALLOW,
                rule_id=verdict.rule_id,
                reason=verdict.reason,
                approved_by="policy",
            )

        # 3. CONFIRM → the human decides
        request = ConfirmationRequest(
            action=action,
            target=target,
            risk=risk.value,
            summary=summary or action,
            rule_id=verdict.rule_id,
            reason=verdict.reason,
            payload=payload,
        )
        approved = bool(self.confirmer.confirm(request))
        approved_by = "operator" if approved else "operator:rejected"

        self.audit.append(
            "authorize",
            action=action,
            target=target,
            actor=actor,
            verdict="confirm",
            approved_by=approved_by,
            outcome="approved" if approved else "rejected",
            detail={"rule": verdict.rule_id, "summary": summary},
        )
        if not approved:
            return Authorization(
                allowed=False,
                verdict=Decision.CONFIRM,
                rule_id=verdict.rule_id,
                reason="the operator declined this action",
            )

        grant_id = ""
        if self.remember_confirmations:
            grant_id = self._mint_grant(action, target, "operator")

        return Authorization(
            allowed=True,
            verdict=Decision.CONFIRM,
            rule_id=verdict.rule_id,
            reason=verdict.reason,
            approved_by="operator",
            grant_id=grant_id,
        )

    def require(
        self,
        action: str,
        risk: Risk,
        target: str = "",
        **kw,
    ) -> Authorization:
        auth = self.authorize(action, risk, target, **kw)
        auth.raise_if_denied()
        return auth

    # -- grants ------------------------------------------------------------
    def _mint_grant(self, action: str, target: str, approved_by: str) -> str:
        grant = Grant(
            id=uuid.uuid4().hex[:12],
            action=action,
            target=target,
            approved_by=approved_by,
            created_at=time.monotonic(),
            ttl=self.grant_ttl,
            one_time=False,
        )
        self._grants.append(grant)
        return grant.id

    def _find_grant(self, action: str, target: str) -> Optional[Grant]:
        for grant in list(self._grants):
            if grant.expired():
                self._revoke(grant.id)
                continue
            if grant.matches(action, target):
                return grant
        return None

    def _revoke(self, grant_id: str) -> None:
        self._grants = [g for g in self._grants if g.id != grant_id]

    def revoke_all(self) -> int:
        n = len(self._grants)
        self._grants.clear()
        return n

    @property
    def active_grants(self) -> List[Grant]:
        return [g for g in self._grants if not g.expired()]
