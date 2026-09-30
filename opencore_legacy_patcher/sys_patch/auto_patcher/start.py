"""
start.py: Start automatic patching of host
"""

import wx
import wx.html2

import logging
import plistlib
import markdown2
import subprocess
import webbrowser

from packaging import version

from ... import constants

from ...datasets import css_data

from ...wx_gui import (
    gui_entry,
    gui_support
)
from ...support import (
    utilities,
    updates,
    global_settings,
    network_handler,
    subprocess_wrapper,
)
from ..patchsets import (
    HardwarePatchsetDetection,
    HardwarePatchsetValidation
)


class StartAutomaticPatching:
    """
    Start automatic patching of host
    """

    def __init__(self, global_constants: constants.Constants):
        self.constants: constants.Constants = global_constants


    def start_auto_patch(self):
        """
        Initiates automatic patching

        Auto Patching's main purpose is to try and tell the user they're missing root patches
        New users may not realize OS updates remove our patches, so we try and run when nessasary

        Conditions for running:
            - Verify running GUI (TUI users can write their own scripts)
            - Verify the Snapshot Seal is intact (if not, assume user is running patches)
            - Verify this model needs patching (if not, assume user upgraded hardware and OCLP was not removed)
            - Verify there are no updates for OCLP (ensure we have the latest patch sets)

        If all these tests pass, start Root Patcher

        """

        logging.info("- Starting Automatic Patching")
        if self.constants.wxpython_variant is False:
            logging.info("- Auto Patch option is not supported on TUI, please use GUI")
            return

        if self._check_for_updates() is True:
            return

        if utilities.check_seal() is True:
            logging.info("- Detected Snapshot seal intact, detecting patches")
            patches = HardwarePatchsetDetection(self.constants).device_properties
            if not any(not patch.startswith("Settings") and not patch.startswith("Validation") and patches[patch] is True for patch in patches):
                patches = {}
            if patches:
                logging.info("- Detected applicable patches, determining whether possible to patch")
                if patches[HardwarePatchsetValidation.PATCHING_NOT_POSSIBLE] is True:
                    logging.info("- Cannot run patching")
                    return

                logging.info("- Determined patching is possible, checking for OCLP updates")
                patch_string = ""
                for patch in patches:
                    if patches[patch] is True and not patch.startswith("Settings") and not patch.startswith("Validation"):
                        patch_string += f"- {patch}\n"

                logging.info("- No new binaries found on Github, proceeding with patching")

                warning_str = ""
                if network_handler.NetworkUtilities(self.constants.update_releases_api_url).verify_network_connection() is False:
                    warning_str = f"""\n\nWARNING: We're unable to verify whether there are any new releases of OpenCore Legacy Patcher T2 on Github. Be aware that you may be using an outdated version for this OS. If you're unsure, verify on Github that OpenCore Legacy Patcher T2 {subprocess_wrapper.applescript_quote(self.constants.patcher_version)} is the latest official release"""

                args = [
                    "/usr/bin/osascript",
                    "-e",
                    f"""display dialog "OpenCore Legacy Patcher T2 has detected you're running without Root Patches, and would like to install them.\n\nmacOS wipes all root patches during OS installs and updates, so they need to be reinstalled.\n\nFollowing Patches have been detected for your system: \n{patch_string}\nWould you like to apply these patches?{warning_str}" """
                    f'with icon POSIX file "{self.constants.app_icon_path}"',
                ]
                output = subprocess.run(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT
                )
                if output.returncode == 0:
                    gui_entry.EntryPoint(self.constants).start(entry=gui_entry.SupportedEntryPoints.SYS_PATCH, start_patching=True)
                return

            else:
                logging.info("- No patches detected")
        else:
            logging.info("- Detected Snapshot seal not intact, skipping")

        if self._determine_if_versions_match():
            self._determine_if_boot_matches()


    def _check_for_updates(self) -> bool:
        """
        Check whether a newer patcher build exists and prompt the user if so

        Mirrors wx_gui/gui_main_menu.py's _check_for_updates():
        - a failing update check can never abort auto patching
        - the remote version is re-validated locally instead of being trusted
        - every network call has a timeout

        Returns:
            bool: True if the user was prompted (caller stops), False to continue patching
        """

        try:
            checker = updates.CheckBinaryUpdates(self.constants)
            update_dict = checker.check_binary_updates()
        except Exception as e:
            # CheckBinaryUpdates() can raise before it ever returns a dict
            # (InvalidVersion/AssertionError on an unparsable patcher_version,
            # the privileged helper permission repair, ...). None of that may
            # take the auto patcher down - its job is reinstalling root patches,
            # not checking for updates. The "unable to verify releases" warning
            # further down in start_auto_patch() already covers this case.
            logging.error(f"- Update check failed: {e}")
            logging.exception("Stack Trace:")
            return False

        if not update_dict:
            logging.info(f"- No update available ({checker.last_error if checker.last_error else 'already on the latest version'})")
            return False

        remote_version_str = str(update_dict["Version"])
        local_version_str  = self.constants.patcher_version

        try:
            if version.parse(remote_version_str) <= version.parse(local_version_str):
                logging.info(f"- Already up to date (Local: {local_version_str}, Remote: {remote_version_str})")
                return False
        except version.InvalidVersion:
            # "Version" is derived from a GitHub release tag, i.e. remote input.
            # gui_main_menu.py still prompts here when the two strings differ;
            # the auto patcher deliberately does not - offering a download based
            # on a version we cannot even parse is the one case worth skipping.
            logging.error(f"- Unparsable version (Local: {local_version_str}, Remote: {remote_version_str}), skipping update prompt")
            return False

        logging.info(f"- Found new version: {remote_version_str}")
        self._show_update_dialog(update_dict, remote_version_str)
        return True


    def _fetch_changelog(self, remote_version_str: str) -> str:
        """
        Fetch the release notes for the version we are about to offer

        Parameters:
            remote_version_str (str): Version reported by the update check

        Returns:
            str: Markdown changelog, or a fallback notice on any failure
        """

        fallback = """## Unable to fetch changelog\n\nPlease check the Github page for more information about this release."""

        # updates.py picks the highest version across /releases (pre-releases
        # included), so /releases/latest can point at a different release than
        # the one we are offering. Match the tag instead of assuming "latest".
        api_url = self.constants.update_releases_api_url

        try:
            response = network_handler.NetworkUtilities().get(
                api_url,
                headers={"User-Agent": f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0/OpenCoreLegacyPatcherT2/{self.constants.patcher_version}"},
                timeout=10,
            )
            releases = response.json()
        except Exception as e:
            # Includes the JSON decode error from NetworkUtilities.get()'s empty
            # Response() fallback, and GitHub's rate limit body.
            logging.error(f"- Failed to fetch changelog: {e}")
            return fallback

        if not isinstance(releases, list):
            return fallback

        for release in releases:
            tag = release.get("tag_name")
            if not tag:
                continue
            try:
                if version.parse(tag) != version.parse(remote_version_str):
                    continue
            except version.InvalidVersion:
                continue

            body = release.get("body") or ""
            return body.split("## Asset Information")[0] if body else fallback

        return fallback


    def _show_update_dialog(self, update_dict: dict, remote_version_str: str) -> None:
        """
        Display the update prompt

        Duplicate of gui_main_menu.py's on_update() - keep both in sync.
        """

        # Auto patching runs outside the GUI, so there is no wx.App yet. Keep a
        # reference on self; letting it be collected takes the dialog with it.
        if wx.GetApp() is None:
            self._wx_app = wx.App()

        ID_GITHUB = wx.NewIdRef() if hasattr(wx, "NewIdRef") else wx.NewId()
        ID_UPDATE = wx.NewIdRef() if hasattr(wx, "NewIdRef") else wx.NewId()

        html_markdown = markdown2.markdown(self._fetch_changelog(remote_version_str), extras=["tables"])
        html_css = css_data.updater_css

        frame = wx.Dialog(None, -1, title="", size=(650, 500))
        try:
            frame.SetMinSize((650, 500))
            frame.SetWindowStyle(wx.STAY_ON_TOP)
            panel = wx.Panel(frame)

            title_text = wx.StaticText(panel, label=f"A new version of {self.constants.patcher_name} is available!")
            description = wx.StaticText(panel, label=f"{self.constants.patcher_name} {remote_version_str} is now available - You have {self.constants.patcher_version_label}. Would you like to update?")
            title_text.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
            description.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
            # Ohne Wrap() ragt der Text bei langen Versions-/Produktnamen ueber die
            # feste Dialogbreite hinaus und wird abgeschnitten (Commit 573d55e).
            description.Wrap(600)

            self.web_view = wx.html2.WebView.New(panel, style=wx.BORDER_SUNKEN)
            html_code = f'''
<html>
    <head>
        <style>
            {html_css}
        </style>
    </head>
    <body class="markdown-body">
        {html_markdown.replace("<a href=", "<a target='_blank' href=")}
    </body>
</html>
'''
            self.web_view.SetPage(html_code, "")
            self.web_view.Bind(wx.html2.EVT_WEBVIEW_NEWWINDOW, self._onWebviewNav)
            self.web_view.EnableContextMenu(False)

            close_button = wx.Button(panel, label="Update Later")
            close_button.Bind(wx.EVT_BUTTON, lambda event: frame.EndModal(wx.ID_CANCEL))
            view_button = wx.Button(panel, ID_GITHUB, label="View on GitHub")
            view_button.Bind(wx.EVT_BUTTON, lambda event: frame.EndModal(ID_GITHUB))
            install_button = wx.Button(panel, label="Update Now")
            install_button.Bind(wx.EVT_BUTTON, lambda event: frame.EndModal(ID_UPDATE))
            install_button.SetDefault()

            buttonsizer = wx.BoxSizer(wx.HORIZONTAL)
            buttonsizer.Add(close_button,   0, wx.ALIGN_CENTRE | wx.RIGHT, 5)
            buttonsizer.Add(view_button,    0, wx.ALIGN_CENTRE | wx.LEFT | wx.RIGHT, 5)
            buttonsizer.Add(install_button, 0, wx.ALIGN_CENTRE | wx.LEFT, 5)

            sizer = wx.BoxSizer(wx.VERTICAL)
            sizer.Add(title_text,  0, wx.ALIGN_CENTRE | wx.TOP, 20)
            sizer.Add(description, 0, wx.ALIGN_CENTRE | wx.BOTTOM, 20)
            sizer.Add(self.web_view, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
            sizer.Add(buttonsizer, 0, wx.ALIGN_RIGHT | wx.ALL, 20)
            panel.SetSizer(sizer)
            frame.Centre()

            result = frame.ShowModal()
        finally:
            frame.Destroy()

        if result == ID_GITHUB:
            webbrowser.open(update_dict["Github Link"])
        elif result == ID_UPDATE:
            gui_entry.EntryPoint(self.constants).start(entry=gui_entry.SupportedEntryPoints.UPDATE_APP)


    def _onWebviewNav(self, event):
        url = event.GetURL()
        webbrowser.open(url)


    def _determine_if_versions_match(self):
        """
        Determine if the booted version of OCLP matches the installed version

        ie. Installed app is 0.2.0, but EFI version is 0.1.0

        Returns:
            bool: True if versions match, False if not
        """

        logging.info("- Checking booted vs installed OCLP Build")
        if self.constants.computer.oclp_version is None:
            logging.info("- Booted version not found")
            return True

        if self.constants.computer.oclp_version == self.constants.patcher_version:
            logging.info("- Versions match")
            return True

        if self.constants.special_build is True:
            # Version doesn't match and we're on a special build
            # Special builds don't have good ways to compare versions
            logging.info("- Special build detected, assuming installed is older")
            return False

        # Check if installed version is newer than booted version
        if updates.CheckBinaryUpdates(self.constants).check_if_newer(self.constants.computer.oclp_version):
            logging.info("- Installed version is newer than booted version")
            return True

        # computer.oclp_version is read out of the OCLP-Version NVRAM variable, i.e. it is
        # whatever the booted EFI put there - fully controlled by whoever built it. It must
        # never be interpolated into AppleScript source unescaped. The version.parse() call
        # above happens to reject most payloads today, but that is incidental: it is a
        # version comparison, not a validation step, and it is not on every path here.
        booted_version = subprocess_wrapper.applescript_quote(self.constants.computer.oclp_version)
        installed_version = subprocess_wrapper.applescript_quote(self.constants.patcher_version)

        args = [
            "/usr/bin/osascript",
            "-e",
            f"""display dialog "OpenCore Legacy Patcher T2 has detected that you are booting {'a different' if self.constants.special_build else 'an outdated'} OpenCore build\n- Booted: {booted_version}\n- Installed: {installed_version}\n\nWould you like to update the OpenCore bootloader?" """
            f'with icon POSIX file "{self.constants.app_icon_path}"',
        ]
        output = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        if output.returncode == 0:
            logging.info("- Launching GUI's Build/Install menu")
            self.constants.start_build_install = True
            gui_entry.EntryPoint(self.constants).start(entry=gui_entry.SupportedEntryPoints.BUILD_OC)

        return False


    def _determine_if_boot_matches(self):
        """
        Determine if the boot drive matches the macOS drive
        ie. Booted from USB, but macOS is on internal disk

        Goal of this function is to determine whether the user
        is using a USB drive to Boot OpenCore but macOS does not
        reside on the same drive as the USB.

        If we determine them to be mismatched, notify the user
        and ask if they want to install to install to disk.
        """

        logging.info("- Determining if macOS drive matches boot drive")

        should_notify = global_settings.GlobalEnviromentSettings().read_property("AutoPatch_Notify_Mismatched_Disks")
        if should_notify is False:
            logging.info("- Skipping due to user preference")
            return
        if self.constants.host_is_hackintosh is True:
            logging.info("- Skipping due to hackintosh")
            return
        if not self.constants.booted_oc_disk:
            logging.info("- Failed to find disk OpenCore launched from")
            return

        root_disk = self.constants.booted_oc_disk.strip("disk")
        root_disk = "disk" + root_disk.split("s")[0]

        logging.info(f"  - Boot Drive: {self.constants.booted_oc_disk} ({root_disk})")
        macOS_disk = utilities.get_disk_path()
        logging.info(f"  - macOS Drive: {macOS_disk}")
        physical_stores = utilities.find_apfs_physical_volume(macOS_disk)
        logging.info(f"  - APFS Physical Stores: {physical_stores}")

        disk_match = False
        for disk in physical_stores:
            if root_disk in disk:
                logging.info(f"- Boot drive matches macOS drive ({disk})")
                disk_match = True
                break

        if disk_match is True:
            return

        # Check if OpenCore is on a USB drive
        logging.info("- Boot Drive does not match macOS drive, checking if OpenCore is on a USB drive")

        disk_info = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", root_disk], stdout=subprocess.PIPE).stdout)
        try:
            if disk_info["Ejectable"] is False:
                logging.info("- Boot Disk is not removable, skipping prompt")
                return

            logging.info("- Boot Disk is ejectable, prompting user to install to internal")

            args = [
                "/usr/bin/osascript",
                "-e",
                f"""display dialog "OpenCore Legacy Patcher T2 has detected that you are booting OpenCore from an USB or External drive.\n\nIf you would like to boot your Mac normally without a USB drive plugged in, you can install OpenCore to the internal hard drive.\n\nWould you like to launch OpenCore Legacy Patcher T2 and install to disk?" """
                f'with icon POSIX file "{self.constants.app_icon_path}"',
            ]
            output = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT
            )
            if output.returncode == 0:
                logging.info("- Launching GUI's Build/Install menu")
                self.constants.start_build_install = True
                gui_entry.EntryPoint(self.constants).start(entry=gui_entry.SupportedEntryPoints.BUILD_OC)

        except KeyError:
            logging.info("- Unable to determine if boot disk is removable, skipping prompt")
