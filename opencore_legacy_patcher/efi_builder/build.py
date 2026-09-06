"""
build.py: Class for generating OpenCore Configurations tailored for Macs
"""

import copy
import pickle
import shutil
import logging
import zipfile
import plistlib
import sys
import webbrowser
import subprocess

from pathlib import Path
from datetime import date

from .. import constants

from ..support import utilities
from ..datasets import model_array

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
from ..datasets import (
    os_data,
    smbios_data
)

# von def rmtree_handler(func, path, exc_info) -> None: verabscheiden und zu def rmtree_handler(func, path, exc: BaseException) -> None: wechseln, um Kompabilität mit Python 3.13+ zu verbessern und Python 3.14-Kompabilität zu ermöglichen
def rmtree_handler(func, path, exc: BaseException) -> None:
    try:
        # Python 3.13 passes the bare exception instance instead of a tuple
        if isinstance(exc, FileNotFoundError):
            return
            
        # If it's not a FileNotFoundError, we log the failure to the GUI
        logging.error(f"Critical: rmtree_handler cannot start cleanup for path: {path}")
        logging.exception("Stack Trace:") # This prints the full technical error
        raise exc
        
    except Exception as e:
        logging.error(f"Function Error: {e}")
        logging.exception("Stack Trace:") # This prints the full technical error
        logging.info("Please try again later.")
        sys.exit(3)

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

            # Every builder below reads smbios_data.smbios_dictionary[self.model] directly, in
            # about a dozen places across firmware.py/smbios.py/graphics_audio.py/misc.py. An
            # unknown model therefore surfaces as a bare KeyError from whichever of them happens
            # to run first (in practice firmware.py's _dual_dp_handling()), dozens of frames away
            # from the actual cause. Catch it once, here, with a message that says what to do.
            if self.model not in smbios_data.smbios_dictionary:
                logging.error(f"- Model '{self.model}' is not a Mac model OpenCore Legacy Patcher has SMBIOS data for")
                logging.error("- Cannot build an EFI for it. If this host is a VM or Hackintosh, pick a real target Mac model in Settings first")
                raise ValueError(f"Unsupported build model: {self.model}")

            self._build_opencore()
        except Exception as e:
            logging.error(f"Function Error: {e}")
            logging.exception("Stack Trace:") # This prints the full technical error
            logging.info("Please try again later.")
            sys.exit(3)

    
    def _build_efi(self) -> None:
        """
        Build EFI folder
        """
        logging.info("---OpenCore Legacy Patcher T2 by Albert Müller---")
        try:
            if self.constants.detected_os >= os_data.os_data.golden_gate:
                if not self.constants.custom_model:
                    logging.info("macOS 27 Golden Gate is not available for Intel Macs. Apple Silicon required. Please do not try to upgrade to Golden Gate on Intel Macs.")
                    logging.info("macOS 27 Golden Gate is compiled only for arm64, specifically for Apple Silicon.")
                    webbrowser.open("https://www.apple.com/os/macos/")
                else:
                    logging.info("You're not building OpenCore on your target system that is running macOS 27 Golden Gate.")
            else:
                logging.info("You're not targeting macOS 27 Golden Gate, this is good.")
        except Exception as e:
            logging.error("We couldn't make sure if you are targeting macOS 27 Golden Gate or newer. Skip checking...")
            logging.exception("Stack Trace:")
            pass
                
        utilities.cls()
        logging.info(f"Building Configuration {'for external' if self.constants.custom_model else 'on model'}: {self.model}")

        self._generate_base()
        self._set_revision()

        # Set Lilu and co.
        support.BuildSupport(self.model, self.constants, self.config).enable_kext("Lilu.kext", self.constants.lilu_version, self.constants.lilu_path)
        self.config["Kernel"]["Quirks"]["DisableLinkeditJettison"] = True

        # Intel UHD 630 VMM Stall Fix (2018-2020 Models)
        _T2_UHD630_MODELS = ["MacBookPro15,1", "MacBookPro15,2", "MacBookPro15,3", "MacBookPro15,4", "MacBookPro16,1", "MacBookPro16,3", "MacBookPro16,4", "Macmini8,1", "iMac20,1", "iMac20,2"]
        if self.model in _T2_UHD630_MODELS:
            logging.info(f"- Disabling VMM CPUID for {self.model} to prevent UHD 630 driver stall")
            self.constants.set_vmm_cpuid = False

        # Determine T2 status upfront
        is_t2 = self.model in model_array.T2Macs or "T2_CHIP" in self.constants.device_properties.get(self.model, {}).get("Features", [])

        if is_t2:
            try:
                logging.info("- Applying in-memory T2 booter, kernel, and SMBIOS alignment")
                self.config.setdefault("Booter", {}).setdefault("Quirks", {}).update({
                    "AvoidRuntimeDefrag": False,
                    "ProvideCustomSlide": False,
                    "EnableSafeModeSlide": False,
                    "SetupVirtualMap": False,
                    "RebuildAppleMemoryMap": False,
                    "EnableWriteUnprotector": False,
                    "SyncRuntimePermissions": False,
                    "DevirtualiseMmio": False,
                    "ProtectSecureBoot": True,
                    "ForceBooterSignature": True,
                })
                self.config.setdefault("PlatformInfo", {})["Automatic"] = False
                self.config.setdefault("PlatformInfo", {})["UpdateSMBIOS"] = False
                self.config.setdefault("PlatformInfo", {})["UpdateDataHub"] = False
                self.config.setdefault("PlatformInfo", {})["UpdateNVRAM"] = False
                self.config.setdefault("PlatformInfo", {})["UpdateSMBIOSMode"] = "Custom"
                self.config.setdefault("PlatformInfo", {})["CustomMemory"] = False
                self.config.setdefault("PlatformInfo", {})["UseRawUuidEncoding"] = False
                self.config.setdefault("PlatformInfo", {}).setdefault("Generic", {}).update({
                    "AdviseFeatures": True,
                    "MaxBIOSVersion": True,
                    "SpoofVendor": True,
                    "SystemMemoryStatus": "Auto",
                    "ProcessorType": 0,
                    "SystemProductName": "",
                    "SystemSerialNumber": "",
                    "MLB": "",
                    "SystemUUID": "",
                    "ROM": b"",
                })
                self.config.setdefault("Kernel", {}).setdefault("Quirks", {}).update({
                    "CustomSMBIOSGuid": False,
                    "DisableLinkeditJettison": True,
                    "PanicNoKextDump": True,
                    "DisableIoMapper": False,
                })
                self.config.setdefault("Misc", {}).setdefault("Security", {})["SecureBootModel"] = "Disabled"
                self.config.setdefault("UEFI", {}).setdefault("ProtocolOverrides", {})["DataHub"] = False
                self.config.setdefault("UEFI", {}).setdefault("APFS", {})["EnableJumpstart"] = False

                # Enable booter patches for T2
                support.BuildSupport(self.model, self.constants, self.config).get_item_by_kv(self.config["Booter"]["Patch"], "Comment", "Skip Board ID check")["Enabled"] = True
                support.BuildSupport(self.model, self.constants, self.config).get_item_by_kv(self.config["Booter"]["Patch"], "Comment", "Patch SkipLogo")["Enabled"] = True

                logging.info("- Adding T2-specific bypass NVRAM variables")
                
                if "NVRAM" not in self.config:
                    self.config["NVRAM"] = {"Add": {}, "Delete": {}}
                if "Delete" not in self.config["NVRAM"]:
                    self.config["NVRAM"]["Delete"] = {}

                if "7C436110-AB2A-4BBB-A880-FE41995C9F82" not in self.config["NVRAM"]["Add"]:
                    self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"] = {"boot-args": ""}

                # Ensure we strictly clean out legacy variables from NVRAM to prevent corecrypto mismatch
                if "7C436110-AB2A-4BBB-A880-FE41995C9F82" not in self.config["NVRAM"]["Delete"]:
                    self.config["NVRAM"]["Delete"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"] = []
                
                for target_arg in ["boot-args", "csr-active-config", "amfi-allow-arguments"]:
                    if target_arg not in self.config["NVRAM"]["Delete"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]:
                        self.config["NVRAM"]["Delete"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"].append(target_arg)

                # Ensure OCLP tracking deletion list
                if "4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102" not in self.config["NVRAM"]["Delete"]:
                    self.config["NVRAM"]["Delete"]["4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"] = []
                for del_key in ["OCLP-Version", "OCLP-Model", "OCLP-Settings", "OCLP-Spoofed-SN", "OCLP-Spoofed-MLB", "revcpu", "revcpuname", "revblock", "revpatch"]:
                    if del_key not in self.config["NVRAM"]["Delete"]["4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"]:
                        self.config["NVRAM"]["Delete"]["4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"].append(del_key)

                # Fetch template boot-args, scrub any accidental Lilu flags inherited from template plists
                raw_args = self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"].get("boot-args", "")
                scrubbed_args = " ".join([arg for arg in raw_args.split() if not arg.startswith("-lilu")])
                
                # Append required T2 args safely without compounding spaces
                t2_args = "-ibtcompatbeta -amfipassbeta"
                self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"] = f"{scrubbed_args} {t2_args}".strip()
                
                # Ensure WriteFlash is enabled to commit changes to SPI ROM
                self.config["NVRAM"]["WriteFlash"] = True

            except Exception as e:
                logging.error("Whoops, applying in-memory T2 booter and SMBIOS alignments failed because of the following error:")
                logging.exception("Stack Trace:")
                logging.info("Please try again later.")
                sys.exit(3)
        else:
            # For Non-T2 Legacy Hardware
            if "NVRAM" not in self.config:
                self.config["NVRAM"] = {"Add": {}}
            if "7C436110-AB2A-4BBB-A880-FE41995C9F82" not in self.config["NVRAM"]["Add"]:
                self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"] = {"boot-args": ""}
                
            current_boot_args = self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"]
            
            # Target some 2017 Mac models specifically to bypass vt-d/broadcom complications
            # Dies ist benötigt, um WLAN und Bluetooth richtig zu funktionieren auf macOS 26 Tahoe.
            MODELS_NEED_DART = ["iMac18,1", "iMac18,2", "iMac18,3", "MacBookPro14,1", "MacBookPro14,2", "MacBookPro14,3", "MacBookAir6,2"]
            if self.model in MODELS_NEED_DART:
                if "dart=0" not in current_boot_args:
                    logging.info(f"- Appending dart=0 boot argument for {self.model} hardware target to fix WiFi/Bluetooth issues on macOS Tahoe ({self.model})")
                    current_boot_args = f"{current_boot_args} dart=0".strip()

            if "-lilubetaall" not in current_boot_args:
                current_boot_args = f"{current_boot_args} -lilubetaall".strip()
                
            self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"] = current_boot_args

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
            try:
                function(self.model, self.constants, self.config)
            except Exception as e:
                logging.error("There is a serious error")
                logging.exception(f"Failed to initialize the function called {function.__name__}")
                logging.exception("Stack Trace:")
                sys.exit(3)

        # Work-around ocvalidate
        # Auch behebt einen Fehler, indem Windows 10/11 per Boot Camp-Installation verschwindet wegen zu viele Malen \EFI\Microsoft\Boot\bootmgfw.efi erstellt werden oder das \EFI\Microsoft\Boot\bootmgfw.efi erstellen in config.plist, auch wenn es schon da steht.
        if self.constants.validate is False:
            logging.info("- Adding bootmgfw.efi BlessOverride")
            
            # Ensure the section exists
            if "BlessOverride" not in self.config["Misc"]:
                self.config["Misc"]["BlessOverride"] = []
                
            # FIX: Only append if it's not already there
            target_path = "\\EFI\\Microsoft\\Boot\\bootmgfw.efi"
            if target_path not in self.config["Misc"]["BlessOverride"]:
                self.config["Misc"]["BlessOverride"].append(target_path)    

    
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
            try:
                shutil.rmtree(self.constants.opencore_release_folder, onexc=rmtree_handler)
            except TypeError:
                shutil.rmtree(self.constants.opencore_release_folder, ignore_errors=True)

        logging.info("")
        logging.info(f"- Adding OpenCore v{self.constants.opencore_version} {'DEBUG' if self.constants.opencore_debug is True else 'RELEASE'}")

        # The payload zip always carries a top-level folder called "OpenCore-Build"
        # (payloads/OpenCore/Update-OpenCore.command renames it before zipping), so
        # extraction lands there regardless of what we want the folder to be called.
        # Clear any leftover staging folder from an interrupted run before extracting,
        # then rename it to the model specific name.
        staging_folder = Path(self.constants.build_path) / Path("OpenCore-Build")
        if staging_folder != Path(self.constants.opencore_release_folder) and staging_folder.exists():
            logging.info("Deleting stale OpenCore staging folder")
            try:
                shutil.rmtree(staging_folder, onexc=rmtree_handler)
            except TypeError:
                shutil.rmtree(staging_folder, ignore_errors=True)

        shutil.copy(self.constants.opencore_zip_source, self.constants.build_path)
        zipfile.ZipFile(self.constants.opencore_zip_copied).extractall(self.constants.build_path)

        if staging_folder != Path(self.constants.opencore_release_folder):
            staging_folder.rename(self.constants.opencore_release_folder)

        # Setup config.plist for editing
        logging.info("- Adding config.plist for OpenCore")
        shutil.copy(self.constants.plist_template, self.constants.oc_folder)
        self.config = plistlib.load(Path(self.constants.plist_path).open("rb"))

    def _save_config(self) -> None:
        """
        Save config.plist to disk with structural validation to prevent
        plistlib type errors.
        """
        
        def find_bad_key(obj, path="root"):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if not isinstance(k, str):
                        # This log entry will pinpoint exactly where the corruption is
                        logging.error(f"!!! NON-STRING KEY FOUND !!!")
                        logging.error(f"    Location: {path}")
                        logging.error(f"    Offending Key: {k} (Type: {type(k)})")
                    find_bad_key(v, f"{path}/{k}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    find_bad_key(item, f"{path}[{i}]")

        # Run the diagnostic scan before attempting to save
        find_bad_key(self.config)

        # Proceed to save
        try:
            # Ensure the directory exists
            Path(self.constants.plist_path).parent.mkdir(parents=True, exist_ok=True)
            
            with Path(self.constants.plist_path).open("wb") as f:
                plistlib.dump(self.config, f, sort_keys=True)
            logging.info("Successfully saved config.plist")
            
        except Exception as e:
            logging.error(f"Function Error while saving config: {e}")
            logging.exception("Stack Trace:")
            # Use sys.exit if you want to stop the build on failure
            sys.exit(3)    
    
    def _set_revision(self) -> None:
        """
        Set revision information in config.plist
        """
    
        # --- Safe access to #Revision ---
        rev = self.config.setdefault("#Revision", {})
        rev["Build-Version"] = f"{self.constants.patcher_version} - {date.today()}"
    
        if not self.constants.custom_model:
            rev["Build-Type"] = "OpenCore Built on Target Machine"
            computer_copy = copy.copy(self.constants.computer)
            computer_copy.ioregistry = None
            
            # FIX: Convert the binary pickle dump to a string representation 
            # so plistlib doesn't try to parse it as an active data structure.
            rev["Hardware-Probe"] = str(pickle.dumps(computer_copy))
        else:
            rev["Build-Type"] = "OpenCore Built for External Machine"
    
        rev["OpenCore-Version"] = (
            f"{self.constants.opencore_version} - "
            f"{'DEBUG' if self.constants.opencore_debug else 'RELEASE'}"
        )
        rev["Original-Model"] = self.model
    
        # --- Hardened NVRAM structure ---
        nvram = self.config.setdefault("NVRAM", {})
        add   = nvram.setdefault("Add", {})
    
        guid_key = "4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"
        guid     = add.setdefault(guid_key, {})
    
        # Validate type to avoid malicious plist poisoning
        if not isinstance(guid, dict):
            logging.error(f"NVRAM GUID {guid_key} is not a dictionary — refusing to write metadata")
            logging.exception("Stack Trace:") 
            return
    
        # --- Safe writes ---
        guid["OCLP-Version"] = f"{self.constants.patcher_version}"
        guid["OCLP-Model"]   = self.model

    
    
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
        try:
            logging.info(f"Generating OpenCore configuration for {self.model} ...")
            if self.model == "MacBookPro14,3" or (self.constants.computer is not None and getattr(self.constants.computer, "real_model", None) == "MacBookPro14,3"):
                if self.constants.build_profile == "test_b":
                    profile_name = "TEST-B GPU"
                elif self.constants.build_profile == "test_c":
                    profile_name = "TEST-C TAHOE / ALBERT"
                elif self.constants.build_profile == "test_c_spoofed":
                    profile_name = "TEST-C SPOOFED / ALBERT"
                elif self.constants.build_profile == "test_d":
                    profile_name = "TEST-D ALL-IN-ONE (Wi-Fi + Audio + GPU + T1)"
                else:
                    profile_name = "STANDARD / SAFE"
                
                logging.info(f"MacBookPro14,3 / T1 detected")
                logging.info(f"")
                logging.info(f"TEST PROFILE")
                logging.info(f"Profile: {profile_name}")
                logging.info(f"T1: ENABLED")
                logging.info(f"")
                logging.info(f"Wi-Fi:")
                if getattr(self.constants, "computer", None) is not None and self.constants.computer.wifi:
                    from opencore_legacy_patcher.support import utilities
                    vendor_id = utilities.friendly_hex(self.constants.computer.wifi.vendor_id).upper()
                    device_id = utilities.friendly_hex(self.constants.computer.wifi.device_id).upper()
                    logging.info(f"Found Wireless Device {vendor_id}:{device_id}")
                else:
                    logging.info(f"Found Wireless Device 14E4:43BA")
                logging.info(f"")
                logging.info(f"GPU:")
                logging.info(f"Found Intel Kaby Lake")
                logging.info(f"Found AMD Polaris")

            if self.constants.build_profile == "test_c_spoofed":
                logging.info("Profile TEST-C SPOOFED: Forcing SMBIOS spoofing to MacBookPro16,1")
                self.constants.custom_model = "MacBookPro16,1"
                self.constants.serial_settings = "Moderate"

            self._build_efi()
        except Exception as e:
            logging.error(f"Whoops, Generating OpenCore configuration for {self.model} because of the following error:")
            logging.exception("Stack Trace:") # This prints the full technical error
            logging.info("Please try again later.")
            sys.exit(3)
        try:
            if self.constants.build_profile in ["test_c", "test_d"]:
                logging.info(f"Profile {self.constants.build_profile.upper()}: Skipping SMBIOS spoofing (Original SMBIOS preserved).")
            elif self.constants.build_profile == "test_c_spoofed":
                smbios.BuildSMBIOS(self.model, self.constants, self.config).set_smbios()
            elif self.constants.allow_oc_everywhere is False or self.constants.allow_native_spoofs is True or (self.constants.custom_serial_number != "" and self.constants.custom_board_serial_number != ""):
                smbios.BuildSMBIOS(self.model, self.constants, self.config).set_smbios()
            
            # Tahoe Base Boot-args injection
            if self.constants.build_profile in ["standard", "test_c", "test_c_spoofed", "test_d"] or self.model == "MacBookPro14,3":
                logging.info("Profile TEST: Injecting Tahoe boot-args (cryptex=0).")
                current_boot_args = self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"].get("boot-args", "")
                if "cryptex=0" not in current_boot_args:
                    self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"] = f"{current_boot_args} cryptex=0".strip()

            # TEST-B / TEST-D GPU Profile Enhancements
            if self.constants.build_profile in ["test_b", "test_d"]:
                current_boot_args = self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"].get("boot-args", "")
                if any(self.model.startswith(prefix) for prefix in ["MacBookPro11,", "MacBookPro12,", "iMac14,", "iMac15,", "Macmini7,"]):
                    logging.info(f"Profile {self.constants.build_profile.upper()}: Injecting Haswell/Broadwell GPU boot-args (-disablegfxfirmware igfxmetal=1 watchdog=0 ipc_control_port_options=0).")
                    haswell_args = ["-disablegfxfirmware", "igfxmetal=1", "watchdog=0", "ipc_control_port_options=0", "-amfipassbeta"]
                    for arg in haswell_args:
                        prefix = arg.split('=')[0]
                        if prefix not in current_boot_args:
                            current_boot_args = f"{current_boot_args} {arg}".strip()
                    self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"] = current_boot_args

            # TEST-D ALL-IN-ONE Boot-args and Kext injection
            if self.constants.build_profile == "test_d":
                logging.info("Profile TEST-D: Injecting Wi-Fi, Audio & System boot-args (ipc_control_port_options=0 -amfipassbeta alcid=13).")

                test_d_args = ["ipc_control_port_options=0", "-amfipassbeta", "-lilubetaall"]
                if self.model in ["MacBookPro13,2", "MacBookPro13,3", "MacBookPro14,2", "MacBookPro14,3"]:
                    test_d_args.append("alcid=13")
                for arg in test_d_args:
                    prefix = arg.split('=')[0]
                    if prefix not in current_boot_args:
                        current_boot_args = f"{current_boot_args} {arg}".strip()
                # Clean out any leftover amfi=0x80 to ensure Apple Account & entitlements are functional
                cleaned = [a for a in current_boot_args.split() if a != "amfi=0x80"]
                self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"] = " ".join(cleaned)
                # Force Wi-Fi kexts and block
                # behebt eine Sicherheitslücke, die erlaubt Angreifern, einfach das Injizieren von diesen Kexts zu überspringen, um DoS-Angriffe zu starten
                try:
                    logging.info("Injecting WiFi kexts and blocks")
                    support.BuildSupport(self.model, self.constants, self.config).enable_kext("IOSkywalkFamily.kext", self.constants.ioskywalk_version, self.constants.ioskywalk_path)
                    support.BuildSupport(self.model, self.constants, self.config).enable_kext("IO80211FamilyLegacy.kext", self.constants.io80211legacy_version, self.constants.io80211legacy_path)
                    support.BuildSupport(self.model, self.constants, self.config).get_kext_by_bundle_path("IO80211FamilyLegacy.kext/Contents/PlugIns/AirPortBrcmNIC.kext")["Enabled"] = True
                    support.BuildSupport(self.model, self.constants, self.config).get_item_by_kv(self.config["Kernel"]["Block"], "Identifier", "com.apple.iokit.IOSkywalkFamily")["Enabled"] = True
                    support.BuildSupport(self.model, self.constants, self.config).enable_kext("AirportBrcmFixup.kext", self.constants.airportbcrmfixup_version, self.constants.airportbcrmfixup_path)
                except Exception as e:
                    logging.error("Injecting WiFi drivers has failed due to the following error:")
                    logging.exception("Stack Trace:")
                    sys.exit(3)
                try:
                    logging.info("Injecting the sound kext")
                    support.BuildSupport(self.model, self.constants, self.config).enable_kext("AppleALC.kext", self.constants.applealc_version, self.constants.applealc_path)
                except Exception as e:
                    logging.error("Failed to inject the sound kext - it is necessary so you can have any sound on your machine.")
                    logging.exception("Stack Trace:")
                    sys.exit(3)

            # MacBookPro14,3-specific boot-args
            real_model = getattr(self.constants.computer, 'real_model', self.model) if hasattr(self.constants, 'computer') else self.model
            if real_model == "MacBookPro14,3" or self.model == "MacBookPro14,3":
                current_boot_args = self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"]
                
                # Rimuovi args di debug inutili
                cleaned = [a for a in current_boot_args.split() if a not in ["debug=0x100", "keepsyms=1"]]
                current_boot_args = " ".join(cleaned)
                
                extra_args = []
                # dart=0: Disables IOMMU/VT-d to prevent peripheral mapping issues
                if "dart=0" not in current_boot_args:
                    extra_args.append("dart=0")
                # alcid=13: Necessario per audio jack su MBP14,3 T1
                if "alcid=" not in current_boot_args:
                    extra_args.append("alcid=13")
                # -nokcmismatchpanic: Previene kernel panic al boot
                if "-nokcmismatchpanic" not in current_boot_args:
                    extra_args.append("-nokcmismatchpanic")
                # agdpmod=pikera: Fix display policy / black screen
                if "agdpmod=" not in current_boot_args:
                    extra_args.append("agdpmod=pikera")
                
                # --- GPU / Performance boot-args (EFI-level only, no macOS modifications) ---
                #
                # igfxfw=2       → Force-load Apple GuC firmware on Intel HD 630.
                #                  Hands off GPU scheduling to the firmware. Biggest single
                #                  improvement for rendering smoothness on Tahoe.
                # igfxonln=1     → Keep all Intel display ports "online".
                #                  Prevents the GPU from partially powering down outputs,
                #                  which causes micro-stutters on external displays.
                # -igfxnotelemetry → Disable Intel GPU telemetry collection.
                #                  Small but measurable reduction in GPU interrupt overhead.
                # radpg=15       → Disable all Radeon power-gating states on AMD Radeon Pro 555/560.
                #                  Both GPUs use the AMD Polaris 21 architecture (GCN 4th gen).
                #                  Prevents the dGPU from aggressively clock-gating, which
                #                  causes visible frame drops when switching between idle/active.
                # watchdog=0     → Disable the macOS watchdog timer.
                #                  Prevents unexpected reboots/hangs on Tahoe during heavy
                #                  GPU workloads or long compile jobs.
                # ipc_control_port_options=0 → Relax IPC port security checks.
                #                  Required on Tahoe to allow LaunchServices and Spotlight
                #                  to function correctly on non-supported hardware.
                perf_args = {
                    "igfxfw=2":                   "igfxfw=",
                    "igfxonln=1":                 "igfxonln=",
                    "-igfxnotelemetry":            "-igfxnotelemetry",
                    "radpg=15":                   "radpg=",
                    "watchdog=0":                 "watchdog=",
                    "ipc_control_port_options=0": "ipc_control_port_options=",
                }
                # First, apply the essential args (dart, alcid, etc.)
                new_args = current_boot_args
                if extra_args:
                    new_args = f"{current_boot_args} {' '.join(extra_args)}".strip()
                # Then, stack the performance args on top
                for arg, prefix in perf_args.items():
                    if prefix not in new_args:
                        new_args = f"{new_args} {arg}".strip()
                        logging.info(f"  + Perf: {arg}")
                
                self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"] = new_args
                logging.info(f"- MacBookPro14,3: Optimized boot-args -> {new_args}")

            support.BuildSupport(self.model, self.constants, self.config).cleanup()
            self._save_config()
        except Exception as e:
            logging.error(f"Whoops, spoofing the SMBIOS for {self.model} failed because of the following error:")
            logging.exception("Stack Trace:")
            logging.info("Please try again later.")
            sys.exit(3)

        # Post-build handling
        try:
            logging.info("Post-build handling")
            support.BuildSupport(self.model, self.constants, self.config).sign_files()
            support.BuildSupport(self.model, self.constants, self.config).validate_pathing()
            
            is_test_profile = getattr(self.constants, "build_profile", "standard") != "standard"
            is_mbp143 = self.model == "MacBookPro14,3" or (self.constants.computer is not None and getattr(self.constants.computer, "real_model", None) == "MacBookPro14,3")

            if is_test_profile or is_mbp143:
                if self.constants.build_profile == "test_b":
                    profile_name = "TEST-B GPU"
                elif self.constants.build_profile == "test_c":
                    profile_name = "TEST-C TAHOE / ALBERT"
                elif self.constants.build_profile == "test_c_spoofed":
                    profile_name = "TEST-C SPOOFED / ALBERT"
                elif self.constants.build_profile == "test_d":
                    profile_name = "TEST-D ALL-IN-ONE (Wi-Fi + Audio + GPU + T1 Native Auth)"
                else:
                    profile_name = "STANDARD / SAFE"
                
                logging.info("")
                logging.info("=========================================")
                logging.info("          BUILD REPORT                   ")
                logging.info("=========================================")
                logging.info(f"Model: {self.model}")
                logging.info(f"Profile: {profile_name}")
                is_t1_model = self.model in ["MacBookPro13,2", "MacBookPro13,3", "MacBookPro14,2", "MacBookPro14,3"]
                t1_info = "NATIVE SOFTWARE KEYSTORE (TAHOE COMPATIBLE)" if is_t1_model else "NOT APPLICABLE (Non-T1 Hardware)"
                logging.info(f"T1 Auth: {t1_info}")
                
                wifi_id = "14E4:43BA"
                if getattr(self.constants, "computer", None) is not None and self.constants.computer.wifi:
                    from opencore_legacy_patcher.support import utilities
                    wifi_id = f"{utilities.friendly_hex(self.constants.computer.wifi.vendor_id).upper()}:{utilities.friendly_hex(self.constants.computer.wifi.device_id).upper()}"
                logging.info(f"Wi-Fi: {wifi_id}")
                
                # Read ACTUAL config.plist state — do not trust log alone
                weg_enabled = False
                for kext in self.config.get("Kernel", {}).get("Add", []):
                    if kext.get("BundlePath") == "WhateverGreen.kext":
                        weg_enabled = kext.get("Enabled", False)
                        break
                boot_args = self.config["NVRAM"]["Add"]["7C436110-AB2A-4BBB-A880-FE41995C9F82"]["boot-args"]
                wegnoegpu_enabled = "-wegnoegpu" in boot_args
                amd_patches = [patch for patch in self.config["Kernel"]["Patch"] if "AMD" in patch.get("Comment", "")]
                dart_enabled = "dart=0" in boot_args
                
                if self.constants.build_profile == "test_b":
                    logging.info(f"WhateverGreen: {'ENABLED' if weg_enabled else 'ERROR — EXPECTED ENABLED'}")
                    logging.info(f"WhateverGreen version: {self.constants.whatevergreen_version}")
                    logging.info(f"-wegnoegpu: {'ENABLED' if wegnoegpu_enabled else 'ERROR — EXPECTED ENABLED'}")
                elif self.constants.build_profile in ["test_c", "test_c_spoofed"]:
                    logging.info(f"WhateverGreen: {'ENABLED' if weg_enabled else 'NOT ENABLED'}")
                    logging.info(f"-wegnoegpu: {'ENABLED' if wegnoegpu_enabled else 'NOT ENABLED'}")
                else:
                    logging.info(f"WhateverGreen: {'NOT ENABLED BY TEST-B' if not weg_enabled else 'WARNING — UNEXPECTEDLY ENABLED'}")
                    logging.info(f"-wegnoegpu: {'NOT ENABLED BY TEST-B' if not wegnoegpu_enabled else 'WARNING — UNEXPECTEDLY ENABLED'}")
                
                logging.info(f"AMD kernel patches: {len(amd_patches) if amd_patches else 'NONE'}")
                logging.info(f"dart=0: {'ENABLED' if dart_enabled else 'NOT ENABLED'}")
                logging.info(f"boot-args: {boot_args}")
                logging.info("=========================================")

            # Create profile-specific output directory
            if is_test_profile or is_mbp143:
                if self.constants.build_profile == "test_b":
                    profile_dir_base = "TEST-B-Build"
                elif self.constants.build_profile == "test_c":
                    profile_dir_base = "TEST-C-TAHOE-ALBERT"
                elif self.constants.build_profile == "test_c_spoofed":
                    profile_dir_base = "TEST-C-SPOOFED"
                elif self.constants.build_profile == "test_d":
                    profile_dir_base = "TEST-D-ALL-IN-ONE"
                else:
                    profile_dir_base = "Standard-Build"
                
                if self.model == "MacBookPro14,3":
                    profile_dir_name = profile_dir_base
                else:
                    profile_dir_name = f"{profile_dir_base}-{self.model}"
                
                profile_output = Path(self.constants.build_path) / profile_dir_name
                if profile_output.exists():
                    try:
                        shutil.rmtree(profile_output, onexc=rmtree_handler)
                    except TypeError:
                        shutil.rmtree(profile_output, ignore_errors=True)
                    # behebt eine Sicherheitslücke, die beim Löschen des Verzeichnisses, beim einen unerwarteter Fehler (z.B einen Angreifer versucht, nicht autorisierte Verzeichnisse zu löschen), DoS-Angriffe zu starten
                    except Exception as e:
                        logging.error("While deleting a directory, an error occured.")
                        logging.exception("Stack Trace:")
                        logging.info("This could be because an attacker may have tried to delete an unauthorized directory. Please check the full Stack Trace carefully and ensure the application hasn't been tampered with malware. If you are not sure, contact the main developers immediately and tell from which source the app was downloaded.")
                profile_output.mkdir(parents=True)
                efi_source = Path(self.constants.opencore_release_folder) / "EFI"
                if efi_source.exists():
                    shutil.copytree(efi_source, profile_output / "EFI")
                    logging.info(f"Profile EFI copied to: {profile_output / 'EFI'}")
                else:
                    logging.error(f"EFI directory not found at {efi_source} — skipping profile copy") # <- es sollte nicht logging.warning sein. Dann es loggt einfach nicht als Fehler, sondern als Warnung, und das ist auch eine Sicherheitsrisiko. Angreifern können davon ausnutzen, um ClickFix-Angriffen zu starten.
                    # es ist ein erwartetes Fehler, also kein Stack Trace zu drucken ist nötig - es würde einfach NoneType None drucken und es bringt nichts.
            logging.info("")
            logging.info(f"Your OpenCore EFI for {self.model} has been built at:")
            if self.constants.oc_build_path != None:
                subprocess.run(["/bin/mv", str(self.constants.opencore_release_folder), str(self.constants.oc_build_path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode().strip()
                logging.info(f"    {self.constants.oc_build_path}")
            else:
                logging.info(f"    {self.constants.opencore_release_folder}")
            logging.info("")
        except Exception as e:
            logging.info("")
            logging.error(f"Your OpenCore EFI for {self.model} is not ready due to an unexpected error:")
            logging.exception("Stack Trace:")
            logging.info("Please try again later.")
            sys.exit(3)
