"""
install.py: Install the auto patcher launch services
"""

import hashlib
import logging
import os
import plistlib
import subprocess
import sys
import tempfile

from pathlib import Path

from ... import constants

from ...volume import generate_copy_arguments

from ...support import (
    utilities,
    subprocess_wrapper
)


class InstallAutomaticPatchingServices:
    """
    Install the auto patcher launch services
    """

    # Where the persistent copy of the patcher lives, as installed by the PKG
    # (see ci_tooling/build_modules/package.py)
    _PATCHER_INSTALL_DIRECTORY: str = "/Library/Application Support/albert-mueller/OpenCore-Patcher-T2"

    # Where older PKGs installed it: Dortania's own directory, shared with their
    # patcher. Only read to keep existing installs working and to clean up.
    _LEGACY_PATCHER_INSTALL_DIRECTORY: str = "/Library/Application Support/Dortania"


    def __init__(self, global_constants: constants.Constants):
        self.constants: constants.Constants = global_constants
        self._staging_directory: Path = None


    @staticmethod
    def _bundle_is_ours(bundle_path: Path) -> bool:
        """
        Whether an app bundle at a shared path belongs to this fork

        Pre-rebrand builds installed as OpenCore-Patcher.app, the exact name and
        location Dortania's patcher uses. Ownership is decided by what the bundle
        declares about itself; anything ambiguous is treated as not ours.
        """

        try:
            info = plistlib.load((bundle_path / "Contents" / "Info.plist").open("rb"))
        except Exception:
            return False

        if "T2" in info.get("CFBundleName", ""):
            return True
        if "Albert" in info.get("NSHumanReadableCopyright", ""):
            return True
        return False


    def _resolve_patcher_binary(self) -> str:
        """
        Resolve the patcher binary that the launch services should invoke

        The app bundle was renamed to OpenCore-Patcher-T2.app with the T2 rebrand,
        while the plists in payloads/Launch Services still referenced the old
        OpenCore-Patcher.app path. launchd then had nothing to exec, so auto-patch,
        macos-update and os-caching all silently did nothing on a PKG install.

        Resolve against what is actually on disk instead of hardcoding either name,
        so both the current PKG layout and the older ZIP layout keep working.
        """
        # The executable itself was later renamed to OpenCore-Patcher-T2 as well,
        # so a T2 bundle installed by an older build still carries the old name.
        # The current install directory wins; the legacy (Dortania) directory is
        # only used by installs from before the move that haven't been updated.
        for directory, bundle, executable in (
            (self._PATCHER_INSTALL_DIRECTORY,        "OpenCore-Patcher-T2.app", "OpenCore-Patcher-T2"),
            (self._LEGACY_PATCHER_INSTALL_DIRECTORY, "OpenCore-Patcher-T2.app", "OpenCore-Patcher-T2"),
            (self._LEGACY_PATCHER_INSTALL_DIRECTORY, "OpenCore-Patcher-T2.app", "OpenCore-Patcher"),
            (self._LEGACY_PATCHER_INSTALL_DIRECTORY, "OpenCore-Patcher.app",    "OpenCore-Patcher"),
        ):
            bundle_path = Path(directory) / bundle
            binary = bundle_path / "Contents" / "MacOS" / executable
            if not binary.exists():
                continue
            # Dortania's patcher installs to this same directory under the old
            # name, so the legacy path is only ours if the bundle says so.
            # Pointing our launch services at their binary would hand them a
            # --auto_patch invocation they never asked for.
            if bundle == "OpenCore-Patcher.app" and not self._bundle_is_ours(bundle_path):
                logging.info(f"- Ignoring {bundle}, not one of ours")
                continue
            return str(binary)

        # Nothing installed yet (ex. services written before the app is copied):
        # fall back to the path the PKG will create.
        return str(Path(self._PATCHER_INSTALL_DIRECTORY) / "OpenCore-Patcher-T2.app" / "Contents" / "MacOS" / "OpenCore-Patcher-T2")


    def _stage_service(self, service: str) -> str:
        """
        Point a launch service at the patcher binary actually present on this host

        Returns the path to use as the copy source: the original payload when no
        change is needed, otherwise a rewritten copy in a temporary directory.
        """
        try:
            service_plist = plistlib.load(open(service, "rb"))
        except Exception as e:
            logging.info(f"  - Failed to parse {Path(service).name}, using as-is: {e}")
            return service

        arguments = service_plist.get("ProgramArguments", [])
        # Services that don't invoke the patcher (ex. the RSRMonitor's /bin/rm) are left alone
        if not arguments or not str(arguments[0]).startswith((self._PATCHER_INSTALL_DIRECTORY, self._LEGACY_PATCHER_INSTALL_DIRECTORY)):
            return service

        resolved_binary = self._resolve_patcher_binary()
        if arguments[0] == resolved_binary:
            return service

        logging.info(f"  - Updating binary path: {resolved_binary}")
        service_plist["ProgramArguments"][0] = resolved_binary

        try:
            if self._staging_directory is None:
                self._staging_directory = Path(tempfile.mkdtemp(prefix="oclp-launch-services-"))
            staged_service = self._staging_directory / Path(service).name
            plistlib.dump(service_plist, staged_service.open("wb"))
        except Exception as e:
            logging.info(f"  - Failed to stage {Path(service).name}, using as-is: {e}")
            return service

        return str(staged_service)


    # Launch services used to be installed under Dortania's identifier, which meant
    # a side-by-side install of their patcher overwrote ours and vice versa. They
    # are now namespaced; these are the old paths, kept only for cleanup.
    _LEGACY_LAUNCH_SERVICES: list = [
        "/Library/LaunchAgents/com.dortania.opencore-legacy-patcher.auto-patch.plist",
        "/Library/LaunchDaemons/com.dortania.opencore-legacy-patcher.macos-update.plist",
        "/Library/LaunchDaemons/com.dortania.opencore-legacy-patcher.rsr-monitor.plist",
        "/Library/LaunchDaemons/com.dortania.opencore-legacy-patcher.os-caching.plist",
    ]


    def _remove_legacy_launch_services(self) -> None:
        """
        Remove pre-rename launch services that belong to us

        Dortania's patcher installs services at these exact paths, so ownership is
        decided by what the service actually launches: only those pointing at our
        app bundle are ours to remove. Anything else is left alone.
        """

        for service in self._LEGACY_LAUNCH_SERVICES:
            if not Path(service).exists():
                continue

            try:
                service_plist = plistlib.load(Path(service).open("rb"))
                program = service_plist.get("ProgramArguments", [""])[0]
            except Exception as e:
                logging.info(f"- Failed to read {Path(service).name}, leaving in place: {e}")
                continue

            if "OpenCore-Patcher-T2.app" not in program:
                logging.info(f"- Leaving {Path(service).name}, not ours")
                continue

            logging.info(f"- Removing legacy service: {Path(service).name}")
            label = Path(service).stem
            if "/LaunchAgents/" in service:
                # Agents live in the console user's GUI domain, not root's. The
                # patcher can run elevated, so derive the UID from /dev/console
                # rather than from our own process.
                try:
                    uid = os.stat("/dev/console").st_uid
                except OSError:
                    uid = os.getuid()
                domain = f"gui/{uid}"
            else:
                domain = "system"
            # Best effort: the service may not be loaded, which bootout reports as an error
            subprocess_wrapper.run_as_root(
                ["/bin/launchctl", "bootout", f"{domain}/{label}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            subprocess_wrapper.run_as_root_and_verify(
                ["/bin/rm", service], stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )


    def _remove_moved_app_bundle(self) -> None:
        """
        Remove our copy left in Dortania's directory after the install location moved

        OpenCore-Patcher-T2.app is a name only this fork uses, so the bundle is ours.
        It is only removed once the new location actually holds the app: until the
        updated PKG has run, launch services may still be executing the old copy.
        """

        moved_bundle = Path(self._LEGACY_PATCHER_INSTALL_DIRECTORY) / "OpenCore-Patcher-T2.app"
        if not moved_bundle.exists():
            return

        if not (Path(self._PATCHER_INSTALL_DIRECTORY) / "OpenCore-Patcher-T2.app").exists():
            logging.info(f"- Keeping {moved_bundle}, nothing installed at {self._PATCHER_INSTALL_DIRECTORY} yet")
            return

        logging.info(f"- Removing copy from previous install location: {moved_bundle}")
        subprocess_wrapper.run_as_root_and_verify(
            ["/bin/rm", "-rf", str(moved_bundle)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )


    def _remove_legacy_app_bundle(self) -> None:
        """
        Remove a pre-rebrand copy of ourselves left at the old bundle name

        Builds from before the T2 rebrand installed as OpenCore-Patcher.app and
        declared Dortania's bundle identifier. Left in place it keeps colliding
        with their app in Launch Services, which is what makes Launchpad show
        only one of the two. Removed only when the bundle is identifiably ours.
        """

        self._remove_moved_app_bundle()

        legacy_bundle = Path(self._LEGACY_PATCHER_INSTALL_DIRECTORY) / "OpenCore-Patcher.app"
        if not legacy_bundle.exists():
            return

        if not self._bundle_is_ours(legacy_bundle):
            logging.info("- Leaving OpenCore-Patcher.app, not one of ours")
            return

        logging.info("- Removing pre-rebrand copy of ourselves: OpenCore-Patcher.app")
        subprocess_wrapper.run_as_root_and_verify(
            ["/bin/rm", "-rf", str(legacy_bundle)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )

        # The /Applications entry is a symlink created by the PKG; it now dangles
        legacy_shim = Path("/Applications/OpenCore-Patcher.app")
        if legacy_shim.is_symlink() and not legacy_shim.exists():
            logging.info("- Removing dangling shim: /Applications/OpenCore-Patcher.app")
            subprocess_wrapper.run_as_root_and_verify(
                ["/bin/rm", "-f", str(legacy_shim)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )


    def install_auto_patcher_launch_agent(self, kdk_caching_needed: bool = False):
        """
        Install patcher launch services

        See start_auto_patch() comments for more info
        """

        if self.constants.launcher_script is not None:
            logging.info("- Skipping Auto Patcher Launch Agent, not supported when running from source")
            return

        self._remove_legacy_launch_services()
        self._remove_legacy_app_bundle()

        services = {
            self.constants.auto_patch_launch_agent_path:        "/Library/LaunchAgents/com.albert-mueller.opencore-legacy-patcher.auto-patch.plist",
            self.constants.update_launch_daemon_path:           "/Library/LaunchDaemons/com.albert-mueller.opencore-legacy-patcher.macos-update.plist",
            **({ self.constants.rsr_monitor_launch_daemon_path: "/Library/LaunchDaemons/com.albert-mueller.opencore-legacy-patcher.rsr-monitor.plist" } if self._create_rsr_monitor_daemon() else {}),
            **({ self.constants.kdk_launch_daemon_path:         "/Library/LaunchDaemons/com.albert-mueller.opencore-legacy-patcher.os-caching.plist" } if kdk_caching_needed is True else {} ),
        }

        for service in services:
            name = Path(service).name
            logging.info(f"- Installing {name}")
            source = self._stage_service(service)
            if Path(services[service]).exists():
                if hashlib.sha256(open(source, "rb").read()).hexdigest() == hashlib.sha256(open(services[service], "rb").read()).hexdigest():
                    logging.info(f"  - {name} checksums match, skipping")
                    continue
                logging.info(f"  - Existing service found, removing")
                subprocess_wrapper.run_as_root_and_verify(["/bin/rm", services[service]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            # Create parent directories
            if not Path(services[service]).parent.exists():
                logging.info(f"  - Creating {Path(services[service]).parent} directory")
                subprocess_wrapper.run_as_root_and_verify(["/bin/mkdir", "-p", Path(services[service]).parent], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            subprocess_wrapper.run_as_root_and_verify(generate_copy_arguments(source, services[service]), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

            # Set the permissions on the service
            subprocess_wrapper.run_as_root_and_verify(["/bin/chmod", "644", services[service]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            subprocess_wrapper.run_as_root_and_verify(["/usr/sbin/chown", "root:wheel", services[service]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


    def _create_rsr_monitor_daemon(self) -> bool:
        # Get kext list in /Library/Extensions that have the 'GPUCompanionBundles' property
        # This is used to determine if we need to run the RSRMonitor
        logging.info("- Checking if RSRMonitor is needed")

        cryptex_path = f"/System/Volumes/Preboot/{utilities.get_preboot_uuid()}/cryptex1/current/OS.dmg"
        if not Path(cryptex_path).exists():
            logging.info("- No OS.dmg, skipping RSRMonitor")
            return False

        kexts = []
        for kext in Path("/Library/Extensions").glob("*.kext"):
            try:
                if not Path(f"{kext}/Contents/Info.plist").exists():
                    continue
            except Exception as e:
                logging.info(f"  - Failed to check if {kext.name} is a directory: {e}")
                continue
            try:
                kext_plist = plistlib.load(open(f"{kext}/Contents/Info.plist", "rb"))
            except Exception as e:
                logging.info(f"  - Failed to load plist for {kext.name}: {e}")
                continue
            if "GPUCompanionBundles" not in kext_plist:
                continue
            logging.info(f"  - Found kext with GPUCompanionBundles: {kext.name}")
            kexts.append(kext.name)

        # If we have no kexts, we don't need to run the RSRMonitor
        if not kexts:
            logging.info("- No kexts found with GPUCompanionBundles, skipping RSRMonitor")
            return False

        # Load the RSRMonitor plist
        rsr_monitor_plist = plistlib.load(open(self.constants.rsr_monitor_launch_daemon_path, "rb"))

        arguments = ["/bin/rm", "-Rfv"]
        arguments += [f"/Library/Extensions/{kext}" for kext in kexts]

        # Add the arguments to the RSRMonitor plist
        rsr_monitor_plist["ProgramArguments"] = arguments

        # Next add monitoring for '/System/Volumes/Preboot/{UUID}/cryptex1/OS.dmg'
        logging.info(f"  - Adding monitor: {cryptex_path}")
        rsr_monitor_plist["WatchPaths"] = [
            cryptex_path,
        ]

        # Write the RSRMonitor plist
        plistlib.dump(rsr_monitor_plist, Path(self.constants.rsr_monitor_launch_daemon_path).open("wb"))

        return True
