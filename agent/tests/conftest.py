from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # …/agent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aios_agent.core.agent import Agent, AgentConfig  # noqa: E402
from aios_agent.security.confirmation import (  # noqa: E402
    AlwaysAllow,
    AlwaysDeny,
    CallbackConfirmer,
)
from aios_agent.security.permission import PermissionManager  # noqa: E402
from aios_agent.security.policy import StaticPolicy  # noqa: E402
from aios_agent.security.audit import AuditLog  # noqa: E402
from aios_agent.security.sandbox import Sandbox  # noqa: E402
from aios_agent.tools.base import ToolContext  # noqa: E402
from aios_agent.tools import default_registry  # noqa: E402


@pytest.fixture
def sock_dir() -> Path:
    # macOS limite les chemins AF_UNIX à ~104 caractères et le tmp_path de
    # pytest est profond : le socket vit dans un répertoire court et unique.
    d = Path(tempfile.mkdtemp(prefix="aios-", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def jail(tmp_path: Path) -> Path:
    root = tmp_path / "jail"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "readme.txt").write_text("hello aiOS\nsecond line\n", "utf-8")
    (root / "docs" / "notes.md").write_text("# notes\nsearch me please\n", "utf-8")
    (root / "data.bin").write_bytes(b"\x00\xff\xfe\x80")
    return root


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def permissions(audit: AuditLog) -> PermissionManager:
    return PermissionManager(
        policy=StaticPolicy.default(),
        confirmer=AlwaysDeny(),
        audit=audit,
    )


@pytest.fixture
def ctx(jail: Path, permissions: PermissionManager) -> ToolContext:
    return ToolContext(sandbox=Sandbox([str(jail)]), permissions=permissions)


def make_agent(jail: Path, tmp_path: Path, confirmer=None, **cfg) -> Agent:
    config = AgentConfig(
        jail_roots=[str(jail)],
        audit_path=str(tmp_path / "audit.jsonl"),
        episodes_path=str(tmp_path / "episodes.json"),
        brain="heuristic",
        confirm=False if confirmer is None else True,
        echo=None,
        **cfg,
    )
    return Agent(config, confirmer=confirmer)


@pytest.fixture
def deny_agent(jail: Path, tmp_path: Path) -> Agent:
    return make_agent(jail, tmp_path, confirmer=AlwaysDeny())


@pytest.fixture
def allow_agent(jail: Path, tmp_path: Path) -> Agent:
    return make_agent(jail, tmp_path, confirmer=AlwaysAllow())
