"""
url.py: Generate URL for Software Update Catalog securely and deterministically.
"""

import logging
import plistlib
import os

from .constants import (
    SeedType,
    CatalogVersion,
    CatalogExtension
)
from ..support import network_handler


class CatalogURL:
    """
    Provides URL generation for Software Update Catalog

    Args:
        version   (CatalogVersion):    Version of macOS
        seed      (SeedType):          Seed type
        extension (CatalogExtension):  Extension for the catalog URL
    """
    def __init__(self,
                 version: CatalogVersion = CatalogVersion.TAHOE,
                 seed: SeedType = SeedType.PublicRelease,
                 extension: CatalogExtension = CatalogExtension.PLIST
                 ) -> None:
        # FIX: Establish primitive attributes first before triggering normalization logic calls
        self.version = version
        self.seed = seed
        self.extension = extension

        # Normalize values cleanly
        self.version = self._fix_version()
        self.seed = self._fix_seed_type()

    def _fix_seed_type(self) -> SeedType:
        """
        Fixes seed type for URL generation based on legacy OS catalog support constraints.
        """
        legacy_no_seeds = [
            CatalogVersion.LION,
            CatalogVersion.SNOW_LEOPARD,
            CatalogVersion.LEOPARD,
            CatalogVersion.TIGER
        ]

        # Pre-Mountain Lion lacked track seeds entirely
        if self.version in legacy_no_seeds:
            if self.seed != SeedType.PublicRelease:
                logger.warning(f"{self.seed.name} not supported for {self.version.name}, defaulting to PublicRelease")
                return SeedType.PublicRelease

        # Pre-Yosemite lacked PublicSeed/CustomerSeed; fallback to DeveloperSeed
        if self.version in [CatalogVersion.MAVERICKS, CatalogVersion.MOUNTAIN_LION]:
            if self.seed in [SeedType.PublicSeed, SeedType.CustomerSeed]:
                logger.warning(f"{self.seed.name} not supported for {self.version.name}, defaulting to DeveloperSeed")
                return SeedType.DeveloperSeed

        return self.seed

    def _fix_version(self) -> CatalogVersion:
        """
        Fixes version mapping for Big Sur branches.
        """
        if self.version == CatalogVersion.BIG_SUR:
            return CatalogVersion.BIG_SUR_LEGACY
        return self.version

    def _fetch_versions_for_url(self) -> list:
        """
        Fetches versions for URL generation sequentially based on strict historical ordering,
        independent of internal Enum definition mechanics.
        """
        # Explicit deterministic order tracking from newest known target down to historical roots
        ordered_versions = [
            CatalogVersion.TAHOE,
            CatalogVersion.SEQUOIA,
            CatalogVersion.SONOMA,
            CatalogVersion.VENTURA,
            CatalogVersion.MONTEREY,
            CatalogVersion.BIG_SUR_LEGACY,
            CatalogVersion.CATALINA,
            CatalogVersion.MOJAVE,
            CatalogVersion.HIGH_SIERRA,
            CatalogVersion.SIERRA,
            CatalogVersion.EL_CAPITAN,
            CatalogVersion.YOSEMITE,
            CatalogVersion.MAVERICKS,
            CatalogVersion.MOUNTAIN_LION,
            CatalogVersion.LION,
            CatalogVersion.SNOW_LEOPARD,
            CatalogVersion.LEOPARD,
            CatalogVersion.TIGER
        ]

        versions: list = []
        try:
            start_idx = ordered_versions.index(self.version)
        except ValueError:
            # Fallback if an unrecognized version is injected
            return []

        # Accumulate applicable historical catalog versions
        for variant in ordered_versions[start_idx:]:
            if variant in [CatalogVersion.BIG_SUR, CatalogVersion.TIGER]:
                continue
            versions.append(variant.value)

        if self.version == CatalogVersion.SNOW_LEOPARD:
            versions = versions[::-1]

        return versions

    def _construct_catalog_url(self) -> str:
        """
        Constructs the catalog URL dynamically based on target seeds and normalized platform states.
        """
        base_url = "https://swscan.apple.com/content/catalogs"

        if self.version == CatalogVersion.TIGER:
            base_url += "/index"
        else:
            base_url += "/others/index"

        if self.seed in [SeedType.DeveloperSeed, SeedType.PublicSeed, SeedType.CustomerSeed]:
            base_url += f"-{self.version.value}"
            if self.version == CatalogVersion.MAVERICKS and self.seed == SeedType.CustomerSeed:
                base_url += "publicseed"
            else:
                base_url += f"{self.seed.value}"

        # 10.10 and older architectures do not append version tracks for CustomerSeed streams
        legacy_customer_versions = [
            CatalogVersion.YOSEMITE,
            CatalogVersion.MAVERICKS,
            CatalogVersion.MOUNTAIN_LION,
            CatalogVersion.LION,
            CatalogVersion.SNOW_LEOPARD,
            CatalogVersion.LEOPARD
        ]

        if self.seed == SeedType.CustomerSeed and self.version in legacy_customer_versions:
            pass
        else:
            for version in self._fetch_versions_for_url():
                base_url += f"-{version}"

        if self.version != CatalogVersion.TIGER:
            base_url += ".merged-1"

        base_url += self.extension.value
        return base_url

    def catalog_url_to_seed(self, catalog_url: str) -> SeedType:
        """
        Converts a raw Catalog URL string to its corresponding SeedType safely.
        """
        if not catalog_url or not isinstance(catalog_url, str):
            return SeedType.PublicRelease

        normalized_url = catalog_url.lower()

        # FIX: Specific substrings take precedence to prevent false-positive matches
        if "customerseed" in normalized_url:
            return SeedType.CustomerSeed
        elif "beta" in normalized_url:
            return SeedType.PublicSeed
        elif "seed" in normalized_url:
            return SeedType.DeveloperSeed

        return SeedType.PublicRelease

    @property
    def url(self) -> str:
        """
        Generate URL for Software Update Catalog.
        """
        return self._construct_catalog_url()

    @property
    def url_contents(self) -> dict:
        """
        Fetches and parses remote plist URL contents safely. Returns an empty dictionary on failure.
        """
        try:
            response = network_handler.NetworkUtilities().get(self.url)
            if response and hasattr(response, "content") and response.content:
                return plistlib.loads(response.content)
        except Exception as e:
            logging.error(f"Failed to fetch or parse URL contents from {self.url}: {e}")

        return {}
