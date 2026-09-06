"""
gui_macos_installer_download.py: macOS Installer Download Frame
"""

import wx
import locale
import logging
import threading
import webbrowser
import sys

from pathlib import Path

from .. import (
    constants,
    sucatalog
)

from ..datasets import (
    os_data,
    smbios_data,
    cpu_data
)
from ..wx_gui import (
    gui_main_menu,
    gui_support,
    gui_download,
    gui_macos_installer_flash
)
from ..support import (
    macos_installer_handler,
    utilities,
    network_handler,
    integrity_verification
)


class macOSInstallerDownloadFrame(wx.Frame):
    """
    Create a frame for downloading and creating macOS installers
    Uses a Modal Dialog for smoother transition from other frames
    Note: Flashing installers is passed to gui_macos_installer_flash.py
    """
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None):
        logging.info("Initializing macOS Installer Download Frame")
        super(macOSInstallerDownloadFrame, self).__init__(parent, title=title, size=(300, 200), style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX))
        
        self.constants: constants.Constants = global_constants
        self.title: str = title
        self.parent: wx.Frame = parent

        self.catalog_products = None
        self.available_installers = None
        self.available_installers_latest = None

        self.catalog_seed: sucatalog.SeedType = sucatalog.SeedType.DeveloperSeed

        self.frame_modal = wx.Dialog(parent, title=title, size=(330, 200))

        self._generate_elements(self.frame_modal)
        self.frame_modal.ShowWindowModal()

        self.icons = [[self._icon_to_bitmap(i), self._icon_to_bitmap(i, (64, 64))] for i in self.constants.icons_path]

    def _icon_to_bitmap(self, icon: str, size: tuple = (32, 32)) -> wx.Bitmap:
        """
        Convert icon to bitmap
        """
        return wx.Bitmap(wx.Bitmap(icon, wx.BITMAP_TYPE_ICON).ConvertToImage().Rescale(size[0], size[1], wx.IMAGE_QUALITY_HIGH))

    def _macos_version_to_icon(self, version: int) -> int:
        """
        Convert macOS version to icon
        """
        try:
            self.constants.icons_path[version - 19]
            return version - 19
        except IndexError:
            return 0

    def _generate_elements(self, frame: wx.Frame = None) -> None:
        frame = self if not frame else frame

        title_label = wx.StaticText(frame, label="Create macOS Installer", pos=(-1, 5))
        title_label.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
        title_label.Centre(wx.HORIZONTAL)

        download_button = wx.Button(frame, label="Download macOS Installer", pos=(-1, title_label.GetPosition()[1] + title_label.GetSize()[1] + 5), size=(200, 30))
        download_button.Bind(wx.EVT_BUTTON, self.on_download)
        download_button.Centre(wx.HORIZONTAL)

        existing_button = wx.Button(frame, label="Use existing macOS Installer", pos=(-1, download_button.GetPosition()[1] + download_button.GetSize()[1] - 5), size=(200, 30))
        existing_button.Bind(wx.EVT_BUTTON, self.on_existing)
        existing_button.Centre(wx.HORIZONTAL)

        return_button = wx.Button(frame, label="Return to Main Menu", pos=(-1, existing_button.GetPosition()[1] + existing_button.GetSize()[1] + 5), size=(150, 30))
        return_button.Bind(wx.EVT_BUTTON, self.on_return)
        return_button.Centre(wx.HORIZONTAL)

        frame.SetSize((-1, return_button.GetPosition()[1] + return_button.GetSize()[1] + 40))

    def _generate_catalog_frame(self) -> None:
        """
        Generate frame to display available installers asynchronously
        """
        gui_support.GenerateMenubar(self, self.constants).generate()
        self.Centre()

        title_label = wx.StaticText(self, label="Finding Available Software", pos=(-1, 5))
        title_label.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
        title_label.Centre(wx.HORIZONTAL)

        self.progress_bar = wx.Gauge(self, range=100, pos=(-1, title_label.GetPosition()[1] + title_label.GetSize()[1] + 5), size=(250, 30))
        self.progress_bar.Centre(wx.HORIZONTAL)
        self.progress_bar_animation = gui_support.GaugePulseCallback(self.constants, self.progress_bar)
        self.progress_bar_animation.start_pulse()

        self.SetSize((-1, self.progress_bar.GetPosition()[1] + self.progress_bar.GetSize()[1] + 40))
        self.Show()

        def _fetch_installers():
            logging.info(f"Fetching AppleDB products")

            # AppleDBProducts() reaches out over the network. Anything from a dead
            # route to a malformed payload can raise here; an uncaught exception in
            # a worker thread kills the fetch silently and leaves the pulse spinning
            # forever, so funnel every failure into _on_fetch_failed().
            try:
                self.catalog_products = sucatalog.AppleDBProducts(self.constants)
            except Exception as error:
                logging.error(f"Failed to fetch installers from AppleDB: {error}")
                self._safe_call_after(self._on_fetch_failed)
                return

            if self.catalog_products.data is None:
                logging.error("Failed to fetch installers from AppleDB")
                self._safe_call_after(self._on_fetch_failed)
                return

            self.available_installers = self.catalog_products.products
            self.available_installers_latest = self.catalog_products.latest_products

            # A non-None but unusable payload (network error swallowed further down,
            # schema change, empty list) leaves us with no products at all. Previously
            # this still took the success path and handed an empty catalog to the UI.
            if not self.available_installers and not self.available_installers_latest:
                logging.error("No installers returned from AppleDB")
                self._safe_call_after(self._on_fetch_failed)
                return

            # Sicherer Rückruf in den Haupt-Thread nach Beendigung des Netzwerkvorgangs
            self._safe_call_after(self._on_fetch_success)

        thread = threading.Thread(target=_fetch_installers, daemon=True)
        thread.start()

    def _safe_call_after(self, target, *args, **kwargs) -> None:
        """
        wx.CallAfter() asserts ("No wx.App created yet") once the wx.App has been
        torn down - which is exactly what happens when the user quits while a worker
        thread is still in flight. There is no UI left to update at that point, so
        drop the callback instead of letting the thread die on an AssertionError.
        """
        if wx.GetApp() is None:
            logging.info("wx.App no longer running, dropping UI callback")
            return
        try:
            wx.CallAfter(target, *args, **kwargs)
        except (AssertionError, RuntimeError) as error:
            logging.info(f"wx.App torn down mid-dispatch, dropping UI callback: {error}")

    def _on_fetch_failed(self):
        if not self:
            return
        self.progress_bar_animation.stop_pulse()
        self.progress_bar.Hide()
        wx.MessageBox("Failed to fetch installers from AppleDB", "Error", wx.OK | wx.ICON_ERROR, self)
        self.on_return_to_main_menu()

    def _on_fetch_success(self):
        if not self:
            return
        self.progress_bar_animation.stop_pulse()
        self.progress_bar.Hide()
        self._display_available_installers()

    def _display_available_installers(self, event: wx.Event = None, show_full: bool = False) -> None:
        bundles = [wx.BitmapBundle.FromBitmaps(icon) for icon in self.icons]

        if self.frame_modal:
            self.frame_modal.Destroy()
            
        self.frame_modal = wx.Dialog(self, title="Select macOS Installer", size=(550, 500))

        title_label = wx.StaticText(self.frame_modal, label="Select macOS Installer", pos=(-1, -1))
        title_label.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))

        # Modernes ID-Handling für wxPython v4+
        list_id = wx.NewIdRef() if hasattr(wx, "NewIdRef") else wx.NewId()

        self.list = wx.ListCtrl(self.frame_modal, list_id, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_NO_HEADER | wx.BORDER_SUNKEN)
        self.list.SetSmallImages(bundles)

        self.list.InsertColumn(0, "Title", width=190 if show_full else 150)
        self.list.InsertColumn(1, "Version", width=80 if show_full else 50)
        self.list.InsertColumn(2, "Build", width=75)
        self.list.InsertColumn(3, "Size", width=75)
        self.list.InsertColumn(4, "Release Date", width=100)

        installers = self.available_installers_latest if show_full is False else self.available_installers
        if show_full is False:
            self.frame_modal.SetSize((480, 370))

        if installers:
            try:
                locale.setlocale(locale.LC_TIME, '')
            except Exception:
                pass
            logging.info(f"Available installers from AppleDB ({'All entries' if show_full else 'Latest only'}):")
            for item in installers:
                index = self.list.InsertItem(self.list.GetItemCount(), f"{item['Title']}")
                self.list.SetItemImage(index, self._macos_version_to_icon(int(item['Build'][:2])))
                self.list.SetItem(index, 1, item['Version'])
                self.list.SetItem(index, 2, item['Build'])
                self.list.SetItem(index, 3, utilities.human_fmt(item['InstallAssistant']['Size']))
                self.list.SetItem(index, 4, item['PostDate'].strftime("%x"))
        else:
            logging.error("No installers found from AppleDB")
            wx.MessageDialog(self.frame_modal, "Failed to fetch installers from AppleDB", "Error", wx.OK | wx.ICON_ERROR).ShowModal()

        if show_full is False:
            self.list.Select(-1)

        self.list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self.on_select_list)
        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_select_list)

        self.select_button = wx.Button(self.frame_modal, label="Download", pos=(-1, -1), size=(150, -1))
        self.select_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        self.select_button.Bind(wx.EVT_BUTTON, lambda event, installers=installers: self.on_download_installer(installers))
        self.select_button.SetToolTip("Download the selected macOS Installer.")
        self.select_button.SetDefault()
        if show_full is True:
            self.select_button.Disable()

        self.copy_button = wx.Button(self.frame_modal, label="Copy Link", pos=(-1, -1), size=(80, -1))
        self.copy_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        if show_full is True:
            self.copy_button.Disable()
        self.copy_button.SetToolTip("Copy the download link of the selected macOS Installer.")
        self.copy_button.Bind(wx.EVT_BUTTON, lambda event, installers=installers: self.on_copy_link(installers))

        return_button = wx.Button(self.frame_modal, label="Return to Main Menu", pos=(-1, -1), size=(150, -1))
        return_button.Bind(wx.EVT_BUTTON, self.on_return_to_main_menu)
        return_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))

        self.showolderversions_checkbox = wx.CheckBox(self.frame_modal, label="Show Older/Beta Versions", pos=(-1, -1))
        if show_full is True:
            self.showolderversions_checkbox.SetValue(True)
        self.showolderversions_checkbox.Bind(wx.EVT_CHECKBOX, lambda event: self._display_available_installers(event, self.showolderversions_checkbox.GetValue()))

        rectbox = wx.StaticBox(self.frame_modal, -1)
        rectsizer = wx.StaticBoxSizer(rectbox, wx.HORIZONTAL)
        rectsizer.Add(self.copy_button, 0, wx.EXPAND | wx.RIGHT, 5)
        rectsizer.Add(self.select_button, 0, wx.EXPAND | wx.LEFT, 5)

        checkboxsizer = wx.BoxSizer(wx.HORIZONTAL)
        checkboxsizer.Add(self.showolderversions_checkbox, 0, wx.ALIGN_CENTRE | wx.RIGHT, 5)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.AddSpacer(10)
        sizer.Add(title_label, 0, wx.ALIGN_CENTRE | wx.ALL, 0)
        sizer.Add(self.list, 1, wx.EXPAND | wx.ALL, 10)
        sizer.Add(rectsizer, 0, wx.ALIGN_CENTRE | wx.ALL, 0)
        sizer.Add(checkboxsizer, 0, wx.ALIGN_CENTRE | wx.ALL, 15)
        sizer.Add(return_button, 0, wx.ALIGN_CENTRE | wx.BOTTOM, 15)

        self.frame_modal.SetSizer(sizer)
        self.frame_modal.ShowWindowModal()

    def on_copy_link(self, installers: dict) -> None:
        selected_item = self.list.GetFirstSelected()
        if selected_item != -1:
            clipboard = wx.Clipboard.Get()
            if not clipboard.IsOpened():
                clipboard.Open()
            clipboard.SetData(wx.TextDataObject(installers[selected_item]['InstallAssistant']['URL']))
            clipboard.Close()
            wx.MessageDialog(self.frame_modal, "Download link copied to clipboard", "", wx.OK | wx.ICON_INFORMATION).ShowModal()

    def on_select_list(self, event):
        if self.list.GetSelectedItemCount() > 0:
            self.select_button.Enable()
            self.copy_button.Enable()
        else:
            self.select_button.Disable()
            self.copy_button.Disable()

    def on_download_installer(self, installers: dict) -> None:
        selected_item = self.list.GetFirstSelected()
        if selected_item != -1:
            selected_installer = installers[selected_item]
            logging.info(f"Selected macOS {selected_installer['Version']} ({selected_installer['Build']})")

            problems = []
            model = self.constants.custom_model or self.constants.computer.real_model
            if model in smbios_data.smbios_dictionary:
                if selected_installer["InstallAssistant"]["XNUMajor"] >= os_data.os_data.ventura:
                    if smbios_data.smbios_dictionary[model]["CPU Generation"] <= cpu_data.CPUGen.penryn or model in ["MacPro4,1", "MacPro5,1", "Xserve3,1"]:
                        if model.startswith("MacBook"):
                            problems.append("Lack of internal Keyboard/Trackpad in macOS installer.")
                        else:
                            problems.append("Lack of internal Keyboard/Mouse in macOS installer.")

            if problems:
                logging.warning(f"Potential issues with {model} and {selected_installer['Version']} ({selected_installer['Build']}): {problems}")
                problems = "\n".join(problems)
                dlg = wx.MessageDialog(self.frame_modal, f"Your model ({model}) may not be fully supported by this installer. You may encounter the following issues:\n\n{problems}\n\nFor more information, see associated page. Otherwise, we recommend using macOS Monterey", "Potential Issues", wx.YES_NO | wx.CANCEL | wx.ICON_WARNING)
                dlg.SetYesNoCancelLabels("View Github Issue", "Download Anyways", "Cancel")
                result = dlg.ShowModal()
                if result == wx.ID_CANCEL:
                    return
                elif result == wx.ID_YES:
                    webbrowser.open("https://github.com/dortania/OpenCore-Legacy-Patcher/issues/1021")
                    return

            host_space = utilities.get_free_space()
            needed_space = selected_installer['InstallAssistant']['Size'] * 2
            if host_space < needed_space:
                logging.error(f"Insufficient space to download and extract: {utilities.human_fmt(host_space)} available vs {utilities.human_fmt(needed_space)} required")
                dlg = wx.MessageDialog(self.frame_modal, f"You do not have enough free space to download and extract this installer. Please free up some space and try again\n\n{utilities.human_fmt(host_space)} available vs {utilities.human_fmt(needed_space)} required", "Insufficient Space", wx.OK | wx.ICON_WARNING)
                dlg.ShowModal()
                return

            self.frame_modal.Close()

            expected_checksum, checksum_algo = self.catalog_products.checksum_for_product(selected_installer)

            download_obj = network_handler.DownloadObject(
                selected_installer["InstallAssistant"]["URL"], self.constants.payload_path / "InstallAssistant.pkg", checksum_algo=checksum_algo
            )

            gui_download.DownloadFrame(
                self,
                title=self.title,
                global_constants=self.constants,
                download_obj=download_obj,
                item_name=f"macOS {selected_installer['Version']} ({selected_installer['Build']})",
                download_icon=self.constants.icons_path[self._macos_version_to_icon(selected_installer["InstallAssistant"]["XNUMajor"])]
            )

            if download_obj.download_complete is False:
                self.on_return_to_main_menu()
                return

            self._validate_installer(expected_checksum, download_obj.checksum)

    def _validate_installer(self, expected_checksum: str, calculated_checksum: str) -> None:
        if expected_checksum != calculated_checksum:
            logging.error(f"Checksum validation failed: Expected {expected_checksum}, got {calculated_checksum}")
            wx.MessageBox(f"Checksum validation failed!\n\nThis generally happens when downloading on unstable connections such as WiFi or cellular.\n\nPlease try redownloading again on a stable connection (ie. Ethernet)", "Corrupted Installer!", wx.OK | wx.ICON_ERROR)
            self.on_return_to_main_menu()
            return

        self.SetSize((300, 200))
        for child in self.GetChildren():
            child.Destroy()

        logging.info("macOS installer validated")

        title_label = wx.StaticText(self, label="Extracting macOS Installer", pos=(-1, 5))
        title_label.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
        title_label.Centre(wx.HORIZONTAL)

        self.chunk_label = wx.StaticText(self, label="May take a few minutes...", pos=(-1, title_label.GetPosition()[1] + title_label.GetSize()[1] + 5))
        self.chunk_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        self.chunk_label.Centre(wx.HORIZONTAL)

        self.extract_progress_bar = wx.Gauge(self, range=100, pos=(-1, self.chunk_label.GetPosition()[1] + self.chunk_label.GetSize()[1] + 5), size=(270, 30))
        self.extract_progress_bar.Centre(wx.HORIZONTAL)

        self.SetSize((-1, self.extract_progress_bar.GetPosition()[1] + self.extract_progress_bar.GetSize()[1] + 40))
        self.Show()

        self.extract_animation = gui_support.GaugePulseCallback(self.constants, self.extract_progress_bar)
        self.extract_animation.start_pulse()

        def extract_installer():
            result = macos_installer_handler.InstallerCreation().install_macOS_installer(self.constants.payload_path)
            self._safe_call_after(self._on_extraction_complete, result)

        thread = threading.Thread(target=extract_installer, daemon=True)
        thread.start()

    def _on_extraction_complete(self, result: bool):
        if not self:
            return
        self.extract_animation.stop_pulse()
        self.extract_progress_bar.Hide()
        
        self.chunk_label.SetLabel("Successfully extracted macOS installer" if result is True else "Failed to extract macOS installer")
        self.chunk_label.Centre(wx.HORIZONTAL)

        create_installer_button = wx.Button(self, label="Create macOS Installer", pos=(-1, self.extract_progress_bar.GetPosition()[1]), size=(170, 30))
        create_installer_button.Bind(wx.EVT_BUTTON, self.on_existing)
        create_installer_button.Centre(wx.HORIZONTAL)
        if result is False:
            create_installer_button.Disable()

        return_button = wx.Button(self, label="Return to Main Menu", pos=(-1, create_installer_button.GetPosition()[1] + create_installer_button.GetSize()[1]), size=(150, 30))
        return_button.Bind(wx.EVT_BUTTON, self.on_return_to_main_menu)
        return_button.Centre(wx.HORIZONTAL)

        self.SetSize((-1, return_button.GetPosition()[1] + return_button.GetSize()[1] + 40))

        if result is False:
            wx.MessageBox("An error occurred while extracting the macOS installer. Could be due to a corrupted installer", "Error", wx.OK | wx.ICON_ERROR, self)
            return

        user_input = wx.MessageBox("Finished extracting the installer, would you like to continue and create a macOS installer?", "Create macOS Installer?", wx.YES_NO | wx.ICON_QUESTION, self)
        if user_input == wx.YES:
            self.on_existing()

    def on_download(self, event: wx.Event) -> None:
        self.frame_modal.Close()
        # Note: self.parent (the original MainFrame) is *this* frame's own
        # wx parent (set in __init__), and self keeps running the download/
        # catalog flow from here on. Calling self.parent.Destroy() directly
        # would, per wx's window-deletion rules, also destroy self right
        # along with it (child frames are destroyed together with their
        # parent) - which was pulling the whole app down (zero top-level
        # windows left -> auto-quit) the moment the parent's deferred
        # Destroy() actually ran, usually while the AppleDB fetch thread
        # below was still in flight. Hiding it and registering it for
        # deferred destruction avoids that: it still gets torn down
        # through wx's own controlled path, just at actual app exit
        # (PatcherApp.OnExit()) instead of immediately - by which point
        # self is going away too, so the cascade no longer matters.
        self.parent.Hide()
        gui_support.register_orphaned_frame(self.parent)
        # Clean up that orphan the moment self itself is actually torn
        # down - whether via Close() (Cmd+Q/menu Quit) or a direct
        # Destroy() elsewhere (e.g. on_return_to_main_menu()) - rather
        # than waiting for PatcherApp.OnExit(). Leaving self.parent alive-
        # but-hidden until final app exit means it still counts as an
        # open top-level window in the meantime, so closing self alone
        # never brings the window count to zero and wx's own "quit once
        # the last window closes" never fires on its own - that's why
        # Cmd+Q needed a second, raw press to actually end things.
        # wx.EVT_WINDOW_DESTROY fires only once self has *already* fully
        # torn itself down, so destroying self.parent here can never
        # cascade onto a still-in-use self - the exact problem that made
        # destroying it immediately, back when this used to call
        # self.parent.Destroy() directly, unsafe.
        self.Bind(wx.EVT_WINDOW_DESTROY, lambda event: gui_support.destroy_orphaned_frames())
        self._generate_catalog_frame()

    def on_existing(self, event: wx.Event = None) -> None:
        frames = [self, self.frame_modal]
        screen_pos = self.GetScreenPosition() if self else None

        # Create (and show - macOSInstallerFlashFrame.__init__ already calls
        # Show()) the next frame BEFORE tearing down these ones. wx.App's
        # default SetExitOnFrameDelete(True) quits the whole app the instant
        # the top-level window count hits zero, so closing every existing
        # frame first - as this used to do - could hit that and shut the
        # app down before the Flash Frame ever got a chance to be shown.
        flash_frame = gui_macos_installer_flash.macOSInstallerFlashFrame(
            None,
            title=self.title,
            global_constants=self.constants,
            **({"screen_location": screen_pos} if screen_pos else {})
        )

        for frame in frames:
            if frame:
                try:
                    frame.Close()
                except Exception:
                    pass

        for frame in frames:
            if frame:
                try:
                    frame.Destroy()
                except Exception:
                    pass

        # self.parent is the *original* MainFrame - the one EntryPoint.start()
        # bound EVT_CLOSE to (see gui_entry.py's OnCloseFrame), which explicitly
        # calls app.ExitMainLoop() once it's done cleaning up. That handler is
        # meant for a genuine app quit; frame.Close() on self.parent used to be
        # included in the loop above right alongside self/self.frame_modal, so
        # it fired immediately after the Flash Frame was created above - ending
        # the whole app's event loop before the user ever got to see that frame
        # (matches the "Installer(s) found" log line being followed straight by
        # "Cleaning up wxPython GUI"). Hide it and defer destruction to real app
        # exit instead, same pattern as on_download() above.
        if self.parent:
            self.parent.Hide()
            gui_support.register_orphaned_frame(self.parent)
            flash_frame.Bind(wx.EVT_WINDOW_DESTROY, lambda event: gui_support.destroy_orphaned_frames())

    def on_return(self, event: wx.Event) -> None:
        self.frame_modal.Close()

    def on_return_to_main_menu(self, event: wx.Event = None) -> None:
        # Failure paths (cancelled/failed download, bad checksum) funnel through here,
        # and one of them is "the user quit mid-download". Building a fresh MainFrame
        # during teardown puts a window back on screen on the way out and touches
        # frames quit_app() has already destroyed, so there is nothing to return to.
        if not self or gui_support.is_app_exiting():
            return

        if self.frame_modal:
            self.frame_modal.Hide()
        
        main_menu_frame = gui_main_menu.MainFrame(
            None,
            title=self.title,
            global_constants=self.constants,
            screen_location=self.GetScreenPosition()
        )
        main_menu_frame.Show()
        
        if self.frame_modal:
            self.frame_modal.Destroy()
        self.Destroy()
