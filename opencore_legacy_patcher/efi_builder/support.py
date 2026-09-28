"""
support.py: Utility class for build functions
"""

import shutil
import typing
import logging
import plistlib
import zipfile
import subprocess
import sys

from pathlib import Path

from .. import constants


class BuildSupport:
    """
    Support Library for build.py and related libraries
    """

    def __init__(self, model: str, global_constants: constants.Constants, config: dict) -> None:
        self.model: str = model
        self.config: dict = config
        self.constants: constants.Constants = global_constants


    @staticmethod
    def get_item_by_kv(iterable: dict, key: str, value: typing.Any) -> dict:
        """
        Gets an item from a list of dicts by key and value

        Parameters:
            iterable (list): List of dicts
            key       (str): Key to search for
            value     (any): Value to search for

        """

        item = None
        for i in iterable:
            if i[key] == value:
                item = i
                break
        return item


    def get_kext_by_bundle_path(self, bundle_path: str) -> dict:
        """
        Gets a kext by bundle path

        Parameters:
            bundle_path (str): Relative bundle path of the kext in the EFI folder
        """

        kext: dict = self.get_item_by_kv(self.config["Kernel"]["Add"], "BundlePath", bundle_path)
        if not kext:
            logging.info(f"- Could not find kext {bundle_path}!")
            raise IndexError
        return kext


    def get_efi_binary_by_path(self, bundle_name: str, entry_type: str, efi_type: str) -> dict:
        """
        Gets an EFI binary by name

        Parameters:
            bundle_name (str): Name of the EFI binary
            entry_type  (str): Type of EFI binary (UEFI, Misc)
            efi_type    (str): Type of EFI binary (Drivers, Tools)
        """

        efi_binary: dict = self.get_item_by_kv(self.config[entry_type][efi_type], "Path", bundle_name)
        if not efi_binary:
            logging.info(f"- Could not find {efi_type}: {bundle_name}!")
            raise IndexError
        return efi_binary


    def enable_kext(self, kext_name: str, kext_version: str, kext_path: Path, check: bool = False) -> None:
        """
        Enables a kext in the config.plist, searching payloads/Kexts for the source.
        """
        kext: dict = self.get_kext_by_bundle_path(kext_name)

        if callable(check) and not check():
            return

        if kext["Enabled"] is True:
            return

        # 1. Force the script to look in the actual Payloads folder for the source
        # self.constants.kexts_path usually points to the build destination
        # We need the source folder where the ZIPs and Kexts actually live
        payload_source_dir = self.constants.payload_kexts_path if hasattr(self.constants, 'payload_kexts_path') else kext_path

        # 2. Check for and extract ZIP if needed
        if kext_path and kext_path.exists() and kext_path.name.endswith('.zip'):
            try:
                with zipfile.ZipFile(kext_path, 'r') as zip_ref:
                    zip_ref.extractall(payload_source_dir)
            except Exception as e:
                logging.info(f"- Failed to extract {kext_path.name}: {e}")
        else:
            zip_pattern = f"**/{kext_name.replace('.kext', '')}*.zip"
            potential_zips = list(payload_source_dir.glob(zip_pattern))

            if potential_zips:
                try:
                    with zipfile.ZipFile(potential_zips[0], 'r') as zip_ref:
                        # Extract directly into the payload folder so we can find it
                        zip_ref.extractall(payload_source_dir)
                except Exception as e:
                    logging.info(f"- Failed to extract {potential_zips[0].name}: {e}")

        # 3. Locate the .kext source (searching recursively)
        source_path = payload_source_dir / kext_name
        if not source_path.exists():
            potential_paths = list(payload_source_dir.glob(f"**/{kext_name}"))
            if potential_paths:
                source_path = potential_paths[0]
            else:
                # Last ditch effort: look in the kext_path provided by the caller
                potential_paths = list(kext_path.glob(f"**/{kext_name}"))
                if potential_paths:
                    source_path = potential_paths[0]
                else:
                    logging.info(f"- Failed to find {kext_name} after extraction check.")
                    return

        logging.info(f"- Adding {kext_name} {kext_version}")

        # 4. Destination is ALWAYS the build folder's Kexts directory
        destination_path = self.constants.kexts_path / kext_name

        try:
            if destination_path.exists():
                shutil.rmtree(destination_path) if destination_path.is_dir() else destination_path.unlink()

            shutil.copytree(source_path, destination_path)
            kext["Enabled"] = True
        except Exception as e:
            logging.info(f"- Error injecting {kext_name}: {e}")

    def sign_files(self) -> None:
        """
        Signs files for on OpenCorePkg's Vault system
        """

        if self.constants.vault is False:
            return

        logging.info("- Vaulting EFI\n=========================================")
        popen = subprocess.Popen([str(self.constants.vault_path), f"{self.constants.oc_folder}/"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        for stdout_line in iter(popen.stdout.readline, ""):
            logging.info(stdout_line.strip())
        logging.info("=========================================")

    def validate_pathing(self) -> None:
        """
        Validate whether all files are accounted for on-disk

        This ensures that OpenCore won't hit a critical error and fail to boot
        """

        logging.info("- Validating generated config")
        if not Path(self.constants.opencore_release_folder / Path("EFI/OC/config.plist")):
            logging.info("- OpenCore config file missing!!!")
            raise Exception("OpenCore config file missing")

        config_plist = plistlib.load(Path(self.constants.opencore_release_folder / Path("EFI/OC/config.plist")).open("rb"))

        for acpi in config_plist["ACPI"]["Add"]:
            if not Path(self.constants.opencore_release_folder / Path("EFI/OC/ACPI") / Path(acpi["Path"])).exists():
                logging.info(f"- Missing ACPI Table: {acpi['Path']}")
                raise Exception(f"Missing ACPI Table: {acpi['Path']}")

        for kext in config_plist["Kernel"]["Add"]:
            kext_path = Path(self.constants.opencore_release_folder / Path("EFI/OC/Kexts") / Path(kext["BundlePath"]))
            kext_binary_path = Path(kext_path / Path(kext["ExecutablePath"]))
            kext_plist_path = Path(kext_path / Path(kext["PlistPath"]))
            if not kext_path.exists():
                logging.info(f"- Missing kext: {kext_path}")
                raise Exception(f"Missing {kext_path}")
            if not kext_binary_path.exists():
                logging.info(f"- Missing {kext['BundlePath']}'s binary: {kext_binary_path}")
                raise Exception(f"Missing {kext_binary_path}")
            if not kext_plist_path.exists():
                logging.info(f"- Missing {kext['BundlePath']}'s plist: {kext_plist_path}")
                raise Exception(f"Missing {kext_plist_path}")

        for tool in config_plist["Misc"]["Tools"]:
            if not Path(self.constants.opencore_release_folder / Path("EFI/OC/Tools") / Path(tool["Path"])).exists():
                logging.info(f"- Missing tool: {tool['Path']}")
                raise Exception(f"Missing tool: {tool['Path']}")

        for driver in config_plist["UEFI"]["Drivers"]:
            if not Path(self.constants.opencore_release_folder / Path("EFI/OC/Drivers") / Path(driver["Path"])).exists():
                logging.info(f"- Missing driver: {driver['Path']}")
                raise Exception(f"Missing driver: {driver['Path']}")

        # Validating local files
        # Report if they have no associated config.plist entry (i.e. they're not being used)
        for tool_files in Path(self.constants.opencore_release_folder / Path("EFI/OC/Tools")).glob("*"):
            # Skip macOS Finder/filesystem metadata (e.g. .DS_Store, ._* AppleDouble files) that
            # can appear if the folder was ever browsed in Finder or copied from a non-APFS/HFS+
            # volume; these aren't real OpenCore tools and shouldn't fail validation.
            if tool_files.name.startswith("."):
                continue
            if tool_files.name not in [x["Path"] for x in config_plist["Misc"]["Tools"]]:
                logging.info(f"- Missing tool from config: {tool_files.name}")
                raise Exception(f"Missing tool from config: {tool_files.name}")

        for driver_file in Path(self.constants.opencore_release_folder / Path("EFI/OC/Drivers")).glob("*"):
            # Same rationale as above: ignore macOS metadata files, not real UEFI drivers.
            if driver_file.name.startswith("."):
                continue
            if driver_file.name not in [x["Path"] for x in config_plist["UEFI"]["Drivers"]]:
                logging.info(f"- Found extra driver: {driver_file.name}")
                raise Exception(f"Found extra driver: {driver_file.name}")

        self._validate_malformed_kexts(self.constants.opencore_release_folder / Path("EFI/OC/Kexts"))


    def _validate_malformed_kexts(self, directory: typing.Union[str, Path]) -> None:
        """
        Validate Info.plist and executable pathing for kexts
        """
        for kext_folder in Path(directory).glob("*.kext"):
            if not Path(kext_folder / Path("Contents/Info.plist")).exists():
                continue

            kext_data = plistlib.load(Path(kext_folder / Path("Contents/Info.plist")).open("rb"))
            if "CFBundleExecutable" in kext_data:
                expected_executable = Path(kext_folder / Path("Contents/MacOS") / Path(kext_data["CFBundleExecutable"]))
                if not expected_executable.exists():
                    logging.info(f"- Missing executable for {kext_folder.name}: Contents/MacOS/{expected_executable.name}")
                    raise Exception(f" - Missing executable for {kext_folder.name}: Contents/MacOS/{expected_executable.name}")

            if Path(kext_folder / Path("Contents/PlugIns")).exists():
                self._validate_malformed_kexts(kext_folder / Path("Contents/PlugIns"))


    def cleanup(self) -> None:
        """Clean up files and entries ensuring OpenCore 1.0.7+ Schema Compliance."""

        logging.info("- Cleaning up files and verifying configuration schema integrity")

        # 1. First Pass: Handle Zip Extraction routines BEFORE stripping configurations
        for kext in list(self.constants.kexts_path.rglob("*.zip")):
            try:
                with zipfile.ZipFile(kext) as zip_file:
                    zip_file.extractall(self.constants.kexts_path)
                kext.unlink()
            except Exception as e:
                logging.error(f"Failed handling deferred kext compression: {e}")

        for item in list(self.constants.oc_folder.rglob("*.zip")):
            try:
                with zipfile.ZipFile(item) as zip_file:
                    zip_file.extractall(self.constants.oc_folder)
                item.unlink()
            except Exception as e:
                logging.error(f"Failed handling deferred core firmware decompression: {e}")

        # 2. Schema Normalization & Disabled Entry Pruning
        entries_to_clean = {
            "ACPI":   ["Add", "Delete", "Patch"],
            "Booter": ["Patch"],
            "Kernel": ["Add", "Block", "Force", "Patch"],
            "Misc":   ["Tools"],
            "UEFI":   ["Drivers"],
        }

        for entry in entries_to_clean:
            for sub_entry in entries_to_clean[entry]:
                for item in list(self.config[entry][sub_entry]):
                    if item.get("Enabled") is False:
                        self.config[entry][sub_entry].remove(item)
                    else:
                        # Hard force strict architectural formatting keys for modern OpenCore runtimes
                        if entry == "Kernel" and sub_entry == "Patch":
                            patch_defaults = {
                                "Arch": "x86_64",
                                "Base": "",
                                "Comment": "",
                                "Count": 0,
                                "Enabled": True,
                                "Find": b"",
                                "Identifier": "kernel",
                                "Limit": 0,
                                "Mask": b"",
                                "MaxKernel": "",
                                "MinKernel": "",
                                "Replace": b"",
                                "ReplaceMask": b"",
                                "Skip": 0
                            }
                            for key, value in patch_defaults.items():
                                if key not in item:
                                    item[key] = value

        if not self.constants.recovery_status:
            for i in list(self.constants.build_path.rglob("__MACOSX")):
                if i.is_dir():
                    shutil.rmtree(i)

        # 3. Safely prune unused nested kext plugins
        known_unused_plugins = [
            "AirPortBrcm4331.kext",
            "AirPortAtheros40.kext",
            "AppleAirPortBrcm43224.kext",
            "AirPortBrcm4360_Injector.kext",
            "AirPortBrcmNIC_Injector.kext"
        ]

        kexts_dir = Path(self.constants.opencore_release_folder / "EFI/OC/Kexts")
        if kexts_dir.exists():
            for kext in kexts_dir.glob("*.kext"):
                plugins_dir = kext / "Contents/PlugIns"
                if plugins_dir.exists():
                    for plugin in list(plugins_dir.glob("*.kext")):
                        should_remove = True
                        for enabled_kext in self.config["Kernel"]["Add"]:
                            if enabled_kext["BundlePath"].endswith(plugin.name):
                                should_remove = False
                                break
                        if should_remove:
                            if plugin.name not in known_unused_plugins:
                                logging.warning(f" - Warning: Untracked kernel extension plugin isolated: {plugin.name}")
                            shutil.rmtree(plugin)

        if Path(self.constants.opencore_zip_copied).exists():
            Path(self.constants.opencore_zip_copied).unlink()
