"""
gui_build_verify.py: Verify a generated build against TEST-B requirements.

Rules this module follows:
- Fail closed. Any unmet requirement means FAILED; warnings never count as a pass.
- Check what OpenCore will actually do (enabled config entries, kernel ranges,
  load order), not just whether files with the right names exist.
- Parse and hash the same bytes, and refuse symlinks anywhere in the EFI tree.
"""

import hashlib
import logging
import os
import plistlib
import stat
import threading

import wx

from .. import constants
from ..wx_gui import gui_main_menu, gui_support


APPLE_BOOT_GUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"

# ----- TEST-B specification ------------------------------------------------
# A value of None means "report only". Every None is printed as UNASSERTED so
# an incomplete spec can never look like a clean pass.
TEST_B_SPEC = {
    "target_kernel": "25.0.0",          # Darwin version the build must load on
    "required_kexts": [                 # in required load order
        "Lilu.kext",
        "IOSkywalkFamily.kext",
        "IO80211FamilyLegacy.kext",
        "AirportBrcmFixup.kext",
        "AMFIPass.kext",
    ],
    "lilu_dependents": ["AirportBrcmFixup.kext", "AMFIPass.kext"],
    "require_skywalk_block": True,      # Kernel > Block com.apple.iokit.IOSkywalkFamily
    "whatevergreen_enabled": None,      # True / False
    "boot_args_required": [],           # e.g. ["-wegnoegpu"]
    "boot_args_forbidden": [],          # e.g. ["dart=0"]
    "allow_amd_patches": None,          # False to forbid any AMD/Polaris kernel patch
    "expected_config_sha256": None,     # known-good hash, if you have one
    "expected_tree_sha256": None,       # known-good hash of the whole EFI folder
}

REPORTED_BOOT_ARGS = ("-wegnoegpu", "dart=0")
AMD_KEYWORDS = ("amd", "polaris", "radeon", "navi", "vega")


class VerificationError(Exception):
    """Structural problem that makes further verification meaningless."""


def _kernel_tuple(value):
    """'25.0.0' -> (25, 0, 0). Empty means unbounded, as in OpenCore."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise VerificationError(f"Kernel version {value!r} is not a string")
    parts = value.split(".")
    if not all(p.isdigit() for p in parts) or len(parts) > 3:
        raise VerificationError(f"Malformed kernel version {value!r}")
    nums = [int(p) for p in parts] + [0] * (3 - len(parts))
    return tuple(nums)


def _is_active(entry, target):
    """True if OpenCore would apply this entry on the target kernel."""
    if entry.get("Enabled") is not True:
        return False
    low = _kernel_tuple(entry.get("MinKernel", ""))
    high = _kernel_tuple(entry.get("MaxKernel", ""))
    return (low is None or target >= low) and (high is None or target <= high)


def _typed(container, key, expected_type, default):
    value = container.get(key, default)
    if not isinstance(value, expected_type):
        raise VerificationError(
            f"config.plist: '{key}' is {type(value).__name__}, expected {expected_type.__name__}"
        )
    return value


def _dict_entries(items, section):
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise VerificationError(f"config.plist: {section}[{index}] is not a dictionary")
    return items


def _hash_tree(root):
    """SHA256 over every regular file under root. Any symlink is an error."""
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in dirnames:
            if os.path.islink(os.path.join(dirpath, name)):
                raise VerificationError(f"Symlinked folder in EFI: {os.path.join(dirpath, name)}")
        for name in sorted(filenames):
            if name == ".DS_Store":
                continue
            full = os.path.join(dirpath, name)
            info = os.lstat(full)
            if not stat.S_ISREG(info.st_mode):
                raise VerificationError(f"Not a regular file in EFI: {full}")
            rel = os.path.relpath(full, root).replace(os.sep, "/").encode()
            digest.update(len(rel).to_bytes(4, "big") + rel + info.st_size.to_bytes(8, "big"))
            with open(full, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
    return digest.hexdigest()


class VerifyBuildFrame(wx.Frame):
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None) -> None:
        logging.info("Initializing Build Verify Frame")
        super(VerifyBuildFrame, self).__init__(parent, title=title, size=(600, 700), style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX))
        gui_support.GenerateMenubar(self, global_constants).generate()

        self.constants: constants.Constants = global_constants
        self.title: str = title
        self._closed = False

        self._generate_elements()
        self.Centre()
        self.Bind(wx.EVT_CLOSE, self._on_close)

        threading.Thread(target=self._perform_verification, daemon=True).start()

    # ----- UI ------------------------------------------------------------------

    def _generate_elements(self) -> None:
        self.panel = wx.Panel(self)
        self.sizer = wx.BoxSizer(wx.VERTICAL)

        title_label = wx.StaticText(self.panel, label="Verify Generated Build (TEST-B)")
        title_label.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
        self.sizer.Add(title_label, 0, wx.ALL | wx.CENTER, 10)

        self.status_text = wx.StaticText(self.panel, label="Verifying...")
        self.status_text.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        self.sizer.Add(self.status_text, 0, wx.ALL | wx.CENTER, 10)

        self.info_box = wx.TextCtrl(self.panel, style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_RICH2, size=(550, 450))
        self.info_box.SetFont(wx.Font(12, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.sizer.Add(self.info_box, 0, wx.ALL | wx.CENTER, 10)

        self.return_button = wx.Button(self.panel, label="Return to Main Menu")
        self.return_button.Bind(wx.EVT_BUTTON, self.on_return_to_main_menu)
        self.return_button.Disable()
        self.sizer.Add(self.return_button, 0, wx.ALL | wx.CENTER, 10)

        self.panel.SetSizer(self.sizer)

    def _on_close(self, event):
        self._closed = True
        event.Skip()

    def _ui(self, func, *args):
        """Run func on the main thread, but only if this frame still exists."""
        wx.CallAfter(self._ui_safe, func, *args)

    def _ui_safe(self, func, *args):
        if not self or self._closed:
            return
        func(*args)

    def _log(self, text: str):
        logging.info(text)
        self._ui(self._append_log_safe, text)

    def _append_log_safe(self, text: str):
        self.info_box.AppendText(text + "\n")
        self.info_box.ShowPosition(self.info_box.GetLastPosition())

    # ----- Verification --------------------------------------------------------

    def _perform_verification(self):
        self.failures = []
        self.unasserted = []
        try:
            self._log("Starting verification of generated build...")
            self._verify()
        except VerificationError as e:
            self.failures.append(str(e))
        except Exception as e:
            logging.exception("Unexpected error during build verification")
            self.failures.append(f"Unexpected error: {type(e).__name__}: {e}")

        self._log("=========================================")
        if self.failures:
            self._log("RESULT: FAILED")
            for failure in self.failures:
                self._log(f"  FAIL: {failure}")
            status = f"Verification FAILED ({len(self.failures)} problem(s))."
        elif self.unasserted:
            self._log("RESULT: NO FAILURES, BUT SPEC INCOMPLETE")
            self._log(f"  Not asserted: {', '.join(self.unasserted)}")
            status = "Verification incomplete: TEST-B spec has unasserted items."
        else:
            self._log("RESULT: PASSED")
            status = "Verification PASSED."

        self._ui(self.status_text.SetLabel, status)
        self._ui(self.return_button.Enable)

    def _fail(self, message):
        self.failures.append(message)
        self._log(f"FAIL: {message}")

    def _assert_or_report(self, label, actual, expected):
        if expected is None:
            self.unasserted.append(label)
            self._log(f"{label}: {actual} (UNASSERTED)")
        elif actual != expected:
            self._fail(f"{label} is {actual}, TEST-B requires {expected}")
        else:
            self._log(f"{label}: {actual} (OK)")

    def _verify(self):
        spec = TEST_B_SPEC
        target = _kernel_tuple(spec["target_kernel"])

        # Same path the EFI builder writes to (honours a custom oc_build_path and the
        # per-model folder name), so we never verify a stale or unrelated build.
        build_root = os.path.realpath(str(self.constants.opencore_release_folder))
        self._log(f"Build folder: {build_root}")
        efi_root = os.path.join(build_root, "EFI")
        oc_root = os.path.join(efi_root, "OC")
        if not os.path.isdir(oc_root) or os.path.islink(efi_root) or os.path.islink(oc_root):
            raise VerificationError("Generated EFI/OC folder not found (or is a symlink). Build OpenCore first.")

        # Rejects symlinks anywhere in the tree, so every later path stays inside it.
        tree_sha = _hash_tree(efi_root)

        config_path = os.path.join(oc_root, "config.plist")
        if not os.path.isfile(config_path):
            raise VerificationError("config.plist not found in EFI/OC.")
        with open(config_path, "rb") as handle:
            config_bytes = handle.read()
        config_sha = hashlib.sha256(config_bytes).hexdigest()
        try:
            config = plistlib.loads(config_bytes)
        except (plistlib.InvalidFileException, ValueError, OverflowError) as e:
            raise VerificationError(f"config.plist could not be parsed: {e}")
        if not isinstance(config, dict):
            raise VerificationError("config.plist root is not a dictionary.")

        kernel = _typed(config, "Kernel", dict, {})
        add = _dict_entries(_typed(kernel, "Add", list, []), "Kernel.Add")
        block = _dict_entries(_typed(kernel, "Block", list, []), "Kernel.Block")
        patches = _dict_entries(_typed(kernel, "Patch", list, []), "Kernel.Patch")

        self._log("=========================================")
        self._log(f"Target kernel: {spec['target_kernel']}")

        # --- Kexts: on disk, valid bundle, exactly one active config entry, order ---
        self._log("\nKEXTS:")
        positions = {}
        for name in spec["required_kexts"]:
            index = self._check_kext(oc_root, name, add, target)
            if index is not None:
                positions[name] = index

        lilu = positions.get("Lilu.kext")
        for dependent in spec["lilu_dependents"]:
            if lilu is not None and dependent in positions and positions[dependent] < lilu:
                self._fail(f"{dependent} loads before Lilu.kext")

        if spec["require_skywalk_block"]:
            blocked = any(
                entry.get("Identifier") == "com.apple.iokit.IOSkywalkFamily" and _is_active(entry, target)
                for entry in block
            )
            if blocked:
                self._log("Kernel > Block com.apple.iokit.IOSkywalkFamily: active (OK)")
            else:
                self._fail("No active Kernel > Block for com.apple.iokit.IOSkywalkFamily; the injected IOSkywalkFamily will clash with the system one")

        # --- TEST-B configuration ---
        self._log("\nTEST-B CONFIGURATION:")
        weg_active = any(e.get("BundlePath") == "WhateverGreen.kext" and _is_active(e, target) for e in add)
        self._assert_or_report("WhateverGreen active", weg_active, spec["whatevergreen_enabled"])

        nvram = _typed(config, "NVRAM", dict, {})
        nvram_add = _typed(_typed(nvram, "Add", dict, {}), APPLE_BOOT_GUID, dict, {}) if "Add" in nvram else {}
        boot_args_raw = _typed(nvram_add, "boot-args", str, "")
        tokens = set(boot_args_raw.split())
        self._log(f"boot-args: {boot_args_raw!r}")

        for token in REPORTED_BOOT_ARGS:
            self._log(f"  {token}: {'present' if token in tokens else 'absent'}")
        for token in spec["boot_args_required"]:
            if token not in tokens:
                self._fail(f"boot-args is missing required '{token}'")
        for token in spec["boot_args_forbidden"]:
            if token in tokens:
                self._fail(f"boot-args contains forbidden '{token}'")

        nvram_delete = _typed(_typed(nvram, "Delete", dict, {}), APPLE_BOOT_GUID, list, []) if "Delete" in nvram else []
        if "boot-args" not in nvram_delete:
            self._fail("NVRAM > Delete does not list boot-args; existing boot-args in NVRAM would override the build's value")

        active_patches = [p for p in patches if _is_active(p, target)]
        amd_patches = []
        for patch in active_patches:
            haystack = " ".join(str(patch.get(k, "")) for k in ("Comment", "Identifier", "Base")).lower()
            if any(word in haystack for word in AMD_KEYWORDS):
                amd_patches.append(patch.get("Comment") or patch.get("Identifier") or "<unnamed>")
        self._log(f"Active kernel patches: {len(active_patches)}")
        for patch in active_patches:
            self._log(f"  - {patch.get('Comment') or '<no comment>'} [{patch.get('Identifier', '')}]")
        if spec["allow_amd_patches"] is None:
            self.unasserted.append("AMD patches")
            self._log(f"AMD-looking patches: {', '.join(amd_patches) or 'none'} (UNASSERTED)")
        elif not spec["allow_amd_patches"] and amd_patches:
            self._fail(f"AMD patches are active: {', '.join(amd_patches)}")
        else:
            self._log(f"AMD-looking patches: {', '.join(amd_patches) or 'none'} (OK)")

        # --- Hashes ---
        self._log("\nSHA256:")
        self._log(f"config.plist: {config_sha}")
        self._log(f"EFI tree:     {tree_sha}")
        for label, actual, expected in (
            ("config.plist", config_sha, spec["expected_config_sha256"]),
            ("EFI tree", tree_sha, spec["expected_tree_sha256"]),
        ):
            if expected is None:
                self.unasserted.append(f"{label} hash")
            elif actual.lower() != expected.lower():
                self._fail(f"{label} hash does not match the known-good value")

    def _check_kext(self, oc_root, name, add, target):
        """Validate one kext. Returns its index in Kernel > Add, or None on failure."""
        bundle = os.path.join(oc_root, "Kexts", name)
        info_path = os.path.join(bundle, "Contents", "Info.plist")
        if not os.path.isdir(bundle):
            self._fail(f"{name}: bundle missing from EFI/OC/Kexts")
            return None
        try:
            with open(info_path, "rb") as handle:
                info = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException, ValueError) as e:
            self._fail(f"{name}: Contents/Info.plist unreadable ({e})")
            return None
        if not isinstance(info, dict) or not info.get("CFBundleIdentifier"):
            self._fail(f"{name}: Info.plist has no CFBundleIdentifier")
            return None

        executable = info.get("CFBundleExecutable")
        expected_exec = ""
        if executable:
            expected_exec = f"Contents/MacOS/{executable}"
            exec_path = os.path.join(bundle, "Contents", "MacOS", executable)
            if not os.path.isfile(exec_path) or os.path.getsize(exec_path) == 0:
                self._fail(f"{name}: executable {expected_exec} missing or empty")
                return None

        active = [(i, e) for i, e in enumerate(add) if e.get("BundlePath") == name and _is_active(e, target)]
        if not active:
            self._fail(f"{name}: present on disk but no enabled Kernel > Add entry covers kernel {TEST_B_SPEC['target_kernel']}")
            return None
        if len(active) > 1:
            self._fail(f"{name}: {len(active)} active Kernel > Add entries (duplicate injection)")
            return None

        index, entry = active[0]
        if entry.get("ExecutablePath", "") != expected_exec:
            self._fail(f"{name}: ExecutablePath is {entry.get('ExecutablePath', '')!r}, bundle declares {expected_exec!r}")
            return None
        if entry.get("PlistPath") != "Contents/Info.plist":
            self._fail(f"{name}: PlistPath is {entry.get('PlistPath')!r}")
            return None

        self._log(f"{name}: OK ({info['CFBundleIdentifier']}, {info.get('CFBundleVersion', '?')}, Add[{index}])")
        return index

    # ----- Navigation ----------------------------------------------------------

    def on_return_to_main_menu(self, event):
        self.Hide()
        main_menu_frame = gui_main_menu.MainFrame(
            None,
            title=self.title,
            global_constants=self.constants,
            screen_location=self.GetScreenPosition()
        )
        main_menu_frame.Show()
        self._closed = True
        self.Destroy()
