"""
updates.py: Check for OpenCore Legacy Patcher binary updates

Call check_binary_updates() to determine if any updates are available
Returns dict with Link and Version of the latest binary update if available
"""

import logging
import applescript

from typing import Optional, Union
from packaging import version

from . import network_handler
from . import subprocess_wrapper

from .. import constants


# REPO_LATEST_RELEASE_URL is now dynamically generated from constants.repo_link


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
        if not subprocess_wrapper.privileged_helper_needs_setuid_repair():
            logging.info("Privileged Helper Tool permissions are already correct, no repair needed")
            return

        logging.info("Privileged Helper Tool permissions need repair, requesting administrator password")
        subprocess_wrapper.repair_privileged_helper_permissions()

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

    def check_binary_updates(self) -> Optional[dict]:
        """
        Check if any updates are available for the OpenCore Legacy Patcher binary

        Returns:
            dict: Dictionary with Link and Version of the latest binary update if available
        """

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

        # Dynamically generate the API URL from constants.repo_link
        repo_api_url = self.constants.repo_link.replace("https://github.com/", "https://api.github.com/repos/").strip("/")
        # Use /releases instead of /releases/latest to ensure we fetch pre-releases (alphas/betas) as well
        repo_latest_release_url = f"{repo_api_url}/releases"

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

        # Fixed: Swap the parameters so that the remote version is tested against the local one properly.
        # Alternatively, you can also just pass (self.binary_version, latest_remote_version)
        if not self._check_if_build_newer(latest_remote_version, self.binary_version):
            logging.info("You are already on the latest version.")
            logging.info("If this meessage appears even if it's not up to date, you should report this issue.")
            logging.info("For most pre-alpha versions, this behavior is normal because various versions are marked as pre-release.")
            return None

        for asset in data_set["assets"]:
            logging.info("A new version is available")
            logging.info(f"Found asset: {asset['name']}")
            if asset["name"] == "OpenCore-Patcher-T2.pkg":
                self.latest_details = {
                    "Name": asset["name"],
                    "Version": latest_remote_version,
                    "Link": asset["browser_download_url"],
                    "Github Link": f"https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/releases/{latest_remote_version}",
                }
                return self.latest_details

        return None
