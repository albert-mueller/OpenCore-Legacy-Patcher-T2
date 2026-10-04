"""
modern_wireless.py: Modern Wireless detection
"""

from ..base import BaseHardware, HardwareVariant

from ...base import PatchType

from .....constants  import Constants
from .....detections import device_probe

from .....datasets.os_data import os_data


class ModernWireless(BaseHardware):

    def __init__(self, xnu_major, xnu_minor, os_build, global_constants: Constants) -> None:
        super().__init__(xnu_major, xnu_minor, os_build, global_constants)


    def name(self) -> str:
        """
        Display name for end users
        """
        return f"{self.hardware_variant()}: Modern Wireless"


    def present(self) -> bool:
        """
        Targeting Modern Wireless
        """
        return isinstance(self._computer.wifi, device_probe.Broadcom) and (
            self._computer.wifi.chipset in [
                device_probe.Broadcom.Chipsets.AirPortBrcm4360,
                device_probe.Broadcom.Chipsets.AirportBrcmNIC,
                device_probe.Broadcom.Chipsets.AirPortBrcmNICThirdParty,
            ]
        )


    def native_os(self) -> bool:
        """
        Dropped support with macOS 14, Sonoma
        """
        return self._xnu_major < os_data.sonoma.value


    def hardware_variant(self) -> HardwareVariant:
        """
        Type of hardware variant
        """
        return HardwareVariant.NETWORKING

    def _base_patch(self) -> dict:
        """
        Base patches for Modern Wireless
        """
        if self._constants.use_ybronst_wifi is True:
            # YBronst's WiFi set (-YB folders in PatcherSupportPkg), proven working on
            # Sonoma, Sequoia and Tahoe. Tahoe reuses the 13.7.2-24 build, as before.
            source = (f"13.7.2-{self._xnu_major}" if self._xnu_major < os_data.tahoe.value else "13.7.2-24") + "-YB"
        else:
            # Dortania's set: dedicated 13.7.2-25 payloads on Tahoe (matches upstream).
            source = f"13.7.2-{self._xnu_major}"
        return {
            "Modern Wireless": {
                PatchType.OVERWRITE_SYSTEM_VOLUME: {
                    "/usr/libexec": {
                        "wifip2pd": source,
                    },
                },
                PatchType.MERGE_SYSTEM_VOLUME: {
                    "/System/Library/PrivateFrameworks": {
                        "IO80211.framework":        source,
                        "WiFiPeerToPeer.framework": source,
                    },
                }
            },
        }

    def _extended_patch(self) -> dict:
        """
        Extended patches for Modern Wireless
        """
        if self._xnu_major > os_data.sonoma:
            return {}

        source = f"13.7.2-{self._xnu_major}" + ("-YB" if self._constants.use_ybronst_wifi is True else "")

        return {
            "Modern Wireless Extended": {
                PatchType.OVERWRITE_SYSTEM_VOLUME: {
                    "/usr/libexec": {
                        "airportd": source,
                    },
                },
                PatchType.MERGE_SYSTEM_VOLUME: {
                    "/System/Library/Frameworks": {
                        **({ "CoreWLAN.framework": source } if self._xnu_major == os_data.sonoma else {}),
                    },
                    "/System/Library/PrivateFrameworks": {
                        "CoreWiFi.framework":       source,
                    },
                }
            },
        }

    def patches(self) -> dict:
        """
        Patches for Modern Wireless
        """
        if self.native_os() is True:
            return {}

        return {
            **self._base_patch(),
            **self._extended_patch(),
        }
