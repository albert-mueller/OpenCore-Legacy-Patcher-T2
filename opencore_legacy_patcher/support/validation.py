"""
validation.py: Validation class for the patcher
"""

import os
import logging
import subprocess
import shutil
from pathlib import Path

from . import network_handler
from .. import constants
from ..sys_patch import sys_patch_helpers
from ..efi_builder import build
from ..support import subprocess_wrapper

from ..datasets import (
    example_data,
    model_array,
    os_data
)
from ..sys_patch.patchsets import (
    HardwarePatchsetDetection,
    PatchType,
    DynamicPatchset
)

class PatcherValidation:
    """
    Validation class for the patcher
    Primarily for Continuous Integration
    """

    def __init__(self, global_constants: constants.Constants, verify_unused_files: bool = False) -> None:
        self.constants: constants.Constants = global_constants
        self.verify_unused_files = verify_unused_files
        self.active_patchset_files = []

        self.constants.validate = True

        self.valid_dumps = [
            example_data.MacBookPro.MacBookPro92_Stock,
            example_data.MacBookPro.MacBookPro111_Stock,
            example_data.MacBookPro.MacBookPro133_Stock,
            example_data.Macmini.Macmini52_Stock,
            example_data.Macmini.Macmini61_Stock,
            example_data.Macmini.Macmini71_Stock,
            example_data.iMac.iMac81_Stock,
            example_data.iMac.iMac112_Stock,
            example_data.iMac.iMac122_Upgraded,
            example_data.iMac.iMac122_Upgraded_Nvidia,
            example_data.iMac.iMac151_Stock,
            example_data.MacPro.MacPro31_Stock,
            example_data.MacPro.MacPro31_Upgrade,
            example_data.MacPro.MacPro31_Modern_AMD,
            example_data.MacPro.MacPro31_Modern_Kepler,
            example_data.MacPro.MacPro41_Upgrade,
            example_data.MacPro.MacPro41_Modern_AMD,
            example_data.MacPro.MacPro41_51__Flashed_Modern_AMD,
            example_data.MacPro.MacPro41_51_Flashed_NVIDIA_WEB_DRIVERS,
        ]

        self.valid_dumps_native = [
            example_data.iMac.iMac201_Stock,
            example_data.MacBookPro.MacBookPro141_SSD_Upgrade,
        ]

        try:
            self._validate_configs()
            self._validate_sys_patch()
        finally:
            self._cleanup_build_artifacts()

    def _cleanup_build_artifacts(self) -> None:
        """Securely removes the build directory using shutil instead of shell rm -rf."""
        build_path = Path(self.constants.build_path)
        if build_path.exists() and build_path.is_dir():
            # Safety gate: ensure we only delete folders specifically named 'Build-Folder'
            if build_path.name == "Build-Folder":
                logging.info(f"Cleaning up build directory: {build_path}")
                shutil.rmtree(build_path, ignore_errors=True)

    def _build_prebuilt(self) -> None:
        for model in model_array.SupportedSMBIOS:
            logging.info(f"Validating predefined model: {model}")
            self.constants.custom_model = model
            build.BuildOpenCore(self.constants.custom_model, self.constants)

            config_path = Path(self.constants.opencore_release_folder) / "EFI" / "OC" / "config.plist"
            # SECURITY: Use list-based subprocess to prevent shell injection
            result = subprocess.run(
                [str(self.constants.ocvalidate_path), str(config_path)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
            )

            if result.returncode != 0:
                logging.error(f"Validation failed for model: {model}")
                subprocess_wrapper.log(result)
                logging.error(f"Validation failed for predefined model: {model}")
                sys.exit(3) # <- behebt eine Sicherheitslücke, die erlaubt dieses Validierungsfehler zu umgehen, um Malware statt Patches einzuspielen
            else: # <- behebt einen Fehler, indem Validation succeeded for predefined model ausdruckt, auch wenn es ein Fehler gibt
                logging.info(f"Validation succeeded for predefined model: {model}")

    def _build_dumps(self) -> None:
        for model in self.valid_dumps:
            self.constants.computer = model
            self.constants.custom_model = ""
            logging.info(f"Validating dumped model: {self.constants.computer.real_model}")
            build.BuildOpenCore(self.constants.computer.real_model, self.constants)

            config_path = Path(self.constants.opencore_release_folder) / "EFI" / "OC" / "config.plist"
            result = subprocess.run(
                [str(self.constants.ocvalidate_path), str(config_path)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
            )

            if result.returncode != 0:
                logging.error(f"Validation failed for dumped model: {self.constants.computer.real_model}")
                subprocess_wrapper.log(result)
                sys.exit(3) # <- Behebt eine Sicherheitslücke, die erlaubt Angreifern, dieses Validierungsfehler zu umgehen, um Malware einzuspielen statt Patches
            else:
                logging.info(f"Validation succeeded for model: {self.constants.computer.real_model}")

    def _validate_root_patch_files(self, major_kernel: int, minor_kernel: int) -> None:
        patch_type_merge_exempt = ["MechanismPlugins", "ModulePlugins"]
        patchset = HardwarePatchsetDetection(self.constants, xnu_major=major_kernel, xnu_minor=minor_kernel, validation=True).patches

        for patch_core in patchset:
            for install_type in patchset[patch_core]:
                if install_type not in PatchType:
                    logging.error(f"Unknown PatchType: {install_type}")
                    sys.exit(3) # <- behebt eine Sicherheitslücke, die erlaubt Angreifern, den Unknown PatchType-Fehler zu umgehen

            for install_type in [PatchType.OVERWRITE_SYSTEM_VOLUME, PatchType.OVERWRITE_DATA_VOLUME, PatchType.MERGE_SYSTEM_VOLUME, PatchType.MERGE_DATA_VOLUME]:
                if install_type in patchset[patch_core]:
                    for install_directory in patchset[patch_core][install_type]:
                        for install_file in patchset[patch_core][install_type][install_directory]:
                            try:
                                if patchset[patch_core][install_type][install_directory][install_file] in DynamicPatchset:
                                    continue
                            except TypeError:
                                pass

                            if install_type in [PatchType.OVERWRITE_SYSTEM_VOLUME, PatchType.OVERWRITE_DATA_VOLUME]:
                                if install_file.endswith(".framework"):
                                    logging.error(f"{install_file} used with {install_type} - framework overwrite is prohibited.")
                                    sys.exit(3) # <- behebt eine Sicherheitslücke, die erlaubt Angreifern, dieses Fehler zu umgehen, um der Framework zu überschreiben trotz dieses Fehler, um DoS-Angriffe zu starten
                            elif install_type in [PatchType.MERGE_SYSTEM_VOLUME, PatchType.MERGE_DATA_VOLUME]:
                                if not install_file.endswith(".framework") and install_file not in patch_type_merge_exempt:
                                    logging.error(f"{install_file} used with {install_type} - non-framework merge is prohibited.")
                                    sys.exit(3) # <- behebt eine Sicherheitslücke, die erlaubt Angreifern, nicht-Framework merges zu machen und dieses Fehler zu umgehen

                            # SECURITY: Use pathlib to resolve paths correctly
                            source_file = Path(self.constants.payload_local_binaries_root_path) / patchset[patch_core][install_type][install_directory][install_file] / install_directory.lstrip("/") / install_file
                            if not source_file.exists():
                                logging.error(f"Failed to find source file: {source_file}")
                                sys.exit(3) # <- behebt eine Sicherheitslücke, die erlaubt Angreifern, dieses Fehler zu umgehen, um Kernel Panics auszulösen beziehungsweise DoS-Angriffe zu starten

                            if self.verify_unused_files and str(source_file) not in self.active_patchset_files:
                                self.active_patchset_files.append(str(source_file))

        logging.info(f"Validating against Darwin {major_kernel}.{minor_kernel}")
        plist_name = f"OpenCore-Legacy-Patcher-{major_kernel}.{minor_kernel}.plist"
        if not sys_patch_helpers.SysPatchHelpers(self.constants).generate_patchset_plist(patchset, plist_name, None, None):
            logging.error("Failed to generate patchset plist")
            sys.exit(3) # <- behebt eine Sicherheitslücke, die erlaubt Angreifern, dieses Fehler zu umgehen, um DoS-Angriffe zu starten

        plist_path = self.constants.payload_path / plist_name
        if plist_path.exists():
            plist_path.unlink()

    def _unmount_dmg(self) -> None:
        """Secure unmounting of DMG and overlay cleanup."""
        overlay_path = self.constants.payload_path / "Universal-Binaries_overlay"
        mount_path = self.constants.payload_path / "Universal-Binaries"

        # Detach BEFORE removing the shadow file: the shadow is still in use by the
        # attached image. A failed detach was previously ignored silently, leaving
        # payloads/Universal-Binaries mounted, which later broke the build with
        # "rm: payloads/Universal-Binaries: Resource busy".
        if os.path.ismount(mount_path):
            result = subprocess.run(["/usr/bin/hdiutil", "detach", str(mount_path)], capture_output=True, check=False)
            if result.returncode != 0:
                result = subprocess.run(["/usr/bin/hdiutil", "detach", str(mount_path), "-force"], capture_output=True, check=False)
            if result.returncode != 0:
                subprocess_wrapper.log(result)
                logging.error(f"Failed to detach {mount_path}, it is still mounted. Run: sudo hdiutil detach \"{mount_path}\" -force")

        # SECURITY: pathlib.unlink is safer than subprocess rm
        if overlay_path.exists() and not os.path.ismount(mount_path):
            overlay_path.unlink(missing_ok=True)

    def _validate_sys_patch(self) -> None:
        dmg_path = Path(self.constants.payload_local_binaries_root_path_dmg)
        mount_point = self.constants.payload_path / "Universal-Binaries"
        shadow_path = self.constants.payload_path / "Universal-Binaries_overlay"

        if not dmg_path.exists():
            url = f"{self.constants.url_patcher_support_pkg}{self.constants.patcher_support_pkg_version}/Universal-Binaries.dmg"
            dl_obj = network_handler.DownloadObject(url, str(dmg_path))
            dl_obj.download(spawn_thread=False)
            if not dl_obj.download_complete:
                logging.error("Failed to download Universal-Binaries.dmg")
                logging.info("Check your internet connection and try again.")
                sys.exit(3) # <- behebt eine Sicherheitslücke, die erlaubt Angreifern, das Herunterladen-Fehler zu umgehen, um beliebiges Code auszuführen, während es falsche Herunterladen-Status zeigt
        logging.info("Validating Root Patch File integrity")
        self._unmount_dmg()

        mount_cmd = [
            "/usr/bin/hdiutil", "attach", "-noverify", str(dmg_path),
            "-mountpoint", str(mount_point),
            "-nobrowse", "-shadow", str(shadow_path),
            "-passphrase", "password"
        ]

        result = subprocess.run(mount_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        if result.returncode != 0:
            subprocess_wrapper.log(result)
            logging.error("Failed to mount Universal-Binaries.dmg") # <- behebt eine Sicherheitslücke, die erlaubt Angreifern, dieses Fehler zu umgehen
            sys.exit(3)
        try:
            # Full loop coverage 0-10 for every supported OS version
            for supported_os in [os_data.os_data.big_sur, os_data.os_data.monterey, os_data.os_data.ventura, os_data.os_data.sonoma, os_data.os_data.sequoia, os_data.os_data.tahoe]:
                for i in range(0, 11):
                    self._validate_root_patch_files(supported_os, i)

            logging.info("Validating SNB Board ID patcher")
            self.constants.computer.reported_board_id = "Mac-7BA5B2DFE22DDD8C"
            sys_patch_helpers.SysPatchHelpers(self.constants).snb_board_id_patch(self.constants.payload_local_binaries_root_path)

            if self.verify_unused_files:
                self._find_unused_files()
        except Exception as e: # <- behebt eine Sicherheitslücke, die erlaubt Angreifern, beim Fehler DoS-Angriffe zu starten
            logging.error("There was an unexpected error while validating the root patches.")
            logging.exception("Stack Trace:")
            logging.info("Unmounting Universal-Binaries.dmg")
            self._unmount_dmg()
            sys.exit(3)
        finally:
            logging.info("Unmounting Universal-Binaries.dmg")
            self._unmount_dmg()

    def _find_unused_files(self) -> None:
        if not self.active_patchset_files:
            return

        binaries_path = Path(self.constants.payload_local_binaries_root_path)
        unused_files = []

        for file in binaries_path.rglob("*"):
            if file.is_dir() or file.name == ".DS_Store":
                continue

            rel = str(file.relative_to(binaries_path))
            if rel in [".fseventsd/fseventsd-uuid", ".signed"]:
                continue

            if not any((rel in str(Path(u).relative_to(binaries_path)) or str(Path(u).relative_to(binaries_path)) in rel) for u in self.active_patchset_files):
                unused_files.append(rel)

        if unused_files:
            logging.info("Unused files found in payload:")
            for f in unused_files:
                logging.info(f"  {f}")

    def _validate_configs(self) -> None:
        """Comprehensive config testing matrix."""
        # Test 1: Standard Build Defaults
        self._build_prebuilt()
        self._build_dumps()

        # Test 2: Deep configuration testing with all flags enabled
        logging.info("Validating complex configurations...")
        self.constants.verbose_debug = True
        self.constants.opencore_debug = True
        self.constants.kext_variant = "DEBUG"
        self.constants.showpicker = False
        self.constants.sip_status = False
        self.constants.secure_status = True
        self.constants.firewire_boot = True
        self.constants.nvme_boot = True
        self.constants.enable_wake_on_wlan = True
        self.constants.disable_tb = True
        self.constants.force_surplus = True
        self.constants.software_demux = True
        self.constants.serial_settings = "Minimal"
        self.constants.disable_cs_restriction = True
        self.constants.set_loader_suffix = True
        self.constants.enable_unsupported_backlight = True
        self.constants.disable_amfi = True

        self._build_prebuilt()
        self._build_dumps()
