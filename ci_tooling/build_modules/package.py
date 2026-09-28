"""
package.py: Generate packages (Installer, Uninstaller, AutoPkg-Assets)
"""

import tempfile
import macos_pkg_builder
import plistlib
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

from opencore_legacy_patcher import constants

from .package_scripts import GenerateScripts


class GeneratePackage:
    """
    Generate OpenCore-Patcher-T2.pkg
    """

    def __init__(self) -> None:
        """
        Initialize
        """
        self._files = {
            "./dist/OpenCore-Patcher-T2.app": "/Library/Application Support/albert-mueller/OpenCore-Patcher-T2/OpenCore-Patcher-T2.app",
            "./ci_tooling/privileged_helper_tool/com.albert-mueller.opencore-patcher-t2.privileged-helper": "/Library/PrivilegedHelperTools/com.albert-mueller.opencore-patcher-t2.privileged-helper",
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


    def _generate_installer_welcome(self) -> str:
        """
        Generate Welcome message for installer PKG
        """
        _welcome = ""

        _welcome += "# Overview\n"
        _welcome += f"This package will install the OpenCore Legacy Patcher T2 application (v{constants.Constants().patcher_version}) on your system."

        _welcome += "\n\nAdditionally, a shortcut for OpenCore Legacy Patcher T2 will be added in the '/Applications' folder."
        _welcome += "\n\nThis package will not 'Build and Install OpenCore' or install any 'Root Patches' on your machine. If required, you can run OpenCore Legacy Patcher T2 to install any patches you may need."
        _welcome += "\n\nBefore installing the patcher, I highly recommend to open Terminal and verify the SHA256 certificates using the shasum -a 256 command with the ones in this repository."
        _welcome += f"\n\nFor more information on OpenCore Legacy Patcher T2 usage, see our [documentation]({constants.Constants().guide_link}) and [GitHub repository]({constants.Constants().repo_link})."
        _welcome += "\n\n"

        _welcome += "## Files Installed"
        _welcome += "\n\nInstallation of this package will add the following files to your system:"
        for value in self._files.values():
            _welcome += f"\n\n- `{value}`"

        return _welcome


    def _generate_uninstaller_welcome(self) -> str:
        """
        Generate Welcome message for uninstaller PKG
        """
        _welcome = ""

        _welcome += "# Application Uninstaller\n"
        _welcome += "This package will uninstall the OpenCore Legacy Patcher T2 application and its Privileged Helper Tool from your system."
        _welcome += "\n\n"
        _welcome += "This will not remove any root patches or OpenCore configurations that you may have installed using OpenCore Legacy Patcher T2."
        _welcome += "\n\n"
        _welcome += f"For more information on OpenCore Legacy Patcher T2, see our [documentation]({constants.Constants().guide_link}) and [GitHub repository]({constants.Constants().repo_link})."

        return _welcome


    def _generate_autopkg_welcome(self) -> str:
        """
        Generate Welcome message for AutoPkg-Assets PKG
        """
        _welcome = ""

        _welcome += "# PLEASE DO NOT RUN AUTOPKG-ASSETS MANUALLY!\n\n"
        _welcome += "## THIS WILL CORRUPT THE OPERATING SYSTEM!\n\n"
        _welcome += "This package is intented to be used only by the Patcher application itslef, not run manually by a user. Download the OpenCore-Patcher-T2.pkg on the Github Repository.\n\n"
        _welcome += f"[OpenCore Legacy Patcher T2 GitHub Release]({constants.Constants().repo_link})"

        return _welcome


    def generate(self) -> None:
        """
        Generate OpenCore-Patcher-T2.pkg
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
            pkg_title="OpenCore Legacy Patcher T2 Uninstaller",
            pkg_welcome=self._generate_uninstaller_welcome(),
        )

        self._build_package(
            "OpenCore-Patcher-T2.pkg",
            pkg_output="./dist/OpenCore-Patcher-T2.pkg",
            pkg_bundle_id="com.dortania.opencore-legacy-patcher-t2",
            pkg_version=constants.Constants().patcher_version,
            pkg_allow_relocation=False,
            pkg_as_distribution=True,
            pkg_background="./ci_tooling/pkg_assets/PkgBackground-Installer.png",
            pkg_preinstall_script=self._write_temp_script(GenerateScripts().preinstall_pkg()),
            pkg_postinstall_script=self._write_temp_script(GenerateScripts().postinstall_pkg()),
            pkg_file_structure=self._files,
            pkg_title="OpenCore Legacy Patcher T2",
            pkg_welcome=self._generate_installer_welcome(),
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
