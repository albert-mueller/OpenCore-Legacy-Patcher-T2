"""
metal_31001.py: Metal 31001 patches
"""


from .base import BaseSharedPatchSet


from ....datasets.os_data import os_data


class LegacyMetal31001(BaseSharedPatchSet):

    def __init__(self, xnu_major: int, xnu_minor: int, marketing_version: str) -> None:
        super().__init__(xnu_major, xnu_minor, marketing_version)

    def _os_requires_patches(self) -> bool:
        """
        Check if the current OS requires
        """
        return self._xnu_major >= os_data.ventura.value

    def _patches_metal_31001_common(self) -> dict:
        """
        Intel Broadwell, Skylake, and AMD GCN are Metal 31001-based GPUs

        Note: PatcherSupportPkg has never shipped a per-xnu_major
        "RenderBox-<xnu_major>" payload directory (e.g. "RenderBox-25"
        does not exist for macOS 26), so this previously raised
        "Failed to find .../RenderBox-<xnu_major>/.../default.metallib"
        during preflight checks. Upstream OCLP does not apply a
        RenderBox.framework override for the Metal 31001 family either,
        so this is intentionally a no-op.
        """
        return {}

    def _patches_metal_31001_metallibs(self) -> dict:
        return {}

    def patches(self) -> dict:
        """
        Dictionary of patches
        """
        return {
            **self._patches_metal_31001_common(),
            **self._patches_metal_31001_metallibs(),
        }

