#!/usr/bin/env python3
"""Apply a hybrid SMBIOS identity for Tahoe testing on MacBookAir8,2.

The installer sees a supported MacBookPro16,2 model, while OpenCore keeps
UpdateSMBIOSMode=Create and CustomSMBIOSGuid=False to avoid the full Custom
SMBIOS path used in the previous failing configuration.
"""

from __future__ import annotations

import argparse
import plistlib
import shutil
import sys
from pathlib import Path


SPOOF_MODEL = "MacBookPro16,2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply hybrid MacBookPro16,2 identity for T2 Tahoe testing"
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

    generic["SystemProductName"] = SPOOF_MODEL
    platform["UpdateSMBIOSMode"] = "Create"
    kernel_quirks["CustomSMBIOSGuid"] = False

    revision = config.setdefault("#Revision", {})
    revision["Spoofed-Model"] = f"{SPOOF_MODEL} - Hybrid T2 keybag test"

    backup_path = config_path.with_name(config_path.name + ".before-hybrid-identity-test")
    if not backup_path.exists():
        shutil.copy2(config_path, backup_path)

    temp_path = config_path.with_name(config_path.name + ".tmp")
    with temp_path.open("wb") as stream:
        plistlib.dump(config, stream, fmt=plist_format, sort_keys=False)
    temp_path.replace(config_path)

    print(f"SystemProductName: {SPOOF_MODEL}")
    print("UpdateSMBIOSMode: Create")
    print("CustomSMBIOSGuid: False")
    print(f"Backup: {backup_path}")
    print(f"Updated: {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
