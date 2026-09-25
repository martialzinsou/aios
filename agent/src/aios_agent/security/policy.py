"""Declarative, first-match-wins security policy.

The policy is intentionally tiny and auditable: every action the agent can
perform carries a :class:`Risk` level and an optional *target* (a filesystem
path, a host, a command line...).  A list of :class:`Rule` objects maps
``(action, risk, target)`` to a :class:`Decision`.

Defaults encode *least privilege*: reads are free, anything that mutates state
requires an explicit human confirmation, privilege escalation is refused.
"""
from __future__ import annotations

import enum
import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class Risk(str, enum.Enum):
    """Ordered from the most to the least harmless.

    The ordinal is used to derive sensible defaults; a tool never gets to pick
    its own risk level at call time.
    """

    READ = "read"
    WRITE = "write"
    NETWORK = "network"
    EXECUTE = "execute"
    DESTRUCTIVE = "destructive"
    PRIVILEGED = "privileged"

    @property
    def rank(self) -> int:
        return _RISK_RANK[self]


_RISK_RANK: Dict[Risk, int] = {
    Risk.READ: 0,
    Risk.WRITE: 1,
    Risk.NETWORK: 2,
    Risk.EXECUTE: 3,
    Risk.DESTRUCTIVE: 4,
    Risk.PRIVILEGED: 5,
}


class Decision(str, enum.Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


#: Least-privilege baseline used when no rule matches.
DEFAULT_DECISIONS: Dict[Risk, Decision] = {
    Risk.READ: Decision.ALLOW,
    Risk.WRITE: Decision.CONFIRM,
    Risk.NETWORK: Decision.CONFIRM,
    Risk.EXECUTE: Decision.CONFIRM,
    Risk.DESTRUCTIVE: Decision.CONFIRM,
    Risk.PRIVILEGED: Decision.DENY,
}


@dataclass(frozen=True)
class Rule:
    """A single declarative policy rule.

    Every field is a glob pattern (``fnmatch``) so a rule can cover a family of
    actions or paths without regular expressions.
    """

    id: str
    decision: Decision
    action: str = "*"
    risk: Optional[Risk] = None
    target: str = "*"
    reason: str = ""

    def matches(self, action: str, risk: Risk, target: str) -> bool:
        if self.risk is not None and self.risk is not risk:
            return False
        if not fnmatch.fnmatchcase(action, self.action):
            return False
        if not fnmatch.fnmatchcase(target, self.target):
            return False
        return True

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Rule":
        risk = raw.get("risk")
        return cls(
            id=str(raw.get("id", f"rule-{raw.get('action', '*')}")),
            decision=Decision(raw["decision"]),
            action=str(raw.get("action", "*")),
            risk=Risk(risk) if risk else None,
            target=str(raw.get("target", "*")),
            reason=str(raw.get("reason", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "decision": self.decision.value,
            "action": self.action,
            "target": self.target,
        }
        if self.risk is not None:
            out["risk"] = self.risk.value
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class Verdict:
    decision: Decision
    rule_id: str
    reason: str = ""
    risk: Risk = Risk.READ


class StaticPolicy:
    """Ordered rule list + least-privilege defaults.

    Evaluation is **first match wins**, which makes the effective behaviour of a
    long rule list easy to reason about when reviewing a diff.
    """

    def __init__(
        self,
        rules: Optional[Iterable[Rule]] = None,
        defaults: Optional[Dict[Risk, Decision]] = None,
    ) -> None:
        self.rules: List[Rule] = list(rules or [])
        self.defaults: Dict[Risk, Decision] = dict(DEFAULT_DECISIONS)
        if defaults:
            self.defaults.update(defaults)

    # -- configuration -----------------------------------------------------
    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "StaticPolicy":
        rules = [Rule.from_dict(r) for r in raw.get("rules", [])]
        defaults = {
            Risk(k): Decision(v) for k, v in raw.get("defaults", {}).items()
        }
        return cls(rules=rules, defaults=defaults)

    @classmethod
    def load(cls, path: "str | Path") -> "StaticPolicy":
        p = Path(path)
        with p.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls.from_dict(raw)

    @classmethod
    def default(cls) -> "StaticPolicy":
        """Baseline policy shipped with aiOS.

        Denies come first (first match wins), scoped by risk so that the OS
        stays *readable* but never writable/executable by the agent.
        """
        rules: List[Rule] = [
            Rule(
                id="deny-privileged",
                decision=Decision.DENY,
                risk=Risk.PRIVILEGED,
                reason="The agent never runs with elevated privileges.",
            ),
            Rule(
                id="deny-dev",
                decision=Decision.DENY,
                target="/dev/*",
                reason="Raw devices are off limits.",
            ),
            Rule(
                id="deny-shadow",
                decision=Decision.DENY,
                target="/etc/shadow*",
                reason="Password hashes are off limits.",
            ),
        ]
        protected = ("/bin/*", "/sbin/*", "/usr/*", "/etc/*", "/lib/*", "/boot/*")
        mutating = (Risk.WRITE, Risk.EXECUTE, Risk.DESTRUCTIVE)
        for root in protected:
            for risk in mutating:
                rules.append(
                    Rule(
                        id=f"deny-os-{root.strip('/*')}-{risk.value}",
                        decision=Decision.DENY,
                        target=root,
                        risk=risk,
                        reason="The operating system is read-only for the agent.",
                    )
                )
        rules.append(
            Rule(
                id="allow-reads",
                decision=Decision.ALLOW,
                risk=Risk.READ,
                reason="Reading is non-mutating; the path jail still applies.",
            )
        )
        return cls(rules=rules)

    # -- evaluation --------------------------------------------------------
    def evaluate(self, action: str, risk: Risk, target: str = "") -> Verdict:
        target = target or "*"
        for rule in self.rules:
            if rule.matches(action, risk, target):
                return Verdict(
                    decision=rule.decision,
                    rule_id=rule.id,
                    reason=rule.reason,
                    risk=risk,
                )
        decision = self.defaults.get(risk, Decision.DENY)
        return Verdict(
            decision=decision,
            rule_id="default",
            reason=f"least-privilege default for risk={risk.value}",
            risk=risk,
        )

    def add(self, rule: Rule) -> None:
        self.rules.append(rule)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rules": [r.to_dict() for r in self.rules],
            "defaults": {k.value: v.value for k, v in self.defaults.items()},
        }
