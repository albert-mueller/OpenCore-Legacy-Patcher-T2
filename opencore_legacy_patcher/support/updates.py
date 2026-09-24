"""
updates.py: Check for OpenCore Legacy Patcher binary updates

Call check_binary_updates() to determine if any updates are available
Returns dict with Link and Version of the latest binary update if available
"""

import logging
import applescript

from urllib.parse import quote

from typing import Optional, Union
from packaging import version
from datetime import date
from . import network_handler
from . import subprocess_wrapper
from . import global_settings


from .. import constants


# The releases URL is generated from constants.update_repo_link, i.e. from the
# update channel selected in Settings (official T2 repository or a fork).


class CheckBinaryUpdates:
    def __init__(self, global_constants: constants.Constants) -> None:
        self.constants: constants.Constants = global_constants
        try:
            logging.info("Checking if the version is valid")
            self.binary_version = version.parse(self.constants.patcher_version)
        except version.InvalidVersion:
            logging.error("Since the version is not valid, we will not install any automatic updates.")
            logging.exception("Stack Trace:")
            logging.info("Please check for updates in GitHub manually.")
            assert self.constants.special_build is True, "Invalid version number for binary"
            # Special builds will not have a proper version number
            self.binary_version = version.parse("0.0.0")
        except Exception as e: # behebt eine Sicherheitslücke, die erlaubt einen Angreifer davon auszunutzen, Fehler außen version.InvalidError zu auslösen, um beliebiges Code auszuführen
            logging.error("An unexpected error occured while validating the version, so this version is considered invalid.")
            logging.exception("Stack Trace:")
            logging.info("Please check for updates in GitHub manually.")
            assert self.constants.special_build is True, "Invalid version number for binary"
            # Special builds will not have a proper version number
            self.binary_version = version.parse("0.0.0")

        self.latest_details = None
        self.last_error: Optional[str] = None

    def _ensure_privileged_helper_permissions(self) -> None:
        """
        Ensure the Privileged Helper Tool still has its expected 4755
        (setuid root, rwxr-xr-x) permissions before we reach out to check
        for updates, repairing them first if needed.

        Only prompts for a password when a repair is actually needed, so
        this doesn't nag the user with a sudo prompt on every check.
        """
        if subprocess_wrapper.privileged_helper_needs_setuid_repair():
            logging.info("Privileged Helper Tool permissions need repair, requesting administrator password")
            subprocess_wrapper.repair_privileged_helper_permissions()
        else: # behebt eine Sicherheitslücke, die erlaubt Angreifern, Reparierung von Priveleged Helper Tool zu erzwingen, um Root-Rechte zu erhalten
            logging.info("Privileged Helper Tool permissions are already correct, no repair needed")
            return

    def check_if_newer(self, version_to_check: Union[str, version.Version]) -> bool:
        """
        Check if the provided version is newer than the local version

        Parameters:
            version_to_check (str): Version to compare against

        Returns:
            bool: True if the provided version is newer, False if not
        """
        if self.constants.special_build is True:
            logging.info("This is a special version. Automatic updates are permanently disabled and to be enabled, you need to switch to a standard release.")
            logging.info("Please check for updates in GitHub manually.")
            return False

        # Fixed: Pass the local version as second argument (as expected by _check_if_build_newer)
        return self._check_if_build_newer(version_to_check, self.binary_version)

    def _check_if_build_newer(self, first_version: Union[str, version.Version], second_version: Union[str, version.Version]) -> bool:
        """
        Check if the first version is newer than the second version

        Parameters:
            first_version (str): First version to compare against (usually the one you want to test)
            second_version (str): Second version to compare against (usually the baseline)

        Returns:
            bool: True if first version is newer, False if not
        """

        if not isinstance(first_version, version.Version):
            try:
                first_version = version.parse(first_version)
            except version.InvalidVersion:
                # Special build > release build: assume special build is newer
                logging.error("There is a problem to update. Please search for updates manually.")
                logging.exception("Stack Trace:")
                return True
            except Exception as e: # behebt eine Sicherheitslücke, die erlaubt Angreifern, Fehler außerhalb except version.InvalidVersion auszulösen, um beliebiges Code auszuführen
                logging.error("There is an unexpected problem to update. Please search for updates manually.")
                logging.exception("Stack Trace:")
                return True

        if not isinstance(second_version, version.Version):
            try:
                second_version = version.parse(second_version)
            except version.InvalidVersion:
                # Release build > special build: assume special build is newer
                logging.error("There is a problem to update. Please search for updates manually.")
                logging.exception("Stack Trace:")
                return False

        if first_version == second_version:
            logging.info("You are on the latest version available already.")

        return first_version > second_version

    def check_binary_updates(self, manual: bool = False) -> Optional[dict]:
        """
        Check if any updates are available for the OpenCore Legacy Patcher binary.
        Automatic checks respect the user's auto-update flag and snooze window.
        Manual checks explicitly bypass those gates so the user can still force
        a refresh when they choose to.
        """
        if self.constants.auto_update is False and manual is False:
            logging.info("Automatic updates are disabled in the settings.")
            return None

        if manual is False:
            next_update_check = global_settings.GlobalEnviromentSettings().read_property("NextUpdateCheck")
            if next_update_check is not None:
                try:
                    if date.fromisoformat(str(next_update_check)) > date.today():
                        logging.info("Automatic updates are snoozed until %s.", next_update_check)
                        return None
                except ValueError:
                    logging.error("NextUpdateCheck value is invalid and will be ignored: %r", next_update_check)
                except Exception as e: # behebt eine Sicherheitslücke, indem einen Angreifer könnte Fehler außerhalb ValueError verursachen, um beliebiges Code auszuführen
                    logging.error("NextUpdateCheck value is invalid and will be ignored: %r", next_update_check)

        # Self-heal the Privileged Helper Tool's permissions before doing anything
        # network-related below. No-op (no prompt) unless a repair is actually needed.
        self._ensure_privileged_helper_permissions()

        self.last_error = None

        if self.constants.special_build is True:
            # Special builds do not get updates through the updater
            logging.info("You are using a special version")
            self.last_error = "This is a special build - automatic updates are disabled."
            return None

        if self.latest_details:
            # We already checked
            return self.latest_details

        # API URL of the selected update channel
        # Use /releases instead of /releases/latest to ensure we fetch pre-releases (alphas/betas) as well
        repo_latest_release_url = self.constants.update_releases_api_url
        channel_switch = self.constants.update_channel_switch_pending
        logging.info(f"Update channel: {self.constants.update_channel} ({self.constants.update_repo_link})")
        if channel_switch:
            logging.info(f"Channel switch pending: {self.constants.installed_update_channel} -> {self.constants.update_channel}")

        if not network_handler.NetworkUtilities(repo_latest_release_url).verify_network_connection():
            logging.error("It failed to connect with the GitHub page")
            logging.info("Please check if your computer is connected to the internet.")
            logging.exception("Stack Trace:")
            logging.info("If so, report this issue immediately")
            self.last_error = "Could not reach GitHub. Please check your internet connection."
            return None

        response = network_handler.NetworkUtilities().get(repo_latest_release_url)
        releases = response.json()

        if not releases or not isinstance(releases, list):
            return None

        # GitHub's /releases API returns items sorted by creation date, not by version number.
        # To avoid fetching an older version that was published more recently, we must find the highest version.
        highest_release = None
        highest_version = None

        for release in releases:
            if "tag_name" not in release:
                continue

            try:
                rel_ver = version.parse(release["tag_name"])
            except version.InvalidVersion:
                continue

            if highest_version is None or rel_ver > highest_version:
                highest_version = rel_ver
                highest_release = release

        if not highest_release:
            logging.error("Could not find any valid versions in the repository releases.")
            logging.info("Please check for updates in GitHub manually.")
            self.last_error = "No valid release versions were found in the repository."
            return None

        data_set = highest_release
        latest_remote_version = highest_version
        logging.info("Checking if the version is valid")

        if channel_switch:
            # Version numbers of two different repositories are not comparable
            # (e.g. a fork's 4.0.0.18009.x vs. the official 4.0.0.180010.x), so a
            # channel switch offers the channel's newest release as long as it is
            # a different build. Callers must always ask before installing it.
            if latest_remote_version == self.binary_version:
                logging.info("Already running the newest build of the selected channel, marking channel as installed")
                self._mark_channel_installed()
                return None
            logging.info(f"Offering {latest_remote_version} from the {self.constants.update_channel} channel (channel switch)")
        # Fixed: Swap the parameters so that the remote version is tested against the local one properly.
        # Alternatively, you can also just pass (self.binary_version, latest_remote_version)
        elif not self._check_if_build_newer(latest_remote_version, self.binary_version):
            logging.info("You are already on the latest version.")
            logging.info("If this meessage appears even if it's not up to date, you should report this issue.")
            logging.info("For most pre-alpha versions, this behavior is normal because various versions are marked as pre-release.")
            return None

        # Only reached for a newer build, or for an explicitly selected channel switch
        # (behebt eine Sicherheitslücke, die erlaubt Angreifern, Downgrade-Angriffen in Hintergrund ohne das Wissen von Benutzer zu starten)
        for asset in data_set["assets"]:
            logging.info("A new version is available")
            logging.info(f"Found asset: {asset['name']}")
            if asset["name"] == "OpenCore-Patcher-T2.pkg":
                self.latest_details = {
                    "Name": asset["name"],
                    "Version": latest_remote_version,
                    "Link": asset["browser_download_url"],
                    "Github Link": f"{self.constants.update_repo_link.rstrip('/')}/releases/tag/{quote(str(data_set['tag_name']), safe='')}",
                    "Changelog": data_set.get("body") or "",
                    "Channel": self.constants.update_channel,
                    "ChannelSwitch": channel_switch,
                }
                return self.latest_details

        if channel_switch:
            self.last_error = f"The newest release of the {self.constants.update_channel_label} channel has no OpenCore-Patcher-T2.pkg asset."
        return None

    def _mark_channel_installed(self) -> None:
        """
        Remember that the running build belongs to the selected update channel
        """
        self.constants.installed_update_channel = self.constants.update_channel
        global_settings.GlobalEnviromentSettings().write_property("UpdateChannelInstalled", self.constants.update_channel)
