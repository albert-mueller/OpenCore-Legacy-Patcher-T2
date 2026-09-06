"""
misc.py: Class for handling Misc Patches, invocation from build.py
"""

import shutil
import logging
import binascii
import sys
import os
import subprocess
from pathlib import Path

from . import support
from .. import constants
from ..support import generate_smbios
from ..detections import device_probe
from ..datasets import (
    model_array,
    smbios_data,
    cpu_data,
    os_data
)

_T2_MODELS = set(model_array.T2Macs)



class BuildMiscellaneous:
    """
    Build Library for Miscellaneous Hardware and Software Support
    Invoke from build.py
    """

    def __init__(self, model: str, global_constants: constants.Constants, config: dict) -> None:
        self.model: str = model
        self.config: dict = config
        self.constants: constants.Constants = global_constants
        self.computer: device_probe.Computer = self.constants.computer

        self._build()

    def _ensure_nvram_path(self, uuid: str) -> None:
        """Ensure core NVRAM dictionary structures exist safely to avoid KeyErrors."""
        if "NVRAM" not in self.config:
            self.config["NVRAM"] = {}
        if "Add" not in self.config["NVRAM"]:
            self.config["NVRAM"]["Add"] = {}
        if uuid not in self.config["NVRAM"]["Add"]:
            self.config["NVRAM"]["Add"][uuid] = {}

    def _update_nvram_string(self, uuid: str, key: str, value: str) -> None:
        """Appends string flags using precise word boundaries to prevent substring collisions."""
        self._ensure_nvram_path(uuid)
        
        current_value = self.config["NVRAM"]["Add"][uuid].get(key, "")
        
        existing_tokens = set(current_value.split())
        new_tokens = value.strip().split()
        
        tokens_to_add = [t for t in new_tokens if t not in existing_tokens]
        if not tokens_to_add:
            return

        if current_value.strip():
            self.config["NVRAM"]["Add"][uuid][key] = current_value.rstrip() + " " + " ".join(tokens_to_add)
        else:
            self.config["NVRAM"]["Add"][uuid][key] = " ".join(tokens_to_add)

    def _set_nvram_value(self, uuid: str, key: str, value: any, overwrite: bool = False) -> None:
        """Sets an NVRAM variable. If overwrite is False, it only sets if the key is missing."""
        self._ensure_nvram_path(uuid)
        if overwrite or key not in self.config["NVRAM"]["Add"][uuid]:
            self.config["NVRAM"]["Add"][uuid][key] = value

    def _is_t2_mac(self) -> bool:
        """Check whether the current model configuration matches a known T2 system."""
        return self.model in _T2_MODELS

    def _build(self) -> None:
        """Kick off Misc Build Process."""
        self._feature_unlock_handling()
        self._restrict_events_handling()
        self._firewire_handling()
        self._topcase_handling()
        self._thunderbolt_handling()
        self._webcam_handling()
        self._usb_handling()
        self._debug_handling()
        self._cpu_friend_handling()
        self._general_oc_handling()
        self._cpu_topology_handling()
        self._t1_handling()
        self._t2_handling()

    def _feature_unlock_handling(self) -> None:
        """FeatureUnlock Handler."""
        if self.constants.fu_status is False:
            return

        if self.model not in smbios_data.smbios_dictionary:
            return

        if smbios_data.smbios_dictionary[self.model]["Max OS Supported"] >= os_data.os_data.sonoma:
            return
        else: # <- behebt eine Sicherheitslücke, die erlaubt Angreifern unerwünscht, FeatureUnlock zu injizieren, um DoS-Angriffe zu starten
            APPLE_UUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
            support.BuildSupport(self.model, self.constants, self.config).enable_kext(
                "FeatureUnlock.kext", self.constants.featureunlock_version, self.constants.featureunlock_path
            )
            if self.constants.fu_arguments:
                logging.info(f"- Adding additional FeatureUnlock args: {self.constants.fu_arguments}")
                self._update_nvram_string(APPLE_UUID, "boot-args", self.constants.fu_arguments)

    def _restrict_events_handling(self) -> None:
        """RestrictEvents Handler."""
        OCLP_UUID = "4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"
        block_args = ",".join(self._re_generate_block_arguments())
        patch_args = ",".join(self._re_generate_patch_arguments())

        if block_args:
            logging.info(f"- Setting RestrictEvents block arguments: {block_args}")
            support.BuildSupport(self.model, self.constants, self.config).enable_kext(
                "RestrictEvents.kext", self.constants.restrictevents_version, self.constants.restrictevents_path
            )
            self._set_nvram_value(OCLP_UUID, "revblock", block_args, overwrite=True)

        if block_args and not patch_args:
            patch_args = "none"

        if patch_args:
            logging.info(f"- Setting RestrictEvents patch arguments: {patch_args}")
            support.BuildSupport(self.model, self.constants, self.config).enable_kext(
                "RestrictEvents.kext", self.constants.restrictevents_version, self.constants.restrictevents_path
            )
            self._set_nvram_value(OCLP_UUID, "revpatch", patch_args, overwrite=True)

        kext_obj = support.BuildSupport(self.model, self.constants, self.config).get_kext_by_bundle_path("RestrictEvents.kext")
        if kext_obj and kext_obj.get("Enabled") is False:
            support.BuildSupport(self.model, self.constants, self.config).enable_kext(
                "EFICheckDisabler.kext", "", self.constants.efi_disabler_path
            )

    def _re_generate_block_arguments(self) -> list:
        """Generate RestrictEvents block arguments."""
        re_block_args = []
        if self.model in ["MacBookPro6,1", "MacBookPro6,2", "MacBookPro9,1", "MacBookPro10,1"]:
            re_block_args.append("gmux")

        if self.model in model_array.MacPro:
            logging.info("- Disabling memory error reporting")
            re_block_args.append("pcie")

        if self.constants.disable_mediaanalysisd is True:
            logging.info("- Disabling mediaanalysisd")
            re_block_args.append("media")

        return re_block_args
    
    def _re_generate_patch_arguments(self) -> list:
        """Generate RestrictEvents patch arguments.

        sbvmm must be injected for T2 Macs regardless of serial_settings because
        macOS Tahoe's installer preflight checks SupportedDeviceModels via
        RestrictEvents at install time.  When serial_settings is "Advanced" the
        original condition (serial_settings == "None" or secure_status is False)
        never fires, leaving revpatch=sbvmm absent from the 4D1FDA02 NVRAM key
        and causing BIPreflightError Code 9 (J680AP / MacBookPro15,1 confirmed).
        """
        
        re_patch_args = []
        if self.constants.allow_oc_everywhere is False and (self.constants.serial_settings == "None" or self.constants.secure_status is False or self._is_t2_mac()):
            re_patch_args.append("sbvmm")

        if self.model in smbios_data.smbios_dictionary:
            if smbios_data.smbios_dictionary[self.model]["CPU Generation"] == cpu_data.CPUGen.ivy_bridge.value:
                logging.info("- Fixing CoreGraphics support on Ivy Bridge")
                re_patch_args.append("f16c")

        return re_patch_args

    def _cpu_friend_handling(self) -> None:
        """CPUFriend Handler."""
        if self.constants.allow_oc_everywhere is False and self.model not in ["iMac7,1", "Xserve2,1", "Dortania1,1"] and self.constants.disallow_cpufriend is False and self.constants.serial_settings != "None":
            support.BuildSupport(self.model, self.constants, self.config).enable_kext(
                "CPUFriend.kext", self.constants.cpufriend_version, self.constants.cpufriend_path
            )

            pp_map_path = Path(self.constants.platform_plugin_plist_path) / Path(f"{self.model}/Info.plist")
            if not pp_map_path.exists():
                raise Exception(f"{pp_map_path} does not exist for {self.model}.")
            
            Path(self.constants.pp_kext_folder).mkdir(parents=True, exist_ok=True)
            Path(self.constants.pp_contents_folder).mkdir(parents=True, exist_ok=True)
            shutil.copy(pp_map_path, self.constants.pp_contents_folder)
            
            kf_obj = support.BuildSupport(self.model, self.constants, self.config).get_kext_by_bundle_path("CPUFriendDataProvider.kext")
            if kf_obj:
                kf_obj["Enabled"] = True

    def _firewire_handling(self) -> None:
        """FireWire Handler."""
        if self.constants.firewire_boot is False:
            return
        if generate_smbios.check_firewire(self.model) is False:
            return
        else: # <- behebt eine Sicherheitslücke, die erlaubt Angreifern, unerwünschte FireWire-Treibern zu injizieren
            if self.constants.detected_os < os_data.os_data.tahoe: # <- behebt eine Sicherheitslücke, die erlaubt Angreifern, FireWire-Treibern auf macOS 26 zu injizieren, obwohl FireWire auf macOS 26 Tahoe gar nicht mehr funktioniert und dies Kernel Panics verursachen können
                logging.info("- Enabling FireWire Boot Support")
                builder = support.BuildSupport(self.model, self.constants, self.config)
                builder.enable_kext("IOFireWireFamily.kext", self.constants.fw_kext, self.constants.fw_family_path)
                builder.enable_kext("IOFireWireSBP2.kext", self.constants.fw_kext, self.constants.fw_sbp2_path)
                builder.enable_kext("IOFireWireSerialBusProtocolTransport.kext", self.constants.fw_kext, self.constants.fw_bus_path)
                
                # get_kext_by_bundle_path() raises IndexError when the entry is absent, it never
                # returns None - so a falsy check here would be dead code. Catch the exception instead.
                try:
                    builder.get_kext_by_bundle_path("IOFireWireFamily.kext/Contents/PlugIns/AppleFWOHCI.kext")["Enabled"] = True
                except IndexError:
                    logging.error("- AppleFWOHCI.kext plugin entry missing from config")
                    sys.exit(3)
            else:
                logging.error("FireWire support for macOS 26 Tahoe is gone - Apple has deprecated FireWire. Skipping injection of FireWire related kexts.")

    def _topcase_handling(self) -> None:
        """USB/SPI Top Case Handler."""
        if self.model.startswith("MacBook") and self.model in smbios_data.smbios_dictionary:
            cpu_gen = smbios_data.smbios_dictionary[self.model]["CPU Generation"]
            if self.model.startswith("MacBookAir6") or (cpu_data.CPUGen.broadwell <= cpu_gen <= cpu_data.CPUGen.kaby_lake):
                logging.info("- Enabling SPI-based top case support")
                builder = support.BuildSupport(self.model, self.constants, self.config)
                builder.enable_kext("AppleHSSPISupport.kext", self.constants.apple_spi_version, self.constants.apple_spi_path)
                builder.enable_kext("AppleHSSPIHIDDriver.kext", self.constants.apple_spi_hid_version, self.constants.apple_spi_hid_path)
                builder.enable_kext("AppleTopCaseInjector.kext", self.constants.topcase_inj_version, self.constants.top_case_inj_path)

        if not self.constants.custom_model and self.computer.internal_keyboard_type and self.computer.trackpad_type:
            builder = support.BuildSupport(self.model, self.constants, self.config)
            builder.enable_kext("AppleUSBTopCase.kext", self.constants.topcase_version, self.constants.top_case_path)
            
            for part in ["AppleUSBTCButtons.kext", "AppleUSBTCKeyboard.kext", "AppleUSBTCKeyEventDriver.kext"]:
                obj = builder.get_kext_by_bundle_path(f"AppleUSBTopCase.kext/Contents/PlugIns/{part}")
                if obj:
                    obj["Enabled"] = True

            if self.computer.internal_keyboard_type == "Legacy":
                builder.enable_kext("LegacyKeyboardInjector.kext", self.constants.legacy_keyboard, self.constants.legacy_keyboard_path)
            if self.computer.trackpad_type == "Legacy":
                builder.enable_kext("AppleUSBTrackpad.kext", self.constants.apple_trackpad, self.constants.apple_trackpad_path)
            elif self.computer.trackpad_type == "Modern":
                builder.enable_kext("AppleUSBMultitouch.kext", self.constants.multitouch_version, self.constants.multitouch_path)
            else: # <- behebt eine Sicherheitslücke, die erlaubt Angreifern, computer.trackpad_type auf q zu stellen, um den Patcher zum Absturz zu bringen und DoS-Angriffe zu starten
                logging.info("No additional kexts are needed for keyboard or trackpad. Continuing.")
        else:
            if self.model in smbios_data.smbios_dictionary and smbios_data.smbios_dictionary[self.model]["CPU Generation"] < cpu_data.CPUGen.skylake.value:
                if self.model.startswith("MacBook") and self.model not in ["MacBookPro11,4", "MacBookPro11,5", "MacBookPro12,1", "MacBook8,1"]:
                    builder = support.BuildSupport(self.model, self.constants, self.config)
                    builder.enable_kext("AppleUSBTopCase.kext", self.constants.topcase_version, self.constants.top_case_path)
                    for part in ["AppleUSBTCButtons.kext", "AppleUSBTCKeyboard.kext", "AppleUSBTCKeyEventDriver.kext"]:
                        obj = builder.get_kext_by_bundle_path(f"AppleUSBTopCase.kext/Contents/PlugIns/{part}")
                        if obj:
                            obj["Enabled"] = True
                    builder.enable_kext("AppleUSBMultitouch.kext", self.constants.multitouch_version, self.constants.multitouch_path)

            if self.model == "MacBook5,2":
                builder = support.BuildSupport(self.model, self.constants, self.config)
                builder.enable_kext("AppleUSBTrackpad.kext", self.constants.apple_trackpad, self.constants.apple_trackpad_path)
                builder.enable_kext("LegacyKeyboardInjector.kext", self.constants.legacy_keyboard, self.constants.legacy_keyboard_path)

    def _thunderbolt_handling(self) -> None:
        """Thunderbolt Handler."""
        if self.constants.disable_tb is True and self.model in ["MacBookPro11,1", "MacBookPro11,2", "MacBookPro11,3", "MacBookPro11,4", "MacBookPro11,5"]:
            logging.info("- Disabling 2013-2014 laptop Thunderbolt Controller")
            tb_device_path = (
                "PciRoot(0x0)/Pci(0x1,0x1)/Pci(0x0,0x0)/Pci(0x0,0x0)/Pci(0x0,0x0)"
                if self.model in ["MacBookPro11,3", "MacBookPro11,5"]
                else "PciRoot(0x0)/Pci(0x1,0x0)/Pci(0x0,0x0)/Pci(0x0,0x0)/Pci(0x0,0x0)"
            )
            self.config.setdefault("DeviceProperties", {}).setdefault("Add", {})
            self.config["DeviceProperties"]["Add"][tb_device_path] = {
                "class-code": binascii.unhexlify("FFFFFFFF"),
                "device-id": binascii.unhexlify("FFFF0000")
            }
        else:
            logging.info("Your Mac doesn't require disabling the thunderbolt controller. Continuing.")

    def _webcam_handling(self) -> None:
        """iSight Handler."""
        if self.model in smbios_data.smbios_dictionary:
            if smbios_data.smbios_dictionary[self.model].get("Legacy iSight") is True:
                support.BuildSupport(self.model, self.constants, self.config).enable_kext(
                    "LegacyUSBVideoSupport.kext", self.constants.apple_isight_version, self.constants.apple_isight_path
                )

        if not self.constants.custom_model:
            if self.constants.computer.pcie_webcam is True:
                support.BuildSupport(self.model, self.constants, self.config).enable_kext(
                    "AppleCameraInterface.kext", self.constants.apple_camera_version, self.constants.apple_camera_path
                )
        else:
            if self.model.startswith("MacBook") and self.model in smbios_data.smbios_dictionary:
                if cpu_data.CPUGen.haswell <= smbios_data.smbios_dictionary[self.model]["CPU Generation"] <= cpu_data.CPUGen.kaby_lake:
                    support.BuildSupport(self.model, self.constants, self.config).enable_kext(
                        "AppleCameraInterface.kext", self.constants.apple_camera_version, self.constants.apple_camera_path
                    )

    def _usb_handling(self) -> None:
        """USB Handler."""
        if not self._is_t2_mac():
            logging.info("Your Mac is not affected by Unsupported Mantissa speed kernel panics, continuing with USB mapping.")
            usb_map_path = Path(self.constants.plist_folder_path) / Path("AppleUSBMaps/Info.plist")
            usb_map_tahoe_path = Path(self.constants.plist_folder_path) / Path("AppleUSBMaps/Info-Tahoe.plist")
            
            if (
                usb_map_path.exists() and usb_map_tahoe_path.exists()
                and (self.constants.allow_oc_everywhere is False or self.constants.allow_native_spoofs is True)
                and self.model not in ["Xserve2,1", "Dortania1,1"]
                and ((self.model in model_array.Missing_USB_Map or self.model in model_array.Missing_USB_Map_Ventura)
                     or self.constants.serial_settings in ["Moderate", "Advanced"])
            ):
                logging.info("- Adding USB-Map.kext and USB-Map-Tahoe.kext")
                Path(self.constants.map_kext_folder).mkdir(parents=True, exist_ok=True)
                Path(self.constants.map_kext_folder_tahoe).mkdir(parents=True, exist_ok=True)
                Path(self.constants.map_contents_folder).mkdir(parents=True, exist_ok=True)
                Path(self.constants.map_contents_folder_tahoe).mkdir(parents=True, exist_ok=True)
                
                shutil.copy(usb_map_path, self.constants.map_contents_folder)
                shutil.copy(usb_map_tahoe_path, self.constants.map_contents_folder_tahoe / Path("Info.plist"))
                
                builder = support.BuildSupport(self.model, self.constants, self.config)
                m1 = builder.get_kext_by_bundle_path("USB-Map.kext")
                m2 = builder.get_kext_by_bundle_path("USB-Map-Tahoe.kext")
                if m1: m1["Enabled"] = True
                if m2: m2["Enabled"] = True
                
                if self.model in model_array.Missing_USB_Map_Ventura and self.constants.serial_settings not in ["Moderate", "Advanced"]:
                    if m1: m1["MinKernel"] = "22.0.0"

            if self.model in smbios_data.smbios_dictionary and (
                smbios_data.smbios_dictionary[self.model]["CPU Generation"] <= cpu_data.CPUGen.penryn.value or \
                self.model in ["MacPro4,1", "MacPro5,1", "Xserve3,1"]
            ):
                logging.info("- Adding UHCI/OHCI USB support")
                shutil.copy(self.constants.apple_usb_11_injector_path, self.constants.kexts_path)
                builder = support.BuildSupport(self.model, self.constants, self.config)
                for injector in ["AppleUSBOHCI.kext", "AppleUSBOHCIPCI.kext", "AppleUSBUHCI.kext", "AppleUSBUHCIPCI.kext"]:
                    obj = builder.get_kext_by_bundle_path(f"USB1.1-Injector.kext/Contents/PlugIns/{injector}")
                    if obj: obj["Enabled"] = True
                
                m1 = builder.get_kext_by_bundle_path("USB-Map.kext")
                if m1: m1["MaxKernel"] = ""
        else:
            logging.info("Your Mac is affected by Unsupported Mantissa speed kernel panics. Skipping USB port mapping.")

    def _debug_handling(self) -> None:
        """Debug Handler for OpenCorePkg and Kernel Space."""
        APPLE_UUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
        if self.constants.verbose_debug is True:
            logging.info("- Enabling Verbose boot")
            self._update_nvram_string(APPLE_UUID, "boot-args", "-v")

        if self.constants.kext_debug is True:
            logging.info("- Enabling DEBUG Kexts")
            self._update_nvram_string(APPLE_UUID, "boot-args", "-liludbgall liludump=90")
            support.BuildSupport(self.model, self.constants, self.config).enable_kext(
                "DebugEnhancer.kext", self.constants.debugenhancer_version, self.constants.debugenhancer_path
            )

        if self.constants.opencore_debug is True:
            logging.info("- Enabling DEBUG OpenCore")
            self.config.setdefault("Misc", {}).setdefault("Debug", {})
            self.config["Misc"]["Debug"]["Target"] = 0x43
            self.config["Misc"]["Debug"]["DisplayLevel"] = 0x80000042

    def _general_oc_handling(self) -> None:
        """General OpenCorePkg Handler."""
        logging.info("- Adding OpenCanopy GUI")
        shutil.copy(self.constants.gui_path, self.constants.oc_folder)
        builder = support.BuildSupport(self.model, self.constants, self.config)
        
        for efi_bin in ["OpenCanopy.efi", "OpenRuntime.efi", "OpenLinuxBoot.efi", "ResetNvramEntry.efi"]:
            obj = builder.get_efi_binary_by_path(efi_bin, "UEFI", "Drivers")
            if obj: obj["Enabled"] = True

        self.config.setdefault("Misc", {}).setdefault("Boot", {})
        if self.constants.showpicker is False:
            logging.info("- Hiding OpenCore picker")
            self.config["Misc"]["Boot"]["ShowPicker"] = False

        if self.constants.oc_timeout != 5:
            logging.info(f"- Setting custom OpenCore picker timeout to {self.constants.oc_timeout} seconds")
            self.config["Misc"]["Boot"]["Timeout"] = self.constants.oc_timeout

        if self.constants.vault is True:
            logging.info("- Setting Vault configuration")
            self.config.setdefault("Misc", {}).setdefault("Security", {})
            self.config["Misc"]["Security"]["Vault"] = "Secure"

    def _t1_handling(self) -> None:
        """T1 Security Chip Handler with Crash Protection & Native Software Keystore Mode for Tahoe."""
        if self.model in ["MacBookPro13,2", "MacBookPro13,3", "MacBookPro14,2", "MacBookPro14,3"]: # <- behebt eine Sicherheitslücke, die erlaubt Angreifern den if not self.model in ["MacBookPro13,2", "MacBookPro13,3", "MacBookPro14,2", "MacBookPro14,3"], invalider Syntax zu injizieren, um Kexts fürs T1 Hardware aud nicht-T1 macs zu injizieren
            # On macOS Tahoe (26.x / Darwin 25+) or modern test profiles, Apple dropped T1 SEP USB linkage.
            # Injecting Ventura 13.6 kexts causes ABI/IPC mismatch with Tahoe user-space (securityd, LocalAuthentication, akd),
            # breaking password authorization in System Settings and Apple Account login.
            # Using Native Software Keystore mode allows Tahoe to handle password auth & Apple Account natively via CPU crypto.
            is_tahoe_or_newer = self.constants.detected_os >= os_data.os_data.tahoe if hasattr(os_data.os_data, 'tahoe') else True
            active_profile = getattr(self.constants, "build_profile", "standard")
            
            if is_tahoe_or_newer or active_profile in ["standard", "test_b", "test_c", "test_c_spoofed", "test_d"]:
                logging.info("- T1 Mac on macOS Tahoe: Enabling Native Software Keystore Mode for Password Auth & Apple Account")
                logging.info("  (Native Tahoe AppleKeyStore & AppleCredentialManager preserved; legacy Ventura kext downgrade bypassed)")
                return
                    
            logging.info("- Enabling Legacy T1 Security Chip support (Ventura fallback)")
            try:
                builder = support.BuildSupport(self.model, self.constants, self.config)
                identifiers = ["com.apple.driver.AppleSSE", "com.apple.driver.AppleKeyStore", "com.apple.driver.AppleCredentialManager"]
                
                self.config.setdefault("Kernel", {}).setdefault("Block", [])
                for identifier in identifiers:
                    item = builder.get_item_by_kv(self.config["Kernel"]["Block"], "Identifier", identifier)
                    if item: item["Enabled"] = True
            
                kexts_to_enable = [
                    ("corecrypto_T1.kext", self.constants.t1_corecrypto_version, self.constants.t1_corecrypto_path),
                    ("AppleSSE.kext", self.constants.t1_sse_version, self.constants.t1_sse_path),
                    ("AppleKeyStore.kext", self.constants.t1_key_store_version, self.constants.t1_key_store_path),
                    ("AppleCredentialManager.kext", self.constants.t1_credential_version, self.constants.t1_credential_path),
                    ("KernelRelayHost.kext", self.constants.kernel_relay_version, self.constants.kernel_relay_path),
                ]
                for name, version, path in kexts_to_enable:
                    builder.enable_kext(name, version, path)
            except Exception as e:
                logging.error(f"CRITICAL: Failed to configure T1 Security Chip: {e}")
                logging.exception("Stack Trace:")
                logging.info("Please try again later.")
                sys.exit(3)
        else:
            logging.error(f"{self.model} is not a T1 Mac.")
            return

    def _validate_patch(self, patch_dict):
        try:
            find_bytes = patch_dict.get("Find", b"")
            replace_bytes = patch_dict.get("Replace", b"")
            base_sym = patch_dict.get("Base", "")
            
            # When Base symbol is specified with empty Find, OpenCore writes Replace at Base entry
            if base_sym and len(find_bytes) == 0 and len(replace_bytes) > 0:
                return True
            
            # Längenvergleich
            if len(find_bytes) != len(replace_bytes):
                logging.error(f"LÄNGENFEHLER in '{patch_dict.get('Comment')}': "
                              f"Find={len(find_bytes)} Bytes, Replace={len(replace_bytes)} Bytes.")
                logging.error(f"LENGTH ISSUE in '{patch_dict.get('Comment')}': "
                              f"Find={len(find_bytes)} Bytes, Replace={len(replace_bytes)} Bytes.")
                return False
            return True
        except Exception as e:
            logging.error("Wir haben einen Problem, die Bytes-Länge zu vergleichen")
            logging.error("We have an issue to compare the bytes length.")
            sys.exit(3)
    
    def _cpu_topology_handling(self) -> None:
        """Apply CPU topology / thread pooling panic fixes on affected models."""
        if self.model in ["MacBookAir8,1", "MacBookAir8,2", "MacBookPro11,1", "MacBookPro11,2", "MacBookPro11,3"]: # <- behebt eine Sicherheitslücke, die erlaubt Angreifern zu zwingen, CPU-Topologie-Patches zu überspringen, um DoS-Angriffe zu starten
            self._cpu_topology_fix()
        else:
            return

    def _cpu_topology_fix(self) -> None:
        if self.model in ["MacBookAir8,1", "MacBookAir8,2", "MacBookPro11,1", "MacBookPro11,2", "MacBookPro11,3"]: # <- behebt eine Sicherheitslücke, die erlaubt Angreifern, nur per einfaches Anruf von die Funktion self._cpu_topology_fix, CPU-Topologie-Patches auf nicht unterstützte Hardware zu injizieren, um DoS-Angriffe zu starten
            try:
                logging.info(f"- Applying patches for {self.model} to fix CPU topology / thread pooling panic layouts")
                self.config["Kernel"]["Quirks"]["ProvideCurrentCpuInfo"] = True
            except Exception as e:
                logging.error("Applying patches to fix this specific kernel panic failed due to the following error:")
                logging.exception("Stack Trace:")
                logging.info("Please try again later.")
                sys.exit(3)
        else:
            logging.info("Your Mac doesn't require CPU topology / thread pooling panic layout patches. Skipping.")
    
    def _t2_handling(self) -> None:
        """T2 Security Chip Handler."""
        if not self._is_t2_mac():
            logging.error(f"{self.model} is not a T2 Mac.")
            return
        else:
            UnsupportedT2Macs = [
                "MacBookAir8,1",
                "MacBookAir8,2",
                "MacBookAir9,1",
                "MacBookPro15,1",
                "MacBookPro15,2",
                "MacBookPro15,3",
                "MacBookPro15,4",
                "MacBookPro16,3",
                "MacBookPro16,4",
                "Macmini8,1",
                "iMacPro1,1",
            ]
            logging.info(f"{self.model} is a T2 Mac.")
            builder = support.BuildSupport(self.model, self.constants, self.config)
            self.config.setdefault("Kernel", {}).setdefault("Patch", [])
    
            # Prerequisite kext checks
            for kext, ver, path in [
                ("WhateverGreen.kext", self.constants.whatevergreen_version, self.constants.whatevergreen_path),
                ("CryptexFixup.kext", "1.0.5", self.constants.kexts_path),
                ("AMFIPass.kext", "1.4.1", self.constants.kexts_path)
            ]:
                obj = builder.get_kext_by_bundle_path(kext)
                if not obj or obj.get("Enabled") is not True:
                    logging.info(f"- Enabling {kext}")
                    builder.enable_kext(kext, ver, path)
    
            # Handle explicit performance/timeout panics on specific MacBook lines
            # Der Grund warum MinKernel auf 24.0.0 (Sequoias Version von Darwin) stattdessen von 25.x.x eingestellt ist, ist es die Installationsprogramm läuft auf Darwin 24 noch, auch die von 26 Tahoe.
            if self.model in ["MacBookAir8,1", "MacBookAir8,2", "MacBookAir9,1", "MacBookPro16,3"]:
                logging.info(f"- {self.model}: Applying Unsupported Mantissa Speed kernel panic patches")
                try:
                    logging.info(f"- {self.model}: Disabling USB-Map.kext and USB-Map-Tahoe.kext if any is there")
                    m1 = builder.get_kext_by_bundle_path("USB-Map.kext")
                    m2 = builder.get_kext_by_bundle_path("USB-Map-Tahoe.kext")
                    if m1: m1["Enabled"] = False
                    if m2: m2["Enabled"] = False
                except Exception as e:
                    logging.info(f"- {self.model}: Great news! We tried disabling USB-Map.kext and USB-Map-Tahoe.kext but we didn't find them.")
                    logging.info("You don't have to worry about this message.")
            try:
                APPLE_NVRAM_UUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
                logging.info("- Defining NVRAM variable APPLE_NVRAM_UUID")
            except Exception as e:
                logging.error("We failed to define APPLE_NVRAM_UUID. It failed to do so because of the following error:")
                logging.exception("Stack Trace:")
                logging.info("Please try again later.")
                sys.exit(3)
    
            try:
                logging.info("- Adding T2-specific boot arguments for macOS 15/26")
                # agdpmod=pikera is required for models with a discrete Polaris/Navi dGPU to prevent
                # the Tahoe AGDP display-policy check from deadlocking WindowServer (black screen).
                # iGPU-only models use agdpmod=vit9696 instead.
                _agdpmod = "pikera" if self.computer.dgpu else "vit9696"
                self._update_nvram_string(APPLE_NVRAM_UUID, "boot-args",
                    f"-v rddelay=10 igfxfw=2 igfxonln=1 -disable_ext_panics -no_compat_check -revbeta "
                    f"agdpmod={_agdpmod} forceRenderStandby=0 revpatch=sbvmm ipc_control_port_options=0 AMFIPass=0x1")
            except Exception as e:
                logging.error("Injecting T2 specific boot arguments failed due to the following error:")
                logging.exception("Stack Trace:")
                logging.info("Please try again later.")
                sys.exit(3)
                
            # Structure guarding for OpenCore NVRAM delete layout
            self.config.setdefault("NVRAM", {}).setdefault("Delete", {})
            if APPLE_NVRAM_UUID not in self.config["NVRAM"]["Delete"]:
                self.config["NVRAM"]["Delete"][APPLE_NVRAM_UUID] = []
            if "boot-args" not in self.config["NVRAM"]["Delete"][APPLE_NVRAM_UUID]:
                self.config["NVRAM"]["Delete"][APPLE_NVRAM_UUID].append("boot-args")
    
            try:
                logging.info("- Set SIP to allow Root Volume patching on T2 Macs (03080000)")
                self._set_nvram_value(APPLE_NVRAM_UUID, "csr-active-config", binascii.unhexlify("03080000"), overwrite=True)
            except Exception as e:
                logging.error("Setting SIP to 03080000 failed due to the following error:")
                logging.exception("Stack Trace:")
                logging.info("Please try again later.")
                sys.exit(3)
            
            # Allows booting macOS 26 Tahoe's installer via OpenCore on T2 Macs
            self.config.setdefault('Kernel', {}).setdefault('Patch', [])
            kernel_patches = self.config['Kernel']['Patch']
    
            if not any(p.get("Comment") == "Patch AppleKeyStore SEP retry limit" for p in kernel_patches):
                new_patch = {
                    "Arch": "x86_64",
                    "Identifier": "com.apple.driver.AppleKeyStore",
                    "Base": "",
                    "Comment": "Patch AppleKeyStore SEP retry limit",
                    "Count": 1,
                    "Enabled": True,
                    "MinKernel": "25.0.0",
                    "MaxKernel": "25.99.99",
                    "Find": binascii.unhexlify("FF90F00100004183FF140F8D06050000"),
                    "Replace": binascii.unhexlify("FF90F00100004183FFC80F8D06050000"),
                    "Mask": b"",
                    "ReplaceMask": b"",
                    "Limit": 0,
                    "Skip": 0
                }
                if self._validate_patch(new_patch):
                    logging.info("- Injecting AppleKeyStore SEP retry-limit patch")
                    kernel_patches.append(new_patch)
             
            # --- Patch 2: Force FileVault on Broken Seal ---
            if not any(p.get("Comment") == "Force FileVault on Broken Seal" for p in kernel_patches):
                new_patch = {
                    "Arch": "x86_64",
                    "Identifier": "com.apple.filesystems.apfs",
                    "Base": "_apfs_filevault_allowed",
                    "Comment": "Force FileVault on Broken Seal",
                    "Count": 0,
                    "Enabled": True,
                    "MinKernel": "20.4.0",
                    "MaxKernel": "",
                    "Find": b"",
                    "Replace": binascii.unhexlify("B801000000C3"),
                    "Mask": b"",
                    "ReplaceMask": b"",
                    "Limit": 0,
                    "Skip": 0
                }
                if self._validate_patch(new_patch):
                    logging.info("- Injecting Force FileVault on Broken Seal patch")
                    kernel_patches.append(new_patch)
             
            # --- Patch 3: Disable Library Validation Enforcement ---
            if not any(p.get("Comment") == "Disable Library Validation Enforcement" for p in kernel_patches):
                new_patch = {
                    "Arch": "x86_64",
                    "Identifier": "kernel",
                    "Base": "_cs_require_lv",
                    "Comment": "Disable Library Validation Enforcement",
                    "Count": 0,
                    "Enabled": True,
                    "MinKernel": "20.0.0",
                    "MaxKernel": "",
                    "Find": b"",
                    "Replace": binascii.unhexlify("B800000000C3"),
                    "Mask": b"",
                    "ReplaceMask": b"",
                    "Limit": 0,
                    "Skip": 0
                }
                if self._validate_patch(new_patch):
                    logging.info("- Injecting Disable Library Validation Enforcement patch")
                    kernel_patches.append(new_patch)
             
            # --- Patch 4: Disable _csr_check() in _vnode_check_signature ---
            if not any(p.get("Comment") == "Disable _csr_check() in _vnode_check_signature" for p in kernel_patches):
                new_patch = {
                    "Arch": "x86_64",
                    "Identifier": "com.apple.driver.AppleMobileFileIntegrity",
                    "Base": "__ZL22_vnode_check_signatureP5vnodeP5labeliP7cs_blobPjS5_ijPPcPm",
                    "Comment": "Disable _csr_check() in _vnode_check_signature",
                    "Count": 1,
                    "Enabled": True,
                    "MinKernel": "22.0.0",
                    "MaxKernel": "",
                    "Find": binascii.unhexlify("01000000E80000000085C075"),
                    "Replace": binascii.unhexlify("01000000B80100000085C075"),
                    "Mask": binascii.unhexlify("FFFFFFFFFF00000000FFFFFF"),
                    "ReplaceMask": b"",
                    "Limit": 0,
                    "Skip": 0
                }
                if self._validate_patch(new_patch):
                    logging.info("- Injecting Disable _csr_check() in _vnode_check_signature patch")
                    kernel_patches.append(new_patch)
            
            # Bypass osinstallersetupd bridge device validation checks (Fixes Attestation Error -10000)
            try:
                logging.info("- Injecting User-Space Attestation bypass flags (Fixes Error -10000)")
                self._update_nvram_string(APPLE_NVRAM_UUID, "boot-args", "-oas_skip_attestation")
                self._set_nvram_value(APPLE_NVRAM_UUID, "IAS_ENV_SKIP_ATTESTATION", "1", overwrite=True)
            except Exception as e:
                logging.error("Failed to inject Attestation Error -10000 bypass flags:")
                logging.exception("Stack Trace:")
                logging.info("Please try again later.")
                sys.exit(3)
            # dieses Patch bringt dazu, dass T2 Macs den Fehler nach 29 Minuten verbleibend weiter mit die Installation fährt statt einen Fehler zu zeigen
            try:
                logging.info("- Disabling UEFI updates for T2 Macs to prevent errors after 29 minutes remaining")
                logging.info("This patch will block UEFI updates on T2 Macs. To update the UEFI on T2 Macs while running unsupported macOS versions, if you care about your UEFI being up to date, you'll need to update it via exiting OpenCore and entering DFU mode using another Mac.")
                self._set_nvram_value(APPLE_NVRAM_UUID, "run-efi-updater", "No", overwrite=True)
            except Exception as e:
                logging.error("Failed to disable UEFI updates for T2 Macs after 29 minutes remaining due to an error:")
                logging.exception("Stack Trace:")
                logging.info("Please try again later.")
                sys.exit(3)
