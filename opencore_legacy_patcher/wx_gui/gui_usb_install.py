"""
gui_usb_install.py: Custom logic for finding USB EFI and installing OpenCore safely
"""

import wx
import logging
import subprocess
import os
import shutil
import time
import hashlib
import threading
import plistlib
from pathlib import Path

from .. import constants
from ..wx_gui import gui_main_menu, gui_support

class InstallUSBFrame(wx.Frame):
    def __init__(self, parent: wx.Frame, title: str, global_constants: constants.Constants, screen_location: tuple = None) -> None:
        logging.info("Initializing USB Install Frame")
        super(InstallUSBFrame, self).__init__(parent, title=title, size=(600, 700), style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX))
        gui_support.GenerateMenubar(self, global_constants).generate()

        self.constants: constants.Constants = global_constants
        self.title: str = title
        
        self.available_efis = {} # identifier -> string representation
        self.selected_efi = None

        self._generate_elements()
        self.Centre()
        
        threading.Thread(target=self._detect_usb_environment).start()

    def _generate_elements(self) -> None:
        self.panel = wx.Panel(self)
        self.sizer = wx.BoxSizer(wx.VERTICAL)

        title_label = wx.StaticText(self.panel, label="Install OpenCore to EFI")
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
        self.status_text.SetLabel("Select a drive to install OpenCore.")
        self._append_log("Please select a target drive from the dropdown.")

    def on_disk_select(self, event):
        selection = self.disk_choice.GetStringSelection()
        self.selected_efi = self.available_efis[selection]
        
        self.info_box.Clear()
        self._append_log("=========================================")
        self._append_log("TARGET SELECTION:")
        self._append_log(selection)
        self._append_log(f"EFI PARTITION:    {self.selected_efi}")
        
        self.backup_path = os.path.join(str(self.constants.current_path), f"USB_EFI_Backup_{time.strftime('%Y%m%d_%H%M%S')}")
        self._append_log(f"BACKUP PATH:      {self.backup_path}")
        self._append_log("=========================================")
        
        self.confirm_button.Enable()
        self.confirm_button.SetLabel(f"Install TEST-B EFI to {self.selected_efi}")

    def on_confirm(self, event):
        self.confirm_button.Disable()
        self.disk_choice.Disable()
        self.return_button.Disable()
        self.status_text.SetLabel(f"Installing to {self.selected_efi}...")
        threading.Thread(target=self._perform_installation).start()

    def _perform_installation(self):
        try:
            self._mount_efi()
            self._backup_efi()
            self._replace_efi()
            self._unmount_efi()
            
            wx.CallAfter(self.status_text.SetLabel, "Installation Complete.")
            wx.CallAfter(self.return_button.Enable)
        except Exception as e:
            self._append_log(f"\nCRITICAL ERROR during installation: {str(e)}")
            wx.CallAfter(self.status_text.SetLabel, "Installation Failed.")
            wx.CallAfter(self.return_button.Enable)
            try:
                self._unmount_efi(silent=True)
            except:
                pass

    def _mount_efi(self):
        self._append_log(f"Mounting {self.selected_efi}...")
        res = self._run_cmd(f"diskutil mount /dev/{self.selected_efi}")
        self._append_log(res)
        # Find mount point
        info = self._run_cmd(f"diskutil info -plist /dev/{self.selected_efi}")
        try:
            data = plistlib.loads(info.encode('utf-8'))
            self.mount_point = data.get("MountPoint")
        except:
            self.mount_point = None
            
        if not self.mount_point:
            raise Exception("Failed to mount EFI or find mount point.")
        self._append_log(f"Mounted at {self.mount_point}")

    def _backup_efi(self):
        self._append_log(f"Backing up EFI to {self.backup_path}...")
        os.makedirs(self.backup_path, exist_ok=True)
        efi_dir = os.path.join(self.mount_point, "EFI")
        if os.path.exists(efi_dir):
            subprocess.run(["cp", "-R", efi_dir + "/", self.backup_path + "/"])
            self._append_log("Backup completed.")
        else:
            self._append_log("No existing EFI folder found on target partition.")

    def _replace_efi(self):
        source_efi = os.path.join(str(self.constants.current_path), "Build-Folder", self.constants.oc_build_folder_name, "EFI")
        target_efi = os.path.join(self.mount_point, "EFI")
        
        if not os.path.exists(source_efi):
            raise Exception(f"Source EFI not found at {source_efi}. Please Build OpenCore first.")
            
        self._append_log("Removing old EFI/OC and EFI/BOOT...")
        if os.path.exists(os.path.join(target_efi, "OC")):
            shutil.rmtree(os.path.join(target_efi, "OC"))
        if os.path.exists(os.path.join(target_efi, "BOOT")):
            shutil.rmtree(os.path.join(target_efi, "BOOT"))
            
        os.makedirs(target_efi, exist_ok=True)
        
        self._append_log("Copying new EFI/OC and EFI/BOOT...")
        subprocess.run(["cp", "-R", os.path.join(source_efi, "OC"), target_efi + "/"])
        subprocess.run(["cp", "-R", os.path.join(source_efi, "BOOT"), target_efi + "/"])
        
        self._append_log("Copy complete. Verifying SHA256 of config.plist...")
        source_config = os.path.join(source_efi, "OC", "config.plist")
        target_config = os.path.join(target_efi, "OC", "config.plist")
        
        source_sha = hashlib.sha256(open(source_config, 'rb').read()).hexdigest()
        target_sha = hashlib.sha256(open(target_config, 'rb').read()).hexdigest()
        
        self._append_log("\nEFI INSTALLATION COMPLETE")
        self._append_log(f"Target:\n{self.selected_efi}")
        self._append_log(f"Backup:\n{self.backup_path}\n")
        
        self._append_log(f"SOURCE SHA256:\n{source_sha}")
        self._append_log(f"INSTALLED SHA256:\n{target_sha}\n")
        
        if source_sha != target_sha:
            self._append_log("WARNING: SHA256 MISMATCH!")
        else:
            self._append_log("Verification SUCCESS: Signatures match.")

    def _unmount_efi(self, silent=False):
        if not silent:
            self._append_log(f"Unmounting {self.selected_efi}...")
        import time
        time.sleep(2)
        self._run_cmd(f"diskutil unmount force /dev/{self.selected_efi}")
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
