"""
security.py: Class for handling macOS Security Patches, invocation from build.py
"""

import logging
import binascii
import sys
import wx
import threading
import webbrowser

from . import support
from .. import constants
from ..support import utilities
from ..detections import device_probe
from ..datasets import (
    model_array,
    smbios_data,
    os_data
)

# T2 Mac models with dual GPUs (Intel UHD 630 + discrete AMD GPU)
# that require connector-less ig-platform-id injection so the dGPU
# drives the display while iGPU provides QuickSync/VDA.
# NOTE: Macmini8,1 and MacBookPro16,3 / MacBookAir9,1 are iGPU-only and
# must NOT receive connector-less platform-ids (avoids installer GUI freeze).
_T2_DGPU_UHD630_MODELS = {
    "MacBookPro15,1",  # 15-inch 2018 Intel UHD Graphics 630 + AMD Radeon Pro 555X
    "MacBookPro15,3",  # 15-inch 2019 Intel UHD Graphics 630 + AMD Radeon Pro Vega 16/20
    "MacBookPro16,1",  # 16-inch 2019, Intel UHD Graphics 630 + AMD Radeon Pro 5300M
    "MacBookPro16,4",  # 16-inch 2019 CTO, Intel UHD Graphics 630 + AMD Radeon Pro 5600M
}

# T2 Mac models that do not have an Intel iGPU, or where iGPU injection is not required/recommended.
_T2_NO_IGPU_MODELS = {
    "iMacPro1,1",      # iMac Pro 2017
    "Macmini8,1",      # Mac mini 2018 (iGPU only - drives HDMI/DP directly)
}



class BuildSecurity:
    """
    Build Library for Security Patch Support
    Invoke from build.py
    """

    def __init__(self, model: str, global_constants: constants.Constants, config: dict) -> None:
        self.model: str = model
        self.config: dict = config
        self.constants: constants.Constants = global_constants
        self.computer: device_probe.Computer = self.constants.computer
        
        # ── Global Hardware & OS Targets Scopes ───────────────────────
        self.is_tahoe_target: bool = False
        self.is_ice_lake: bool = (self.model == "MacBookAir9,1")
        self.is_mac_mini: bool = (self.model == "Macmini8,1")

        self._build()

    # ------------------------------------------------------------------
    # NVRAM helpers
    # ------------------------------------------------------------------

    def _read_nvram_string(self, uuid: str, key: str) -> str:
        """Utility helper to read an existing NVRAM string safely."""
        if uuid in self.config.get("NVRAM", {}).get("Add", {}):
            return self.config["NVRAM"]["Add"][uuid].get(key, "")
        return ""

    def _update_nvram_string(self, uuid: str, key: str, value: str) -> None:
        """
        Appends boot-arg tokens to an NVRAM string variable, only for
        tokens not already present.
        """
        if "NVRAM" not in self.config:
            self.config["NVRAM"] = {"Add": {}}
        if "Add" not in self.config["NVRAM"]:
            self.config["NVRAM"]["Add"] = {}
        if uuid not in self.config["NVRAM"]["Add"]:
            self.config["NVRAM"]["Add"][uuid] = {}

        current_value = self.config["NVRAM"]["Add"][uuid].get(key, "")

        existing_tokens = set(current_value.split())
        new_tokens = value.strip().split()

        tokens_to_add = [t for t in new_tokens if t not in existing_tokens]
        if not tokens_to_add:
            return

        if current_value.strip():
            self.config["NVRAM"]["Add"][uuid][key] = (
                current_value.rstrip() + " " + " ".join(tokens_to_add)
            )
        else:
            self.config["NVRAM"]["Add"][uuid][key] = " ".join(tokens_to_add)

    def _set_nvram_value(self, uuid: str, key: str, value: any, overwrite: bool = False) -> None:
        """
        Sets an NVRAM variable. If overwrite is False, only sets if the
        key is absent.
        """
        if "NVRAM" not in self.config:
            self.config["NVRAM"] = {"Add": {}}
        if "Add" not in self.config["NVRAM"]:
            self.config["NVRAM"]["Add"] = {}
        if uuid not in self.config["NVRAM"]["Add"]:
            self.config["NVRAM"]["Add"][uuid] = {}

        if overwrite or key not in self.config["NVRAM"]["Add"][uuid]:
            self.config["NVRAM"]["Add"][uuid][key] = value

    # ------------------------------------------------------------------
    # Model detection helpers
    # ------------------------------------------------------------------

    def _ensure_path(self, *keys, default=dict):
        """Utility helper to ensure a nested dict path exists."""
        node = self.config
        for key in keys:
            node = node.setdefault(key, default() if isinstance(default, type) else default)
        return node

    def _is_t2_mac(self) -> bool:
        """Return True if the current model has a T2 security chip."""
        if self.model in model_array.T2Macs:
            return True
        return "T2_CHIP" in self.constants.device_properties.get(self.model, {}).get("Features", [])

    def _requires_t2_graphics_injection(self) -> bool:
        """Return True if this T2 model needs Intel graphics injection (dual-GPU models only)."""
        return self.model in _T2_DGPU_UHD630_MODELS

    def _should_skip_t2_graphics_injection(self) -> bool:
        """Return True if this T2 model should explicitly skip Intel graphics injection."""
        return self.model in _T2_NO_IGPU_MODELS

    def _t2_uses_amfipass(self) -> bool:
        """T2 builds enable AMFIPass in misc._t2_handling (runs after security)."""
        return self._is_t2_mac()

    def _apply_t2_amfi_boot_args(self, apple_nvram_uuid: str) -> None:
        """Apply AMFI-related boot-args based on user path validation."""
        if self._t2_uses_amfipass():
            logging.info("  > T2 target utilizes AMFIPass layer. Injecting validated Tahoe storage bypasses.")
            self._update_nvram_string(apple_nvram_uuid, "boot-args", (
                "-amfipassbeta cs_allow_invalid=1 cs_unrestricted_cs=1 cs_debug=1 io=0xffffffff"
            ))
            return

        # Fallback if AMFIPass pathing is completely stripped
        existing = self._read_nvram_string(apple_nvram_uuid, "boot-args")
        if "amfi=0x80" not in existing:
            logging.warning("  > AMFIPass bypassed. Falling back to amfi=0x80 absolute drop.")
            self._update_nvram_string(apple_nvram_uuid, "boot-args", (
                "amfi=0x80 amfi_get_out_of_my_way=1 cs_debug=1 io=0xffffffff"
            ))

    # ------------------------------------------------------------------
    # Graphics injection helpers
    # ------------------------------------------------------------------

    def _get_graphics_device_properties_path(self):
        """Return the probed PCI path for the integrated graphics device."""
        if self.constants.custom_model:
            logging.info("- Skipping T2 Intel graphics injection for custom model (no probed iGPU path)")
            return None

        igpu = getattr(self.computer, "igpu", None)
        if igpu and getattr(igpu, "pci_path", None):
            return igpu.pci_path

        for gpu in getattr(self.computer, "gpus", []) or []:
            if isinstance(gpu, device_probe.Intel) and getattr(gpu, "pci_path", None):
                return gpu.pci_path

        logging.info("- Skipping T2 Intel graphics injection (unable to confirm iGPU PCI path)")
        return None

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _set_nested_config_value(self, path: str, value: any) -> None:
        """Write a value into a nested config dict using a dotted path."""
        node = self.config
        keys = path.split('.')
        for part in keys[:-1]:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        node[keys[-1]] = value

    # ------------------------------------------------------------------
    # T2 security helpers
    # ------------------------------------------------------------------

    def _apply_t2_graphics_injection(self) -> None:
        """Inject integrated Intel iGPU DeviceProperties for dual-GPU T2 Macs."""
        if self._should_skip_t2_graphics_injection() or not self._requires_t2_graphics_injection():
            logging.info(f"- Skipping Intel graphics injection for {self.model} (no iGPU or single-GPU model)")
            return

        graphics_path = self._get_graphics_device_properties_path()
        if not graphics_path:
            return

        self._ensure_path("DeviceProperties", "Add", graphics_path)
        gfx = self.config["DeviceProperties"]["Add"][graphics_path]

        # Dual-GPU Coffee Lake models (MacBookPro15,1/15,3/16,1/16,4):
        # Set headless connector-less ig-platform-id (0x3E9B0006) so the dGPU
        # drives the display output without display-policy arbitration deadlock.
        logging.info(f"- {self.model}: Injecting headless UHD630 DeviceProperties for dGPU display configuration")
        gfx["AAPL,ig-platform-id"] = binascii.unhexlify("06009B3E")  # 0x3E9B0006 LE
        gfx["device-id"]           = binascii.unhexlify("9B3E0000")  # 0x3E9B0000 LE

        try:
            gfx["framebuffer-patch-enable"] = binascii.unhexlify("01000000")
            gfx["framebuffer-stolenmem"]    = binascii.unhexlify("00003001")  
            gfx["framebuffer-fbmem"]        = binascii.unhexlify("00009000")  
            logging.info("  > T2 iGPU headless configuration parameters applied successfully.")
        except Exception as e:
            logging.error(f"Whoops, injecting framebuffer patches for {self.model} failed: {e}")
            logging.exception("Stack Trace:")
            logging.info("Please try again later.")
            sys.exit(3)

    def _apply_t2_memory_descriptor_overrides(self, apple_nvram_uuid: str) -> None:
        if not self.model in model_array.T2Macs:
            logging.error(f"By accident, we executed logic for T2 Macs while {self.model} doesn't have the T2 chip. Aborting.")
            sys.exit(3) # # sollte normalerweise niemals hier erreichen - falls die Programme durch einen Angreifer ausgetrickst wurde, dass es nicht um T2 Mac handelt statt um ein T2 Mac, nur denn wird es hier erreichen
        else:
            """Apply mandatory security overrides required for T2 Macs to boot."""
            logging.info("- Applying T2 memory descriptor overrides (T2 ONLY)")
    
            # Configure raw boundaries cleanly
            self.config["Misc"]["Security"]["SecureBootModel"] = "Disabled"
            self.config["Misc"]["Security"]["DmgLoading"]      = "Any"
            self.config["Misc"]["Security"]["ApECID"]          = 0
    
            # FIX: Keyword-Typo korrigiert
            self._apply_t2_amfi_boot_args(apple_nvram_uuid)
            self._update_nvram_string(apple_nvram_uuid, "boot-args", "ipc_control_port_options=0 -v keepsyms=1")
    
    # ------------------------------------------------------------------
    # Main build entry point
    # ------------------------------------------------------------------

    def _build(self) -> None:
        """Kick off Security Build Process."""
        APPLE_NVRAM_UUID = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
        OCLP_NVRAM_UUID  = "4D1FDA02-38C7-4A6A-9CC6-4BCCA8B30102"

        # ==============================================================
        # GLOBAL EVALUATION: Universal AMFIPass Injection Engine
        # ==============================================================
        needs_amfipass = False

        if self._is_t2_mac():
            if self.is_tahoe_target or smbios_data.smbios_dictionary[self.model]["Max OS Supported"] < os_data.os_data.tahoe:
                needs_amfipass = True
        else:
            if self.model in model_array.T2Macs:
                logging.error(f"By accident, we executed logic for non-T2 Macs while {self.model} has the T2 chip. Aborting. Try reinstalling OpenCore Legacy Patcher T2.")
                sys.exit(3) # sollte normalerweise niemals hier erreichen - falls die Programme durch einen Angreifer ausgetrickst wurde, dass ein T2 Mac nicht ein T2 Mac ist, nur denn wird es hier erreichen
            else:
                if smbios_data.smbios_dictionary[self.model]["Max OS Supported"] < os_data.os_data.sonoma or self.model == "MacBookPro14,3":
                    needs_amfipass = True

        # ==============================================================
        # Branch A: T2 Mac Consolidated Security Configuration
        # ==============================================================
        if self._is_t2_mac():
            if not self.model in model_array.T2Macs:
                logging.error(f"By accident, we executed logic for T2 Macs while {self.model} doesn't have the T2 chip. Aborting.")
                sys.exit(3) # # sollte normalerweise niemals hier erreichen - falls die Programme durch einen Angreifer ausgetrickst wurde, dass es nicht um T2 Mac handelt statt um ein T2 Mac, nur denn wird es hier erreichen
            else:
                logging.info("- T2 Mac detected — applying consolidated T2 security settings")
                logging.info(f"{self.model} has a T2 chip.")
                
                # 1. Base initialization & OS Target Checks (Zwingend als Erstes!)
                self._apply_t2_memory_descriptor_overrides(APPLE_NVRAM_UUID)
                
                # 2. Grafik- & Kernel-Injektionen (Unabhängig von Variablen-Fluktuatuationen absichern)
                self._apply_t2_graphics_injection()
    
                # 3. Scope graphics injection flags strictly to active valid targets
                if self._requires_t2_graphics_injection():
                    self._update_nvram_string(APPLE_NVRAM_UUID, "boot-args", "igfxonln=1 igfxfw=2 forceRenderStandby=0 agdpmod=vit9696")
    
                # 5. Hard Structural Boundaries Pass
                logging.info("- Final T2 verification pass (Enforcing absolute boundaries)")
                self.config["Misc"]["Security"]["SecureBootModel"] = "Disabled"
                self.config["Misc"]["Security"]["ApECID"]          = 0
                self.config["Misc"]["Security"]["DmgLoading"]      = "Any"
    
                logging.info("  > Final T2 verification complete. ")

        # ==============================================================
        # Branch B: Non-T2 Mac Configuration (PROTECTED VIA ELSE)
        # ==============================================================
        else:
            if self.model in model_array.T2Macs:
                logging.error(f"By accident, we executed logic for non-T2 Macs while {self.model} has the T2 chip. Aborting. Try reinstalling OpenCore Legacy Patcher T2.")
                sys.exit(3) # sollte normalerweise niemals hier erreichen - falls die Programme durch einen Angreifer ausgetrickst wurde, dass ein T2 Mac nicht ein T2 Mac ist, nur denn wird es hier erreichen
            else:
                logging.info("- Non-T2 Mac detected — isolating legacy environment execution chain")
                logging.info(f"{self.model} has no T2 chip.")
                if self.constants.sip_status is False or self.constants.custom_sip_value:
                    logging.info("- Non-T2 Mac: SIP lowered — applying SIP-related settings")
                    
                    self._update_nvram_string(APPLE_NVRAM_UUID, "boot-args", "ipc_control_port_options=0")
              
                    if self.constants.wxpython_variant is True:
                        support.BuildSupport(self.model, self.constants, self.config).enable_kext(
                            "AutoPkgInstaller.kext", self.constants.autopkg_version, self.constants.autopkg_path
                        )
             
                    if self.constants.custom_sip_value:
                        logging.info(f"- Setting SIP value to: {self.constants.custom_sip_value}")
                        sip_hex = utilities.string_to_hex(self.constants.custom_sip_value.lstrip("0x"))
                        self._set_nvram_value(APPLE_NVRAM_UUID, "csr-active-config", sip_hex, overwrite=True)
                    elif self.constants.sip_status is False:
                        logging.info("- Set SIP to allow Root Volume patching")
                        self._set_nvram_value(APPLE_NVRAM_UUID, "csr-active-config", binascii.unhexlify("03080000"), overwrite=True)
              
                    logging.info("- Allowing FileVault on Root Patched systems")
                    support.BuildSupport(self.model, self.constants, self.config).get_item_by_kv(
                        self.config["Kernel"]["Patch"], "Comment", "Force FileVault on Broken Seal"
                    )["Enabled"] = True
                    self._update_nvram_string(OCLP_NVRAM_UUID, "OCLP-Settings", "-allow_fv")
               
                    logging.info("- Enabling KC UUID mismatch patch")
                    self._update_nvram_string(APPLE_NVRAM_UUID, "boot-args", "-nokcmismatchpanic")
                    support.BuildSupport(self.model, self.constants, self.config).enable_kext(
                        "RSRHelper.kext", self.constants.rsrhelper_version, self.constants.rsrhelper_path
                    )
                
                # Shared: AMFI / Library Validation (Legacy Non-T2 verification targets)
                # Ensure kernel patches for Library Validation and FileVault are active
                support.BuildSupport(self.model, self.constants, self.config).get_item_by_kv(
                    self.config["Kernel"]["Patch"], "Comment", "Disable Library Validation Enforcement"
                )["Enabled"] = True
                support.BuildSupport(self.model, self.constants, self.config).get_item_by_kv(
                    self.config["Kernel"]["Patch"], "Comment", "Disable _csr_check() in _vnode_check_signature"
                )["Enabled"] = True
                self._update_nvram_string(OCLP_NVRAM_UUID, "OCLP-Settings", "-allow_amfi")

                # Attestation error -10000 bypass (Apple Account / setup compatibility)
                self._update_nvram_string(APPLE_NVRAM_UUID, "boot-args", "-oas_skip_attestation")
                self._set_nvram_value(APPLE_NVRAM_UUID, "IAS_ENV_SKIP_ATTESTATION", "1", overwrite=True)

                if self.constants.disable_cs_lv is True and self.constants.disable_amfi is True and not needs_amfipass:
                    logging.info("- Disabling AMFI (non-T2 fallback)")
                    self._update_nvram_string(APPLE_NVRAM_UUID, "boot-args", "amfi=0x80")
                else:
                    logging.info("- Using AMFIPass & Library Validation Enforcement Bypass for Apple Account compatibility")
                    self._update_nvram_string(APPLE_NVRAM_UUID, "boot-args", "-amfipassbeta ipc_control_port_options=0")
    
                if self.constants.secure_status is False:
                    logging.info("- Disabling SecureBootModel (non-T2)")
                    self.config["Misc"]["Security"]["SecureBootModel"] = "Disabled"

        if needs_amfipass:
            logging.info("- Enabling AMFIPass Framework Kext injection context natively.")
            support.BuildSupport(self.model, self.constants, self.config).enable_kext(
                "AMFIPass.kext", self.constants.amfipass_version, self.constants.amfipass_path
            )
            # Ensure -amfipassbeta is present in boot-args and remove conflicting amfi=0x80
            self._update_nvram_string(APPLE_NVRAM_UUID, "boot-args", "-amfipassbeta ipc_control_port_options=0")
            current_args = self._read_nvram_string(APPLE_NVRAM_UUID, "boot-args")
            if "amfi=0x80" in current_args:
                cleaned_args = " ".join([arg for arg in current_args.split() if arg != "amfi=0x80"])
                self.config["NVRAM"]["Add"][APPLE_NVRAM_UUID]["boot-args"] = cleaned_args
