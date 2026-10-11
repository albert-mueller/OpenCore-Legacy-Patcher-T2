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
        """
        macOS 26 Tahoe introduces a WindowServer deadlock with AMD Legacy GCN, Polaris,
        and Intel Skylake GPUs due to incompatibilities between the legacy drivers
        and the Tahoe metallib format. Re-using the 3802 downgraded metallibs
        from MetallibSupportPkg resolves this.
        """
        # Intentionally a no-op now. This used to install the Sequoia-era MetallibSupportPkg
        # 3802 set on Tahoe for every Metal 31001 GPU (Broadwell, Skylake, GCN, Polaris, Vega,
        # Navi), which upstream never does. Upstream covers these GPUs on Tahoe with
        # TahoeGraphics (RenderBox 26.0-3802) plus the -25 driver bundles, and
        # LegacyMetal3802._patches_metal_3802_metallibs() returns {} on Tahoe anyway.
        return {}

    def patches(self) -> dict:
        """
        Dictionary of patches
        """
        return {
            **self._patches_metal_31001_common(),
            **self._patches_metal_31001_metallibs(),
        }
