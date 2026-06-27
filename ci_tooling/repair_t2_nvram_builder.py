#!/usr/bin/env python3
"""Repair the unreachable T2 NVRAM builder block and optionally patch a built EFI.

The current builder places the T2 NVRAM setup after sys.exit(3) inside an
exception handler, so the setup never runs on a successful build. This tool
moves that setup into its own reachable try/except block. With --config it also
applies the same settings to an existing EFI config for an isolated boot test.
"""

from __future__ import annotations

import argparse
import plistlib
import py_compile
import shutil
import sys
from pathlib import Path


APPLE_NVRAM_GUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
SOURCE_START = "        if is_t2:\n"
SOURCE_END = "        else:\n            # For Non-T2 Legacy Hardware\n"

CORRECTED_T2_BLOCK = '''        if is_t2:
            try:
                logging.info("- Applying in-memory T2 booter and SMBIOS alignment")
                self.config.setdefault("Booter", {}).setdefault("Quirks", {}).update({
                    "RebuildAppleMemoryMap": False,
                    "EnableWriteUnprotector": False,
                    "SyncRuntimePermissions": False,
                    "DevirtualiseMmio": False,
                })
                self.config.setdefault("PlatformInfo", {})["UpdateSMBIOSMode"] = "Custom"
                self.config.setdefault("Kernel", {}).setdefault("Quirks", {})["CustomSMBIOSGuid"] = True
                self.config.setdefault("Misc", {}).setdefault("Security", {})["SecureBootModel"] = "Disabled"
            except Exception as e:
                logging.error("Whoops, applying in-memory T2 booter and SMBIOS alignments failed because of the following error:")
                logging.exception("Stack Trace:")
                logging.info("Please try again later.")
                sys.exit(3)

            try:
                logging.info("- Adding T2-specific bypass NVRAM variables")
                nvram = self.config.setdefault("NVRAM", {})
                nvram_add = nvram.setdefault("Add", {})
                nvram_delete = nvram.setdefault("Delete", {})
                apple_add = nvram_add.setdefault(APPLE_NVRAM_GUID, {"boot-args": ""})
                apple_delete = nvram_delete.setdefault(APPLE_NVRAM_GUID, [])

                for target_arg in ["boot-args", "csr-active-config", "amfi-allow-arguments"]:
                    if target_arg not in apple_delete:
                        apple_delete.append(target_arg)

                raw_args = apple_add.get("boot-args", "")
                boot_args = [arg for arg in raw_args.split() if not arg.startswith("-lilu")]
                for required_arg in ["-ibtcompatbeta", "-amfipassbeta"]:
                    if required_arg not in boot_args:
                        boot_args.append(required_arg)
                apple_add["boot-args"] = " ".join(boot_args)

                nvram["WriteFlash"] = True
                self.config.setdefault("Kernel", {}).setdefault("Quirks", {})["DisableIoMapper"] = True
            except Exception as e:
                logging.error("Whoops, applying the T2 NVRAM setup failed because of the following error:")
                logging.exception("Stack Trace:")
                logging.info("Please try again later.")
                sys.exit(3)
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair T2 NVRAM builder setup")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("opencore_legacy_patcher/efi_builder/build.py"),
        help="Path to build.py",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional path to an existing EFI/OC/config.plist",
    )
    return parser.parse_args()


def repair_source(source_path: Path) -> None:
    source_path = source_path.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Source not found: {source_path}")

    text = source_path.read_text(encoding="utf-8")
    start = text.find(SOURCE_START)
    end = text.find(SOURCE_END, start)
    if start < 0 or end < 0:
        raise RuntimeError("Could not locate the T2 builder block")

    existing = text[start:end]
    if "try:\n                logging.info(\"- Adding T2-specific bypass NVRAM variables\")" in existing and "sys.exit(3)\n\n            try:" in existing:
        print("Builder source: already repaired")
        return

    backup = source_path.with_name(source_path.name + ".before-t2-nvram-fix")
    if not backup.exists():
        shutil.copy2(source_path, backup)

    updated = text[:start] + CORRECTED_T2_BLOCK + text[end:]
    source_path.write_text(updated, encoding="utf-8")
    py_compile.compile(str(source_path), doraise=True)
    print(f"Builder source repaired: {source_path}")
    print(f"Source backup: {backup}")


def patch_config(config_path: Path) -> None:
    config_path = config_path.expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")

    raw = config_path.read_bytes()
    plist_format = plistlib.FMT_BINARY if raw.startswith(b"bplist00") else plistlib.FMT_XML
    config = plistlib.loads(raw)

    nvram = config.setdefault("NVRAM", {})
    nvram_add = nvram.setdefault("Add", {})
    nvram_delete = nvram.setdefault("Delete", {})
    apple_add = nvram_add.setdefault(APPLE_NVRAM_GUID, {"boot-args": ""})
    apple_delete = nvram_delete.setdefault(APPLE_NVRAM_GUID, [])

    for target_arg in ["boot-args", "csr-active-config", "amfi-allow-arguments"]:
        if target_arg not in apple_delete:
            apple_delete.append(target_arg)

    raw_args = apple_add.get("boot-args", "")
    if isinstance(raw_args, bytes):
        raw_args = raw_args.decode("utf-8", errors="ignore")
    boot_args = [arg for arg in str(raw_args).split() if not arg.startswith("-lilu")]
    for required_arg in ["-ibtcompatbeta", "-amfipassbeta"]:
        if required_arg not in boot_args:
            boot_args.append(required_arg)
    apple_add["boot-args"] = " ".join(boot_args)

    nvram["WriteFlash"] = True
    config.setdefault("Kernel", {}).setdefault("Quirks", {})["DisableIoMapper"] = True

    backup = config_path.with_name(config_path.name + ".before-t2-nvram-path-test")
    if not backup.exists():
        shutil.copy2(config_path, backup)

    temp = config_path.with_name(config_path.name + ".tmp")
    with temp.open("wb") as stream:
        plistlib.dump(config, stream, fmt=plist_format, sort_keys=False)
    temp.replace(config_path)

    print(f"EFI config patched: {config_path}")
    print(f"boot-args: {apple_add['boot-args']}")
    print("WriteFlash: True")
    print("DisableIoMapper: True")
    print(f"Config backup: {backup}")


def main() -> int:
    args = parse_args()
    try:
        repair_source(args.source)
        if args.config:
            patch_config(args.config)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
