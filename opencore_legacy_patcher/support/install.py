"""
install.py: Installation of OpenCore files to ESP
"""

import logging
import plistlib
import subprocess
import re
import sys
from pathlib import Path

from . import utilities, subprocess_wrapper
from .. import constants

# Dieses Variable ist da, um zu überprüfen, ob die OpenCore-Konfiguration fertig ist oder nicht.
OpenCore_EFI_Konfiguration_abgeschlossen=False


class tui_disk_installation:
    def __init__(self, versions):
        self.constants: constants.Constants = versions

    def list_disks(self):
        all_disks = {}
        # TODO: AllDisksAndPartitions wird in Snow Leopard und älter nicht unterstützt
        try:
            # High Sierra und neuer
            disks = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "list", "-plist", "physical"], stdout=subprocess.PIPE).stdout.decode().strip().encode())
        except ValueError:
            # Sierra und älter
            disks = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "list", "-plist"], stdout=subprocess.PIPE).stdout.decode().strip().encode())

        for disk in disks["AllDisksAndPartitions"]:
            try:
                disk_info = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", disk["DeviceIdentifier"]], stdout=subprocess.PIPE).stdout.decode().strip().encode())
            except Exception:
                # "Chinesium" USB-Sticks können korrupte Daten im MediaName Feld haben
                diskutil_output = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", disk["DeviceIdentifier"]], stdout=subprocess.PIPE).stdout.decode().strip()
                # FIX: flags=re.DOTALL hinzugefügt, damit Zeilenumbrüche im XML mitgematcht werden
                ungarbafied_output = re.sub(r'(<key>MediaName</key>\s*<string>).*?(</string>)', r'\1\2', diskutil_output, flags=re.DOTALL).encode()
                try:
                    disk_info = plistlib.loads(ungarbafied_output)
                except Exception:
                    # Falls das Laden immer noch fehlschlägt, überspringen wir die Disk, um Abstürze zu verhindern
                    continue

            try:
                all_disks[disk["DeviceIdentifier"]] = {"identifier": disk_info["DeviceNode"], "name": disk_info.get("MediaName", "Disk"), "size": disk_info["TotalSize"], "partitions": {}}
                for partition in disk["Partitions"]:
                    partition_info = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", partition["DeviceIdentifier"]], stdout=subprocess.PIPE).stdout.decode().strip().encode())
                    all_disks[disk["DeviceIdentifier"]]["partitions"][partition["DeviceIdentifier"]] = {
                        "fs": partition_info.get("FilesystemType", partition_info["Content"]),
                        "type": partition_info["Content"],
                        "name": partition_info.get("VolumeName", ""),
                        "size": partition_info["TotalSize"],
                    }
            except KeyError:
                # Verhindert Abstürze, wenn z. B. CDs/DVDs eingelegt sind
                continue

        supported_disks = {}
        for disk in all_disks:
            if not any(all_disks[disk]["partitions"][partition]["fs"] in ("msdos", "EFI") for partition in all_disks[disk]["partitions"]):
                continue
            supported_disks.update({
                disk: {
                    "disk": disk,
                    "name": all_disks[disk]["name"],
                    "size": utilities.human_fmt(all_disks[disk]['size']),
                    "partitions": all_disks[disk]["partitions"]
                }
            })
        return supported_disks

    def list_partitions(self, disk_response, supported_disks):
        disk_identifier = disk_response

        # FIX: Sicherheitsprüfung, falls die Festplatte nicht (mehr) existiert
        selected_disk = supported_disks.get(disk_identifier)
        if not selected_disk:
            logging.error(f"The selected disk {disk_identifier} wasn't found.")
            return {}

        supported_partitions = {}
        for partition in selected_disk["partitions"]:
            if selected_disk["partitions"][partition]["fs"] not in ("msdos", "EFI"):
                continue
            supported_partitions.update({
                partition: {
                    "partition": partition,
                    "name": selected_disk["partitions"][partition]["name"],
                    "size": utilities.human_fmt(selected_disk["partitions"][partition]["size"])
                }
            })
        return supported_partitions

    def _determine_sd_card(self, media_name: str):
        if any(x in media_name for x in ("SD Card", "SD/MMC", "SDXC Reader", "SD Reader", "Card Reader")):
            logging.info("You're using an SD card, MMC, SDXC Reader or Card Reader")
            return True
        else:
            return False

    def install_opencore(self, full_disk_identifier: str):
        # TODO: Apple Script schlägt in Yosemite und älter fehl
        logging.info(f"Mounte Partition: {full_disk_identifier}")
        logging.info(f"Mounting partition: {full_disk_identifier}")

        # Mount-Versuch als Root
        result = subprocess_wrapper.run_as_root(["/usr/sbin/diskutil", "mount", full_disk_identifier], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # FIX 1: Wenn der Mount fehlschlägt (z.B. weil Root-Rechte verweigert wurden)
        if result.returncode != 0:
            logging.error("Failed to mount the drive due to not enought rights or locked partition.")
            subprocess_wrapper.log(result)
            return False  # Gibt False zurück. Der Aufrufer (die TUI) MUSS dies abfangen!

        # Festplatten-Infos nach erfolgreichem Mount auslesen
        partition_info = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", full_disk_identifier], stdout=subprocess.PIPE).stdout.decode().strip().encode())
        parent_disk = partition_info["ParentWholeDisk"]
        drive_host_info = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", parent_disk], stdout=subprocess.PIPE).stdout.decode().strip().encode())
        sd_type = drive_host_info.get("MediaName", "Disk")

        try:
            logging.info("Checking hard disk type")
            ssd_type = drive_host_info["SolidState"]
        except KeyError:
            ssd_type = False

        mount_path = Path(partition_info["MountPoint"])
        disk_type = partition_info["BusProtocol"]

        # FIX 2: Absicherung, falls diskutil Erfolg meldet, der Pfad aber trotzdem nicht existiert
        if not mount_path.exists():
            logging.error("The EFI couldn't be mounted because the directory doesn't exist.")
            return False

        # Start der Dateioperationen
        try:
            # Da wir als Root gemountet haben, löschen/kopieren wir konsequent mit run_as_root
            if (mount_path / "EFI/OC").exists():
                logging.info("Removing existing EFI/OC folder")
                subprocess_wrapper.run_as_root(["/bin/rm", "-rf", mount_path / "EFI/OC"])

            if (mount_path / "System").exists():
                logging.info("Removing existing System folder")
                subprocess_wrapper.run_as_root(["/bin/rm", "-rf", mount_path / "System"])

            if (mount_path / "boot.efi").exists():
                logging.info("Removing existing boot.efi")
                subprocess_wrapper.run_as_root(["/bin/rm", mount_path / "boot.efi"])

            logging.info("Mounting the EFI partition")
            # FIX 6: run_as_root() only returned the CompletedProcess and never checked its exit code, so a
            # failed "cp -r" (very common specifically on USB flash drives: privileged-helper permission
            # errors on external/removable volumes, slow/cheap "Chinesium" media dropping out mid-copy,
            # etc. -- see list_disks() above) was silently swallowed. The function then fell through all
            # the way to "OpenCore Transfer complete" and returned True even though nothing was actually
            # copied. Switched the critical file operations to run_as_root_and_verify(), which raises on a
            # non-zero exit code and is caught by the existing except-block below (sys.exit(3)), so
            # failures are now surfaced instead of hidden. Also capturing stdout/stderr so the failure
            # reason shows up in the log instead of "None".
            subprocess_wrapper.run_as_root_and_verify(["/bin/mkdir", "-p", mount_path / "EFI"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logging.info("Copying OpenCore to the EFI partition")
            subprocess_wrapper.run_as_root_and_verify(["/bin/cp", "-r", str(self.constants.opencore_release_folder / "EFI/OC"), str(mount_path / "EFI/OC")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess_wrapper.run_as_root_and_verify(["/bin/cp", "-r", str(self.constants.opencore_release_folder / "System"), str(mount_path / "System")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # FIX 7: belt-and-braces check in case the privileged helper ever reports success (exit 0)
            # without the copy having actually landed on disk (seen with some external/USB edge cases).
            if not (mount_path / "EFI/OC").exists() or not (mount_path / "System").exists():
                logging.error("Copying OpenCore to the EFI partition failed: EFI/OC or System is missing on the target after copy.")
                logging.error(f"Target partition bus protocol: {disk_type}")
                sys.exit(3)

            if (self.constants.opencore_release_folder / "boot.efi").exists():
                logging.info("Copying boot.efi to the EFI partition")
                subprocess_wrapper.run_as_root_and_verify(["/bin/cp", str(self.constants.opencore_release_folder / "boot.efi"), str(mount_path / "boot.efi")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if self.constants.boot_efi is True:
                logging.info("Converting Bootstrap to BOOTx64.efi")
                if (mount_path / "EFI/BOOT").exists():
                    subprocess_wrapper.run_as_root_and_verify(["/bin/rm", "-rf", mount_path / "EFI/BOOT"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                subprocess_wrapper.run_as_root_and_verify(["/bin/mkdir", "-p", mount_path / "EFI/BOOT"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                subprocess_wrapper.run_as_root_and_verify(["/bin/mv", str(mount_path / "System/Library/CoreServices/boot.efi"), str(mount_path / "EFI/BOOT/BOOTx64.efi")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                subprocess_wrapper.run_as_root_and_verify(["/bin/rm", "-rf", mount_path / "System"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        except Exception as e:
            logging.error(f"File operation failed during installation: {e}")
            logging.exception("Stack Trace:")
            logging.info("Please try again later.")
            sys.exit(3)

        # Volume-Icons setzen (Fehler hier kopieren wir sicherheitshalber auch als Root, da EFI geschützt ist)
        try:
            if self._determine_sd_card(sd_type) is True:
                logging.info("Adding SD Card icon")
                subprocess_wrapper.run_as_root(["/bin/cp", str(self.constants.icon_path_sd), str(mount_path)])
            elif ssd_type is True:
                logging.info("Adding SSD icon")
                subprocess_wrapper.run_as_root(["/bin/cp", str(self.constants.icon_path_ssd), str(mount_path)])
            elif disk_type == "USB":
                logging.info("Adding USB stick icon")
                subprocess_wrapper.run_as_root(["/bin/cp", str(self.constants.icon_path_external), str(mount_path)])
            else:
                logging.info("Adding internal hard disk icon")
                subprocess_wrapper.run_as_root(["/bin/cp", str(self.constants.icon_path_internal), str(mount_path)])
        except Exception as icon_error:
            logging.warning(f"Copying the icons failed (not critical): {icon_error}")

        # Bereinigung & Unmount
        logging.info("Cleaning up installation site")
        if not self.constants.recovery_status:
            logging.info("Unmounting the EFI partition")
            # FIX 4: Auch unmount als Root ausführen, da wir es als Root gemountet haben
            subprocess_wrapper.run_as_root(["/usr/sbin/diskutil", "umount", mount_path])
            OpenCore_EFI_Konfiguration_abgeschlossen=True
        # behebt einen Fehler, indem bedingungslos OpenCore Transfer complete anzeigt. Dies ist auch eine Sicherheitslücke, die erlaubt Angreifern ungefertigtes EFI-Configuration auf den EFI oder andere OpenCore Partition zu platzieren, um DoS-Angriffe zu starten. Es ist eine extrem seriöse Sicherheitslücke, die zu Datenverlust und manchmal sogar Geldverlust bringen kann.
        if OpenCore_EFI_Konfiguration_abgeschlossen==True:
            logging.info("OpenCore Transfer complete")
            return True
        else:
            logging.error("Configuring OpenCore failed due to the following error:")
            logging.exception("Stack Trace:")
            logging.info("Please report this issue.")
            sys.exit(3)
