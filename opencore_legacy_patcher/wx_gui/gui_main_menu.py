"""
gui_main_menu.py: Generate GUI for main menu
"""

import wx
import wx.html2

import sys
import logging
import subprocess
import requests
import markdown2
import threading
import webbrowser
import shutil
import os
from pathlib import Path
from packaging import version

from .. import constants

from ..support import (
    global_settings,
    updates
)
from ..datasets import (
    os_data,
    css_data
)
from ..wx_gui import (
    gui_build,
    gui_macos_installer_download,
    gui_support,
    gui_help,
    gui_settings,
    gui_sys_patch_display,
    gui_test_info,
    gui_update,
    gui_oc_settings,
    gui_macos_configeration,
    gui_model_change,
)

class MainFrame(wx.Frame):
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None):
        logging.info("Initializing Main Menu Frame")
        super(MainFrame, self).__init__(parent, title=title, size=(700, 800), style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX))
        gui_support.GenerateMenubar(self, global_constants).generate()

        self.constants: constants.Constants = global_constants
        self.title: str = title

        self.model_button: wx.Button = None
        self.build_button: wx.Button = None
        
        # FIX: Absicherung gegen Thread-Races & Verwaiste Fenster-Referenzen
        self.exiting_app: bool = False  
        self.active_gemini_frame: wx.Frame = None

        self.constants.update_stage = gui_support.AutoUpdateStages.INACTIVE

        self._generate_elements()

        self.Centre()
        self.Show()

        self._preflight_checks()

    def _generate_elements(self) -> None:
        """
        Generate UI elements for the main menu
        """
        # Logo
        logo = wx.StaticBitmap(self, bitmap=wx.Bitmap(str(self.constants.icns_resource_path / "OC-Patcher.icns"), wx.BITMAP_TYPE_ICON), pos=(-1, 0), size=(128, 128))
        logo.Centre(wx.HORIZONTAL)

        # Title label
        title_label = wx.StaticText(self, label=self.constants.patcher_name, pos=(-1, 128))
        title_label.SetFont(gui_support.font_factory(25, wx.FONTWEIGHT_BOLD))
        title_label.Centre(wx.HORIZONTAL)

        is_matteo = getattr(self.constants, "app_mode", "albert") == "matteo"

        display_version = self.constants.experimental_version if is_matteo else self.constants.patcher_version_label
        version_label = wx.StaticText(self, label=f"Version {display_version}", pos=(-1, title_label.GetPosition()[1] + 32))
        version_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        version_label.Centre(wx.HORIZONTAL)
        version_label.SetForegroundColour(wx.Colour(128, 128, 128))

        # Model label
        try:
            if self.constants.Experimental_Features:
                dev_label = wx.StaticText(self, label="Experimental Mode is ON", pos=(-1, version_label.GetPosition()[1] + 20))
                dev_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
                dev_label.Centre(wx.HORIZONTAL)
                dev_label.SetForegroundColour(wx.Colour(100, 196, 102))

                model_Button = wx.Button(self, label=f"Model: {self.constants.custom_model or self.constants.computer.real_model}", pos=(-1, version_label.GetPosition()[1] + 40))
            else:
                model_Button = wx.Button(self, label=f"Model: {self.constants.custom_model or self.constants.computer.real_model}", pos=(-1, version_label.GetPosition()[1] + 30))
        except Exception as e:
            logging.error("We couldn't verify whether Experimental Mode is on or off due to a critical bug.")
            logging.info("Please, report this bug.")
            logging.exception("The error is the following:")
            logging.info("Since we couldn't verify this, we'll assume Experimental Mode is disabled.")
            model_Button = wx.Button(self, label=f"Model: {self.constants.custom_model or self.constants.computer.real_model}", pos=(-1, version_label.GetPosition()[1] + 30))

        model_Button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        model_Button.Centre(wx.HORIZONTAL)
        model_Button.SetToolTip("Edit the Target Model OpenCore will build for")
        model_Button.Bind(wx.EVT_BUTTON, self.on_edit_model)
        self.model_button = model_Button

        # Main Feature Buttons
        menu_buttons = {
                "OpenCore": {
                    "function": self.on_oc_settings,
                    "description": ["Settings to prepares provided drives to be", "able to boot unsupported macOSes."],
                    "icon": str(self.constants.icns_resource_path / "OC-Build.icns"),
                },
                "Settings": {
                    "function": self.on_settings,
                    "description": ["App settings"],
                    "icon": str(self.constants.icns_resource_path / "Settings.icns"),
                },
                "Check for updates": {
                    "function": self.on_check_for_updates,
                    "description": ["Checks for updates so you have the", "latest features and bug fixes."],
                    "icon": str(self.constants.icns_resource_path / "Settings.icns"),
                },
                "Create macOS Installer": {
                    "function": self.on_create_macos_installer,
                    "description": ["Download and flash a macOS", "Installer for your system."],
                    "icon": str(self.constants.icns_resource_path / "OC-Installer.icns"),
                },
                "macOS Configuration": {
                    "function": self.on_macos_config,
                    "description": ["Settings, drivers and", "patches for your system."],
                    "icon": str(self.constants.patch_icon_path),
                },
                "Help": {
                    "function": self.on_help,
                    "description": ["Resources for OpenCore Legacy", "Patcher-T2."],
                    "icon": str(self.constants.icns_resource_path / "OC-Support.icns"),
                }
        }

        # Widest a description may be before it collides with the other column
        DESCRIPTION_MAX_WIDTH = 270

        button_x = 25
        button_y = self.model_button.GetPosition()[1] + 30
        rollover = (len(menu_buttons) + 1) // 2
        index = 0
        max_height = 0

        for button_name, button_function in menu_buttons.items():
            if "icon" in button_function:
                icon = wx.StaticBitmap(self, bitmap=wx.Bitmap(button_function["icon"], wx.BITMAP_TYPE_ICON), pos=(button_x - 5, button_y), size=(64, 64))
                if "OpenCore" in button_name or "EXPERIMENTAL" in button_name:
                    icon.SetSize((68, 68))
            
            button = wx.Button(self, label=button_name, pos=(button_x + 68, button_y), size=(205, 30))
            button.SetFont(gui_support.font_factory(12, wx.FONTWEIGHT_NORMAL))
            button.Bind(wx.EVT_BUTTON, lambda event, f=button_function["function"]: f(event))

            if button_name == "Check for updates":
                self.update_button = button

            if "OpenCore" in button_name or "EXPERIMENTAL" in button_name:
                self.build_button = button
                # NOTE: since the GUI redesign this entry point no longer builds or installs
                # anything - it only OPENS gui_oc_settings.OCSettingsFrame. That frame already
                # gates its own "Save OpenCore" / "Install OpenCore" buttons on host_can_build(),
                # so nothing that touches a disk is reachable from here anyway, and disabling the
                # way in was actively harmful:
                #   (a) "Allow native models" (allow_oc_everywhere) - the one opt-in that makes
                #       host_can_build() true on a Hackintosh/VM - is a checkbox INSIDE that very
                #       frame (gui_oc_settings.py, "Allow native models"). Disabling the button
                #       locked the only door to the switch that unlocks the button: an affected
                #       host could never opt in at all;
                #   (b) same for picking a real, supported Mac as the target model, which is the
                #       other documented way host_can_build() flips to true on such a host (see
                #       the custom_model branch in gui_support.CheckProperties.host_can_build());
                #   (c) it also hid every OpenCore setting - including the read-only look at what
                #       the current config would be - from anyone on a host that can't build,
                #       e.g. a T2 Mac whose model isn't (or isn't yet) listed in SupportedSMBIOS.
                # So: the entry point stays enabled for everyone, and the frame keeps deciding
                # what may actually be written to disk. Keep self.build_button pointing at it,
                # other frames still look this attribute up.
                if not gui_support.CheckProperties(self.constants).host_can_build():
                    button.SetToolTip(
                        "Opens OpenCore settings.\n\n"
                        "Building and installing OpenCore is not supported on this host, so "
                        "\"Save OpenCore\" and \"Install OpenCore\" stay disabled inside. To unlock them, "
                        "enable \"Allow native models\" in these settings, or pick a real, supported Mac "
                        "as the target model.\n\n"
                        "For installing OpenCore on Hackintoshes, follow Dortania's guide here: "
                        "https://dortania.github.io/OpenCore-Install-Guide/"
                    )
                    logging.info("Host cannot build OpenCore: settings stay accessible, Save/Install stay disabled.")
                else:
                    button.SetToolTip("Opens OpenCore settings, where OpenCore can be built and installed")
                    logging.info("Building OpenCore is supported on this host.")

            # Info / Details button right next to each entry
            if "info_tab" in button_function:
                info_btn = wx.Button(self, label="ℹ️", pos=(button_x + 278, button_y), size=(36, 30))
                info_btn.SetToolTip("Click to read the detailed explanation of this option and its parameters.")
                info_btn.Bind(wx.EVT_BUTTON, lambda event, tab=button_function["info_tab"]: self.on_show_test_info(event, tab))

            description_label = wx.StaticText(self, label='\n'.join(button_function["description"]), pos=(button_x + 72, button.GetPosition()[1] + 33))
            description_label.SetFont(gui_support.font_factory(10, wx.FONTWEIGHT_NORMAL))
            # These labels are absolutely positioned: the left column starts at x+72 and the
            # right column's icon at 360, so anything wider than this runs underneath the
            # other column instead of wrapping. Entries are hand-split into short lines above;
            # this is the backstop for when one is edited and grows.
            description_label.Wrap(DESCRIPTION_MAX_WIDTH)

            # Maintain spacing - grows if a wrapped description needs more than two lines
            row_height = max(85, 33 + description_label.GetSize()[1] + 20)
            button_y += row_height
            
            if button_y > max_height:
                max_height = button_y

            index += 1
            if index == rollover:
                button_x = 360
                button_y = self.model_button.GetPosition()[1] + 30

        # --- COPYRIGHT ---
        copy_label = wx.StaticText(self, label=self.constants.copyright_date, pos=(-1, max_height + 25))
        copy_label.SetFont(gui_support.font_factory(10, wx.FONTWEIGHT_NORMAL))
        copy_label.Centre(wx.HORIZONTAL)

        # Final Window Size adjustment
        self.SetSize((-1, copy_label.GetPosition()[1] + 60))

    def on_return_to_mode_selector(self, event: wx.Event = None):
        try:
            self.Hide()
            from ..wx_gui import gui_mode_selector
            new_frame = gui_mode_selector.ModeSelectorFrame(parent=None, title=self.title, global_constants=self.constants, screen_location=self.GetPosition())
            app = wx.GetApp()
            if hasattr(app, 'frame'):
                app.frame = new_frame
                new_frame.Bind(wx.EVT_CLOSE, app.OnCloseFrame)
            wx.CallAfter(self.Destroy)
        except Exception as e:
            logging.error(f"Failed to return to mode selector: {e}")
            logging.exception("Stack Trace:") # <- Angreifern könnten davon ausnutzen, dass Benutzer nicht das exakte Fehler weißen, um ClickFix-Angriffe zu starten

    def _preflight_checks(self, event: wx.Event = None) -> None:
        try:
            if self.constants.computer.build_model is None:
                logging.info("No build model detected. Defaulting to current host hardware.")
                self.constants.computer.build_model = self.constants.computer.real_model
            
            real_model = str(self.constants.computer.real_model).strip()
            build_model = str(self.constants.computer.build_model).strip() if self.constants.computer.build_model else None
            
            print(f"DEBUG: Real: '{real_model}' | Build: '{build_model}'")

            if (
                build_model is not None and
                build_model != real_model and
                self.constants.host_is_hackintosh is False
            ):
                pop_up = wx.MessageDialog(
                    self,
                    f"We found you are currently booting OpenCore built for a different unit: {build_model}\n\nPlease Build and Install a new OpenCore config.",
                    "Unsupported Configuration Detected!",
                    style=wx.OK | wx.ICON_EXCLAMATION
                )
                pop_up.ShowModal()
                self.Hide()
                gui_build.BuildFrame(
                    self,
                    self.title,
                    self.constants,
                    self.GetPosition(),
                    install=True,
                )
                return

        except Exception as e:
            print(f"DEBUG: Preflight error: {e}")

        # NOTE: the "--update_installed" block below MUST stay ahead of the
        # _check_for_updates() thread, which is now started at the end of this
        # function instead. _check_for_updates() sets
        # constants.has_checked_updates = True as its very first statement, and
        # this prompt only fires while that flag is still False. Starting the
        # thread first (as this function used to) meant the worker almost always
        # won the race - Thread.start() blocks on _started.wait(), so the worker
        # is already running and holding the GIL when start() returns, and the
        # main thread has to re-acquire the GIL before it gets down here - so
        # the flag was already True and the "Update successful!" prompt never
        # appeared after an in-app update. Upstream Dortania starts the thread
        # on the last line of this function for exactly this reason.
        if "--update_installed" in sys.argv and self.constants.has_checked_updates is False and gui_support.CheckProperties(self.constants).host_can_build():
            self.constants.has_checked_updates = True
            pop_up = wx.MessageDialog(
                self,
                f"{self.constants.patcher_name} has been updated to the latest available version: {self.constants.patcher_version_label}\n\nWould you like to update OpenCore and your root volume patches?",
                "Update successful!",
                style=wx.YES_NO | wx.YES_DEFAULT | wx.ICON_INFORMATION
            )
            response = pop_up.ShowModal()
            pop_up.Destroy()

            if response != wx.ID_YES:
                logging.info("Skipping OpenCore and root volume patch updates...")
                return

            logging.info("Updating OpenCore and root volume patches...")
            self.constants.update_stage = gui_support.AutoUpdateStages.CHECKING
            self.Hide()
            pos = self.GetPosition()
            gui_build.BuildFrame(
                parent=None,
                title=self.title,
                global_constants=self.constants,
                screen_location=pos,
                install=True
            )
            wx.CallAfter(self.Destroy)
            # We just installed the newest build, so checking for updates again
            # here is pointless - and this frame is already on its way out, so
            # handing it to a worker that ends in wx.CallAfter(self.on_update, ...)
            # would put us right back into the dead-frame callback problem.
            return

        self.update_thread = threading.Thread(target=self._check_for_updates)
        self.update_thread.daemon = True
        self.update_thread.start()
        # Also tracked on constants (not just this frame) so PatcherApp.OnExit()
        # can join it at quit the same way it already does for unpack_thread/
        # analytics_thread - this frame instance itself may already be gone by
        # then (the app keeps destroying and recreating MainFrame as the user
        # navigates), but constants persists for the whole process lifetime.
        self.constants.update_thread = self.update_thread

    def _check_for_updates(self, manual: bool = False):
        if manual is False and self.constants.has_checked_updates is True:
            logging.info("We have already checked for updates.")
            return
        self.constants.has_checked_updates = True

        checker = updates.CheckBinaryUpdates(self.constants)
        try:
            update_dict = checker.check_binary_updates()
        except Exception as e:
            logging.error(f"Update check failed: {e}")
            logging.exception("Stack Trace:")
            self._report_manual_check(manual, None, str(e))
            return

        if not update_dict:
            self._report_manual_check(manual, None, checker.last_error)
            return
    
        remote_version_str = update_dict["Version"]
        local_version_str = self.constants.patcher_version
    
        try:
            remote_v = version.parse(str(remote_version_str))
            local_v = version.parse(local_version_str)
    
            if remote_v <= local_v:
                logging.info(f"{self.constants.patcher_name} is up to date. (Local: {local_v} >= Remote: {remote_v})")
                self._report_manual_check(manual, None, None)
                return
    
        except version.InvalidVersion:
            logging.info("The version is invalid, you'll not receive any further updates.")
            if remote_version_str == local_version_str:
                self._report_manual_check(manual, None, None)
                return
    
        if getattr(self, 'exiting_app', False) or gui_support.is_app_exiting():
            return
        
        logging.info(f"Newer version detected: {remote_version_str}")
        
        url = "https://api.github.com/repos/albert-mueller/OpenCore-Legacy-Patcher-T2/releases/latest"
        changelog = """## Unable to fetch changelog\n\nPlease check the Github page for more information."""
        # User-Agent auf Edge gesetzt statt einfach OpenCore-Legacy-Patcher-T2, um die API sicher zu laden und MitM-Angriffe zu vermeiden
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0/OpenCoreLegacyPatcherT2"}, timeout=10).json()
            if "body" in response:
                changelog = response["body"].split("## Asset Information")[0]
        except Exception as e:
            logging.error(f"Failed to fetch changelog text: {e}")
            logging.error(f"Es hat fehlgeschlagen, den Changelog-Text anzuzeigen: {e}")

        if not getattr(self, 'exiting_app', False) and not gui_support.is_app_exiting():
            self._report_manual_check(manual, str(remote_version_str), None)
            wx.CallAfter(self.on_update, update_dict["Link"], remote_version_str, update_dict["Github Link"], changelog)
        
    def on_check_for_updates(self, event: wx.Event = None) -> None:
        """
        Manual "Check for updates" button.

        Unlike the startup check this ignores constants.has_checked_updates and
        always reports back - a user who clicks the button gets an answer even
        when there is nothing new. Runs on a worker thread so the GitHub request
        cannot freeze the GUI.
        """
        thread = getattr(self, "update_thread", None)
        if thread is not None and thread.is_alive():
            logging.info("An update check is already running.")
            return

        button = getattr(self, "update_button", None)
        if button is not None:
            button.SetLabel("Checking...")
            button.Disable()

        self.update_thread = threading.Thread(target=self._check_for_updates, kwargs={"manual": True})
        self.update_thread.daemon = True
        self.update_thread.start()
        self.constants.update_thread = self.update_thread

    def _report_manual_check(self, manual: bool, new_version, error) -> None:
        """
        Hand the result of a manual check back to the main thread. No-op for the
        automatic startup check, which stays silent when there is nothing new.
        """
        if manual is False:
            return
        wx.CallAfter(self._on_manual_check_finished, new_version, error)

    def _on_manual_check_finished(self, new_version, error) -> None:
        if getattr(self, 'exiting_app', False) or gui_support.is_app_exiting():
            return

        button = getattr(self, "update_button", None)
        if button is not None:
            try:
                button.SetLabel("Check for updates")
                button.Enable()
            except RuntimeError:
                # Frame was destroyed while the check was running
                return

        if new_version is not None:
            # on_update() opens the updater window, no message box needed
            return

        if error:
            wx.MessageBox(
                f"Could not check for updates:\n\n{error}",
                "Update check failed",
                wx.OK | wx.ICON_ERROR, self
            )
            return

        wx.MessageBox(
            f"You are running the latest version ({self.constants.patcher_version_label}).",
            "No updates available",
            wx.OK | wx.ICON_INFORMATION, self
        )

    def on_update(self, oclp_url: str, oclp_version: str, oclp_github_url: str, changelog_text: str):
        if not self or gui_support.is_app_exiting():
            return

        ID_GITHUB = wx.NewIdRef() if hasattr(wx, "NewIdRef") else wx.NewId()
        ID_UPDATE = wx.NewIdRef() if hasattr(wx, "NewIdRef") else wx.NewId()

        html_markdown = markdown2.markdown(changelog_text, extras=["tables"])
        html_css = css_data.updater_css
        
        # Parent auf self gesetzt zur sauberen Speicherhierarchie
        frame = wx.Dialog(self, -1, title="", size=(650, 500))
        frame.SetMinSize((650, 500))
        frame.SetWindowStyle(wx.STAY_ON_TOP)
        panel = wx.Panel(frame)
        
        self.title_text = wx.StaticText(panel, label=f"A new version of {self.constants.patcher_name} is available!")
        self.description = wx.StaticText(panel, label=f"{self.constants.patcher_name} {oclp_version} is now available - You have {self.constants.patcher_version_label}. Would you like to update?")
        self.title_text.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
        self.description.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        # Ohne Wrap() ragt der Text bei langen Versions-/Produktnamen über die feste Dialogbreite hinaus
        # und wird dadurch abgeschnitten (z.B. "Would you like to update?" -> "Would you like to").
        # Siehe Commit 573d55e; ging beim GUI-Redesign (PR #200) in dieser Datei verloren.
        # Duplikat dieses Dialogs in sys_patch/auto_patcher/start.py -- dort bei Änderungen mitziehen.
        self.description.Wrap(600)
        
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
        
        self.close_button = wx.Button(panel, label="Update Later")
        self.close_button.Bind(wx.EVT_BUTTON, lambda event: frame.EndModal(wx.ID_CANCEL))
        self.view_button = wx.Button(panel, ID_GITHUB, label="View on GitHub")
        self.view_button.Bind(wx.EVT_BUTTON, lambda event: frame.EndModal(ID_GITHUB))
        self.install_button = wx.Button(panel, label="Update Now")
        self.install_button.Bind(wx.EVT_BUTTON, lambda event: frame.EndModal(ID_UPDATE))
        self.install_button.SetDefault()

        buttonsizer = wx.BoxSizer(wx.HORIZONTAL)
        buttonsizer.Add(self.close_button, 0, wx.ALIGN_CENTRE | wx.RIGHT, 5)
        buttonsizer.Add(self.view_button, 0, wx.ALIGN_CENTRE | wx.LEFT|wx.RIGHT, 5)
        buttonsizer.Add(self.install_button, 0, wx.ALIGN_CENTRE | wx.LEFT, 5)
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.title_text, 0, wx.ALIGN_CENTRE | wx.TOP, 20)
        sizer.Add(self.description, 0, wx.ALIGN_CENTRE | wx.BOTTOM, 20)
        sizer.Add(self.web_view, 1, wx.EXPAND | wx.LEFT|wx.RIGHT, 10)
        sizer.Add(buttonsizer, 0, wx.ALIGN_RIGHT | wx.ALL, 20)
        panel.SetSizer(sizer)
        frame.Centre()

        result = frame.ShowModal()

        if result == ID_GITHUB:
            webbrowser.open(oclp_github_url)
        elif result == ID_UPDATE:
            gui_update.UpdateFrame(
                parent=self,
                title=self.title,
                global_constants=self.constants,
                screen_location=self.GetPosition(),
                url=oclp_url,
                version_label=oclp_version
            )

        frame.Destroy()

    def _onWebviewNav(self, event):
        url = event.GetURL()
        webbrowser.open(url)

    def on_show_test_info(self, event: wx.Event = None, initial_tab: int = 0):
        try:
            dialog = gui_test_info.TestExplanationDialog(self, self.constants, initial_tab=initial_tab)
            dialog.ShowModal()
            dialog.Destroy()
        except Exception as e:
            logging.error(f"Failed to open Test Explanation dialog: {e}")
            logging.exception("Stack Trace:")

    def on_build_and_install_standard(self, event: wx.Event = None):
        self.constants.build_profile = "standard"
        self.on_build_and_install(event)

    def on_build_opencore_menu(self, event: wx.Event = None):
        choices = [
            "🟢 Standard / Safe Build",
            "🧪 [LEVEL-B] Experimental GPU",
            "🧪 [LEVEL-C] Experimental Tahoe (Native SMBIOS)",
            "🧪 [LEVEL-C] Experimental Spoof T2 (MacBookPro16,1)",
            "🧪 [LEVEL-D] All-In-One Tahoe (Wi-Fi + Audio + GPU + T1)"
        ]
        dialog = wx.SingleChoiceDialog(
            self,
            "Select the OpenCore build profile you wish to generate:",
            "Build OpenCore",
            choices
        )
        
        if dialog.ShowModal() == wx.ID_OK:
            selection = dialog.GetSelection()
            if selection == 0:
                self.constants.build_profile = "standard"
            elif selection == 1:
                self.constants.build_profile = "test_b"
            elif selection == 2:
                self.constants.build_profile = "test_c"
            elif selection == 3:
                self.constants.build_profile = "test_c_spoofed"
            elif selection == 4:
                self.constants.build_profile = "test_d"
            # behebt eine Sicherheitslücke, die erlaubt Angreifern, selection zu manipulieren und beispielsweise zu behaupten, es wäre Option 5 ausgewählt, die erst gar nicht existiert, um die Anwendung zum Absturz zu bringen.
            else:
                logging.error("You haven't selected a valid testing OpenCore option.")
                logging.info("Please try again later.")
            
            self.on_build_and_install(event)
        
        dialog.Destroy()

    def on_build_and_install_testd(self, event: wx.Event = None):
        self.constants.build_profile = "test_d"
        self.on_build_and_install(event)

    def on_build_and_install(self, event: wx.Event = None):
        try:
            self.Hide()
            gui_build.BuildFrame(parent=None, title=self.title, global_constants=self.constants, screen_location=self.GetPosition())
            wx.CallAfter(self.Destroy)
        except Exception as e:
            logging.error(f"We failed to open up Build and Install OpenCore: {e}")
            logging.exception("Stack Trace:")

    def on_root_patches(self, event: wx.Event = None):
        try:
            self.Hide()
            gui_sys_patch_display.SysPatchDisplayFrame(parent=None, title=self.title, global_constants=self.constants, screen_location=self.GetPosition())
            wx.CallAfter(self.Destroy)
        except Exception as e:
            logging.error(f"We failed to open up Root Patches: {e}")
            logging.exception("Stack Trace:")
            return

    def on_macos_config(self, event: wx.Event = None):
        try:
            gui_macos_configeration.MacosConfigFrame(
                parent=self,
                title=self.title,
                global_constants=self.constants,
                screen_location=self.GetPosition()
            )
        except Exception as e:
            logging.error(f"We failed to open MacOS Configuration: {e}")
            logging.exception("Stack Trace:")
            return
    def on_edit_model(self, event: wx.Event = None):
        # behebt eine Sicherheitslücke, die erlaubt Angreifern, wenn Fehlern in on_edit_model gibt, die Anwendung zum Absturz zu bringen oder beliebiges Code auszuführen
        try:
            self.Disable()
            gui_model_change.ModelPickerFrame(
                parent=self,
                title=self.title,
                global_constants=self.constants,
                screen_location=self.GetPosition(),
            )
        except Exception as e:
            logging.error(f"We failed to call the function on_edit_model: {e}")
            logging.exception("Stack Trace:")

    def on_oc_settings(self, event: wx.Event = None):
        try:
            gui_oc_settings.OCSettingsFrame(
                parent=self,
                title=self.title,
                global_constants=self.constants,
                screen_location=self.GetPosition()
            )
        except Exception as e:
            logging.error(f"We failed to open OpenCore Settings: {e}")
            logging.exception("Stack Trace:")
            return

    def on_create_macos_installer(self, event: wx.Event = None):
        try:
            gui_macos_installer_download.macOSInstallerDownloadFrame(parent=self, title=self.title, global_constants=self.constants, screen_location=self.GetPosition())
        except Exception as e:
            logging.error(f"We failed to open up Download macOS: {e}")
            logging.exception("Stack Trace:")
            return # <- da fehlte das return-Funktion, also der App könnte trotzdem der fehlerhafte Code auszuführen, auch wenn es schlug fehl. Angreifern könnten davon ausnutzen, um die Anwendung zum Absturz zu bringen oder beliebiges Code auszuführen

    def on_settings(self, event: wx.Event = None):
        try:
            gui_settings.SettingsFrame(parent=self, title=self.title, global_constants=self.constants, screen_location=self.GetPosition())
        except Exception as e:
            logging.error(f"We failed to open up Settings: {e}")
            logging.exception("Stack Trace:")
            return # <- da fehlte das return-Funktion, also der App könnte trotzdem der fehlerhafte Code auszuführen, auch wenn es schlug fehl. Angreifern könnten davon ausnutzen, um die Anwendung zum Absturz zu bringen oder beliebiges Code auszuführen

    def on_help(self, event: wx.Event = None):
        try:
            gui_help.HelpFrame(parent=self, title=self.title, global_constants=self.constants, screen_location=self.GetPosition())
        except Exception as e:
            logging.error(f"We failed to open up Help: {e}")
            logging.exception("Stack Trace:")
            return # <- da fehlte das return-Funktion, also der App könnte trotzdem der fehlerhafte Code auszuführen, auch wenn es schlug fehl. Angreifern könnten davon ausnutzen, um die Anwendung zum Absturz zu bringen oder beliebiges Code auszuführen

