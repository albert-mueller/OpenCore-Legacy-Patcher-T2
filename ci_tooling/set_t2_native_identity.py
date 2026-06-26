#!/usr/bin/env python3
"""Create a native-identity T2 test config from a built OpenCore config.plist.

This keeps Tahoe compatibility bypasses and all existing kext/kernel settings,
but restores the real Mac model identity so T2/APFS keybag authorization can be
compared against the full-SMBIOS-spoof configuration.
"""

from __future__ import annotations

import argparse
import plistlib
import shutil
import sys
from pathlib import Path


NATIVE_MODEL = "MacBookAir8,2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore native MacBookAir8,2 identity in an OpenCore config"
    )
    parser.add_argument("config", type=Path, help="Path to EFI/OC/config.plist")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.expanduser().resolve()
    if not config_path.is_file():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 2

    raw = config_path.read_bytes()
    plist_format = plistlib.FMT_BINARY if raw.startswith(b"bplist00") else plistlib.FMT_XML
    config = plistlib.loads(raw)

    platform = config.setdefault("PlatformInfo", {})
    generic = platform.setdefault("Generic", {})
    kernel_quirks = config.setdefault("Kernel", {}).setdefault("Quirks", {})

    generic["SystemProductName"] = NATIVE_MODEL
    platform["UpdateSMBIOSMode"] = "Create"
    kernel_quirks["CustomSMBIOSGuid"] = False

    revision = config.setdefault("#Revision", {})
    revision["Spoofed-Model"] = f"{NATIVE_MODEL} - Native identity keybag test"

    backup_path = config_path.with_name(config_path.name + ".before-native-identity-test")
    if not backup_path.exists():
        shutil.copy2(config_path, backup_path)

    temp_path = config_path.with_name(config_path.name + ".tmp")
    with temp_path.open("wb") as stream:
        plistlib.dump(config, stream, fmt=plist_format, sort_keys=False)
    temp_path.replace(config_path)

    print(f"SystemProductName: {NATIVE_MODEL}")
    print("UpdateSMBIOSMode: Create")
    print("CustomSMBIOSGuid: False")
    print(f"Backup: {backup_path}")
    print(f"Updated: {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
