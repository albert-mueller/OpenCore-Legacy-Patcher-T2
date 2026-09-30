"""
gui_update.py: Generate UI for updating the patcher
"""

import os
import wx
import sys
import time
import shutil
import logging
import threading
import subprocess

from pathlib import Path

from .. import constants

from ..wx_gui import (
    gui_download,
    gui_support
)
from ..support import (
    network_handler,
    updates,
    subprocess_wrapper,
    global_settings
)


class UpdateFrame(wx.Frame):
    """
    Create a frame for updating the patcher
    """
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: wx.Point, url: str = "", version_label: str = "", changelog: str = "") -> None:
        # CORRECTED: Always call the super-class constructor first to register the window correctly
        super().__init__(parent, title=title, size=(350, 300), style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX))

        logging.info("Initializing Update Frame")

        # Handle the parent/child UI logic after the super-class is initialized
        self.parent: wx.Frame = parent
        # Remember which children were actually visible before hiding them, so a
        # cancelled update restores exactly that state instead of un-hiding widgets
        # that were deliberately hidden by the caller.
        self._hidden_children: list = []
        if parent:
            visible_children = [child for child in parent.GetChildren() if child.IsShown()]
            # Only plain widgets get hidden and restored later. Top-level windows owned
            # by the parent (e.g. the Settings sheet the manual "Check for updates"
            # came from) must not be re-shown with Show(): a window-modal sheet comes
            # back as a detached window behind the main menu. The user left them by
            # choosing to update, so close them for good - a cancelled update returns
            # to the main menu, not to Settings.
            for child in visible_children:
                if isinstance(child, wx.TopLevelWindow):
                    logging.info(f"Closing {child.__class__.__name__} '{child.GetTitle()}' before updating")
                    child.Hide()
                    wx.CallAfter(self._destroy_window, child)
                    continue
                self._hidden_children.append(child)
                child.Hide()
            parent.Hide()
        else:
            gui_support.GenerateMenubar(self, global_constants).generate()

        self.title: str = title
        self.constants: constants.Constants = global_constants
        self.screen_location: wx.Point = screen_location
        if parent:
            self.parent.Centre()
            self.screen_location = parent.GetScreenPosition()
        else:
            self.Centre()
            self.screen_location = self.GetScreenPosition()

        if url == "" or version_label == "":
            dict = updates.CheckBinaryUpdates(self.constants).check_binary_updates()
            if dict:
                version_label = dict["Version"]
                url = dict["Link"]
                if not changelog:
                    changelog = dict.get("Changelog") or ""
            else:
                logging.error("Failed to receive update info")
                logging.exception("Stack Trace:")
                wx.MessageBox("Failed to get update info", "Critical Error")
                sys.exit(3)
        self.version_label = version_label
        self.url = url
        # Release notes of the version being installed, shown in the download window.
        # Same cut as gui_main_menu.py: the asset table at the end is not useful here.
        self.changelog = str(changelog).split("## Asset Information")[0] if changelog else ""

        # Our own releases ship a raw "OpenCore-Patcher-T2.pkg" asset (see updates.py),
        # while the upstream Dortania nightly.link fallback (gui_macos_configeration.py)
        # still ships the original "OpenCore-Patcher.pkg" zipped up - keep expecting
        # whichever one this URL actually points to instead of hardcoding one name.
        self.pkg_download_path = self.constants.payload_path / ("OpenCore-Patcher.pkg" if self.url.endswith(".zip") else "OpenCore-Patcher-T2.pkg")
        # The download is written here and _extract_update() reads it back from the
        # same path. These used to be two different hardcoded names
        # ("OpenCore-Patcher-T2.pkg.zip" vs "OpenCore-Patcher.pkg.zip"), so the ZIP
        # route could never extract and then failed in the installer step.
        self.download_path = Path(f"{self.pkg_download_path}.zip") if self.url.endswith(".zip") else self.pkg_download_path
        # Filled in by the worker threads, read by _workflow_thread()
        self._install_status: str = ""
        self._install_error: str = ""

        logging.info(f"Update URL: {url}")
        logging.info(f"Update Version: {version_label}")

        self.frame: wx.Frame = wx.Frame(
            parent=parent if parent else self,
            title=self.title,
            size=(350, 130),
            pos=self.screen_location,
            style=wx.DEFAULT_FRAME_STYLE ^ wx.RESIZE_BORDER ^ wx.MAXIMIZE_BOX
        )

        # The shared download dialog owns download progress and cancellation.
        try:
            self.title_label = wx.StaticText(self.frame, label="Preparing download...", pos=(-1, 1))
            self.title_label.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
            self.title_label.Centre(wx.HORIZONTAL)
        except Exception as e:
            logging.error("Failed to download the update")
            logging.exception("Stack Trace:")
            wx.MessageBox("Failed to download the update", "Critical Error")
            sys.exit(3)

        self.progress_bar = wx.Gauge(self.frame, range=100, pos=(10, 50), size=(300, 20))
        self.progress_bar.Centre(wx.HORIZONTAL)
        self.progress_bar_animation = gui_support.GaugePulseCallback(self.constants, self.progress_bar)

        # Instantiating timer variables for the exit countdown
        self.timer_countdown = 5
        self.exit_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_exit_timer_tick, self.exit_timer)

        # Wait for payloads to mount if they haven't already
        # Without this, if the GUI starts before the background unpack thread finishes,
        # self.constants.payload_path will still point to the read-only DMG inside the app bundle
        # instead of the writable /var/folders/... overlay.
        while gui_support.PayloadMount(self.constants, self).is_unpack_finished() is False:
            wx.Yield()
            time.sleep(self.constants.thread_sleep_interval)

        download_obj = network_handler.DownloadObject(self.url, self.download_path)
        download_frame = gui_download.DownloadFrame(
            self.frame,
            title=self.title,
            global_constants=self.constants,
            download_obj=download_obj,
            item_name=self.version_label,
            download_icon=str(self.constants.app_icon_path),
            changelog=self.changelog,
            cancel_message=(
                "Are you sure you want to cancel the update?\n\n"
                "Staying on an older version of OpenCore Legacy Patcher T2 means you "
                "won't get the latest fixes, which can include security fixes. "
                "Running outdated software may leave your Mac exposed to known vulnerabilities"
                "that attackers could exploit."
            )
        )

        if download_obj.download_complete is not True:
            # Neither a cancelled nor a failed download is a reason to quit: nothing
            # has been changed on disk, so hand control back to the window we came
            # from. DownloadFrame already reported genuine errors to the user.
            if download_frame.user_cancelled:
                logging.info("User cancelled the update download, returning")
            else:
                logging.error("It failed to download the update")
            if self._return_to_parent():
                return
            # Parentless updater (auto patcher path): nothing to return to.
            sys.exit(3)

        self.frame.Centre()
        self.frame.Show()
        self.progress_bar_animation.start_pulse()

        # Start the remaining update workflow on a background thread.
        threading.Thread(target=self._workflow_thread, daemon=True).start()

    def _workflow_thread(self) -> None:
        """
        Background orchestrator thread. Keeps tasks entirely off the main loop,
        preventing GUI lockups and avoiding hazardous wx.Yield use.

        The worker functions report their outcome through self._install_status
        instead of calling sys.exit(): sys.exit() inside a thread only ends that
        thread, so previously a failed install still fell through to
        "Update complete!" and tried to launch an app that was never installed,
        racing the error dialog.
        """
        # --- Phase 1: Extraction ---
        logging.info("Extract update")
        wx.CallAfter(self._update_status_label, "Extracting update...")
        try:
            extracted = self._extract_update()
        except Exception:
            logging.exception("Stack Trace:")
            extracted = False
        if not extracted:
            logging.error("It failed to extract the update, so it can't be installed.")
            message = self._install_error or "Failed to extract the update. If you continue to have this issue, please manually download the update."
            wx.CallAfter(self._handle_fatal_failure, message, "Critical Error!")
            return

        # --- Phase 2: Installation ---
        logging.info("Updating")
        wx.CallAfter(self._update_status_label, "Installing update...")
        try:
            self._install_update()
        except Exception:
            logging.error("The update could not be installed.")
            logging.exception("Stack Trace:")
            self._install_status = "failed"

        if self._install_status == "ok":
            wx.CallAfter(self._finalize_ui_and_start_countdown)
        elif self._install_status == "cancelled":
            wx.CallAfter(self._handle_fatal_failure, "User cancelled update", "Update Cancelled", True)
        else:
            wx.CallAfter(self._hand_off_to_installer_app)

    # =========================================================================
    # ATOMIC MAIN-THREAD UI MUTATORS (Prevents race conditions / split events)
    # =========================================================================

    @staticmethod
    def _destroy_window(window: wx.Window) -> None:
        try:
            window.Destroy()
        except RuntimeError:
            # Already gone (e.g. closed by its own code in the meantime)
            pass

    def _return_to_parent(self) -> bool:
        """
        Tear the updater down and restore the window it was launched from.

        Returns False if there is nothing to go back to (updater started without a
        parent frame); the caller then has to handle termination itself.
        """
        if not self.parent:
            return False

        try:
            self.progress_bar_animation.stop_pulse()
        except RuntimeError:
            pass
        if self.exit_timer.IsRunning():
            self.exit_timer.Stop()

        for child in self._hidden_children:
            try:
                child.Show()
            except RuntimeError:
                continue
        try:
            self.parent.Show()
            self.parent.Raise()
        except RuntimeError:
            # Parent went away while we were updating - nothing left to return to.
            return False

        wx.CallAfter(self.frame.Destroy)
        wx.CallAfter(self.Destroy)
        return True

    def _update_status_label(self, message: str) -> None:
        """Safely alters text components atomically on the main thread."""
        self.title_label.SetLabel(message)
        self.title_label.Centre(wx.HORIZONTAL)

    def _handle_fatal_failure(self, error_msg: str, title: str, is_cancelled: bool = False, is_handoff: bool = False) -> None:
        """
        Executes atomically on the main thread to completely clean up UI elements
        and handle script termination instantly, preventing thread race conditions.
        """
        if is_cancelled or is_handoff:
            wx.MessageBox(error_msg, title, wx.OK | wx.ICON_INFORMATION)
        else:
            wx.MessageBox(error_msg, title, wx.OK | wx.ICON_ERROR)

        self.progress_bar_animation.stop_pulse()
        self.progress_bar.Hide()

        # A cancelled install (user dismissed the admin prompt) leaves the system
        # untouched, so return to the main menu instead of taking the app down.
        if is_cancelled and self._return_to_parent():
            logging.info("Aktualisierung abgebrochen, zurueck zum Hauptmenü")
            logging.info("Update cancelled, returning to the main menu")
            return

        logging.info("Die App wird geschlossen")
        logging.info("Closing the app")
        sys.exit(3)

    def _finalize_ui_and_start_countdown(self) -> None:
        """Reconstructs the interface layout and initializes the exit timer safely."""
        self.title_label.SetLabel("Update complete!")
        self.title_label.Centre(wx.HORIZONTAL)

        self.progress_bar_animation.stop_pulse()
        self.progress_bar.Hide()

        installed_label = wx.StaticText(self.frame, label=f"{self.version_label} has been installed:", pos=(-1, 35))
        installed_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_BOLD))
        installed_label.Centre(wx.HORIZONTAL)

        installed_path_label = wx.StaticText(self.frame, label=self._install_directory(), pos=(-1, installed_label.GetPosition().y + 20))
        installed_path_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        installed_path_label.Centre(wx.HORIZONTAL)

        self.launch_label = wx.StaticText(self.frame, label="Launching update shortly...", pos=(-1, installed_path_label.GetPosition().y + 30))
        self.launch_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        self.launch_label.Centre(wx.HORIZONTAL)

        self.frame.SetSize((-1, self.launch_label.GetPosition().y + 60))

        # Fire and forget launch execution thread
        thread = threading.Thread(target=self._launch_update)
        thread.start()

        # Fire non-blocking main loop timer event every 1 second (1000ms)
        self.exit_timer.Start(1000)

    def _on_exit_timer_tick(self, event: wx.TimerEvent) -> None:
        """Non-blocking timer callback driven directly by native OS event loop."""
        if self.timer_countdown > 0:
            self.launch_label.SetLabel(f"Closing old process in {self.timer_countdown} seconds")
            self.launch_label.Centre(wx.HORIZONTAL)
            self.timer_countdown -= 1
        else:
            self.exit_timer.Stop()
            sys.exit(0)

    # =========================================================================
    # SYSTEM ACTIONS (Executed inside sub-threads safely)
    # =========================================================================

    def _extract_update(self) -> bool:
        logging.debug("Extracting update...")
        if not self.url.endswith(".zip"):
            return True
        logging.info("Extracting update")
        if Path(self.pkg_download_path).exists():
            subprocess.run(["/bin/rm", "-rf", str(self.pkg_download_path)])

        result = subprocess.run(
            ["/usr/bin/ditto", "-xk", str(self.download_path), str(self.constants.payload_path)], capture_output=True
        )
        if result.returncode != 0 or not self.pkg_download_path.exists():
            logging.error("Failed to extract update.")
            subprocess_wrapper.log(result)
            self._install_error = f"Failed to extract update. Error: {self._to_text(result.stderr) or 'package not found in archive'}"
            return False
        return True

    @staticmethod
    def _to_text(value) -> str:
        """run_as_root() can hand back bytes, str or None depending on the elevation path."""
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _installed_app_path(self) -> Path:
        _app_name = "OpenCore-Patcher.app" if self.url.endswith(".zip") else "OpenCore-Patcher-T2.app"
        return Path(self._install_directory()) / _app_name

    def _installer_running(self) -> bool:
        """
        True while any process that is installing our package is alive.

        Plain substring match over `ps` instead of `pgrep -f "installer -pkg <path>"`:
        the Authorization Services fallback starts /usr/libexec/security_authtrampoline
        first, whose argv ("... /usr/sbin/installer auth 3 -pkg <path> ...") does not
        contain "installer -pkg" until it has exec()ed the real installer, and the
        re.escape()d pattern was not a reliable POSIX ERE for pgrep either.
        """
        result = subprocess.run(["/bin/ps", "-axww", "-o", "pid=,command="], capture_output=True, text=True)
        if result.returncode != 0:
            return False
        own_pid = str(os.getpid())
        pkg_path = str(self.pkg_download_path)
        for line in result.stdout.splitlines():
            pid, _, command = line.strip().partition(" ")
            if pid == own_pid:
                continue
            if pkg_path in command and "installer" in command:
                return True
        return False

    def _app_was_rewritten(self, started: float) -> bool:
        # st_ctime changes on every write, even though the installer keeps the payload's mtime.
        info_plist = self._installed_app_path() / "Contents" / "Info.plist"
        try:
            return info_plist.stat().st_ctime >= started - 5
        except OSError:
            return False

    def _wait_for_install(self, started: float, timeout: int = 900, start_grace: int = 30) -> bool:
        """
        Wait until the package install has really finished and report whether the
        new bundle landed on disk.

        The Authorization Services fallback in run_as_root() (utilities.get_admin_permission)
        returns right after spawning the installer, with returncode 0 and no exit status.
        The old check returned as soon as pgrep found nothing - which was usually the case
        right after spawning, before the installer existed - and then looked at Info.plist
        while the install was still running. That reported a failure and opened the macOS
        Installer even though the update went on to install fine in the background.

        For the synchronous paths (helper tool, already root) the installer has exited and
        the bundle is rewritten by the time we get here, so this returns immediately.
        """
        deadline = time.time() + timeout
        start_deadline = time.time() + start_grace
        seen_running = False
        while time.time() < deadline:
            if self._installer_running():
                seen_running = True
            elif self._app_was_rewritten(started):
                # Installer process gone and the new bundle is on disk: done.
                return True
            elif seen_running or time.time() >= start_deadline:
                # It ran and exited without installing, or never started at all.
                return False
            time.sleep(1)
        logging.error("Timed out waiting for the installer to finish")
        return False

    def _install_update(self) -> None:
        logging.info(f"Installing update: {self.pkg_download_path}")
        started = time.time()
        result = subprocess_wrapper.run_as_root(["/usr/sbin/installer", "-pkg", str(self.pkg_download_path), "-target", "/"], capture_output=True)
        output = f"{self._to_text(result.stdout)}\n{self._to_text(result.stderr)}"

        if result.returncode != 0:
            # osascript and Authorization Services say "canceled", our own paths "cancelled"
            if "user cancel" in output.lower():
                logging.info("User cancelled update")
                self._install_status = "cancelled"
                return
            if result.returncode in subprocess_wrapper._HELPER_REFUSAL_ERRORS:
                # Expected with a Debug build of the Privileged Helper Tool, which refuses
                # /usr/sbin/installer by design, and refusals are final (no other root path
                # is tried, see run_as_root()). Not an error: the macOS Installer takes over.
                logging.info("Privileged Helper Tool does not run /usr/sbin/installer in this build, handing the update to the macOS Installer")
            else:
                logging.critical("The app failed to update via the builtin updater.")
                subprocess_wrapper.log(result)
            self._install_status = "handoff"
            return

        # Only trust the result once the installer has finished and the new bundle
        # is actually on disk.
        if not self._wait_for_install(started):
            logging.error(f"Installer reported success, but {self._installed_app_path() / 'Contents' / 'Info.plist'} was not (re)written")
            self._install_status = "handoff"
            return

        # Installed successfully - the running build now belongs to the selected
        # update channel, so later checks compare versions normally again.
        try:
            self.constants.installed_update_channel = self.constants.update_channel
            global_settings.GlobalEnviromentSettings().write_property("UpdateChannelInstalled", self.constants.update_channel)
        except Exception as e:
            logging.error(f"Failed to store installed update channel: {e}")
        self._install_status = "ok"

    def _hand_off_to_installer_app(self) -> None:
        """
        Main thread: the update could not be installed silently, so open it in the
        macOS Installer instead, which asks for authorization itself.

        The package is copied out of payload_path first: that directory is a temporary
        overlay that reroute_payloads.py deletes when this app quits, which happened
        right after the old fallback opened the package from there.
        """
        target = Path.home() / "Downloads" / f"OpenCore-Patcher-T2-{self.version_label}.pkg"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target.unlink()
            shutil.copy2(self.pkg_download_path, target)
            opened = subprocess.run(["/usr/bin/open", "-a", "Installer", str(target)], capture_output=True).returncode == 0
        except Exception:
            logging.exception("Failed to hand the update to the macOS Installer")
            opened = False

        if opened:
            logging.info(f"Opened {target} in the macOS Installer")
            self._handle_fatal_failure(
                f"The update could not be installed automatically, so it was opened in the macOS Installer.\n\n"
                f"Follow the steps there to finish updating. OpenCore Legacy Patcher T2 will now close so it can be replaced.\n\n"
                f"A copy of the package was saved to {target}.",
                "Finish Updating in the Installer",
                is_handoff=True,
            )
        else:
            self._handle_fatal_failure(
                f"Failed to install update automatically. Please visit {self.constants.repo_link.rstrip('/')}/releases "
                f"to manually download the package and perform an in-place upgrade.",
                "Critical Error!",
            )

    def _install_directory(self) -> str:
        """
        Directory the downloaded update installs into

        Our PKG installs to its own directory; an upstream Dortania ZIP still
        lands in theirs (see pkg_download_path above).
        """
        if self.url.endswith(".zip"):
            return "/Library/Application Support/Dortania"
        return "/Library/Application Support/albert-mueller/OpenCore-Patcher-T2"

    def _launch_update(self) -> None:
        # Same reasoning as pkg_download_path above: an upstream Dortania nightly
        # install still lands as "OpenCore-Patcher.app", only our own T2 releases
        # install as "OpenCore-Patcher-T2.app" (see package.py's _files mapping).
        _app_name = self._installed_app_path().name
        try:
            logging.info(f"Aktualisierung beginnen: '{self._install_directory()}/{_app_name}'")
            logging.info(f"Launching update: '{self._install_directory()}/{_app_name}'")
            # T2 builds now ship their executable as OpenCore-Patcher-T2; older T2
            # releases and Dortania's app still use OpenCore-Patcher, so launch
            # whichever one the freshly installed bundle actually contains.
            _macos_dir = f"{self._install_directory()}/{_app_name}/Contents/MacOS"
            _executable = f"{_macos_dir}/OpenCore-Patcher-T2"
            if not Path(_executable).exists():
                _executable = f"{_macos_dir}/OpenCore-Patcher"
            subprocess.Popen([_executable, "--update_installed"])
        except Exception as e:
            logging.error("Das Starten des Aktualisierung durch den Builtin-Update-Instrument hat fehlgeschlagen.")
            logging.error("Launching the update via the builtin updater failed.")
            logging.exception("Stack Trace:")
