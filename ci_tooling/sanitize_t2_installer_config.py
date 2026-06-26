#!/usr/bin/env python3
"""Disable unverified T2 storage/security kernel patches in a built OpenCore config.

This is a temporary research tool for isolating Tahoe installer failures on T2
Macs. It keeps the rest of the generated EFI unchanged and creates a backup
before writing the sanitized plist.
"""

from __future__ import annotations

import argparse
import plistlib
import shutil
import sys
from pathlib import Path


UNVERIFIED_PATCH_COMMENTS = {
    "Bypass XARTDisableLog limits (Tahoe Cache Fix)",
    "Hardcode SEP OOL Max Send Pages Limit",
    "Bypass AppleKeyStore Deadline Mismatch (Tahoe Fix)",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Disable unverified AppleSEPManager/AppleKeyStore patches in config.plist"
    )
    parser.add_argument("config", type=Path, help="Path to the built OpenCore config.plist")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path: Path = args.config.expanduser().resolve()

    if not config_path.is_file():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 2

    raw = config_path.read_bytes()
    plist_format = plistlib.FMT_BINARY if raw.startswith(b"bplist00") else plistlib.FMT_XML
    config = plistlib.loads(raw)

    patches = config.get("Kernel", {}).get("Patch", [])
    if not isinstance(patches, list):
        print("Kernel/Patch is not a list", file=sys.stderr)
        return 3

    changed: list[str] = []
    found: set[str] = set()

    for patch in patches:
        if not isinstance(patch, dict):
            continue
        comment = patch.get("Comment")
        if comment not in UNVERIFIED_PATCH_COMMENTS:
            continue
        found.add(comment)
        if patch.get("Enabled") is not False:
            patch["Enabled"] = False
            changed.append(comment)

    missing = sorted(UNVERIFIED_PATCH_COMMENTS - found)
    if missing:
        print("Warning: expected patches not found:")
        for comment in missing:
            print(f"  - {comment}")

    backup_path = config_path.with_name(config_path.name + ".before-t2-storage-fix")
    if not backup_path.exists():
        shutil.copy2(config_path, backup_path)

    temp_path = config_path.with_name(config_path.name + ".tmp")
    with temp_path.open("wb") as stream:
        plistlib.dump(config, stream, fmt=plist_format, sort_keys=False)
    temp_path.replace(config_path)

    if changed:
        print("Disabled unverified T2 patches:")
        for comment in changed:
            print(f"  - {comment}")
    else:
        print("No enabled unverified T2 patches remained.")

    print(f"Backup: {backup_path}")
    print(f"Updated: {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
