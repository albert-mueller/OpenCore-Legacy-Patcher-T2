"""
products.py: Parse products from Software Update Catalog securely and cleanly
"""

import re
import plistlib
import logging
import packaging.version
import xml.etree.ElementTree as ET

from pathlib import Path
from functools import cached_property

from .url import CatalogURL
from .constants import CatalogVersion, SeedType
from ..support import network_handler

logger = logging.getLogger(__name__)


class CatalogProducts:
    """
    Args:
        catalog                       (dict): Software Update Catalog (contents of CatalogURL's URL)
        install_assistants_only       (bool): Only list InstallAssistant products
        only_vmm_install_assistants   (bool): Only list VMM-x86_64-compatible InstallAssistant products
        max_install_assistant_version (CatalogVersion): Maximum InstallAssistant version to list
    """
    def __init__(self,
                 catalog: dict,
                 install_assistants_only: bool = True,
                 only_vmm_install_assistants: bool = True,
                 max_install_assistant_version: CatalogVersion = CatalogVersion.TAHOE
                ) -> None:
        self.catalog: dict = catalog
        self.ia_only: bool = install_assistants_only
        self.vmm_only: bool = only_vmm_install_assistants
        self.max_ia_version = packaging.version.parse(f"{max_install_assistant_version.value}.99.99")
        self.max_ia_catalog: CatalogVersion = max_install_assistant_version

    def _legacy_parse_info_plist(self, data: dict) -> dict:
        """
        Legacy version of parsing for installer details through Info.plist
        """
        try:
            asset_props = data["MobileAssetProperties"]
            if not all(k in asset_props for k in ["SupportedDeviceModels", "OSVersion", "Build"]):
                return {}
        except KeyError:
            return {}

        # Ensure Apple Silicon specific Installers are not listed if vmm_only is requested
        if "VMM-x86_64" not in asset_props["SupportedDeviceModels"]:
            if self.vmm_only:
                return {"Missing VMM Support": True}

        version = asset_props["OSVersion"]
        build = asset_props["Build"]

        if not version or not build:
            return {}

        catalog = ""
        try:
            catalog = asset_props["BridgeVersionInfo"]["CatalogURL"]
        except KeyError:
            pass

        return {
            "Version": version,
            "Build": build,
            "Catalog": CatalogURL().catalog_url_to_seed(catalog),
        }

    def _parse_mobile_asset_plist(self, data: dict) -> dict:
        """
        Parses the MobileAsset plist for installer details
        """
        if "Assets" not in data or not isinstance(data["Assets"], list):
            return {}

        _does_support_vmm = False
        for entry in data["Assets"]:
            if not all(k in entry for k in ["SupportedDeviceModels", "OSVersion", "Build"]):
                continue

            if "VMM-x86_64" not in entry["SupportedDeviceModels"]:
                if self.vmm_only:
                    continue

            _does_support_vmm = True
            build = entry["Build"]
            version = entry["OSVersion"]

            catalog_url = ""
            try:
                catalog_url = entry["BridgeVersionInfo"]["CatalogURL"]
            except KeyError:
                pass

            return {
                "Version": version,
                "Build": build,
                "Catalog": CatalogURL().catalog_url_to_seed(catalog_url),
            }

        if not _does_support_vmm and self.vmm_only:
            return {"Missing VMM Support": True}

        return {}

    def _parse_english_distributions(self, data: bytes) -> dict:
        """
        Resolve Title, Build and Version from the English distribution file
        """
        plist_contents = None
        if data.startswith(b"<?xml"):
            try:
                plist_contents = plistlib.loads(data)
            except Exception:
                pass

        xml_contents = None
        try:
            xml_contents = ET.fromstring(data)
        except ET.ParseError:
            pass

        _product_map = {
            "Title": None,
            "Build": None,
            "Version": None,
        }

        if isinstance(plist_contents, dict):
            for b_key in ["macOSProductBuildVersion", "BUILD"]:
                if b_key in plist_contents:
                    _product_map["Build"] = plist_contents[b_key]
            for v_key in ["macOSProductVersion", "VERSION"]:
                if v_key in plist_contents:
                    _product_map["Version"] = plist_contents[v_key]

        if xml_contents is not None:
            title_element = xml_contents.find(".//title")
            item_title = title_element.text if title_element is not None else None

            if item_title in ["SU_TITLE", "MANUAL_TITLE", "MAN_TITLE"]:
                try:
                    title_search = re.search(r'"SU_TITLE"\s*=\s*"(.*)";', data.decode("utf-8"))
                    if title_search:
                        item_title = title_search.group(1)
                except Exception:
                    pass

            _product_map["Title"] = item_title

        return _product_map

    def _build_installer_name(self, version: str, catalog: SeedType) -> str:
        """
        Builds the installer name based on the version and catalog
        """
        try:
            marketing_name = CatalogVersion(version.split(".")[0]).name
        except ValueError:
            marketing_name = "Unknown"

        marketing_name = marketing_name.replace("_", " ")
        marketing_name = "macOS " + " ".join([word.capitalize() for word in marketing_name.split()])

        if catalog in [SeedType.DeveloperSeed, SeedType.PublicSeed, SeedType.CustomerSeed]:
            marketing_name += " Beta"

        return marketing_name

    def _list_latest_installers_only(self, products: list) -> list:
        """
        List only the latest installers per macOS version safely via functional comprehension mappings.
        """
        supported_versions = []
        did_find_latest = False

        for version in CatalogVersion:
            if not did_find_latest:
                if version != self.max_ia_catalog:
                    continue
                did_find_latest = True

            supported_versions.append(version)
            if len(supported_versions) == 4:
                break

        supported_versions = supported_versions[::-1]
        if not supported_versions:
            return []

        # Track the latest stable parsed version for each supported release string boundary
        version_thresholds = {}
        for version in supported_versions:
            _latest_stable_version = packaging.version.parse("0.0.0")
            for installer in products:
                if not installer.get("Version") or not installer["Version"].startswith(version.value):
                    continue
                if installer.get("Catalog") in [SeedType.CustomerSeed, SeedType.DeveloperSeed, SeedType.PublicSeed]:
                    continue
                try:
                    parsed_v = packaging.version.parse(installer["Version"])
                    if parsed_v > _latest_stable_version:
                        _latest_stable_version = parsed_v
                except packaging.version.InvalidVersion:
                    pass
            version_thresholds[version.value] = _latest_stable_version

        # Filter out outdated files without mutating the active loop collection sequence
        products_filtered = []
        for installer in products:
            v_str = installer.get("Version")
            if not v_str:
                continue

            matching_prefix = next((prefix for prefix in version_thresholds if v_str.startswith(prefix)), None)
            if matching_prefix:
                try:
                    parsed_v = packaging.version.parse(v_str)
                    if parsed_v < version_thresholds[matching_prefix]:
                        continue
                    # Strip beta installers if a stable release threshold has already been established
                    if version_thresholds[matching_prefix] != packaging.version.parse("0.0.0") and \
                       installer.get("Catalog") in [SeedType.CustomerSeed, SeedType.DeveloperSeed, SeedType.PublicSeed]:
                        continue
                except packaging.version.InvalidVersion:
                    pass
            products_filtered.append(installer)

        # Consolidate and deduplicate identical versions
        version_map = {}
        for installer in products_filtered:
            version = installer.get("Version")
            post_date = installer.get("PostDate", "")
            if not version:
                continue
            if version not in version_map or post_date > version_map[version].get("PostDate", ""):
                version_map[version] = installer

        final_products = list(version_map.values())

        # Exclude End-of-Life asset definitions
        eol_threshold = supported_versions[0].value
        final_products = [
            inst for inst in final_products
            if inst.get("Version", "0.0.0").split(".")[0] >= eol_threshold
        ]

        return final_products

    @cached_property
    def products(self) -> list:
        """
        Returns a list of products from the sucatalog
        """
        catalog = self.catalog
        if "Products" not in catalog:
            return []

        _products = []

        for product in catalog["Products"]:
            product_entry = catalog["Products"][product]

            if self.ia_only:
                try:
                    meta = product_entry["ExtendedMetaInfo"]
                    if "SharedSupport" not in meta["InstallAssistantPackageIdentifiers"]:
                        continue
                except KeyError:
                    continue

            _product_map = {
                "ProductID": product,
                "PostDate": product_entry.get("PostDate"),
                "Title": None,
                "Build": None,
                "Version": None,
                "Catalog": None,
            }

            if "Packages" in product_entry:
                if not self.ia_only:
                    _product_map["Packages"] = product_entry["Packages"]

                for package in product_entry["Packages"]:
                    if "URL" not in package:
                        continue

                    pkg_name = Path(package["URL"]).name
                    if pkg_name == "InstallAssistant.pkg":
                        _product_map["InstallAssistant"] = {
                            "URL": package["URL"],
                            "Size": package.get("Size"),
                            "IntegrityDataURL": package.get("IntegrityDataURL"),
                            "IntegrityDataSize": package.get("IntegrityDataSize")
                        }

                    if pkg_name not in ["Info.plist", "com_apple_MobileAsset_MacSoftwareUpdate.plist"]:
                        continue

                    net_obj = network_handler.NetworkUtilities().get(package["URL"])
                    if net_obj is None:
                        continue

                    try:
                        plist_contents = plistlib.loads(net_obj.content)
                    except plistlib.InvalidFileException:
                        continue

                    if plist_contents:
                        if pkg_name == "Info.plist":
                            result = self._legacy_parse_info_plist(plist_contents)
                        else:
                            result = self._parse_mobile_asset_plist(plist_contents)

                        if result == {"Missing VMM Support": True}:
                            _product_map = {}
                            break

                        _product_map.update(result)

            if not _product_map:
                continue

            if _product_map["Version"] is not None:
                _product_map["Title"] = self._build_installer_name(_product_map["Version"], _product_map["Catalog"])

            # Fall back to English distribution metadata parsing steps
            if _product_map["Version"] is None:
                url = None
                if "Distributions" in product_entry:
                    distros = product_entry["Distributions"]
                    url = distros.get("English") or distros.get("en")

                if url is not None:
                    net_obj = network_handler.NetworkUtilities().get(url)
                    if net_obj is not None:
                        _product_map.update(self._parse_english_distributions(net_obj.content))

                if _product_map["Version"] is None and "ServerMetadataURL" in product_entry:
                    net_obj = network_handler.NetworkUtilities().get(product_entry["ServerMetadataURL"])
                    if net_obj is not None:
                        server_metadata_plist = {}
                        try:
                            server_metadata_plist = plistlib.loads(net_obj.content)
                        except plistlib.InvalidFileException:
                            pass

                        if "CFBundleShortVersionString" in server_metadata_plist:
                            _product_map["Version"] = server_metadata_plist["CFBundleShortVersionString"]

            if _product_map["Version"] is not None:
                if self.ia_only:
                    try:
                        if packaging.version.parse(_product_map["Version"]) > self.max_ia_version:
                            continue
                    except packaging.version.InvalidVersion:
                        pass

            if _product_map["Build"] is not None and "InstallAssistant" in _product_map:
                try:
                    _product_map["InstallAssistant"]["XNUMajor"] = int(_product_map["Build"][:2])
                except ValueError:
                    pass

            if _product_map["Version"] is None:
                _product_map["Version"] = "0.0.0"

            _products.append(_product_map)

        try:
            _products = sorted(_products, key=lambda x: packaging.version.parse(x["Version"]))
        except Exception:
            _products = sorted(_products, key=lambda x: x["Version"])

        return _products

    @cached_property
    def latest_products(self) -> list:
        """
        Returns a list of the latest products from the sucatalog
        """
        return self._list_latest_installers_only(self.products)
