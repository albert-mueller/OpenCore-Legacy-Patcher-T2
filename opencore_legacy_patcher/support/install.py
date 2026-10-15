"""
install.py: Installation of OpenCore files to ESP
"""

import logging
import os
import plistlib
import subprocess
import re
import sys
from pathlib import Path

from . import utilities, subprocess_wrapper
from .. import constants
from ..datasets import model_array


# GPT partition type GUID of the EFI System Partition (diskutil may report either the name or the GUID)
_ESP_TYPE_GUID = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"

# Partitions that only exist on disks with a Windows installation. "Microsoft Basic Data" alone is NOT
# used, since macOS uses the same type for plain FAT32/exFAT data partitions (including a separate
# OpenCore partition) - for that type only the NTFS filesystem counts as Windows.
_WINDOWS_CONTENT_TYPES = {
    "microsoft reserved", "windows recovery", "windows_ntfs",
    "e3c9e316-0b5c-4db8-817d-f92df00215ae",  # Microsoft Reserved
    "de94bba4-06d1-4d40-a16a-bfd50179d6ac",  # Windows Recovery Environment
}
_WINDOWS_FILESYSTEMS = {"ntfs"}

_LINUX_CONTENT_TYPES = {
    "0fc63daf-8483-4772-8e79-3d69d8477de4",  # Linux filesystem data
    "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f",  # Linux swap
    "e6d6d379-f507-44c2-a23c-238f2a3df928",  # Linux LVM
    "a19d880f-05fc-4d3b-a006-743f0f84911e",  # Linux RAID
    "4f68bce3-e8cd-4db1-96e7-fbcaf984b709",  # Linux root (x86-64)
    "933ac7e1-2eb4-4f13-b844-0e14e2aef915",  # Linux /home
    "bc13c2ff-59e6-4262-a352-b275fd6f7172",  # Linux /boot (XBOOTLDR)
}
_LINUX_FILESYSTEMS = {"ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "reiserfs", "jfs"}

# Boot loader folders other operating systems put on the ESP (EFI/<name>). EFI/BOOT (generic fallback
# path) and EFI/APPLE / EFI/OC are deliberately not listed.
_WINDOWS_ESP_FOLDERS = {"microsoft"}
_LINUX_ESP_FOLDERS = {
    "ubuntu", "debian", "fedora", "redhat", "centos", "rocky", "almalinux", "opensuse", "sles",
    "arch", "manjaro", "endeavouros", "linuxmint", "pop", "elementary", "kali", "gentoo", "void",
    "nixos", "grub", "systemd", "refind", "linux", "t2linux",
}


def _foreign_os_from_partitions(partitions) -> list:
    """
    Return the operating systems ("Windows"/"Linux") found in an iterable of (filesystem, content) pairs
    """
    found = []
    for fs, content in partitions:
        fs = (fs or "").lower()
        content = (content or "").lower()
        if fs in _WINDOWS_FILESYSTEMS or content in _WINDOWS_CONTENT_TYPES:
            if "Windows" not in found:
                found.append("Windows")
        if fs in _LINUX_FILESYSTEMS or content in _LINUX_CONTENT_TYPES or content.startswith("linux"):
            if "Linux" not in found:
                found.append("Linux")
    return found


def _foreign_os_from_esp(mount_path: Path) -> list:
    """
    Return the operating systems whose boot loaders live in EFI/ of a mounted ESP
    """
    found = []
    try:
        folders = {entry.name.lower() for entry in (mount_path / "EFI").iterdir() if entry.is_dir()}
    except Exception:
        return found
    if folders & _WINDOWS_ESP_FOLDERS:
        found.append("Windows")
    if folders & _LINUX_ESP_FOLDERS:
        found.append("Linux")
    return found


def is_esp(content: str) -> bool:
    return (content or "").upper() in ("EFI", _ESP_TYPE_GUID)


SHARED_ESP_HELP = (
    "On T2 Macs OpenCore must not share the EFI partition with Windows or Linux, because their boot "
    "loaders and OpenCore would interfere with each other there.\n\n"
    "On Windows 11 on T2 Macs if OpenCore is flashed to the ESP, it results in This device needs to \n\n"
    "be repaired error. On T2 Macs unfortuantely their UEFI is very sensitive and would be impossible to fix \n\n"
    "if this happened."
    "Create a separate partition for OpenCore instead: in Disk Utility select the disk, click "
    "\"Partition\", add a new partition of about 300 MB, format it as \"MS-DOS (FAT)\" and name it "
    "\"OPENCORE\". Then search for disks again and select that partition."
)


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

    def target_is_t2(self) -> bool:
        """
        True if OpenCore is being installed for a T2 Mac (build target, not necessarily the host)
        """
        return (self.constants.custom_model or self.constants.computer.real_model) in model_array.T2Macs

    def _shared_esp_check_enabled(self) -> bool:
        if not self.target_is_t2():
            return False
        if getattr(self.constants, "allow_oc_on_shared_t2_esp", False) is True:
            logging.warning("allow_oc_on_shared_t2_esp is set: skipping the Windows/Linux EFI partition check for T2 Macs")
            return False
        return True

    def foreign_os_on_disk(self, disk_identifier: str, supported_disks: dict) -> list:
        """
        Windows/Linux installations found on a disk from list_disks()
        """
        disk = supported_disks.get(disk_identifier) or {}
        partitions = disk.get("partitions", {})
        return _foreign_os_from_partitions((partitions[p].get("fs"), partitions[p].get("type")) for p in partitions)

    def _foreign_os_on_parent_disk(self, parent_disk: str) -> list:
        """
        Same as foreign_os_on_disk(), but queries diskutil directly (used right before installing)
        """
        pairs = []
        try:
            disk_list = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "list", "-plist", parent_disk], stdout=subprocess.PIPE).stdout)
        except Exception as e:
            logging.warning(f"Could not list partitions of {parent_disk}: {e}")
            return []
        for disk in disk_list.get("AllDisksAndPartitions", []):
            for partition in disk.get("Partitions", []):
                content = partition.get("Content", "")
                fs = ""
                try:
                    info = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", partition["DeviceIdentifier"]], stdout=subprocess.PIPE).stdout)
                    fs = info.get("FilesystemType", "")
                    content = info.get("Content", content)
                except Exception:
                    pass
                pairs.append((fs, content))
        return _foreign_os_from_partitions(pairs)

    def list_partitions(self, disk_response, supported_disks):
        disk_identifier = disk_response

        # FIX: Sicherheitsprüfung, falls die Festplatte nicht (mehr) existiert
        selected_disk = supported_disks.get(disk_identifier)
        if not selected_disk:
            logging.error(f"The selected disk {disk_identifier} wasn't found.")
            return {}

        # T2 Macs: if Windows or Linux is installed on this disk, the EFI partition is off limits and
        # OpenCore has to go to a separate FAT32 partition (see SHARED_ESP_HELP).
        foreign_os = self.foreign_os_on_disk(disk_identifier, supported_disks) if self._shared_esp_check_enabled() else []
        if foreign_os:
            logging.info(f"{' and '.join(foreign_os)} detected on {disk_identifier}: the EFI partition can't be used for OpenCore on T2 Macs")

        supported_partitions = {}
        for partition in selected_disk["partitions"]:
            if selected_disk["partitions"][partition]["fs"] not in ("msdos", "EFI"):
                continue
            blocked_reason = None
            if foreign_os and is_esp(selected_disk["partitions"][partition]["type"]):
                blocked_reason = f"{' and '.join(foreign_os)} installed on this disk"
            supported_partitions.update({
                partition: {
                    "partition": partition,
                    "name": selected_disk["partitions"][partition]["name"],
                    "size": utilities.human_fmt(selected_disk["partitions"][partition]["size"]),
                    "blocked_reason": blocked_reason,
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

        # FIX: Ziel validieren, BEVOR als Root "rm -rf" darauf losgelassen wird.
        # install_opencore() wird mit einer Partitionskennung aufgerufen; wird eine falsche
        # (z. B. APFS-Datenvolume) übergeben, löschte der Code dort als Root EFI/OC und System.
        # Ein leerer/fehlender MountPoint wurde zu Path("") == aktuelles Verzeichnis.
        mount_point = partition_info.get("MountPoint", "")
        partition_fs = partition_info.get("FilesystemType", partition_info.get("Content", ""))
        if partition_fs not in ("msdos", "EFI"):
            logging.error(f"Refusing to install OpenCore to {full_disk_identifier}: not a FAT/EFI partition ({partition_fs}).")
            return False
        if not mount_point:
            logging.error(f"Refusing to install OpenCore to {full_disk_identifier}: no mount point reported.")
            return False

        mount_path = Path(mount_point)
        disk_type = partition_info.get("BusProtocol", "")

        # FIX 2: Absicherung, falls diskutil Erfolg meldet, der Pfad aber trotzdem nicht existiert
        if not mount_path.exists():
            logging.error("The EFI couldn't be mounted because the directory doesn't exist.")
            return False

        # Der Mountpunkt darf beliebig liegen (die ESP muss nicht unter /Volumes/EFI gemountet
        # sein, z. B. bei eigenem Mountpunkt oder bereits anderweitig gemounteter ESP). Statt eines
        # festen Pfads wird geprüft, dass der Pfad wirklich ein Mountpunkt ist und genau DIESE
        # Partition dort gemountet ist - sonst würde "rm -rf" auf einem fremden Dateisystem landen.
        if not mount_path.is_absolute() or mount_path.resolve() == Path("/") or not os.path.ismount(mount_path):
            logging.error(f"Refusing to install OpenCore to unexpected mount point: {mount_path}")
            return False
        try:
            mounted_info = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", str(mount_path)], stdout=subprocess.PIPE).stdout)
        except Exception:
            mounted_info = {}
        if mounted_info.get("DeviceIdentifier") != partition_info.get("DeviceIdentifier"):
            logging.error(f"Refusing to install OpenCore: {mount_path} does not belong to {full_disk_identifier}.")
            return False

        # T2 Macs: never install to an EFI partition that is shared with Windows or Linux. The GUI already
        # disables these partitions; this is the backstop for every other caller and also catches boot
        # loaders on the ESP itself (e.g. Windows/Linux on another disk using this ESP).
        if self._shared_esp_check_enabled() and is_esp(partition_info.get("Content", "")):
            foreign_os = self._foreign_os_on_parent_disk(parent_disk)
            for os_name in _foreign_os_from_esp(mount_path):
                if os_name not in foreign_os:
                    foreign_os.append(os_name)
            if foreign_os:
                logging.error(f"Refusing to install OpenCore to the EFI partition {full_disk_identifier}: {' and '.join(foreign_os)} detected.")
                logging.error(SHARED_ESP_HELP)
                if not self.constants.recovery_status:
                    subprocess_wrapper.run_as_root(["/usr/sbin/diskutil", "umount", mount_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
        # FIX: Hier stand vorher ein Flag "OpenCore_EFI_Konfiguration_abgeschlossen", das nur beim
        # Unmount gesetzt wurde. Durch die Zuweisung in der Funktion war es eine lokale Variable:
        # im Recovery-Modus (kein Unmount) führte das Lesen zu UnboundLocalError, also zu einem
        # Absturz NACH erfolgreicher Installation. Erfolg/Misserfolg der Kopie wird bereits oben
        # über run_as_root_and_verify() + Existenzprüfung abgesichert; hier wird nur noch der
        # Unmount sauber behandelt.
        logging.info("Cleaning up installation site")
        if not self.constants.recovery_status:
            logging.info("Unmounting the EFI partition")
            # FIX 4: Auch unmount als Root ausführen, da wir es als Root gemountet haben
            umount_result = subprocess_wrapper.run_as_root(["/usr/sbin/diskutil", "umount", mount_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if umount_result.returncode != 0:
                logging.warning("Unmounting the EFI partition failed (not critical, OpenCore was installed):")
                subprocess_wrapper.log(umount_result)

        logging.info("OpenCore Transfer complete")
        return True
