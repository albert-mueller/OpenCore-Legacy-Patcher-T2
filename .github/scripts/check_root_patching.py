#!/usr/bin/env python3
"""
check_root_patching.py: make sure root patching can't leave a Mac that no
longer boots.

Root patching edits the mounted system volume, rebuilds the Kernel Collections
and then seals a new APFS snapshot with `bless --create-snapshot`, which becomes
the boot target. Anything that goes wrong *before* that seal is harmless - the
booted snapshot stays untouched. Anything that goes wrong and gets sealed
anyway is what bricks a Mac: a half-written KC, KCs built without the Kernel
Debug Kit, half-copied frameworks, files written to the live (sealed) root
instead of the mounted copy, or a patchset that deletes a file the system needs
to boot.

Unlike a grep, this script runs the real patcher code. Every command it would
run (cp, rm, rsync, kmutil, bless, ...) goes to a simulator that works inside a
temporary sandbox and can be told to fail, so the actual control flow of
sys_patch.py is what gets checked:

  flow:success          everything works -> the patched files really land on the
                        mounted volume, the right Kernel Collections are rebuilt
                        after the last file operation and before the snapshot,
                        bless seals the mounted volume, nothing touches the live
                        root and the run reports success
  flow:kc-failure       kmutil fails -> no snapshot may be sealed
  flow:kdk-failure      the Kernel Debug Kit can't be merged although it is
                        required (Ventura+) -> no KC rebuild, no snapshot
  flow:file-failure     copying a kext / merging a framework fails -> no snapshot
  flow:snapshot-failure bless fails -> the run must not report success
  flow:revert           reverting goes back to the last sealed snapshot of the
                        mounted volume and never seals a new one
  static:patchsets      every patchset, for every supported macOS: no removal or
                        replacement of boot-critical files (kernel, KCs,
                        boot.efi, apfs.kext, ...), no empty/relative/'..' paths
                        (an empty file name turns `rm -R dir/name` into
                        `rm -R dir/`), no data-volume patch type pointing into
                        /System (that hits the sealed live root)
  gate:detect           detect.py still blocks root patching with FileVault on,
                        SIP enabled, a broken seal, or an unsupported macOS

Usage:
  check_root_patching.py --root DIR [--baseline DIR] --json OUT [--summary OUT.md]

--root      the tree to check (a PR checkout, or the repo itself)
--baseline  optional tree to compare against (main). Problems that also exist
            there are reported as "already broken on main" and don't fail.

Exit code: 0 = OK, 1 = problems (new ones, if --baseline is given),
           2 = the check itself could not run against --root.

The patcher imports pyobjc/wx. Off macOS those modules are replaced by inert
stubs. Nothing here touches the real disk outside a temporary directory, and no
command is ever executed for real.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CASES = [
    "flow:success",
    "flow:kc-failure",
    "flow:kdk-failure",
    "flow:file-failure",
    "flow:snapshot-failure",
    "flow:revert",
    "static:patchsets",
    "gate:detect",
]

WORKER_TIMEOUT = 15 * 60

# (XNU major, label) - Big Sur is the first release with sealed system volumes
FLOW_OSES = [
    (20, "macOS 11"),
    (21, "macOS 12"),
    (22, "macOS 13"),
    (23, "macOS 14"),
    (24, "macOS 15"),
    (25, "macOS 26"),
]
VENTURA = 22

# Files a patchset must never remove or replace. Removing or replacing any of
# these leaves a system that can't boot (or can't even be reverted from inside
# macOS). A directory mapped to None means "anything in it".
BOOT_CRITICAL = {
    "/System/Library/Kernels": None,
    "/System/Library/KernelCollections": None,
    "/System/Library/PrelinkedKernels": None,
    "/System/Library/dyld": None,
    "/System/Library/CoreServices": {
        "boot.efi", "bootbase.efi", "PlatformSupport.plist", "SystemVersion.plist",
        "BridgeVersion.plist", "BridgeVersion.bin", "com.apple.Boot.plist",
    },
    "/System/Library/Extensions": {
        "apfs.kext", "AppleACPIPlatform.kext", "AppleAPIC.kext", "AppleSMC.kext",
        "IOACPIFamily.kext", "IOPCIFamily.kext", "IOPlatformPluginFamily.kext",
        "IOStorageFamily.kext", "IONVMeFamily.kext", "IOAHCIFamily.kext", "AppleAHCIPort.kext",
        "AppleKeyStore.kext", "AppleSEPManager.kext", "AppleImage4.kext",
        "AppleMobileFileIntegrity.kext", "AppleSystemPolicy.kext", "Sandbox.kext",
        "System.kext", "corecrypto.kext", "AppleFSCompressionTypeZlib.kext",
        "AppleEFIRuntime.kext", "AppleRTC.kext", "IOSystemManagementFamily.kext",
    },
    "/usr/lib": {"dyld", "libSystem.B.dylib", "libc++.1.dylib", "libobjc.A.dylib"},
    "/sbin": {"launchd", "mount", "mount_apfs", "fsck_apfs"},
    "/usr/standalone/i386": None,
}

LIVE_SYSTEM_PREFIXES = ("/System/", "/usr/", "/bin/", "/sbin/")
LIVE_ALLOWED_PREFIXES = ("/usr/local/",)


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


class CommandSimulator:
    """
    Stands in for every process the patcher would start.

    Records each command in order. File operations whose paths all lie inside
    the sandbox are carried out there for real, so later steps (and the
    checks) see their effect; anything outside the sandbox is only recorded.
    `fail` decides per command which exit code to return instead of 0.
    """

    def __init__(self, sandbox: Path):
        self.sandbox = str(sandbox)
        self.calls = []
        self.fail = lambda argv: 0
        self.readonly = ()   # path prefixes that behave like the sealed live root

    def _inside(self, path: str) -> bool:
        return os.path.abspath(path).startswith(self.sandbox + os.sep)

    def note(self, *argv):
        self.calls.append([str(a) for a in argv])

    def handle(self, argv, *args, **kwargs):
        import subprocess as _sp
        if isinstance(argv, (str, bytes)):
            argv = str(argv).split(" ")
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        rc = self.fail(argv) or 0
        if rc:
            return _sp.CompletedProcess(argv, rc, b"simulated failure\n", b"")
        if self.readonly and os.path.basename(argv[0]) in ("rm", "cp", "rsync", "mv", "mkdir", "ditto") and \
                any(a.startswith(self.readonly) for a in argv[1:]):
            return _sp.CompletedProcess(argv, 1, b"Read-only file system\n", b"")
        out = b""
        try:
            out = self._apply(argv) or b""
        except Exception as e:  # a broken simulated op is a failed op
            return _sp.CompletedProcess(argv, 1, f"simulator: {e}\n".encode(), b"")
        return _sp.CompletedProcess(argv, 0, out, b"")

    def _apply(self, argv):
        import shutil
        tool = os.path.basename(argv[0])
        rest = [a for a in argv[1:] if not a.startswith("-")]
        if tool == "mkdir":
            for p in rest:
                if self._inside(p):
                    os.makedirs(p, exist_ok=True)
        elif tool == "rm":
            for p in rest:
                if self._inside(p):
                    if os.path.isdir(p) and not os.path.islink(p):
                        shutil.rmtree(p, ignore_errors=True)
                    elif os.path.lexists(p):
                        os.unlink(p)
        elif tool in ("cp", "ditto") and len(rest) >= 2:
            src, dst = rest[-2], rest[-1]
            if self._inside(src) and self._inside(dst):
                if os.path.isdir(dst):
                    dst = os.path.join(dst, os.path.basename(src.rstrip("/")))
                if os.path.isdir(src):
                    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
        elif tool == "rsync" and len(rest) >= 2:
            src, dst = rest[-2], rest[-1]
            if self._inside(src) and self._inside(dst):
                target = os.path.join(dst, os.path.basename(src.rstrip("/")))
                if os.path.isdir(src):
                    shutil.copytree(src, target, symlinks=True, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, target)
        elif tool == "mv" and len(rest) >= 2:
            if self._inside(rest[-2]) and self._inside(rest[-1]):
                shutil.move(rest[-2], rest[-1])
        return b""


def _worker(root: str, case: str, out_path: str) -> None:
    import logging

    os.chdir(root)
    sys.path.insert(0, root)
    if sys.platform != "darwin":
        _install_stubs()
    logging.disable(logging.CRITICAL)

    from opencore_legacy_patcher.support import utilities
    try:
        utilities.disable_cls()
    except Exception:
        pass

    if case.startswith("flow:"):
        result = _worker_flow(case)
    elif case == "static:patchsets":
        result = _worker_static()
    elif case == "gate:detect":
        result = _worker_gate()
    else:
        raise SystemExit(f"unknown case {case}")

    problems, checked, errors = result
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"case": case, "checked": checked, "problems": problems, "errors": errors}, f)


# ---------------------------- flow:* -----------------------------------------

def _worker_flow(case: str):
    import copy
    import plistlib
    import shutil
    import subprocess as real_subprocess

    from opencore_legacy_patcher import constants
    from opencore_legacy_patcher.datasets import example_data
    from opencore_legacy_patcher.support import subprocess_wrapper, utilities
    from opencore_legacy_patcher.sys_patch import sys_patch as sp_mod
    from opencore_legacy_patcher.sys_patch import sys_patch_helpers
    from opencore_legacy_patcher.sys_patch.patchsets import HardwarePatchsetSettings, PatchType

    problems, checked, errors = [], 0, []

    sandbox = Path(tempfile.mkdtemp(prefix="oclp-root-patch-"))
    sim = CommandSimulator(sandbox)

    # Every process goes to the simulator - through the wrapper and directly.
    subprocess_wrapper.run = sim.handle
    subprocess_wrapper.run_as_root = sim.handle
    real_subprocess.run = sim.handle
    real_subprocess.Popen = lambda argv, *a, **k: (_ for _ in ()).throw(
        OSError(f"simulator: Popen not supported ({argv!r})"))
    try:
        import importlib
        copy_mod = importlib.import_module("opencore_legacy_patcher.volume.copy")
        copy_mod.can_copy_on_write = lambda *a, **k: False
    except Exception:
        pass
    utilities.check_if_root_is_apfs_snapshot = lambda *a, **k: True
    utilities.get_nvram = lambda *a, **k: None

    class FakeMount:
        def __init__(self, *a, **k):
            pass

        def mount(self):
            sim.note("<mount>")
            return sp_mod.MOUNT_LOCATION_BASE

        def unmount(self, ignore_errors=True):
            sim.note("<unmount>")
            return True

    kdk_mode = {"fail": False}

    class FakeKDKMerge:
        """KernelDebugKitMerge: returns None when no KDK is needed, raises like the real one on failure."""
        def __init__(self, global_constants, mount_location, skip_root_kmutil_requirement):
            self.skip = skip_root_kmutil_requirement

        def merge(self, save_hid_cs=False):
            if self.skip:
                return None
            sim.note("<kdk-merge>")
            if kdk_mode["fail"]:
                raise Exception("Failed to merge KDK with Root Volume (simulated)")
            return "/Library/Developer/KDKs/KDK_simulated.kdk"

    sp_mod.RootVolumeMount = FakeMount
    sp_mod.KernelDebugKitMerge = FakeKDKMerge
    sys_patch_helpers.SysPatchHelpers.install_rsr_repair_binary = lambda self, *a, **k: sim.note("<rsr-repair>")
    sys_patch_helpers.SysPatchHelpers.generate_patchset_plist = lambda self, *a, **k: False

    host = example_data.iMac.iMac201_Stock

    def fresh_tree(xnu):
        """A minimal system volume, data volume and payload inside the sandbox."""
        for child in sandbox.iterdir():
            shutil.rmtree(child, ignore_errors=True)
        mnt = sandbox / "mnt1"
        data = sandbox / "data"
        payload = sandbox / "payload"
        (mnt / "System/Library/CoreServices").mkdir(parents=True)
        (mnt / "System/Library/KernelCollections").mkdir(parents=True)
        (mnt / "System/Library/Frameworks").mkdir(parents=True)
        (mnt / "System/Library/Extensions/OCLPCheckOld.kext/Contents").mkdir(parents=True)
        (mnt / "System/Library/Extensions/OCLPCheckOld.kext/Contents/Info.plist").write_bytes(b"")
        with (mnt / "System/Library/CoreServices/SystemVersion.plist").open("wb") as f:
            plistlib.dump({"ProductBuildVersion": "SIM1", "ProductVersion": "0.0"}, f)
        (data / "Library/Extensions").mkdir(parents=True)
        # The live (booted, sealed) root as seen through the data volume prefix
        (data / "System/Library/Extensions/OCLPCheckOld.kext/Contents").mkdir(parents=True)
        (data / "System/Library/Frameworks").mkdir(parents=True)
        (data / "Library/Application Support").mkdir(parents=True)

        kext = payload / "System/Library/Extensions/OCLPCheck.kext/Contents"
        kext.mkdir(parents=True)
        with (kext / "Info.plist").open("wb") as f:
            plistlib.dump({"CFBundleIdentifier": "com.apple.driver.OCLPCheck",
                           "OSBundleRequired": "Root"}, f)
        (kext / "MacOS").mkdir()
        (kext / "MacOS/OCLPCheck").write_bytes(b"\0" * 16)
        fw = payload / "System/Library/Frameworks/OCLPCheck.framework/Versions/A"
        fw.mkdir(parents=True)
        (fw / "OCLPCheck").write_bytes(b"\0" * 16)
        return mnt, data, payload

    def patchset(payload):
        src = str(payload)
        return {
            "OCLP Check Patch": {
                PatchType.OVERWRITE_SYSTEM_VOLUME: {"/System/Library/Extensions": {"OCLPCheck.kext": src}},
                PatchType.MERGE_SYSTEM_VOLUME:     {"/System/Library/Frameworks": {"OCLPCheck.framework": src}},
                PatchType.REMOVE_SYSTEM_VOLUME:    {"/System/Library/Extensions": ["OCLPCheckOld.kext"]},
            }
        }

    def run(xnu, kdk_required, fail=None, kdk_fail=False, revert=False):
        """Run the real _patch_root_vol() (or _unpatch_root_vol()) once."""
        mnt, data, payload = fresh_tree(xnu)
        sp_mod.MOUNT_LOCATION_BASE = str(mnt)
        sim.calls = []
        sim.fail = fail or (lambda argv: 0)
        sim.readonly = tuple(str(data) + p for p in LIVE_SYSTEM_PREFIXES)
        kdk_mode["fail"] = kdk_fail

        c = constants.Constants()
        c.computer = copy.deepcopy(host)
        c.detected_os = xnu
        c.detected_os_minor = 0
        c.detected_os_build = "SIM1"
        c.detected_os_version = "0.0"
        c.wxpython_variant = False
        c.gui_mode = False

        details = {
            HardwarePatchsetSettings.KERNEL_DEBUG_KIT_REQUIRED:     kdk_required,
            HardwarePatchsetSettings.KERNEL_DEBUG_KIT_MISSING:      False,
            HardwarePatchsetSettings.METALLIB_SUPPORT_PKG_REQUIRED: False,
            HardwarePatchsetSettings.METALLIB_SUPPORT_PKG_MISSING:  False,
        }
        patcher = sp_mod.PatchSysVolume("iMac20,1", c, hardware_details=details)
        if patcher.mount_location != str(mnt):
            raise RuntimeError(f"PatchSysVolume mounts at {patcher.mount_location!r}, not at "
                               f"MOUNT_LOCATION_BASE - can't simulate the mounted volume")
        # The data volume is '/' on a real Mac; relocate it into the sandbox
        patcher.mount_location_data = str(data)
        patcher.mount_application_support = f"{data}/Library/Application Support"
        patcher.patch_set_dictionary = patchset(payload)

        exit_code = None
        try:
            if revert:
                patcher._unpatch_root_vol()
            else:
                patcher._patch_root_vol()
        except SystemExit as e:
            exit_code = e.code
        return {
            "calls": list(sim.calls),
            "succeeded": bool(getattr(c, "root_patcher_succeeded", False)),
            "mnt": str(mnt), "data": str(data),
            "skip_kc": patcher.skip_root_kmutil_requirement,
            "exit": exit_code,
        }

    def idx(calls, pred):
        return [i for i, a in enumerate(calls) if pred(a)]

    def is_tool(a, name):
        return os.path.basename(a[0]) == name

    def is_seal(a):
        return is_tool(a, "bless") and "--create-snapshot" in a

    def is_kmutil(a):
        return is_tool(a, "kmutil")

    def is_sysvol_fileop(a, mnt):
        return os.path.basename(a[0]) in ("cp", "rm", "rsync", "mv", "ditto", "mkdir") and \
               any(x.startswith(mnt + "/") for x in a[1:])

    def live_root_writes(calls, sandbox_root, data_root):
        # On a real Mac the data volume prefix is '' - so anything the patcher
        # sends to <data>/System, <data>/usr, ... would land on the live root.
        live_via_data = tuple(data_root + p for p in LIVE_SYSTEM_PREFIXES)
        bad = []
        for a in calls:
            tool = os.path.basename(a[0])
            paths = []
            if tool in ("rm", "mkdir", "chmod", "chown", "touch", "ln"):
                paths = [x for x in a[1:] if x.startswith("/")]
            elif tool in ("cp", "rsync", "ditto", "mv"):
                rest = [x for x in a[1:] if not x.startswith("-")]
                paths = rest[-1:] if tool != "mv" else rest[-2:]
            elif tool == "kmutil":
                for flag in ("--volume-root", "--boot-path", "--system-path"):
                    if flag in a and a.index(flag) + 1 < len(a):
                        paths.append(a[a.index(flag) + 1])
            elif tool == "bless":
                for flag in ("--mount", "--folder"):
                    if flag in a and a.index(flag) + 1 < len(a):
                        paths.append(a[a.index(flag) + 1])
            for p in paths:
                if p.startswith(live_via_data):
                    bad.append(" ".join(a)[:200])
                    break
                if p.startswith(sandbox_root):
                    continue
                if p.startswith(LIVE_ALLOWED_PREFIXES):
                    continue
                if p.startswith(LIVE_SYSTEM_PREFIXES) or p == "/":
                    bad.append(" ".join(a)[:200])
                    break
        return bad

    def scenarios_for(case):
        out = []
        for xnu, label in FLOW_OSES:
            modes = [False] if xnu < VENTURA else [True, False]
            for kdk in modes:
                subject = f"{label} (Darwin {xnu})" + ("" if xnu < VENTURA else (", KDK" if kdk else ", no KDK"))
                out.append((xnu, kdk, subject))
        return out

    sandbox_root = str(sandbox) + os.sep

    for xnu, kdk, subject in scenarios_for(case):
        try:
            if case == "flow:success":
                r = run(xnu, kdk)
                checked += 1
                calls, mnt = r["calls"], r["mnt"]
                seals = idx(calls, is_seal)
                kcs = idx(calls, is_kmutil)
                fileops = idx(calls, lambda a: is_sysvol_fileop(a, mnt))

                if xnu >= VENTURA and not kdk:
                    want, want_desc = (lambda a: "--new" in a and "aux" in a), "the Auxiliary KC (kmutil create --new aux)"
                else:
                    want, want_desc = (lambda a: "--volume-root" in a and mnt in a), f"the Boot/System KCs (kmutil --volume-root {mnt})"
                good_kc = [i for i in kcs if want(calls[i])]

                if not good_kc:
                    problems.append(_problem(case, subject,
                        f"Kexts were added to / removed from the system volume, but {want_desc} was never rebuilt - "
                        f"the snapshot is sealed with Kernel Collections that don't match the patched kexts"
                        + (" (the AuxKC is skipped whenever no KDK is needed, so the patched kexts never load)"
                           if xnu >= VENTURA and not kdk else "")))
                elif fileops and good_kc[-1] < fileops[-1]:
                    problems.append(_problem(case, subject,
                        "Files on the system volume were changed after the Kernel Collections were rebuilt - "
                        "the sealed KCs don't contain the final state"))
                if not seals:
                    problems.append(_problem(case, subject,
                        "Patching finished without sealing a snapshot (bless --create-snapshot never ran) - "
                        "the patches never become the boot target"))
                else:
                    a = calls[seals[-1]]
                    target = a[a.index("--mount") + 1] if "--mount" in a else (a[a.index("--folder") + 1] if "--folder" in a else "")
                    if target not in (mnt, f"{mnt}/System/Library/CoreServices"):
                        problems.append(_problem(case, subject,
                            f"bless seals '{target}' instead of the mounted, patched volume ({mnt})"))
                    if good_kc and seals[-1] < good_kc[-1]:
                        problems.append(_problem(case, subject,
                            "The snapshot is sealed before the Kernel Collections are rebuilt - "
                            "it boots with stale KCs"))
                if not r["succeeded"]:
                    problems.append(_problem(case, subject,
                        "A fully successful run doesn't report success (root_patcher_succeeded is False)"))

                bad = live_root_writes(calls, sandbox_root, r["data"])
                if bad:
                    problems.append(_problem(case, subject,
                        f"Writes to the live, sealed root volume instead of the mounted copy "
                        f"({len(bad)}x, first: {bad[0]}) - this is the Issue #161 class of bug"))

                expected_kext_dir = (Path(r["data"]) / "Library/Extensions") if (xnu >= VENTURA and not kdk) \
                                    else (Path(mnt) / "System/Library/Extensions")
                missing = []
                if not (expected_kext_dir / "OCLPCheck.kext/Contents/Info.plist").exists():
                    missing.append(f"OCLPCheck.kext in {expected_kext_dir}")
                if not (Path(mnt) / "System/Library/Frameworks/OCLPCheck.framework/Versions/A/OCLPCheck").exists():
                    missing.append("merged OCLPCheck.framework")
                if (Path(mnt) / "System/Library/Extensions/OCLPCheckOld.kext").exists():
                    missing.append("removal of OCLPCheckOld.kext")
                if missing:
                    problems.append(_problem(case, subject,
                        "Patch files didn't end up on the mounted volume (" + ", ".join(missing) +
                        ") although the run went on to seal the snapshot"))

            elif case == "flow:kc-failure":
                r = run(xnu, kdk, fail=lambda a: 1 if is_kmutil(a) else 0)
                checked += 1
                if not idx(r["calls"], is_kmutil):
                    continue  # covered by flow:success (KC never rebuilt)
                if idx(r["calls"], is_seal):
                    problems.append(_problem(case, subject,
                        "kmutil failed, but the snapshot was sealed anyway - the Mac boots into broken "
                        "Kernel Collections"))
                if r["succeeded"]:
                    problems.append(_problem(case, subject, "kmutil failed, but the run reports success"))

            elif case == "flow:kdk-failure":
                if xnu < VENTURA or not kdk:
                    continue
                r = run(xnu, kdk, kdk_fail=True)
                checked += 1
                calls = r["calls"]
                if not idx(calls, lambda a: a[0] == "<kdk-merge>"):
                    errors.append({"case": case, "subject": subject,
                                   "message": "the KDK merge was never attempted - scenario not reproduced"})
                    continue
                if idx(calls, is_kmutil) or idx(calls, is_seal) or r["succeeded"]:
                    did = [w for w, hit in (("ran kmutil", idx(calls, is_kmutil)),
                                            ("sealed the snapshot", idx(calls, is_seal)),
                                            ("reported success", r["succeeded"])) if hit]
                    problems.append(_problem(case, subject,
                        "Merging the Kernel Debug Kit failed, but patching went on and " + ", ".join(did) +
                        ". On Ventura+ the system volume has no kext binaries without the KDK, so the "
                        "rebuilt Kernel Collections can miss kexts the Mac needs to boot "
                        "(_merge_kdk_with_root() swallows the exception)"))

            elif case == "flow:file-failure":
                for what, pred in (
                    ("copying OCLPCheck.kext", lambda a: 1 if is_tool(a, "cp") and any(x.endswith("/OCLPCheck.kext") for x in a[1:-1]) else 0),
                    ("merging OCLPCheck.framework (rsync)", lambda a: 23 if is_tool(a, "rsync") else 0),
                ):
                    r = run(xnu, kdk, fail=pred)
                    checked += 1
                    hits = [w for w, hit in (("sealed the snapshot", idx(r["calls"], is_seal)),
                                             ("reported success", r["succeeded"])) if hit]
                    if hits:
                        problems.append(_problem(case, f"{subject}: {what}",
                            f"{what[0].upper() + what[1:]} failed, but the run " + " and ".join(hits) +
                            " - the Mac boots a snapshot with half-installed patches"))

            elif case == "flow:snapshot-failure":
                r = run(xnu, kdk, fail=lambda a: 1 if is_seal(a) else 0)
                checked += 1
                if not idx(r["calls"], is_seal):
                    continue  # covered by flow:success
                if r["succeeded"]:
                    problems.append(_problem(case, subject,
                        "bless --create-snapshot failed, but the run reports success - the user is told to reboot "
                        "into patches that were never sealed"))

            elif case == "flow:revert":
                r = run(xnu, kdk, revert=True)
                checked += 1
                calls, mnt = r["calls"], r["mnt"]
                reverts = idx(calls, lambda a: is_tool(a, "bless") and "--last-sealed-snapshot" in a)
                if not reverts:
                    problems.append(_problem(case, subject,
                        "Reverting never runs bless --last-sealed-snapshot - the patched snapshot stays the boot target"))
                else:
                    a = calls[reverts[0]]
                    target = a[a.index("--mount") + 1] if "--mount" in a else ""
                    if target != mnt:
                        problems.append(_problem(case, subject,
                            f"Reverting points bless at '{target}' instead of the mounted root volume ({mnt})"))
                if idx(calls, is_seal):
                    problems.append(_problem(case, subject, "Reverting seals a new snapshot instead of going back"))
                bad = live_root_writes(calls, sandbox_root, r["data"])
                if bad:
                    problems.append(_problem(case, subject,
                        f"Reverting writes to the live, sealed root volume ({bad[0]})"))

                # A failed revert must never be reported as done
                r = run(xnu, kdk, revert=True,
                        fail=lambda a: 1 if is_tool(a, "bless") and "--last-sealed-snapshot" in a else 0)
                checked += 1
                if r["succeeded"]:
                    problems.append(_problem(case, f"{subject}: bless fails",
                        "Reverting failed, but the run reports success"))
        except Exception as e:
            errors.append({"case": case, "subject": subject, "message": f"{type(e).__name__}: {e}"[:300]})

    shutil.rmtree(sandbox, ignore_errors=True)
    # Keep messages stable between runs (and between --root and --baseline)
    for entry in problems + errors:
        entry["message"] = entry["message"].replace(str(sandbox), "<sandbox>")
    return problems, checked, errors


# ---------------------------- static:patchsets -------------------------------

def _worker_static():
    import copy
    import posixpath

    from opencore_legacy_patcher import constants
    from opencore_legacy_patcher.datasets import example_data
    from opencore_legacy_patcher.sys_patch.patchsets import detect
    from opencore_legacy_patcher.sys_patch.patchsets.base import PatchType, DynamicPatchset

    problems, checked, errors = [], 0, []
    case = "static:patchsets"

    system_types = {PatchType.OVERWRITE_SYSTEM_VOLUME, PatchType.MERGE_SYSTEM_VOLUME, PatchType.REMOVE_SYSTEM_VOLUME}
    data_types = {PatchType.OVERWRITE_DATA_VOLUME, PatchType.MERGE_DATA_VOLUME, PatchType.REMOVE_DATA_VOLUME}
    remove_types = {PatchType.REMOVE_SYSTEM_VOLUME, PatchType.REMOVE_DATA_VOLUME}
    file_types = system_types | data_types

    # Use detect.py's own list of patchsets, so a new one is checked automatically
    detect.HardwarePatchsetDetection._detect = lambda self: None

    seen = set()

    def report(subject, message):
        key = (subject, message)
        if key in seen:
            return
        seen.add(key)
        problems.append(_problem(case, subject, message))

    def bad_name(name):
        if not isinstance(name, str):
            return f"is not a string ({type(name).__name__})"
        if name.strip() != name or not name:
            return "is empty or has leading/trailing whitespace"
        if name in (".", "..") or "/" in name:
            return "is '.', '..' or contains '/'"
        if any(ch in name for ch in "*?[]"):
            return "contains a glob character"
        return None

    def bad_dir(d):
        if not isinstance(d, str) or not d.startswith("/"):
            return "is not an absolute path"
        if d.rstrip("/") == "":
            return "is the volume root"
        if ".." in d.split("/"):
            return "contains '..'"
        return None

    for xnu in range(20, 26):
        for minor in (0, 5):
            c = constants.Constants()
            c.computer = copy.deepcopy(example_data.iMac.iMac201_Stock)
            c.detected_os = xnu
            c.detected_os_minor = minor
            c.detected_os_build = "SIM1"
            c.detected_os_version = f"{xnu - 9}.{minor}"
            try:
                d = detect.HardwarePatchsetDetection(c, xnu_major=xnu, xnu_minor=minor,
                                                     os_build="SIM1", os_version=c.detected_os_version,
                                                     validation=True)
                variants = list(d._hardware_variants)
            except Exception as e:
                errors.append({"case": case, "subject": f"Darwin {xnu}.{minor}",
                               "message": f"couldn't list patchsets: {type(e).__name__}: {e}"[:300]})
                continue

            for hw in variants:
                label = f"{hw.__module__.rsplit('.', 1)[-1]}.{hw.__name__}"
                try:
                    item = hw(xnu_major=xnu, xnu_minor=minor, os_build="SIM1", global_constants=c)
                    patches = item.patches()
                except Exception as e:
                    errors.append({"case": case, "subject": f"{label} on Darwin {xnu}.{minor}",
                                   "message": f"patches() raised {type(e).__name__}: {e}"[:300]})
                    continue
                checked += 1
                if not isinstance(patches, dict):
                    report(label, f"patches() returns {type(patches).__name__}, not a dict")
                    continue

                for patch_name, body in patches.items():
                    subject = f"{label} / {patch_name}"
                    if not isinstance(body, dict):
                        report(subject, "patch body is not a dict")
                        continue
                    for ptype, entries in body.items():
                        if ptype == PatchType.EXECUTE or ptype not in file_types:
                            if ptype not in file_types and ptype != PatchType.EXECUTE:
                                report(subject, f"unknown patch type '{ptype}' - sys_patch.py silently ignores it")
                            continue
                        if not isinstance(entries, dict):
                            report(subject, f"{ptype}: not a dict of directories")
                            continue
                        for directory, files in entries.items():
                            why = bad_dir(directory)
                            if why:
                                report(subject, f"{ptype}: directory {directory!r} {why}")
                                continue
                            norm = posixpath.normpath(directory)
                            if ptype in data_types and (norm == "/System" or norm.startswith("/System/")):
                                report(subject,
                                       f"{ptype} targets {directory} - data-volume patches are applied to the live root, "
                                       f"which is sealed and read-only on Big Sur+, so patching fails half way "
                                       f"(the Issue #161 class of bug)")
                            names = list(files) if ptype in remove_types else (list(files) if isinstance(files, dict) else None)
                            if names is None:
                                report(subject, f"{ptype}: {directory} is not a dict of file -> source")
                                continue
                            for name in names:
                                why = bad_name(name)
                                if why:
                                    report(subject, f"{ptype}: file name {name!r} in {directory} {why} - "
                                                    f"`rm -R {directory}/{name}` would hit far more than one file")
                                    continue
                                if ptype not in remove_types:
                                    src = files[name]
                                    if not (isinstance(src, str) and src) and src not in list(DynamicPatchset):
                                        report(subject, f"{ptype}: {directory}/{name} has no source ({src!r})")
                                crit = BOOT_CRITICAL.get(norm, False)
                                if crit is None or (crit and name in crit):
                                    verb = "removes" if ptype in remove_types else "replaces"
                                    report(subject,
                                           f"{verb} {norm}/{name} - the Mac can't boot (or be reverted from macOS) "
                                           f"without the original")

    return problems, checked, errors


# ---------------------------- gate:detect ------------------------------------

def _worker_gate():
    import copy
    import plistlib
    import subprocess as real_subprocess
    from types import SimpleNamespace

    import py_sip_xnu

    from opencore_legacy_patcher import constants
    from opencore_legacy_patcher.datasets import example_data, sip_data
    from opencore_legacy_patcher.support import utilities, global_settings
    from opencore_legacy_patcher.sys_patch.patchsets import detect

    problems, checked, errors = [], 0, []
    case = "gate:detect"

    state = {"fv": False, "seal": "Yes", "sip": 0, "nvram": {}}

    def fake_run(argv, *a, **k):
        argv = [str(x) for x in (argv if isinstance(argv, (list, tuple)) else str(argv).split(" "))]
        out = b""
        if argv and argv[0].endswith("fdesetup"):
            out = b"FileVault is On.\n" if state["fv"] else b"FileVault is Off.\n"
        elif argv[:2] == ["/usr/sbin/diskutil", "info"]:
            out = plistlib.dumps({"Sealed": state["seal"], "APFSSnapshot": True})
        return real_subprocess.CompletedProcess(argv, 0, out, b"")

    real_subprocess.run = fake_run
    detect.subprocess.run = fake_run

    class FakeSip:
        def get_sip_status(self):
            return SimpleNamespace(value=state["sip"])

    py_sip_xnu.SipXnu = FakeSip
    utilities.get_nvram = lambda name, *a, **k: state["nvram"].get(name)
    utilities.check_secure_boot_level = lambda *a, **k: False    # covered by the SecureBootModel check
    utilities.check_kext_loaded = lambda *a, **k: None
    utilities.find_any_oclp_manifest = lambda *a, **k: None
    detect.network_handler.NetworkUtilities.verify_network_connection = lambda self, *a, **k: True

    class FakeAmfi:
        def __init__(self, *a, **k):
            pass

        def check_config(self, level):
            return True

    detect.amfi_detect.AmfiConfigurationDetection = FakeAmfi

    class FakeSettings:
        def __init__(self, *a, **k):
            pass

        def read_property(self, *a, **k):
            return None

        def write_property(self, *a, **k):
            return True

        def delete_property(self, *a, **k):
            return True

    global_settings.GlobalEnviromentSettings = FakeSettings
    detect.HardwarePatchsetDetection._is_cached_kernel_debug_kit_present = lambda self: True
    detect.HardwarePatchsetDetection._is_cached_metallib_support_pkg_present = lambda self: True
    detect.HardwarePatchsetDetection._dortania_internal_check = lambda self: False

    # A Mac that actually needs root patches on every macOS checked here
    host = example_data.MacBookPro.MacBookPro111_Stock

    def evaluate(xnu):
        csr = sip_data.system_integrity_protection.csr_values
        for k in csr:                          # csr_decode() caches set bits in this global
            csr[k] = False
        c = constants.Constants()
        c.computer = copy.deepcopy(host)
        c.detected_os = xnu
        c.detected_os_minor = 0
        c.detected_os_build = "SIM1"
        c.detected_os_version = f"{xnu - 9}.0"
        d = detect.HardwarePatchsetDetection(c, xnu_major=xnu, xnu_minor=0, os_build="SIM1",
                                             os_version=c.detected_os_version)
        return d

    def lowered_sip(xnu):
        from opencore_legacy_patcher.datasets.sip_data import system_integrity_protection as s
        configs = s.root_patch_sip_ventura if xnu >= 22 else s.root_patch_sip_big_sur
        value = 0
        for cfg in configs:
            if cfg in s.csr_values_extended:
                value |= s.csr_values_extended[cfg]["value"]
        return value

    blocked_scenarios = [
        ("FileVault is on", dict(fv=True),
         "root patching must not run with FileVault on (the patched system fails to boot)"),
        ("SIP is enabled", dict(sip=0),
         "root patching must not run with SIP enabled (mounting/sealing fails half way)"),
        ("the system volume's seal is broken", dict(seal="Broken"),
         "patching on top of a broken seal without reverting first stacks changes on an unknown state"),
    ]

    # MacBookPro11,1 needs root patches (Haswell graphics) from Ventura on
    for xnu in (22, 23, 25):
        base = dict(fv=False, seal="Yes", sip=lowered_sip(xnu), nvram={})
        subject_os = f"Darwin {xnu}"
        try:
            state.update(base)
            control = evaluate(xnu)
        except Exception as e:
            errors.append({"case": case, "subject": subject_os,
                           "message": f"detection raised {type(e).__name__}: {e}"[:300]})
            continue
        if not control.can_patch:
            blockers = [k for k, v in control.device_properties.items() if str(k).startswith("Validation:") and v is True]
            errors.append({"case": case, "subject": f"{subject_os}: control",
                           "message": "patching is blocked even with every requirement met - the gate can't be "
                                      "checked (" + "; ".join(str(b) for b in blockers)[:200] + ")"})
            continue
        if not control.patches:
            errors.append({"case": case, "subject": f"{subject_os}: control",
                           "message": "the simulated Mac needs no patches here - the gate can't be checked"})
            continue
        checked += 1

        for label, change, why in blocked_scenarios:
            state.update(base)
            state.update(change)
            try:
                d = evaluate(xnu)
            except Exception as e:
                errors.append({"case": case, "subject": f"{subject_os}: {label}",
                               "message": f"detection raised {type(e).__name__}: {e}"[:300]})
                continue
            checked += 1
            if d.can_patch:
                problems.append(_problem(case, f"{subject_os}: {label}",
                                         f"detect.py allows root patching although {label} - {why}"))

    for xnu, label in ((19, "macOS 10.15 (no sealed volume support)"), (26, "an unknown, newer macOS")):
        state.update(dict(fv=False, seal="Yes", sip=lowered_sip(25), nvram={}))
        try:
            d = evaluate(xnu)
        except Exception as e:
            errors.append({"case": case, "subject": f"Darwin {xnu}",
                           "message": f"detection raised {type(e).__name__}: {e}"[:300]})
            continue
        checked += 1
        if d.can_patch:
            problems.append(_problem(case, f"Darwin {xnu}",
                                     f"detect.py allows root patching on {label} - patches for other releases "
                                     f"would be installed and sealed"))

    return problems, checked, errors


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
        except (OSError, ValueError):
            data = None
        finally:
            Path(out).unlink(missing_ok=True)

        if data is None:
            tail = (proc.stderr.decode("utf-8", "replace")[-1500:] if proc else "timed out")
            last = tail.strip().splitlines()[-1] if tail.strip() else "no output"
            results["fatal"] = results["fatal"] or f"{case}: the check could not run ({last})"
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
        results["fatal"] = "nothing could be checked"
    return results


def _key(p):
    return (p["case"], p["subject"], p["message"])


def write_summary(path: str, result: dict) -> None:
    lines = ["## Root patching bootability check", ""]
    if result["fatal"]:
        lines.append(f"❌ The check could not run: `{result['fatal']}`")
    elif result["new"]:
        lines.append(f"❌ {len(result['new'])} problem(s)")
    else:
        lines.append("✅ No checked root patching scenario can leave an unbootable system.")
    lines += ["", "| Case | Checked | Problems | Couldn't check |", "|---|---|---|---|"]
    for case, c in result["cases"].items():
        lines.append(f"| `{case}` | {c['checked']} | {c['problems']} | {c['errors']} |")
    for title, items in (("Problems", result["new"]), ("Already broken on main", result["known"])):
        if items:
            lines += ["", f"### {title}", "", "| Case | Where | Problem |", "|---|---|---|"]
            lines += [f"| `{p['case']}` | `{p['subject']}` | {p['message']} |" for p in items[:200]]
    if result["errors"]:
        lines += ["", "### Couldn't check", "", "| Case | Where | Error |", "|---|---|---|"]
        lines += [f"| `{e['case']}` | `{e['subject']}` | {e['message']} |" for e in result["errors"][:100]]
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
