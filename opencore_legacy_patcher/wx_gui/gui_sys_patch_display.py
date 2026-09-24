"""
gui_sys_patch_display.py: Display root patching menu
"""

import wx
import logging
import threading

from .. import constants

from ..sys_patch.patchsets import (
    HardwarePatchsetDetection,
    HardwarePatchsetValidation,
    get_disabled_patchsets,
    set_disabled_patchsets,
)

from ..wx_gui import (
    gui_main_menu,
    gui_support,
    gui_sys_patch_start,
)

from ..support import global_settings


class SysPatchDisplayFrame(wx.Frame):
    """
    Create a modal frame for displaying root patches
    """
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None):
        logging.info("Initializing Root Patch Display Frame")

        # Always properly construct the underlying wx.Frame C++ peer, regardless of
        # whether a parent was supplied - the previous "if parent:" branch skipped
        # this call entirely in that case, leaving self as a half-initialized
        # wx.Frame subclass with no real backing window. wxPython's C++/Python
        # binding doesn't support that safely: it can crash natively (no Python
        # traceback, since it isn't a Python exception) once the object is torn
        # down and the garbage collector touches it again - matching exactly the
        # silent crash-on-return seen after "No applicable patches available".
        super().__init__(parent, title=title, size=(360, 200), style=wx.DEFAULT_FRAME_STYLE ^ wx.RESIZE_BORDER ^ wx.MAXIMIZE_BOX)

        if parent:
            self.frame = parent
        else:
            self.frame = self
            self.frame.Centre()

        self.title = title
        self.constants: constants.Constants = global_constants
        self.frame_modal: wx.Dialog = None
        self.return_button: wx.Button = None
        self.available_patches: bool = False
        self.init_with_parent = True if parent else False

        # Patchsets applicable to this Mac, and the subset the user opted out of.
        # Populated by the detection run in '_generate_elements_display_patches()'.
        self.available_patchsets: list = []
        self.disabled_patchsets:  list = []
        self.required_patchsets:  list = []

        # Patchsets already recorded in the root volume manifest, and that manifest's
        # own metadata (patcher version, date, commit URL).
        self.installed_patchsets: list = []
        self.manifest_metadata:   dict = {}

        self.frame_modal = wx.Dialog(self.frame, title=title, size=(360, 200))

        self._generate_elements_display_patches(self.frame_modal)

        if self.constants.update_stage != gui_support.AutoUpdateStages.INACTIVE:
            if self.available_patches is False:
                gui_support.RestartHost(self.frame).restart(message="No root patch updates needed!\n\nWould you like to reboot to apply the new OpenCore build?")


    def _generate_elements_display_patches(self, frame: wx.Frame = None) -> None:
        """
        Generate UI elements for root patching frame

        Format:
            - Title label:        Post-Install Menu
            - Label:              Available patches:
            - Labels:             {patch name}
            - Button:             Start Root Patching
            - Button:             Revert Root Patches
            - Button:             Return to Main Menu
        """
        frame = self if not frame else frame

        title_label = wx.StaticText(frame, label="Post-Install Menu", pos=(-1, 10))
        title_label.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
        title_label.Centre(wx.HORIZONTAL)

        # Label: Fetching patches...
        available_label = wx.StaticText(frame, label="Fetching patches for host", pos=(-1, title_label.GetPosition()[1] + title_label.GetSize()[1] + 10))
        available_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_BOLD))
        available_label.Centre(wx.HORIZONTAL)

        # Progress bar
        progress_bar = wx.Gauge(frame, range=100, pos=(-1, available_label.GetPosition()[1] + available_label.GetSize()[1] + 10), size=(250, 20))
        progress_bar.Centre(wx.HORIZONTAL)
        progress_bar_animation = gui_support.GaugePulseCallback(self.constants, progress_bar)
        progress_bar_animation.start_pulse()

        # Set window height
        frame.SetSize((-1, progress_bar.GetPosition()[1] + progress_bar.GetSize()[1] + 40))

        # Labels: {patch name}
        patches: dict = {}
        def _fetch_patches(self) -> None:
            nonlocal patches
            detection = HardwarePatchsetDetection(constants=self.constants)
            patches = detection.device_properties
            # Keep the full picture around: 'device_properties' only lists the patchsets
            # that will actually be installed, so without this a deselected patchset could
            # never be re-enabled from the menu again.
            self.available_patchsets = detection.available_patchsets
            self.disabled_patchsets  = detection.disabled_patchsets
            self.required_patchsets  = detection.required_patchsets
            # What the root volume manifest says is already installed, so the menu can
            # tell a patched Mac apart from an unpatched one (#387).
            self.installed_patchsets = detection.installed_patchsets
            self.manifest_metadata   = detection.manifest_metadata

        thread = threading.Thread(target=_fetch_patches, args=(self,))
        thread.start()

        frame.ShowWindowModal()

        gui_support.wait_for_thread(thread)

        progress_bar.Hide()
        progress_bar_animation.stop_pulse()

        # What this run will install, and what it leaves out.
        #
        # 'device_properties' only carries the patchsets that survived detection, so one
        # the user deselected in 'Configure Patches' is simply absent here. Labelling that
        # list "Available patches for your system" therefore read like the full set
        # detected for the Mac, and a deselected patchset was invisible unless the user
        # opened the configure dialog again - leaving "modern wireless and modern audio
        # will be installed" on screen while only one of them was (#381). Both halves are
        # spelled out below instead, so the menu alone says what will and will not be
        # installed.
        selected_patchsets: list = [
            patch for patch in patches
            if not patch.startswith("Settings") and not patch.startswith("Validation") and patches[patch] is True
        ]
        # Detected for this Mac, yet not part of this run: deselected by the user, or held
        # back because the patcher installs the network patches first (see
        # '_handle_missing_network_connection()').
        skipped_patchsets: list = [name for name in self.available_patchsets if name not in selected_patchsets]

        # Of the patchsets this run covers, the ones the root volume manifest already
        # records - and the ones genuinely still missing.
        #
        # Detection alone cannot answer this: it reports every patchset applicable to the
        # hardware, whether or not it is on disk. The previous shortcut asked instead
        # whether the manifest's 'Commit URL' matched the running build, so updating the
        # app was enough to have a fully patched Mac told that all of its patches "will be
        # installed" (#387). Which build installed them is a separate question, answered
        # by 'patched_with_other_build' below.
        already_installed: list = [patch for patch in selected_patchsets if patch in self.installed_patchsets]
        pending_patchsets: list = [patch for patch in selected_patchsets if patch not in self.installed_patchsets]

        patched_with_other_build: bool = bool(self.manifest_metadata) and self.manifest_metadata.get("Commit URL") != self.constants.commit_info[2]

        can_unpatch: bool = not patches[HardwarePatchsetValidation.UNPATCHING_NOT_POSSIBLE]

        if not selected_patchsets:
            logging.info("No applicable patches available")
            patches = {}

        # Everything applicable is already on the volume
        no_new_patches = bool(patches) and not pending_patchsets

        available_label.SetLabel("Root patch status:" if no_new_patches else "Patches that will be installed:")
        available_label.Centre(wx.HORIZONTAL)

        if not patches:
            # Prompt user with no patches found
            #
            # Deselecting every applicable patchset ends up here as well, and "No patches
            # required" is plainly wrong in that case: the patches exist, they were just
            # turned off. The list below names them.
            no_patches_text = "No patches will be installed" if skipped_patchsets else "No patches required"
            patch_label = wx.StaticText(frame, label=no_patches_text, pos=(-1, available_label.GetPosition()[1] + 20))
            patch_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
            patch_label.Centre(wx.HORIZONTAL)

        else:
            # Add Label for each patch
            i = 0
            if no_new_patches is True:
                patch_label = wx.StaticText(frame, label="All applicable patches already installed", pos=(-1, available_label.GetPosition()[1] + 20))
                patch_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
                patch_label.Centre(wx.HORIZONTAL)
                i = i + 20

                # The patches are installed, they just came from another build of the
                # patcher. Saying so beats listing them as pending: nothing is missing,
                # while reinstalling them with this build does require a revert first.
                if patched_with_other_build is True:
                    logging.info("Patches were installed by a different build of the patcher")
                    patch_label = wx.StaticText(frame, label="Installed by a different build - revert to reinstall", pos=(-1, available_label.GetPosition()[1] + 20 + i))
                    patch_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
                    patch_label.Centre(wx.HORIZONTAL)
                    i = i + 20
            else:
                longest_patch = max(pending_patchsets, key=len)
                anchor = wx.StaticText(frame, label=longest_patch, pos=(-1, available_label.GetPosition()[1] + 20))
                anchor.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
                anchor.Centre(wx.HORIZONTAL)
                anchor.Hide()

                logging.info("Patches that will be installed:")
                for patch in pending_patchsets:
                    i = i + 20
                    logging.info(f"- {patch}")
                    patch_label = wx.StaticText(frame, label=f"- {patch}", pos=(anchor.GetPosition()[0], available_label.GetPosition()[1] + i))
                    patch_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))

                if i == 20:
                    patch_label.SetLabel(patch_label.GetLabel().replace("-", ""))
                    patch_label.Centre(wx.HORIZONTAL)

            # Reasons that actually block patching, collected once and reused below.
            #
            # 'PATCHING_NOT_POSSIBLE' always comes with at least one of these, but
            # 'no_new_patches' does not: everything applicable can already be installed
            # with nothing blocking at all. That left the list empty, and the anchor
            # label below then ran ''.split('Validation: ')[1] -> IndexError, taking the
            # whole Post-Install menu down with an uncaught exception. Nothing to list
            # also means nothing to explain, so fall through to the regular
            # "Root Volume last patched" summary instead (what the menu did before
            # 'no_new_patches' was added to this branch).
            blocking_reasons = [
                patch for patch in patches
                if patch.startswith("Validation")
                and patches[patch] is True
                and patch not in [HardwarePatchsetValidation.PATCHING_NOT_POSSIBLE, HardwarePatchsetValidation.UNPATCHING_NOT_POSSIBLE]
            ]

            if blocking_reasons and (patches[HardwarePatchsetValidation.PATCHING_NOT_POSSIBLE] is True or no_new_patches is True):
                # Cannot patch due to the following reasons:
                patch_label = wx.StaticText(frame, label="Cannot patch due to the following reasons:", pos=(-1, patch_label.GetPosition()[1] + 25))
                patch_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_BOLD))
                patch_label.Centre(wx.HORIZONTAL)

                longest_patch = max(blocking_reasons, key=len)
                anchor = wx.StaticText(frame, label=longest_patch.split('Validation: ')[1], pos=(-1, patch_label.GetPosition()[1] + 20))
                anchor.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
                anchor.Centre(wx.HORIZONTAL)
                anchor.Hide()

                i = 0
                for patch in blocking_reasons:
                    patch_label = wx.StaticText(frame, label=f"- {patch.split('Validation: ')[1]}", pos=(anchor.GetPosition()[0], anchor.GetPosition()[1] + i))
                    patch_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
                    i = i + 20

                if i == 20:
                    patch_label.SetLabel(patch_label.GetLabel().replace("-", ""))
                    patch_label.Centre(wx.HORIZONTAL)

            else:
                if self.constants.computer.oclp_sys_version and self.constants.computer.oclp_sys_date:
                    date = self.constants.computer.oclp_sys_date.split(" @")
                    date = date[0] if len(date) == 2 else ""

                    patch_text = f"{self.constants.computer.oclp_sys_version}, {date}"

                    patch_label = wx.StaticText(frame, label="Root Volume last patched:", pos=(-1, patch_label.GetPosition().y + 25))
                    patch_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_BOLD))
                    patch_label.Centre(wx.HORIZONTAL)

                    patch_label = wx.StaticText(frame, label=patch_text, pos=(available_label.GetPosition().x - 10, patch_label.GetPosition().y + 20))
                    patch_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
                    patch_label.Centre(wx.HORIZONTAL)


        # Labels: everything detected for this Mac that this run will NOT install
        #
        # Without this the skipped patches simply vanish from the menu: the list above
        # then looks like the complete set detected for the machine, and a patchset
        # deselected during an earlier test session stays silently missing from every
        # later patch run (#381). The reason matters as well - "Disabled by you" is undone
        # in 'Configure Patches', while the network-first split resolves itself after the
        # reboot.
        for group_label, group in (
            # Patchsets this run would cover, but that are already on the volume. Listing
            # them keeps the menu honest in the mixed case - some installed, some still
            # missing - where the header above only names the missing ones.
            ("Already installed", already_installed if pending_patchsets else []),
            ("Disabled by you", [name for name in skipped_patchsets if name in self.disabled_patchsets]),
            ("Skipped this run", [name for name in skipped_patchsets if name not in self.disabled_patchsets]),
        ):
            if not group:
                continue
            group_text = ", ".join(name.split(": ")[1] if ": " in name else name for name in group)
            if len(group_text) > 45:
                group_text = f"{len(group)} patches"
            patch_label = wx.StaticText(frame, label=f"{group_label}: {group_text}", pos=(-1, patch_label.GetPosition().y + 25))
            patch_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
            patch_label.Centre(wx.HORIZONTAL)

        # Button: Start Root Patching
        start_button = wx.Button(frame, label="Start Root Patching", pos=(10, patch_label.GetPosition().y + 25), size=(170, 30))
        start_button.Bind(wx.EVT_BUTTON, lambda event: self.on_start_root_patching(patches))
        start_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        start_button.Centre(wx.HORIZONTAL)

        # Button: Configure Patches
        configure_button = wx.Button(frame, label="Configure Patches", pos=(10, start_button.GetPosition().y + start_button.GetSize().height - 5), size=(170, 30))
        configure_button.Bind(wx.EVT_BUTTON, self.on_configure_patches)
        configure_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        configure_button.Centre(wx.HORIZONTAL)
        if not self.available_patchsets:
            configure_button.Disable()

        # Button: Revert Root Patches
        revert_button = wx.Button(frame, label="Revert Root Patches", pos=(10, configure_button.GetPosition().y + configure_button.GetSize().height - 5), size=(170, 30))
        revert_button.Bind(wx.EVT_BUTTON, lambda event: self.on_revert_root_patching(patches))
        revert_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        revert_button.Centre(wx.HORIZONTAL)

        # Button: Return to Main Menu
        return_button = wx.Button(frame, label="Return to Main Menu", pos=(10, revert_button.GetPosition().y + revert_button.GetSize().height), size=(150, 30))
        return_button.Bind(wx.EVT_BUTTON, self.on_return_dismiss if self.init_with_parent else self.on_return_to_main_menu)
        return_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        return_button.Centre(wx.HORIZONTAL)
        self.return_button = return_button

        # Disable buttons if unsupported
        if not patches:
            start_button.Disable()
        else:
            self.available_patches = True
            if patches[HardwarePatchsetValidation.PATCHING_NOT_POSSIBLE] is True:
                start_button.Disable()
            elif no_new_patches is False:
                start_button.SetDefault()
            else:
                self.available_patches = False
        if can_unpatch is False:
            revert_button.Disable()

        # Set frame size
        frame.SetSize((-1, return_button.GetPosition().y + return_button.GetSize().height + 15))
        # Deliberately no second ShowWindowModal(): this dialog was already shown as a sheet
        # further up, before the patch detection thread ran. On macOS every ShowWindowModal()
        # call begins another NSWindow sheet session on the parent, and tearing the dialog
        # down ends only one of them - the leftover session kept the parent blocked behind an
        # empty grey sheet, which is the "Return to Main Menu hangs on a white screen" bug.
        # This frame was the only one in the app calling ShowWindowModal() twice. The controls
        # added above appear on their own, being children of an already visible window.
        frame.Refresh()


    def on_start_root_patching(self, patches: dict):
        t1_status = "DETECTED" if getattr(self.constants.computer, 't1_chip', False) else "NOT DETECTED"

        gpu_status = "NOT DETECTED"
        if getattr(self.constants.computer, 'dgpu', None):
            dgpu = self.constants.computer.dgpu
            arch = getattr(dgpu, 'arch', None)
            if arch and "Polaris" in str(arch):
                gpu_status = "DETECTED"
        if gpu_status == "NOT DETECTED" and getattr(self.constants.computer, 'gpus', None):
            for gpu in self.constants.computer.gpus:
                arch = getattr(gpu, 'arch', None)
                if arch and "Polaris" in str(arch):
                    gpu_status = "DETECTED"
                    break
        if gpu_status == "NOT DETECTED" and any("AMD Polaris" in p for p in patches if patches[p] is True):
            gpu_status = "DETECTED"

        wifi_status = "NOT DETECTED"
        if getattr(self.constants.computer, 'wifi', None):
            wifi = self.constants.computer.wifi
            vendor_name = getattr(wifi, 'vendor_name', 'Broadcom')
            vendor_id = getattr(wifi, 'vendor_id', 0)
            device_id = getattr(wifi, 'device_id', 0)
            wifi_status = f"{vendor_name} {vendor_id:04X}:{device_id:04X}"

        patch_list = "\n".join([f"- {patch.split(': ')[1] if ': ' in patch else patch}" for patch in patches if not patch.startswith("Settings") and not patch.startswith("Validation") and patches[patch] is True])
        if not patch_list:
            patch_list = "- None"

        os_name = "macOS Tahoe 26.x" if self.constants.detected_os >= 25 else f"macOS (Build {self.constants.detected_os_build})"

        warning_msg = f"""Target OS: {os_name}
Model: {self.constants.computer.real_model}
T1 Security: {t1_status}
AMD Polaris: {gpu_status}
Wi-Fi: {wifi_status}

Root Patches to apply:
{patch_list}

WARNING:
Applying Root Patches will modify the system volume
by creating a new APFS snapshot.
"""
        pop_up = wx.MessageDialog(
            self.frame,
            warning_msg,
            "CONFIRM ROOT PATCH APPLICATION",
            style=wx.OK | wx.CANCEL | wx.ICON_WARNING
        )
        pop_up.SetOKCancelLabels("APPLY ROOT PATCH", "CANCEL")

        if pop_up.ShowModal() != wx.ID_OK:
            return

        frame = gui_sys_patch_start.SysPatchStartFrame(
            parent=None,
            title=self.title,
            global_constants=self.constants,
            patches=patches,
        )
        if hasattr(self, 'frame_modal') and self.frame_modal:
            self.frame_modal.Hide()
            self.frame_modal.Destroy()
        if hasattr(self, 'frame') and self.frame:
            self.frame.Hide()
            self.frame.Destroy()
        frame.start_root_patching()


    def on_configure_patches(self, event: wx.Event = None):
        """
        Let the user choose which of the detected root patches are installed

        Every applicable patch is selected by default, matching the behaviour of the
        patcher without this menu. Deselecting one is meant for patches that are known
        to misbehave on a given machine (ie. a graphics patch causing a kernel panic):
        skipping it lets the remaining patches install and the Mac reach the desktop,
        with only that piece of hardware left unaccelerated.
        """
        # Required patchsets are never offered: skipping them would leave the machine
        # without a working input device, ie. no way back into this menu to undo it.
        required  = list(self.required_patchsets)
        available = [name for name in self.available_patchsets if name not in required]
        if not available:
            pop_up = wx.MessageDialog(
                self.frame,
                "No configurable root patches were detected for this Mac.",
                "Configure Root Patches",
                style=wx.OK | wx.ICON_INFORMATION
            )
            pop_up.ShowModal()
            pop_up.Destroy()
            return

        currently_disabled = set(self.disabled_patchsets)

        description = (
            "All patches detected for your Mac are installed by default.\n\n"
            "Uncheck any patch you do not want to install, for example one that is known to\n"
            "break booting on your machine. Your selection is remembered for future runs.\n\n"
            "Note: unchecked patches leave the matching hardware unpatched, so features such\n"
            "as graphics acceleration, Wi-Fi or audio may not work."
        )
        if required:
            description += (
                "\n\nAlways installed: " + ", ".join(required) + "\n"
                "These cannot be turned off. Without them your Mac loses its keyboard and\n"
                "mouse, leaving no way to return to this menu and undo the change."
            )

        dialog = wx.MultiChoiceDialog(
            self.frame,
            description,
            "Configure Root Patches",
            available
        )
        dialog.SetSelections([index for index, name in enumerate(available) if name not in currently_disabled])

        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return

        selected = {available[index] for index in dialog.GetSelections()}
        dialog.Destroy()

        newly_disabled = set(available) - selected
        if newly_disabled == currently_disabled:
            logging.info("Patch selection unchanged")
            return

        # Deselecting is a deliberate, remembered choice with visible consequences, so
        # spell them out once before storing it.
        if newly_disabled:
            confirmation = wx.MessageDialog(
                self.frame,
                "These patches will not be installed:\n\n"
                + "\n".join(f"  \u2022 {name}" for name in sorted(newly_disabled))
                + "\n\nThe matching hardware stays unpatched, so graphics acceleration, Wi-Fi, "
                  "audio or the built-in camera may stop working until you re-enable them here "
                  "and patch again.\n\nContinue?",
                "Skip these patches?",
                style=wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING
            )
            answer = confirmation.ShowModal()
            confirmation.Destroy()
            if answer != wx.ID_YES:
                logging.info("Patch selection discarded by user")
                return

        # Preserve stored entries that don't apply to this host (ie. a patchset for
        # hardware that isn't currently detected) - only the visible ones are being
        # decided here.
        stored = set(get_disabled_patchsets()) - set(available) - set(required)
        if set_disabled_patchsets(sorted(stored | newly_disabled)) is False:
            # The selection never reached disk, so reloading the menu here would show
            # every patch enabled again with no hint as to why - the failure has to be
            # named, together with the one command that fixes the usual cause (a
            # settings file left behind root-owned by an earlier elevated run, which
            # cannot be removed without sudo thanks to the sticky bit on /Users/Shared).
            logging.error("Failed to store patch selection")
            pop_up = wx.MessageDialog(
                self.frame,
                "Your patch selection could not be saved.\n\n"
                "The patcher's settings file cannot be written, so the selection would "
                "be lost again on the next run. This usually happens when the file was "
                "left behind by an earlier run as root.\n\n"
                "Run this in Terminal, then reopen this menu:\n\n"
                f"    sudo rm '{global_settings.SETTINGS_PLIST_PATH}'\n\n"
                "The patch list is unchanged for now.",
                "Could not save patch selection",
                style=wx.OK | wx.ICON_ERROR
            )
            pop_up.ShowModal()
            pop_up.Destroy()
            return

        logging.info(f"Patch selection updated, disabled patchsets: {sorted(newly_disabled) if newly_disabled else 'None'}")

        self._reload_frame()


    def _reload_frame(self):
        """
        Rebuild the Post-Install menu so the patch list reflects the new selection

        Mirrors how the main menu hands off to this frame: build the replacement first,
        then tear the old one down deferred - the button invoking this is itself a child
        of the dialog being destroyed.
        """
        old_modal = self.frame_modal
        old_frame = self.frame
        screen_location = old_frame.GetPosition()

        self.frame_modal = None

        if old_modal:
            # End the sheet session before hiding it (see gui_support.end_window_modal)
            gui_support.end_window_modal(old_modal)
            old_modal.Hide()
        old_frame.Hide()

        SysPatchDisplayFrame(
            parent=None,
            title=self.title,
            global_constants=self.constants,
            screen_location=screen_location,
        )

        if old_modal:
            wx.CallAfter(old_modal.Destroy)
        wx.CallAfter(old_frame.Destroy)


    def on_revert_root_patching(self, patches: dict):
        frame = gui_sys_patch_start.SysPatchStartFrame(
            parent=None,
            title=self.title,
            global_constants=self.constants,
            patches=patches,
        )
        self.frame_modal.Hide()
        self.frame_modal.Destroy()
        self.frame.Hide()
        self.frame.Destroy()
        frame.revert_root_patching()


    def on_return_to_main_menu(self, event: wx.Event = None):
        # Get frame from event
        frame_modal: wx.Dialog = event.GetEventObject().GetParent()
        frame: wx.Frame = frame_modal.Parent
        # As in on_return_dismiss: end the sheet session before hiding it.
        gui_support.end_window_modal(frame_modal)
        frame_modal.Hide()
        frame.Hide()

        main_menu_frame = gui_main_menu.MainFrame(
            None,
            title=self.title,
            global_constants=self.constants,
        )
        main_menu_frame.Show()
        # Deferred, so the frame outlives the button event handler running inside it.
        wx.CallAfter(frame.Destroy)


    def on_return_dismiss(self, event: wx.Event = None):
        if not self.frame_modal:
            return
        # End the sheet's modal session before tearing it down (see
        # gui_support.end_window_modal), and defer the Destroy: the button running this
        # handler is itself a child of the dialog being destroyed.
        gui_support.end_window_modal(self.frame_modal)
        self.frame_modal.Hide()
        wx.CallAfter(self.frame_modal.Destroy)
        self.frame_modal = None


