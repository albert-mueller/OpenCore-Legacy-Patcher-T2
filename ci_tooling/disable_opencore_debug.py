#!/usr/bin/env python3
"""Disable OpenCore and kernel verbose/debug output in a built config.plist."""

from __future__ import annotations

import argparse
import plistlib
import shutil
import sys
from pathlib import Path


DEBUG_BOOT_ARGS = {
    "-v",
    "debug=0x100",
    "keepsyms=1",
    "msgbuf=1048576",
    "-liludbgall",
    "-liludump",
    "-alldbg",
    "-wegdbg",
}

APPLE_NVRAM_GUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Disable OpenCore debug output")
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

    debug = config.setdefault("Misc", {}).setdefault("Debug", {})
    debug["AppleDebug"] = False
    debug["ApplePanic"] = False
    debug["DisplayDelay"] = 0
    debug["DisplayLevel"] = 0
    debug["LogModules"] = ""
    debug["SysReport"] = False
    debug["Target"] = 0

    nvram_add = config.setdefault("NVRAM", {}).setdefault("Add", {})
    apple_nvram = nvram_add.setdefault(APPLE_NVRAM_GUID, {})
    boot_args = apple_nvram.get("boot-args", "")
    if isinstance(boot_args, bytes):
        boot_args = boot_args.decode("utf-8", errors="ignore")
    filtered_args = [arg for arg in str(boot_args).split() if arg not in DEBUG_BOOT_ARGS]
    apple_nvram["boot-args"] = " ".join(filtered_args)

    backup_path = config_path.with_name(config_path.name + ".before-debug-off")
    if not backup_path.exists():
        shutil.copy2(config_path, backup_path)

    temp_path = config_path.with_name(config_path.name + ".tmp")
    with temp_path.open("wb") as stream:
        plistlib.dump(config, stream, fmt=plist_format, sort_keys=False)
    temp_path.replace(config_path)

    print("OpenCore debug output: disabled")
    print(f"boot-args: {apple_nvram['boot-args']}")
    print(f"Backup: {backup_path}")
    print(f"Updated: {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
