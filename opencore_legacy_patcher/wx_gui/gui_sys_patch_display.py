"""
gui_sys_patch_display.py: Display root patching menu
"""

import wx
import logging
import plistlib
import threading

from pathlib import Path

from .. import constants

from ..sys_patch.patchsets import HardwarePatchsetDetection, HardwarePatchsetValidation

from ..wx_gui import (
    gui_main_menu,
    gui_support,
    gui_sys_patch_start,
)


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
            patches = HardwarePatchsetDetection(constants=self.constants).device_properties

        thread = threading.Thread(target=_fetch_patches, args=(self,))
        thread.start()

        frame.ShowWindowModal()

        gui_support.wait_for_thread(thread)

        progress_bar.Hide()
        progress_bar_animation.stop_pulse()

        available_label.SetLabel("Available patches for your system:")
        available_label.Centre(wx.HORIZONTAL)


        can_unpatch: bool = not patches[HardwarePatchsetValidation.UNPATCHING_NOT_POSSIBLE]

        if not any(not patch.startswith("Settings") and not patch.startswith("Validation") and patches[patch] is True for patch in patches):
            logging.info("No applicable patches available")
            patches = {}

        # Check if OCLP has already applied the same patches
        no_new_patches = not self._check_if_new_patches_needed(patches) if patches else False

        if not patches:
            # Prompt user with no patches found
            patch_label = wx.StaticText(frame, label="No patches required", pos=(-1, available_label.GetPosition()[1] + 20))
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
            else:
                longest_patch = ""
                for patch in patches:
                    if (not patch.startswith("Settings") and not patch.startswith("Validation") and patches[patch] is True):
                        if len(patch) > len(longest_patch):
                            longest_patch = patch
                anchor = wx.StaticText(frame, label=longest_patch, pos=(-1, available_label.GetPosition()[1] + 20))
                anchor.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
                anchor.Centre(wx.HORIZONTAL)
                anchor.Hide()

                logging.info("Available patches:")
                for patch in patches:
                    if (not patch.startswith("Settings") and not patch.startswith("Validation") and patches[patch] is True):
                        i = i + 20
                        logging.info(f"- {patch}")
                        patch_label = wx.StaticText(frame, label=f"- {patch}", pos=(anchor.GetPosition()[0], available_label.GetPosition()[1] + i))
                        patch_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))

                if i == 20:
                    patch_label.SetLabel(patch_label.GetLabel().replace("-", ""))
                    patch_label.Centre(wx.HORIZONTAL)

            if patches[HardwarePatchsetValidation.PATCHING_NOT_POSSIBLE] is True or no_new_patches is True:
                # Cannot patch due to the following reasons:
                patch_label = wx.StaticText(frame, label="Cannot patch due to the following reasons:", pos=(-1, patch_label.GetPosition()[1] + 25))
                patch_label.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_BOLD))
                patch_label.Centre(wx.HORIZONTAL)

                longest_patch = ""
                for patch in patches:
                    if not patch.startswith("Validation"):
                        continue
                    if patches[patch] is False:
                        continue
                    if patch in [HardwarePatchsetValidation.PATCHING_NOT_POSSIBLE, HardwarePatchsetValidation.UNPATCHING_NOT_POSSIBLE]:
                        continue

                    if len(patch) > len(longest_patch):
                        longest_patch = patch
                anchor = wx.StaticText(frame, label=longest_patch.split('Validation: ')[1], pos=(-1, patch_label.GetPosition()[1] + 20))
                anchor.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
                anchor.Centre(wx.HORIZONTAL)
                anchor.Hide()

                i = 0
                for patch in patches:
                    if not patch.startswith("Validation"):
                        continue
                    if patches[patch] is False:
                        continue
                    if patch in [HardwarePatchsetValidation.PATCHING_NOT_POSSIBLE, HardwarePatchsetValidation.UNPATCHING_NOT_POSSIBLE]:
                        continue

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


        # Button: Start Root Patching
        start_button = wx.Button(frame, label="Start Root Patching", pos=(10, patch_label.GetPosition().y + 25), size=(170, 30))
        start_button.Bind(wx.EVT_BUTTON, lambda event: self.on_start_root_patching(patches))
        start_button.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        start_button.Centre(wx.HORIZONTAL)

        # Button: Revert Root Patches
        revert_button = wx.Button(frame, label="Revert Root Patches", pos=(10, start_button.GetPosition().y + start_button.GetSize().height - 5), size=(170, 30))
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


    def _check_if_new_patches_needed(self, patches: dict) -> bool:
        """
        Checks if any new patches are needed for the user to install
        Newer users will assume the root patch menu will present missing patches.
        Thus we'll need to see if the exact same OCLP build was used already
        """

        logging.info("Checking if new patches are needed")

        if self.constants.commit_info[0] in ["Running from source", "Built from source"]:
            return True

        if self.constants.computer.oclp_sys_url != self.constants.commit_info[2]:
            # If commits are different, assume patches are as well
            return True

        oclp_plist = "/System/Library/CoreServices/OpenCore-Legacy-Patcher.plist"
        if not Path(oclp_plist).exists():
            # If it doesn't exist, no patches were ever installed
            # ie. all patches applicable
            return True

        oclp_plist_data = plistlib.load(open(oclp_plist, "rb"))
        for patch in patches:
            if (not patch.startswith("Settings") and not patch.startswith("Validation") and patches[patch] is True):
                # Patches should share the same name as the plist key
                # See sys_patch/patchsets/base.py for more info
                if patch.split(": ")[1] not in oclp_plist_data:
                    logging.info(f"- Patch {patch} not installed")
                    return True

        logging.info("No new patches detected for system")
        return False
