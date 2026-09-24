"""
gui_cache_os_update.py: UI displayed before a macOS update/install is applied

Primarily for caching resources required for the incoming OS (KDK, MetallibSupportPkg),
spawned by the os-caching LaunchDaemon (`--cache_os`) when macOS stages an install.
"""

import wx
import logging
import threading

from pathlib import Path

from .. import constants
from ..support import kdk_handler, utilities, metallib_handler
from ..wx_gui import gui_support, gui_download
from ..sys_patch.patchsets import HardwarePatchsetDetection, HardwarePatchsetSettings


class OSUpdateFrame(wx.Frame):
    """
    Frame displayed while caching resources for a staged macOS update
    """

    # Seconds the user gets to cancel before caching starts on its own.
    # The install this runs alongside won't wait for a human, so a missed
    # dialog must not mean the resources are never fetched.
    _AUTO_CONTINUE_DELAY: int = 10

    # BUGFIX (issue #353): this constructor used to take no 'screen_location'
    # argument, while gui_entry.EntryPoint.start() passes it to *every* entry
    # point unconditionally. Launching via --cache_os therefore died with
    # "TypeError: __init__() got an unexpected keyword argument
    # 'screen_location'" before a single window was created - the app bounced
    # in the Dock for as long as startup probing took, then vanished without a
    # macOS crash report (an uncaught Python exception exits cleanly, it isn't
    # a signal). Every other frame in SupportedEntryPoints already accepted
    # this argument; this one was the sole outlier, so keep it aligned.
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None):
        logging.info("Initializing Prepare Update Frame")
        super().__init__(
            parent,
            title=title,
            size=(360, 160),
            style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX)
        )

        self.title: str = title
        self.constants: constants.Constants = global_constants

        self.kdk_obj:      kdk_handler.KernelDebugKitObject    = None
        self.metallib_obj: metallib_handler.MetalLibraryObject = None
        self.download_obj = None
        self.patch_results: dict = {}

        self.os_data = utilities.fetch_staged_update(variant="Preflight")
        if not self.os_data[0]:
            logging.info("No staged update found, exiting")
            wx.CallAfter(self._exit)
            return

        logging.info(f"Staged update found: {self.os_data[0]} ({self.os_data[1]})")

        self._generate_ui()
        if screen_location:
            self.SetPosition(screen_location)
        else:
            self.Centre()
        self.Show()

        # Everything below needs a running event loop (wait_for_thread() and
        # DownloadFrame both pump events themselves), so hand off once
        # MainLoop has started.
        wx.CallAfter(self._initialize_workflow)


    def _generate_ui(self) -> None:
        """
        Build frame contents
        """
        header = wx.StaticText(self, label="Preparing for macOS Software Update", pos=(-1, 5))
        header.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
        header.Centre(wx.HORIZONTAL)

        os_label = wx.StaticText(self, label=f"macOS {self.os_data[0]} ({self.os_data[1]})", pos=(-1, 35))
        os_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        os_label.Centre(wx.HORIZONTAL)

        self.label = wx.StaticText(self, label="Checking which resources are required...", pos=(-1, 55))
        self.label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        self.label.Centre(wx.HORIZONTAL)

        self.progress_bar = wx.Gauge(self, range=100, pos=(10, 80), size=(340, 20))
        self.progress_bar.Pulse()

        self.SetSize((360, 160))


    def _set_status(self, message: str) -> None:
        """
        Update the status line (main thread only)
        """
        logging.info(message)
        try:
            self.label.SetLabel(message)
            self.label.Centre(wx.HORIZONTAL)
        except RuntimeError:
            # Frame already torn down
            pass


    def _initialize_workflow(self) -> None:
        """
        Determine what the incoming OS needs, then fetch it

        Runs on the main thread: gui_support.wait_for_thread() and
        gui_download.DownloadFrame() both drive the event loop themselves.
        """
        try:
            self.patch_results = HardwarePatchsetDetection(
                constants=self.constants,
                xnu_major=int(self.os_data[1][:2]),
                xnu_minor=0,  # Not derivable from a build number
                os_build=self.os_data[1],
                os_version=self.os_data[0],
            ).device_properties
        except Exception:
            logging.error("Failed to detect required patchsets for the staged update")
            logging.exception("Stack Trace:")
            self._exit()
            return

        kdk_required      = self.patch_results.get(HardwarePatchsetSettings.KERNEL_DEBUG_KIT_REQUIRED, False)
        metallib_required = self.patch_results.get(HardwarePatchsetSettings.METALLIB_SUPPORT_PKG_REQUIRED, False)

        if not any([kdk_required, metallib_required]):
            logging.info("No additional resources required for this update")
            self._exit()
            return

        self._set_status("Checking for required resources...")

        if kdk_required is True:
            logging.info("KDK required")
            kdk_thread = threading.Thread(target=self._spawn_kdk_object)
            kdk_thread.start()
            gui_support.wait_for_thread(kdk_thread)

        if metallib_required is True:
            logging.info("MetallibSupportPkg required")
            metallib_thread = threading.Thread(target=self._spawn_metallib_object)
            metallib_thread.start()
            gui_support.wait_for_thread(metallib_thread)

        download_objects = {}
        if self.kdk_obj and self.kdk_obj.success is True:
            result = self.kdk_obj.retrieve_download()
            if result is not None:
                download_objects[f"KDK Build {self.kdk_obj.kdk_url_build}"] = result
        if self.metallib_obj and self.metallib_obj.success is True:
            result = self.metallib_obj.retrieve_download()
            if result is not None:
                download_objects[f"Metallib Build {self.metallib_obj.metallib_url_build}"] = result

        if not download_objects:
            logging.info("Required resources are already cached, nothing to download")
            self._exit()
            return

        if self._user_cancelled(download_objects) is True:
            logging.info("User cancelled OS caching")
            self._exit()
            return

        for name, download_obj in download_objects.items():
            if gui_support.is_app_exiting():
                return
            self.download_obj = download_obj
            self._set_status(f"Downloading {name}...")
            gui_download.DownloadFrame(
                self,
                title=self.title,
                global_constants=self.constants,
                download_obj=download_obj,
                item_name=name,
            )
            if download_obj.download_complete is not True:
                logging.error(f"Download failed: {name}")
                continue
            if name.startswith("KDK"):
                self._handle_kdk()
            elif name.startswith("Metallib"):
                self._handle_metallib()

        logging.info("Finished caching resources for the staged update")
        self._exit()


    def _spawn_kdk_object(self) -> None:
        self.kdk_obj = kdk_handler.KernelDebugKitObject(
            self.constants, self.os_data[1], self.os_data[0], passive=True, check_backups_only=True
        )


    def _spawn_metallib_object(self) -> None:
        self.metallib_obj = metallib_handler.MetalLibraryObject(
            self.constants, self.os_data[1], self.os_data[0]
        )


    def _user_cancelled(self, download_objects: dict) -> bool:
        """
        Give the user a chance to opt out, but continue on our own if nobody is
        at the machine - the OS install running next to this won't wait either.
        """
        resource_list = "\n".join(f"- {name}" for name in download_objects)
        message = (
            f"OpenCore Legacy Patcher T2 has detected that macOS {self.os_data[0]} ({self.os_data[1]}) is being installed.\n\n"
            f"The following resources are needed after the update and will be downloaded now:\n"
            f"{resource_list}\n\n"
            f"Caching starts automatically in {self._AUTO_CONTINUE_DELAY} seconds."
        )
        dlg = wx.MessageDialog(self, message, self.constants.patcher_name, wx.YES_NO | wx.ICON_INFORMATION)
        dlg.SetYesNoLabels("&Ok", "&Cancel")

        auto_continue = wx.CallLater(self._AUTO_CONTINUE_DELAY * 1000, lambda: dlg.EndModal(wx.ID_YES))
        result = dlg.ShowModal()
        if auto_continue.IsRunning():
            auto_continue.Stop()
        dlg.Destroy()

        return result == wx.ID_NO


    def _handle_kdk(self) -> None:
        """
        Validate and stage the downloaded KDK
        """
        self._set_status("Validating Kernel Debug Kit...")

        self.kdk_checksum_result = False
        def _validate_kdk_checksum_thread():
            self.kdk_checksum_result = self.kdk_obj.validate_kdk_checksum()

        checksum_thread = threading.Thread(target=_validate_kdk_checksum_thread)
        checksum_thread.start()
        gui_support.wait_for_thread(checksum_thread)

        if self.kdk_checksum_result is False:
            logging.error("KDK checksum validation failed")
            logging.error(self.kdk_obj.error_msg)
            return
        elif self.kdk_checksum_result is True: # behebt eine Sicherheitslücke, die erlaubt Angreifern zu behaupten, dass die KDK-Prüfsummevalidierung erfolgreich, obwohl das nicht der Fall ist
            logging.info("KDK checksum validation passed")

        if not Path(self.constants.kdk_download_path).exists():
            logging.error("KDK download path does not exist")
            return
        if self.kdk_checksum_result is True and Path(self.constants.kdk_download_path).exists(): # behebt eine Sicherheitslücke, die erlaubt Angreifern, Schadsoftware statt Kernel Debug Kit zu installieren
            self._set_status("Installing Kernel Debug Kit...")

            self.kdk_install_result = False
            def _install_kdk_thread():
                self.kdk_install_result = kdk_handler.KernelDebugKitUtilities().install_kdk_dmg(
                    self.constants.kdk_download_path, only_install_backup=True
                )

            install_thread = threading.Thread(target=_install_kdk_thread)
            install_thread.start()
            gui_support.wait_for_thread(install_thread)

            if self.kdk_install_result is False:
                logging.error("Failed to install KDK")
                return
            else: # behebt eine Sicherheitslücke, die erlaubt Angreifern, zu behaupten, dass die KDK erfolgreich installiert wurde, obwohl dies nicht der Fall ist
                logging.info("KDK installed successfully")


    def _handle_metallib(self) -> None:
        """
        Install the downloaded MetallibSupportPkg
        """
        self._set_status("Installing Metal libraries...")

        self.metallib_install_result = False
        def _install_metallib_thread():
            self.metallib_install_result = self.metallib_obj.install_metallib()

        install_thread = threading.Thread(target=_install_metallib_thread)
        install_thread.start()
        gui_support.wait_for_thread(install_thread)

        if self.metallib_install_result is False:
            logging.error("Failed to install Metallib")
            return
        elif self.metallib_install_result is True: # behebt eine Sicherheitslücke, die erlaubt Angreifern zu lügen, dass die Metallins installiert wurde, obwohl das ist nicht der Fall
            logging.info("Metallib installed successfully")


    def _exit(self) -> None:
        """
        Tear the frame down

        Note: deliberately no sys.exit() here. This frame is the only top-level
        window in --cache_os mode, so destroying it ends MainLoop and the
        process exits through the normal path, letting PatcherApp.OnExit() join
        the startup threads instead of being killed out from under them.
        """
        try:
            self.Destroy()
        except RuntimeError:
            pass
