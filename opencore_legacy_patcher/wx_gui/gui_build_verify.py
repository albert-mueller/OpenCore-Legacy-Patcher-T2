"""
gui_build_verify.py: Custom logic for verifying generated Build against TEST-B requirements
"""

import wx
import logging
import os
import plistlib
import hashlib
import threading

from .. import constants
from ..wx_gui import gui_main_menu, gui_support

class VerifyBuildFrame(wx.Frame):
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None) -> None:
        logging.info("Initializing Build Verify Frame")
        super(VerifyBuildFrame, self).__init__(parent, title=title, size=(600, 700), style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX))
        gui_support.GenerateMenubar(self, global_constants).generate()

        self.constants: constants.Constants = global_constants
        self.title: str = title

        self._generate_elements()
        self.Centre()

        threading.Thread(target=self._perform_verification).start()

    def _generate_elements(self) -> None:
        self.panel = wx.Panel(self)
        self.sizer = wx.BoxSizer(wx.VERTICAL)

        title_label = wx.StaticText(self.panel, label="Verify Generated Build (TEST-B)")
        title_label.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
        self.sizer.Add(title_label, 0, wx.ALL | wx.CENTER, 10)

        self.status_text = wx.StaticText(self.panel, label="Verifying...")
        self.status_text.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        self.sizer.Add(self.status_text, 0, wx.ALL | wx.CENTER, 10)

        self.info_box = wx.TextCtrl(self.panel, style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_RICH2, size=(550, 450))
        self.info_box.SetFont(wx.Font(12, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.sizer.Add(self.info_box, 0, wx.ALL | wx.CENTER, 10)

        self.return_button = wx.Button(self.panel, label="Return to Main Menu")
        self.return_button.Bind(wx.EVT_BUTTON, self.on_return_to_main_menu)
        self.return_button.Disable()
        self.sizer.Add(self.return_button, 0, wx.ALL | wx.CENTER, 10)

        self.panel.SetSizer(self.sizer)

    def _append_log(self, text: str):
        wx.CallAfter(self._append_log_safe, text)

    def _append_log_safe(self, text: str):
        self.info_box.AppendText(text + "\n")
        self.info_box.ShowPosition(self.info_box.GetLastPosition())

    def _perform_verification(self):
        try:
            self._append_log("Starting verification of generated build...")
            self._verify_files()
            wx.CallAfter(self.status_text.SetLabel, "Verification Complete.")
        except Exception as e:
            self._append_log(f"\nCRITICAL ERROR: {str(e)}")
            wx.CallAfter(self.status_text.SetLabel, "Verification Failed.")
        finally:
            wx.CallAfter(self.return_button.Enable)

    def _verify_files(self):
        self._append_log("=========================================")
        target_efi = os.path.join(self.constants.launcher_script_location, "Build-Folder", self.constants.oc_build_folder_name, "EFI", "OC")

        if not os.path.exists(target_efi):
            raise Exception("Generated EFI/OC folder not found! Please BUILD OPENCORE first.")

        required_kexts = [
            "Lilu.kext",
            "IOSkywalkFamily.kext",
            "IO80211FamilyLegacy.kext",
            "AirportBrcmFixup.kext",
            "AMFIPass.kext"
        ]

        missing_kexts = []
        for kext in required_kexts:
            if not os.path.exists(os.path.join(target_efi, "Kexts", kext)):
                missing_kexts.append(kext)

        if missing_kexts:
            self._append_log(f"WARNING: Missing Kexts: {', '.join(missing_kexts)}")
        else:
            self._append_log("All required Kexts are present in EFI/OC/Kexts.")

        config_path = os.path.join(target_efi, "config.plist")
        if not os.path.exists(config_path):
            raise Exception("config.plist not found in EFI/OC!")

        with open(config_path, "rb") as f:
            config = plistlib.load(f)

        # Verify WhateverGreen is enabled
        weg_enabled = False
        for kext in config.get("Kernel", {}).get("Add", []):
            if kext.get("BundlePath") == "WhateverGreen.kext" and kext.get("Enabled") == True:
                weg_enabled = True
                break

        boot_args = config.get("NVRAM", {}).get("Add", {}).get("7C436110-AB2A-4BBB-A880-FE41995C9F82", {}).get("boot-args", "")
        wegnoegpu_enabled = "-wegnoegpu" in boot_args
        dart0_enabled = "dart=0" in boot_args

        amd_patches = []
        for patch in config.get("Kernel", {}).get("Patch", []):
            if "AMD" in patch.get("Comment", "") or "Polaris" in patch.get("Comment", ""):
                if patch.get("Enabled") == True:
                    amd_patches.append(patch.get("Comment"))

        self._append_log("\nTEST-B CONFIGURATION IN BUILD:")
        self._append_log(f"WhateverGreen: {'ENABLED' if weg_enabled else 'DISABLED'}")
        self._append_log(f"-wegnoegpu: {'ENABLED' if wegnoegpu_enabled else 'DISABLED'}")
        self._append_log(f"AMD patches: {'NONE' if not amd_patches else ', '.join(amd_patches)}")
        self._append_log(f"dart=0: {'ENABLED' if dart0_enabled else 'NOT ENABLED'}")

        self._append_log("\nSHA256 CHECKSUM:")
        target_sha = hashlib.sha256(open(config_path, 'rb').read()).hexdigest()
        self._append_log(f"{target_sha}")

        self._append_log("=========================================")

    def on_return_to_main_menu(self, event):
        self.Hide()
        main_menu_frame = gui_main_menu.MainFrame(
            None,
            title=self.title,
            global_constants=self.constants,
            screen_location=self.GetScreenPosition()
        )
        main_menu_frame.Show()
        self.Destroy()
