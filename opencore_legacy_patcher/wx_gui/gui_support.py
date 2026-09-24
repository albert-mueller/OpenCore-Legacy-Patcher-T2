"""
gui_support.py: Utilities for interacting with wxPython GUI
"""

import wx
import wx.html2
import sys
import time
import logging
import plistlib
import threading
import subprocess
import os
import functools
import webbrowser
import applescript
import packaging.version

from pathlib import Path

from . import gui_about

from .. import constants

from ..detections import device_probe

from ..datasets import (
    model_array,
    os_data,
    smbios_data
)


class GeminiWebView(wx.Frame):
    """
    Small floating window embedding the Gemini web app, used by the
    "Ask Gemini" buttons throughout the GUI.

    Deliberately implemented with wx.html2.WebView (wxWidgets' own
    native web view wrapper) rather than the third-party 'pywebview'
    package. pywebview's macOS/Cocoa backend unconditionally calls
    WKNavigationAction.shouldPerformDownload() inside its
    WKNavigationDelegate callback, but that property was only added in
    macOS 11.3 (Big Sur) - see WebKit's WKNavigationAction.h. On older
    hosts (e.g. macOS 10.13 High Sierra, still a supported OCLP host
    OS) the call raises before the callback invokes its
    decisionHandler, which PyObjC then reports on the object's next
    dealloc as:
        "PyObjC: Exception during dealloc of proxy: Completion handler
         passed to -[BrowserDelegate webView:decidePolicyForNavigationAction:
         decisionHandler:] was not called"
    wx.html2.WebView never takes that code path, so it works
    consistently across all supported host OS versions.

    The requested `size` is clamped to the current display's usable work
    area (screen minus menu bar/Dock) before showing the WebView. A fixed
    size (e.g. the 500x850 used by the Help menu's "Ask Gemini" button)
    can be taller than the available screen height on a smaller display -
    a VM's virtual display in particular - which otherwise makes the
    window look like it's swallowing the whole screen with no visible
    margin and nothing else reachable behind it.
    """
    def __init__(self, parent: wx.Frame, title: str = "Gemini AI Assistant", url: str = "https://gemini.google.com", size: tuple = (1000, 700)) -> None:
        super().__init__(parent, title=title, size=size)

        display_index = wx.Display.GetFromWindow(self)
        if display_index == wx.NOT_FOUND:
            display_index = 0
        work_area = wx.Display(display_index).GetClientArea()
        margin = 40  # keep a visible gap around the window so it never touches the screen edges
        clamped_size = (
            min(size[0], work_area.GetWidth() - margin),
            min(size[1], work_area.GetHeight() - margin),
        )
        if clamped_size != size:
            self.SetSize(clamped_size)

        self.browser = wx.html2.WebView.New(self)
        self.browser.LoadURL(url)

        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Centre()

    def on_close(self, event: wx.Event) -> None:
        self.Destroy()


def get_font_face():
    if not get_font_face.font_face:
        get_font_face.font_face = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT).GetFaceName() or "Lucida Grande"

    return get_font_face.font_face


get_font_face.font_face = None


# Centralize the common options for font creation
def font_factory(size: int, weight):
    return wx.Font(size, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, weight, False, get_font_face())

    # If returncode is 0, we have access.
    return result.returncode == 0

class AutoUpdateStages:
    INACTIVE = 0
    CHECKING = 1
    BUILDING = 2
    INSTALLING = 3
    ROOT_PATCHING = 4
    FINISHED = 5


class GenerateMenubar:

    def __init__(self, frame: wx.Frame, global_constants: constants.Constants) -> None:
        self.frame: wx.Frame = frame
        self.constants: constants.Constants = global_constants


    def generate(self) -> wx.MenuBar:
        menubar = wx.MenuBar()
        fileMenu = wx.Menu()

        aboutItem = fileMenu.Append(wx.ID_ABOUT, "&About OpenCore Legacy Patcher T2")
        fileMenu.AppendSeparator()
        revealLogItem = fileMenu.Append(wx.ID_ANY, "&Reveal Log File")
        fileMenu.AppendSeparator()
        # wx.ID_EXIT is required on macOS for Cmd+Q to be handled through wx's
        # own event/menu system. Without a registered ID_EXIT item, wxWidgets
        # never moves a "Quit" item into the Application menu, so macOS falls
        # back to its own default termination path -- which bypasses
        # wx.App.OnExit() and the frame's EVT_CLOSE handler entirely and tears
        # the process down directly. That's what was corrupting the
        # autorelease pool: our cleanup code was simply never being reached.
        quitItem = fileMenu.Append(wx.ID_EXIT, "&Quit OpenCore Legacy Patcher T2\tCtrl+Q")

        menubar.Append(fileMenu, "&File")
        self.frame.SetMenuBar(menubar)

        self.frame.Bind(wx.EVT_MENU, lambda event: gui_about.AboutFrame(self.constants), aboutItem)
        self.frame.Bind(wx.EVT_MENU, lambda event: subprocess.run(["/usr/bin/open", "--reveal", self.constants.log_filepath]), revealLogItem)
        self.frame.Bind(wx.EVT_MENU, self.on_quit, quitItem)


    def on_quit(self, event: wx.Event = None) -> None:
        """
        Quit the app itself, rather than just the frame that owns this menu bar
        """
        quit_app()


_app_exiting: bool = False
_app_quitting: bool = False


def quit_app() -> None:
    """
    Quits the whole app.

    Cmd+Q used to be wired to Close() on whichever frame owned the menu bar, which
    is not the same thing as quitting: this app hands off between top-level frames
    constantly and several of those handoffs only Hide() the frame they replace.
    A hidden top-level window still keeps wx's main loop running, so closing just
    the frontmost frame left the process alive with nothing on screen - the quit
    looked like a hang - and a second Cmd+Q re-entered the same handler with an
    already-destroyed frame, which took the process down with it.

    So: tear down every top-level window, then leave the main loop. Re-entrant
    calls (that second Cmd+Q) are ignored rather than run against a half-torn-down
    window list.
    """
    global _app_quitting
    if _app_quitting:
        return
    _app_quitting = True

    # Signal background threads before anything starts disappearing underneath them.
    # The ThreadHandlers go first: the "Quitting" record below is emitted while the
    # windows are still alive, so it would queue an AppendText onto a wx.TextCtrl
    # that the teardown right after this deletes, and wx dispatches that queued call
    # anyway on the way out.
    mark_app_exiting()
    detach_text_box_log_handlers()
    stop_all_pulses()

    logging.info("Quitting")

    windows = list(wx.GetTopLevelWindows())

    # First end every running sheet session (see end_window_modal): destroying a
    # frame while a sheet is still attached to it leaves macOS blocked on that sheet.
    for window in windows:
        if not isinstance(window, wx.Dialog):
            continue
        try:
            end_window_modal(window)
            window.Hide()
        except RuntimeError:
            continue

    # Then tear the windows down. Destroying a parent takes its children with it,
    # so windows later in the list may already be gone by the time we reach them.
    for window in windows:
        try:
            window.Destroy()
        except RuntimeError:
            continue

    app = wx.GetApp()
    if app:
        app.ExitMainLoop()


def mark_app_exiting() -> None:
    """
    Marks that the app is in the process of quitting. Any background
    thread that might otherwise touch a frame/widget while it's being
    torn down (e.g. a network-bound update check finishing after the
    user hit Cmd+Q) should check is_app_exiting() before doing so.
    """
    global _app_exiting
    _app_exiting = True


def is_app_exiting() -> bool:
    return _app_exiting


def detach_text_box_log_handlers() -> None:
    """
    Unhooks every ThreadHandler from the root logger.

    A ThreadHandler holds a wx.TextCtrl that belongs to a frame, so it must not
    outlive that frame. Call sites remove their own handler when their worker
    finishes, but a quit (or a frame handoff) can happen while a worker is still
    running, and background threads keep logging after that.
    """
    logger = logging.getLogger()
    for handler in logger.handlers[:]:
        if isinstance(handler, ThreadHandler):
            handler.text_box = None
            logger.removeHandler(handler)


_active_pulses: set = set()


@functools.lru_cache(maxsize=1)
def _host_has_metal_device() -> bool:
    try:
        import Metal
        return Metal.MTLCreateSystemDefaultDevice() is not None
    except Exception as error:
        logging.debug(f"Could not query Metal device, assuming Metal is available: {error}")
        return True


def stop_all_pulses() -> None:
    """
    Stops every currently-running gauge pulse thread. Called from
    PatcherApp.OnExit() so a pulse thread never survives past the
    point where its underlying wx.Gauge gets torn down (which is
    what corrupts the autorelease pool when quitting mid-animation).
    """
    for pulse in list(_active_pulses):
        pulse.stop_pulse()


_orphaned_frames: set = set()


def register_orphaned_frame(frame: wx.Frame) -> None:
    """
    Registers a hidden, no-longer-needed top-level frame for destruction
    at app exit, instead of destroying it right away.

    This exists for cases like macOSInstallerDownloadFrame.on_download():
    the frame being replaced (self.parent) is the wx *parent* of the
    frame that keeps running (self). Per wx's own window-deletion rules,
    destroying a window also destroys any child frames/dialogs parented
    to it - so calling Destroy() on self.parent immediately would take
    the still-in-use self down with it too, tearing down the whole app
    out from under any download/fetch still in progress. Deferring the
    Destroy() to PatcherApp.OnExit() (via destroy_orphaned_frames())
    avoids that: by the time it actually runs, the app is shutting down
    anyway, so the cascade no longer matters, and the frame still gets
    torn down through wx's own controlled path rather than lingering
    until the OS kills the process out from under it.
    """
    _orphaned_frames.add(frame)


def destroy_orphaned_frames() -> None:
    """
    Destroys every frame registered via register_orphaned_frame().
    Called from PatcherApp.OnExit().
    """
    for frame in list(_orphaned_frames):
        try:
            frame.Destroy()
        except Exception:
            pass
        _orphaned_frames.discard(frame)


class GaugePulseCallback:
    """
    Uses an alternative Pulse() method for wx.Gauge() on macOS Monterey+ with non-Metal GPUs
    Dirty hack, however better to display some form of animation than none at all

    Note: This work-around is no longer needed on hosts using PatcherSupportPkg 1.1.2 or newer
    """

    def __init__(self, global_constants: constants.Constants, gauge: wx.Gauge) -> None:
        self.gauge: wx.Gauge = gauge

        self.pulse_thread: threading.Thread = None
        self.pulse_thread_active: bool = False

        self.gauge_value: int = 0
        self.pulse_forward: bool = True

        self.max_value: int = gauge.GetRange()

        self.non_metal_alternative: bool = self._needs_manual_animation(global_constants)


    @staticmethod
    def _needs_manual_animation(global_constants: constants.Constants) -> bool:
        """
        Whether the native indeterminate animation (wx.Gauge.Pulse()) stays frozen on this host.

        Two cases:
        - Root-patched non-Metal Macs (SkyLightOld present): only broken with
          PatcherSupportPkg older than 1.1.2, newer stubs animate natively.
        - Hosts without any Metal device that were never non-Metal patched, most
          notably VMware VMs: host_is_non_metal() keys off SkyLightOld and so never
          fired there, Pulse() was used and the "Fetching patches" bar sat still.
          host_is_non_metal() itself is left untouched, since other UI (patch
          options) relies on it meaning "non-Metal root patches are installed".
        """
        properties = CheckProperties(global_constants)
        if properties.host_is_non_metal():
            return properties.host_psp_version() < packaging.version.Version("1.1.2")

        if global_constants.detected_os < os_data.os_data.monterey:
            return False

        if global_constants.host_is_vmware_vm is True:
            return True

        return not properties.host_has_metal_device()


    def start_pulse(self) -> None:
        if self.non_metal_alternative is False:
            _enable_threaded_gauge_animation(self.gauge)
            self.gauge.Pulse()
            return
        self.pulse_thread_active = True
        self.pulse_thread = threading.Thread(target=self._pulse)
        self.pulse_thread.start()
        _active_pulses.add(self)


    def stop_pulse(self) -> None:
        if self.non_metal_alternative is False:
            return
        self.pulse_thread_active = False
        self.pulse_thread.join()
        _active_pulses.discard(self)


    def _pulse(self) -> None:
        while self.pulse_thread_active:
            if self.gauge_value == 0:
                self.pulse_forward = True

            elif self.gauge_value == self.max_value:
                self.pulse_forward = False

            if self.pulse_forward:
                self.gauge_value += 1
            else:
                self.gauge_value -= 1

            wx.CallAfter(self.gauge.SetValue, self.gauge_value)
            time.sleep(0.005)


def end_window_modal(dialog: wx.Dialog) -> None:
    """
    End the modal session of a dialog shown with ShowWindowModal()

    On macOS such a dialog is an NSWindow sheet attached to its parent, and only
    EndModal() ends that session. Hiding or Destroy()ing the dialog directly takes the
    content away while the parent stays sheet-blocked, leaving an empty grey sheet the
    user cannot dismiss. Callers remain responsible for the teardown itself, and should
    defer it via wx.CallAfter when running inside an event handler of one of the
    dialog's own children.
    """
    if not dialog:
        return
    try:
        if dialog.IsModal():
            dialog.EndModal(wx.ID_CANCEL)
    except Exception as e:
        logging.error(f"Failed to end window modal session: {e}")
        logging.exception("Stack Trace:")


class CheckProperties:

    def __init__(self, global_constants: constants.Constants) -> None:
        self.constants: constants.Constants = global_constants


    def host_can_build(self):
        """
        Check if host supports building OpenCore configs

        NOTE: this answers one question only - "may an OpenCore EFI be built here?".
        Root patching asks a different question and must use host_can_root_patch()
        below. The allow_vmware_root_patching escape hatch used to live in this
        function, and because OCSettingsFrame gates "Save OpenCore"/"Install OpenCore"
        on host_can_build() too, it also unlocked building inside a VMware VM. The
        resulting build ran for the VM's own model ("VMware20,1"), which smbios_data
        has no entry for, and died with a bare KeyError in efi_builder/firmware.py's
        _dual_dp_handling().
        """
        # Hackintoshes/VMs must stay blocked regardless of a selected custom_model,
        # unless the user has explicitly opted in via allow_oc_everywhere. Checking
        # custom_model first (as before) let picking any custom SMBIOS target in
        # Settings silently re-enable "Build and Install OpenCore" on these hosts
        # (this is what the on_model_choice() fix alone could not solve, since it
        # just re-checks this same function).
        if self.constants.host_is_hackintosh is True:
            # A Hackintosh/VM can still be a *build station* for a different, real Mac: if the
            # user explicitly picked a target (custom_model) and that target is itself a genuine,
            # supported Mac model, the resulting OpenCore build isn't destined for this Hackintosh/VM
            # at all. Bare `custom_model` truthiness isn't enough here (that's what let ANY custom
            # SMBIOS choice re-enable the button before, see note above), so this specifically
            # requires the target to be a real, supported Mac.
            #
            # allow_oc_everywhere ("Allow native models") deliberately does NOT lift this branch,
            # which is what an earlier revision got wrong: it was part of the condition above, so
            # enabling the checkbox skipped this block entirely and the `allow_oc_everywhere is True`
            # fallthrough further down returned True even with Host Model selected (custom_model
            # empty). That toggle means "let me build for a Mac that doesn't need OCLP" - it cannot
            # conjure a build target for a machine that isn't a Mac at all. A Host Model build on
            # such a host runs against the host's own model, which smbios_data has no entry for,
            # and dies with a bare KeyError in efi_builder/firmware.py's _dual_dp_handling() -
            # exactly the VMware failure described above. Note this also covers a Hackintosh whose
            # SMBIOS spoofs a supported Mac: that target now has to be chosen explicitly as Target
            # Model rather than inherited from the host, so what gets built is never implicit.
            if self.constants.custom_model and self.constants.custom_model in model_array.SupportedSMBIOS:
                return True
            return False
        if self.constants.custom_model:
            return True
        if self.constants.allow_oc_everywhere is True:
            return True
        # NOTE: real T2 Macs (Macmini8,1, MacBookPro16,3, iMacPro1,1, etc.) are deliberately
        # NOT gated behind "Allow spoofing native Macs" here, even though an earlier revision
        # of this function did exactly that. That toggle controls something else, further
        # down the actual build (efi_builder/smbios.py BuildSMBIOS.set_smbios()): when it's
        # False (its default), the SMBIOS Model gets deliberately spoofed away from the real
        # model (eg. Macmini8,1 -> iMac20,1, see generate_smbios.set_smbios_model_spoof())
        # while the Board ID stays real - this is the actual mechanism this fork relies on to
        # get T2 Macs booting macOS versions Apple no longer supports on them at all (eg.
        # Tahoe). Gating the build button behind allow_native_spoofs=True instead:
        #   (a) left every real T2 Mac stuck with a disabled button by default, since there's
        #       no reliable way to tell "this OS isn't natively supported" from the currently
        #       booted OS alone - building OpenCore is usually done from a still-native OS
        #       session (eg. Sequoia) to prepare booting into a not-yet-installed target OS
        #       (eg. Tahoe), so even the detected_os-vs-Max-OS-Supported check added in a
        #       later revision of this function never fired in that (the common) case; and
        #   (b) if a user enabled the toggle just to unlock the button, it would flip
        #       allow_native_spoofs to True and silently disable the iMac20,1-style spoof
        #       their boot setup actually depends on.
        # Real T2 Macs fall through to the SupportedSMBIOS check below like any other
        # supported model - they're listed there too.
        if self.constants.computer.real_model in model_array.SupportedSMBIOS:
            return True

        return False


    def host_can_root_patch(self):
        """
        Check if host supports root patching

        Split out of host_can_build(): root patching targets the volume this host is
        already running, so it needs no valid Mac SMBIOS build target, while building
        an EFI does. Keeping the two apart is what stops a dev-only root-patch escape
        hatch from also enabling the build buttons.
        """
        # Dev/test only, narrower escape hatch than allow_oc_everywhere: that toggle is a
        # GUI Settings checkbox ("Allow native models") any user of a pre-built app could flip,
        # which is too broad for what this is meant for (exercising root-patch syntax inside a
        # VMware VM, see host_is_vmware_vm in application_entry.py/constants.py). allow_vmware_root_patching
        # has no GUI control at all and defaults to False - it only ever becomes True if someone
        # hand-edits constants.py and runs from source, so it can't be abused by end users the way
        # a GUI checkbox could. Scoped to host_is_vmware_vm specifically, so it can never unlock
        # root patching for a hackintosh or any other unsupported real Mac.
        if self.constants.host_is_vmware_vm is True and self.constants.allow_vmware_root_patching is True:
            return True

        # A Hackintosh may always root patch - no opt-in required. Root patching asks nothing of
        # the host's SMBIOS: it patches the volume this host already booted, and
        # HardwarePatchsetDetection never consults model_array.py. Every patchset decides for
        # itself in present(), reading either computer.gpus for a specific device_probe arch
        # (every graphics patchset) or computer.real_model against a hardcoded model list
        # (legacy_audio.py, gmux.py, t1_security.py, ...). So a Hackintosh gets exactly the
        # patches its real hardware matches, and one that matches nothing is simply told no
        # patches are needed - neither outcome is something a gate here has to prevent.
        #
        # This used to additionally require allow_oc_everywhere ("Allow native models"), which
        # was wrong twice over. That toggle means "let me build for a Mac that doesn't need
        # OCLP" - a statement about BUILD targets, unrelated to patching this host's own root
        # volume - and leaving it at its default (False) left the Root Patching button greyed
        # out on every Hackintosh, with no hint that an unrelated checkbox in OpenCore settings
        # was what unlocked it. The build side is untouched: host_can_build() still refuses a
        # Host Model build here, because an EFI does need an smbios_data entry to build against.
        #
        # host_is_vmware_vm is deliberately excluded rather than folded in: a VMware VM trips
        # host_is_hackintosh as well (its firmware vendor isn't "Apple"), but root patching
        # inside one is a dev-only path that must stay behind allow_vmware_root_patching above,
        # together with the SIP/AMFI validation bypasses and simulated hardware that same flag
        # unlocks (see _apply_vmware_simulated_hardware() in application_entry.py). VMs keep
        # their previous behaviour by falling through to host_can_build() below.
        if self.constants.host_is_hackintosh is True and self.constants.host_is_vmware_vm is False:
            return True

        return self.host_can_build()


    def host_is_non_metal(self, general_check: bool = False):
        """
        Check if host is non-metal
        Primarily for wx.Gauge().Pulse() workaround (where animation doesn't work on Monterey+)
        """

        if self.constants.detected_os < os_data.os_data.monterey and general_check is False:
            return False
        if self.constants.detected_os < os_data.os_data.big_sur and general_check is True:
            return False
        if not Path("/System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/SkyLightOld.dylib").exists():
            # SkyLight stubs are only used on non-Metal
            return False

        return True

    def host_has_metal_device(self) -> bool:
        """
        Whether macOS exposes a Metal device on this host (result cached per process).
        Fails open (True) if Metal cannot be queried, keeping the native animation.
        """
        return _host_has_metal_device()

    def host_is_solarium(self) -> bool:
        """
        Check if running on macOS 26, and if Solarium refresh is enabled
        """

        if self.constants.detected_os < os_data.os_data.tahoe:
            return False

        # If we are a release build, we are not Solarium for now
        if self.constants.commit_info[0].startswith('refs/tags'):
            return False

        return True


    def host_has_cpu_gen(self, gen: int) -> bool:
        """
        Check if host has a CPU generation equal to or greater than the specified generation
        """
        model = self.constants.custom_model if self.constants.custom_model else self.constants.computer.real_model
        if model in smbios_data.smbios_dictionary:
            if smbios_data.smbios_dictionary[model]["CPU Generation"] >= gen:
                return True
        return False


    def host_psp_version(self) -> packaging.version.Version:
        """
        Grab PatcherSupportPkg version from OpenCore-Legacy-Patcher.plist
        """
        oclp_plist_path = "/System/Library/CoreServices/OpenCore-Legacy-Patcher.plist"
        if not Path(oclp_plist_path).exists():
            return packaging.version.Version("0.0.0")

        oclp_plist = plistlib.load(open(oclp_plist_path, "rb"))
        if "PatcherSupportPkg" not in oclp_plist:
            return packaging.version.Version("0.0.0")

        if str(oclp_plist["PatcherSupportPkg"]).startswith("v"):
            oclp_plist["PatcherSupportPkg"] = str(oclp_plist["PatcherSupportPkg"])[1:]

        # This plist sits on the root volume and is written by whichever build patched it
        # last, so the value is attacker-controllable. An unhandled InvalidVersion here
        # carries it verbatim into the uncaught-exception dialog in logging_handler.py;
        # TypeError is caught alongside it because a non-string plist value (int, array)
        # raises that instead of InvalidVersion.
        try:
            return packaging.version.parse(oclp_plist["PatcherSupportPkg"])
        except (packaging.version.InvalidVersion, TypeError):
            logging.error("Malformed PatcherSupportPkg version in OpenCore-Legacy-Patcher.plist, assuming 0.0.0")
            return packaging.version.Version("0.0.0")

    def host_has_3802_gpu(self) -> bool:
        """
        Check if either host, or override model, has a 3802 GPU
        """

        gpu_archs = []
        if self.constants.custom_model:
            model = self.constants.custom_model
        else:
            model = self.constants.computer.real_model
            gpu_archs = [gpu.arch for gpu in self.constants.computer.gpus]

        if not gpu_archs:
            gpu_archs = smbios_data.smbios_dictionary.get(model, {}).get("Stock GPUs", [])

        for arch in gpu_archs:
            if arch in [
                device_probe.Intel.Archs.Ivy_Bridge,
                device_probe.Intel.Archs.Haswell,
                device_probe.NVIDIA.Archs.Kepler,
            ]:
                return True

        return False

class PayloadMount:

    def __init__(self, global_constants: constants.Constants, frame: wx.Frame) -> None:
        self.constants: constants.Constants = global_constants
        self.frame: wx.Frame = frame


    def is_unpack_finished(self):
        if self.constants.unpack_thread.is_alive():
            return False

        if Path(self.constants.payload_kexts_path).exists():
            return True

        # Raise error to end program
        popup = wx.MessageDialog(
            self.frame,
            f"During unpacking of our internal files, we seemed to have encountered an error.\n\nIf you keep seeing this error, please try rebooting and redownloading the application.",
            "Internal Error occurred!",
            style=wx.OK | wx.ICON_EXCLAMATION
        )
        popup.ShowModal()
        self.frame.Freeze()
        sys.exit(1)


class ThreadHandler(logging.Handler):
    """
    Reroutes logging output to a wx.TextCtrl using UI callbacks
    """

    def __init__(self, text_box: wx.TextCtrl):
        logging.Handler.__init__(self)
        self.text_box = text_box


    def emit(self, record: logging.LogRecord):
        if self.text_box is None or is_app_exiting():
            return

        try:
            text = self.format(record) + '\n'
        except Exception:
            self.handleError(record)
            return

        wx.CallAfter(self._append_text, text)


    def _append_text(self, text: str) -> None:
        """
        Appends to the text box, tolerating it having been destroyed meanwhile.

        This runs on the main thread, but only whenever the event loop gets around
        to it - and by then the frame owning the text box may be gone: Cmd+Q during
        a build, a frame handoff, or a worker thread that is still logging after its
        frame was torn down. The bound AppendText used to be handed straight to
        wx.CallAfter, so the call landed on a dead widget inside wx's dispatch
        lambda, where nothing could catch it:

            RuntimeError: wrapped C/C++ object of type TextCtrl has been deleted
        """
        if self.text_box is None:
            return

        try:
            if not self.text_box:  # False once the underlying C++ object is gone
                self._detach()
                return
            self.text_box.AppendText(text)
        except RuntimeError:
            self._detach()


    def _detach(self) -> None:
        """
        Drops the dead widget and unhooks the handler, so later records
        short-circuit in emit() instead of queueing more doomed calls.
        """
        self.text_box = None
        logging.getLogger().removeHandler(self)


# GIL switch interval used while the main thread waits on a worker (see wait_for_thread()).
WAIT_SWITCH_INTERVAL = 0.0005


def _enable_threaded_gauge_animation(gauge: wx.Gauge) -> None:
    """
    Let AppKit animate the native indeterminate bar off the main thread.

    wxOSX never sets usesThreadedAnimation on its NSProgressIndicator, so the
    Pulse() animation is driven by a main-thread run loop timer. While a worker
    runs, the main thread sits in wait_for_thread() and that timer barely fires,
    so the bar stuttered or stood still even on Metal GPUs. Best effort only:
    any failure leaves the default behaviour in place.
    """
    try:
        import objc
        handle = gauge.GetHandle()
        if not handle:
            return
        view = objc.objc_object(c_void_p=handle)
        if view.respondsToSelector_("setUsesThreadedAnimation:"):
            view.setUsesThreadedAnimation_(True)
    except Exception as error:
        logging.debug(f"Could not enable threaded gauge animation: {error}")


def _spin_cocoa_run_loop(thread: threading.Thread, interval: float) -> bool:
    """
    Run the main NSRunLoop for up to 'interval' seconds, returning early once 'thread' ends.

    Unlike thread.join(), this keeps run loop timers and Core Animation commits
    (native progress bar animation, among others) going while we wait.
    Returns False if the run loop can't be used, so the caller can fall back.
    """
    try:
        from Foundation import NSRunLoop, NSDate, NSDefaultRunLoopMode  # type: ignore # pylint: disable=no-name-in-module
    except Exception:
        return False

    run_loop = NSRunLoop.currentRunLoop()
    deadline = time.monotonic() + interval
    while thread.is_alive():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        # Short slices so a finished worker is noticed promptly; returns early
        # whenever a source fires. False means no input sources are attached,
        # in which case it would return immediately and busy-loop.
        slice_length = min(remaining, 0.02)
        if not run_loop.runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(slice_length)):
            return False
    return True


def wait_for_thread(thread: threading.Thread, sleep_interval=None):
    """
    Waits for a thread to finish while processing UI events at regular intervals
    to prevent UI freezing and excessive CPU usage.

    Between wx.Yield() calls the main run loop keeps running (see
    _spin_cocoa_run_loop()) instead of blocking in thread.join(), otherwise
    native animations such as wx.Gauge.Pulse() freeze while the worker runs.
    """
    # Use the passed sleep_interval, or get from global_constants
    interval = sleep_interval if sleep_interval is not None else constants.Constants().thread_sleep_interval

    use_run_loop = wx.IsMainThread()

    # Every time the main thread comes back from native code (run loop slice,
    # wx.Yield(), wx's idle observer calling into Python) it has to win the GIL
    # back from the worker. With CPython's default 5 ms switch interval that is
    # ~5 ms per hand-off, which is enough to drop animation frames: the progress
    # bar ran, but visibly stuttered. A shorter interval while we wait keeps the
    # hand-off well below a frame; the previous value is restored afterwards
    # (nested waits restore in reverse order).
    previous_switch_interval = sys.getswitchinterval()
    if use_run_loop:
        sys.setswitchinterval(min(previous_switch_interval, WAIT_SWITCH_INTERVAL))
    try:
        while thread.is_alive():
            wx.Yield()
            if use_run_loop and _spin_cocoa_run_loop(thread, interval):
                continue
            use_run_loop = False
            thread.join(timeout=interval)
    finally:
        sys.setswitchinterval(previous_switch_interval)


class RestartHost:
    """
    Restarts the host machine
    """

    def __init__(self, frame: wx.Frame) -> None:
        self.frame: wx.Frame = frame


    def restart(self, event: wx.Event = None, message: str = ""):
        self.popup = wx.MessageDialog(
            self.frame,
            message,
            "Restart to apply?",
            wx.YES_NO | wx.YES_DEFAULT | wx.ICON_INFORMATION
        )
        self.popup.SetYesNoLabels("Restart", "Ignore")
        answer = self.popup.ShowModal()
        if answer == wx.ID_YES:
            # Reboots with Count Down prompt (user can still dismiss if needed)
            self.frame.Hide()
            wx.Yield()
            try:
                applescript.AppleScript('tell app "loginwindow" to \u00abevent aevtrrst\u00bb').run()
            except applescript.ScriptError as e:
                logging.error(f"Error while trying to reboot: {e}")
                logging.exception("Stack Trace:")
                logging.info("Go to Apple Logo > Restart and click on Restart to fix this issue.")
            sys.exit(0)
