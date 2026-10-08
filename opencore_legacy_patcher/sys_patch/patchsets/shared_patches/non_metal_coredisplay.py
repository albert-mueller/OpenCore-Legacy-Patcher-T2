"""
non_metal_coredisplay.py: Non-Metal CoreDisplay patches
"""

from .base import BaseSharedPatchSet

from ..base import PatchType

from ....datasets.os_data import os_data


class NonMetalCoreDisplay(BaseSharedPatchSet):

    def __init__(self, xnu_major: int, xnu_minor: int, marketing_version: str) -> None:
        super().__init__(xnu_major, xnu_minor, marketing_version)


    def _os_requires_patches(self) -> bool:
        """
        Dropped support with macOS 10.14, Mojave
        """
        return self._xnu_major >= os_data.mojave.value


    def patches(self) -> dict:
        """
        Nvidia Web Drivers require an older build of CoreDisplay
        """
        if self._xnu_major >= os_data.tahoe.value:
            # Non-Metal GPU patches currently cause kernel panics on macOS 26, Tahoe.
            # Safety guard: skip this patchset entirely until a working fix is found.
            # Unrelated patchsets (eg. Legacy Wireless) are unaffected, as they come
            # from separate hardware/shared patch classes and are not gated here.
            return {}

        if self._os_requires_patches() is False:
            return {}

        return {
            "Non-Metal CoreDisplay Common": {
                PatchType.MERGE_SYSTEM_VOLUME: {
                    "/System/Library/Frameworks": {
                        # PatcherSupportPkg ships 10.13.6-<xnu_major> CoreDisplay up to xnu_major 25 (Tahoe);
                        # cap there for newer hosts instead of requesting a non-existent folder.
                        # Tahoe now uses 10.13.6-25 like upstream (dortania/OpenCore-Legacy-Patcher dd681ac).
                        "CoreDisplay.framework": f"10.13.6-{min(self._xnu_major, os_data.tahoe.value)}",
                    },
                },
            },
        }
