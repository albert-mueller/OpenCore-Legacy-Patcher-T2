"""
package.py: Generate packages (Installer, Uninstaller, AutoPkg-Assets)
"""

import tempfile
import macos_pkg_builder
import plistlib
import macos_pkg_builder.distribution_pkg
from xml.etree import ElementTree as ET
import rich
from pathlib import Path

def _patched_generate_component_file(self) -> Path:
    bundle = None
    if self._pkg_file_structure:
        for source, destination in self._pkg_file_structure.items():
            if Path(source, "Contents", "Info.plist").exists():
                bundle = destination
                break
    if bundle is None:
        raise ValueError("No valid bundle found in the provided file structure.")
    contents = [{
        "BundleHasStrictIdentifier": False,
        "BundleIsRelocatable":       self._pkg_allow_relocation,
        "BundleIsVersionChecked":    True,
        "BundleOverwriteAction":     "upgrade",
        "RootRelativeBundlePath":    bundle,
    }]
    file = tempfile.NamedTemporaryFile(delete=False)
    plistlib.dump(contents, Path(file.name).open("wb"))
    return Path(file.name)

macos_pkg_builder.flat_pkg.FlatPackage._generate_component_file = _patched_generate_component_file


# Minimum macOS version the packages (Installer, Uninstaller, AutoPkg-Assets)
# may be installed on. macos_pkg_builder has no option for this, so the
# requirement is injected into the Distribution XML it generates. Installer.app
# and the `installer` CLI both refuse to install on older systems.
PKG_MINIMUM_MACOS: str = "10.13"

_original_prepare_distribution_file = macos_pkg_builder.distribution_pkg.DistributionPackage._prepare_distribution_file

def _patched_prepare_distribution_file(self, input_file) -> None:
    _original_prepare_distribution_file(self, input_file)

    tree = ET.parse(input_file.name)
    root = tree.getroot()
    for existing in root.findall("allowed-os-versions"):
        root.remove(existing)
    allowed = ET.Element("allowed-os-versions")
    ET.SubElement(allowed, "os-version", min=PKG_MINIMUM_MACOS)
    root.append(allowed)
    tree.write(input_file.name)

macos_pkg_builder.distribution_pkg.DistributionPackage._prepare_distribution_file = _patched_prepare_distribution_file

from opencore_legacy_patcher import constants

from .package_scripts import GenerateScripts


class GeneratePackage:
    """
    Generate BridgePatcher.pkg (and, on request, OpenCore-Patcher-T2.pkg)
    """

    def __init__(self, legacy_installer: bool = False) -> None:
        """
        Initialize

        legacy_installer: also build OpenCore-Patcher-T2.pkg next to BridgePatcher.pkg.
        Same app, same files; it only exists because builds from before the rename
        look for that asset name when checking for updates (see support/updates.py
        of those builds), and additionally leaves a compatibility link under the old
        bundle name so their built-in updater recognises the finished install.
        """
        self._legacy_installer = legacy_installer

        self._files = {
            f"./dist/{constants.APP_BUNDLE_NAME}": f"{constants.APP_INSTALL_DIRECTORY}/{constants.APP_BUNDLE_NAME}",
            "./ci_tooling/privileged_helper_tool/io.github.bridgepatcher.privileged-helper": "/Library/PrivilegedHelperTools/io.github.bridgepatcher.privileged-helper",
            # Background update check, see support/arguments.py _check_updates_handler()
            "./payloads/Launch Services/com.albert-mueller.opencore-legacy-patcher.update-check.plist": "/Library/LaunchAgents/com.albert-mueller.opencore-legacy-patcher.update-check.plist",
        }
        self._autopkg_files = {
            "./payloads/Launch Services/com.albert-mueller.opencore-legacy-patcher.auto-patch.plist": "/Library/LaunchAgents/com.albert-mueller.opencore-legacy-patcher.auto-patch.plist",
        }
        self._autopkg_files.update(self._files)

        # Every generated script lands in a NamedTemporaryFile(delete=False); track
        # them so generate() can clean up instead of leaving five files in /tmp per build.
        self._temp_files = []


    def _write_temp_script(self, contents: str) -> str:
        """
        Write a generated install script to a temporary file and register it for cleanup
        """
        _file = tempfile.NamedTemporaryFile(delete=False)
        self._temp_files.append(_file.name)
        with open(_file.name, "w") as f:
            f.write(contents)
        return _file.name


    @property
    def installer_packages(self) -> list:
        """
        Installer packages generate() builds, in build order (file names in ./dist)
        """
        _packages = [constants.INSTALLER_PKG_NAME]
        if self._legacy_installer:
            _packages.append(constants.LEGACY_INSTALLER_PKG_NAME)
        return _packages


    def _generate_installer_welcome(self, is_legacy: bool = False) -> str:
        """
        Generate Welcome message for installer PKG
        """
        _welcome = ""

        _welcome += "# Overview\n"
        _welcome += f"This package will install the BridgePatcher application (v{constants.Constants().patcher_version}) on your system."

        _welcome += "\n\nAdditionally, a shortcut for BridgePatcher will be added in the '/Applications' folder."
        _welcome += "\n\nThis package will not 'Build and Install OpenCore' or install any 'Root Patches' on your machine. If required, you can run BridgePatcher to install any patches you may need."
        _welcome += "\n\nBefore installing the patcher, I highly recommend to open Terminal and verify the SHA256 certificates using the shasum -a 256 command with the ones in this repository."
        _welcome += f"\n\nFor more information on BridgePatcher usage, see our [documentation]({constants.Constants().guide_link}) and [GitHub repository]({constants.Constants().repo_link})."
        _welcome += "\n\nA copy of LICENSE.txt, including the upstream BSD 4-Clause terms and the additional terms for this fork, is included in the application bundle at `Contents/Resources/LICENSE.txt`."
        _welcome += "\n\n"

        if is_legacy:
            _welcome += f"\n\nThis is the same installer as {constants.INSTALLER_PKG_NAME}, published under the app's former name so that versions from before the rename to BridgePatcher can still find and install this update. If you are installing manually, use {constants.INSTALLER_PKG_NAME} instead."
            _welcome += "\n\n"

        _welcome += "## Files Installed"
        _welcome += "\n\nInstallation of this package will add the following files to your system:"
        for value in self._files.values():
            _welcome += f"\n\n- `{value}`"
        if is_legacy:
            _welcome += f"\n\n- `{constants.APP_INSTALL_DIRECTORY}/{constants.LEGACY_APP_BUNDLE_NAME}` (link to {constants.APP_BUNDLE_NAME}, removed again by the next update)"

        return _welcome


    def _generate_uninstaller_welcome(self) -> str:
        """
        Generate Welcome message for uninstaller PKG
        """
        _welcome = ""

        _welcome += "# Application Uninstaller\n"
        _welcome += "This package will uninstall the BridgePatcher application and its Privileged Helper Tool from your system."
        _welcome += "\n\n"
        _welcome += "This will not remove any root patches or OpenCore configurations that you may have installed using BridgePatcher."
        _welcome += "\n\n"
        _welcome += f"For more information on BridgePatcher, see our [documentation]({constants.Constants().guide_link}) and [GitHub repository]({constants.Constants().repo_link})."

        return _welcome


    def _generate_autopkg_welcome(self) -> str:
        """
        Generate Welcome message for AutoPkg-Assets PKG
        """
        _welcome = ""

        _welcome += "# PLEASE DO NOT RUN AUTOPKG-ASSETS MANUALLY!\n\n"
        _welcome += "## THIS WILL CORRUPT THE OPERATING SYSTEM!\n\n"
        _welcome += f"This package is intented to be used only by the Patcher application itslef, not run manually by a user. Download {constants.INSTALLER_PKG_NAME} on the Github Repository.\n\n"
        _welcome += f"[BridgePatcher GitHub Release]({constants.Constants().repo_link})"

        return _welcome


    def generate(self) -> None:
        """
        Generate the uninstaller, installer(s) and AutoPkg-Assets packages
        """
        try:
            self._generate_packages()
        finally:
            for _file in self._temp_files:
                Path(_file).unlink(missing_ok=True)
            self._temp_files = []


    def _build_package(self, name: str, **kwargs) -> None:
        """
        Build a single package and fail loudly if it did not succeed

        macos_pkg_builder returns False rather than raising, and the previous
        "assert Packages(...).build() is True" both vanished under 'python -O' -
        turning a failed build into a silent success - and gave no indication of
        which of the three packages actually failed when it did fire.
        """
        rich.print(f"Generating {name}")
        if macos_pkg_builder.Packages(**kwargs).build() is not True:
            raise RuntimeError(f"Failed to build {name}")


    def _generate_packages(self) -> None:
        """
        Generate the uninstaller, installer and AutoPkg-Assets packages
        """
        self._build_package(
            "OpenCore-Patcher-Uninstaller.pkg",
            pkg_output="./dist/OpenCore-Patcher-Uninstaller.pkg",
            pkg_bundle_id="com.dortania.opencore-legacy-patcher-uninstaller",
            pkg_version=constants.Constants().patcher_version,
            pkg_background="./ci_tooling/pkg_assets/PkgBackground-Uninstaller.png",
            pkg_preinstall_script=self._write_temp_script(GenerateScripts().uninstall()),
            pkg_as_distribution=True,
            pkg_title="BridgePatcher Uninstaller",
            pkg_welcome=self._generate_uninstaller_welcome(),
        )

        # A leftover from an earlier build with --legacy-installer would otherwise
        # be hashed into certificates.txt and could end up on a release by accident.
        if not self._legacy_installer:
            Path("./dist", constants.LEGACY_INSTALLER_PKG_NAME).unlink(missing_ok=True)

        for _package in self.installer_packages:
            _is_legacy = _package == constants.LEGACY_INSTALLER_PKG_NAME
            self._build_package(
                _package,
                pkg_output=f"./dist/{_package}",
                # Same receipt identifier for both: they install the same product,
                # so either one upgrades an install made by the other.
                pkg_bundle_id="com.dortania.opencore-legacy-patcher-t2",
                pkg_version=constants.Constants().patcher_version,
                pkg_allow_relocation=False,
                pkg_as_distribution=True,
                pkg_background="./ci_tooling/pkg_assets/PkgBackground-Installer.png",
                pkg_preinstall_script=self._write_temp_script(GenerateScripts().preinstall_pkg()),
                pkg_postinstall_script=self._write_temp_script(GenerateScripts().postinstall_pkg(legacy_compatibility=_is_legacy)),
                pkg_file_structure=self._files,
                pkg_title=constants.APP_NAME,
                pkg_welcome=self._generate_installer_welcome(is_legacy=_is_legacy),
            )

        self._build_package(
            "AutoPkg-Assets-T2.pkg",
            pkg_output="./dist/AutoPkg-Assets-T2.pkg",
            pkg_bundle_id="com.dortania.pkg.AutoPkg-Assets",
            pkg_version=constants.Constants().patcher_version,
            pkg_allow_relocation=False,
            pkg_as_distribution=True,
            pkg_background="./ci_tooling/pkg_assets/PkgBackground-AutoPkg.png",
            pkg_preinstall_script=self._write_temp_script(GenerateScripts().preinstall_autopkg()),
            pkg_postinstall_script=self._write_temp_script(GenerateScripts().postinstall_autopkg()),
            pkg_file_structure=self._autopkg_files,
            pkg_title="AutoPkg Assets",
            pkg_welcome=self._generate_autopkg_welcome(),
        )
