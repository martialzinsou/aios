#!/usr/bin/env python3
"""Sanity check: the policy shipped in the image must match the code.

`os/scripts/20-prepare-overlay.sh` runs this before greffing the overlay so a
drift between `aios-policy.json` and `StaticPolicy.default()` cannot reach an
image.  Tests cover the code; this covers the packaging.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    root = Path(os.environ.get("AIOS_ROOT") or Path(__file__).resolve().parents[2])
    shipped_path = Path(
        os.environ.get("AIOS_SHIPPED_POLICY")
        or root / "os/overlay/chromeos-base/aios-agent/files/aios-policy.json"
    )

    src = root / "agent" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from aios_agent.security.policy import StaticPolicy  # noqa: E402

    if not shipped_path.exists():
        print(f"✗ politique introuvable : {shipped_path}", file=sys.stderr)
        return 1

    shipped = json.loads(shipped_path.read_text("utf-8"))
    expected = StaticPolicy.default().to_dict()
    if shipped == expected:
        print(f"✓ politique cohérente : {shipped_path}")
        return 0

    print("✗ aios-policy.json diffère de StaticPolicy.default()", file=sys.stderr)
    print("  régénère-la avec :  make policy", file=sys.stderr)
    for key in sorted(set(shipped) | set(expected)):
        if shipped.get(key) != expected.get(key):
            print(f"  • clé divergente : {key}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
