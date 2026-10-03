"""
utilities.py: Utility functions for OpenCore Legacy Patcher
"""

import os
import re
import math
import atexit
import shutil
import logging
import argparse
import binascii
import plistlib
import subprocess
import py_sip_xnu
import Security

from pathlib import Path

from .. import constants

from ..detections import ioreg

from ..datasets import (
    sip_data,
    model_array
)


def is_t2_mac(model: str, global_constants=None) -> bool:
    """
    Return True if the given model has a T2 security chip.

    model_array.T2Macs is the single source of truth. The previous fallback
    read global_constants.device_properties, an attribute Constants never had,
    so every non-T2 model raised AttributeError instead of returning False
    (breaking e.g. CatalinaBCM5701Ethernet.kext injection on non-T2 Macs).
    global_constants is kept only for call-site compatibility.
    """
    return model in model_array.T2Macs

def hexswap(input_hex: str):
    hex_pairs = [input_hex[i : i + 2] for i in range(0, len(input_hex), 2)]
    hex_rev = hex_pairs[::-1]
    hex_str = "".join(["".join(x) for x in hex_rev])
    return hex_str.upper()


def string_to_hex(input_string):
    if not (len(input_string) % 2) == 0:
        input_string = "0" + input_string
    input_string = hexswap(input_string)
    input_string = binascii.unhexlify(input_string)
    return input_string


def human_fmt(num):
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(num) < 1000.0:
            return "%3.1f %s" % (num, unit)
        num /= 1000.0
    return "%.1f %s" % (num, "EB")


def seconds_to_readable_time(seconds) -> str:
    """
    Convert seconds to a readable time format

    Parameters:
        seconds (int | float | str): Seconds to convert

    Returns:
        str: Readable time format
    """
    seconds = int(seconds)
    time = ""

    if 0 <= seconds < 60:
        return "Less than a minute "
    if seconds < 0:
        return "Indeterminate time "

    years, seconds = divmod(seconds, 31536000)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    if years > 0:
        return "Over a year"
    if days > 0:
        if days > 31:
            return "Over a month"
        time += f"{days}d "
    if hours > 0:
        time += f"{hours}h "
    if minutes > 0:
        time += f"{minutes}m "
    #if seconds > 0:
    #    time += f"{seconds}s"
    return time


def header(lines):
    lines = [i for i in lines if i is not None]
    total_length = len(max(lines, key=len)) + 4
    logging.info("#" * (total_length))
    for line in lines:
        left_side = math.floor(((total_length - 2 - len(line.strip())) / 2))
        logging.info("#" + " " * left_side + line.strip() + " " * (total_length - len("#" + " " * left_side + line.strip()) - 1) + "#")
    logging.info("#" * total_length)


RECOVERY_STATUS = None


def check_recovery():
    global RECOVERY_STATUS  # pylint: disable=global-statement # We need to cache the result

    if RECOVERY_STATUS is None:
        RECOVERY_STATUS = Path("/System/Library/BaseSystem").exists()

    return RECOVERY_STATUS


def get_disk_path():
    root_partition_info = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", "/"], stdout=subprocess.PIPE).stdout.decode().strip().encode())
    root_mount_path = root_partition_info["DeviceIdentifier"]
    root_mount_path = root_mount_path[:-2] if root_mount_path.count("s") > 1 else root_mount_path
    return root_mount_path


def check_if_root_is_apfs_snapshot():
    root_partition_info = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", "/"], stdout=subprocess.PIPE).stdout.decode().strip().encode())
    try:
        is_snapshotted = root_partition_info["APFSSnapshot"]
    except KeyError:
        is_snapshotted = False
    return is_snapshotted


def check_seal():
    # 'Snapshot Sealed' property is only listed on booted snapshots
    sealed = subprocess.run(["/usr/sbin/diskutil", "apfs", "list"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if "Snapshot Sealed:           Yes" in sealed.stdout.decode():
        return True
    else:
        return False

def find_any_oclp_manifest(root_path: Path = None):
    """
    Search common locations for any OpenCore Legacy Patcher root-volume
    manifest left behind by a previous root patch, regardless of which
    OCLP version originally wrote it.

    Returns the Path to the first manifest found, or None if none exists.
    """
    if root_path is None:
        root_path = Path("/")

    search_dirs = [
        root_path / "System" / "Library" / "CoreServices",
        root_path / "Library" / "Application Support" / "Dortania",
    ]

    for directory in search_dirs:
        try:
            if not directory.is_dir():
                continue
            for candidate in directory.iterdir():
                # Note: 'pathlib.Path.glob()' matches case-sensitively even on
                # case-insensitive filesystems (default macOS APFS included),
                # since the pattern matching itself happens in Python, not the OS.
                # The real file OCLP writes is mixed-case
                # ("OpenCore-Legacy-Patcher.plist"), which a lowercase glob
                # pattern will never match, so match case-insensitively instead.
                if candidate.is_file() and "opencore-legacy-patcher" in candidate.name.lower():
                    return candidate
        except (OSError, PermissionError):
            continue

    return None


def csr_decode(os_sip):
    sip_int = py_sip_xnu.SipXnu().get_sip_status().value
    for i,  current_sip_bit in enumerate(sip_data.system_integrity_protection.csr_values):
        if sip_int & (1 << i):
            sip_data.system_integrity_protection.csr_values[current_sip_bit] = True

    # Can be adjusted to whatever OS needs patching
    sip_needs_change = all(sip_data.system_integrity_protection.csr_values[i] for i in os_sip)
    if sip_needs_change is True:
        return False
    else:
        return True


def friendly_hex(integer: int):
    return "{:02X}".format(integer)

sleep_process = None

def disable_sleep_while_running():
    global sleep_process
    logging.info("Disabling Idle Sleep")
    if sleep_process is None:
        # If sleep_process is active, we'll just keep it running
        sleep_process = subprocess.Popen(["/usr/bin/caffeinate", "-d", "-i", "-s"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Ensures that if we don't properly close the process, 'atexit' will for us
    atexit.register(enable_sleep_after_running)

def enable_sleep_after_running():
    global sleep_process
    if sleep_process:
        logging.info("Re-enabling Idle Sleep")
        sleep_process.kill()
        sleep_process = None


def check_kext_loaded(bundle_id: str) -> str:
    """
    Checks if a kext is loaded

    Parameters:
        bundle_id (str): The bundle ID of the kext to check

    Returns:
        str: The version of the kext if it is loaded, or "" if it is not loaded
    """
    # Name (Version) UUID <Linked Against>
    # no UUID for kextstat
    pattern = re.compile(re.escape(bundle_id) + r"\s+\((?P<version>.+)\)")

    args = ["/usr/sbin/kextstat", "-list-only", "-bundle-id", bundle_id]

    if Path("/usr/bin/kmutil").exists():
        args = ["/usr/bin/kmutil", "showloaded", "--list-only", "--variant-suffix", "release", "--optional-identifier", bundle_id]

    kext_loaded = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if kext_loaded.returncode != 0:
        return ""
    output = kext_loaded.stdout.decode()
    if not output.strip():
        return ""
    match = pattern.search(output)
    if match:
        return match.group("version")
    return ""


def check_secure_boot_model():
    """
    HardwareModel from the Apple Secure Boot NVRAM GUID, without NULs.

    OpenCore writes it as "<model>ap" (OcAppleSecureBootBootstrapValues(),
    "%aap") - config x86legacy becomes x86legacyap, j174 becomes j174ap - and
    only when Secure Boot is on. With SecureBootModel=Disabled, whatever boot.efi
    or the firmware set stays (x86legacyap on genuine non-T2 Macs since Monterey).
    """
    sbm_raw = get_nvram("HardwareModel", "94B73556-2197-4702-82A8-3E1337DAFBFB", decode=False)
    if not sbm_raw:
        return None
    try:
        if isinstance(sbm_raw, str):
            sbm_string = sbm_raw
        else:
            sbm_string = bytes(sbm_raw).decode("utf-8", errors="replace")
    except Exception:
        sbm_string = str(sbm_raw)
    sbm_string = sbm_string.replace("\x00", "").strip()
    return sbm_string or None


def _normalize_secure_boot_model(model) -> str:
    """
    HardwareModel as macOS reports it -> SecureBootModel as configured.

    Strips NULs/whitespace, lowercases (BridgeOSHardwareModel is uppercase) and
    drops Apple's "ap" suffix: "x86legacyap" -> "x86legacy", "j174ap" -> "j174".
    Only used for logging - the policy decides (see check_secure_boot_level()).
    """
    if not model:
        return ""
    model = str(model).replace("\x00", "").strip().lower()
    if model.endswith("ap"):
        model = model[:-2]
    return model


def _parse_secure_boot_policy(raw):
    """
    AppleSecureBootPolicy NVRAM value -> int, or None if it can't be understood.

    OpenCore stores a single UINT8 (OcAppleSecureBootInstallProtocol()), but
    IOKit may hand it back as bytes, bytearray, an NSData-like buffer, an int,
    or a string. Absent/empty means no policy (0).
    """
    if raw is None:
        return 0
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        if raw.strip().isdigit():
            return int(raw.strip())
        try:
            raw = raw.encode("latin-1")
        except Exception:
            return None
    try:
        data = bytes(raw)
    except Exception:
        return None
    return int.from_bytes(data, byteorder="little") if data else 0


def check_ap_security_policy():
    """
    AppleSecureBootPolicy: 0 = Disabled, 1 = Medium, 2 = Full.
    Returns -1 if the variable exists but can't be parsed.

    Ref: https://github.com/acidanthera/OpenCorePkg/blob/f7c1a3d483fa2535b6a62c25a4f04017bfeee09a/Include/Apple/Protocol/AppleImg4Verification.h#L27-L31
    """
    raw = get_nvram("AppleSecureBootPolicy", "94B73556-2197-4702-82A8-3E1337DAFBFB", decode=False)
    policy = _parse_secure_boot_policy(raw)
    return -1 if policy is None else policy


def check_secure_boot_level():
    """
    True if Apple Secure Boot is enforced for this boot - root patching must not run.

    The policy decides, not the model string:
      - OpenCore writes AppleSecureBootPolicy every boot from what it actually
        enforces (OpenCoreUefi.c): 0 for SecureBootModel=Disabled, 2 (Full) with
        ApECID set on a non-x86legacy model, otherwise 1 (Medium) - x86legacy,
        and "Default" resolving to it on non-T2 SMBIOS, is always Medium.
      - HardwareModel is only written when Secure Boot is on, as "<model>ap".
        With Secure Boot off it can still say x86legacyap (genuine non-T2 Macs
        since Monterey, policy 0) or a j-model (T2 firmware).
      - On T2 Macs whose firmware already provides the Secure Boot protocol,
        OpenCore leaves the policy to the firmware (Startup Security: Full 2,
        Medium 1, No Security 0) - root patching breaks either non-zero mode.

    Before, the model string was compared first. "x86legacyap" matched neither
    "x86legacy" nor constants.sbm_values, so this returned False exactly when
    OpenCore's x86legacy Secure Boot was on, and root patching went ahead into
    Err(0x1A) (Issue #465). A non-zero policy with an absent or unknown model
    was ignored the same way. An unreadable policy fails closed.
    """
    policy = check_ap_security_policy()
    if policy == 0:
        return False
    model = check_secure_boot_model()
    if policy < 0:
        logging.warning(f"AppleSecureBootPolicy can't be read (HardwareModel {model!r}) - treating Secure Boot as enabled")
    else:
        logging.info(f"Apple Secure Boot is enabled: AppleSecureBootPolicy {policy}, HardwareModel {model!r} "
                     f"(SecureBootModel {_normalize_secure_boot_model(model) or 'unknown'})")
    return True

clear = True


def disable_cls():
    global clear
    clear = False


def cls():
    if not clear:
        return
    if check_cli_args() is None:
        # Our GUI does not support clear screen
        if not check_recovery():
            os.system("cls" if os.name == "nt" else "clear")
        else:
            logging.info("\u001Bc")
            
def get_nvram(variable: str, uuid: str = None, *, decode: bool = False):
    # TODO: Properly fix for El Capitan, which does not print the XML representation even though we say to

    if uuid is not None:
        uuid += ":"
    else:
        uuid = ""

    nvram = ioreg.IORegistryEntryFromPath(ioreg.kIOMasterPortDefault, "IODeviceTree:/options".encode())

    value = ioreg.IORegistryEntryCreateCFProperty(nvram, f"{uuid}{variable}", ioreg.kCFAllocatorDefault, ioreg.kNilOptions)

    ioreg.IOObjectRelease(nvram)

    if not value:
        return None

    value = ioreg.corefoundation_to_native(value)

    if decode:
        if isinstance(value, bytes):
            try:
                value = value.strip(b"\0").decode()
            except UnicodeDecodeError:
                # Some sceanrios the firmware will throw garbage in
                # ie. iMac12,2 with FireWire boot-path
                value = None
        elif isinstance(value, str):
            value = value.strip("\0")
    return value


def get_rom(variable: str, *, decode: bool = False):
    # TODO: Properly fix for El Capitan, which does not print the XML representation even though we say to

    rom = ioreg.IORegistryEntryFromPath(ioreg.kIOMasterPortDefault, "IODeviceTree:/rom".encode())

    value = ioreg.IORegistryEntryCreateCFProperty(rom, variable, ioreg.kCFAllocatorDefault, ioreg.kNilOptions)

    ioreg.IOObjectRelease(rom)

    if not value:
        return None

    value = ioreg.corefoundation_to_native(value)

    if decode and isinstance(value, bytes):
        value = value.strip(b"\0").decode()
    return value

def get_firmware_vendor(*, decode: bool = False):
    efi = ioreg.IORegistryEntryFromPath(ioreg.kIOMasterPortDefault, "IODeviceTree:/efi".encode())
    value = ioreg.IORegistryEntryCreateCFProperty(efi, "firmware-vendor", ioreg.kCFAllocatorDefault, ioreg.kNilOptions)
    ioreg.IOObjectRelease(efi)

    if not value:
        return None

    value = ioreg.corefoundation_to_native(value)
    if decode:
        if isinstance(value, bytes):
            value = value.strip(b"\0").decode()
        elif isinstance(value, str):
            value = value.strip("\0")
    return value


def find_apfs_physical_volume(device):
    # ex: disk3s1s1
    # return: [disk0s2]
    disk_list = None
    physical_disks = []
    try:
        disk_list = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", device], stdout=subprocess.PIPE).stdout)
    except TypeError:
        pass

    if disk_list:
        try:
            # Note: Fusion Drive Macs return multiple APFSPhysicalStores:
            # APFSPhysicalStores:
            #  - 0:
            #      APFSPhysicalStore: disk0s2
            #  - 1:
            #      APFSPhysicalStore: disk3s2
            for disk in disk_list["APFSPhysicalStores"]:
                physical_disks.append(disk["APFSPhysicalStore"])
        except KeyError:
            pass
    return physical_disks

def clean_device_path(device_path: str):
    # ex:
    #   'PciRoot(0x0)/Pci(0xA,0x0)/Sata(0x0,0x0,0x0)/HD(1,GPT,C0778F23-3765-4C8E-9BFA-D60C839E7D2D,0x28,0x64000)/EFI\OC\OpenCore.efi'
    #   'PciRoot(0x0)/Pci(0x1A,0x7)/USB(0x0,0x0)/USB(0x2,0x0)/HD(2,GPT,4E929909-2074-43BA-9773-61EBC110A670,0x64800,0x38E3000)/EFI\OC\OpenCore.efi'
    #   'PciRoot(0x0)/Pci(0x1A,0x7)/USB(0x0,0x0)/USB(0x1,0x0)/\EFI\OC\OpenCore.efi'
    # return:
    #   'C0778F23-3765-4C8E-9BFA-D60C839E7D2D'
    #   '4E929909-2074-43BA-9773-61EBC110A670'
    #   'None'

    if device_path:
        if not any(partition in device_path for partition in ["GPT", "MBR"]):
            return None
        device_path_array = device_path.split("/")
        # we can always assume [-1] is 'EFI\OC\OpenCore.efi'
        if len(device_path_array) >= 2:
            device_path_stripped = device_path_array[-2]
            device_path_root_array = device_path_stripped.split(",")
            if len(device_path_root_array) > 2:
                return device_path_root_array[2]
    return None


def find_disk_off_uuid(uuid):
    # Find disk by UUID
    disk_list = None
    try:
        disk_list = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", uuid], stdout=subprocess.PIPE).stdout)
    except TypeError:
        pass
    if disk_list:
        try:
            return disk_list["DeviceIdentifier"]
        except KeyError:
            pass
    return None

def get_free_space(disk=None):
    """
    Get free space on disk in bytes

    Parameters:
        disk (str): Path to mounted disk (or folder on disk)

    Returns:
        int: Free space in bytes
    """
    if disk is None:
        disk = "/"

    total, used, free = shutil.disk_usage(disk)
    return free

def grab_mount_point_from_disk(disk):
    data = plistlib.loads(subprocess.run(["/usr/sbin/diskutil", "info", "-plist", disk], stdout=subprocess.PIPE).stdout.decode().strip().encode())
    return data["MountPoint"]

def monitor_disk_output(disk):
    # Returns MB written on drive
    output = subprocess.check_output(["/usr/sbin/iostat", "-Id", disk])
    output = output.decode("utf-8")
    #  Grab second last entry (last is \n)
    output = output.split(" ")
    output = output[-2]
    return output


def get_preboot_uuid() -> str:
    """
    Get the UUID of the Preboot volume
    """
    args = ["/usr/sbin/ioreg", "-a", "-n", "chosen", "-p", "IODeviceTree", "-r"]
    output = plistlib.loads(subprocess.run(args, stdout=subprocess.PIPE).stdout)
    return output[0]["apfs-preboot-uuid"].strip(b"\0").decode()


def block_os_updaters():
    # Disables any processes that would be likely to mess with
    # the root volume while we're working with it.
    bad_processes = [
        "softwareupdate",
        "SoftwareUpdate",
        "Software Update",
        "MobileSoftwareUpdate",
    ]
    output = subprocess.check_output(["/bin/ps", "-ax"])
    lines = output.splitlines()
    for line in lines:
        entry = line.split()
        pid = entry[0].decode()
        current_process = entry[3].decode()
        for bad_process in bad_processes:
            if bad_process in current_process:
                if pid != "":
                    logging.info(f"Killing Process: {pid} - {current_process.split('/')[-1]}")
                    subprocess.run(["/bin/kill", "-9", pid])
                    break

def fetch_staged_update(variant: str = "Update") -> tuple[str, str]:
    """
    Check for staged macOS update
    Supported variants:
    - Preflight
    - Update
    """

    os_build   = None
    os_version = None

    update_config = f"/System/Volumes/Update/{variant}.plist"
    if not Path(update_config).exists():
        return (None, None)
    try:
        update_staged = plistlib.load(open(update_config, "rb"))
    except:
        return (None, None)
    if "update-asset-attributes" not in update_staged:
        return (None, None)

    os_build   = update_staged["update-asset-attributes"]["Build"]
    os_version = update_staged["update-asset-attributes"]["OSVersion"]

    return os_version, os_build


def check_cli_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", help="Build OpenCore", action="store_true", required=False)
    parser.add_argument("--verbose", help="Enable verbose boot", action="store_true", required=False)
    parser.add_argument("--debug_oc", help="Enable OpenCore DEBUG", action="store_true", required=False)
    parser.add_argument("--debug_kext", help="Enable kext DEBUG", action="store_true", required=False)
    parser.add_argument("--hide_picker", help="Hide OpenCore picker", action="store_true", required=False)
    parser.add_argument("--disable_sip", help="Disable SIP", action="store_true", required=False)
    parser.add_argument("--disable_smb", help="Disable SecureBootModel", action="store_true", required=False)
    parser.add_argument("--vault", help="Enable OpenCore Vaulting", action="store_true", required=False)
    parser.add_argument("--support_all", help="Allow OpenCore on natively supported Models", action="store_true", required=False)
    parser.add_argument("--firewire", help="Enable FireWire Booting", action="store_true", required=False)
    parser.add_argument("--nvme", help="Enable NVMe Booting", action="store_true", required=False)
    parser.add_argument("--wlan", help="Enable Wake on WLAN support", action="store_true", required=False)
    # parser.add_argument("--disable_amfi", help="Disable AMFI", action="store_true", required=False)
    parser.add_argument("--moderate_smbios", help="Moderate SMBIOS Patching", action="store_true", required=False)
    parser.add_argument("--disable_tb", help="Disable Thunderbolt on 2013-2014 MacBook Pros", action="store_true", required=False)
    parser.add_argument("--force_surplus", help="Force SurPlus in all newer OSes", action="store_true", required=False)

    # Building args requiring value values (ie. --model iMac12,2)
    parser.add_argument("--model", action="store", help="Set custom model", required=False)
    parser.add_argument("--disk", action="store", help="Specifies disk to install to", required=False)
    parser.add_argument("--smbios_spoof", action="store", help="Set SMBIOS patching mode", required=False)

    # sys_patch args
    parser.add_argument("--patch_sys_vol", help="Patches root volume", action="store_true", required=False)
    parser.add_argument("--unpatch_sys_vol", help="Unpatches root volume, EXPERIMENTAL", action="store_true", required=False)
    parser.add_argument("--prepare_for_update", help="Prepares host for macOS update, ex. clean /Library/Extensions", action="store_true", required=False)
    parser.add_argument("--cache_os", help="Caches patcher files (ex. KDKs) for incoming OS in Preflight.plist", action="store_true", required=False)

    # validation args
    parser.add_argument("--validate", help="Runs Validation Tests for CI", action="store_true", required=False)

    # GUI args
    parser.add_argument("--gui_patch", help="Starts GUI in Root Patcher", action="store_true", required=False)
    parser.add_argument("--gui_unpatch", help="Starts GUI in Root Unpatcher", action="store_true", required=False)
    parser.add_argument("--auto_patch", help="Check if patches are needed and prompt user", action="store_true", required=False)
    parser.add_argument("--update_installed", help="Prompt user to finish updating via GUI", action="store_true", required=False)
    # Both of these are GUI-side switches, not CLI-mode triggers: they are
    # deliberately absent from the "did the user ask for CLI mode" check below,
    # so passing them still opens the normal GUI. They only need to exist here
    # because parse_args() aborts the whole app on an unrecognised argument.
    # "--developer" takes an optional value (nargs="?") purely for backwards
    # compatibility: as a plain store it demanded one, so passing just
    # "--developer" - the documented usage, and what application_entry.py looks
    # for in sys.argv - made argparse exit(2) and killed the launch.
    parser.add_argument("--developer", nargs="?", const=True, default=None, help="Force True Developer Mode", required=False)
    parser.add_argument("--disable_auto_update", help="Disable automatic installation of updates (updates are still checked for and offered), equivalent to Settings > \"Turn Off Auto Updates\"", action="store_true", required=False)

    args = parser.parse_args()
    if not (
        args.build or
        args.patch_sys_vol or
        args.unpatch_sys_vol or
        args.validate or
        args.auto_patch or
        args.prepare_for_update or
        args.cache_os
    ):
        return None
    else:
        return args

def get_admin_permission(action: str = "/usr/bin/whoami", args: list =None, reason: str = "OpenCore-Patcher-T2 needs your administrative permission"):
    """
    run the give action as root without using the Privileged Helper Tool

    * action: a str path to the progra being executed
    * args: a list of all the arguments to be sent to the program
    * reason: the message that tells the user why they are seeing this format it like this: why you are seeing this (e.g "OpenCore-Patcher-T2 needs your administrative permission") and what will [...]
    * confirm_button: the name of the OK button that is desplayed to the user if the default doesn't work
    * deny_button: the name of the Cancel button that is desplayed to the user if the default doesn't work
    """
    if not isinstance(reason, str) or not reason:
        # A non-str here (e.g. a bound method) used to raise "encoding without a string argument"
        logging.warning(f"get_admin_permission() called with invalid reason {type(reason).__name__}, using default prompt")
        reason = "OpenCore-Patcher-T2 needs your administrative permission"
    # Callers hand us whatever they built their argv from: str, bytes, and very often
    # pathlib.Path (e.g. install.py: ["/bin/mkdir", "-p", mount_path / "EFI"]).
    # bytes(<Path>, encoding="utf-8") raises "TypeError: encoding without a string
    # argument", which aborted every elevated command containing a Path as soon as the
    # Privileged Helper Tool was unusable. Normalise everything to bytes instead.
    def _as_arg_bytes(value) -> bytes:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if isinstance(value, (str, os.PathLike)):
            return os.fsencode(value)
        return str(value).encode("utf-8")

    action_bytes = _as_arg_bytes(action)
    if args is None:
        byte_args: list = []
        return_args = [os.fsdecode(action_bytes)]
    else:
        byte_args = [_as_arg_bytes(arg) for arg in args]
        return_args = [os.fsdecode(action_bytes)] + [os.fsdecode(arg) for arg in byte_args]
    status, auth_ref = Security.AuthorizationCreate(
        None,
        None,
        Security.kAuthorizationFlagDefaults,
        None
    )

    if status != Security.errAuthorizationSuccess:
        return subprocess.CompletedProcess(args=return_args, returncode=2, stderr=f"AuthorizationCreate failed with status {status}")

    try:
        rights = (
            Security.AuthorizationItem(
                Security.kAuthorizationRightExecute,
                0,
                None,
                0
            ),
        )
        prompt = bytes(reason, encoding="utf-8")
        environment = (
            Security.AuthorizationItem(
                Security.kAuthorizationEnvironmentPrompt,
                len(prompt),
                prompt,
                0
            ),
        )

        status, authorized_rights = Security.AuthorizationCopyRights(
            auth_ref,
            rights,
            environment,
            (
                Security.kAuthorizationFlagInteractionAllowed
                | Security.kAuthorizationFlagExtendRights
            ),
            None,
        )

        if status == Security.errAuthorizationCanceled:
            return subprocess.CompletedProcess(args=return_args, returncode=1, stdout="User canceled the request")

        if status != Security.errAuthorizationSuccess:
            return subprocess.CompletedProcess(args=return_args, returncode=2, stderr=f"AuthorizationCopyRights failed with status {status}")

        if not byte_args:
            status, _ = Security.AuthorizationExecuteWithPrivileges(
                auth_ref,
                action_bytes,
                Security.kAuthorizationFlagDefaults,
                b'',
                None,
            )
        else:
            status, _ = Security.AuthorizationExecuteWithPrivileges(
                auth_ref,
                action_bytes,
                Security.kAuthorizationFlagDefaults,
                byte_args,
                None,
            )

        if status == Security.errAuthorizationCanceled:
            return subprocess.CompletedProcess(args=return_args, returncode=1, stdout="User canceled the request")

        if status != Security.errAuthorizationSuccess:
            return subprocess.CompletedProcess(args=return_args, returncode=2, stderr=f"AuthorizationExecuteWithPrivileges failed with status {status}")
# fix the success logic of mount root volume
        return subprocess.CompletedProcess(args=return_args, returncode=0, stdout="Running as root succeeded")

    except Exception:
        logging.error("Running as root failed")
        logging.exception("Stack Trace:")
        return subprocess.CompletedProcess(args=return_args, returncode=2, stderr="Running as root failed")

    finally:
        Security.AuthorizationFree(
           auth_ref,
           Security.kAuthorizationFlagDefaults
        )
