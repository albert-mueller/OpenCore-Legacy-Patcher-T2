"""
detect.py: Detects patches for a given system
"""

import logging
import plistlib
import subprocess
import py_sip_xnu
import sys
import packaging.version

try:
    from enum import StrEnum
except ImportError:
    from enum import Enum
    class StrEnum(str, Enum):
        pass
except Exception as e: # behebt eine Sicherheitslücke, indem Angreifern Fehler außerhalb ImportError auslösen können
    logging.error("CRITICAL ERROR: Imports failed, please reinstall the patcher from GitHub and if this issue persists, report this issue.")
    logging.exception("Stack Trace:")
    sys.exit(3)
from pathlib   import Path
from functools import cache

from .hardware.base import BaseHardware, HardwareVariantGraphicsSubclass

from .hardware.graphics import (
    intel_iron_lake,
    intel_sandy_bridge,
    intel_ivy_bridge,
    intel_haswell,
    intel_broadwell,
    intel_skylake,

    nvidia_tesla,
    nvidia_kepler,
    nvidia_webdriver,

    amd_terascale_1,
    amd_terascale_2,
    amd_legacy_gcn,
    amd_polaris,
    amd_vega,
    amd_navi,
)
from .hardware.networking import (
    legacy_wireless,
    modern_wireless,
)
from .hardware.misc import (
    display_backlight,
    gmux,
    legacy_audio,
    modern_audio,
    pcie_webcam,
    t1_security,
    usb11,
)

from ... import constants

from ...datasets import sip_data
from ...datasets.os_data import os_data
from ...support import (
    global_settings,
    network_handler,
    utilities,
    kdk_handler,
    metallib_handler
)
from ...detections import (
    amfi_detect,
    device_probe
)


class HardwarePatchsetSettings(StrEnum):
    """
    Enum for patch settings
    """
    KERNEL_DEBUG_KIT_REQUIRED     = "Settings: Kernel Debug Kit required"
    KERNEL_DEBUG_KIT_MISSING      = "Settings: Kernel Debug Kit missing"
    METALLIB_SUPPORT_PKG_REQUIRED = "Settings: MetallibSupportPkg.pkg required"
    METALLIB_SUPPORT_PKG_MISSING  = "Settings: MetallibSupportPkg.pkg missing"


class HardwarePatchsetValidation(StrEnum):
    """
    Enum for validation settings
    """
    UNSUPPORTED_HOST_OS           = "Validation: Your operating system version is not supported"
    MISSING_NETWORK_CONNECTION    = "Validation: There is no internet connection. To fix this, connect your Mac to Ethernet, WiFi or USB tethering."
    FILEVAULT_ENABLED             = "Validation: FileVault is enabled. To disable FileVault, go to System Settings > Privacy and security > FileVault and turn that off. Then wait until the data is fully decrypted and try again."
    SIP_ENABLED                   = "Validation: System Integrity Protection is enabled"
    SECURE_BOOT_MODEL_ENABLED     = "Validation: SecureBootModel is enabled"
    AMFI_ENABLED                  = "Validation: AMFI is enabled"
    WHATEVERGREEN_MISSING         = "Validation: WhateverGreen.kext missing"
    FORCE_OPENGL_MISSING          = "Validation: Force OpenGL property missing"
    FORCE_COMPAT_MISSING          = "Validation: Force compat property missing"
    NVDA_DRV_MISSING              = "Validation: nvda_drv(_vrl) variable missing"
    PATCHING_NOT_POSSIBLE         = "Validation: Patching not possible"
    UNPATCHING_NOT_POSSIBLE       = "Validation: Unpatching not possible"
    REPATCHING_NOT_SUPPORTED      = "Validation: Revert first to update or reinstall OCLP patches"
    ROOT_VOLUME_DIRTY             = "Validation: The system volume was modified outside of the patcher. Revert the root patches, restart your Mac and patch again."


# Root volume manifest written by 'sys_patch.py' after every successful root patch.
#
# Only this copy is consulted when deciding whether a revert is required, exactly
# like upstream. It lives on the sealed system volume, so it disappears together
# with the patches (revert, or an OS update replacing the snapshot). Copies on the
# Data volume (ie. '/Library/Application Support/Dortania', written by older fork
# builds) survive both, and treating them as "patches installed" could leave a Mac
# stuck on "Revert only" with nothing left to revert.
ROOT_VOLUME_MANIFEST: str = "/System/Library/CoreServices/OpenCore-Legacy-Patcher.plist"

# Patchsets installed during the network-only first stage of root patching
# ('_handle_missing_network_connection()'). A manifest recording nothing beyond
# these must not block the second stage.
#
# Upstream only lists "Legacy Wireless" and "Modern Wireless Common", which misses
# the "Extended" halves both networking patchsets add on some OS versions - the
# first stage then blocked the second one with "Revert first". Keep in sync with
# the patch keys in 'patchsets/hardware/networking/'.
WIRELESS_PATCHSET_KEYS: set = {
    "Legacy Wireless",
    "Legacy Wireless Extended",
    "Modern Wireless Common",
    "Modern Wireless Extended",
    "Modern Wireless", # Written by older fork builds
}

# Metadata keys written alongside the patchsets.
# Keep in sync with 'SysPatchHelpers.generate_patchset_plist()'.
MANIFEST_METADATA_KEYS: set = {
    "OpenCore Legacy Patcher",
    "PatcherSupportPkg",
    "Time Patched",
    "Commit URL",
    "Kernel Debug Kit Used",
    "Metal Library Used",
    "OS Version",
    "Custom Signature",
    "Disabled Patchsets", # Fork only, see 'DISABLED_PATCHSETS_KEY'
}


def read_root_volume_manifest() -> dict:
    """
    Load the root volume manifest

    Returns an empty dictionary when no readable manifest exists, ie. nothing was
    ever patched - callers must treat that as "nothing installed".
    """
    if not Path(ROOT_VOLUME_MANIFEST).exists():
        return {}
    try:
        manifest = plistlib.load(open(ROOT_VOLUME_MANIFEST, "rb"))
    except Exception as e:
        logging.error(f"Failed to read root patch manifest: {e}")
        return {}
    if not isinstance(manifest, dict):
        logging.error("Root patch manifest is malformed")
        return {}
    return manifest


def manifest_keys_for_patchset(hardware: BaseHardware) -> set[str]:
    """
    Keys a patchset writes into the root volume manifest once installed

    The manifest is keyed by the entries of 'BaseHardware.patches()', not by the
    display name of the patchset ('Miscellaneous: Legacy Audio' installs
    'Legacy Non-GOP' or 'Legacy Realtek'). Falls back to the stripped display name
    for patchsets that expose no patches of their own.
    """
    keys = set(hardware.patches())
    if keys:
        return keys

    name = hardware.name()
    return {name.split(": ")[1] if ": " in name else name}


# Global settings key holding the patchsets the user opted out of (fork only).
#
# Every patchset detected for the host is applied by default. Users can however
# deselect individual ones (see 'wx_gui/gui_sys_patch_display.py'), so a patchset
# that is known to break their machine no longer blocks them from installing all
# the others. Values are the full hardware patchset names ('Graphics: AMD Polaris').
DISABLED_PATCHSETS_KEY: str = "Root Patch: Disabled Patchsets"


def get_disabled_patchsets() -> list[str]:
    """
    Read the patchsets the user opted out of

    Returns an empty list when nothing was ever deselected, or when the stored
    value is unreadable/malformed - patching everything is the safe default.
    """
    stored = global_settings.GlobalEnviromentSettings().read_property(DISABLED_PATCHSETS_KEY)
    if not stored:
        return []

    # Tolerate a comma separated string, ie. a hand-edited settings file
    if isinstance(stored, str):
        stored = stored.split(",")

    if not isinstance(stored, list):
        logging.error(f"Malformed '{DISABLED_PATCHSETS_KEY}' entry in global settings, ignoring")
        return []

    return [str(entry).strip() for entry in stored if str(entry).strip()]


def set_disabled_patchsets(patchsets: list[str]) -> bool:
    """
    Store the patchsets the user opted out of

    Returns:
        bool: Whether the selection reached the settings file, so the caller can
              tell the user when it was dropped.
    """
    cleaned = sorted({str(entry).strip() for entry in patchsets if str(entry).strip()})
    if not cleaned:
        return global_settings.GlobalEnviromentSettings().delete_property(DISABLED_PATCHSETS_KEY)
    return global_settings.GlobalEnviromentSettings().write_property(DISABLED_PATCHSETS_KEY, cleaned)


class HardwarePatchsetDetection:

    def __init__(self, constants: constants.Constants,
                 xnu_major: int = None, xnu_minor:  int = None,
                 os_build:  str = None, os_version: str = None,
                 validation: bool = False, # Whether to run validation checks
                 disabled_patchsets: list[str] = None # Patchsets to skip, None reads the user's selection
                 ) -> None:
        self._constants = constants

        self._xnu_major  = xnu_major  or self._constants.detected_os
        self._xnu_minor  = xnu_minor  or self._constants.detected_os_minor
        self._os_build   = os_build   or self._constants.detected_os_build
        self._os_version = os_version or self._constants.detected_os_version
        self._validation = validation

        # Validation walks every patchset on purpose (file integrity checks), so a
        # user's selection must never narrow it down.
        if validation is True:
            self._disabled_patchsets = []
        elif disabled_patchsets is not None:
            self._disabled_patchsets = list(disabled_patchsets)
        else:
            self._disabled_patchsets = get_disabled_patchsets()

        # Consumed by 'wx_gui/gui_sys_patch_display.py':
        # - every patchset applicable to this host, including deselected ones
        # - the ones the user deselected
        # - the ones that cannot be deselected (BaseHardware.required())
        # - which of them the root volume manifest already records (#387), plus the
        #   manifest's metadata (version, date, commit URL)
        self.available_patchsets:   list[str] = []
        self.disabled_patchsets:    list[str] = []
        self.required_patchsets:    list[str] = []
        self.installed_patchsets:   list[str] = []
        self.uninstalled_patchsets: list[str] = []
        self.manifest_metadata:     dict      = {}

        self._hardware_variants = [
            intel_iron_lake.IntelIronLake,
            intel_sandy_bridge.IntelSandyBridge,
            intel_ivy_bridge.IntelIvyBridge,
            intel_haswell.IntelHaswell,
            intel_broadwell.IntelBroadwell,
            intel_skylake.IntelSkylake,

            nvidia_tesla.NvidiaTesla,
            nvidia_kepler.NvidiaKepler,
            nvidia_webdriver.NvidiaWebDriver,

            amd_terascale_1.AMDTeraScale1,
            amd_terascale_2.AMDTeraScale2,
            amd_legacy_gcn.AMDLegacyGCN,
            amd_polaris.AMDPolaris,
            amd_vega.AMDVega,
            amd_navi.AMDNavi,

            legacy_wireless.LegacyWireless,
            modern_wireless.ModernWireless,

            legacy_audio.LegacyAudio,
            modern_audio.ModernAudio,

            display_backlight.DisplayBacklight,
            gmux.GraphicsMultiplexer,
            pcie_webcam.PCIeFaceTimeCamera,
            t1_security.T1SecurityChip,
            usb11.USB11Controller,
        ]

        self.device_properties = None
        self.patches           = None

        self.can_patch         = False
        self.can_unpatch       = False

        self._detect()


    def _validation_check_unsupported_host_os(self) -> bool:
        """
        Determine if host OS is unsupported
        """
        _min_os = os_data.big_sur.value
        _max_os = os_data.tahoe.value
        if self._dortania_internal_check() is True:
            return False
        if self._xnu_major < _min_os or self._xnu_major > _max_os:
            return True
        return False


    @cache
    def _validation_check_missing_network_connection(self) -> bool:
        """
        Determine if network connection is present
        """
        return network_handler.NetworkUtilities().verify_network_connection() is False


    @cache
    def _validation_check_filevault_is_enabled(self) -> bool:
        """
        Determine if FileVault is enabled
        """
        # macOS 11.0 introduced a FileVault check for root patching
        if self._xnu_major < os_data.big_sur.value:
            return False

        # OpenCore Legacy Patcher exposes whether it patched APFS.kext to allow for FileVault
        nvram = utilities.get_nvram("OCLP-Settings", "4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102", decode=True)
        if nvram:
            if "-allow_fv" in nvram:
                return False

        return "FileVault is Off" not in subprocess.run(["/usr/bin/fdesetup", "status"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode()

    
    def _validation_check_repatching_not_possible(self) -> bool:
        """
        Determine if repatching is not allowed
        """
        oclp_plist = read_root_volume_manifest()
        if not oclp_plist:
            return False

        # Compared against the manifest itself rather than 'computer.oclp_sys_url':
        # device_probe falls back to Data volume copies of the manifest, which must not
        # decide this (see 'ROOT_VOLUME_MANIFEST').
        if oclp_plist.get("Commit URL") != self._constants.commit_info[2]:
            logging.error("Installed patches are from different commit, unpatching is required")
            return True

        # Fork addition (#539): builds run from source record the same (empty) commit
        # URL, so also compare the versions. Merging a newer payload set over an older
        # one leaves files of both on the volume.
        for key, running in [
            ("OpenCore Legacy Patcher", f"v{self._constants.patcher_version}"),
            ("PatcherSupportPkg",       f"v{self._constants.patcher_support_pkg_version}"),
        ]:
            recorded = oclp_plist.get(key)
            if recorded and recorded != running:
                logging.error(f"Installed patches are from a different build ({key} {recorded} vs {running}), unpatching is required")
                return True

        existing_patches = set(oclp_plist) - WIRELESS_PATCHSET_KEYS - MANIFEST_METADATA_KEYS
        if existing_patches:
            logging.error(f"Patch(es) already installed: {', '.join(sorted(existing_patches))}, unpatching is required")
            return True

        return False


    def _validation_check_root_volume_dirty(self) -> bool:
        """
        Determine if the booted system volume was modified by something other than us

        Fork addition (#493): a broken seal without a manifest of ours means the
        volume is in an unknown state, and patching on top of it would seal that
        state into the new snapshot. Revert first.

        Only the booted volume is asked ('diskutil info /'), and only 'Broken'
        counts: 'Sealed: No' (authenticated-root disabled, nothing changed yet) is
        fine. With our own manifest present the seal is always broken, and
        '_validation_check_repatching_not_possible()' decides instead - otherwise
        the network-only first stage would block the second one (#285).
        """
        # macOS 11.0 introduced sealed system volumes
        if self._xnu_major < os_data.big_sur.value:
            return False

        if read_root_volume_manifest():
            return False

        try:
            info = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", "/"], stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout)
        except Exception as e:
            logging.error(f"Failed to read the seal of the system volume: {e}")
            return False

        if "Broken" in str(info.get("Sealed", "")):
            logging.error("The system volume's seal is broken without OpenCore Legacy Patcher patches recorded, unpatching is required")
            return True

        return False


    def _validation_check_system_integrity_protection_enabled(self, configs: list[str]) -> bool:
        """
        Determine if System Integrity Protection is enabled
        """
        if getattr(self._constants, "host_is_vmware_vm", False) is True and getattr(self._constants, "allow_vmware_root_patching", False) is True:
            # Dev/test only: bypass the live SIP validation gate inside VMware VMs so the
            # root-patching command paths can be exercised without real T2 hardware.
            # Requires allow_vmware_root_patching (constants.py, no GUI control, hand-edit
            # only), mirroring the two-flag gate in gui_support.CheckProperties.host_can_build().
            logging.info("Bypassing SIP validation - host detected as VMware VM with allow_vmware_root_patching enabled (test-only, see host_is_vmware_vm)")
            logging.warning("This is designed only for advanced developers to test the syntax of the root patching process.")
            return False
        return utilities.csr_decode(configs)


    def _validation_check_secure_boot_model_enabled(self) -> bool:
        """
        Determine if SecureBootModel is enabled
        """
        return utilities.check_secure_boot_level()


    def _validation_check_amfi_enabled(self, level: amfi_detect.AmfiConfigDetectLevel) -> bool:
        """
        Determine if AMFI is enabled
        """
        if getattr(self._constants, "host_is_vmware_vm", False) is True and getattr(self._constants, "allow_vmware_root_patching", False) is True:
            # Dev/test only, see the SIP bypass above
            logging.info("Bypassing AMFI validation - host detected as VMware VM with allow_vmware_root_patching enabled (test-only, see host_is_vmware_vm)")
            return False
        return not amfi_detect.AmfiConfigurationDetection().check_config(self._override_amfi_level(level))


    def _validation_check_whatevergreen_missing(self) -> bool:
        """
        Determine if WhateverGreen.kext is missing
        """
        return utilities.check_kext_loaded("as.vit9696.WhateverGreen") is False


    @cache
    def _validation_check_force_opengl_missing(self) -> bool:
        """
        Determine if Force OpenGL property is missing
        """
        nv_on = utilities.get_nvram("boot-args", decode=True)
        if nv_on:
            if "ngfxgl=" in nv_on:
                return False
        for gpu in self._constants.computer.gpus:
            if isinstance(gpu, device_probe.NVIDIA):
                if gpu.disable_metal is True:
                    return False
        return True


    def _validation_check_force_compat_missing(self) -> bool:
        """
        Determine if Force compat property is missing
        """
        nv_on = utilities.get_nvram("boot-args", decode=True)
        if nv_on:
            if "ngfxcompat=" in nv_on:
                return False
        for gpu in self._constants.computer.gpus:
            if isinstance(gpu, device_probe.NVIDIA):
                if gpu.force_compatible is True:
                    return False
        return True


    def _validation_check_nvda_drv_missing(self) -> bool:
        """
        Determine if nvda_drv(_vrl) variable is missing
        """
        nv_on = utilities.get_nvram("boot-args", decode=True)
        if nv_on:
            if "nvda_drv_vrl=" in nv_on:
                return False
        nv_on = utilities.get_nvram("nvda_drv")
        if nv_on:
            return False
        return True


    @cache
    def _override_amfi_level(self, level: amfi_detect.AmfiConfigDetectLevel) -> amfi_detect.AmfiConfigDetectLevel:
        """
        Override level required based on whether AMFIPass is loaded
        """
        amfipass_version = utilities.check_kext_loaded("com.dhinakg.AMFIPass")
        if amfipass_version:
            if packaging.version.parse(amfipass_version) >= packaging.version.parse(self._constants.amfipass_compatibility_version):
                # If AMFIPass is loaded, our binaries will work
                return amfi_detect.AmfiConfigDetectLevel.NO_CHECK
        return level


    def _dortania_internal_check(self) -> None:
        """
        Determine whether to unlock Dortania Developer mode
        """
        return Path("~/.dortania_developer").expanduser().exists()


    def _already_has_networking_patches(self) -> bool:
        """
        Check if network patches are already applied
        """
        # Upstream checks "Modern Wireless", a key no patchset writes ("Modern Wireless
        # Common" is the real one), so Macs with modern wireless were offered the
        # network-only stage over and over while offline.
        return bool(set(read_root_volume_manifest()) & WIRELESS_PATCHSET_KEYS)


    def _is_cached_kernel_debug_kit_present(self) -> bool:
        """
        Check if Kernel Debug Kit is present
        """
        return kdk_handler.KernelDebugKitObject(self._constants, self._os_build, self._os_version, passive=True).kdk_already_installed


    def _is_cached_metallib_support_pkg_present(self) -> bool:
        """
        Check if MetallibSupportPkg is present
        """
        return metallib_handler.MetalLibraryObject(self._constants, self._os_build, self._os_version).metallib_already_installed


    def _can_patch(self, requirements: dict, ignore_keys: list[str] = []) -> bool:
        """
        Check if patching is possible
        """
        for key, value in requirements.items():
            if key in ignore_keys:
                continue
            if not key.startswith("Validation:"):
                continue
            if value is True:
                return False
        return True


    def _convert_required_sip_config_to_int(self, configs: list[str]) -> int:
        """
        Convert required SIP configurations to integer
        """
        value = 0
        for config in configs:
            if config in sip_data.system_integrity_protection.csr_values_extended:
                value += sip_data.system_integrity_protection.csr_values_extended[config]["value"]

        return value


    def _strip_incompatible_hardware(self, present_hardware: list[BaseHardware]) -> list[BaseHardware]:
        """
        Strip out incompatible hardware. Priority is given to Metal GPUs (specifically 31001 when applicable)

        Notes:
        - Non-Metal GPUs are stripped out if any Metal GPUs are present
        - Metal 3802 GPUs are stripped out if Metal 31001 GPUs are present on macOS Sequoia or newer
          - Exception is made for "Graphics: AMD Legacy GCN" on Sequoia or newer
          - Special handling is done in amd_legacy_gcn.py
        """
        non_metal_gpu_present   = False
        metal_gpu_present       = False
        metal_3802_gpu_present  = False
        metal_31001_gpu_present = False
        metal_31001_name        = None

        for hardware in present_hardware:
            hardware: BaseHardware
            sub_variant = hardware.hardware_variant_graphics_subclass()
            if sub_variant == HardwareVariantGraphicsSubclass.METAL_31001_GRAPHICS:
                metal_31001_gpu_present = True
                metal_31001_name = hardware.name()
            elif sub_variant == HardwareVariantGraphicsSubclass.METAL_3802_GRAPHICS:
                metal_3802_gpu_present = True
            elif sub_variant == HardwareVariantGraphicsSubclass.NON_METAL_GRAPHICS:
                non_metal_gpu_present = True

        metal_gpu_present = metal_31001_gpu_present or metal_3802_gpu_present

        if metal_gpu_present and non_metal_gpu_present:
            logging.error("Cannot mix Metal and Non-Metal GPUs")
            logging.error("Stripping out Non-Metal GPUs")
            for hardware in list(present_hardware):
                if hardware.hardware_variant_graphics_subclass() == HardwareVariantGraphicsSubclass.NON_METAL_GRAPHICS:
                    logging.info(f"  Stripping out {hardware.name()}")
                    present_hardware.remove(hardware)

        if metal_3802_gpu_present and metal_31001_gpu_present and self._xnu_major >= os_data.sequoia.value:
            if metal_31001_name != "Graphics: AMD Legacy GCN":
                logging.error("Cannot mix Metal 3802 and Metal 31001 GPUs on macOS Sequoia or newer")
                logging.error("Stripping out Metal 3802 GPUs")
                for hardware in list(present_hardware):
                    if hardware.hardware_variant_graphics_subclass() == HardwareVariantGraphicsSubclass.METAL_3802_GRAPHICS:
                        logging.error(f"  Stripping out {hardware.name()}")
                        present_hardware.remove(hardware)

        return present_hardware


    def _is_disabled(self, hardware: BaseHardware) -> bool:
        """
        Whether the user opted out of this patchset

        Patchsets marked as required (BaseHardware.required()) are never skipped, no
        matter what the settings file says (it is hand editable, and the auto patcher
        comes through here as well).
        """
        if hardware.name() not in self._disabled_patchsets:
            return False

        if hardware.required() is True:
            logging.warning(f"Ignoring deselection of required patchset: {hardware.name()}")
            return False

        return True


    def _strip_disabled_hardware(self, present_hardware: list[BaseHardware]) -> list[BaseHardware]:
        """
        Strip out patchsets the user deselected

        Runs after '_strip_incompatible_hardware()' so a deselection can never revive
        hardware that was stripped for being incompatible.
        """
        self.available_patchsets = [hardware.name() for hardware in present_hardware]
        self.required_patchsets  = [hardware.name() for hardware in present_hardware if hardware.required() is True]

        if not self._disabled_patchsets:
            return present_hardware

        remaining_hardware = []
        for hardware in present_hardware:
            hardware: BaseHardware
            if self._is_disabled(hardware) is True:
                logging.info(f"Skipping patchset disabled by user: {hardware.name()}")
                self.disabled_patchsets.append(hardware.name())
                continue
            remaining_hardware.append(hardware)

        return remaining_hardware


    def _classify_installed_patchsets(self, present_hardware: list[BaseHardware]) -> None:
        """
        Split the patchsets detected for this host into installed and uninstalled,
        based on the root volume manifest (#387)

        Purely informational for the Post-Install menu. Whether patching may proceed
        is decided by '_validation_check_repatching_not_possible()' alone.
        """
        manifest = read_root_volume_manifest()
        self.manifest_metadata = {key: value for key, value in manifest.items() if key in MANIFEST_METADATA_KEYS}

        for item in present_hardware:
            item: BaseHardware
            if manifest and manifest_keys_for_patchset(item).issubset(set(manifest)):
                self.installed_patchsets.append(item.name())
            else:
                self.uninstalled_patchsets.append(item.name())


    def _handle_missing_network_connection(self, requirements: dict, device_properties: dict) -> tuple[dict, dict]:
        """
        Sync network connection requirements
        """
        if self._can_patch(requirements, ignore_keys=[HardwarePatchsetValidation.MISSING_NETWORK_CONNECTION]) is False:
            return requirements, device_properties
        logging.info("Network connection missing, checking whether network patches are applicable")
        if self._already_has_networking_patches() is True:
            logging.info("Network patches are already applied, requiring network connection")
            return requirements, device_properties

        if not any([key.startswith("Networking:") for key in device_properties.keys()]):
            logging.info("Network patches are not applicable, requiring network connection")
            return requirements, device_properties

        logging.info("Network patches are applicable, removing other patches")
        for key in list(device_properties.keys()):
            if key.startswith("Networking:"):
                continue
            device_properties.pop(key, None)

        requirements[HardwarePatchsetValidation.MISSING_NETWORK_CONNECTION]  = False
        requirements[HardwarePatchsetSettings.KERNEL_DEBUG_KIT_REQUIRED]     = False
        requirements[HardwarePatchsetSettings.KERNEL_DEBUG_KIT_MISSING]      = False
        requirements[HardwarePatchsetSettings.METALLIB_SUPPORT_PKG_REQUIRED] = False
        requirements[HardwarePatchsetSettings.METALLIB_SUPPORT_PKG_MISSING]  = False

        return requirements, device_properties


    def _handle_sip_breakdown(self, requirements: dict, required_sip_configs: list[str]) -> dict:
        """
        Handle SIP breakdown
        """
        current_sip_status  = hex(py_sip_xnu.SipXnu().get_sip_status().value)
        expected_sip_status = hex(self._convert_required_sip_config_to_int(required_sip_configs))
        sip_string = f"Validation: Booted SIP: {current_sip_status} vs expected: {expected_sip_status}"
        index = list(requirements.keys()).index(HardwarePatchsetValidation.SIP_ENABLED)
        return dict(list(requirements.items())[:index+1] + [(sip_string, True)] + list(requirements.items())[index+1:])


    def _detect(self) -> None:
        """
        Detect patches for a given system
        """
        present_hardware  = []
        device_properties = {}
        patches           = {}

        requires_metallib_support_pkg = False
        missing_metallib_support_pkg  = False
        requires_kernel_debug_kit     = False
        missing_kernel_debug_kit      = False
        requires_network_connection   = False
        has_nvidia_web_drivers        = False
        highest_amfi_level            = amfi_detect.AmfiConfigDetectLevel.NO_CHECK
        required_sip_configs          = []

        # First pass to find all present hardware
        for hardware in self._hardware_variants:
            item: BaseHardware = hardware(
                xnu_major        = self._xnu_major,
                xnu_minor        = self._xnu_minor,
                os_build         = self._os_build,
                global_constants = self._constants
            )
            # During validation, don't skip missing items
            # This is to ensure we can validate all files
            if self._validation is False:
                if item.present() is False:  # Skip if not present
                    continue
                if item.native_os() is True: # Skip if native OS
                    continue
            present_hardware.append(item)

        if self._validation is False:
            present_hardware = self._strip_incompatible_hardware(present_hardware)
            present_hardware = self._strip_disabled_hardware(present_hardware)
            self._classify_installed_patchsets(present_hardware)

        # Second pass to determine requirements
        for item in present_hardware:
            item: BaseHardware
            device_properties[item.name()] = True

            if item.name() == "Graphics: Nvidia Web Drivers":
                has_nvidia_web_drivers = True

            for config in item.required_system_integrity_protection_configurations():
                if config not in required_sip_configs:
                    required_sip_configs.append(config)

            if item.requires_metallib_support_pkg() is True:
                requires_metallib_support_pkg = True
            if item.requires_kernel_debug_kit() is True:
                requires_kernel_debug_kit = True
            if item.required_amfi_level() > highest_amfi_level:
                highest_amfi_level = item.required_amfi_level()

        if self._validation is False:
            if requires_metallib_support_pkg is True:
                missing_metallib_support_pkg = not self._is_cached_metallib_support_pkg_present()
            if requires_kernel_debug_kit is True:
                missing_kernel_debug_kit = not self._is_cached_kernel_debug_kit_present()

        requires_network_connection = missing_metallib_support_pkg or missing_kernel_debug_kit

        requirements = {
            HardwarePatchsetSettings.KERNEL_DEBUG_KIT_REQUIRED:     requires_kernel_debug_kit,
            HardwarePatchsetSettings.KERNEL_DEBUG_KIT_MISSING:      missing_kernel_debug_kit,
            HardwarePatchsetSettings.METALLIB_SUPPORT_PKG_REQUIRED: requires_metallib_support_pkg,
            HardwarePatchsetSettings.METALLIB_SUPPORT_PKG_MISSING:  missing_metallib_support_pkg,

            HardwarePatchsetValidation.UNSUPPORTED_HOST_OS:         self._validation_check_unsupported_host_os(),
            HardwarePatchsetValidation.MISSING_NETWORK_CONNECTION:  self._validation_check_missing_network_connection() if requires_network_connection else False,
            HardwarePatchsetValidation.FILEVAULT_ENABLED:           self._validation_check_filevault_is_enabled(),
            HardwarePatchsetValidation.SIP_ENABLED:                 self._validation_check_system_integrity_protection_enabled(required_sip_configs),
            HardwarePatchsetValidation.SECURE_BOOT_MODEL_ENABLED:   self._validation_check_secure_boot_model_enabled(),
            HardwarePatchsetValidation.AMFI_ENABLED:                self._validation_check_amfi_enabled(highest_amfi_level),
            HardwarePatchsetValidation.REPATCHING_NOT_SUPPORTED:    self._validation_check_repatching_not_possible(),
            HardwarePatchsetValidation.ROOT_VOLUME_DIRTY:           self._validation_check_root_volume_dirty() if self._validation is False else False,
            HardwarePatchsetValidation.WHATEVERGREEN_MISSING:       self._validation_check_whatevergreen_missing() if has_nvidia_web_drivers is True else False,
            HardwarePatchsetValidation.FORCE_OPENGL_MISSING:        self._validation_check_force_opengl_missing()  if has_nvidia_web_drivers is True else False,
            HardwarePatchsetValidation.FORCE_COMPAT_MISSING:        self._validation_check_force_compat_missing()  if has_nvidia_web_drivers is True else False,
            HardwarePatchsetValidation.NVDA_DRV_MISSING:            self._validation_check_nvda_drv_missing()      if has_nvidia_web_drivers is True else False,
        }

        _cant_patch   = False
        _cant_unpatch = requirements[HardwarePatchsetValidation.SIP_ENABLED]

        if self._validation is False:
            if requirements[HardwarePatchsetValidation.SIP_ENABLED] is True:
                requirements = self._handle_sip_breakdown(requirements, required_sip_configs)
            if requirements[HardwarePatchsetValidation.MISSING_NETWORK_CONNECTION] is True:
                requirements, device_properties = self._handle_missing_network_connection(requirements, device_properties)

        # Third pass to sync stripped hardware (ie. '_handle_missing_network_connection()')
        for item in present_hardware:
            item: BaseHardware
            if item.name() not in device_properties:
                continue
            patches.update(item.patches())

        _cant_patch = not self._can_patch(requirements)

        requirements[HardwarePatchsetValidation.PATCHING_NOT_POSSIBLE]   = _cant_patch
        requirements[HardwarePatchsetValidation.UNPATCHING_NOT_POSSIBLE] = _cant_unpatch

        self.can_patch   = not _cant_patch
        self.can_unpatch = not _cant_unpatch

        device_properties.update(requirements)

        self.device_properties = device_properties
        self.patches           = patches


    def detailed_errors(self) -> None:
        """
        Print out detailed errors
        """
        logging.error("- Breakdown:")
        for key, value in self.device_properties.items():
            if not key.startswith("Validation:"):
                continue
            if key in [HardwarePatchsetValidation.PATCHING_NOT_POSSIBLE, HardwarePatchsetValidation.UNPATCHING_NOT_POSSIBLE]:
                continue
            if value is False:
                continue
            logging.error(f"  - {key.replace('Validation: ', '')}")
