"""
gui_entry.py: Entry point for the wxPython GUI
"""

import wx
import sys
import logging

from Cocoa import NSApp, NSApplication

from .. import constants
from ..sys_patch.patchsets import HardwarePatchsetDetection
from ..efi_builder.misc import _T2_MODELS # benötigt für T2 Macs


from ..wx_gui import (
    gui_cache_os_update,
    gui_main_menu,
    gui_build,
    gui_install_oc,
    gui_sys_patch_start,
    gui_support,
    gui_update,
    gui_mode_selector,
)


class SupportedEntryPoints:
    """
    Enum for supported entry points
    """
    MAIN_MENU  = gui_main_menu.MainFrame
    MODE_SELECT = gui_mode_selector.ModeSelectorFrame
    BUILD_OC   = gui_build.BuildFrame
    INSTALL_OC = gui_install_oc.InstallOCFrame
    SYS_PATCH  = gui_sys_patch_start.SysPatchStartFrame
    UPDATE_APP = gui_update.UpdateFrame
    OS_CACHE   = gui_cache_os_update.OSUpdateFrame


class PatcherApp(wx.App):
    """
    wx.App subclass so we have a reliable, single place to clean up on
    quit -- OnExit() fires on every quit path (Cmd+Q, Dock > Quit, or the
    last window closing), regardless of which frame is currently active.
    A frame-level EVT_CLOSE handler only ever covers the one frame it was
    bound to, which isn't enough since this app constantly destroys one
    top-level frame and creates another as the user navigates.
    """

    def __init__(self, global_constants: constants.Constants, *args, **kwargs) -> None:
        self.constants: constants.Constants = global_constants
        super().__init__(*args, **kwargs)


    def OnExit(self) -> int:
        # Signal first: any background thread checking is_app_exiting()
        # (e.g. gui_main_menu's update check) should bail out rather than
        # touch a frame that's being torn down.
        gui_support.mark_app_exiting()

        # Startup kicks off a couple of background threads (payload
        # unpacking, analytics) before any window even exists, so they
        # never check is_app_exiting(). Quitting right after launch, while
        # one of these is still doing disk/network work, tears the process
        # down underneath it -- the same autorelease pool corruption
        # trigger, just from a startup thread instead of a UI one. Give
        # each a bounded window to finish rather than hanging quit forever.
        # update_thread (gui_main_menu's update check) already guards its
        # own wx.CallAfter() calls with is_app_exiting(), but it wasn't
        # actually joined here like the other two - closing that gap too.
        for thread_attr in ("unpack_thread", "analytics_thread", "update_thread"):
            thread = getattr(self.constants, thread_attr, None)
            if thread and thread.is_alive():
                thread.join(timeout=10)

        # Stop any still-running gauge pulse thread before Cocoa tears
        # down the window it's animating -- letting it keep firing
        # wx.CallAfter() into a vanishing frame is what corrupts the
        # autorelease pool on quit.
        gui_support.stop_all_pulses()

        # Destroy any frame that was deliberately left alive-but-hidden
        # instead of being torn down immediately (see
        # gui_support.register_orphaned_frame() for why). Doing it here
        # means it goes through wx's own controlled teardown as part of
        # the same shutdown as everything else, rather than surviving
        # until the OS kills the process out from under it.
        gui_support.destroy_orphaned_frames()
        return super().OnExit()


class EntryPoint:

    def __init__(self, global_constants: constants.Constants) -> None:
        self.app: wx.App = None
        self.frame: wx.Frame = None
        self.main_menu_frame: gui_main_menu.MainFrame = None
        self.constants: constants.Constants = global_constants

        self.constants.gui_mode = True


    def _generate_base_data(self) -> None:
        self.app = PatcherApp(self.constants)
        self.app.SetAppName(self.constants.patcher_name)

        # Reference:
        # - https://discuss.wxpython.org/t/macos-window-opens-in-the-background-and-does-not-receive-focus/36763/10
        NSApplication.sharedApplication()
        NSApp().activateIgnoringOtherApps_(True)


    def start(self, entry: SupportedEntryPoints = gui_mode_selector.ModeSelectorFrame, start_patching: bool = False) -> None:
        """
        Launches entry point for the wxPython GUI
        """
        self._generate_base_data()

        # BEHOBEN: "patches" initialisieren, um UnboundLocalError/NameError im else-Zweig zu verhindern
        patches = None
        is_patching_mode = "--gui_patch" in sys.argv or "--gui_unpatch" in sys.argv or start_patching is True

        if is_patching_mode:
            entry = gui_sys_patch_start.SysPatchStartFrame
            patches = HardwarePatchsetDetection(constants=self.constants).device_properties
        elif entry is gui_mode_selector.ModeSelectorFrame:
            # Bypass Mode Selector entirely.
            # If Developer Mode is OFF -> Albert mode (Standard)
            # If Developer Mode is ON -> Matteo mode (T1 Experimental)
            try:
                if not self.constants.Experimental_Features:
                    logging.info(f"Developer Mode is OFF, bypassing Mode Selector → Standard UI")
                    self.constants.app_mode = "albert"
                    entry = gui_main_menu.MainFrame
                else:
                    logging.info(f"Developer Mode is ON, bypassing Mode Selector → Experimental UI")
                    self.constants.app_mode = "matteo"
                    entry = gui_main_menu.MainFrame
            except Exception as e:
                logging.error("We couldn't enter or exit Developer Mode due to an error:")
                logging.exception("Stack Trace:")
                logging.info("Please report this issue.")
                logging.info("In the meanwhile, Developer Mode may be switched off or the app may crash.")

        logging.info(f"Entry point set: {entry.__name__}")

        # Normally set by main.py, but transitions from CLI mode may not have this set
        self.constants.gui_mode = True

        self.frame = entry(
            None,
            title=f"{self.constants.patcher_name} {self.constants.patcher_version}",
            global_constants=self.constants,
            screen_location=None,
            **({"patches": patches} if is_patching_mode else {})
        )

        # BEHOBEN: Gefährliches atexit.register entfernt.
        # Stattdessen nutzen wir das native wxPython Event-Handling für das Schließen des Fensters.
        self.frame.Bind(wx.EVT_CLOSE, self.OnCloseFrame)

        if "--gui_patch" in sys.argv or start_patching is True:
            self.frame.start_root_patching()
        elif "--gui_unpatch" in sys.argv:
            self.frame.revert_root_patching()

        self.app.MainLoop()


    def OnCloseFrame(self, event: wx.Event = None) -> None:
        """
        Closes the wxPython GUI safely using wxWidgets native event lifecycle
        """
        logging.info("Closing wxPython GUI")
        if event:
            event.Skip()
        elif self.frame:
            self.frame.Destroy()
