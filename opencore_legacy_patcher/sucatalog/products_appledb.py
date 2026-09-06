"""
products.py: Parse and validate products from AppleDB API pipelines securely.
"""

import datetime
import hashlib
import logging
import os
import zoneinfo

import packaging.version
from functools import cached_property

from opencore_legacy_patcher import constants
from opencore_legacy_patcher.datasets.os_data import os_data
from ..support import network_handler

logger = logging.getLogger(__name__)

APPLEDB_API_URL = "https://api.appledb.dev/ios/macOS/main.json"


class AppleDBProducts:
    """
    Fetch and sanitize InstallAssistants from AppleDB API pipelines.
    """

    def __init__(
        self,
        global_constants: constants.Constants,
        max_install_assistant_version: os_data = os_data.tahoe,
    ) -> None:
        self.constants: constants.Constants = global_constants
        self.max_ia: os_data = max_install_assistant_version
        self.data = []

        # Allow environment configuration overrides to support isolated testing or offline mocks
        api_url = os.environ.get("APPLEDB_API_URL_OVERRIDE", APPLEDB_API_URL)

        try:
            response = network_handler.NetworkUtilities().get(
                api_url, 
                headers={"User-Agent": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6.2 Safari/605.1.15/OpenCoreLegacyPatcherT2/{self.constants.patcher_version}"}
            )
            if response and hasattr(response, "json"):
                self.data = response.json()
            else:
                self.data = []
        except Exception as e:
            self.data = []
            logger.error(f"Failed to fetch AppleDB API response: {e}")

    def _build_installer_name(self, xnu_major: int, beta: bool) -> str:
        """
        Builds the installer name safely based on the XNU target map matching values.
        """
        try:
            # Look up matching version from os_data enum integer values safely
            resolved_os = os_data(xnu_major)
            marketing_name = resolved_os.name.replace('_', ' ').title()
            return f"macOS {marketing_name}{' Beta' if beta else ''}"
        except ValueError:
            return f"macOS{' Beta' if beta else ''}"

    def _list_latest_installers_only(self, products: list) -> list:
        """
        List only the latest installers per macOS version, capped at n-3.
        """
        if not products:
            return []

        # FIXED: Explicitly extract the `.value` integer from the os_data Enum to prevent type errors
        max_val = self.max_ia.value
        min_val = max_val - 3

        supported_versions = {}
        for i in range(min_val, max_val + 1):
            try:
                resolved_enum = os_data(i)
                supported_versions[resolved_enum] = [
                    v for v in products 
                    if isinstance(v, dict) and v.get("InstallAssistant", {}).get("XNUMajor") == i
                ]
            except ValueError:
                continue

        final_list = []
        for enum_version, versions in supported_versions.items():
            if not versions:
                continue
            
            # Sort constraints: Stable releases prioritized over Betas, then sort descending by version
            versions.sort(
                key=lambda v: (
                    not v.get("Beta", False), 
                    packaging.version.parse(v.get("RawVersion", "0.0.0"))
                ), 
                reverse=True
            )
            
            # Extract the absolute freshest matching platform generation target entry safely
            final_list.append(next(iter(versions)))

        return final_list

    @cached_property
    def products(self) -> list:
        """
        Safely maps, verifies, and deduplicates applicable assets parsed out of AppleDB payload blocks.
        """
        # VULNERABILITY FIX: Prevent type confusion or traversal issues over malformed JSON structures
        if not self.data or not isinstance(self.data, list):
            logger.error("AppleDB input data is missing or not a valid list payload.")
            return []

        _products = []

        for firmware in self.data:
            if not isinstance(firmware, dict):
                continue
            
            if firmware.get("internal") or firmware.get("sdk") or firmware.get("rsr"):
                continue

            if "deviceMap" not in firmware or "MacPro7,1" not in firmware["deviceMap"]:
                continue

            # VULNERABILITY FIX: Strict schema verification before unpacking downstream structural variables
            if not firmware.get("build") or not firmware.get("version"):
                continue

            firmware["raw_version"] = firmware["version"].partition(" ")[0]

            # VULNERABILITY FIX: Enforce defensive integer conversion ranges against arbitrary strings
            try:
                xnu_major = int(firmware["build"][:2])
            except (ValueError, TypeError, IndexError):
                continue

            # Logic boundary check against enum value constraints
            if xnu_major > self.max_ia.value:
                continue

            beta = bool(firmware.get("beta") or firmware.get("rc"))

            details = {
                "PostDate": None,
                "Title": self._build_installer_name(xnu_major, beta),
                "Build": firmware["build"],
                "RawVersion": firmware["raw_version"],
                "Version": firmware["version"],
                "Beta": beta,
                "InstallAssistant": {"XNUMajor": xnu_major},
            }

            if firmware.get("released"):
                try:
                    base_date = datetime.datetime.fromisoformat(firmware["released"])
                    details["PostDate"] = base_date.replace(
                        hour=10, minute=0, second=0, microsecond=0,
                        tzinfo=zoneinfo.ZoneInfo("America/Los_Angeles")
                    )
                except Exception:
                    pass

            has_valid_source = False
            for source in firmware.get("sources", []):
                if not isinstance(source, dict) or source.get("type") != "installassistant":
                    continue

                if "deviceMap" not in source or "MacPro7,1" not in source["deviceMap"]:
                    continue

                for link in source.get("links", []):
                    if not isinstance(link, dict) or not link.get("active") or not link.get("url"):
                        continue

                    # Validate URL string integrity using the network utility layers
                    if not network_handler.NetworkUtilities(link["url"]).validate_link():
                        continue

                    details["InstallAssistant"] |= {
                        "URL": link["url"],
                        "Size": source.get("size", 0),
                        "Checksum": source.get("hashes"),
                    }
                    has_valid_source = True
                    break
                
                if has_valid_source:
                    break

            if not has_valid_source or "URL" not in details["InstallAssistant"]:
                continue

            _products.append(details)

        # Sort: Stable installers first, then grouped by build strings to assist target tracking
        _products = sorted(_products, key=lambda x: (x.get("Beta", False), x.get("Build", "")))
        _deduplicated_products = []
        _seen_builds = set()

        for product in _products:
            build_str = product.get("Build")
            # Deduplication flaw fix: Drop beta candidate builds if a stable equivalent is verified
            if product.get("Beta") and build_str in _seen_builds:
                continue
            _deduplicated_products.append(product)
            if build_str:
                _seen_builds.add(build_str)

        # Re-sort into standard version processing arrays
        _deduplicated_products = sorted(
            _deduplicated_products, 
            key=lambda x: (
                packaging.version.parse(x.get("RawVersion", "0.0.0")), 
                x.get("Build", ""), 
                not x.get("Beta", False)
            )
        )

        return _deduplicated_products

    @cached_property
    def latest_products(self) -> list:
        """
        Returns a list of the latest filtered products.
        """
        return self._list_latest_installers_only(self.products)

    def checksum_for_product(self, product: dict):
        """
        Returns the checksum string and cryptographic matching algorithm pair for a given product payload.
        """
        HASH_TO_ALGO = {
            "md5": hashlib.md5, 
            "sha1": hashlib.sha1, 
            "sha2-256": hashlib.sha256, 
            "sha2-512": hashlib.sha512
        }

        # VULNERABILITY FIX: Prevent attribute lookup failures on unexpected input signatures
        if not isinstance(product, dict):
            return None, None

        checksum_map = product.get("InstallAssistant", {}).get("Checksum")
        if not isinstance(checksum_map, dict):
            return None, None

        for algo, hash_func in HASH_TO_ALGO.items():
            if algo in checksum_map:
                return checksum_map[algo], hash_func()

        return None, None
