"""
t1_login_experimental.py: Experimental T1 Login Patches for macOS Tahoe

Goal: Allow MacBookPro14,3 (T1) users to log in with password and use
iCloud/Apple services WITHOUT requiring Touch ID to be functional.

On macOS Tahoe (26.x), legacy Ventura/Sequoia biometrickitd and SharedUtils
binaries cause SecurityAgent and WindowServer to crash, resulting in a black
screen at login and Touch Bar flashing.

Tahoe natively supports password-based authentication when biometric daemons
are left untouched and not replaced with broken legacy binaries.
"""

from ..base import BaseHardware, HardwareVariant

from ...base import PatchType

from .....constants import Constants

from .....datasets.os_data import os_data


class T1LoginExperimental(BaseHardware):

    def __init__(self, xnu_major, xnu_minor, os_build, global_constants: Constants) -> None:
        super().__init__(xnu_major, xnu_minor, os_build, global_constants)


    def name(self) -> str:
        """
        Display name for end users
        """
        return f"{self.hardware_variant()}: T1 Login (Experimental – Password Only)"


    def present(self) -> bool:
        """
        Only activate on T1 Macs AND only when targeting macOS Tahoe or later.
        On Sequoia and earlier the standard t1_security.py patches are sufficient.
        """
        if not self._computer.t1_chip:
            return False
        # Only activate for Tahoe (macOS 26) and later
        return self._xnu_major >= os_data.tahoe.value


    def native_os(self) -> bool:
        """
        T1 support was dropped in macOS 14 Sonoma.
        This patch is never 'native' on Tahoe.
        """
        return False


    def hardware_variant(self) -> HardwareVariant:
        """
        Type of hardware variant
        """
        return HardwareVariant.MISCELLANEOUS


    def patches(self) -> dict:
        """
        Experimental patches for T1 Login on macOS Tahoe.

        On macOS Tahoe, password-based authentication and iCloud login are handled
        natively by macOS. Legacy biometrics daemons and mismatched SharedUtils
        frameworks are intentionally NOT injected to prevent SecurityAgent / WindowServer
        crashes (black screen at login) and Touch Bar reboot loops.
        """
        return {}
