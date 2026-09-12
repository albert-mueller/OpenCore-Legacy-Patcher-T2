"""
gui_usb_verify.py: Custom logic for verifying USB EFI against TEST-B
"""

import wx
import logging
import subprocess
import os
import plistlib
import hashlib
import threading

from .. import constants
from ..wx_gui import gui_main_menu, gui_support

class VerifyUSBFrame(wx.Frame):
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None) -> None:
        logging.info("Initializing USB Verify Frame")
        super(VerifyUSBFrame, self).__init__(parent, title=title, size=(600, 700), style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX))
        gui_support.GenerateMenubar(self, global_constants).generate()

        self.constants: constants.Constants = global_constants
        self.title: str = title
        
        self.available_efis = {} # identifier -> string representation
        self.selected_efi = None
        self.mount_point = None

        self._generate_elements()
        self.Centre()
        
        threading.Thread(target=self._detect_usb_environment).start()

    def _generate_elements(self) -> None:
        self.panel = wx.Panel(self)
        self.sizer = wx.BoxSizer(wx.VERTICAL)

        title_label = wx.StaticText(self.panel, label="Verify EFI (TEST-B)")
        title_label.SetFont(gui_support.font_factory(19, wx.FONTWEIGHT_BOLD))
        self.sizer.Add(title_label, 0, wx.ALL | wx.CENTER, 10)

        self.status_text = wx.StaticText(self.panel, label="Scanning drives...")
        self.status_text.SetFont(gui_support.font_factory(13, wx.FONTWEIGHT_NORMAL))
        self.sizer.Add(self.status_text, 0, wx.ALL | wx.CENTER, 10)

        # Choice dropdown for drives
        self.disk_choice = wx.Choice(self.panel, choices=[])
        self.disk_choice.Bind(wx.EVT_CHOICE, self.on_disk_select)
        self.disk_choice.Disable()
        self.sizer.Add(self.disk_choice, 0, wx.ALL | wx.EXPAND, 10)

        self.info_box = wx.TextCtrl(self.panel, style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_RICH2, size=(550, 350))
        self.info_box.SetFont(wx.Font(12, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.sizer.Add(self.info_box, 0, wx.ALL | wx.CENTER, 10)
        
        self.confirm_button = wx.Button(self.panel, label="Waiting for selection...")
        self.confirm_button.Bind(wx.EVT_BUTTON, self.on_confirm)
        self.confirm_button.Disable()
        self.sizer.Add(self.confirm_button, 0, wx.ALL | wx.CENTER, 10)

        self.return_button = wx.Button(self.panel, label="Return to Main Menu")
        self.return_button.Bind(wx.EVT_BUTTON, self.on_return_to_main_menu)
        self.sizer.Add(self.return_button, 0, wx.ALL | wx.CENTER, 10)

        self.panel.SetSizer(self.sizer)

    def _append_log(self, text: str):
        wx.CallAfter(self._append_log_safe, text)

    def _append_log_safe(self, text: str):
        self.info_box.AppendText(text + "\n")
        self.info_box.ShowPosition(self.info_box.GetLastPosition())

    def _run_cmd(self, cmd):
        result = subprocess.run(cmd.split(" "), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.stdout.strip()
        
    def _detect_usb_environment(self):
        self._append_log("Scanning for drives with EFI partitions...")
        
        try:
            plist_out = self._run_cmd("diskutil list -plist")
            if not plist_out:
                raise Exception("No drives found or diskutil failed.")
            data = plistlib.loads(plist_out.encode('utf-8'))
            
            for disk in data.get("AllDisksAndPartitions", []):
                # Ensure it has partitions
                disk_id = disk.get("DeviceIdentifier", "")
                disk_name = disk.get("MediaName", "Unknown")
                size = disk.get("Size", 0) // (1024*1024*1024)
                
                for part in disk.get("Partitions", []):
                    if part.get("Content") == "EFI":
                        part_id = part.get("DeviceIdentifier")
                        label = f"{disk_name} ({disk_id}) - {size}GB -> EFI Partition: {part_id}"
                        self.available_efis[label] = part_id

            wx.CallAfter(self._update_choices)
            
        except Exception as e:
            self._append_log(f"Error scanning drives: {e}")
            wx.CallAfter(self.status_text.SetLabel, "Error scanning drives.")

    def _update_choices(self):
        if not self.available_efis:
            self._append_log("No EFI partitions found.")
            self.status_text.SetLabel("No EFI partitions found.")
            return
            
        choices = list(self.available_efis.keys())
        self.disk_choice.SetItems(choices)
        self.disk_choice.Enable()
        self.status_text.SetLabel("Select a drive to verify OpenCore.")
        self._append_log("Please select a target drive from the dropdown.")

    def on_disk_select(self, event):
        selection = self.disk_choice.GetStringSelection()
        self.selected_efi = self.available_efis[selection]
        
        self.info_box.Clear()
        self._append_log("=========================================")
        self._append_log("TARGET SELECTION:")
        self._append_log(selection)
        self._append_log(f"EFI PARTITION:    {self.selected_efi}")
        self._append_log("=========================================")
        
        self.confirm_button.Enable()
        self.confirm_button.SetLabel(f"Verify EFI on {self.selected_efi}")

    def on_confirm(self, event):
        self.confirm_button.Disable()
        self.disk_choice.Disable()
        self.return_button.Disable()
        self.status_text.SetLabel(f"Verifying {self.selected_efi}...")
        threading.Thread(target=self._perform_verification).start()

    def _perform_verification(self):
        try:
            self._append_log("Starting verification...")
            self._mount_efi()
            self._verify_files()
            self._unmount_efi()
            wx.CallAfter(self.status_text.SetLabel, "Verification Complete.")
        except Exception as e:
            self._append_log(f"\nCRITICAL ERROR: {str(e)}")
            wx.CallAfter(self.status_text.SetLabel, "Verification Failed.")
            try:
                self._unmount_efi(silent=True)
            except:
                pass
        finally:
            wx.CallAfter(self.return_button.Enable)

    def _mount_efi(self):
        self._append_log(f"Mounting {self.selected_efi}...")
        self._run_cmd(f"diskutil mount /dev/{self.selected_efi}")
        info = self._run_cmd(f"diskutil info -plist /dev/{self.selected_efi}")
        try:
            data = plistlib.loads(info.encode('utf-8'))
            self.mount_point = data.get("MountPoint")
        except:
            self.mount_point = None
            
        if not self.mount_point:
            raise Exception("Failed to mount EFI or find mount point.")
        self._append_log(f"Mounted at {self.mount_point}")

    def _verify_files(self):
        self._append_log("=========================================")
        target_efi = os.path.join(self.mount_point, "EFI", "OC")
        
        if not os.path.exists(target_efi):
            raise Exception("EFI/OC folder not found on target!")
            
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
                    
        self._append_log("\nTEST-B CONFIGURATION IN EFI:")
        self._append_log(f"WhateverGreen: {'ENABLED' if weg_enabled else 'DISABLED'}")
        self._append_log(f"-wegnoegpu: {'ENABLED' if wegnoegpu_enabled else 'DISABLED'}")
        self._append_log(f"AMD patches: {'NONE' if not amd_patches else ', '.join(amd_patches)}")
        self._append_log(f"dart=0: {'ENABLED' if dart0_enabled else 'NOT ENABLED'}")
        
        self._append_log("\nSHA256 CHECKSUM:")
        target_sha = hashlib.sha256(open(config_path, 'rb').read()).hexdigest()
        self._append_log(f"Installed: {target_sha}")
        
        source_config = os.path.join(self.constants.launcher_script_location, "Build-Folder", self.constants.oc_build_folder_name, "EFI", "OC", "config.plist")
        if os.path.exists(source_config):
            source_sha = hashlib.sha256(open(source_config, 'rb').read()).hexdigest()
            self._append_log(f"Source:    {source_sha}")
            if source_sha == target_sha:
                self._append_log("MATCH!")
            else:
                self._append_log("MISMATCH!")
        else:
            self._append_log("Source config.plist not found to compare.")

        self._append_log("=========================================")

    def _unmount_efi(self, silent=False):
        if self.selected_efi:
            if not silent:
                self._append_log(f"Unmounting {self.selected_efi}...")
            self._run_cmd(f"diskutil unmount /dev/{self.selected_efi}")
            if not silent:
                self._append_log("Unmount complete.")

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
