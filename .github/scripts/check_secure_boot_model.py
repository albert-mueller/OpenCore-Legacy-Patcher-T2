#!/usr/bin/env python3
"""
check_secure_boot_model.py: make sure root patching can never be combined with
Apple Secure Boot (SecureBootModel != Disabled).

Root patching rebuilds the Boot/System Kernel Collections, which breaks their
.im4m signature. If the EFI still has SecureBootModel enabled (e.g. "Default",
which resolves to x86legacy + ApECID on non-T2 SMBIOS), boot.efi rejects
BootKernelExtensions.kc (Err(0x1A) MKRN/MKRD) and resets into Recovery. That is
what shipped in 4.0.0.190007 (Issue #465, fixed in f60669b and 6895ce7).

Unlike a grep, this script runs the real EFI builder and the real root-patch
gate and checks what they produce:

  build:sip-lowered       non-T2 models, SIP lowered for root patching, a stale
                          secure_status=True still set -> config.plist must have
                          SecureBootModel=Disabled and ApECID=0
  build:custom-sip        same with a custom SIP value instead of sip_status
  build:t2                T2 models, even with secure_status=True -> Disabled
  defaults:stale-gui      GenerateDefaults() with a settings file that still says
                          GUI:secure_status=True and GUI:sip_status=False (exactly
                          the #465 path), then a build -> secure_status must end
                          up False and config.plist must say Disabled
  runtime:gate            utilities.check_secure_boot_level() with simulated
                          NVRAM (x86legacy / x86legacyap as macOS reports it /
                          T2 j-models / AppleSecureBootPolicy)
                          and detect.py still blocking root patching on it

Usage:
  check_secure_boot_model.py --root DIR [--baseline DIR] --json OUT [--summary OUT.md]

--root      the tree to check (a PR checkout, or the repo itself)
--baseline  optional tree to compare against (main). Problems that also exist
            there are reported as "already broken on main" and don't fail.

Exit code: 0 = OK, 1 = problems (new ones, if --baseline is given),
           2 = the check itself could not run against --root.

The builder imports pyobjc/wx. Off macOS those modules are replaced by inert
stubs - nothing here talks to real hardware, every host value is simulated.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ALLOWED_SBM = {
    # Misc > Security > SecureBootModel values accepted by OpenCore
    "Disabled", "Default", "x86legacy",
    "j137", "j680", "j132", "j174", "j140k", "j780", "j213", "j140a",
    "j152f", "j160", "j230k", "j214k", "j223", "j215", "j185", "j185f",
}

CASES = ["build:sip-lowered", "build:custom-sip", "build:t2", "defaults:stale-gui", "runtime:gate"]

WORKER_TIMEOUT = 30 * 60


# ----------------------------------------------------------------------------
# Worker side - runs inside a subprocess, with cwd/sys.path set to the tree
# ----------------------------------------------------------------------------

def _install_stubs() -> None:
    import importlib.abc
    import importlib.machinery
    from unittest import mock

    stub_roots = {
        "objc", "Foundation", "CoreFoundation", "AppKit", "IOKit", "wx",
        "applescript", "PyObjCTools", "Quartz", "Cocoa", "WebKit",
        "SystemConfiguration", "LocalAuthentication", "ServiceManagement",
        "Security", "webview",
    }

    class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, name, path, target=None):
            if name.split(".")[0] in stub_roots:
                return importlib.machinery.ModuleSpec(name, self, is_package=True)
            return None

        def create_module(self, spec):
            module = mock.MagicMock()
            module.__path__ = []
            module.__spec__ = spec
            return module

        def exec_module(self, module):
            pass

    sys.meta_path.insert(0, _StubFinder())


def _problem(case, subject, message, **extra):
    entry = {"case": case, "subject": subject, "message": message}
    entry.update(extra)
    return entry


def _worker(root: str, case: str, out_path: str) -> None:
    import copy
    import logging
    import plistlib

    os.chdir(root)
    sys.path.insert(0, root)
    if sys.platform != "darwin":
        _install_stubs()
    logging.disable(logging.CRITICAL)

    from opencore_legacy_patcher import constants
    from opencore_legacy_patcher.efi_builder import build
    from opencore_legacy_patcher.datasets import example_data, model_array, os_data, smbios_data
    from opencore_legacy_patcher.support import utilities

    try:
        utilities.disable_cls()
    except Exception:
        pass

    # The host is simulated: a clean NVRAM, nothing booted through OpenCore,
    # and no IODeviceTree:/rom to read. Without the get_rom() stub, building for
    # the host's own model (defaults:stale-gui) crashes off macOS in the minimal
    # SMBIOS spoof (generate_fw_features() reads firmware-features from the ROM),
    # and those models silently end up as "couldn't check". With it the builder
    # takes its normal no-ROM path and falls back to the model's defaults.
    utilities.get_nvram = lambda *args, **kwargs: None
    utilities.get_rom = lambda *args, **kwargs: None

    problems, checked, errors = [], 0, []

    all_models = [m for m in model_array.SupportedSMBIOS if m in smbios_data.smbios_dictionary]
    t2_models = [m for m in model_array.T2Macs if m in smbios_data.smbios_dictionary]
    non_t2_models = [m for m in all_models if m not in model_array.T2Macs]

    host_dump = example_data.iMac.iMac201_Stock

    def new_constants(model, host_is_target=False):
        c = constants.Constants()
        c.computer = copy.deepcopy(host_dump)
        if host_is_target:
            c.computer.real_model = model
        c.detected_os = os_data.os_data.tahoe
        c.validate = True
        c.custom_model = "" if host_is_target else model
        return c

    def run_build(model, c):
        """Build and return Misc > Security from the generated config.plist."""
        release = Path(c.opencore_release_folder)
        config_path = release / "EFI" / "OC" / "config.plist"
        if config_path.exists():
            config_path.unlink()
        try:
            build.BuildOpenCore(model, c)
        except SystemExit as e:
            raise RuntimeError(f"builder exited with code {e.code}") from None
        if not config_path.exists():
            raise RuntimeError("builder did not write EFI/OC/config.plist")
        with config_path.open("rb") as f:
            config = plistlib.load(f)
        return config.get("Misc", {}).get("Security", {})

    def expect_disabled(case_name, model, security, why):
        sbm = security.get("SecureBootModel", "<missing>")
        apecid = security.get("ApECID", "<missing>")
        if sbm not in ALLOWED_SBM:
            problems.append(_problem(case_name, model,
                f"SecureBootModel is '{sbm}', which is not a value OpenCore accepts", value=str(sbm)))
            return
        if sbm != "Disabled":
            problems.append(_problem(case_name, model,
                f"SecureBootModel is '{sbm}' although {why} - root patching breaks the "
                f"Kernel Collections' .im4m and boot.efi resets into Recovery (Issue #465)",
                value=str(sbm)))
        if apecid not in (0, "<missing>"):
            problems.append(_problem(case_name, model,
                f"ApECID is {apecid} although {why} - personalised Secure Boot can't verify patched KCs",
                value=str(apecid)))

    if case in ("build:sip-lowered", "build:custom-sip", "build:t2"):
        models = t2_models if case == "build:t2" else non_t2_models
        for model in models:
            c = new_constants(model)
            c.secure_status = True  # stale/requested Secure Boot - must lose against root patching
            if case == "build:sip-lowered":
                c.sip_status = False
                c.custom_sip_value = None
                why = "SIP is lowered for root patching"
            elif case == "build:custom-sip":
                c.sip_status = True
                c.custom_sip_value = "0x803"
                why = "a custom (lowered) SIP value is set"
            else:
                c.sip_status = True
                c.custom_sip_value = None
                why = "this is a T2 Mac (always built with Secure Boot disabled)"
            try:
                security = run_build(model, c)
            except Exception as e:
                errors.append({"case": case, "subject": model, "message": str(e)[:300]})
                continue
            checked += 1
            expect_disabled(case, model, security, why)

    elif case == "defaults:stale-gui":
        from opencore_legacy_patcher.support import defaults, global_settings

        settings_file = Path(tempfile.mkdtemp()) / "settings.plist"
        with settings_file.open("wb") as f:
            plistlib.dump({"GUI:secure_status": True, "GUI:sip_status": False}, f)

        class FakeSettings:
            def __init__(self):
                self.global_settings_plist = str(settings_file)

            def read_property(self, name):
                with settings_file.open("rb") as f:
                    return plistlib.load(f).get(name)

            def write_property(self, name, value):
                return True

            def delete_property(self, name):
                return True

        global_settings.GlobalEnviromentSettings = FakeSettings

        for model in non_t2_models:
            c = new_constants(model, host_is_target=True)
            try:
                defaults.GenerateDefaults(model, True, c)
            except BaseException as e:
                errors.append({"case": case, "subject": model, "message": f"GenerateDefaults failed: {e!r}"[:300]})
                continue
            if c.sip_status is not False and not c.custom_sip_value:
                errors.append({"case": case, "subject": model,
                               "message": "stored GUI:sip_status=False was not applied - scenario not reproduced"})
                continue
            checked += 1
            if c.secure_status is not False:
                problems.append(_problem(case, model,
                    "GenerateDefaults() left secure_status=True after loading a stored GUI:secure_status=True "
                    "while SIP is lowered (the exact Issue #465 path)", value=str(c.secure_status)))
            try:
                security = run_build(model, c)
            except Exception as e:
                errors.append({"case": case, "subject": model, "message": str(e)[:300]})
                continue
            expect_disabled(case, model, security,
                            "SIP is lowered and only a stale GUI setting asked for Secure Boot")

    elif case == "runtime:gate":
        sbm_values = list(constants.Constants().sbm_values)
        table = [
            (None, 0, False, "no Secure Boot (HardwareModel absent)"),
            ("x86legacy", 0, False, "genuine non-T2 Mac (boot.efi sets x86legacy, policy 0)"),
            ("x86legacy", 1, True, "OpenCore SecureBootModel=x86legacy/Default (policy Medium)"),
            ("x86legacy", 2, True, "x86legacy with policy Full"),
            # What macOS actually reads: OpenCore publishes the model with an "ap"
            # suffix (config j140k -> NVRAM j140kap, x86legacy -> x86legacyap), and
            # every real-hardware dump in example_data.py says x86legacyap.
            ("x86legacyap", 0, False, "genuine non-T2 Mac as macOS reports it (x86legacyap, policy 0)"),
            ("x86legacyap", 1, True, "OpenCore SecureBootModel=x86legacy/Default as macOS reports it (x86legacyap, policy Medium)"),
            ("x86legacyap", 2, True, "x86legacyap with policy Full"),
            ("x86legacyap\x00", 1, True, "x86legacyap with a trailing NUL, policy Medium"),
        ]
        for j_model in sbm_values:
            table.append((j_model, 0, False, f"genuine T2 Mac with Secure Boot off ({j_model})"))
            table.append((j_model, 1, True, f"{j_model}, policy Medium"))
            table.append((j_model, 2, True, f"{j_model} + ApECID, policy Full"))

        for hardware_model, policy, expected, label in table:
            utilities.check_secure_boot_model = lambda hm=hardware_model: hm
            utilities.check_ap_security_policy = lambda p=policy: p
            subject = f"HardwareModel={hardware_model}, AppleSecureBootPolicy={policy}"
            try:
                result = utilities.check_secure_boot_level()
            except Exception as e:
                errors.append({"case": case, "subject": subject, "message": f"check_secure_boot_level() raised {e!r}"[:300]})
                continue
            checked += 1
            if bool(result) != expected:
                if expected:
                    msg = (f"check_secure_boot_level() says Secure Boot is OFF for {label} - root patching "
                           f"would go ahead and the next boot fails with Err(0x1A)")
                else:
                    msg = f"check_secure_boot_level() says Secure Boot is ON for {label} - root patching would be blocked on stock Macs"
                problems.append(_problem(case, subject, msg, value=str(result)))

        # detect.py must still use that gate to block root patching
        detect_src = Path("opencore_legacy_patcher/sys_patch/patchsets/detect.py")
        try:
            src = detect_src.read_text(encoding="utf-8")
        except OSError as e:
            errors.append({"case": case, "subject": str(detect_src), "message": f"can't read: {e}"})
        else:
            checked += 1
            mapped = re.search(r"SECURE_BOOT_MODEL_ENABLED\s*:\s*self\._validation_check_secure_boot_model_enabled\(\)", src)
            uses_gate = re.search(r"def _validation_check_secure_boot_model_enabled\(self\)[^\n]*\n(?:[ \t]+[^\n]*\n|\s*\n)*?"
                                  r"[ \t]+return utilities\.check_secure_boot_level\(\)", src)
            if not (mapped and uses_gate):
                problems.append(_problem(case, str(detect_src),
                    "root-patch validation no longer maps SECURE_BOOT_MODEL_ENABLED to "
                    "utilities.check_secure_boot_level() - root patching wouldn't be blocked with Secure Boot on"))

    else:
        raise SystemExit(f"unknown case {case}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"case": case, "checked": checked, "problems": problems, "errors": errors}, f)


# ----------------------------------------------------------------------------
# Driver side
# ----------------------------------------------------------------------------

def run_tree(root: Path) -> dict:
    results = {"cases": {}, "problems": [], "errors": [], "fatal": None}
    for case in CASES:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            out = tmp.name
        start = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__), "--worker", "--root", str(root), "--case", case, "--out", out],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=WORKER_TIMEOUT, check=False,
            )
            data = json.loads(Path(out).read_text(encoding="utf-8")) if proc.returncode == 0 else None
        except subprocess.TimeoutExpired:
            proc, data = None, None
        finally:
            Path(out).unlink(missing_ok=True)

        if data is None:
            tail = (proc.stderr.decode("utf-8", "replace")[-1500:] if proc else "timed out")
            results["fatal"] = results["fatal"] or f"{case}: the check could not run ({tail.strip().splitlines()[-1] if tail.strip() else 'no output'})"
            results["cases"][case] = {"checked": 0, "problems": 0, "errors": 1, "seconds": round(time.time() - start)}
            print(f"[{case}] could not run:\n{tail}", file=sys.stderr)
            continue

        results["cases"][case] = {"checked": data["checked"], "problems": len(data["problems"]),
                                  "errors": len(data["errors"]), "seconds": round(time.time() - start)}
        results["problems"].extend(data["problems"])
        results["errors"].extend(data["errors"])
        print(f"[{case}] checked {data['checked']}, problems {len(data['problems'])}, "
              f"could not check {len(data['errors'])} ({round(time.time() - start)}s)")

    if not results["fatal"] and not any(c["checked"] for c in results["cases"].values()):
        results["fatal"] = "nothing could be checked - every build failed"
    return results


def _key(p):
    return (p["case"], p["subject"])


def write_summary(path: str, result: dict) -> None:
    lines = ["## SecureBootModel / root patching check", ""]
    if result["fatal"]:
        lines.append(f"❌ The check could not run: `{result['fatal']}`")
    elif result["new"]:
        lines.append(f"❌ {len(result['new'])} problem(s)")
    else:
        lines.append("✅ Root patching can't be combined with Apple Secure Boot in any checked scenario.")
    lines += ["", "| Case | Checked | Problems | Couldn't check |", "|---|---|---|---|"]
    for case, c in result["cases"].items():
        lines.append(f"| `{case}` | {c['checked']} | {c['problems']} | {c['errors']} |")
    for title, items in (("Problems", result["new"]), ("Already broken on main", result["known"])):
        if items:
            lines += ["", f"### {title}", "", "| Case | Where | Problem |", "|---|---|---|"]
            lines += [f"| `{p['case']}` | `{p['subject']}` | {p['message']} |" for p in items[:200]]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--json")
    parser.add_argument("--summary")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--case", help=argparse.SUPPRESS)
    parser.add_argument("--out", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker:
        _worker(os.path.abspath(args.root), args.case, args.out)
        return 0

    print(f"== Checking {args.root}")
    current = run_tree(Path(args.root).resolve())

    known_keys = set()
    if args.baseline:
        print(f"== Checking baseline {args.baseline}")
        base = run_tree(Path(args.baseline).resolve())
        if base["fatal"]:
            print(f"baseline could not be checked ({base['fatal']}) - treating every problem as new")
        else:
            known_keys = {_key(p) for p in base["problems"]}

    new = [p for p in current["problems"] if _key(p) not in known_keys]
    known = [p for p in current["problems"] if _key(p) in known_keys]

    result = {
        "fatal": current["fatal"],
        "cases": current["cases"],
        "new": new,
        "known": known,
        "errors": current["errors"],
    }

    for p in new:
        print(f"PROBLEM [{p['case']}] {p['subject']}: {p['message']}")
    for p in known:
        print(f"already on main [{p['case']}] {p['subject']}: {p['message']}")
    for e in current["errors"][:50]:
        print(f"could not check [{e['case']}] {e['subject']}: {e['message']}")

    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1), encoding="utf-8")
    if args.summary:
        write_summary(args.summary, result)

    if current["fatal"]:
        return 2
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
