#!/usr/bin/env python3
"""Static validation for the T2 Tahoe SMBIOS routing table.

This checker deliberately avoids importing the patcher package so it can run on
Linux CI hosts without macOS-only dependencies.
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "opencore_legacy_patcher" / "support" / "generate_smbios.py"

EXPECTED_TARGETS = {
    "MacBookAir8,1": "MacBookPro16,2",
    "MacBookAir8,2": "MacBookPro16,2",
    "MacBookAir9,1": "MacBookPro16,2",
    "MacBookPro15,2": "MacBookPro16,2",
    "MacBookPro15,4": "MacBookPro16,2",
    "MacBookPro16,3": "MacBookPro16,2",
    "MacBookPro15,1": "MacBookPro16,1",
    "MacBookPro15,3": "MacBookPro16,1",
    "MacBookPro16,4": "MacBookPro16,1",
}


def read_mapping() -> dict[str, str]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name)
            and target.id == "TAHOE_T2_MOBILE_SPOOF_TARGETS"
            for target in node.targets
        ):
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, dict):
            raise TypeError("TAHOE_T2_MOBILE_SPOOF_TARGETS must be a dictionary")
        return value
    raise RuntimeError("TAHOE_T2_MOBILE_SPOOF_TARGETS was not found")


def main() -> int:
    actual = read_mapping()
    if actual != EXPECTED_TARGETS:
        missing = {key: value for key, value in EXPECTED_TARGETS.items() if actual.get(key) != value}
        unexpected = {key: value for key, value in actual.items() if EXPECTED_TARGETS.get(key) != value}
        raise SystemExit(
            "Invalid T2 Tahoe spoof routing. "
            f"Missing or changed: {missing}; unexpected: {unexpected}"
        )

    mba82_target = actual["MacBookAir8,2"]
    if mba82_target == "MacBookPro16,4":
        raise SystemExit("MacBookAir8,2 must never route to MacBookPro16,4 / J215AP")

    print("T2 Tahoe SMBIOS routing is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
