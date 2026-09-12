"""
sys_patch.py: Framework for mounting and patching macOS root volume
"""

"""
System based off of Apple's Kernel Debug Kit (KDK)
- https://developer.apple.com/download/all/

The system relies on mounting the APFS volume as a live read/write volume
We perform our required edits, then create a new snapshot for the system boot

The manual process is as follows:
 1. Find the Root Volume
    'diskutil info / | grep "Device Node:"'
 2. Convert Snapshot Device Node to Root Volume Device Node
    /dev/disk3s1s1 -> /dev/disk3s1 (strip last 's1')
 3. Mount the APFS volume as a read/write volume
    'sudo mount -o nobrowse -t apfs  /dev/disk5s5 /System/Volumes/Update/mnt1'
 4. Perform edits to the system (ie. create new KernelCollection)
    'sudo kmutil install --volume-root /System/Volumes/Update/mnt1/ --update-all'
 5. Create a new snapshot for the system boot
    'sudo bless --folder /System/Volumes/Update/mnt1/System/Library/CoreServices --bootefi --create-snapshot'

Additionally Apple's APFS snapshot system supports system rollbacks:
  'sudo bless --mount /System/Volumes/Update/mnt1 --bootefi --last-sealed-snapshot'
Note: root volume rollbacks are unstable in Big Sur due to quickly discarding the original snapshot
- Generally within 2~ boots, the original snapshot is discarded
- Monterey always preserves the original snapshot allowing for reliable rollbacks

Alternative to mounting via 'mount', Apple's update system uses 'mount_apfs' directly
  '/sbin/mount_apfs -R /dev/disk5s5 /System/Volumes/Update/mnt1'

With macOS Ventura, you will also need to install the KDK onto root if you plan to use kmutil
This is because Apple removed on-disk binaries (ref: https://github.com/dortania/OpenCore-Legacy-Patcher/issues/998)
  'sudo ditto /Library/Developer/KDKs/<KDK Version>/System /System/Volumes/Update/mnt1/System'
"""

import logging
import plistlib
import subprocess
import sys
import threading

from pathlib   import Path
from functools import cache

from .mount import (
    RootVolumeMount,
    APFSSnapshot
)
from .utilities import (
    install_new_file,
    remove_file,
    PatcherSupportPkgMount,
    KernelDebugKitMerge
)

from .. import constants

from ..volume   import generate_copy_arguments

from ..datasets import (
    os_data
)
from ..support import (
    utilities,
    subprocess_wrapper,
    metallib_handler
)
from .patchsets import (
    HardwarePatchsetDetection,
    HardwarePatchsetSettings,
    PatchType,
    DynamicPatchset
)
from . import (
    sys_patch_helpers,
    kernelcache
)
from .auto_patcher import InstallAutomaticPatchingServices


# ============================
# Module Constants
# ============================

# APFS mount locations
MOUNT_LOCATION_BASE = "/System/Volumes/Update/mnt1"

# System paths
SYSTEM_EXTENSIONS_PATH = "/System/Library/Extensions"
CORE_SERVICES_PATH = "/System/Library/CoreServices"
APP_SUPPORT_PATH = "/Library/Application Support"
SKYLIGHT_PLUGINS_PATH = f"{APP_SUPPORT_PATH}/SkyLightPlugins"

# Preference paths
CORE_DISPLAY_PREF_PATH = "/Library/Preferences/com.apple.CoreDisplay"

# File names
PATCHSET_FILENAME = "OpenCore-Legacy-Patcher.plist"
SYSTEM_VERSION_FILENAME = "SystemVersion.plist"

# Preference keys
METAL_ENFORCEMENT_KEYS = ["useMetal", "useIOP"]


class PatchSysVolume:
    """
    Main patching orchestrator for macOS root volume.
    
    Handles mounting, patching, and snapshotting of the root APFS volume.
    Coordinates between multiple subsystems (kernel cache, KDK, metallib).
    """
    
    def __init__(self, model: str, global_constants: constants.Constants, hardware_details: list = None) -> None:
        self.model = model
        self.constants: constants.Constants = global_constants
        self.computer = self.constants.computer
        self.root_supports_snapshot = utilities.check_if_root_is_apfs_snapshot()
        self.constants.root_patcher_succeeded = False  # Reset Variable each time we start
        self.constants.needs_to_open_preferences = False
        self.patch_set_dictionary = {}
        self.needs_kmutil_exemptions = False  # For '/Library/Extensions' rebuilds
        self.kdk_path = None
        self.metallib_path = None
        self._metallib_preflight_refresh_attempted = False

        # GUI will detect hardware patches before starting PatchSysVolume()
        # However the TUI will not, so allow for data to be passed in manually avoiding multiple calls
        if hardware_details is None:
            hardware_details = HardwarePatchsetDetection(self.constants).device_properties
        self.hardware_details = hardware_details
        self._init_pathing()

        self.skip_root_kmutil_requirement = (
            not self.hardware_details[HardwarePatchsetSettings.KERNEL_DEBUG_KIT_REQUIRED]
            if self.constants.detected_os >= os_data.os_data.ventura
            else False
        )

        self.requires_kdk_caching = (
            self.hardware_details[HardwarePatchsetSettings.KERNEL_DEBUG_KIT_REQUIRED]
            and self.constants.detected_os >= os_data.os_data.ventura
        )
        self.requires_metallib_caching = (
            self.hardware_details[HardwarePatchsetSettings.METALLIB_SUPPORT_PKG_REQUIRED]
            and self.constants.detected_os >= os_data.os_data.sequoia
        )

        self.mount_obj = RootVolumeMount(self.constants.detected_os)


    def _init_pathing(self) -> None:
        """Initialize mount locations for root volume patching."""
        self.mount_location_data = ""
        if self.root_supports_snapshot:
            self.mount_location = MOUNT_LOCATION_BASE
        else:
            self.mount_location = ""

        self.mount_extensions = f"{self.mount_location}{SYSTEM_EXTENSIONS_PATH}"
        self.mount_application_support = f"{self.mount_location_data}{APP_SUPPORT_PATH}"
        logging.debug(f"Initialized pathing - mount_location: {self.mount_location}, "
                     f"extensions: {self.mount_extensions}")


    def _mount_root_vol(self) -> bool:
        """
        Mount root volume.
        
        Returns:
            bool: True if mount successful, False otherwise
        """
        logging.debug("Attempting to mount root volume")
        return self.mount_obj.mount()


    def _unmount_root_vol(self) -> None:
        """Unmount root volume gracefully."""
        logging.info("- Unmounting root volume")
        self.mount_obj.unmount(ignore_errors=True)


    def _run_sanity_checks(self) -> bool:
        """
        Run sanity checks before continuing patching.
        
        Verifies:
        - SystemVersion.plist exists on mounted volume
        - Build version matches expected version
        - No system update is in progress
        
        Returns:
            bool: True if all checks pass, False otherwise
        """
        logging.info("- Running sanity checks before patching")

        mounted_system_version = Path(self.mount_location) / CORE_SERVICES_PATH / SYSTEM_VERSION_FILENAME

        if not mounted_system_version.exists():
            logging.error("- Failed to find SystemVersion.plist on mounted root volume")
            return False

        try:
            with open(mounted_system_version, "rb") as plist_file:
                mounted_data = plistlib.load(plist_file)
            
            if mounted_data.get("ProductBuildVersion") != self.constants.detected_os_build:
                product_version = mounted_data.get("ProductVersion", "unknown")
                product_build = mounted_data.get("ProductBuildVersion", "unknown")
                logging.error(
                    f"- SystemVersion.plist build version mismatch: "
                    f"found {product_version} ({product_build}), "
                    f"expected {self.constants.detected_os_version} ({self.constants.detected_os_build})"
                )
                logging.error("An update is in progress on your machine and patching cannot continue until it is cancelled or finished")
                return False
        except FileNotFoundError:
            logging.error("- SystemVersion.plist file not found")
            logging.exception("Stack Trace:")
            return False
        except plistlib.InvalidFileException as e:
            logging.error(f"- Failed to parse SystemVersion.plist: {e}")
            logging.exception("Stack Trace:")
            return False
        except Exception as e:
            logging.error(f"- Unexpected error reading SystemVersion.plist: {e}")
            logging.exception("Stack Trace:")
            return False

        logging.debug("Sanity checks passed")
        return True


    def _prompt_open_software_update(self) -> bool:
        """
        Ask the user whether Software Update should be opened after failed sanity checks.

        input() cannot be used here: the patcher normally runs from the app bundle,
        where no stdin is attached, so the prompt would raise instead of asking.
        start_patch() additionally runs on a worker thread (see wx_gui/gui_sys_patch_start.py),
        so the dialog has to be marshalled onto the main thread and waited on.

        Returns:
            bool: True if the user asked for Software Update to be opened.
                  False if declined, or when running without a GUI (ex. '--patch' from the CLI).
        """
        try:
            import wx
        except ImportError:
            logging.info("- No GUI available, skipping Software Update prompt")
            return False

        if wx.GetApp() is None:
            logging.info("- No GUI available, skipping Software Update prompt")
            return False

        result   = {}
        finished = threading.Event()

        def _show_dialog() -> None:
            try:
                dialog = wx.MessageDialog(
                    parent=wx.GetApp().GetTopWindow(),
                    message=(
                        "Pending macOS updates or upgrades were detected on this system.\n\n"
                        "It is recommended to install that update/upgrade first, and only then "
                        "run the patcher once again.\n\n"
                        "Would you like to open Software Update now?"
                    ),
                    caption="Cannot Patch: Pending Updates",
                    style=wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING
                )
                result["open_software_update"] = dialog.ShowModal() == wx.ID_YES
                dialog.Destroy()
            except Exception as e:
                logging.error(f"Failed to display pending update dialog: {e}")
                logging.exception("Stack Trace:")
            finally:
                finished.set()

        if wx.IsMainThread():
            _show_dialog()
        else:
            wx.CallAfter(_show_dialog)
            finished.wait()

        return result.get("open_software_update", False)


    def _open_software_update(self) -> None:
        """
        Open the Software Update pane in System Settings/System Preferences.

        The pane identifier changed with Ventura's System Settings rewrite.
        """
        pane = "x-apple.systempreferences:com.apple.preferences.softwareupdate"
        if self.constants.detected_os >= os_data.os_data.ventura:
            pane = "x-apple.systempreferences:com.apple.Software-Update-Settings.extension"

        logging.info("- Launching Software Update...")
        subprocess.run(["open", pane])


    def _merge_kdk_with_root(self, save_hid_cs: bool = False) -> None:
        """
        Merge Kernel Debug Kit (KDK) with the root volume.
        
        If no KDK is present, will call kdk_handler to download and install it.

        Parameters:
            save_hid_cs (bool): If True, will save the HID CS file before merging KDK.
                                Required for USB 1.1 downgrades on Ventura and newer.
        """
        try:
            logging.debug(f"Merging KDK with root volume (save_hid_cs={save_hid_cs})")
            self.kdk_path = KernelDebugKitMerge(
                self.constants,
                self.mount_location,
                self.skip_root_kmutil_requirement
            ).merge(save_hid_cs)
        except Exception as e:
            logging.error("Merging KDK with root volume failed")
            logging.exception("Stack Trace:")
            return


    def _unpatch_root_vol(self):
        """
        Revert APFS snapshot and clean up any changes made to the root and data volume.
        """
        logging.info("- Starting APFS snapshot revert")
        
        if not APFSSnapshot(self.constants.detected_os, self.mount_location).revert_snapshot():
            logging.error("- Failed to revert APFS snapshot")
            return

        self._clean_skylight_plugins()
        self._delete_nonmetal_enforcement()

        # Clean up any lingering OCLP manifests across root mount and Data volume
        for manifest_path in [
            Path("/Library/Application Support/Dortania/OpenCore-Legacy-Patcher.plist"),
            Path("/Library/Application Support/Dortania/OpenCore-Legacy-Patcher-Lifecycle.plist"),
            Path(f"{self.mount_location}{CORE_SERVICES_PATH}/{PATCHSET_FILENAME}"),
            Path(f"{CORE_SERVICES_PATH}/{PATCHSET_FILENAME}"),
        ]:
            if manifest_path.exists():
                logging.info(f"- Removing manifest: {manifest_path}")
                try:
                    subprocess_wrapper.run_as_root(["/bin/rm", "-f", str(manifest_path)])
                except Exception as e:
                    logging.warning(f"- Failed to remove {manifest_path}: {e}")

        try:
            kernelcache.KernelCacheSupport(
                mount_location_data=self.mount_location_data,
                detected_os=self.constants.detected_os,
                skip_root_kmutil_requirement=self.skip_root_kmutil_requirement
            ).clean_auxiliary_kc()
        except Exception as e:
            logging.error(f"- Failed to clean auxiliary kernel cache: {e}")
            logging.exception("Stack Trace:")
            return

        self.constants.root_patcher_succeeded = True
        logging.info("- Unpatching complete")
        logging.info("\nPlease reboot the machine for patches to take effect")


    def _rebuild_root_volume(self) -> bool:
        """
        Rebuild the root volume.
        
        Steps:
        - Rebuilds the Kernel Collection
        - Updates the Preboot Kernel Cache
        - Rebuilds the dyld Shared Cache
        - Creates a new APFS Snapshot

        Returns:
            bool: True if successful, False if not
        """
        if not self._rebuild_kernel_cache():
            return False

        self._update_preboot_kernel_cache()
        self._rebuild_dyld_shared_cache()

        if not self._create_new_apfs_snapshot():
            return False
        try:
            logging.info("Unmounting the root volume")
            self._unmount_root_vol()
        except Exception as e:
            logging.error("Failed to unmount the root volume")
            logging.exception("Stack Trace:")

        logging.info("- Patching complete")
        logging.info("\nPlease reboot the machine for patches to take effect")

        if self.needs_kmutil_exemptions:
            if self.constants.detected_os <= os_data.os_data.monterey:
                logging.info("Note: Apple will require you to open System Preferences -> Security to allow the new kernel extensions to be loaded")
            else:
                logging.info("Note: Apple will require you to open System Settings -> Privacy & Security to allow the new kernel extensions to be loaded")

        self.constants.root_patcher_succeeded = True
        return True


    def _rebuild_kernel_cache(self) -> bool:
        """
        Rebuild the kernel cache.
        
        Returns:
            bool: True if successful, False otherwise
        """
        logging.debug("Rebuilding kernel cache")
        
        try:
            result = kernelcache.RebuildKernelCache(
                os_version=self.constants.detected_os,
                mount_location=self.mount_location,
                auxiliary_cache=self.needs_kmutil_exemptions,
                auxiliary_cache_only=self.skip_root_kmutil_requirement
            ).rebuild()

            if not result:
                logging.error("- Kernel cache rebuild failed")
                logging.exception("Stack Trace:")
                return False

            if not self.skip_root_kmutil_requirement:
                sys_patch_helpers.SysPatchHelpers(self.constants).install_rsr_repair_binary()

            return True
        except Exception as e:
            logging.error(f"- Exception during kernel cache rebuild: {e}")
            logging.exception("Stack Trace:")
            return False


    def _create_new_apfs_snapshot(self) -> bool:
        """
        Create a new APFS snapshot of the root volume.
        
        Returns:
            bool: True if snapshot was created, False if not
        """
        logging.debug("Creating new APFS snapshot")
        try:
            logging.info("Creating APFS snapshot")
            return APFSSnapshot(self.constants.detected_os, self.mount_location).create_snapshot()
        except Exception as e:
            logging.error(f"- Failed to create APFS snapshot: {e}")
            logging.exception("Stack Trace:")
            return False


    def _rebuild_dyld_shared_cache(self) -> None:
        """
        Rebuild the dyld shared cache.
        
        Only required on Mojave and older.
        """
        if self.constants.detected_os > os_data.os_data.catalina:
            logging.info(f"You're running newer version than macOS 10.14 Mojave, so not compatible with dyld shared cache patches.")
            return
        else: # behebt eine Sicherheitslücke, die erlaubt Angreifern zum Erzwingen von macOS Mojave-Patches auf moderne Systemen zu erzwingen, um die Betriebssystem unbrauchbar zu machen
            logging.info("- Rebuilding dyld shared cache")
            try:
                subprocess_wrapper.run_as_root_and_verify(
                    ["/usr/bin/update_dyld_shared_cache", "-root", f"{self.mount_location}/"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT
                )
            except Exception as e:
                logging.error(f"- Failed to rebuild dyld shared cache: {e}")
                logging.exception("Stack Trace:")


    def _update_preboot_kernel_cache(self) -> None:
        """
        Update the preboot kernel cache.
        
        Only required on Catalina.
        """
        if self.constants.detected_os != os_data.os_data.catalina:
            logging.info("You're not running macOS 10.15 Catalina. The patch for updating the preboot kernel cache is not compatible for your system.")
            return
        else: # behebt eine Sicherheitslücke, die erlaubt Angreifern, Patches fürs Catalina zu injizieren
            logging.info("- Rebuilding preboot kernel cache")
            try:
                subprocess_wrapper.run_as_root_and_verify(
                    ["/usr/sbin/kcditto"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT
                )
            except Exception as e:
                logging.error(f"- Failed to update preboot kernel cache: {e}")
                logging.exception("Stack Trace:")


    def _clean_skylight_plugins(self) -> None:
        """
        Clean non-Metal's SkylightPlugins folder.
        
        Ensures old plugins aren't lingering from previous installs.
        """
        try:
            skylight_path = Path(self.mount_application_support) / SKYLIGHT_PLUGINS_PATH.split("/")[-1]
            
            if skylight_path.exists():
                logging.info("- Found SkylightPlugins folder, removing old plugins")
                subprocess_wrapper.run_as_root_and_verify(
                    ["/bin/rm", "-Rf", str(skylight_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT
                )
                subprocess_wrapper.run_as_root_and_verify(
                    ["/bin/mkdir", str(skylight_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT
                )
            else:
                logging.info("- Creating SkylightPlugins folder")
                subprocess_wrapper.run_as_root_and_verify(
                    ["/bin/mkdir", "-p", str(skylight_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT
                )
        except Exception as e:
            logging.error(f"- Failed to manage SkylightPlugins folder: {e}")
            logging.exception("Stack Trace:")


    def _delete_nonmetal_enforcement(self) -> None:
        """
        Remove defaults related to forced OpenGL rendering.
        
        Primarily for development purposes and cleanup.
        """
        for arg in METAL_ENFORCEMENT_KEYS:
            try:
                result = subprocess.run(
                    ["/usr/bin/defaults", "read", CORE_DISPLAY_PREF_PATH, arg],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=10
                ).stdout.decode("utf-8").strip()
                
                if result in ["0", "false", "1", "true"]:
                    logging.info(f"- Removing non-Metal Enforcement Preference: {arg}")
                    subprocess_wrapper.run_as_root(["/usr/bin/defaults", "delete", CORE_DISPLAY_PREF_PATH, arg])
            except subprocess.TimeoutExpired:
                logging.warning(f"- Timeout reading preference {arg}")
            except Exception as e:
                logging.debug(f"- Could not read preference {arg}: {e}")


    def _write_patchset(self, patchset: dict) -> None:
        """
        Write patchset information to root volume.
        
        Stores metadata about applied patches for system recovery.

        Parameters:
            patchset (dict): Patchset information (generated by HardwarePatchsetDetection)
        """
        destination_path = f"{self.mount_location}{CORE_SERVICES_PATH}"
        destination_path_file = f"{destination_path}/{PATCHSET_FILENAME}"
        
        try:
            if sys_patch_helpers.SysPatchHelpers(self.constants).generate_patchset_plist(
                patchset, PATCHSET_FILENAME, self.kdk_path, self.metallib_path
            ):
                logging.info("- Writing patchset information to Root Volume")
                if Path(destination_path_file).exists():
                    subprocess_wrapper.run_as_root_and_verify(
                        ["/bin/rm", destination_path_file],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT
                    )
                subprocess_wrapper.run_as_root_and_verify(
                    generate_copy_arguments(f"{self.constants.payload_path}/{PATCHSET_FILENAME}", destination_path),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT
                )
        except Exception as e:
            logging.error(f"- Failed to write patchset: {e}")
            logging.exception("Stack Trace:")


    def _patch_root_vol(self):
        """
        Main patching orchestrator.
        
        Executes patches and triggers kernel cache rebuild.
        """
        logging.info(f"- Running patches for {self.model}")
        try:
            patches = self.patch_set_dictionary if self.patch_set_dictionary else HardwarePatchsetDetection(self.constants).patches
            self._execute_patchset(patches)
    
            if self.constants.wxpython_variant and self.constants.detected_os >= os_data.os_data.big_sur:
                needs_daemon = self.requires_kdk_caching or self.requires_metallib_caching
                InstallAutomaticPatchingServices(self.constants).install_auto_patcher_launch_agent(
                    kdk_caching_needed=needs_daemon
                )
    
            self._rebuild_root_volume()
        except Exception as e:
            logging.error("We have a problem to execute patches and rebuild the Kernel Cache.")
            logging.exception("Stack Trace:")
            return


    def _get_destination_path(self, method_type: PatchType, patch_directory: str) -> str:
        """
        Resolve destination path based on patch method type.
        
        Parameters:
            method_type: Type of patch installation method
            patch_directory: Target directory within the volume
            
        Returns:
            str: Full destination path
        """
        try:
            if method_type in [PatchType.OVERWRITE_SYSTEM_VOLUME, PatchType.MERGE_SYSTEM_VOLUME, PatchType.REMOVE_SYSTEM_VOLUME]:
                return str(self.mount_location) + patch_directory
            else:
                return str(self.mount_location_data) + patch_directory
        except Exception as e:
            logging.error("We couldn't get the destination path.")
            logging.exception("Stack Trace:")


    def _handle_patch_removal(self, required_patches: dict, patch: str, kc_support_obj) -> None:
        """
        Handle removal of files as specified in the patchset.
        
        Parameters:
            required_patches: Full patchset dictionary
            patch: Current patch name being processed
            kc_support_obj: Kernel cache support object
        """
        for method_remove in [PatchType.REMOVE_SYSTEM_VOLUME, PatchType.REMOVE_DATA_VOLUME]:
            try:
                if method_remove not in required_patches[patch]:
                    continue
            except Exception as e:
                 logging.error("We have issues to handle patch removal, so we couldn't remove the patch.")
                 logging.exception("Stack Trace:")
                 return
                
            for remove_patch_directory in required_patches[patch][method_remove]:
                logging.info("- Remove Files at: " + remove_patch_directory)
                destination_folder_path = self._get_destination_path(method_remove, remove_patch_directory)
                
                for remove_patch_file in required_patches[patch][method_remove][remove_patch_directory]:
                    logging.debug(f"Removing file: {remove_patch_file} from {destination_folder_path}")
                    remove_file(destination_folder_path, remove_patch_file)


    def _handle_patch_installation(self, required_patches: dict, patch: str, source_files_path: str, kc_support_obj) -> None:
        """
        Handle installation of files as specified in the patchset.
        
        Parameters:
            required_patches: Full patchset dictionary
            patch: Current patch name being processed
            source_files_path: Path to source binary files
            kc_support_obj: Kernel cache support object
        """
        for method_install in [
            PatchType.OVERWRITE_SYSTEM_VOLUME,
            PatchType.OVERWRITE_DATA_VOLUME,
            PatchType.MERGE_SYSTEM_VOLUME,
            PatchType.MERGE_DATA_VOLUME
        ]:
            if method_install not in required_patches[patch]:
                continue

            for install_patch_directory in list(required_patches[patch][method_install]):
                logging.info(f"- Handling Installs in: {install_patch_directory}")
                
                for install_file in list(required_patches[patch][method_install][install_patch_directory]):
                    source_folder_path = required_patches[patch][method_install][install_patch_directory][install_file] + install_patch_directory
                    
                    # Check whether to source from root
                    if not required_patches[patch][method_install][install_patch_directory][install_file].startswith("/"):
                        source_folder_path = source_files_path + "/" + source_folder_path

                    destination_folder_path = self._get_destination_path(method_install, install_patch_directory)
                    
                    # Handle special cases for data volume extensions
                    if method_install in [PatchType.OVERWRITE_DATA_VOLUME, PatchType.MERGE_DATA_VOLUME]:
                        if install_patch_directory == "/Library/Extensions":
                            self.needs_kmutil_exemptions = True
                            if kc_support_obj.check_kexts_needs_authentication(install_file):
                                self.constants.needs_to_open_preferences = True

                    # Add auxiliary kernel cache support if needed
                    updated_destination_folder_path = kc_support_obj.add_auxkc_support(
                        install_file,
                        source_folder_path,
                        install_patch_directory,
                        destination_folder_path
                    )
                    
                    if updated_destination_folder_path != destination_folder_path:
                        if kc_support_obj.check_kexts_needs_authentication(install_file):
                            self.constants.needs_to_open_preferences = True
                        
                        # Update required_patches to reflect the new destination folder path
                        if updated_destination_folder_path not in required_patches[patch][method_install]:
                            required_patches[patch][method_install].update({updated_destination_folder_path: {}})
                        required_patches[patch][method_install][updated_destination_folder_path].update({
                            install_file: required_patches[patch][method_install][install_patch_directory][install_file]
                        })
                        required_patches[patch][method_install][install_patch_directory].pop(install_file)
                        
                        destination_folder_path = updated_destination_folder_path

                    logging.debug(f"Installing file: {install_file} to {destination_folder_path}")
                    install_new_file(source_folder_path, destination_folder_path, install_file, method_install)


    def _execute_patchset(self, required_patches: dict):
        """
        Execute provided patchset.
        Orchestrates file removal, installation, and post-install scripts.
        Parameters:
            required_patches (dict): Patchset to execute (generated by HardwarePatchsetDetection)
        """
        kc_support_obj = kernelcache.KernelCacheSupport(
            mount_location_data=self.mount_location_data,
            detected_os=self.constants.detected_os,
            skip_root_kmutil_requirement=self.skip_root_kmutil_requirement
        )

        source_files_path = str(self.constants.payload_local_binaries_root_path)
        required_patches = self._preflight_checks(required_patches, source_files_path)
        
        for patch in required_patches:
            logging.info("- Installing Patchset: " + patch)
            
            # Handle file removals
            self._handle_patch_removal(required_patches, patch, kc_support_obj)
            
            # Handle file installations
            self._handle_patch_installation(required_patches, patch, source_files_path, kc_support_obj)

            # Handle post-install scripts
            if PatchType.EXECUTE in required_patches[patch]:
                for process in required_patches[patch][PatchType.EXECUTE]:
                    # Some processes need sudo, however we cannot directly call sudo in some scenarios
                    # Instead, call elevated function if string's boolean is True
                    if required_patches[patch][PatchType.EXECUTE][process]:
                        logging.info(f"- Running Process as Root:\n{process}")
                        try:
                            subprocess_wrapper.run_as_root_and_verify(
                                process.split(" "),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT
                            )
                        except Exception as e:
                            logging.error(f"- Failed to execute root process: {e}")
                            logging.error("Stack Trace:")
                    else:
                        logging.info(f"- Running Process:\n{process}")
                        try:
                            subprocess_wrapper.run_and_verify(
                                process.split(" "),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT
                            )
                        except Exception as e:
                            logging.error(f"- Failed to execute process: {e}")
                            logging.exception("Stack Trace:")

        # Handle GPU-specific patches
        if any(x in required_patches for x in ["AMD Legacy GCN", "AMD Legacy Polaris", "AMD Legacy Vega"]):
            try:
                sys_patch_helpers.SysPatchHelpers(self.constants).disable_window_server_caching()
            except Exception as e:
                logging.error(f"- Failed to disable window server caching: {e}")
                logging.exception("Stack Trace:")
        
        if "Metal 3802 Common Extended" in required_patches:
            try:
                sys_patch_helpers.SysPatchHelpers(self.constants).patch_gpu_compiler_libraries(mount_point=self.mount_location)
            except Exception as e:
                logging.error(f"- Failed to patch GPU compiler libraries: {e}")
                logging.exception("Stack Trace:")

        self._write_patchset(required_patches)


    def _resolve_metallib_support_pkg(self, _post_install_retry: bool = False, force_refresh: bool = False) -> str:
        """
        Resolve MetalLibSupportPkg.
        
        Downloads and installs if necessary.
        
        Returns:
            str: Path to resolved metallib support package
            
        Raises:
            Exception: If resolution fails
        """
        metallib_obj = metallib_handler.MetalLibraryObject(
            self.constants,
            self.constants.detected_os_build,
            self.constants.detected_os_version,
            ignore_installed=force_refresh
        )
        
        if not metallib_obj.success:
            logging.error(f"Failed to find MetalLibSupportPkg: {metallib_obj.error_msg}")
            logging.exception("Stack Trace:")
            raise Exception(f"Failed to find MetalLibSupportPkg: {metallib_obj.error_msg}")

        metallib_download_obj = metallib_obj.retrieve_download()
        if not metallib_download_obj:
            # Already downloaded, return path
            logging.info(f"Using MetalLibSupportPkg: {metallib_obj.metallib_installed_path}")
            self.metallib_path = metallib_obj.metallib_installed_path
            return str(metallib_obj.metallib_installed_path)

        if _post_install_retry:
            # We already downloaded and installed MetalLibSupportPkg once during this
            # resolution attempt, but it's still not detected on disk afterwards.
            # Recursing again here would silently re-download and re-install the
            # ~100MB package forever (this is what caused the "metallib download
            # gets stuck in an endless loop" reports), so bail out with a clear
            # error instead of looping.
            logging.error("MetalLibSupportPkg was installed but could not be verified on disk afterwards")
            logging.exception("Stack Trace:")
            raise Exception(
                "MetalLibSupportPkg installer reported success, but the package still isn't "
                "detected on disk afterwards. This points to a permissions or installer issue "
                "rather than a network problem - please check Console.app for installer errors "
                "and file a bug report."
            )

        metallib_download_obj.download(spawn_thread=False)
        if not metallib_download_obj.download_complete:
            logging.error(f"Could not download MetalLibSupportPkg: {metallib_download_obj.error_msg}")
            logging.exception("Stack Trace:")
            raise Exception(f"Could not download MetalLibSupportPkg: {metallib_download_obj.error_msg}")

        if not metallib_obj.install_metallib():
            logging.error("Failed to install MetalLibSupportPkg")
            logging.exception("Stack Trace:")
            raise Exception("Failed to install MetalLibSupportPkg")

        # After install, verify it's now present - but only retry once, otherwise a
        # persistent detection mismatch turns into an infinite re-download loop.
        return self._resolve_metallib_support_pkg(_post_install_retry=True)


    @cache
    def _resolve_dynamic_patchset(self, variant: DynamicPatchset) -> str:
        """
        Resolve dynamic patchset to a path.
        
        Caches results to avoid repeated downloads/installations.

        Parameters:
            variant: Dynamic patchset variant to resolve
            
        Returns:
            str: Path to resolved patchset
            
        Raises:
            Exception: If variant is unknown
        """
        logging.debug(f"Resolving dynamic patchset: {variant}")
        # behebt eine Sicherheitslücke, die erlaubt Angreifern, das App zum Absturz bringen beim unerwartetes Fehler oder davon auszunutzen, belibiges Code auszuführen
        try:
            logging.info("Resolving dynamic patchset")
            if variant == DynamicPatchset.MetallibSupportPkg:
                return self._resolve_metallib_support_pkg()
                logging.info("Successfully resolved the dynamic patchset")
            else:
                logging.error(f"Unknown Dynamic Patchset: {variant}")
                logging.exception("Stack Trace:")
                raise Exception(f"Unknown Dynamic Patchset: {variant}")
        except Exception as e:
            logging.error("Couldn't resolve patchset due to unexpected error:")
            logging.exception("Stack Trace:")
            logging.info("Please try again later.")
            sys.exit(3)


    def _preflight_checks(self, required_patches: dict, source_files_path: Path) -> dict:
        """
        Run preflight checks before patching.
        
        Validates:
        - All required files exist
        - Dynamic patchsets are resolved
        - Legacy plugin cleanup
        - Kernel cache cleanup
        - Hardware-specific setup (SNB, KDK)

        Parameters:
            required_patches (dict): Patchset dictionary (from HardwarePatchsetDetection)
            source_files_path (Path): Path to the source files (PatcherSupportPkg)

        Returns:
            dict: Updated patchset dictionary
            
        Raises:
            Exception: If critical preflight check fails
        """
        logging.info("- Running Preflight Checks before patching")

        # Validate all required files exist
        for patch in required_patches:
            for method_type in [
                PatchType.OVERWRITE_SYSTEM_VOLUME,
                PatchType.OVERWRITE_DATA_VOLUME,
                PatchType.MERGE_SYSTEM_VOLUME,
                PatchType.MERGE_DATA_VOLUME
            ]:
                if method_type not in required_patches[patch]:
                    continue
                    
                for install_patch_directory in list(required_patches[patch][method_type]):
                    for install_file in list(required_patches[patch][method_type][install_patch_directory]):
                        is_dynamic_patchset = False
                        try:
                            # Resolve dynamic patchsets
                            if required_patches[patch][method_type][install_patch_directory][install_file] in DynamicPatchset:
                                is_dynamic_patchset = True
                                required_patches[patch][method_type][install_patch_directory][install_file] = self._resolve_dynamic_patchset(
                                    required_patches[patch][method_type][install_patch_directory][install_file]
                                )
                        except TypeError:
                            pass
                        # behebt auch eine Sicherheitslücke, die erlaubt Angreifern, falls dies nicht ein erwartetes Fehler ist, beliebiges Code auszuführen
                        except Exception as e:
                            logging.error("We couldn't resolve the dynamic patchset and install Metallibs due to the following error:")
                            logging.exception("Stack Trace:")
                            logging.info("Please try again later.")
                            logging.info("Try reporting this issue to the OpenCore Legacy Patcher T2 repository and check for updates.")
                            sys.exit(3)

                        source_file = (
                            required_patches[patch][method_type][install_patch_directory][install_file]
                            + install_patch_directory
                            + "/"
                            + install_file
                        )

                        # Check whether to source from root
                        if not required_patches[patch][method_type][install_patch_directory][install_file].startswith("/"):
                            source_file = source_files_path + "/" + source_file

                        if not Path(source_file).exists():
                            # If a payload directory (such as a speculative macOS release version like
                            # '12.5-25' or '12.5-26') does not exist in PatcherSupportPkg's Universal-Binaries,
                            # attempt fallback to earlier compatible payload versions (e.g. 12.5-24 -> 12.5-23.4 -> 12.5-22 -> 12.5).
                            current_src_rel = required_patches[patch][method_type][install_patch_directory][install_file]
                            if not current_src_rel.startswith("/"):
                                for fallback_ver in ["12.5-24", "12.5-23.4", "12.5-23", "12.5-22", "12.5"]:
                                    candidate_file = source_files_path + "/" + fallback_ver + install_patch_directory + "/" + install_file
                                    if Path(candidate_file).exists():
                                        logging.warning(
                                            f"- Missing payload {current_src_rel}/{install_file}; "
                                            f"falling back to existing {fallback_ver}/{install_file}"
                                        )
                                        required_patches[patch][method_type][install_patch_directory][install_file] = fallback_ver
                                        source_file = candidate_file
                                        break

                        if not Path(source_file).exists():
                            # _local_metallib_installed() only matches an already-installed
                            # MetallibSupportPkg folder by macOS build name, never by verifying
                            # every file inside it is actually present. If an earlier run left
                            # behind a package that's missing files this patchset needs (e.g.
                            # upstream shipped an incomplete release for this build and later
                            # fixed it), every future preflight attempt would keep failing on the
                            # same stale "already installed" cache forever. Force one fresh
                            # re-download/install before giving up.
                            if (
                                self.metallib_path
                                and source_file.startswith(str(self.metallib_path))
                                and not self._metallib_preflight_refresh_attempted
                            ):
                                self._metallib_preflight_refresh_attempted = True
                                logging.warning(f"- {source_file} missing from cached MetallibSupportPkg, forcing a fresh download")
                                try:
                                    refreshed_path = self._resolve_metallib_support_pkg(force_refresh=True)
                                    self._resolve_dynamic_patchset.cache_clear()
                                    required_patches[patch][method_type][install_patch_directory][install_file] = refreshed_path
                                    source_file = refreshed_path + install_patch_directory + "/" + install_file
                                # behebt eine Sicherheitslücke, indem das Fehler ausgedrückt wurde, aber das Prozess nicht richtig beendete und dass das Fehler nicht besonders verständlich war. Angreifern können davon ausnutzen ohne der Ahnung des Nutzers auszuführen, ohne überhaupt das zu loggen, um schädliches Code ungemerkt zu ausführen. Und auch, Angreifern können davon ausnutzen, um ClickFix-Angriffe zu starten.
                                except Exception as e:
                                    logging.error(f"- Failed to force-refresh MetallibSupportPkg: {e}")
                                    logging.exception("Stack Trace:")
                                    logging.info("Try reporting this issue to the OpenCore Legacy Patcher T2 repository and check for updates.")
                                    sys.exit(3)

                            if not Path(source_file).exists():
                                if is_dynamic_patchset:
                                    # Even after a fresh MetallibSupportPkg pull, this specific file is
                                    # still missing. MetallibSupportPkg packages are generated per exact
                                    # macOS build by a third-party service and aren't guaranteed to
                                    # contain every single metallib for every build/Mac combination (see
                                    # reports of e.g. missing VisionKitInternal.framework/.../default.metallib
                                    # on multiple different builds, even after updating macOS). Treat a
                                    # missing file sourced from it as non-fatal: skip installing just this
                                    # one file instead of aborting root patching entirely, since these are
                                    # supplemental shader libraries, not files every patch set depends on
                                    # to function.
                                    logging.warning(f"- MetallibSupportPkg is missing {install_patch_directory}/{install_file} for this build, skipping")
                                    del required_patches[patch][method_type][install_patch_directory][install_file]
                                    continue
                                else:
                                    logging.error(f"Failed to find {source_file}")
                                    logging.exception("Stack Trace:")
                                    raise Exception(f"Failed to find {source_file}")

                        logging.debug(f"Verified file exists: {source_file}")

        # Make sure old SkyLight plugins aren't being used
        self._clean_skylight_plugins()

        # Make sure non-Metal Enforcement preferences are not present
        self._delete_nonmetal_enforcement()

        # Make sure we clean old kexts in /L*/E* that are not in the patchset
        try:
            kernelcache.KernelCacheSupport(
                mount_location_data=self.mount_location_data,
                detected_os=self.constants.detected_os,
                skip_root_kmutil_requirement=self.skip_root_kmutil_requirement
            ).clean_auxiliary_kc()
        except Exception as e:
            logging.error(f"- Failed to clean auxiliary kernel cache during preflight: {e}")
            logging.exception("Stack Trace:")
            raise

        # Make sure SNB kexts are compatible with the host
        if "Intel Sandy Bridge" in required_patches:
            try:
                sys_patch_helpers.SysPatchHelpers(self.constants).snb_board_id_patch(source_files_path)
            except Exception as e:
                logging.error(f"- Failed to patch Sandy Bridge board ID: {e}")
                logging.exception("Stack Trace:")
                raise

        # Ensure KDK is properly installed
        try:
            self._merge_kdk_with_root(
                save_hid_cs="Legacy USB 1.1" in required_patches
            )
        except Exception as e:
            logging.error(f"- Failed to merge KDK with root: {e}")
            logging.exception("Stack Trace:")
            raise

        logging.info("- Finished Preflight, starting patching")

        return required_patches


    def start_patch(self):
        """
        Entry point for the patching process.
        
        Main orchestrator that:
        1. Determines required patches
        2. Validates patch feasibility
        3. Mounts root volume
        4. Runs sanity checks
        5. Executes patching
        """
        logging.info("- Starting Patch Process")
        logging.info(f"- Determining Required Patch set for Darwin {self.constants.detected_os}")
        
        patchset_obj = HardwarePatchsetDetection(self.constants)
        self.patch_set_dictionary = patchset_obj.patches
        if not self.patch_set_dictionary:
            logging.info("- No Root Patches required for your machine!")
            return

        logging.info("- Verifying whether Root Patching possible")
        if not patchset_obj.can_patch:
            logging.error("- Cannot continue with patching!!!")
            patchset_obj.detailed_errors()
            return

        logging.info("- Patcher is capable of patching")
        logging.info("If you see a prompt that says Enter password to access Universal-Binaries.dmg, don't enter your user password! Enter the password for Universal-Binaries.dmg instead, which is password. If this fails, report this issue.")
        if not PatcherSupportPkgMount(self.constants).mount():
            logging.error("- Critical resources missing, cannot continue with patching!!!")
            return

        if not self._mount_root_vol():
            logging.error("- Failed to mount root volume, cannot continue with patching!!!")
            return

        if not self._run_sanity_checks():
            self._unmount_root_vol()
            logging.error("- Failed sanity checks: Pending updates/upgrades detected.")
            logging.info("It is recommended to install that update/upgrade and only then run the patcher once again.")

            # Offer the user a choice. Never sys.exit() here: start_patch() runs on a worker
            # thread, so exiting would surface as an unrelated "internal error" in the GUI
            # instead of returning control to the menu.
            if self._prompt_open_software_update():
                self._open_software_update()
            else:
                logging.info("- User declined to open Software Update.")

            logging.info("- Exiting the Install drivers and patches menu.")
            return
        try:
            logging.info("Patching the root volume")
            self._patch_root_vol()
        except Exception as e:
            logging.error("Failed to root patch the volume")
            logging.exception("Stack Trace:")
            logging.info("To ensure that your system continues to boot even after the root volume patches have failed to apply, we'll undo the patches that were applied until now.")
            self.start_unpatch()


    def start_unpatch(self) -> None:
        """
        Entry point for unpatching the root volume.
        
        Reverts APFS snapshot to undo patches.
        """
        logging.info("- Starting Unpatch Process")
        patchset_obj = HardwarePatchsetDetection(self.constants)
        
        if not patchset_obj.can_unpatch:
            logging.error("- Cannot continue with unpatching!!!")
            logging.error("If you continue to encounter this error, consider to start an upgrade in place to fix this error, no data will be lost.")
            patchset_obj.detailed_errors()
            return

        if not self._mount_root_vol():
            logging.error("- Failed to mount root volume, cannot continue with unpatching!!!")
            logging.info("This error may appear due to misconfiguration of Apple Mobile File Integrity (AMFI) or/and System Integrity Protection (SIP).")
            logging.info("To check whether that's the issue or not, do the following:")
            logging.info("1. Open Terminal")
            logging.info("2. Type sudo diskutil mount /dev/disk0s1 - this will mount the EFI partition")
            logging.info("3. Go to EFI > OC and find the config.plist")
            logging.info("4. If you know how to work with OCAT, proceed, else, submit the plist in a GitHub Discussion or issue")
            logging.info("5. Open the plist in OCAT")
            logging.info("6. Go to the NVRAM tab")
            logging.info("7. Find csr-active-config and change it to the proper value")
            logging.info("8. Do something similar with the AMFI")
            logging.info("9. Save the config.plist and restart your Mac")
            return

        self._unpatch_root_vol()
