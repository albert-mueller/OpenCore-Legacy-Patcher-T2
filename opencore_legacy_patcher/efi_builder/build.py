"""
build.py: Class for generating OpenCore Configurations tailored for Macs
"""

import copy
import pickle
import shutil
import logging
import zipfile
import plistlib
import logging

from pathlib import Path
from datetime import date

from .. import constants

from ..support import utilities

from .networking import (
wired,
wireless
)
from . import (
bluetooth,
firmware,
graphics_audio,
support,
storage,
smbios,
security,
misc
)

def rmtree_handler(func, path, exc_info) -> None:
    try:
        if exc_info[0] == FileNotFoundError:
            return
        # If it's not a FileNotFoundError, we log the failure to the GUI
        logging.error("Critical: rmtree_handler cannot start cleanup for path!")
        raise 
    except Exception as e:
        logging.error(f"Function Error: {e}")

class BuildOpenCore:
    """
    Core Build Library for generating and validating OpenCore EFI Configurations
    compatible with genuine Macs
    """

def __init__(self, model: str, global_constants: constants.Constants) -> None:
    try:
        self.model: str = model
        self.config: dict = None
        self.constants: constants.Constants = global_constants

        if not hasattr(self.constants, "device_properties"):
            self.constants.device_properties = {}

        self._build_opencore()
    except Exception as e:
        logging.error(f"Function Error: {e}")


def _build_efi(self) -> None:
    """
    Build EFI folder
    """

    utilities.cls()
    logging.info(f"Building Configuration {'for external' if self.constants.custom_model else 'on model'}: {self.model}")

    self._generate_base()
    self._set_revision()

    # Set Lilu and co.
    support.BuildSupport(self.model, self.constants, self.config).enable_kext("Lilu.kext", self.constants.lilu_version, self.constants.lilu_path)
    self.config["Kernel"]["Quirks"]["DisableLinkeditJettison"] = True

    # Patches for unsupported T2 Macs
    # We check if the current model has the T2_CHIP feature flag
    if "T2_CHIP" in self.constants.device_properties.get(self.model, {}).get("Features", []):
        logging.info("- Adding T2-specific bypass kexts (iBridged & CryptexFixup)")
        support.BuildSupport(self.model, self.constants, self.config).enable_kext("iBridged.kext", "1.0.0", self.constants.kext_path)
        support.BuildSupport(self.model, self.constants, self.config).enable_kext("CryptexFixup.kext", "1.1.0", self.constants.kext_path)
        # 1. Helper Kexts
        support.BuildSupport(self.model, self.constants, self.config).enable_kext("iBridged.kext", "1.1.0", self.constants.kext_path)
        support.BuildSupport(self.model, self.constants, self.config).enable_kext("CryptexFixup.kext", "1.1.0", self.constants.kext_path)
        
        # AMFIPass is critical for root patching (GPU drivers) on Tahoe
        support.BuildSupport(self.model, self.constants, self.config).enable_kext("AMFIPass.kext", "1.4.1", self.constants.kext_path)
        
        # Append T2-specific boot args to the existing boot-args string
        t2_args = " -ibtcompatbeta -amfipassbeta"
        self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"] += t2_args
        
        # Ensure VT-d is exposed for iBridged
        self.config["Kernel"]["Quirks"]["DisableIoMapper"] = False

    # macOS Sequoia support for Lilu plugins
    self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"] += " -lilubetaall"

    # Call support functions
    for function in [
        firmware.BuildFirmware,
        wired.BuildWiredNetworking,
        wireless.BuildWirelessNetworking,
        graphics_audio.BuildGraphicsAudio,
        bluetooth.BuildBluetooth,
        storage.BuildStorage,
        smbios.BuildSMBIOS,
        security.BuildSecurity,
        misc.BuildMiscellaneous
    ]:
        function(self.model, self.constants, self.config)

    # Work-around ocvalidate
    if self.constants.validate is False:
        logging.info("- Adding bootmgfw.efi BlessOverride")
        self.config["Misc"]["BlessOverride"] += ["\\EFI\\Microsoft\\Boot\\bootmgfw.efi"]


def _generate_base(self) -> None:
    """
    Generate OpenCore base folder and config
    """

    if not Path(self.constants.build_path).exists():
        logging.info("Creating build folder")
        Path(self.constants.build_path).mkdir()
    else:
        logging.info("Build folder already present, skipping")

    if Path(self.constants.opencore_zip_copied).exists():
        logging.info("Deleting old copy of OpenCore zip")
        Path(self.constants.opencore_zip_copied).unlink()
    if Path(self.constants.opencore_release_folder).exists():
        logging.info("Deleting old copy of OpenCore folder")
        shutil.rmtree(self.constants.opencore_release_folder, onerror=rmtree_handler, ignore_errors=True)

    logging.info("")
    logging.info(f"- Adding OpenCore v{self.constants.opencore_version} {'DEBUG' if self.constants.opencore_debug is True else 'RELEASE'}")
    shutil.copy(self.constants.opencore_zip_source, self.constants.build_path)
    zipfile.ZipFile(self.constants.opencore_zip_copied).extractall(self.constants.build_path)

    # Setup config.plist for editing
    logging.info("- Adding config.plist for OpenCore")
    shutil.copy(self.constants.plist_template, self.constants.oc_folder)
    self.config = plistlib.load(Path(self.constants.plist_path).open("rb"))


def _set_revision(self) -> None:
    """
    Set revision information in config.plist
    """

    self.config["#Revision"]["Build-Version"] = f"{self.constants.patcher_version} - {date.today()}"
    if not self.constants.custom_model:
        self.config["#Revision"]["Build-Type"] = "OpenCore Built on Target Machine"
        computer_copy = copy.copy(self.constants.computer)
        computer_copy.ioregistry = None
        self.config["#Revision"]["Hardware-Probe"] = pickle.dumps(computer_copy)
    else:
        self.config["#Revision"]["Build-Type"] = "OpenCore Built for External Machine"
    self.config["#Revision"]["OpenCore-Version"] = f"{self.constants.opencore_version} - {'DEBUG' if self.constants.opencore_debug is True else 'RELEASE'}"
    self.config["#Revision"]["Original-Model"] = self.model
    self.config["NVRAM"]["Add"]["4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"]["OCLP-Version"] = f"{self.constants.patcher_version}"
    self.config["NVRAM"]["Add"]["4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"]["OCLP-Model"] = self.model


def _save_config(self) -> None:
    """
    Save config.plist to disk
    """

    plistlib.dump(self.config, Path(self.constants.plist_path).open("wb"), sort_keys=True)


def _build_opencore(self) -> None:
    """
    Kick off the build process

    This is the main function:
    - Generates the OpenCore configuration
    - Cleans working directory
    - Signs files
    - Validates generated EFI
    """

    # Generate OpenCore Configuration
    self._build_efi()
    if self.constants.allow_oc_everywhere is False or self.constants.allow_native_spoofs is True or (self.constants.custom_serial_number != "" and self.constants.custom_board_serial_number != ""):
        smbios.BuildSMBIOS(self.model, self.constants, self.config).set_smbios()
    support.BuildSupport(self.model, self.constants, self.config).cleanup()
    self._save_config()

    # Post-build handling
    support.BuildSupport(self.model, self.constants, self.config).sign_files()
    support.BuildSupport(self.model, self.constants, self.config).validate_pathing()

    logging.info("")
    logging.info(f"Your OpenCore EFI for {self.model} has been built at:")
    logging.info(f"    {self.constants.opencore_release_folder}")
    logging.info("")
